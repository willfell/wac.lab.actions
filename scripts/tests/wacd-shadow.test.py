"""wacd-shadow compares hemi's copy with the files and never fails or leaks the token.

Helm is the real one where the comparison depends on how it reads YAML, gated
like wacd-chart.test.py by WACD_REQUIRE_HELM (CI sets it). The failure paths
use small stand-in executables on a PATH the test controls.

One ThreadingHTTPServer stands in for both the GitHub OIDC token endpoint and
hemi. In-process scenarios point the script's hemi address and token-endpoint
check at it with patch.object, the way wacd-release.test.py points ci-release's
URL at its stub gateway; the subprocess scenarios run the CLI unpatched.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "wacd-shadow.py"
SPEC = importlib.util.spec_from_file_location("wacd_shadow", SCRIPT)
shadow = importlib.util.module_from_spec(SPEC)
sys.modules["wacd_shadow"] = shadow
SPEC.loader.exec_module(shadow)

REQUEST_BEARER = "runner.request.bearer"
TOKEN = "minted.job.token"
PROFILE = {"schema_version": 1, "app": "travel", "values_file": "deploy/values.yaml"}
VALUES = "image:\n  repository: travel-app\nreplicas: 2\n"
VALUES_AS_HELM_READS_THEM = {"image": {"repository": "travel-app"}, "replicas": 2}
SILENT = "hemi did not answer; nothing was compared.\n"
HELM_UNREAD = "Helm could not read the values; hemi compared its own reading."
HAVE_HELM = shutil.which("helm") is not None
NEEDS_HELM = unittest.skipUnless(
    HAVE_HELM or os.environ.get("WACD_REQUIRE_HELM"), "helm is not installed"
)
YAML_11_VALUES = """\
nothing: null
nested:
  nothing: null
list: [null, 1]
yes_word: yes
on_word: on
octal: 010
base: &base
  replicas: 2
derived:
  <<: *base
  tier: web
