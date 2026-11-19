"""wacd-contract asks the gateway for the app's settings copy and decides where plan reads from.

A ThreadingHTTPServer stands in for the gateway. Each scenario runs the CLI
in-process with ci-release's gateway URL pointed at it and its token minting
replaced, the way wacd-release.test.py points the same names at its stub
gateway, so every request travels over real HTTP with a fresh token.
"""

import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
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

    def test_the_wait_timeout_must_be_a_bounded_whole_number(self):
        for bad in ("0", "-5", "3601", "2.5", "soon", ""):
            with self.subTest(wait=bad):
                self.summary.unlink(missing_ok=True)
                code = self.fetch(answer(None), wait=bad)
                self.assertRefused(code, "--wait-timeout")
                self.assertEqual(self.gateway.seen, [], "nothing is asked with a bad bound")

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

    def test_an_unverified_caller_gets_the_generic_sentence_naming_an_old_gateway(self):
        code = self.fetch((403, {"error": "release request denied"}))
        self.assertRefused(code, contract.SENTENCES["unverified"])
        self.assertIn("does not yet serve the contract operation", self.stderr)
        self.assertIn("wacwini/wac.lab#570", self.summary_text)

    def test_a_coded_body_on_another_status_is_not_a_denial(self):
        for status in (400, 404, 500):
            with self.subTest(status=status):
                self.summary.unlink(missing_ok=True)
                code = self.fetch((status, {"error": "x", "reason": "fleet_copy_missing"}))
                self.assertRefused(code, f"HTTP {status}")
                self.assertNotIn(contract.REFUSALS["fleet_copy_missing"], self.stderr)
                self.assertEqual(len(self.gateway.seen), 1)

    def test_a_refused_connection_fails_at_once(self):
        dead = Gateway((200, {}))
        dead.close()
        self.dead_url = dead.url
        code = self.fetch((200, {}), wait=WAIT)
        self.assertRefused(code, "could not be reached")
        self.assertEqual(len(self.minted), 1, "a refused connection is not retried, as for a release")
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


if __name__ == "__main__":
    unittest.main()
