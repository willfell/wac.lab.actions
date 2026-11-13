"""wacd-release proves a chart release through the gateway and then the served build."""

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

SCRIPT = Path(__file__).parents[1] / "wacd-release.py"
SPEC = importlib.util.spec_from_file_location("wacd_release", SCRIPT)
wacd = importlib.util.module_from_spec(SPEC)
sys.modules["wacd_release"] = wacd
SPEC.loader.exec_module(wacd)

VERSION = "0.1.0"
SOURCE = "a" * 40
CHART_DIGEST = "sha256:" + "d" * 64
IMAGE_DIGEST = "sha256:" + "e" * 64
IMAGES = {
    "travel-app": {
        "repository": "localhost:30500/travel-app",
        "tag": VERSION,
        "digest": IMAGE_DIGEST,
    }
}
IMAGE_REF = f"localhost:30500/travel-app:{VERSION}@{IMAGE_DIGEST}"
TOKEN = "fake.token.value"
ReleaseError = wacd.ReleaseError


def evidence():
    return {
        "application": "travel",
        "chart": "deploy/travel",
        "version": VERSION,
        "chart_digest": CHART_DIGEST,
        "automated": True,
        "source_sha": SOURCE,
        "sync": {"status": "Synced", "revision": CHART_DIGEST},
        "health": {"status": "Healthy"},
        "operation_phase": "Succeeded",
        "deployments": [
            {
                "name": "travel-app",
                "generation": 3,
                "replicas": 2,
                "images": [IMAGE_REF],
                "status": {
                    "observedGeneration": 3,
                    "replicas": 2,
                    "updatedReplicas": 2,
                    "readyReplicas": 2,
                    "availableReplicas": 2,
                },
            }
        ],
    }


def released(value=None):
    value = evidence() if value is None else value
    value["released"] = {
        "version": VERSION,
        "chart_digest": CHART_DIGEST,
        "chart_source_sha": SOURCE,
        "previous_version": "0.0.9",
        "sync_requested": True,
    }
    return value


def not_ready():
    value = evidence()
    value["sync"]["status"] = "OutOfSync"
    return value


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.001)


def fake_clock():
    clock = Clock()
    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(wacd.time, "monotonic", clock.monotonic))
    stack.enter_context(patch.object(wacd.time, "sleep", clock.sleep))
    return stack, clock


class Script:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, body, trust, timeout):
        self.bodies.append(body)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


GARBAGE = object()


class Health:
    def __init__(self, *responses):
        self.requests = 0
        self.responses = list(responses)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                if len(outer.responses) > 1:
                    status, body, location = outer.responses.pop(0)
                else:
                    status, body, location = outer.responses[0]
                if body is GARBAGE:
                    self.wfile.write(b"this is not http\r\n\r\n")
                    return
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/health"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Gateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers["Content-Length"])
                outer.seen.append(
                    (self.headers["Authorization"], json.loads(self.rfile.read(size)))
                )
                if len(outer.responses) > 1:
                    status, body = outer.responses.pop(0)
                else:
                    status, body = outer.responses[0]
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/release"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class BodyTests(unittest.TestCase):
    def test_bodies_match_the_contract(self):
        self.assertEqual(
            wacd.RELEASE_BODY("0.1.0"), {"operation": "release", "version": "0.1.0"}
        )
        self.assertEqual(wacd.STATUS_BODY(), {"operation": "status"})


