"""wacd-release proves a chart release through the gateway and then the served build."""

import contextlib
import copy
import errno
import importlib.util
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
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
WAC_CD = {"mode": "release", "version": VERSION, "tag": "v" + VERSION, "expected_sha": SOURCE}
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


def running(version):
    value = evidence()
    value["version"] = version
    return value


class DowngradeTests(unittest.TestCase):
    def run_release(self, mode, *responses, timeout=60):
        script = Script(*responses)
        stack, clock = fake_clock()
        with stack:
            try:
                result = wacd.proved(VERSION, IMAGES, timeout, request=script, mode=mode)[0]
            except ReleaseError as error:
                result = error
        return script, result

    def test_a_newer_running_version_refuses_a_release_without_sending_it(self):
        for mode in ("release", "resume"):
            with self.subTest(mode=mode):
                script, error = self.run_release(mode, (200, running("0.2.0")), (200, released()))
                self.assertIsInstance(error, ReleaseError)
                self.assertIn("0.2.0", str(error))
                self.assertIn(VERSION, str(error))
                self.assertIn("rollback", str(error))
                self.assertEqual(len(str(error).splitlines()), 1)
                self.assertEqual(script.bodies, [wacd.STATUS_BODY()])

    def test_semver_order_is_numeric(self):
        script, error = self.run_release("release", (200, running("0.10.0")), (200, released()))
        self.assertIsInstance(error, ReleaseError)
        self.assertNotIn(wacd.RELEASE_BODY(VERSION), script.bodies)

    def test_a_rollback_may_go_back(self):
        script, result = self.run_release("rollback", (200, running("0.2.0")), (200, released()))
        self.assertEqual(result, released())
        self.assertEqual(script.bodies, [wacd.STATUS_BODY(), wacd.RELEASE_BODY(VERSION)])

    def test_equal_lower_or_unversioned_running_releases(self):
        for version in (VERSION, "0.0.9", "bootstrap", "v0.2.0", "0.2.0-rc.1", "02.0.0", "", None, 7):
            with self.subTest(running=version):
                status = evidence()
                if version is None:
                    del status["version"]
                else:
                    status["version"] = version
                script, result = self.run_release("release", (200, status), (200, released()))
                self.assertEqual(result, released())
                self.assertEqual(script.bodies, [wacd.STATUS_BODY(), wacd.RELEASE_BODY(VERSION)])

    def test_unavailable_pre_check_is_retried(self):
        script, result = self.run_release(
            "release", (503, {}), (200, running(VERSION)), (200, released())
        )
        self.assertEqual(result, released())
        self.assertEqual(
            script.bodies,
            [wacd.STATUS_BODY(), wacd.STATUS_BODY(), wacd.RELEASE_BODY(VERSION)],
        )

    def test_unavailable_pre_check_respects_the_deadline(self):
        script, error = self.run_release("release", (503, {}), timeout=1)
        self.assertIsInstance(error, ReleaseError)
        self.assertIn("deadline", str(error))
        self.assertNotIn(wacd.RELEASE_BODY(VERSION), script.bodies)

    def test_a_denied_pre_check_fails(self):
        for code in (400, 403, 409, 500):
            with self.subTest(code=code):
                script, error = self.run_release("release", (code, {}), (200, released()))
                self.assertIsInstance(error, ReleaseError)
                self.assertIn(f"HTTP {code}", str(error))
                self.assertEqual(script.bodies, [wacd.STATUS_BODY()])

    def test_cli_release_runs_the_pre_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            script = Script((200, running("1.0.0")), (200, released()))
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd, "transport", script
            ):
                code, out, err = CliTests.main(self, *CliTests.release_argv(self, receipt))
            self.assertEqual((code, out), (1, ""))
            self.assertIn("1.0.0", err)
            self.assertEqual(len(err.strip().splitlines()), 1)
            self.assertEqual(script.bodies, [wacd.STATUS_BODY()])
            self.assertFalse(Path(receipt).exists())


class SettingsModeTests(unittest.TestCase):
    """A settings release goes through the same gateway proof as a release, expecting its own source."""

    def test_a_newer_running_version_refuses_it_as_it_refuses_a_release(self):
        script, error = DowngradeTests.run_release(self, "settings", (200, running("0.2.0")), (200, released()))
        self.assertIsInstance(error, ReleaseError)
        self.assertIn("dispatch a rollback", str(error))
        self.assertEqual(script.bodies, [wacd.STATUS_BODY()])
        script, result = DowngradeTests.run_release(self, "settings", (200, running("0.0.9")), (200, released()))
        self.assertEqual(result, released())

    def test_the_receipt_records_settings_mode_and_the_running_code_as_expected(self):
        plan = {"mode": "settings", "version": VERSION, "tag": "v" + VERSION, "expected_sha": "c" * 40}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd, "transport", Script((200, released()))
            ):
                code, _, err = CliTests.main(self, *CliTests.release_argv(
                    self, receipt, mode="settings", expected_sha="c" * 40))
            self.assertEqual((code, err), (0, ""), err)
            self.assertEqual(json.loads(Path(receipt).read_text())["wac_cd"], plan)


RELEASE = {"digest": CHART_DIGEST, "revision": SOURCE, "images": IMAGES, "copy": "6", "fleet_commit": "f" * 40}


def unsteady(change):
    value = evidence()
    change(value)
    return value