y: letter
"""
YAML_11_AS_HELM_READS_IT = {
    "nothing": None,
    "nested": {"nothing": None},
    "list": [None, 1],
    "yes_word": True,
    "on_word": True,
    "octal": 8,
    "base": {"replicas": 2},
    "derived": {"replicas": 2, "tier": "web"},
    "true": "letter",
}
CLOSE = object()


def contract(revision):
    return {"identity": {"app": "travel", "revision": revision}, "profile": PROFILE}


class Stub:
    """The token endpoint at /token and hemi's two routes, recording every request."""

    def __init__(self, contract_reply, shadow_reply=(200, {"equal": True, "differences": []})):
        self.replies = {"/api/v1/contract": contract_reply, "/api/v1/shadow": shadow_reply}
        self.seen = []
        self.connections = 0
        self.minted = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self, method):
                url = urllib.parse.urlsplit(self.path)
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(size)) if size else None
                outer.seen.append((method, url.path, url.query, self.headers.get("Authorization"), body))
                if url.path == "/token":
                    outer.minted += 1
                    reply = (200, {"value": f"{TOKEN}.{outer.minted}"})
                else:
                    reply = outer.replies[url.path]
                if callable(reply):
                    reply = reply(self.headers.get("Authorization"))
                if reply is CLOSE:
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                status, value = reply
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self.answer("GET")

            def do_POST(self):
                self.answer("POST")

            def log_message(self, *args):
                pass

        class Server(ThreadingHTTPServer):
            def verify_request(self, request, client_address):
                outer.connections += 1
                return True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def hemi_requests(self):
        return [s for s in self.seen if s[1] != "/token"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def app_repo(tmp, values=VALUES):
    root = Path(tmp) / "app"
    (root / ".github").mkdir(parents=True)
    (root / "deploy").mkdir()
    (root / ".github" / "wacd.json").write_text(json.dumps(PROFILE, indent=2) + "\n")
    (root / "deploy" / "values.yaml").write_text(values)
    return root / ".github" / "wacd.json"


class Harness:
    def run_against(self, stub, values=VALUES, path=None, helm_timeout=None):
        """Run main() against the stub from an unrelated directory; return (code, stdout, stderr, summary).

        path replaces PATH for the run, so what is installed on the machine does not decide the result.
        """
        with tempfile.TemporaryDirectory() as tmp:
            profile = app_repo(tmp, values)
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            summary = Path(tmp) / "summary.md"
            env = {
                "ACTIONS_ID_TOKEN_REQUEST_URL": stub.url + "/token?audience=wrong&api-version=2.0",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQUEST_BEARER,
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            if path is not None:
                env["PATH"] = str(path)
            out, err = io.StringIO(), io.StringIO()
            with (
                patch.dict(os.environ, env),
                patch.object(shadow, "URL", stub.url),
                patch.object(shadow, "TOKEN_SCHEME", "http"),
                patch.object(shadow, "TOKEN_HOST_SUFFIX", "127.0.0.1"),
                patch.object(shadow, "HELM_TIMEOUT", helm_timeout or shadow.HELM_TIMEOUT),
                contextlib.chdir(elsewhere),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                code = shadow.main(["wacd-shadow.py", str(profile)])
            text = summary.read_text() if summary.exists() else ""
        for secret in (REQUEST_BEARER, TOKEN):
            self.assertNotIn(secret, out.getvalue() + err.getvalue() + text)
        return code, out.getvalue(), err.getvalue(), text

    def posted(self, stub):
        return stub.hemi_requests()[1][4]


@NEEDS_HELM
class ShadowTests(Harness, unittest.TestCase):

    def test_an_equal_copy_is_reported_with_its_number(self):
        stub = Stub((200, contract(7)))
        try:
            code, _, _, summary = self.run_against(stub)
        finally:
            stub.close()
        self.assertEqual(code, 0)
        self.assertEqual(summary, "hemi's copy matched the files (copy 7).\n")
        tokens = [s for s in stub.seen if s[1] == "/token"]
        self.assertEqual(len(tokens), 2)
        for _, _, query, auth, _ in tokens:
            self.assertEqual(auth, "Bearer " + REQUEST_BEARER)
            self.assertEqual(
                urllib.parse.parse_qsl(query),
                [("api-version", "2.0"), ("audience", "wacwini:hemi")],
            )
        self.assertEqual(
            stub.hemi_requests(),
            [
                ("GET", "/api/v1/contract", "", f"Bearer {TOKEN}.1", None),
                (
                    "POST",
                    "/api/v1/shadow",
                    "",
                    f"Bearer {TOKEN}.2",
                    {
                        "revision": 7,
                        "profile": PROFILE,
                        "values": VALUES,
                        "helmValues": VALUES_AS_HELM_READS_THEM,
                    },
                ),
            ],
        )

    def test_each_difference_gets_its_own_line(self):
        differences = [
            {"path": "profile.health_url", "contract": "https://a.example/h", "files": "https://b.example/h"},
            {"path": "values.replicas", "contract": 3, "files": None},
        ]
        stub = Stub((200, contract(4)), (200, {"equal": False, "differences": differences}))
        try:
            code, _, _, summary = self.run_against(stub)
        finally:
            stub.close()
        self.assertEqual(code, 0)
        self.assertEqual(
            summary.splitlines(),
            [
                "hemi's copy differs from the files in 2 places (copy 4).",
                '- profile.health_url: hemi\'s copy has "https://a.example/h", the files have "https://b.example/h"',
                "- values.replicas: hemi's copy has 3, the files have null",
            ],
        )

    def test_a_contract_route_that_is_not_ready_compares_nothing(self):
        stub = Stub((503, {"error": "not connected"}))
        try:
            code, _, _, summary = self.run_against(stub)
        finally:
            stub.close()
        self.assertEqual((code, summary), (0, SILENT))
        self.assertEqual([s[:2] for s in stub.hemi_requests()], [("GET", "/api/v1/contract")])

    def test_a_dropped_connection_compares_nothing(self):
        stub = Stub((200, contract(2)), CLOSE)
        try:
            code, _, _, summary = self.run_against(stub)
        finally:
            stub.close()
        self.assertEqual((code, summary), (0, SILENT))

    def test_the_token_is_never_echoed_from_what_hemi_answers(self):
        """hemi answers with the token it was sent; the script reports none of it."""

        def echoed(auth):
            difference = {"path": "values.token", "contract": auth.removeprefix("Bearer "), "files": None}
            return (200, {"equal": False, "differences": [difference]})

        cases = {
            "copy number": ((200, {"identity": {"revision": f"{TOKEN}.1"}}), None, [SILENT]),
            "refusal": ((200, contract(1)), (401, {"error": f"rejected {TOKEN}.2"}), [SILENT]),
            "comparison": (
                (200, contract(1)),
                (200, {"equal": False, "differences": f"{TOKEN}.2"}),
                [SILENT],
            ),
            "a well-formed difference": (
                (200, contract(1)),
                echoed,
                [
                    "hemi's copy differs from the files in 1 place (copy 1).",
                    "- values.token: hemi's copy has \"[token]\", the files have null",
                ],
            ),
        }
        for label, (contract_reply, shadow_reply, expected) in cases.items():
            with self.subTest(label):
                stub = Stub(contract_reply, shadow_reply)
                try:
                    code, _, _, summary = self.run_against(stub)
                finally:
                    stub.close()
                self.assertEqual(code, 0)
                self.assertEqual(summary.splitlines(), [line.rstrip("\n") for line in expected])
                self.assertEqual(len(stub.hemi_requests()), 1 if shadow_reply is None else 2)

    def test_an_answer_that_contradicts_itself_compares_nothing(self):
        difference = {"path": "values.replicas", "contract": 3, "files": 2}
        cases = {
            "equal with a difference": {"equal": True, "differences": [difference]},
            "not equal with no difference": {"equal": False, "differences": []},
        }
        for label, answer in cases.items():
            with self.subTest(label):
                stub = Stub((200, contract(3)), (200, answer))
                try:
                    code, _, _, summary = self.run_against(stub)
                finally:
                    stub.close()
                self.assertEqual((code, summary), (0, SILENT))

    def test_helm_reading_of_yaml_1_1_forms_is_posted_beside_the_text(self):
        stub = Stub((200, contract(5)))
        try:
            code, _, _, summary = self.run_against(stub, values=YAML_11_VALUES)
        finally:
            stub.close()
        self.assertEqual((code, summary), (0, "hemi's copy matched the files (copy 5).\n"))
        body = self.posted(stub)
        self.assertEqual(body["values"], YAML_11_VALUES)
        self.assertEqual(body["helmValues"], YAML_11_AS_HELM_READS_IT)

    def test_values_helm_cannot_parse_are_posted_without_its_reading(self):
        stub = Stub((200, contract(5)))
        try:
            code, _, _, summary = self.run_against(stub, values="a: [unclosed\n")
        finally:
            stub.close()
        self.assertEqual((code, summary), (0, f"hemi's copy matched the files (copy 5).\n{HELM_UNREAD}\n"))
        self.assertEqual(
            self.posted(stub),
            {"revision": 5, "profile": PROFILE, "values": "a: [unclosed\n"},
        )


class HelmStandInTests(Harness, unittest.TestCase):
    """Helm absent or misbehaving: the files are still posted and the release is untouched."""

    def bin_dir(self, tmp, script=None):
        directory = Path(tmp) / "bin"
        directory.mkdir()
        if script is not None:
            helm = directory / "helm"
            helm.write_text("#!/bin/sh\n" + script)
            helm.chmod(helm.stat().st_mode | stat.S_IXUSR)
        return directory

    def assert_posts_files_alone(self, stub, result):
        code, _, _, summary = result
        self.assertEqual((code, summary), (0, f"hemi's copy matched the files (copy 3).\n{HELM_UNREAD}\n"))
        self.assertEqual(
            self.posted(stub), {"revision": 3, "profile": PROFILE, "values": VALUES}
        )

    def test_no_helm_on_path_posts_the_files_alone(self):
        stub = Stub((200, contract(3)))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = self.run_against(stub, path=self.bin_dir(tmp))
        finally:
            stub.close()
        self.assert_posts_files_alone(stub, result)

    def test_a_helm_that_fails_or_answers_badly_posts_the_files_alone(self):
        cases = {
            "exits non-zero": "echo '{}'; exit 1\n",
            "prints no JSON": "echo not json\n",
            "prints a JSON list": "echo '[1]'\n",
            "prints a JSON scalar": "echo '3'\n",
            "never finishes": f"exec {shutil.which('sleep')} 30\n",
        }
        for label, script in cases.items():
            with self.subTest(label):
                stub = Stub((200, contract(3)))
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        result = self.run_against(
                            stub, path=self.bin_dir(tmp, script), helm_timeout=1
                        )
                finally:
                    stub.close()
                self.assert_posts_files_alone(stub, result)

    def test_helm_runs_without_a_cluster_and_without_the_job_token(self):
        stub = Stub((200, contract(3)))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                seen = Path(tmp) / "env"
                script = f"{shutil.which('env')} > '{seen}'\necho '{{\"a\": 1}}'\n"
                code, _, _, _ = self.run_against(stub, path=self.bin_dir(tmp, script))
                helm_env = dict(
                    line.split("=", 1) for line in seen.read_text().splitlines() if "=" in line
                )
        finally:
            stub.close()
        self.assertEqual(code, 0)
        self.assertEqual(self.posted(stub)["helmValues"], {"a": 1})
        self.assertEqual(helm_env["KUBECONFIG"], "/dev/null")
        self.assertFalse([k for k in helm_env if k.startswith("ACTIONS_") or k == "GITHUB_STEP_SUMMARY"])


class CommandLineTests(unittest.TestCase):
    def run_cli(self, profile, token_env):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            env = {k: v for k, v in os.environ.items() if not k.startswith("ACTIONS_")}
            env.update(token_env, GITHUB_STEP_SUMMARY=str(summary))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(profile)],
                capture_output=True, text=True, env=env, timeout=60,
            )
            text = summary.read_text()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(REQUEST_BEARER, result.stdout + result.stderr + text)
        return text

    def test_no_usable_token_endpoint_compares_nothing_and_sends_nothing(self):
        stub = Stub((200, contract(1)))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                profile = app_repo(tmp)
                cases = {
                    "absent from the environment": {},
                    "plain http": {
                        "ACTIONS_ID_TOKEN_REQUEST_URL": stub.url + "/token",
                        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQUEST_BEARER,
                    },
                    "https to a host that is not GitHub's": {
                        "ACTIONS_ID_TOKEN_REQUEST_URL": f"https://127.0.0.1:{stub.port}/token",
                        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQUEST_BEARER,
                    },
                }
                for label, token_env in cases.items():
                    with self.subTest(label):
                        self.assertEqual(self.run_cli(profile, token_env), SILENT)
        finally:
            stub.close()
        self.assertEqual(stub.connections, 0)

    def test_an_unreadable_profile_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_cli(Path(tmp) / "absent.json", {}), SILENT)


if __name__ == "__main__":
    unittest.main()