class ReadyTests(unittest.TestCase):
    def ready(self, value, images=IMAGES):
        return wacd.ready(value, VERSION, CHART_DIGEST, images)

    def test_ready_requires_the_contract_proof(self):
        self.assertTrue(self.ready(evidence()))

        def mutate(change):
            value = evidence()
            change(value)
            return value

        mutations = {
            "version": lambda v: v.update(version="0.0.9"),
            "sync status": lambda v: v["sync"].update(status="OutOfSync"),
            "sync revision": lambda v: v["sync"].update(revision="sha256:" + "0" * 64),
            "health": lambda v: v["health"].update(status="Progressing"),
            "automated false": lambda v: v.update(automated=False),
            "automated absent": lambda v: v.pop("automated"),
            "automated truthy": lambda v: v.update(automated="true"),
            "generation behind": lambda v: v["deployments"][0]["status"].update(
                observedGeneration=2
            ),
            "ready replicas": lambda v: v["deployments"][0]["status"].update(
                readyReplicas=1
            ),
            "updated replicas": lambda v: v["deployments"][0]["status"].update(
                updatedReplicas=1
            ),
            "available replicas": lambda v: v["deployments"][0]["status"].pop(
                "availableReplicas"
            ),
            "unavailable replicas": lambda v: v["deployments"][0]["status"].update(
                unavailableReplicas=1
            ),
            "image tag": lambda v: v["deployments"][0].update(
                images=[f"localhost:30500/travel-app:0.0.9@{IMAGE_DIGEST}"]
            ),
            "image digest": lambda v: v["deployments"][0].update(
                images=[f"localhost:30500/travel-app:{VERSION}@sha256:" + "0" * 64]
            ),
            "image without digest": lambda v: v["deployments"][0].update(
                images=[f"localhost:30500/travel-app:{VERSION}"]
            ),
            "no release image": lambda v: v["deployments"][0].update(images=[]),
        }
        for label, change in mutations.items():
            with self.subTest(label):
                self.assertFalse(self.ready(mutate(change)))

    def test_ready_checks_every_deployment(self):
        value = evidence()
        second = copy.deepcopy(value["deployments"][0])
        second["name"] = "travel-worker"
        second["status"]["readyReplicas"] = 0
        value["deployments"].append(second)
        self.assertFalse(self.ready(value))

    def test_ready_allows_unavailable_zero_and_a_second_deployment_without_release_images(
        self,
    ):
        value = evidence()
        value["deployments"][0]["status"]["unavailableReplicas"] = 0
        sidecar = copy.deepcopy(value["deployments"][0])
        sidecar["images"] = ["localhost:30500/other:1"]
        value["deployments"].append(sidecar)
        self.assertTrue(self.ready(value))

    def test_ready_is_false_while_deployments_are_missing(self):
        for deployments in ([], None, "x"):
            with self.subTest(deployments=deployments):
                value = evidence()
                if deployments is None:
                    del value["deployments"]
                else:
                    value["deployments"] = deployments
                self.assertFalse(self.ready(value))

    def test_ready_expects_the_release_version_as_the_image_tag(self):
        stale = {
            "travel-app": {
                "repository": "localhost:30500/travel-app",
                "tag": "0.0.9",
                "digest": IMAGE_DIGEST,
            }
        }
        value = evidence()
        value["deployments"][0]["images"] = [
            f"localhost:30500/travel-app:0.0.9@{IMAGE_DIGEST}"
        ]
        self.assertFalse(self.ready(value, stale))
        self.assertTrue(self.ready(evidence(), stale))

    def test_ready_ignores_images_of_other_repositories(self):
        value = evidence()
        value["deployments"][0]["images"].append("localhost:30500/other:1")
        self.assertTrue(self.ready(value))

    def test_ready_rejects_a_stale_image_of_any_release_repository(self):
        images = dict(IMAGES)
        images["travel-worker"] = {
            "repository": "localhost:30500/travel-worker",
            "tag": VERSION,
            "digest": "sha256:" + "f" * 64,
        }
        value = evidence()
        value["deployments"][0]["images"].append(
            f"localhost:30500/travel-worker:0.0.9@sha256:{'f' * 64}"
        )
        self.assertFalse(self.ready(value, images))
        value["deployments"][0]["images"][1] = (
            f"localhost:30500/travel-worker:{VERSION}@sha256:{'f' * 64}"
        )
        self.assertTrue(self.ready(value, images))

    def test_ready_allows_a_release_image_that_no_deployment_runs(self):
        images = dict(IMAGES)
        images["travel-worker"] = {
            "repository": "localhost:30500/travel-worker",
            "tag": VERSION,
            "digest": "sha256:" + "f" * 64,
        }
        self.assertTrue(self.ready(evidence(), images))


class DigestTests(unittest.TestCase):
    def test_released_digest_must_match_the_version(self):
        self.assertEqual(wacd.released_digest(released(), VERSION), CHART_DIGEST)
        wrong_version = released()
        wrong_version["released"]["version"] = "0.1.1"
        bad_digest = released()
        bad_digest["released"]["chart_digest"] = "abc"
        for label, value in {
            "other version": wrong_version,
            "bad digest": bad_digest,
            "no released": evidence(),
            "released not an object": dict(evidence(), released="x"),
        }.items():
            with self.subTest(label):
                with self.assertRaises(ReleaseError):
                    wacd.released_digest(value, VERSION)