UNSTEADY = {
    "out of sync": lambda v: v["sync"].update(status="OutOfSync"),
    "synced at another chart": lambda v: v["sync"].update(revision="sha256:" + "0" * 64),
    "degraded": lambda v: v["health"].update(status="Degraded"),
    "a replica not updated": lambda v: v["deployments"][0]["status"].update(updatedReplicas=1),
    "a replica not available": lambda v: v["deployments"][0]["status"].update(availableReplicas=1),
    "generation not observed": lambda v: v["deployments"][0]["status"].update(observedGeneration=2),
    "a deployment by name only": lambda v: v["deployments"].append({"name": "travel-worker"}),
    "no deployments": lambda v: v.update(deployments=[]),
    "another digest of the image": lambda v: v["deployments"][0].update(
        images=[f"localhost:30500/travel-app:{VERSION}@sha256:" + "1" * 64]),
    "an image of another repository": lambda v: v["deployments"][0]["images"].append(
        "localhost:30500/sidecar:1@sha256:" + "2" * 64),
    "an image with no digest": lambda v: v["deployments"][0].update(images=["localhost:30500/travel-app:0.1.0"]),
    "no image at all": lambda v: v["deployments"][0].update(images=[]),
    "only a foreign image": lambda v: v["deployments"][0].update(images=["docker.io/envoyproxy/envoy:v1.30@sha256:" + "3" * 64]),
}


class RunningTests(unittest.TestCase):
    """plan takes the running version only from a status that proves it runs."""

    def run_cli(self, *responses, timeout="180", release=RELEASE):
        script = Script(*responses)
        reads = []

        def read(app, version, timeout=30):
            reads.append((app, version))
            return copy.deepcopy(release)

        stack, clock = fake_clock()
        with tempfile.TemporaryDirectory() as tmp, stack:
            summary = Path(tmp) / "summary"
            with patch.object(wacd, "trust_context", lambda: None), patch.object(wacd, "transport", script), \
                    patch.object(wacd, "read_release", read), \
                    patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
                code, out, err = CliTests.main(self, "running", "--app", "travel", "--wait-timeout", timeout)
            written = summary.read_text() if summary.exists() else ""
        return script, reads, clock, (code, out, err, written)

    def test_a_steady_status_names_the_version_its_code_and_its_images(self):
        script, reads, _, (code, out, err, _) = self.run_cli((200, evidence()))
        self.assertEqual((code, err), (0, ""), err)
        self.assertEqual(
            out.splitlines(),
            [f"running={VERSION}", f"commit={SOURCE}",
             "images=" + json.dumps(IMAGES, separators=(",", ":"), sort_keys=True), "copy_revision=6",
             "fleet_commit=" + "f" * 40],
        )
        self.assertEqual(script.bodies, [wacd.STATUS_BODY()], "one status read; nothing is released")
        self.assertEqual(reads, [("travel", VERSION)])

    def test_images_are_matched_by_repository_and_digest_not_by_the_string(self):
        for images in ([f"localhost:30500/travel-app:0.0.9@{IMAGE_DIGEST}"], [f"localhost:30500/travel-app@{IMAGE_DIGEST}"]):
            with self.subTest(images=images):
                status = evidence()
                status["deployments"][0]["images"] = images
                _, _, _, (code, out, err, _) = self.run_cli((200, status))
                self.assertEqual(code, 0, err)

    def test_a_foreign_sidecar_beside_the_release_image_is_steady_not_mid_rollout(self):
        for sidecar in ("docker.io/envoyproxy/envoy:v1.30@sha256:" + "3" * 64, "ghcr.io/acme/agent:2", "busybox"):
            with self.subTest(sidecar=sidecar):
                status = evidence()
                status["deployments"][0]["images"].append(sidecar)
                script, _, _, (code, out, err, _) = self.run_cli((200, status))
                self.assertEqual(code, 0, err)
                self.assertEqual(len(script.bodies), 1, "steady on the first read")

    def test_an_unsteady_status_is_read_again_until_it_settles(self):
        for label, change in UNSTEADY.items():
            with self.subTest(case=label):
                script, _, _, (code, out, err, _) = self.run_cli((200, unsteady(change)), (200, evidence()))
                self.assertEqual(code, 0, err)
                self.assertEqual(len(script.bodies), 2)

    def test_a_status_that_never_settles_is_a_rollout_refusal_exit_3(self):
        for label, change in UNSTEADY.items():
            with self.subTest(case=label):
                script, _, clock, (code, out, err, _) = self.run_cli((200, unsteady(change)), timeout="30")
                self.assertEqual((code, out), (3, ""), err)
                self.assertIn(f"travel was rolling out {VERSION}", err)
                self.assertEqual(len(err.strip().splitlines()), 1)
                self.assertGreater(len(script.bodies), 1)
                self.assertLessEqual(clock.now - 1000.0, 31)

    def test_an_unknown_or_malformed_running_version_is_a_fault_not_a_rollout(self):
        for value in ("", "bootstrap", "v1.4.2", None, 7):
            with self.subTest(running=value):
                status = evidence()
                status["version"] = value
                script, reads, _, (code, out, err, _) = self.run_cli((200, status))
                self.assertEqual((code, out), (1, ""))
                self.assertIn("is not a released X.Y.Z", err)
                self.assertEqual((len(script.bodies), reads), (1, []))

    def test_the_status_must_name_the_chart_the_registry_holds(self):
        for label, release, needle in (
            ("another manifest", {**RELEASE, "digest": "sha256:" + "9" * 64}, "not the chart the registry holds"),
            ("absent", None, "is not in the registry"),
        ):
            with self.subTest(case=label):
                script, _, _, (code, out, err, _) = self.run_cli((200, evidence()), release=release)
                self.assertEqual((code, out), (1, ""))
                self.assertIn(needle, err)
                self.assertEqual(len(script.bodies), 1)

    def test_unavailable_is_retried_and_staying_unavailable_is_not_a_rollout(self):
        script, _, _, (code, out, err, _) = self.run_cli((503, {}), (503, {}), (200, evidence()))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(script.bodies), 3)
        _, _, _, (code, out, err, _) = self.run_cli((503, {}), timeout="20")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("could not read the cluster", err)

    def test_refusals(self):
        _, _, _, (code, out, err, summary) = self.run_cli((403, {}))
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "wacd release: The gateway refused the status read (HTTP 403 with no reason), so the "
                              "running version is unknown and no settings were released.\n")
        _, _, _, (code, out, err, summary) = self.run_cli((403, {"reason": "fleet_copy_missing"}))
        self.assertEqual((code, out), (1, ""))
        self.assertIn(wacd.REFUSALS["fleet_copy_missing"], err)
        self.assertIn(wacd.REFUSALS["fleet_copy_missing"], summary)
        _, _, _, (code, _, err, _) = self.run_cli((500, {}))
        self.assertEqual(code, 1)
        self.assertIn("HTTP 500", err)

    def test_the_chart_is_read_at_the_push_address(self):
        calls = []
        with patch.object(wacd.chart, "chart_release", lambda *args: calls.append(args) or RELEASE):
            self.assertEqual(wacd.read_release("travel", VERSION), RELEASE)
        self.assertEqual(calls, [("registry.network.svc.cluster.local:5000", "travel", VERSION, 30.0)])

    def test_the_token_is_never_printed(self):
        def explode(body, trust, timeout):
            raise RuntimeError(f"Authorization: Bearer {TOKEN}")

        with patch.object(wacd, "trust_context", lambda: None), patch.object(wacd, "transport", explode):
            code, out, err = CliTests.main(self, "running", "--app", "travel", "--wait-timeout", "30")
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, out + err)


