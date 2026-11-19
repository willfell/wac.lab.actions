"""wacd-contract asks the gateway for the app's settings copy and decides where plan reads from.

A ThreadingHTTPServer stands in for the gateway. Each scenario runs the CLI
in-process with ci-release's gateway URL pointed at it and its token minting
replaced, the way wacd-release.test.py points the same names at its stub
gateway, so every request travels over real HTTP with a fresh token.
"""

import base64
import contextlib
import copy
import errno
import importlib.util
import io
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1]
SCRIPT = SCRIPTS / "wacd-contract.py"
SPEC = importlib.util.spec_from_file_location("wacd_contract", SCRIPT)
contract = importlib.util.module_from_spec(SPEC)
sys.modules["wacd_contract"] = contract
SPEC.loader.exec_module(contract)

FIXTURES = Path(__file__).parent / "fixtures" / "wacd-contract"
REPOSITORY_ID = "987654321"
WAIT = 2100
FLEET_COMMIT = "c" * 40
BODY_MARKER = "raw-gateway-body-never-printed"
CODES = (
    "fleet_copy_missing",
    "fleet_copy_unreadable",
    "fleet_copy_mismatch",
    "fleet_commit_not_on_default_branch",
    "fleet_commit_older_than_last_release",
    "revision_annotation_missing",
    "revision_mismatch",
    "fleet_repository_unreachable",
    "fleet_repository_mismatch",
)


def fixture_copy(source="hemi"):
    value = json.loads((FIXTURES / "contract.json").read_text())
    value["flags"]["source"] = source
    return value


def token(n, repository_id=REPOSITORY_ID):
    """A JWT-shaped job token whose payload names the repository it was minted for."""
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"repository_id": repository_id, "jti": f"token-{n}", "aud": "wacwini:ci-deploy"}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature-{n}"


class Gateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers["Content-Length"])
                outer.seen.append((self.headers["Authorization"], json.loads(self.rfile.read(size))))
                if len(outer.responses) > 1:
                    response = outer.responses.pop(0)
                else:
                    response = outer.responses[0]
                status, body, *delay = response
                if delay:
                    threading.Event().wait(delay[0])
                raw = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/release"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.001)


def answer(copy_value, fleet_commit=FLEET_COMMIT):
    return 200, {"copy": copy_value, "fleet_commit": fleet_commit if copy_value is not None else None}


class FetchCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / ".github").mkdir(parents=True)
        (self.repo / "deploy").mkdir()
        (self.repo / ".github" / "wacd.json").write_text((FIXTURES / "wacd.json").read_text())
        (self.repo / "deploy" / "values.yaml").write_text((FIXTURES / "values.yaml").read_text())
        self.summary = Path(self._tmp.name) / "summary.md"
        self.out = self.repo / ".wacd-contract"
        self.minted = []
        self.repository_id = REPOSITORY_ID

    def remove_files(self):
        (self.repo / ".github" / "wacd.json").unlink()
        (self.repo / "deploy" / "values.yaml").unlink()

    def mint(self):
        value = token(len(self.minted), self.repository_id)
        self.minted.append(value)
        return value

    def fetch(self, *responses, wait=WAIT):
        gateway = Gateway(*responses)
        self.addCleanup(gateway.close)
        self.gateway = gateway
        clock = Clock()
        self.clock = clock
        stdout, stderr = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(contract.ci, "URL", getattr(self, "dead_url", gateway.url)))
                stack.enter_context(patch.object(contract.ci, "oidc_token", self.mint))
                if not getattr(self, "keep_trust", False):
                    stack.enter_context(patch.object(contract, "trust_context", lambda: None))
                stack.enter_context(patch.object(contract.time, "monotonic", clock.monotonic))
                stack.enter_context(patch.object(contract.time, "sleep", clock.sleep))
                stack.enter_context(patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)}))
                stack.enter_context(contextlib.redirect_stdout(stdout))
                stack.enter_context(contextlib.redirect_stderr(stderr))
                code = contract.main(
                    ["wacd-contract.py", "fetch", "--out", ".wacd-contract", "--wait-timeout", str(wait)]
                )
        finally:
            os.chdir(cwd)
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        self.summary_text = self.summary.read_text() if self.summary.exists() else ""
        everything = self.stdout + self.stderr + self.summary_text
        for minted in self.minted:
            self.assertNotIn(minted, everything, "a job token was printed")
            self.assertNotIn(minted.split(".")[1], everything, "a job token's claims were printed")
        self.assertNotIn(BODY_MARKER, everything, "the gateway's body was printed raw")
        return code

    def fetch_with_trust(self, *responses):
        self.keep_trust = True
        try:
            return self.fetch(*responses)
        finally:
            self.keep_trust = False

    def outputs(self):
        return dict(line.split("=", 1) for line in self.stdout.splitlines())

    def assertRefused(self, code, sentence):
        self.assertEqual(code, 1, self.stdout + self.stderr)
        self.assertEqual(self.stdout, "", "no step output on a refusal")
        self.assertTrue(self.stderr.startswith("wacd contract: "), self.stderr)
        self.assertEqual(len(self.stderr.strip().splitlines()), 1)
        self.assertIn(sentence, self.stderr)
        self.assertIn(sentence, self.summary_text)
        self.assertFalse(self.out.exists(), "nothing may be left for a later job to act on")