class ReleaseTests(unittest.TestCase):
    def run_release(self, *responses, timeout=60):
        script = Script(*responses)
        stack, clock = fake_clock()
        with stack:
            try:
                return script, clock, wacd.release(VERSION, IMAGES, timeout, request=script)
            except ReleaseError as error:
                return script, clock, error

    def test_release_retries_unavailable_then_polls(self):
        script, clock, result = self.run_release(
            (503, {}),
            (200, released(not_ready())),
            (200, not_ready()),
            (200, evidence()),
        )
        self.assertEqual(result, evidence())
        release, status = wacd.RELEASE_BODY(VERSION), wacd.STATUS_BODY()
        self.assertEqual(script.bodies, [release, release, status, status])
        self.assertTrue(all(s <= 5 for s in clock.sleeps))

    def test_release_returns_at_once_when_the_release_response_is_ready(self):
        script, _, result = self.run_release((200, released()))
        self.assertEqual(result, released())
        self.assertEqual(script.bodies, [wacd.RELEASE_BODY(VERSION)])

    def test_release_retries_unavailable_status_polls(self):
        script, _, result = self.run_release(
            (200, released(not_ready())), (503, {}), (200, evidence())
        )
        self.assertEqual(result, evidence())
        self.assertEqual(len(script.bodies), 3)

    def test_release_fails_on_a_denial(self):
        for code in (400, 403, 404, 409, 500, 502):
            with self.subTest(code=code):
                script, _, error = self.run_release((code, {}))
                self.assertIsInstance(error, ReleaseError)
                self.assertIn(f"HTTP {code}", str(error))
                self.assertEqual(len(script.bodies), 1)

    def test_release_fails_on_a_denied_status_poll(self):
        script, _, error = self.run_release((200, released(not_ready())), (409, {}))
        self.assertIsInstance(error, ReleaseError)
        self.assertIn("HTTP 409", str(error))
        self.assertEqual(len(script.bodies), 2)

    def test_release_deadline(self):
        _, _, error = self.run_release((503, {}), timeout=1)
        self.assertIsInstance(error, ReleaseError)
        self.assertIn("deadline", str(error))

    def test_release_deadline_while_never_ready(self):
        _, clock, error = self.run_release(
            (200, released(not_ready())), (200, not_ready()), timeout=12
        )
        self.assertIsInstance(error, ReleaseError)
        self.assertIn("deadline", str(error))
        self.assertLessEqual(clock.now - 1000.0, 12.5)

    def test_release_rejects_a_release_for_another_version(self):
        value = released(not_ready())
        value["released"]["version"] = "0.1.1"
        script, _, error = self.run_release((200, value))
        self.assertIsInstance(error, ReleaseError)
        self.assertEqual(len(script.bodies), 1)

    def test_release_sends_a_fresh_token_through_the_default_transport(self):
        gateway = Gateway((200, released(not_ready())), (200, evidence()))
        tokens = iter(["token-one", "token-two", "token-three"])
        stack, _ = fake_clock()
        try:
            with stack, patch.object(wacd.ci, "URL", gateway.url), patch.object(
                wacd.ci, "oidc_token", lambda: next(tokens)
            ), patch.object(wacd, "trust_context", lambda: None):
                result = wacd.release(VERSION, IMAGES, 60)
        finally:
            gateway.close()
        self.assertEqual(result, evidence())
        self.assertEqual(
            gateway.seen,
            [
                ("Bearer token-one", wacd.RELEASE_BODY(VERSION)),
                ("Bearer token-two", wacd.STATUS_BODY()),
            ],
        )