DRIVER = """
import importlib.util, json, os, sys, time
spec = importlib.util.spec_from_file_location("wacd_release_driver", sys.argv[1])
wacd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wacd)
fixture = json.loads(os.environ["DRIVER_FIXTURE"])
wacd.DEADLINE_MARGIN = fixture["margin"]
wacd.POLL_SECONDS = 0.2
wacd.trust_context = lambda: None
wacd.transport = lambda body, trust, timeout: (200, fixture["status"])
wacd.read_release = lambda app, version, timeout=30: fixture["release"]
wacd.read_copy = lambda app, version: "7"
argv = [a.replace("DEADLINE", str(time.time() + fixture["deadline"])) for a in sys.argv[2:]]
sys.exit(wacd.main(["wacd-release.py", *argv]))
"""


class DeadlineTests(unittest.TestCase):
    """One wall-clock deadline: the status wait decides before it, and the backstop never fires after a decision."""

    def driver(self, tmp, *argv, margin=1, deadline=3.5, status=None, env=None):
        path = Path(tmp) / "driver.py"
        if not path.exists():
            path.write_text(DRIVER)
        fixture = {"margin": margin, "deadline": deadline, "status": status or unsteady(UNSTEADY["out of sync"]),
                   "release": RELEASE}
        return subprocess.Popen(
            [sys.executable, "-I", str(path), str(SCRIPT), *argv],
            env={**os.environ, **(env or {}), "DRIVER_FIXTURE": json.dumps(fixture)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def test_an_app_that_never_settles_always_reaches_the_rollout_refusal(self):
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            runs = [self.driver(tmp, "running", "--app", "travel", "--wait-timeout", "120", "--deadline", "DEADLINE")
                    for _ in range(24)]
            results = [(run.wait(timeout=30), *run.communicate()) for run in runs]
        for code, out, err in results:
            self.assertEqual((code, out), (wacd.ROLLING_OUT_EXIT, ""), err)
            self.assertIn("travel was rolling out 0.1.0", err)
            self.assertNotIn("Traceback", err)
        self.assertLess(time.monotonic() - started, 12, "every run decided inside its 3.5-second deadline")

    def test_a_slow_drip_answer_cannot_outlast_the_deadline(self):
        class Drip(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "100000")
                self.end_headers()
                try:
                    for _ in range(200):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.2)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = {"GITHUB_API_URL": f"http://127.0.0.1:{server.server_address[1]}",
               "GITHUB_REPOSITORY": "willfell/travel", "GITHUB_TOKEN": "t0k"}
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            run = self.driver(tmp, "earlier-release", "--app", "travel", "--prefix", "v", "--revision", "7",
                              "--running", "1.3.9", "--versions", "1.4.3", "--claimed", "", "--deadline", "DEADLINE",
                              deadline=2, env=env)
            code = run.wait(timeout=30)
            out, err = run.communicate()
        self.assertEqual((code, out), (1, ""), err)
        self.assertIn("did not finish within the settings plan's deadline", err)
        self.assertNotIn("t0k", err)
        self.assertLess(time.monotonic() - started, 6, "a body dripping one byte at a time is cut off at the deadline")

    def test_a_stuck_status_read_ends_at_the_deadline(self):
        def stuck(body, trust, timeout):
            time.sleep(30)

        started = time.monotonic()
        with patch.object(wacd, "trust_context", lambda: None), patch.object(wacd, "transport", stuck), \
                patch.object(wacd, "DEADLINE_MARGIN", 0):
            code, out, err = CliTests.main(
                self, "running", "--app", "travel", "--wait-timeout", "120", "--deadline", str(time.time() + 2.5))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("did not finish within the settings plan's deadline", err)
        self.assertLess(time.monotonic() - started, 5)

    def test_a_deadline_already_past_or_too_close_reads_nothing(self):
        for offset in (-1, 10):
            with self.subTest(offset=offset):
                calls = []
                with patch.object(wacd, "trust_context", lambda: None), \
                        patch.object(wacd, "transport", lambda *a: calls.append(a) or (200, evidence())):
                    code, _, err = CliTests.main(self, "running", "--app", "travel", "--wait-timeout", "120",
                                                 "--deadline", str(time.time() + offset))
                self.assertEqual(code, 1)
                self.assertIn("deadline", err)
                self.assertEqual(calls, [])
                self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_the_wait_ends_a_margin_before_the_deadline(self):
        script = Script((503, {}))
        stack, clock = fake_clock()
        with stack, patch.object(wacd, "trust_context", lambda: None), patch.object(wacd, "transport", script), \
                patch.object(wacd, "arm_deadline", lambda deadline: (30.0, signal.getsignal(signal.SIGALRM))), \
                patch.object(wacd, "disarm", lambda previous: None):
            code, _, err = CliTests.main(self, "running", "--app", "travel", "--wait-timeout", "120",
                                         "--deadline", "0")
        self.assertEqual(code, 1)
        self.assertLessEqual(clock.now - 1000.0, 30 - wacd.DEADLINE_MARGIN, "the wait decides before the backstop")

    def test_the_backstop_is_disarmed_and_the_handler_restored_once_a_command_decides(self):
        def mine(signum, frame):
            raise AssertionError("the backstop fired after the command decided")

        steady_status = (200, evidence())
        cases = (
            ("released", ["running"], {"transport": Script(steady_status)}, 0),
            ("rolling out", ["running"], {"transport": Script((200, unsteady(UNSTEADY["degraded"])))},
             wacd.ROLLING_OUT_EXIT),
            ("a fault", ["running"], {"transport": Script((500, {}))}, 1),
            ("earlier, none", ["earlier-release"], {"read_copy": lambda app, v: "6"}, 0),
            ("earlier, refused", ["earlier-release"],
             {"read_copy": lambda app, v: "7", "release_exists": lambda tag: True}, wacd.ALREADY_RELEASED_EXIT),
        )
        previous = signal.signal(signal.SIGALRM, mine)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        for label, command, patches, expected in cases:
            with self.subTest(case=label):
                args = (["--app", "travel", "--wait-timeout", "120"] if command == ["running"] else
                        ["--app", "travel", "--prefix", "v", "--revision", "7", "--running", "1.3.9",
                         "--versions", "1.4.3", "--claimed", ""])
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(wacd, "trust_context", lambda: None))
                    stack.enter_context(patch.object(wacd, "read_release", lambda app, version, timeout=30: RELEASE))
                    stack.enter_context(patch.object(wacd, "DEADLINE_MARGIN", 1))
                    stack.enter_context(patch.object(wacd, "POLL_SECONDS", 0.2))
                    for name, value in patches.items():
                        stack.enter_context(patch.object(wacd, name, value))
                    code, _, err = CliTests.main(self, *command, *args, "--deadline", str(time.time() + 3.5))
                self.assertEqual(code, expected, err)
                self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
                self.assertIs(signal.getsignal(signal.SIGALRM), mine)
        time.sleep(1.5)


