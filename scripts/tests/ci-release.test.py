"""Migration evidence and the shared transport must fail closed without leaking tokens."""

import argparse
import importlib.util
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

SPEC = importlib.util.spec_from_file_location(
    "ci_release", Path(__file__).parents[1] / "ci-release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SOURCE = "a" * 40
DIGEST = "sha256:" + "c" * 64


def migrate_args(**overrides):
    values = {
        "operation": "migrate",
        "image_tag": "release",
        "image_digest": DIGEST,
        "wait_timeout": 30,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def receipt():
    return {
        "source_sha": SOURCE,
        "image": "localhost:30500/warehouse:release@" + DIGEST,
        "run_id": "123",
        "run_attempt": "2",
        "succeeded": True,
        "jobs": [
            {"target": n, "uid": n + "-uid", "succeeded": True}
            for n in ["warehouse", "ecosystem"]
        ],
    }


RUN_ENV = {"GITHUB_SHA": SOURCE, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}


class ReleaseTests(unittest.TestCase):
    def test_migrate_sends_only_the_image_identity_and_returns_the_receipt(self):
        with (
            patch.dict(os.environ, RUN_ENV),
            patch.object(release.ssl, "create_default_context"),
            patch.object(release, "request", return_value=(200, receipt())) as request,
        ):
            self.assertEqual(release.run(migrate_args()), receipt())
            self.assertEqual(
                request.call_args.args[0],
                {"operation": "migrate", "image_tag": "release", "image_digest": DIGEST},
            )

    def test_busy_service_is_retried_within_the_deadline(self):
        with (
            patch.dict(os.environ, RUN_ENV),
            patch.object(release.ssl, "create_default_context"),
            patch.object(release.time, "sleep"),
            patch.object(
                release,
                "request",
                side_effect=[(409, {}), (503, {}), (200, receipt())],
            ) as request,
        ):
            self.assertEqual(release.run(migrate_args()), receipt())
            self.assertEqual(request.call_count, 3)

    def test_retired_operations_and_bad_images_never_reach_the_service(self):
        for args in [
            migrate_args(operation="status"),
            migrate_args(operation="sync"),
            migrate_args(image_tag=""),
            migrate_args(image_tag="-bad"),
            migrate_args(image_digest="sha256:short"),
        ]:
            with (
                patch.dict(os.environ, RUN_ENV),
                patch.object(release, "request") as request,
            ):
                with self.assertRaises(release.ReleaseError):
                    release.run(args)
                request.assert_not_called()

    def test_migrations_require_both_jobs_and_this_attempt(self):
        with patch.dict(os.environ, RUN_ENV):
            release.migrated(receipt(), SOURCE, "release", DIGEST)
            for change in [
                {"run_attempt": "1"},
                {"succeeded": False},
                {"jobs": receipt()["jobs"][:1]},
            ]:
                with self.assertRaises(release.ReleaseError):
                    release.migrated(dict(receipt(), **change), SOURCE, "release", DIGEST)

    def test_oidc_url_is_https_github_and_audience_is_fixed(self):
        with (
            patch.dict(
                os.environ,
                {
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "http://evil.invalid/token",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private",
                },
            ),
            patch.object(release, "fetch") as fetch,
        ):
            with self.assertRaises(release.ReleaseError):
                release.oidc_token()
            fetch.assert_not_called()
        with (
            patch.dict(
                os.environ,
                {
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://test.actions.githubusercontent.com/token?audience=wrong&x=1",
                    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private",
                },
            ),
            patch.object(
                release, "fetch", return_value=(200, {"value": "signed-token"})
            ) as fetch,
        ):
            self.assertEqual(release.oidc_token(), "signed-token")
            url = fetch.call_args.args[0].full_url
            self.assertIn("audience=wacwini%3Aci-deploy", url)
            self.assertNotIn("wrong", url)

    def test_every_poll_obtains_new_oidc_and_denial_is_not_retried(self):
        with (
            patch.object(
                release, "oidc_token", side_effect=["first", "second"]
            ) as token,
            patch.object(release, "fetch", return_value=(200, {})) as fetch,
        ):
            release.request({}, None, 20)
            release.request({}, None, 20)
            self.assertEqual(token.call_count, 2)
            self.assertEqual(
                [c.args[0].headers["Authorization"] for c in fetch.call_args_list],
                ["Bearer first", "Bearer second"],
            )
        args = migrate_args()
        with (
            patch.dict(os.environ, {"GITHUB_SHA": SOURCE}),
            patch.object(release.ssl, "create_default_context"),
            patch.object(release, "request", return_value=(403, {})) as request,
        ):
            with self.assertRaises(release.ReleaseError):
                release.run(args)
            self.assertEqual(request.call_count, 1)

    def test_missing_trust_never_sends_token(self):
        args = migrate_args()
        with (
            patch.dict(os.environ, {"GITHUB_SHA": SOURCE}),
            patch.object(
                release.ssl, "create_default_context", side_effect=FileNotFoundError
            ),
            patch.object(release, "request") as request,
        ):
            with self.assertRaises(release.ReleaseError):
                release.run(args)
            request.assert_not_called()

    def test_real_tls_transport_requires_trust_and_refuses_redirects(self):
        with tempfile.TemporaryDirectory() as directory:
            cert = Path(directory) / "cert.pem"
            key = Path(directory) / "key.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    if self.path == "/redirect":
                        self.send_response(302)
                        self.send_header("Location", "https://localhost:1/leak")
                        self.end_headers()
                    else:
                        body = b'{"accepted":true}'
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

            server = HTTPServer(("127.0.0.1", 0), Handler)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"https://localhost:{server.server_port}"
            try:
                trust = ssl.create_default_context(cafile=str(cert))
                self.assertEqual(
                    release.fetch(urllib.request.Request(url), trust, 5),
                    (200, {"accepted": True}),
                )
                self.assertEqual(
                    release.fetch(urllib.request.Request(url + "/redirect"), trust, 5),
                    (302, {}),
                )
                with self.assertRaises(release.ReleaseError):
                    release.fetch(
                        urllib.request.Request(url), ssl.create_default_context(), 5
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_loaders_find_the_names_they_depend_on(self):
        for name in ["URL", "CA_FILE", "ReleaseError", "NoRedirect"]:
            self.assertTrue(hasattr(release, name), name)
        for name in ["request", "oidc_token", "fetch", "migrated"]:
            self.assertTrue(callable(getattr(release, name, None)), name)

    def run_action(self, operation):
        root = Path(__file__).resolve().parents[2]
        action = yaml.safe_load((root / "lab-release/action.yml").read_text())
        self.assertEqual(
            set(action["inputs"]),
            {"operation", "image_tag", "image_digest", "wait_timeout"},
        )
        step = next(s for s in action["runs"]["steps"] if s.get("id") == "release")
        script = step["run"].replace(
            "${{ github.action_path }}", str(root / "lab-release")
        )
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / "bin"
            bin_dir.mkdir()
            (bin_dir / "python").symlink_to(sys.executable)
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "RUNNER_TEMP": directory,
                "GITHUB_OUTPUT": str(Path(directory) / "output"),
                "RELEASE_OPERATION": operation,
                "RELEASE_TAG": "release",
                "RELEASE_DIGEST": DIGEST,
                "RELEASE_TIMEOUT": "2700",
            }
            return subprocess.run(
                ["bash", "-c", script], env=env, capture_output=True, text=True
            )

    def test_action_passes_only_flags_the_client_accepts(self):
        result = self.run_action("migrate")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Release verification failed", result.stderr)

    def test_action_refuses_a_retired_operation_before_any_request(self):
        result = self.run_action("sync")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("invalid choice", result.stderr)

if __name__ == "__main__":
    unittest.main()