class RuleTests(FetchCase):
    def test_rule_1_a_hemi_copy_is_written_with_its_derived_profile_and_values(self):
        code = self.fetch(answer(fixture_copy("hemi")))
        self.assertEqual(code, 0, self.stderr)
        out = self.outputs()
        self.assertEqual((out["source"], out["revision"], out["fleet_commit"]), ("hemi", "7", FLEET_COMMIT))
        self.assertIn("revision 7", out["reason"])
        self.assertEqual(json.loads((self.out / "contract.json").read_text()), fixture_copy("hemi"))
        self.assertEqual(
            json.loads((self.out / "values.json").read_text()), fixture_copy("hemi")["runtime"]
        )
        derived = json.loads((self.out / "profile.json").read_text())
        self.assertEqual(derived["values_file"], ".wacd-contract/values.json")
        self.assertEqual([auth for auth, _ in self.gateway.seen], ["Bearer " + self.minted[0]])
        self.assertEqual([body for _, body in self.gateway.seen], [{"operation": "contract"}])
        for word in ("hemi", "7", FLEET_COMMIT):
            self.assertIn(word, self.summary_text)

    def test_rule_1_the_derived_profile_reads_like_the_files_through_the_existing_scripts(self):
        self.assertEqual(self.fetch(answer(fixture_copy("hemi"))), 0, self.stderr)

        def outputs(path):
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "wacd-profile.py"), str(path)],
                capture_output=True, text=True, cwd=self.repo, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.splitlines()

        files = outputs(self.repo / ".github" / "wacd.json")
        derived = outputs(self.out / "profile.json")
        self.assertEqual(
            [line for line in derived if not line.startswith("values_file=")],
            [line for line in files if not line.startswith("values_file=")],
        )
        self.assertIn("values_file=.wacd-contract/values.json", derived)

    def test_rule_1_needs_no_files_in_the_repository(self):
        self.remove_files()
        self.assertEqual(self.fetch(answer(fixture_copy("hemi"))), 0, self.stderr)
        self.assertEqual(self.outputs()["source"], "hemi")

    def test_rule_2_a_files_copy_keeps_the_files_and_names_the_copy(self):
        code = self.fetch(answer(fixture_copy("files")))
        self.assertEqual(code, 0, self.stderr)
        out = self.outputs()
        self.assertEqual((out["source"], out["revision"], out["fleet_commit"]), ("files", "7", FLEET_COMMIT))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["contract.json"])

    def test_rule_3_no_copy_reads_the_files_as_today(self):
        planted = self.out
        planted.mkdir()
        (planted / "contract.json").write_text(json.dumps(fixture_copy("hemi")))
        code = self.fetch(answer(None))
        self.assertEqual(code, 0, self.stderr)
        out = self.outputs()
        self.assertEqual((out["source"], out["revision"], out["fleet_commit"]), ("files", "", ""))
        self.assertFalse(self.out.exists(), "a copy committed to the repository is never acted on")

    def test_rule_4_no_copy_and_no_files(self):
        self.remove_files()
        self.assertRefused(self.fetch(answer(None)), "no .github/wacd.json")

    def test_rule_4_a_files_copy_without_files(self):
        self.remove_files()
        self.assertRefused(self.fetch(answer(fixture_copy("files"))), "no .github/wacd.json")

    def test_rule_4_a_copy_naming_another_repository(self):
        self.repository_id = "111"
        self.assertRefused(self.fetch(answer(fixture_copy("hemi"))), contract.SENTENCES["another_repository"])

    def test_rule_4_a_copy_that_cannot_be_read(self):
        def without(path):
            value = fixture_copy("hemi")
            *parents, last = path
            holder = value
            for key in parents:
                holder = holder[key]
            del holder[last]
            return value

        cases = (
            ("no flags", answer(without(["flags"]))),
            ("unknown source", answer({**fixture_copy(), "flags": {"source": "elsewhere"}})),
            ("string revision", answer({**fixture_copy(), "identity": {**fixture_copy()["identity"], "revision": "7"}})),
            ("zero revision", answer({**fixture_copy(), "identity": {**fixture_copy()["identity"], "revision": 0}})),
            ("version 2", answer({**fixture_copy(), "version": 2})),
            ("no runtime", answer(without(["runtime"]))),
            ("profile does not derive", answer(without(["delivery", "healthUrl"]))),
            ("copy without a commit", (200, {"copy": fixture_copy(), "fleet_commit": None})),
            ("commit without a copy", (200, {"copy": None, "fleet_commit": FLEET_COMMIT})),
            ("short commit", (200, {"copy": fixture_copy(), "fleet_commit": "abc"})),
            ("eleven-digit revision", answer({**fixture_copy(), "identity": {**fixture_copy()["identity"],
                                                                             "revision": 10 ** 10}})),
            ("not an object", (200, [BODY_MARKER])),
        )
        for label, response in cases:
            with self.subTest(case=label):
                self.summary.unlink(missing_ok=True)
                self.assertRefused(self.fetch(response), "cannot be read")

    def test_rule_4_a_runtime_that_sets_what_wac_cd_owns_is_refused_before_anything_is_tagged(self):
        """publish's package refuses these keys; plan refuses them first, so no tag is claimed for a doomed release."""
        for key in ("app", "images", "global"):
            with self.subTest(key=key):
                self.summary.unlink(missing_ok=True)
                copy_value = fixture_copy("hemi")
                copy_value["runtime"][key] = "anything"
                self.assertRefused(self.fetch(answer(copy_value)), "cannot be read")
                self.assertIn(repr(key), self.stderr)
        files_copy = fixture_copy("files")
        files_copy["runtime"]["app"] = "anything"
        self.summary.unlink()
        self.assertEqual(self.fetch(answer(files_copy)), 0, "a files copy's runtime is never rendered")

    def test_a_token_that_names_no_repository_is_refused_when_a_copy_is_present(self):
        self.repository_id = None
        self.assertRefused(self.fetch(answer(fixture_copy("hemi"))), "which repository")