class EarlierReleaseTests(unittest.TestCase):
    """Rule B: an earlier version above the running one counts as released only if its GitHub Release exists."""

    SENTENCE = ("Revision 7 was released as {v}, and 1.3.9 runs now after a rollback. To put those settings on the "
                "running code, make them live again in hemi; they are released as a new revision.")

    def run_cli(self, copies, releases, versions="1.4.4,1.4.3,1.4.2", claimed=""):
        reads, asked = [], []

        def read_copy(app, version):
            reads.append(version)
            return copies.get(version, "absent")

        def released(tag):
            asked.append(tag)
            outcome = releases.get(tag, False)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(wacd, "read_copy", read_copy), patch.object(wacd, "release_exists", released):
            code, out, err = CliTests.main(
                self, "earlier-release", "--app", "travel", "--prefix", "v", "--revision", "7",
                "--running", "1.3.9", "--versions", versions, "--claimed", claimed)
        return reads, asked, (code, out, err)

    def test_a_version_carrying_the_revision_with_a_release_refuses(self):
        _, asked, (code, out, err) = self.run_cli({"1.4.4": "7"}, {"v1.4.4": True})
        self.assertEqual((code, out), (wacd.ALREADY_RELEASED_EXIT, ""))
        self.assertEqual(err, "wacd release: " + self.SENTENCE.format(v="1.4.4") + "\n")
        self.assertEqual(asked, ["v1.4.4"])

    def test_a_version_carrying_the_revision_that_never_ran_does_not_refuse(self):
        reads, asked, (code, out, err) = self.run_cli({"1.4.4": "7", "1.4.3": "6"}, {})
        self.assertEqual((code, out, err), (0, "reuse=\n", ""), "the settings release proceeds with a fresh version")
        self.assertEqual(asked, ["v1.4.4"])
        reads, asked, (code, out, err) = self.run_cli({"1.4.4": "7", "1.4.3": "7"}, {"v1.4.3": True})
        self.assertEqual(code, wacd.ALREADY_RELEASED_EXIT, "an older version that ran still counts")
        self.assertIn(self.SENTENCE.format(v="1.4.3"), err)

    def test_a_claim_on_the_running_code_is_taken_again_when_it_never_ran(self):
        for copy in ("absent", "7"):
            with self.subTest(claimed_chart=copy):
                copies = {} if copy == "absent" else {"1.4.4": copy}
                _, _, (code, out, err) = self.run_cli(copies, {}, claimed="1.4.4")
                self.assertEqual((code, out), (0, "reuse=1.4.4\n"), err)
        _, _, (code, out, err) = self.run_cli({"1.4.3": "7"}, {"v1.4.3": True}, claimed="1.4.4")
        self.assertEqual(code, wacd.ALREADY_RELEASED_EXIT, "a released earlier version still refuses beside a claim")

    def test_only_the_candidate_s_release_decides_never_the_running_version_s(self):
        for label, running_release in (("V has its own Release", True), ("V is pre-ledger with no Release", False)):
            with self.subTest(case=label):
                releases = {"v1.3.9": running_release, "v1.4.4": True}
                _, asked, (code, _, err) = self.run_cli({"1.4.4": "7"}, releases)
                self.assertEqual(code, wacd.ALREADY_RELEASED_EXIT, err)
                releases = {"v1.3.9": running_release}
                _, asked_none, (code, out, err) = self.run_cli({"1.4.4": "7", "1.4.3": "6"}, releases)
                self.assertEqual((code, out), (0, "reuse=\n"), err)
                self.assertNotIn("v1.3.9", asked + asked_none)

    def test_the_scan_stops_at_the_first_older_revision(self):
        reads, asked, (code, out, _) = self.run_cli({"1.4.4": "absent", "1.4.3": "5", "1.4.2": "7"}, {"v1.4.2": True})
        self.assertEqual((code, out), (0, "reuse=\n"))
        self.assertEqual((reads, asked), (["1.4.4", "1.4.3"], []), "revisions only grow with versions")
        reads, _, (code, out, _) = self.run_cli({"1.4.4": "none", "1.4.3": "7"}, {"v1.4.3": True})
        self.assertEqual((code, out, reads), (0, "reuse=\n", ["1.4.4"]), "a chart with no copy predates every copy")

    def test_a_release_that_cannot_be_read_fails_and_is_never_taken_as_absent(self):
        _, _, (code, out, err) = self.run_cli({"1.4.4": "7"}, {"v1.4.4": ReleaseError("GitHub answered HTTP 502")})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("HTTP 502", err)

    def test_the_count_is_bounded_with_a_sentence(self):
        versions = ",".join(f"1.5.{n}" for n in range(40, 0, -1))
        copies = {f"1.5.{n}": "7" for n in range(1, 41)}
        reads, asked, (code, out, err) = self.run_cli(copies, {}, versions=versions)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(len(reads), wacd.MAX_EARLIER)
        self.assertIn(f"{wacd.MAX_EARLIER} versions above 1.3.9", err)

    def test_no_versions_above_reads_nothing(self):
        reads, asked, (code, out, _) = self.run_cli({}, {}, versions="")
        self.assertEqual((code, out, reads, asked), (0, "reuse=\n", [], []))

    def test_release_exists_reads_one_tag_with_the_job_token(self):
        for status, expected in ((200, True), (404, False)):
            with self.subTest(status=status):
                api = ReleasesApi([status])
                self.addCleanup(api.close)
                env = {"GITHUB_API_URL": api.url, "GITHUB_REPOSITORY": "willfell/travel", "GITHUB_TOKEN": "t0k"}
                with patch.dict(os.environ, env):
                    self.assertIs(wacd.release_exists("v1.4.4"), expected)
                self.assertEqual(api.seen, [("GET", "/repos/willfell/travel/releases/tags/v1.4.4", "Bearer t0k", None)])
        for status in (401, 403, 500, 502):
            with self.subTest(status=status):
                api = ReleasesApi([status])
                self.addCleanup(api.close)
                env = {"GITHUB_API_URL": api.url, "GITHUB_REPOSITORY": "willfell/travel", "GITHUB_TOKEN": "t0k"}
                with patch.dict(os.environ, env), self.assertRaises(ReleaseError) as ctx:
                    wacd.release_exists("v1.4.4")
                self.assertIn(f"HTTP {status}", str(ctx.exception))
                self.assertNotIn("t0k", str(ctx.exception))
        with patch.dict(os.environ, {"GITHUB_API_URL": "http://127.0.0.1:9", "GITHUB_REPOSITORY": "willfell/travel",
                                     "GITHUB_TOKEN": "t0k"}), self.assertRaises(ReleaseError):
            wacd.release_exists("v1.4.4")


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