class ServedTests(unittest.TestCase):
    def serve(self, *responses, sha=SOURCE, expect_db=True, timeout=1):
        health = Health(*responses)
        stack, clock = fake_clock()
        try:
            with stack:
                try:
                    return health, clock, wacd.served(health.url, sha, expect_db, timeout)
                except ReleaseError as error:
                    return health, clock, error
        finally:
            health.close()

    def test_health_checks_sha_and_db(self):
        _, _, ok = self.serve((200, {"sha": SOURCE, "db": "ok"}, None))
        self.assertEqual(ok, {"sha": SOURCE, "db": "ok"})

        other = "b" * 40
        _, _, error = self.serve((200, {"sha": other, "db": "ok"}, None))
        self.assertIsInstance(error, ReleaseError)
        self.assertIn(other, str(error))

        for body in ({"sha": SOURCE, "db": "down"}, {"sha": SOURCE}):
            with self.subTest(body=body):
                _, _, error = self.serve((200, body, None))
                self.assertIsInstance(error, ReleaseError)

        _, _, ok = self.serve((200, {"sha": SOURCE}, None), expect_db=False)
        self.assertEqual(ok, {"sha": SOURCE})

    def test_health_retries_until_the_build_is_served(self):
        health, clock, ok = self.serve(
            (503, b"", None),
            (200, {"sha": "b" * 40, "db": "ok"}, None),
            (200, {"sha": SOURCE, "db": "ok"}, None),
            timeout=60,
        )
        self.assertEqual(ok, {"sha": SOURCE, "db": "ok"})
        self.assertEqual(health.requests, 3)
        self.assertEqual(clock.sleeps, [5, 5])

    def test_health_retries_a_malformed_response(self):
        health, _, ok = self.serve(
            (200, GARBAGE, None),
            (200, {"sha": SOURCE}, None),
            expect_db=False,
            timeout=60,
        )
        self.assertEqual(ok, {"sha": SOURCE})
        self.assertEqual(health.requests, 2)

    def test_health_truncates_a_long_served_sha(self):
        _, _, error = self.serve((200, {"sha": "7" * 500, "db": "ok"}, None), timeout=1)
        self.assertIsInstance(error, ReleaseError)
        self.assertIn("7" * 80, str(error))
        self.assertNotIn("7" * 81, str(error))

    def test_health_fails_closed(self):
        cases = {
            "not an object": (200, b'["sha"]', None),
            "not json": (200, b"<html>", None),
            "not 200": (500, {"sha": SOURCE, "db": "ok"}, None),
            "redirect": (302, b"", "http://127.0.0.1:1/elsewhere"),
            "sha not a string": (200, {"sha": [SOURCE], "db": "ok"}, None),
        }
        for label, response in cases.items():
            with self.subTest(label):
                health, _, error = self.serve(response, timeout=6)
                self.assertIsInstance(error, ReleaseError)
                self.assertGreaterEqual(health.requests, 2)

    def test_health_names_the_last_served_sha_on_timeout(self):
        other = "9" * 40
        _, _, error = self.serve((200, {"sha": other, "db": "ok"}, None), timeout=6)
        self.assertIsInstance(error, ReleaseError)
        self.assertIn(other, str(error))

    def test_health_refuses_non_http_urls(self):
        with self.assertRaises(ReleaseError):
            wacd.served("file:///etc/passwd", SOURCE, False, 1)

    def test_health_ignores_proxy_environment(self):
        health = Health((200, {"sha": SOURCE}, None))
        env = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"}
        try:
            with patch.dict(os.environ, env):
                self.assertEqual(
                    wacd.served(health.url, SOURCE, False, 1), {"sha": SOURCE}
                )
        finally:
            health.close()