class AnswerTests(FetchCase):
    def test_a_503_is_retried_with_a_fresh_token(self):
        busy = (503, {"error": "release dependency unavailable"})
        code = self.fetch(busy, busy, answer(fixture_copy("hemi")))
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(len(self.gateway.seen), 3)
        self.assertEqual(len(set(auth for auth, _ in self.gateway.seen)), 3, "each attempt mints its own token")
        self.assertEqual(self.clock.sleeps, [5, 10])

    def test_a_gateway_that_stays_unavailable_is_waited_out_for_wait_timeout_with_a_growing_interval(self):
        """A release used to ride out gateway 503s for wait_timeout in deploy; plan now waits as long."""
        code = self.fetch((503, {"error": "release dependency unavailable"}), wait=WAIT)
        self.assertRefused(code, f"{WAIT} seconds")
        waited = self.clock.now - 1000.0
        self.assertGreaterEqual(waited, WAIT)
        self.assertLessEqual(waited, WAIT + contract.MAX_POLL_SECONDS)
        self.assertEqual(self.clock.sleeps[:4], [5, 10, 20, 30])
        self.assertEqual(max(self.clock.sleeps), contract.MAX_POLL_SECONDS)
        self.assertLessEqual(len(self.gateway.seen), WAIT // contract.MAX_POLL_SECONDS + 5, "the gateway is not hammered")
        self.assertEqual(len(set(auth for auth, _ in self.gateway.seen)), len(self.gateway.seen))

    def test_a_short_wait_timeout_is_honoured(self):
        code = self.fetch((503, {}), wait=12)
        self.assertRefused(code, "12 seconds")
        self.assertLessEqual(self.clock.now - 1000.0, 12)

    def test_plan_accepts_exactly_the_wait_timeouts_deploy_accepts(self):
        """One wait_timeout input feeds both jobs, so they must agree on what it means."""
        release = contract.release
        for text in ("600", " 600", "+600", "600\n", "1", "3600", "0", "-5", "3601", "2.5", "soon", "", "6_00"):
            with self.subTest(wait=repr(text)):
                try:
                    deploy = release.wait_bound(text)
                except release.ReleaseError:
                    deploy = None
                self.summary.unlink(missing_ok=True)
                code = self.fetch(answer(None), wait=text)
                if deploy is None:
                    self.assertRefused(code, "--wait-timeout")
                    self.assertEqual(self.gateway.seen, [], "nothing is asked with a bad bound")
                else:
                    self.assertEqual(code, 0, self.stderr)
                    self.assertEqual(len(self.gateway.seen), 1)

    def test_plan_waits_at_most_the_cap_inside_its_job_timeout(self):
        """deploy honours a larger wait_timeout; plan stops at the cap so its summary is written."""
        code = self.fetch((503, {}), wait=3600)
        self.assertRefused(code, f"{contract.PLAN_WAIT_CAP} seconds")
        self.assertLessEqual(self.clock.now - 1000.0, contract.PLAN_WAIT_CAP + contract.MAX_POLL_SECONDS)
        self.assertGreaterEqual(self.clock.now - 1000.0, contract.PLAN_WAIT_CAP)

    def test_each_coded_denial_has_its_own_sentence(self):
        self.assertEqual(sorted(contract.REFUSALS), sorted(CODES))
        sentences = set()
        for code in CODES:
            with self.subTest(code=code):
                self.summary.unlink(missing_ok=True)
                result = self.fetch((403, {"error": "release request denied", "reason": code, "detail": BODY_MARKER}))
                self.assertRefused(result, contract.REFUSALS[code])
                self.assertEqual(len(self.gateway.seen), 1, "a denial is not retried")
                sentences.add(contract.REFUSALS[code])
        self.assertEqual(len(sentences), len(CODES))

    def test_an_unknown_code_gets_the_default_sentence(self):
        for reason in ("fleet_copy_haunted", BODY_MARKER, 7, "x" * 300):
            with self.subTest(reason=str(reason)[:20]):
                self.summary.unlink(missing_ok=True)
                code = self.fetch((403, {"error": "release request denied", "reason": reason}))
                self.assertRefused(code, contract.SENTENCES["unknown_refusal"])

    def test_an_uncoded_denial_is_asked_twice_more_with_a_fresh_token_before_it_fails(self):
        """The gateway answers a token it could not check (signing keys, replay store) with the uncoded 403."""
        uncoded = (403, {"error": "release request denied", "detail": BODY_MARKER})
        code = self.fetch(uncoded)
        self.assertRefused(code, contract.SENTENCES["unverified"])
        self.assertEqual(len(self.gateway.seen), 3)
        self.assertEqual(len({auth for auth, _ in self.gateway.seen}), 3, "each attempt mints its own token")
        self.assertEqual(self.clock.sleeps, [10, 10])
        self.assertIn("The gateway refused this run's token on each of 3 attempts", self.summary_text)
        self.assertIn("a gateway that does not yet serve the contract operation (wacwini/wac.lab#570 not deployed) "
                      "looks like this too", self.summary_text)
        self.assertTrue(self.stderr.rstrip().endswith("; if this app is new, register it with lab first."))
        self.summary.unlink()
        code = self.fetch(uncoded, uncoded, answer(None))
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(self.outputs()["source"], "files")
        self.assertEqual((len(self.gateway.seen), self.clock.sleeps), (3, [10, 10]))

    def test_a_coded_body_on_another_status_is_not_a_denial(self):
        for status in (400, 404, 500):
            with self.subTest(status=status):
                self.summary.unlink(missing_ok=True)
                code = self.fetch((status, {"error": "x", "reason": "fleet_copy_missing"}))
                self.assertRefused(code, f"HTTP {status}")
                self.assertNotIn(contract.REFUSALS["fleet_copy_missing"], self.stderr)
                self.assertEqual(len(self.gateway.seen), 1)

    def dead_port_url(self):
        dead = Gateway((200, {}))
        dead.close()
        return dead.url

    def refused_then(self, refusals):
        """Send the first `refusals` attempts to a closed port, the rest to the stub gateway."""
        real_post = contract.release.post
        dead = self.dead_port_url()
        attempts = []

        def post(body, trust, timeout):
            attempts.append(timeout)
            url = dead if len(attempts) <= refusals else self.gateway.url
            with patch.object(contract.ci, "URL", url):
                return real_post(body, trust, timeout)

        return patch.object(contract.release, "post", post), attempts

    def test_a_gateway_restart_is_ridden_out(self):
        """The gateway is Recreate: a rollout leaves about 35 s with no endpoint behind it."""
        spy, attempts = self.refused_then(2)
        with spy:
            code = self.fetch(answer(None), wait=WAIT)
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(self.outputs()["source"], "files")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(self.minted), 3, "each attempt mints its own token")
        self.assertEqual(len(self.gateway.seen), 1)
        self.assertEqual(self.clock.sleeps, [5, 10])

    def test_refusals_past_the_restart_window_could_not_be_reached(self):
        self.dead_url = self.dead_port_url()
        with patch.object(contract, "RESTART_WINDOW", 20):
            code = self.fetch((200, {}), wait=WAIT)
        self.assertRefused(code, "could not be reached")
        self.assertGreater(len(self.minted), 1)
        self.assertLess(self.clock.now - 1000.0, 20 + contract.MAX_POLL_SECONDS)

    def test_the_restart_window_never_outlasts_the_wait(self):
        self.dead_url = self.dead_port_url()
        code = self.fetch((200, {}), wait=12)
        self.assertRefused(code, "could not be reached")
        self.assertLessEqual(self.clock.now - 1000.0, 12)

    def test_the_restart_window_is_ninety_seconds(self):
        self.assertEqual(contract.RESTART_WINDOW, 90)

    def unanswered_then(self, count, url=None, error=None):
        """The first `count` attempts go unanswered: to url, or with error raised from the connect."""
        real_post = contract.release.post
        real_connect = socket.create_connection
        attempts = []

        def refuse_connect(*args, **kwargs):
            raise error

        def post(body, trust, timeout):
            attempts.append(timeout)
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(contract.ci, "URL", self.gateway.url))
                if len(attempts) <= count and url is not None:
                    stack.enter_context(patch.object(contract.ci, "URL", url))
                if len(attempts) <= count and error is not None:
                    stack.enter_context(patch.object(socket, "create_connection", refuse_connect))
                return real_post(body, trust, timeout)

        self.assertIs(socket.create_connection, real_connect)
        return patch.object(contract.release, "post", post), attempts

    def test_every_way_nothing_answers_before_a_response_is_ridden_out(self):
        """The gateway sheds load by closing a socket before the handshake, and its pod can die mid-handshake."""
        cases = (
            ("reset", Silent("reset").url("http"), None),
            ("EOF before the status line", Silent("eof").url("http"), None),
            ("EOF in the TLS handshake", Silent("eof").url("https"), None),
            ("connect timeout", None, TimeoutError("timed out")),
            ("no route to host", None, OSError(errno.EHOSTUNREACH, "No route to host")),
            ("network unreachable", None, OSError(errno.ENETUNREACH, "Network is unreachable")),
        )
        for label, url, error in cases:
            with self.subTest(case=label):
                self.summary.unlink(missing_ok=True)
                self.minted = []
                spy, attempts = self.unanswered_then(2, url, error)
                with spy:
                    code = self.fetch(answer(None), wait=WAIT)
                self.assertEqual(code, 0, self.stderr)
                self.assertEqual(self.outputs()["source"], "files")
                self.assertEqual(len(attempts), 3)
                self.assertEqual(len(self.minted), 3, "each attempt mints its own token")
                self.assertEqual(len(self.gateway.seen), 1)
                self.assertEqual(self.clock.sleeps, [5, 10])

    def test_the_last_failure_names_the_cause_when_an_uncoded_denial_is_followed_by_silence(self):
        """An uncoded 403, then nothing answers until the wait ends: the gateway could not be reached."""
        real_post = contract.release.post
        dead = self.dead_port_url()
        attempts = []

        def post(body, trust, timeout):
            attempts.append(timeout)
            with patch.object(contract.ci, "URL", self.gateway.url if len(attempts) == 1 else dead):
                return real_post(body, trust, timeout)

        with patch.object(contract.release, "post", post):
            code = self.fetch((403, {}), wait=30)
        self.assertRefused(code, "could not be reached")
        self.assertNotIn("refused this run's token", self.stderr)
        self.assertEqual(len(self.gateway.seen), 1)
        self.assertGreater(len(attempts), 2, "the silence was asked again within the wait")

    def test_a_certificate_that_fails_verification_fails_at_once(self):
        def unverified(*args, **kwargs):
            raise ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")

        gateway = Gateway((200, {"copy": None, "fleet_commit": None}))
        self.addCleanup(gateway.close)
        self.dead_url = gateway.url.replace("http://", "https://")
        with patch.object(contract, "trust_context", ssl.create_default_context), \
                patch.object(ssl.SSLContext, "wrap_socket", unverified):
            code = self.fetch_with_trust((200, {}))
        self.assertRefused(code, "could not be reached")
        self.assertEqual(len(self.minted), 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_tls_failure_fails_at_once(self):
        """Wrong trust or address is not a restart: an https request to a plain-http port."""
        gateway = Gateway((200, {"copy": None, "fleet_commit": None}))
        self.addCleanup(gateway.close)
        self.dead_url = gateway.url.replace("http://", "https://")
        with patch.object(contract, "trust_context", ssl.create_default_context):
            code = self.fetch_with_trust((200, {}))
        self.assertRefused(code, "could not be reached")
        self.assertEqual(len(self.minted), 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_slow_answer_is_waited_for_with_the_whole_time_left(self):
        """The gateway checks the caller against main before it reads wac.fleet, and can be slow."""
        timeouts = []
        real_post = contract.release.post

        def spy(body, trust, timeout):
            timeouts.append(timeout)
            return real_post(body, trust, timeout)

        with patch.object(contract.release, "post", spy):
            code = self.fetch((200, {"copy": None, "fleet_commit": None}, 1.5), wait=WAIT)
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(self.outputs()["source"], "files")
        self.assertEqual(len(self.minted), 1)
        self.assertEqual(timeouts, [WAIT], "an attempt may wait the whole deadline, as deploy's requests do")

    def test_an_answer_slower_than_the_deadline_could_not_be_reached(self):
        code = self.fetch((200, {"copy": None, "fleet_commit": None}, 2.5), wait=1)
        self.assertRefused(code, "could not be reached")
        self.assertEqual(len(self.minted), 1)


class Silent:
    """A listener that reads the request, or the TLS ClientHello, and answers nothing.

    "eof" closes the connection cleanly; "reset" closes it with an RST.
    """

    def __init__(self, how):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.how = how
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(5)
                try:
                    received = connection.recv(65536)
                    while b"\r\n\r\n" in received and len(received.split(b"\r\n\r\n", 1)[1]) < len(
                            json.dumps(contract.CONTRACT_BODY)):
                        received += connection.recv(65536)
                except OSError:
                    pass
                if self.how == "reset":
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")

    def url(self, scheme):
        return f"{scheme}://127.0.0.1:{self.server.getsockname()[1]}/v1/release"


class CliTests(unittest.TestCase):
    def test_without_oidc_fetch_fails_cleanly_and_prints_no_bearer(self):
        bearer = "runner.request.bearer.value"
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary"
            env = {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": bearer,
                   "ACTIONS_ID_TOKEN_REQUEST_URL": "http://example.invalid/token",
                   "GITHUB_STEP_SUMMARY": str(summary)}
            stdout, stderr = io.StringIO(), io.StringIO()
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, env), patch.object(contract, "trust_context", lambda: None), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = contract.main(["wacd-contract.py", "fetch", "--out", ".wacd-contract", "--wait-timeout", "60"])
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 1, stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "wacd contract: " + contract.SENTENCES["no_token"] + "\n")
            self.assertNotIn(bearer, stdout.getvalue() + stderr.getvalue() + summary.read_text())

    def test_fetch_takes_no_address_for_the_gateway(self):
        usage = subprocess.run(
            [sys.executable, str(SCRIPT), "fetch", "--help"], capture_output=True, text=True, check=False
        )
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertEqual(
            {word for word in usage.stdout.split() if word.startswith("--")}, {"--help", "--out", "--wait-timeout"}
        )