class Recorder:
    """A loopback server that records every request it gets, whatever the method."""

    def __init__(self, status=200, body=None, location=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def handle_any(self):
                size = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get_all("Authorization") or [],
                    "content_length": self.headers.get_all("Content-Length") or [],
                    "content_type": self.headers.get_all("Content-Type") or [],
                    "body": self.rfile.read(size),
                })
                raw = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_CONNECT = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        self.url = f"http://{self.address}/v1/release"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class PostTests(unittest.TestCase):
    """post() is the real opener wac-cd sends every gateway request through."""

    def serve(self, *args, **kwargs):
        server = Recorder(*args, **kwargs)
        self.addCleanup(server.close)
        return server

    def post(self, url, environ=None):
        with patch.object(wacd.ci, "URL", url), patch.object(wacd.ci, "oidc_token", lambda: TOKEN), \
                patch.dict(os.environ, environ or {}):
            return wacd.post(wacd.STATUS_BODY(), None, 5)

    def test_a_redirect_is_never_followed_and_the_token_never_resent(self):
        target = self.serve()
        gateway = self.serve(302, {}, location=f"http://{target.address}/stolen")
        status, _, _ = self.post(gateway.url)
        self.assertEqual(status, 302)
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(target.requests, [], "the Location target was contacted")

    def test_the_environment_proxy_sees_nothing(self):
        proxy = self.serve()
        gateway = self.serve(200, {"version": VERSION})
        proxy_url = f"http://{proxy.address}"
        status, value, _ = self.post(gateway.url, {
            "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url, "http_proxy": proxy_url,
            "https_proxy": proxy_url, "NO_PROXY": "", "no_proxy": "",
        })
        self.assertEqual((status, value), (200, {"version": VERSION}))
        self.assertEqual(proxy.requests, [])
        self.assertEqual(len(gateway.requests), 1)

    def test_one_post_with_one_length_a_json_type_and_the_bearer(self):
        gateway = self.serve(200, {})
        self.post(gateway.url)
        [request] = gateway.requests
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/release")
        self.assertEqual(request["authorization"], ["Bearer " + TOKEN])
        self.assertEqual(request["content_type"], ["application/json"])
        self.assertEqual(request["content_length"], [str(len(request["body"]))])
        self.assertEqual(json.loads(request["body"]), wacd.STATUS_BODY())