class CliTests(unittest.TestCase):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wacd.main(["wacd-release.py", *argv])
        return code, out.getvalue(), err.getvalue()

    def release_argv(self, receipt, images=IMAGES, timeout="30", version=VERSION):
        return (
            "release",
            "--version", version,
            "--images", json.dumps(images),
            "--wait-timeout", timeout,
            "--receipt", receipt,
        )

    def test_cli_release_writes_the_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            script = Script((200, released()))
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd.ci, "request", script
            ):
                code, out, err = self.main(*self.release_argv(receipt))
            self.assertEqual((code, err), (0, ""), err)
            self.assertIn(receipt, out)
            self.assertEqual(json.loads(Path(receipt).read_text()), released())

    def test_cli_receipt_keeps_the_released_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            script = Script((200, released(not_ready())), (200, evidence()))
            stack, _ = fake_clock()
            with stack, patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd.ci, "request", script
            ):
                code, _, err = self.main(*self.release_argv(receipt))
            self.assertEqual((code, err), (0, ""), err)
            written = json.loads(Path(receipt).read_text())
            self.assertEqual(written["released"]["chart_source_sha"], SOURCE)
            self.assertEqual(written["released"], released()["released"])
            self.assertEqual(written, {**evidence(), "released": released()["released"]})

    def test_cli_never_prints_the_token(self):
        def explode(body, trust, timeout):
            raise RuntimeError(f"Authorization: Bearer {TOKEN}")

        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd.ci, "request", explode
            ):
                code, out, err = self.main(*self.release_argv(receipt))
            self.assertEqual(code, 1)
            self.assertNotIn(TOKEN, out + err)
            self.assertEqual(len(err.strip().splitlines()), 1)
            self.assertNotIn("Traceback", err)
            self.assertFalse(Path(receipt).exists())

    def test_cli_failure_is_one_stable_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            lines = set()
            for code_ in (403, 404, 409):
                with patch.object(wacd, "trust_context", lambda: None), patch.object(
                    wacd.ci, "request", Script((code_, {}))
                ):
                    code, out, err = self.main(*self.release_argv(receipt))
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(len(err.strip().splitlines()), 1)
                self.assertTrue(err.startswith("wacd release: "))
                lines.add(err)
            self.assertEqual(len(lines), 3)

    def test_cli_rejects_bad_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            bad = {
                "version": self.release_argv(receipt, version="v0.1.0"),
                "leading zero": self.release_argv(receipt, version="01.0.0"),
                "timeout zero": self.release_argv(receipt, timeout="0"),
                "timeout high": self.release_argv(receipt, timeout="3601"),
                "image tag is not the version": self.release_argv(
                    receipt,
                    images={
                        "a": {
                            "repository": "localhost:30500/a",
                            "tag": "0.0.9",
                            "digest": IMAGE_DIGEST,
                        }
                    },
                ),
                "no images": self.release_argv(receipt, images={}),
                "bad digest": self.release_argv(
                    receipt,
                    images={"a": {"repository": "localhost:30500/a", "tag": VERSION, "digest": "x"}},
                ),
                "foreign registry": self.release_argv(
                    receipt,
                    images={
                        "a": {
                            "repository": "registry.network.svc.cluster.local:5000/a",
                            "tag": VERSION,
                            "digest": IMAGE_DIGEST,
                        }
                    },
                ),
            }
            for label, argv in bad.items():
                with self.subTest(label):
                    with patch.object(wacd.ci, "request", Script((200, released()))) as _:
                        code, out, err = self.main(*argv)
                    self.assertEqual(code, 1, err)
                    self.assertNotIn("Traceback", err)

    def test_cli_images_must_be_json(self):
        code, _, err = self.main(
            "release", "--version", VERSION, "--images", "{", "--wait-timeout", "5",
            "--receipt", "/nonexistent/receipt.json",
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_cli_health_rejects_a_malformed_sha(self):
        for sha in ("abc", "A" * 40, "g" * 40, "a" * 41):
            with self.subTest(sha=sha):
                code, out, err = self.main(
                    "health", "--url", "http://127.0.0.1:9/h", "--sha", sha,
                    "--expect-db", "false", "--wait-timeout", "1",
                )
                self.assertEqual((code, out), (1, ""))
                self.assertIn("--sha", err)

    def test_cli_health(self):
        health = Health((200, {"sha": SOURCE, "db": "ok"}, None))
        try:
            code, out, err = self.main(
                "health", "--url", health.url, "--sha", SOURCE,
                "--expect-db", "true", "--wait-timeout", "5",
            )
            self.assertEqual((code, err), (0, ""), err)
            self.assertIn(SOURCE, out)
            code, out, err = self.main(
                "health", "--url", health.url, "--sha", "b" * 40,
                "--expect-db", "false", "--wait-timeout", "1",
            )
        finally:
            health.close()
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_subprocess_without_oidc_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith("ACTIONS_")}
            env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = TOKEN
            env["ACTIONS_ID_TOKEN_REQUEST_URL"] = "http://example.invalid/token"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *self.release_argv(str(Path(tmp) / "r.json"))],
                capture_output=True, text=True, env=env, timeout=60,
            )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