PROFILE = ".github/wacd.json"
VALUES = "deploy/values.yaml"
FILES = {
    PROFILE: (FIXTURES / "wacd.json").read_text(),
    VALUES: (FIXTURES / "values.yaml").read_text(),
}
REPOSITORY = "wacwini/wac.app.travel"
NO_ANSWER = "hemi did not answer; re-run when it does."
NEW_OR_CUT_OVER = (f"if {REPOSITORY} is new, register it with lab first and re-run; if it is cut over, its "
                   "settings live in hemi and the checks cannot run until hemi admits this run.")
NOT_REGISTERED = (f"hemi does not know {REPOSITORY}: it is not registered with lab, or hemi holds no active copy "
                  "of its settings. If it is new, register it (lab:lab-onboard-app), wait for hemi to list it, "
                  "then re-run the checks.")


class Hemi:
    """A stand-in for hemi's GET /api/v1/contract.

    Each response is (status, body), (status, body, delay seconds), "drop",
    which closes the connection without an answer, "reset", which resets it
    after the request was read, or "partial", which sends the status line,
    the headers and half the body it declares, then closes. The last one
    repeats.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.command, self.path, self.headers["Authorization"]))
                response = outer.responses.pop(0) if len(outer.responses) > 1 else outer.responses[0]
                if response == "drop":
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                if response == "reset":
                    self.close_connection = True
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.connection.close()
                    return
                if response == "partial":
                    raw = json.dumps(fixture_copy("hemi")).encode()
                    self.close_connection = True
                    try:
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw[:len(raw) // 2])
                        self.wfile.flush()
                    except OSError:
                        pass
                    return
                if response == "trickle":
                    raw = json.dumps(fixture_copy("hemi")).encode()
                    try:
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        size = len(raw) // 8 + 1
                        for start in range(0, len(raw), size):
                            threading.Event().wait(0.2)
                            self.wfile.write(raw[start:start + size])
                            self.wfile.flush()
                    except OSError:
                        pass
                    return
                status, body, *delay = response
                if delay:
                    threading.Event().wait(delay[0])
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class PullRequestCase(unittest.TestCase):
    """wac-ci's contract job: main's tree and the merge commit's, read the way actions/checkout leaves them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.remote = self.root / "remote.git"
        git("init", "-q", "--bare", str(self.remote), cwd=self.root)
        git("config", "uploadpack.allowFilter", "true", cwd=self.remote)
        self.work = self.root / "work"
        git("init", "-q", str(self.work), cwd=self.root)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.out = self.workspace / ".wacd-contract"
        self.summary = self.root / "summary.md"
        self.minted = []
        self.repository_id = REPOSITORY_ID
        self.grant = True

    def branch(self, name, files, parents=()):
        """Push a branch whose tree is a README plus exactly these files, on these parent commits."""
        self.commits = getattr(self, "commits", 0) + 1
        git("checkout", "-q", "--orphan", f"commit-{self.commits}", cwd=self.work)
        git("rm", "-rq", "--cached", "--ignore-unmatch", ".", cwd=self.work)
        for child in self.work.iterdir():
            if child.name != ".git":
                subprocess.run(["rm", "-rf", str(child)], check=True)
        (self.work / "README.md").write_text(name + "\n")
        for path, text in files.items():
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        git("add", "-A", cwd=self.work)
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", name, cwd=self.work)
        commit = git("rev-parse", "HEAD", cwd=self.work).strip()
        if parents:
            linked = [arg for parent in parents for arg in ("-p", parent)]
            commit = git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit-tree", *linked,
                         "-m", name, "HEAD^{tree}", cwd=self.work).strip()
        git("push", "-q", "--force", str(self.remote), f"{commit}:refs/heads/{name}", cwd=self.work)
        return commit

    def checkout(self, name, path):
        """actions/checkout with fetch-depth 1, filter blob:none and a sparse .github/wacd.json.

        Its remote is then pointed nowhere, as persist-credentials: false leaves
        a private repository: a read that needs any further object fails.
        """
        target = self.workspace / path
        subprocess.run(["rm", "-rf", str(target)], check=True)
        git("init", "-q", str(target), cwd=self.root)
        git("remote", "add", "origin", self.remote.as_uri(), cwd=target)
        git("sparse-checkout", "set", "--no-cone", PROFILE, cwd=target)
        git("fetch", "-q", "--no-tags", "--filter=blob:none", "--depth=1", "origin",
            f"+refs/heads/{name}:refs/remotes/origin/{name}", cwd=target)
        git("checkout", "-q", "--force", "-B", name, f"refs/remotes/origin/{name}", cwd=target)
        git("remote", "set-url", "origin", str(self.root / "gone"), cwd=target)

    def mint(self):
        value = token(len(self.minted), self.repository_id)
        self.minted.append(value)
        return value

    def run_pr(self, *responses, main=None, head=None, base=None):
        """The pull request's tree is checked out as GitHub's merge commit: main's head as its first parent."""
        on_main = self.branch("main", FILES if main is None else main)
        self.branch("pr", FILES if head is None else head, parents=(base or on_main,))
        self.checkout("main", ".wacd-main")
        self.checkout("pr", ".wacd-head")
        return self.invoke(*responses)

    def invoke(self, *responses):
        hemi = Hemi(*responses)
        self.addCleanup(hemi.close)
        self.hemi = hemi
        self.sleeps = []
        self.minted = []
        env = {"GITHUB_STEP_SUMMARY": str(self.summary), "GITHUB_REPOSITORY": REPOSITORY,
               "GITHUB_EVENT_NAME": getattr(self, "event", "pull_request"),
               "JOB_RUNNER_NAME": getattr(self, "runner", "ci-light-abc12")}
        if self.grant:
            env.update({"ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.actions.githubusercontent.com/t",
                        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-bearer"})
        stdout, stderr = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.workspace)
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(contract.shadow, "URL", getattr(self, "hemi_url", hemi.url)))
                stack.enter_context(patch.object(contract.shadow, "oidc_token", getattr(self, "minter", self.mint)))
                stack.enter_context(patch.object(contract.time, "sleep", self.sleeps.append))
                stack.enter_context(patch.object(contract, "PR_ATTEMPT_SECONDS", 0.3))
                stack.enter_context(patch.dict(os.environ, env))
                if not self.grant:
                    for name in ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
                        stack.enter_context(patch.dict(os.environ, {name: ""}))
                stack.enter_context(contextlib.redirect_stdout(stdout))
                stack.enter_context(contextlib.redirect_stderr(stderr))
                code = contract.main(
                    ["wacd-contract.py", "pr", "--out", ".wacd-contract", "--main", ".wacd-main", "--head", ".wacd-head"]
                )
        finally:
            os.chdir(cwd)
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        self.summary_text = self.summary.read_text() if self.summary.exists() else ""
        everything = self.stdout + self.stderr + self.summary_text
        for minted in self.minted:
            self.assertNotIn(minted, everything, "a job token was printed")
            self.assertNotIn(minted.split(".")[1], everything, "a job token's claims were printed")
        self.assertNotIn(BODY_MARKER, everything, "hemi's body was printed raw")
        self.assertNotIn("runner-bearer", everything)
        return code

    def outputs(self):
        return dict(line.split("=", 1) for line in self.stdout.splitlines())

    def assertSource(self, code, source):
        self.assertEqual(code, 0, self.stdout + self.stderr)
        self.assertEqual(self.outputs()["source"], source)
        if source == "files":
            self.assertFalse(self.out.exists(), "a run on the files leaves nothing for a later job")

    def assertFails(self, code, sentence):
        self.assertEqual(code, 1, self.stdout + self.stderr)
        self.assertEqual(self.stdout, "", "no step output on a failure")
        self.assertEqual(self.stderr, "wacd contract: " + sentence + "\n")
        self.assertIn(sentence, self.summary_text)
        self.assertFalse(self.out.exists(), "nothing may be left for a later job to act on")

    def hemi_answer(self, source="hemi", **changes):
        value = fixture_copy(source)
        value.update(changes)
        return 200, value