class UnansweredReleaseTests(unittest.TestCase):
    """Only plan's contract call rides out a gateway that answers nothing; deploy's release fails at once."""

    def test_a_release_nothing_answers_fails_at_once_with_one_token(self):
        closed = Gateway((200, {}))
        closed.close()

        def no_route(*args, **kwargs):
            raise OSError(errno.EHOSTUNREACH, "No route to host")

        for label, url, connect in (("refused", closed.url, None), ("no route to host", "http://127.0.0.1:9/v1/release",
                                                                     no_route)):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                minted = []
                stack, clock = fake_clock()
                out, err = io.StringIO(), io.StringIO()
                with stack, patch.object(wacd.ci, "URL", url), \
                        patch.object(wacd.ci, "oidc_token", lambda: minted.append(TOKEN) or TOKEN), \
                        patch.object(wacd, "trust_context", lambda: None), \
                        contextlib.ExitStack() as connecting, \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    if connect is not None:
                        connecting.enter_context(patch.object(socket, "create_connection", connect))
                    code = wacd.main(["wacd-release.py", *CliTests.release_argv(self, str(Path(tmp) / "r.json"))])
                self.assertEqual(code, 1)
                self.assertEqual(err.getvalue(), "wacd release: release transport failed; check TLS trust and "
                                                 "service readiness\n")
                self.assertEqual((len(minted), clock.sleeps), (1, []), "nothing is retried on the release path")


