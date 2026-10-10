"""Release evidence must fail closed without leaking tokens or skipping health."""

import argparse
import copy
import importlib.util
import os
import ssl
import subprocess
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
PIN = "b" * 40
IMAGE = "localhost:30500/baton:release@sha256:" + "c" * 64


def evidence():
    return {
        "source_sha": SOURCE,
        "pin_sha": PIN,
        "sync": {"revision": PIN, "status": "Synced"},
        "health": {"status": "Healthy"},
        "deployments": [
            {
                "name": "baton",
                "generation": 2,
                "replicas": 2,
                "images": [IMAGE],
                "status": {
                    "observedGeneration": 2,
                    "replicas": 2,
                    "updatedReplicas": 2,
                    "readyReplicas": 2,
                    "availableReplicas": 2,
                },
            }
        ],
    }


class ReleaseTests(unittest.TestCase):
    def test_exact_revision_image_and_two_ready_replicas(self):
        value = evidence()
        self.assertTrue(release.ready(value, PIN, SOURCE, IMAGE, 2))
        for path, replacement in [
            (("sync", "revision"), SOURCE),
            (("health", "status"), "Progressing"),
        ]:
            wrong = copy.deepcopy(value)
            wrong[path[0]][path[1]] = replacement
            self.assertFalse(release.ready(wrong, PIN, SOURCE, IMAGE, 2))
        for key in [
            "observedGeneration",
            "replicas",
            "updatedReplicas",
            "readyReplicas",
            "availableReplicas",
        ]:
            wrong = copy.deepcopy(value)
            wrong["deployments"][0]["status"][key] = 1
            self.assertFalse(release.ready(wrong, PIN, SOURCE, IMAGE, 2))
        self.assertFalse(release.ready(value, PIN, SOURCE, IMAGE + "wrong", 2))
        with self.assertRaises(release.ReleaseError):
            release.ready(value, SOURCE, SOURCE, IMAGE, 2)

    def test_migrations_require_both_jobs_and_this_attempt(self):
        digest = "sha256:" + "c" * 64
        receipt = {
            "source_sha": SOURCE,
            "image": "localhost:30500/warehouse:release@" + digest,
            "run_id": "123",
            "run_attempt": "2",
            "succeeded": True,
            "jobs": [
                {"target": n, "uid": n + "-uid", "succeeded": True}
                for n in ["warehouse", "ecosystem"]
            ],
        }
        with patch.dict(
            os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
        ):
            release.migrated(receipt, SOURCE, "release", digest)
            for change in [
                {"run_attempt": "1"},
                {"succeeded": False},
                {"jobs": receipt["jobs"][:1]},
            ]:
                with self.assertRaises(release.ReleaseError):
                    release.migrated(dict(receipt, **change), SOURCE, "release", digest)

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
        args = argparse.Namespace(
            operation="status",
            pin_sha=PIN,
            expected_image=IMAGE,
            wait_timeout=30,
            min_replicas=2,
        )
        with (
            patch.dict(os.environ, {"GITHUB_SHA": SOURCE}),
            patch.object(release.ssl, "create_default_context"),
            patch.object(release, "request", return_value=(403, {})) as request,
        ):
            with self.assertRaises(release.ReleaseError):
                release.run(args)
            self.assertEqual(request.call_count, 1)

    def test_missing_trust_never_sends_token(self):
        args = argparse.Namespace(
            operation="status",
            pin_sha=PIN,
            expected_image=IMAGE,
            wait_timeout=30,
            min_replicas=2,
        )
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

    def test_oidc_route_skips_kubernetes_but_retains_served_source_proof(self):
        root = Path(__file__).resolve().parents[2]
        action = yaml.safe_load((root / "lab-gitops-deploy/action.yml").read_text())
        self.assertEqual(action["inputs"]["authorization"]["default"], "kubernetes")
        steps = action["runs"]["steps"]
        for step in steps:
            script = step.get("run", "")
            if "kubectl -n" in script or "argo-await-sync.sh" in script:
                self.assertIn(
                    "inputs.authorization == 'kubernetes'", step.get("if", "")
                )
        health = next(
            s
            for s in steps
            if s.get("name") == "Verify the running build is the one just built"
        )
        self.assertIn("inputs.authorization == 'oidc'", health["if"])
        self.assertEqual(health["env"]["WANT_SHA"], "${{ github.sha }}")
        self.assertIn('j.db !== "ok"', health["run"])


if __name__ == "__main__":
    unittest.main()