class PullRequestDecisionTests(PullRequestCase):
    """One test per row of the decision table in docs/reference/wac-ci.md."""

    def test_hemi_and_files_added_back_by_the_pull_request_fails(self):
        for label, head, added in (
            ("both", FILES, [PROFILE, VALUES]),
            ("the profile", {PROFILE: FILES[PROFILE]}, [PROFILE]),
            ("the values", {VALUES: FILES[VALUES]}, [VALUES]),
        ):
            with self.subTest(added=label):
                self.summary.unlink(missing_ok=True)
                code = self.run_pr(self.hemi_answer(), main={}, head=head)
                self.assertFails(code, " ".join(f"{path} was added back; travel's settings live in hemi." for path in added))

    def test_hemi_with_the_files_still_on_main_uses_hemi_and_says_so(self):
        code = self.run_pr(self.hemi_answer(), main=FILES, head=FILES)
        self.assertSource(code, "hemi")
        for path in (PROFILE, VALUES):
            self.assertIn(
                f"{path} is still on main until hemi's clean-up pull request merges; hemi's settings were used.",
                self.summary_text,
            )
        self.assertEqual(json.loads((self.out / "contract.json").read_text()), fixture_copy("hemi"))
        self.assertEqual(json.loads((self.out / "values.json").read_text()), fixture_copy("hemi")["runtime"])
        self.assertEqual(json.loads((self.out / "profile.json").read_text())["values_file"], ".wacd-contract/values.json")

    def test_hemi_with_the_files_still_on_main_fails_a_pull_request_that_changes_them(self):
        changed = {PROFILE: FILES[PROFILE], VALUES: FILES[VALUES] + "extra: true\n"}
        code = self.run_pr(self.hemi_answer(), main=FILES, head=changed)
        self.assertFails(code, f"{VALUES} was changed; travel's settings live in hemi, so change them there.")
        self.summary.unlink()
        both = {PROFILE: FILES[PROFILE].replace("{", "{ ", 1), VALUES: changed[VALUES]}
        self.assertFails(self.run_pr(self.hemi_answer(), main=FILES, head=both),
                         " ".join(f"{path} was changed; travel's settings live in hemi, so change them there."
                                  for path in (PROFILE, VALUES)))

    def test_hemi_with_no_files_uses_hemi_with_nothing_to_say(self):
        for label, main in (("cut over", {}), ("the clean-up pull request itself", FILES)):
            with self.subTest(case=label):
                self.summary.unlink(missing_ok=True)
                code = self.run_pr(self.hemi_answer(), main=main, head={})
                self.assertSource(code, "hemi")
                self.assertNotIn("still on main", self.summary_text)
                self.assertNotIn("added back", self.summary_text)
                self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                                 ["contract.json", "profile.json", "values.json"])

    def test_files_copy_and_a_restore_pull_request_runs_on_its_own_files(self):
        code = self.run_pr(self.hemi_answer("files"), main={}, head=FILES)
        self.assertSource(code, "files")
        self.assertIn("hemi's copy (revision 7) says travel is returning to its files and this pull request puts "
                      "them back, so its own files were used.", self.summary_text)
        self.assertNotIn("added back", self.summary_text)

    def test_a_new_apps_first_pull_request_runs_on_the_files_it_adds(self):
        """Onboarding: lab registers the app first, then its first pull request adds the files and wac-ci.

        For a registered repository never cut over, hemi answers with this body and nothing else.
        """
        never_cut_over = {"version": 1, "identity": {"repositoryId": REPOSITORY_ID, "repository": REPOSITORY,
                                                     "app": "travel"}, "flags": {"source": "files"}}
        code = self.run_pr((200, never_cut_over), main={}, head=FILES)
        self.assertSource(code, "files")
        self.assertEqual(self.outputs()["revision"], "")
        self.assertIn("hemi holds no settings for travel yet, so the pull request's own files were used.",
                      self.summary_text)
        self.assertNotIn("added back", self.summary_text)
        self.summary.unlink()
        self.assertSource(self.run_pr((200, never_cut_over), main=FILES, head=FILES), "files")
        self.summary.unlink()
        self.assertFails(self.run_pr((200, never_cut_over), main={}, head={}),
                         "hemi holds no settings for travel yet, and neither main nor this pull request has "
                         ".github/wacd.json, so there is nothing to check.")

    def test_a_files_answer_needs_its_source_and_the_tokens_repository_id(self):
        for label, body, readable in (
            ("the source alone", {"flags": {"source": "files"}}, False),
            ("an identity with no id", {"identity": {"app": "travel"}, "flags": {"source": "files"}}, False),
            ("an id that is the token's", {"identity": {"repositoryId": REPOSITORY_ID}, "flags": {"source": "files"}},
             True),
            ("another repository's id", {"identity": {"repositoryId": "111"}, "flags": {"source": "files"}}, False),
            ("no source", {"identity": {"repositoryId": REPOSITORY_ID}}, False),
        ):
            with self.subTest(answer=label):
                self.summary.unlink(missing_ok=True)
                code = self.run_pr((200, body), main={}, head=FILES)
                if readable:
                    self.assertSource(code, "files")
                    self.assertIn(f"hemi holds no settings for {REPOSITORY} yet", self.summary_text)
                else:
                    self.assertFails(code, contract.PR_SENTENCES["unreadable"].format(app=REPOSITORY))

    def test_files_copy_with_no_files_on_main_or_in_the_pull_request_fails(self):
        code = self.run_pr(self.hemi_answer("files"), main={}, head={})
        self.assertFails(code, contract.PR_SENTENCES["returning"].format(app="travel"))
        self.assertIn("restore pull request", self.stderr)

    def test_files_copy_with_files_on_main_runs_on_the_pull_requests_files(self):
        for label, head in (("present", FILES), ("absent", {})):
            with self.subTest(head=label):
                self.summary.unlink(missing_ok=True)
                self.out.mkdir(exist_ok=True)
                (self.out / "contract.json").write_text(json.dumps(fixture_copy("hemi")))
                self.assertSource(self.run_pr(self.hemi_answer("files"), main=FILES, head=head), "files")

    def test_any_answer_but_200_with_files_on_main_runs_on_the_files(self):
        for label, response, said in (
            ("401", (401, {"error": BODY_MARKER}), "hemi answered 401; using the repository's files"),
            ("403", (403, {"error": "not registered"}), "hemi answered 403; using the repository's files"),
            ("404", (404, {"error": "not found"}), "hemi answered 404; using the repository's files"),
            ("422", (422, {"error": BODY_MARKER}), "hemi answered 422; using the repository's files"),
            ("500", (500, {}), "hemi did not answer; using the repository's files"),
            ("503", (503, {"error": "not connected"}), "hemi did not answer; using the repository's files"),
            ("no answer", "drop", "hemi did not answer; using the repository's files"),
            ("reset after the request", "reset", "hemi did not answer; using the repository's files"),
            ("a partial answer", "partial", "hemi did not answer; using the repository's files"),
            ("timeout", (200, fixture_copy("hemi"), 1), "hemi did not answer; using the repository's files"),
        ):
            with self.subTest(answer=label):
                self.summary.unlink(missing_ok=True)
                self.assertSource(self.run_pr(response, main=FILES, head=FILES), "files")
                self.assertIn(said, self.summary_text)

    def test_hemis_clean_up_pull_request_during_a_hemi_outage_fails_saying_so(self):
        """It deletes the files from main; run on its own files, checks and render would fail reading them."""
        removes = f"and this pull request removes {REPOSITORY}'s files"
        for label, response, sentence in (
            ("503", (503, {}), f"hemi did not answer, {removes}; re-run when hemi answers."),
            ("no answer", "drop", f"hemi did not answer, {removes}; re-run when hemi answers."),
            ("401", (401, {}), f"hemi refused this pull request's token (HTTP 401), {removes}; if {REPOSITORY} "
                               "is new, register it with lab first and re-run."),
            ("403", (403, {}), f"hemi refused this pull request's token (HTTP 403), {removes}; if {REPOSITORY} "
                               "is new, register it with lab first and re-run."),
            ("404", (404, {}), f"hemi answered HTTP 404, {removes}; re-run once hemi serves this repository."),
            ("unreadable", (200, BODY_MARKER.encode()), f"hemi's answer could not be read, {removes}; re-run once "
                                                         "hemi serves this repository."),
        ):
            for head_label, head in (("both", {}), ("the values file", {PROFILE: FILES[PROFILE]}),
                                     ("the profile", {VALUES: FILES[VALUES]})):
                with self.subTest(answer=label, removes=head_label):
                    self.summary.unlink(missing_ok=True)
                    self.assertFails(self.run_pr(response, main=FILES, head=head), sentence)

    def test_a_clean_up_pull_request_that_cannot_reach_or_ask_hemi_names_the_cause(self):
        removes = f"and this pull request removes {REPOSITORY}'s files"
        head = {PROFILE: FILES[PROFILE]}
        self.hemi_url = "http://hemi-app.hemi.invalid:3000"
        self.assertFails(self.run_pr((200, fixture_copy("hemi")), main=FILES, head=head),
                         f"the contract job must run on a Lab runner: ci-light-abc12 cannot reach hemi's in-cluster "
                         f"address, {removes}. Pass a Lab pool as `runner`.")
        del self.hemi_url
        self.summary.unlink(missing_ok=True)

        def refuse():
            raise contract.shadow.Unanswered("GitHub did not return a usable OIDC token")

        self.minter = refuse
        self.assertFails(self.run_pr((200, fixture_copy("hemi")), main=FILES, head=head),
                         f"GitHub would not mint a job token for hemi, {removes}; re-run the checks.")
        del self.minter
        self.summary.unlink(missing_ok=True)
        self.grant = False
        self.assertFails(self.run_pr((200, fixture_copy("hemi")), main=FILES, head=head),
                         f"no job token for hemi: the caller's ci.yml must grant id-token: write, {removes}.")

    def test_a_refused_token_with_no_files_on_main_fails(self):
        for status in (401, 403):
            for head_label, head in (("absent", {}), ("both added", FILES)):
                with self.subTest(status=status, head=head_label):
                    self.summary.unlink(missing_ok=True)
                    code = self.run_pr((status, {"error": BODY_MARKER}), main={}, head=head)
                    self.assertFails(code, f"hemi refused this pull request's token (HTTP {status}): "
                                           + NEW_OR_CUT_OVER)
                    self.assertEqual(len(self.hemi.seen), 1, "a refusal is not asked again")

    def test_hemis_not_registered_refusal_says_hemi_does_not_know_the_app(self):
        """hemi answers {"error":"not registered"} for an unregistered app and for one with no active copy."""
        for event in ("pull_request", "push"):
            for head_label, head in (("absent", {}), ("both added", FILES)):
                with self.subTest(event=event, head=head_label):
                    self.event = event
                    self.summary.unlink(missing_ok=True)
                    self.assertFails(self.run_pr((403, {"error": "not registered"}), main={}, head=head),
                                     NOT_REGISTERED)
                    self.assertEqual(len(self.hemi.seen), 1, "a refusal is not asked again")
        self.event = "pull_request"
        for label, body in (("whitespace", b' {\n  "error" : "not registered"\n} '),
                            ("an escaped letter", b'{"error": "not \\u0072egistered"}')):
            with self.subTest(body=label):
                self.summary.unlink(missing_ok=True)
                self.assertFails(self.run_pr((403, body), main={}, head={}), NOT_REGISTERED)

    def test_any_other_403_body_reads_as_an_uncoded_refusal(self):
        """The body is parsed defensively and bounded: anything but hemi's exact code is the generic sentence."""
        oversized = json.dumps({"error": "not registered", "pad": "x" * contract.MAX_REFUSAL_BODY}).encode()
        for label, response in (
            ("no body", (403, b"")),
            ("not JSON", (403, b"not registered")),
            ("a list", (403, [{"error": "not registered"}])),
            ("another code", (403, {"error": "forbidden"})),
            ("the code in another case", (403, {"error": "Not Registered"})),
            ("the code under another key", (403, {"reason": "not registered"})),
            ("past the bound", (403, oversized)),
            ("deeply nested", (403, b"[" * 4000)),
            ("not UTF-8", (403, b'{"error": "not registered\xff"}')),
            ("UTF-16 with its BOM", (403, '{"error": "not registered"}'.encode("utf-16"))),
            ("UTF-16 without a BOM", (403, '{"error": "not registered"}'.encode("utf-16-le"))),
            ("UTF-32", (403, '{"error": "not registered"}'.encode("utf-32"))),
            ("UTF-8 with a BOM", (403, b'\xef\xbb\xbf{"error": "not registered"}')),
            ("an extra key", (403, {"error": "not registered", "detail": "x"})),
            ("a duplicate key ending in the code", (403, b'{"error": "forbidden", "error": "not registered"}')),
            ("a duplicate key of the code", (403, b'{"error": "not registered", "error": "not registered"}')),
            ("the code as a list", (403, {"error": ["not registered"]})),
            ("the code on a 401", (401, {"error": "not registered"})),
        ):
            with self.subTest(body=label):
                self.summary.unlink(missing_ok=True)
                status = response[0]
                self.assertFails(self.run_pr(response, main={}, head={}),
                                 f"hemi refused this pull request's token (HTTP {status}): " + NEW_OR_CUT_OVER)

    def test_no_answer_with_no_files_on_main_fails(self):
        for label, response in (("no answer", "drop"), ("reset after the request", "reset"),
                                ("a partial answer", "partial"), ("timeout", (200, fixture_copy("hemi"), 1)),
                                ("500", (500, {})), ("503", (503, {"error": "not connected"}))):
            for head_label, head in (("absent", {}), ("both added", FILES)):
                with self.subTest(answer=label, head=head_label):
                    self.summary.unlink(missing_ok=True)
                    self.assertFails(self.run_pr(response, main={}, head=head), NO_ANSWER)
                    self.assertEqual(len(self.hemi.seen), contract.PR_ATTEMPTS, "silence is asked again")

    def test_not_found_or_unprocessable_with_no_files_on_main_fails_naming_the_status(self):
        for status in (404, 422):
            with self.subTest(status=status):
                self.summary.unlink(missing_ok=True)
                code = self.run_pr((status, {"error": BODY_MARKER}), main={}, head={})
                self.assertFails(code, f"hemi answered HTTP {status}; {REPOSITORY}'s settings live in hemi, "
                                       "so the checks cannot run.")