class RefusalTests(unittest.TestCase):
    """A coded 403 from the gateway reaches the job summary as a sentence, never as its body."""

    MARKER = "raw-gateway-body-never-printed"
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

    def release(self, *responses):
        gateway = Gateway(*responses)
        self.addCleanup(gateway.close)
        minted = []

        def mint():
            minted.append(f"minted.token.{len(minted)}")
            return minted[-1]

        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            argv = CliTests.release_argv(self, str(Path(tmp) / "receipt.json"))
            out, err = io.StringIO(), io.StringIO()
            stack, _ = fake_clock()
            with stack, patch.object(wacd.ci, "URL", gateway.url), patch.object(wacd.ci, "oidc_token", mint), \
                    patch.object(wacd, "trust_context", lambda: None), \
                    patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = wacd.main(["wacd-release.py", *argv])
            written = summary.read_text() if summary.exists() else ""
        everything = out.getvalue() + err.getvalue() + written
        for token in minted:
            self.assertNotIn(token, everything)
        self.assertNotIn(self.MARKER, everything)
        return code, err.getvalue(), written, gateway

    def refused(self, reason=None, status=403):
        body = {"error": "release request denied", "detail": self.MARKER}
        if reason is not None:
            body["reason"] = reason
        return status, body

    def test_each_release_check_code_reaches_the_summary_as_its_sentence(self):
        self.assertEqual(sorted(wacd.REFUSALS), sorted(self.CODES))
        for reason in self.CODES:
            with self.subTest(reason=reason):
                code, err, summary, gateway = self.release((200, running(VERSION)), self.refused(reason))
                self.assertEqual(code, 1)
                self.assertEqual(len(err.strip().splitlines()), 1)
                self.assertIn("HTTP 403", err)
                self.assertIn(wacd.REFUSALS[reason], err)
                self.assertIn(wacd.REFUSALS[reason], summary)
                self.assertEqual([body for _, body in gateway.seen], [wacd.STATUS_BODY(), wacd.RELEASE_BODY(VERSION)])

    def test_a_fault_the_gateway_names_as_final_says_re_running_will_not_help(self):
        for reason, sentence in (
            ("fleet_repository_unreachable", "The gateway's GitHub App can no longer read wac.fleet; the owner must "
                                             "restore its access, so re-running will not help."),
            ("fleet_repository_mismatch", "The gateway's pinned wac.fleet no longer names wacwini/wac.fleet; the "
                                          "gateway needs a change, so re-running will not help."),
        ):
            with self.subTest(reason=reason):
                code, err, summary, gateway = self.release((200, running(VERSION)), self.refused(reason))
                self.assertEqual(code, 1)
                self.assertIn(sentence, err)
                self.assertIn(sentence, summary)
                self.assertEqual(len(gateway.seen), 2, "a coded refusal is never asked again")

    def test_a_coded_pre_check_refusal_is_named_too(self):
        code, err, summary, _ = self.release(self.refused("revision_annotation_missing"))
        self.assertEqual(code, 1)
        self.assertIn(wacd.REFUSALS["revision_annotation_missing"], summary)

    def test_unknown_and_absent_codes_get_the_default_sentences(self):
        for reason, expected in (
            ("fleet_copy_haunted", wacd.UNKNOWN_REFUSAL + " (code fleet_copy_haunted)."),
            (self.MARKER, wacd.UNKNOWN_REFUSAL + "."),
            (7, wacd.UNKNOWN_REFUSAL + "."),
            (None, wacd.UNVERIFIED),
        ):
            with self.subTest(reason=str(reason)[:20]):
                code, err, summary, _ = self.release((200, running(VERSION)), self.refused(reason))
                self.assertEqual(code, 1)
                self.assertIn(expected, err)
                self.assertIn(expected, summary)

    def test_a_coded_body_on_another_status_is_only_its_status(self):
        code, err, summary, _ = self.release((200, running(VERSION)), self.refused("revision_mismatch", 409))
        self.assertEqual(code, 1)
        self.assertIn("HTTP 409", err)
        self.assertNotIn(wacd.REFUSALS["revision_mismatch"], err + summary)