class PullRequestTests(PullRequestCase):
    def test_a_pull_request_that_drops_the_token_grant_and_adds_the_files_back_fails(self):
        """A pull request can drop id-token from its own ci.yml to force hemi's silence; main decides."""
        self.grant = False
        code = self.run_pr((200, fixture_copy("hemi")), main={}, head=FILES)
        self.assertFails(code, contract.PR_SENTENCES["no_grant"].format(app=REPOSITORY))
        self.assertEqual(self.hemi.seen, [], "nothing is asked without a token")

    def test_without_the_token_grant_a_repository_on_its_files_runs_as_today(self):
        self.grant = False
        self.assertSource(self.run_pr((200, fixture_copy("hemi")), main=FILES, head=FILES), "files")
        self.assertIn("no job token for hemi; using the repository's files", self.summary_text)

    def test_a_stacked_pull_request_is_judged_by_main_not_by_its_base(self):
        self.branch("base", FILES)
        for label, response, sentence in (
            ("401", (401, {}), "hemi refused this pull request's token (HTTP 401): " + NEW_OR_CUT_OVER),
            ("hemi", (200, fixture_copy("hemi")),
             f"{PROFILE} was added back; travel's settings live in hemi. {VALUES} was added back; travel's "
             "settings live in hemi."),
        ):
            with self.subTest(answer=label):
                self.summary.unlink(missing_ok=True)
                self.assertFails(self.run_pr(response, main={}, head=FILES), sentence)

    def test_each_attempt_asks_with_its_own_token_and_backs_off(self):
        busy = (503, {"error": "not connected"})
        code = self.run_pr(busy, busy, (200, fixture_copy("hemi")), main={}, head={})
        self.assertSource(code, "hemi")
        self.assertEqual([(method, path) for method, path, _ in self.hemi.seen], [("GET", "/api/v1/contract")] * 3)
        self.assertEqual([auth for _, _, auth in self.hemi.seen], ["Bearer " + value for value in self.minted])
        self.assertEqual(len(set(self.minted)), 3)
        self.assertEqual(self.sleeps, list(contract.PR_BACKOFF))

    def test_silence_is_asked_at_most_the_attempt_count(self):
        self.assertFails(self.run_pr((503, {}), main={}, head={}), NO_ANSWER)
        self.assertEqual(len(self.hemi.seen), contract.PR_ATTEMPTS)
        self.assertEqual(len(self.minted), contract.PR_ATTEMPTS)

    def test_a_runner_that_cannot_resolve_hemis_address_is_named(self):
        """A hosted runner has no cluster DNS: the sentence says which runner, and what to pass instead."""
        self.hemi_url = "http://hemi-app.hemi.invalid:3000"
        unreachable = ("the contract job must run on a Lab runner: ci-light-abc12 cannot reach hemi's "
                       "in-cluster address. Pass a Lab pool as `runner`.")
        self.assertFails(self.run_pr((200, fixture_copy("hemi")), main={}, head={}), unreachable)
        self.assertEqual(len(self.minted), contract.PR_ATTEMPTS, "a lookup that fails is asked twice more")
        self.assertEqual(self.sleeps, list(contract.PR_BACKOFF))
        self.summary.unlink(missing_ok=True)
        self.assertSource(self.run_pr((200, fixture_copy("hemi")), main=FILES, head=FILES), "files")
        self.assertIn(unreachable, self.summary_text)

    def test_the_runner_is_named_when_its_name_is_safe_to_print(self):
        """A hosted runner's name has spaces; a name with a workflow-command or shell character is not printed."""
        self.hemi_url = "http://hemi-app.hemi.invalid:3000"
        for name, shown in (
            ("GitHub Actions 1000000123", "GitHub Actions 1000000123"),
            ("ci-light-abc12", "ci-light-abc12"),
            ("x::error::y", "this runner"),
            ("a\nb", "this runner"),
            ("a%0Ab", "this runner"),
            ("a$(id)", "this runner"),
            ("a;b", "this runner"),
            ("`id`", "this runner"),
            (" padded", "this runner"),
            ("", "this runner"),
        ):
            with self.subTest(name=name):
                self.runner = name
                self.summary.unlink(missing_ok=True)
                self.assertFails(self.run_pr((200, fixture_copy("hemi")), main={}, head={}),
                                 f"the contract job must run on a Lab runner: {shown} cannot reach hemi's in-cluster "
                                 "address. Pass a Lab pool as `runner`.")

    def test_a_lookup_that_fails_once_on_a_lab_runner_is_asked_again(self):
        real = socket.getaddrinfo
        failures = []

        def flaky(*args, **kwargs):
            if not failures:
                failures.append(args[0])
                raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
            return real(*args, **kwargs)

        with patch.object(socket, "getaddrinfo", flaky):
            code = self.run_pr((200, fixture_copy("hemi")), main={}, head={})
        self.assertSource(code, "hemi")
        self.assertEqual((len(failures), len(self.hemi.seen), self.sleeps), (1, 1, [contract.PR_BACKOFF[0]]))

    def test_a_rerun_whose_merge_commit_is_older_than_the_clean_up_says_to_update_the_branch(self):
        """The pull request's merge commit was made on main before hemi's clean-up merged."""
        before = self.branch("main", FILES)
        self.branch("main", {}, parents=(before,))
        after = self.branch("main", {}, parents=(git("rev-parse", "HEAD", cwd=self.work).strip(),))
        self.branch("pr", FILES, parents=(before,))
        self.checkout("main", ".wacd-main")
        self.checkout("pr", ".wacd-head")
        self.assertFails(self.invoke((200, fixture_copy("hemi"))),
                         "This run's merge commit is older than travel's clean-up; update the branch and re-run.")
        self.branch("pr", FILES, parents=(after,))
        self.checkout("pr", ".wacd-head")
        self.summary.unlink()
        self.assertFails(self.invoke((200, fixture_copy("hemi"))),
                         " ".join(f"{path} was added back; travel's settings live in hemi." for path in (PROFILE, VALUES)))

    def test_a_token_github_would_not_mint_is_not_hemis_silence(self):
        refusals = []

        def refuse():
            refusals.append(1)
            raise contract.shadow.Unanswered("GitHub did not return a usable OIDC token")

        self.minter = refuse
        self.assertFails(self.run_pr((200, fixture_copy("hemi")), main={}, head={}),
                         "GitHub would not mint a job token for hemi; re-run the checks.")
        self.assertEqual(self.hemi.seen, [], "hemi is never asked without a token")
        self.assertEqual(len(refusals), contract.PR_ATTEMPTS, "a mint failure is tried again")
        self.summary.unlink(missing_ok=True)
        self.assertSource(self.run_pr((200, fixture_copy("hemi")), main=FILES, head=FILES), "files")
        self.assertIn("GitHub would not mint a job token for hemi; using the repository's files.", self.summary_text)

    def test_a_refusal_outside_a_pull_request_names_the_event_not_a_pull_request(self):
        """hemi admits wac-ci only for pull_request runs; a push run's 401 is about the event."""
        self.event = "push"
        code = self.run_pr((401, {"error": "unauthorized"}), main={}, head={})
        self.assertFails(code, "hemi refused the job token of this push run (HTTP 401): it admits wac-ci only "
                               "for pull_request runs; " + NEW_OR_CUT_OVER)
        self.assertNotIn("pull request's", self.stderr)

    def test_a_clean_up_refused_outside_a_pull_request_names_the_event(self):
        self.event = "push"
        code = self.run_pr((401, {"error": "unauthorized"}), main=FILES, head={})
        self.assertFails(code, "hemi refused the job token of this push run (HTTP 401): it admits wac-ci only "
                               f"for pull_request runs, and this pull request removes {REPOSITORY}'s files.")
        self.assertNotIn("pull request's", self.stderr)

    def test_an_answer_that_trickles_in_past_the_attempt_bound_is_silence(self):
        """Each read meets the socket timeout; only the attempt's own bound stops it."""
        self.assertFails(self.run_pr("trickle", main={}, head={}), NO_ANSWER)
        self.assertEqual(len(self.hemi.seen), contract.PR_ATTEMPTS)

    def test_the_values_path_on_main_comes_from_mains_profile(self):
        moved = json.loads(FILES[PROFILE])
        moved["values_file"] = "deploy/other.yaml"
        main = {PROFILE: json.dumps(moved), "deploy/other.yaml": FILES[VALUES]}
        code = self.run_pr((200, fixture_copy("hemi")), main=main, head=main)
        self.assertSource(code, "hemi")
        self.assertIn("deploy/other.yaml is still on main", self.summary_text)

    def test_an_answer_that_cannot_be_read_is_not_hemis_settings(self):
        for label, response in (
            ("not JSON", (200, BODY_MARKER.encode())),
            ("not an object", (200, [BODY_MARKER])),
            ("another repository", (200, {**fixture_copy(), "identity": {**fixture_copy()["identity"],
                                                                         "repositoryId": "111"}})),
            ("version 2", (200, {**fixture_copy(), "version": 2})),
            ("unknown source", (200, {**fixture_copy(), "flags": {"source": BODY_MARKER}})),
            ("no values path", (200, {**fixture_copy(), "delivery": {
                key: value for key, value in fixture_copy()["delivery"].items() if key != "valuesFile"}})),
            ("profile does not derive", (200, {**fixture_copy(), "gates": []})),
        ):
            with self.subTest(answer=label, main="files"):
                self.summary.unlink(missing_ok=True)
                self.assertSource(self.run_pr(response, main=FILES, head=FILES), "files")
                self.assertIn("hemi's answer could not be read; using the repository's files", self.summary_text)
            with self.subTest(answer=label, main="cut over"):
                self.summary.unlink(missing_ok=True)
                code = self.run_pr(response, main={}, head={})
                self.assertFails(code, contract.PR_SENTENCES["unreadable"].format(app=REPOSITORY))

    def test_pr_takes_no_address_for_hemi(self):
        usage = subprocess.run(
            [sys.executable, str(SCRIPT), "pr", "--help"], capture_output=True, text=True, check=False
        )
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertEqual(
            {word for word in usage.stdout.split() if word.startswith("--")},
            {"--help", "--out", "--main", "--head"},
        )
        self.assertEqual(contract.shadow.URL, "http://hemi-app.hemi.svc.cluster.local:3000")
        self.assertEqual(contract.shadow.AUDIENCE, "wacwini:hemi")