class CliTests(unittest.TestCase):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wacd.main(["wacd-release.py", *argv])
        return code, out.getvalue(), err.getvalue()

    def release_argv(self, receipt, images=IMAGES, timeout="30", version=VERSION,
                     mode="release", tag="v" + VERSION, expected_sha=SOURCE):
        return (
            "release",
            "--version", version,
            "--images", json.dumps(images),
            "--wait-timeout", timeout,
            "--receipt", receipt,
            "--mode", mode,
            "--tag", tag,
            "--expected-sha", expected_sha,
        )

    def test_cli_release_writes_the_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            script = Script((200, released()))
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd, "transport", script
            ):
                code, out, err = self.main(*self.release_argv(receipt))
            self.assertEqual((code, err), (0, ""), err)
            self.assertIn(receipt, out)
            self.assertEqual(json.loads(Path(receipt).read_text()), {**released(), "wac_cd": WAC_CD})

    def test_cli_receipt_keeps_the_released_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            script = Script(
                (200, evidence()), (200, released(not_ready())), (200, evidence())
            )
            stack, _ = fake_clock()
            with stack, patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd, "transport", script
            ):
                code, _, err = self.main(*self.release_argv(receipt))
            self.assertEqual((code, err), (0, ""), err)
            self.assertEqual(
                script.bodies,
                [wacd.STATUS_BODY(), wacd.RELEASE_BODY(VERSION), wacd.STATUS_BODY()],
            )
            written = json.loads(Path(receipt).read_text())
            self.assertEqual(written["released"]["chart_source_sha"], SOURCE)
            self.assertEqual(written["released"], released()["released"])
            self.assertEqual(
                written, {**evidence(), "released": released()["released"], "wac_cd": WAC_CD}
            )

    def test_cli_receipt_records_the_wac_cd_plan(self):
        rollback = {"mode": "rollback", "version": VERSION, "tag": "app-v" + VERSION,
                    "expected_sha": "b" * 40}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd, "transport", Script((200, released()))
            ):
                code, _, err = self.main(*self.release_argv(
                    receipt, mode="rollback", tag="app-v" + VERSION, expected_sha="b" * 40))
            self.assertEqual((code, err), (0, ""), err)
            self.assertEqual(json.loads(Path(receipt).read_text())["wac_cd"], rollback)

    def test_cli_never_prints_the_token(self):
        def explode(body, trust, timeout):
            raise RuntimeError(f"Authorization: Bearer {TOKEN}")

        with tempfile.TemporaryDirectory() as tmp:
            receipt = str(Path(tmp) / "receipt.json")
            with patch.object(wacd, "trust_context", lambda: None), patch.object(
                wacd, "transport", explode
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
                    wacd, "transport", Script((code_, {}))
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
                "mode publish is never released": self.release_argv(receipt, mode="publish"),
                "mode none": self.release_argv(receipt, mode="none"),
                "blank tag": self.release_argv(receipt, tag=""),
                "short expected sha": self.release_argv(receipt, expected_sha="abc"),
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
                    with patch.object(wacd, "transport", Script((200, released()))) as _:
                        code, out, err = self.main(*argv)
                    self.assertEqual(code, 1, err)
                    self.assertNotIn("Traceback", err)

    def test_cli_images_must_be_json(self):
        code, _, err = self.main(
            "release", "--version", VERSION, "--images", "{", "--wait-timeout", "5",
            "--receipt", "/nonexistent/receipt.json",
            "--mode", "release", "--tag", "v" + VERSION, "--expected-sha", SOURCE,
        )
        self.assertEqual(code, 1)
        self.assertIn("JSON", err)
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



class ReleasesApi:
    """A stub GitHub REST API for the releases endpoints.

    GET answers come from `gets` in order (the last one repeats); every request,
    including any write other than the POST under test, is recorded in `seen`.
    """

    def __init__(self, gets, post_status=201):
        self.gets = list(gets)
        self.post_status = post_status
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def record(self, method):
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(size)) if size else None
                outer.seen.append((method, self.path, self.headers["Authorization"], body))
                return body

            def do_GET(self):
                self.record("GET")
                status = outer.gets.pop(0) if len(outer.gets) > 1 else outer.gets[0]
                self.reply(status, {"tag_name": self.path.rsplit("/", 1)[-1]})

            def do_POST(self):
                body = self.record("POST")
                self.reply(outer.post_status, {"tag_name": (body or {}).get("tag_name")})

            def do_PATCH(self):
                self.record("PATCH")
                self.reply(200, {})

            def do_PUT(self):
                self.record("PUT")
                self.reply(200, {})

            def do_DELETE(self):
                self.record("DELETE")
                self.reply(204, {})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GithubReleaseTests(unittest.TestCase):
    TAG = "v0.1.0"
    GH_TOKEN = "ghs_fake_release_token"
    RELEASES = "/repos/willfell/travel/releases"

    def publish(self, api, tag=TAG, sha=SOURCE, env=None):
        environ = {
            "GITHUB_API_URL": api.url if api else "http://127.0.0.1:9",
            "GITHUB_REPOSITORY": "willfell/travel",
            "GITHUB_TOKEN": self.GH_TOKEN,
            "PATH": os.environ.get("PATH", ""),
            "NO_PROXY": "127.0.0.1",
            "no_proxy": "127.0.0.1",
        }
        environ.update(env or {})
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "github-release", "--tag", tag, "--sha", sha],
            capture_output=True,
            text=True,
            env=environ,
            timeout=60,
        )
        self.assertNotIn(self.GH_TOKEN, result.stdout + result.stderr)
        return result

    def serve(self, gets, post_status=201):
        api = ReleasesApi(gets, post_status)
        self.addCleanup(api.close)
        return api

    def calls(self, api):
        return [(method, path) for method, path, _, _ in api.seen]

    def assert_one_line_failure(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("wacd release: "), result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
        self.assertEqual(result.stdout, "")

    def test_github_release_created_when_absent(self):
        api = self.serve([404], 201)
        result = self.publish(api)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.TAG, result.stdout)
        self.assertEqual(
            self.calls(api),
            [("GET", f"{self.RELEASES}/tags/{self.TAG}"), ("POST", self.RELEASES)],
        )
        self.assertTrue(all(auth == f"Bearer {self.GH_TOKEN}" for _, _, auth, _ in api.seen))

    def test_rollback_of_a_version_with_no_release_creates_it(self):
        revision = "b" * 40
        api = self.serve([404], 201)
        result = self.publish(api, tag="app-v0.0.7", sha=revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        posts = [body for method, _, _, body in api.seen if method == "POST"]
        self.assertEqual(
            posts,
            [{
                "tag_name": "app-v0.0.7",
                "name": "app-v0.0.7",
                "target_commitish": revision,
                "generate_release_notes": True,
            }],
        )

    def test_rollback_of_a_version_with_a_release_leaves_it(self):
        api = self.serve([200], 201)
        result = self.publish(api, sha="b" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already exists", result.stdout)
        self.assertEqual(self.calls(api), [("GET", f"{self.RELEASES}/tags/{self.TAG}")])

    def test_github_release_created_concurrently_is_accepted(self):
        api = self.serve([404, 200], 422)
        result = self.publish(api)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [method for method, _ in self.calls(api)], ["GET", "POST", "GET"]
        )

    def test_github_release_failures(self):
        for label, gets, post, methods in (
            ("422 and still no release", [404], 422, ["GET", "POST", "GET"]),
            ("forbidden create", [404], 403, ["GET", "POST"]),
            ("forbidden lookup", [403], 201, ["GET"]),
            ("server error lookup", [500], 201, ["GET"]),
        ):
            with self.subTest(case=label):
                api = self.serve(gets, post)
                result = self.publish(api)
                self.assert_one_line_failure(result)
                self.assertEqual([method for method, _ in self.calls(api)], methods)

    def test_github_release_validates_before_calling(self):
        api = self.serve([404], 201)
        for label, kwargs in (
            ("short sha", {"sha": "abc"}),
            ("blank tag", {"tag": ""}),
            ("tag with a space", {"tag": "v0 .1.0"}),
            ("no token", {"env": {"GITHUB_TOKEN": ""}}),
            ("no repository", {"env": {"GITHUB_REPOSITORY": "travel"}}),
            ("api not http", {"env": {"GITHUB_API_URL": "file:///etc/passwd"}}),
        ):
            with self.subTest(case=label):
                result = self.publish(api, **kwargs)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertTrue(result.stderr.startswith("wacd release: "), result.stderr)
        self.assertEqual(api.seen, [])

    def test_github_release_unreachable_api_fails_cleanly(self):
        self.assert_one_line_failure(self.publish(None))


if __name__ == "__main__":
    unittest.main()