DEFAULT_DOCS = '["docs/**","mkdocs.yml","*.md"]'


def profile_with(**ci):
    data = json.loads(FILES[PROFILE])
    data["ci"].update(ci)
    return {PROFILE: json.dumps(data), VALUES: FILES[VALUES]}


class DocsPathsTests(PullRequestCase):
    """`docs_paths` is the scope job's only source of patterns: the list the settings this run uses hold.

    It is the effective list, the default when the key is absent, from hemi's copy when the source is hemi
    and from the merge commit's .github/wacd.json when it is the files; empty whenever that list cannot be
    read or is refused, which the scope job reads as "run the checks".
    """

    def docs_paths(self, code, source):
        self.assertSource(code, source)
        return self.outputs()["docs_paths"]

    def copy_with(self, **delivery):
        value = fixture_copy("hemi")
        value["delivery"].update(delivery)
        return 200, value

    def test_hemi_source_reads_the_copys_list_and_keeps_absent_apart_from_empty(self):
        for label, answer, expected in (
            ("absent", self.hemi_answer(), DEFAULT_DOCS),
            ("empty", self.copy_with(docsPaths=[]), "[]"),
            ("a list", self.copy_with(docsPaths=["docs/guides/**", "*.md"]), '["docs/guides/**","*.md"]'),
        ):
            with self.subTest(docs_paths=label):
                self.assertEqual(self.docs_paths(self.run_pr(answer, main={}, head={}), "hemi"), expected)

    def test_a_copys_refused_list_leaves_the_output_empty_and_the_contract_job_green(self):
        for label, docs in (("code", ["src/**"]), ("null", None), ("a gate's path", ["docs/**"])):
            with self.subTest(docs_paths=label):
                answer = self.copy_with(docsPaths=docs)
                if label == "a gate's path":
                    answer[1]["gates"][0]["commands"].append("node scripts/check.mjs docs/llc.md")
                self.assertEqual(self.docs_paths(self.run_pr(answer, main={}, head={}), "hemi"), "")

    def test_files_source_reads_the_merge_commits_profile(self):
        for label, head, expected in (
            ("absent", FILES, DEFAULT_DOCS),
            ("empty", profile_with(docs_paths=[]), "[]"),
            ("a list", profile_with(docs_paths=["docs/**"]), '["docs/**"]'),
            ("refused", profile_with(docs_paths=["**"]), ""),
            ("unreadable", {PROFILE: "{not json", VALUES: FILES[VALUES]}, ""),
            ("removed", {}, ""),
        ):
            with self.subTest(head=label):
                self.summary.unlink(missing_ok=True)
                code = self.run_pr(self.hemi_answer("files"), main=FILES, head=head)
                self.assertEqual(self.docs_paths(code, "files"), expected)

    def test_files_source_without_hemi_still_names_the_list(self):
        code = self.run_pr("drop", "drop", "drop", main=FILES, head=profile_with(docs_paths=["docs/**"]))
        self.assertEqual(self.docs_paths(code, "files"), '["docs/**"]')


if __name__ == "__main__":
    unittest.main()
