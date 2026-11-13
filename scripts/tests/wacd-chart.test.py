"""wacd-chart packages the per-app deploy chart and reads registry state back."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "wacd-chart.py"
SPEC = importlib.util.spec_from_file_location("wacd_chart", SCRIPT)
chart = importlib.util.module_from_spec(SPEC)
sys.modules["wacd_chart"] = chart
SPEC.loader.exec_module(chart)

SHA = "a" * 40
OTHER = "c" * 40
DIGEST = "sha256:" + "b" * 64
HELM_CONFIG = "application/vnd.cncf.helm.config.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
IMAGES = {
    "travel-app": {
        "repository": "localhost:30500/travel-app",
        "tag": "0.1.0",
        "digest": DIGEST,
    }
}
PROFILE = {
    "schema_version": 1,
    "app": "travel",
    "chart_version": "1.0.0",
    "values_file": "deploy/values.yaml",
    "tag_prefix": "v",
    "health_url": "http://travel-app.travel.svc.cluster.local:3000/api/health",
    "images": [{"name": "travel-app", "dockerfile": "Dockerfile", "context": "."}],
    "ci": {"stack": "node", "node_version": "22", "commands": ["npm test"]},
}
HAVE_HELM = shutil.which("helm") is not None


def digest_of(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


class Registry:
    """A canned OCI registry on a loopback port chosen by the kernel."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        routes, requests = self.routes, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Accept", "")))
                status, ctype, body = routes.get(self.path, (404, "text/plain", b"nope"))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def put_blob(self, repo, body):
        digest = digest_of(body)
        self.routes[f"/v2/{repo}/blobs/{digest}"] = (200, "application/octet-stream", body)
        return digest

    def put_manifest(self, repo, ref, config_digest, config_type, status=200):
        manifest = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": OCI_MANIFEST,
                "config": {"mediaType": config_type, "digest": config_digest, "size": 1},
                "layers": [],
            }
        ).encode()
        self.routes[f"/v2/{repo}/manifests/{ref}"] = (status, OCI_MANIFEST, manifest)

    def put_chart(self, app, version, revision=SHA, images="", config_type=HELM_CONFIG):
        annotations = {}
        if revision is not None:
            annotations["org.opencontainers.image.revision"] = revision
        if images:
            annotations["wacwini.com/images"] = images
        body = json.dumps({"name": app, "version": version, "annotations": annotations}).encode()
        repo = f"deploy/{app}"
        self.put_manifest(repo, version, self.put_blob(repo, body), config_type)

    def put_image(self, name, tag, revision=SHA):
        labels = {} if revision is None else {"org.opencontainers.image.revision": revision}
        body = json.dumps({"architecture": "arm64", "config": {"Labels": labels}}).encode()
        self.put_manifest(name, tag, self.put_blob(name, body), "application/vnd.oci.image.config.v1+json")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


class RegistryCase(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.addCleanup(self.registry.close)
        self.address = self.registry.address


class ValuesTests(unittest.TestCase):
    def test_values_nest_under_wac_app_with_app_and_images(self):
        got = chart.deploy_values({"name": "travel-app", "image": "travel-app"}, "travel", IMAGES)
        self.assertEqual(
            got,
            {
                "wac-app": {
                    "name": "travel-app",
                    "image": "travel-app",
                    "app": "travel",
                    "images": IMAGES,
                }
            },
        )

    def test_values_may_not_set_owned_keys(self):
        for key in ("app", "images", "global"):
            with self.assertRaises(chart.ChartError) as ctx:
                chart.deploy_values({"name": "x", key: "y"}, "travel", IMAGES)
            self.assertIn(key, str(ctx.exception))
        with self.assertRaises(chart.ChartError) as ctx:
            chart.deploy_values(["a"], "travel", IMAGES)
        self.assertIn("mapping", str(ctx.exception))


class RefTests(unittest.TestCase):
    def test_chart_metadata(self):
        got = chart.chart_metadata("travel", "0.1.0", "1.0.0", SHA, IMAGES)
        self.assertEqual(
            got,
            {
                "apiVersion": "v2",
                "name": "travel",
                "version": "0.1.0",
                "appVersion": "0.1.0",
                "type": "application",
                "annotations": {
                    "org.opencontainers.image.revision": SHA,
                    "wacwini.com/images": "localhost:30500/travel-app:0.1.0@sha256:" + "b" * 64,
                },
                "dependencies": [
                    {
                        "name": "wac-app",
                        "version": "1.0.0",
                        "repository": "oci://registry.network.svc.cluster.local:5000/charts",
                    }
                ],
            },
        )

    def test_chart_metadata_without_images_omits_the_annotation(self):
        got = chart.chart_metadata("travel", "0.1.0", "1.0.0", SHA, None)
        self.assertEqual(got["annotations"], {"org.opencontainers.image.revision": SHA})

    def test_image_refs_round_trip_and_reject_bad_refs(self):
        two = dict(IMAGES)
        two["a-worker"] = {"repository": "localhost:30500/a-worker", "tag": "0.1.0", "digest": DIGEST}
        text = chart.image_refs(two)
        self.assertTrue(text.startswith("localhost:30500/a-worker:"))
        self.assertEqual(chart.parse_image_refs(text), two)
        bad = "registry.network.svc.cluster.local:5000/x:1@" + DIGEST
        with self.assertRaises(chart.ChartError):
            chart.parse_image_refs(bad)
        with self.assertRaises(chart.ChartError):
            chart.parse_image_refs("localhost:30500/x:1@sha256:abc")
        with self.assertRaises(chart.ChartError):
            chart.parse_image_refs("localhost:30500/x:1")
        with self.assertRaises(chart.ChartError):
            chart.image_refs({"x": {"repository": "localhost:30500/x", "tag": "1"}})

    def test_ci_images(self):
        got = chart.ci_images(PROFILE)
        self.assertEqual(
            got, {"travel-app": {"repository": "localhost:30500/travel-app", "tag": "ci"}}
        )


class RegistryReadTests(RegistryCase):
    def test_chart_revision_and_images_from_the_config_blob(self):
        refs = chart.image_refs(IMAGES)
        self.registry.put_chart("travel", "0.1.0", images=refs)
        self.assertEqual(chart.chart_revision(self.address, "travel", "0.1.0"), SHA)
        self.assertEqual(chart.chart_images(self.address, "travel", "0.1.0"), IMAGES)
        accept = self.registry.requests[0][1]
        self.assertIn(OCI_MANIFEST, accept)
        self.assertIn("application/vnd.docker.distribution.manifest.v2+json", accept)

    def test_absent_chart_is_none_and_the_cli_exits_1(self):
        self.assertIsNone(chart.chart_revision(self.address, "travel", "0.1.0"))
        self.assertIsNone(chart.chart_images(self.address, "travel", "0.1.0"))
        for command, key in (("chart-revision", "revision"), ("chart-images", "images")):
            result = run_cli(
                command, "--registry", self.address, "--app", "travel", "--version", "0.1.0"
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertTrue(result.stderr.startswith("wacd chart: "), result.stderr)
            self.assertNotIn(key + "=", result.stdout)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_prints_revision_and_images(self):
        self.registry.put_chart("travel", "0.1.0", images=chart.image_refs(IMAGES))
        base = ("--registry", self.address, "--app", "travel", "--version", "0.1.0")
        revision = run_cli("chart-revision", *base)
        self.assertEqual((revision.returncode, revision.stdout), (0, f"revision={SHA}\n"))
        images = run_cli("chart-images", *base)
        self.assertEqual(images.returncode, 0, images.stderr)
        line = images.stdout.rstrip("\n")
        self.assertTrue(line.startswith("images=") and "\n" not in line)
        self.assertEqual(json.loads(line[len("images="):]), IMAGES)

    def test_wrong_config_media_type_is_an_error(self):
        self.registry.put_chart("travel", "0.1.0", config_type="application/json")
        with self.assertRaises(chart.ChartError) as ctx:
            chart.chart_config(self.address, "travel", "0.1.0")
        self.assertIn(HELM_CONFIG, str(ctx.exception))

    def test_present_chart_without_revision_fails(self):
        self.registry.put_chart("travel", "0.1.0", revision=None)
        with self.assertRaises(chart.ChartError) as ctx:
            chart.chart_revision(self.address, "travel", "0.1.0")
        self.assertIn("revision", str(ctx.exception))
        self.registry.put_chart("travel", "0.2.0", revision="abc")
        with self.assertRaises(chart.ChartError):
            chart.chart_revision(self.address, "travel", "0.2.0")

    def test_chart_state_absent_present_and_conflicting(self):
        base = ("chart-state", "--registry", self.address, "--app", "travel", "--version", "0.1.0")
        absent = run_cli(*base, "--source-sha", SHA)
        self.assertEqual((absent.returncode, absent.stdout), (0, "state=absent\n"))
        self.registry.put_chart("travel", "0.1.0", revision=SHA)
        present = run_cli(*base, "--source-sha", SHA)
        self.assertEqual((present.returncode, present.stdout), (0, "state=present\n"))
        conflict = run_cli(*base, "--source-sha", OTHER)
        self.assertEqual(conflict.returncode, 1)
        self.assertIn(SHA, conflict.stderr)
        self.assertIn(OTHER, conflict.stderr)
        self.assertTrue(conflict.stderr.startswith("wacd chart: "))
        self.registry.routes["/v2/deploy/travel/manifests/0.1.0"] = (500, "text/plain", b"boom")
        failed = run_cli(*base, "--source-sha", SHA)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("500", failed.stderr)

    def test_image_state_reads_the_config_label(self):
        base = ("image-state", "--registry", self.address, "--name", "travel-app", "--tag", "0.1.0")
        absent = run_cli(*base, "--source-sha", SHA)
        self.assertEqual((absent.returncode, absent.stdout), (0, "state=absent\n"))
        self.registry.put_image("travel-app", "0.1.0", SHA)
        self.assertEqual(chart.image_revision(self.address, "travel-app", "0.1.0"), SHA)
        present = run_cli(*base, "--source-sha", SHA)
        self.assertEqual((present.returncode, present.stdout), (0, "state=present\n"))
        conflict = run_cli(*base, "--source-sha", OTHER)
        self.assertEqual(conflict.returncode, 1)
        self.assertIn(SHA, conflict.stderr)
        self.assertIn(OTHER, conflict.stderr)

    def test_image_without_a_revision_label_is_an_error(self):
        self.registry.put_image("travel-app", "0.1.0", None)
        with self.assertRaises(chart.ChartError):
            chart.image_revision(self.address, "travel-app", "0.1.0")

    def test_transport_failure_is_a_chart_error(self):
        dead = Registry()
        address = dead.address
        dead.close()
        with self.assertRaises(chart.ChartError):
            chart.manifest_state(address, "deploy/travel", "0.1.0", 2)

    def test_oversized_response_is_refused(self):
        self.registry.routes["/v2/deploy/travel/manifests/0.1.0"] = (
            200,
            OCI_MANIFEST,
            b" " * (4 * 1024 * 1024 + 1),
        )
        with self.assertRaises(chart.ChartError):
            chart.manifest_state(self.address, "deploy/travel", "0.1.0", 5)

    def test_unsafe_arguments_never_reach_the_url(self):
        with self.assertRaises(chart.ChartError):
            chart.chart_config(self.address, "../x", "0.1.0")
        with self.assertRaises(chart.ChartError):
            chart.chart_config(self.address, "travel", "latest")
        self.assertEqual(self.registry.requests, [])


class IndexTests(RegistryCase):
    def test_an_image_index_is_followed_to_its_runnable_manifest(self):
        self.registry.put_image("travel-app", "sha-1", SHA)
        inner = self.registry.routes["/v2/travel-app/manifests/sha-1"][2]
        inner_digest = digest_of(inner)
        self.registry.routes[f"/v2/travel-app/manifests/{inner_digest}"] = (
            200,
            OCI_MANIFEST,
            inner,
        )
        index = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [
                    {
                        "digest": "sha256:" + "d" * 64,
                        "platform": {"os": "unknown", "architecture": "unknown"},
                    },
                    {"digest": inner_digest, "platform": {"os": "linux", "architecture": "arm64"}},
                ],
            }
        ).encode()
        self.registry.routes["/v2/travel-app/manifests/0.1.0"] = (
            200,
            "application/vnd.oci.image.index.v1+json",
            index,
        )
        self.assertEqual(chart.image_revision(self.address, "travel-app", "0.1.0"), SHA)


@unittest.skipUnless(
    HAVE_HELM or os.environ.get("WACD_REQUIRE_HELM"), "helm is not installed"
)
class HelmTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def helm(self, *args, cwd=None):
        result = subprocess.run(
            ["helm", *args], capture_output=True, text=True, cwd=cwd, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def fake_wac_app(self):
        src = self.base / "src" / "wac-app"
        (src / "templates").mkdir(parents=True)
        (src / "Chart.yaml").write_text(
            json.dumps({"apiVersion": "v2", "name": "wac-app", "version": "1.0.0"})
        )
        (src / "templates" / "cm.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Values.name }}\n"
            "data:\n  app: {{ .Values.app | quote }}\n"
            "  tag: {{ (index .Values.images .Values.image).tag | quote }}\n"
        )
        out = self.base / "tgz"
        out.mkdir()
        self.helm("package", str(src), "--destination", str(out))
        return out / "wac-app-1.0.0.tgz"

    def repo(self, values):
        repo = self.base / "repo"
        (repo / "deploy").mkdir(parents=True, exist_ok=True)
        (repo / "deploy" / "values.yaml").write_text(values)
        return repo / "deploy" / "values.yaml"

    def test_packaged_chart_renders_through_helm(self):
        values = self.repo("# the app\nname: travel-app\nimage: travel-app\nreplicas: 2\n")
        tgz = self.fake_wac_app()
        chart_dir = chart.write_chart(self.base / "out", PROFILE, values, "0.1.0", SHA, IMAGES, tgz)
        self.assertEqual(chart_dir, self.base / "out" / "travel")
        self.assertTrue((chart_dir / "charts" / "wac-app-1.0.0.tgz").is_file())
        pkg = self.base / "pkg"
        pkg.mkdir()
        self.helm("package", str(chart_dir), "--destination", str(pkg))
        packaged = pkg / "travel-0.1.0.tgz"
        self.assertTrue(packaged.is_file())
        rendered = self.helm("template", "travel", str(packaged))
        self.assertIn("name: travel-app", rendered)
        self.assertIn('app: "travel"', rendered)
        self.assertIn('tag: "0.1.0"', rendered)
        shown = self.helm("show", "chart", str(packaged))
        self.assertIn(f"org.opencontainers.image.revision: {SHA}", shown)
        self.assertIn("localhost:30500/travel-app:0.1.0@" + DIGEST, shown)

    def test_json_values_files_nest_too(self):
        values = self.repo(json.dumps({"name": "travel-app", "image": "travel-app"}))
        tgz = self.fake_wac_app()
        chart_dir = chart.write_chart(self.base / "out", PROFILE, values, "0.1.0", SHA, IMAGES, tgz)
        pkg = self.base / "pkg"
        pkg.mkdir()
        self.helm("package", str(chart_dir), "--destination", str(pkg))
        rendered = self.helm("template", "travel", str(pkg / "travel-0.1.0.tgz"))
        self.assertIn('tag: "0.1.0"', rendered)

    def test_text_values_reject_owned_keys_and_non_mappings(self):
        tgz = self.fake_wac_app()
        for key in ("app", "images", "global"):
            values = self.repo(f"name: x\n{key}: y\n")
            with self.assertRaises(chart.ChartError) as ctx:
                chart.write_chart(self.base / "out", PROFILE, values, "0.1.0", SHA, IMAGES, tgz)
            self.assertIn(key, str(ctx.exception))
        values = self.repo('"app": y\n')
        with self.assertRaises(chart.ChartError):
            chart.write_chart(self.base / "out", PROFILE, values, "0.1.0", SHA, IMAGES, tgz)
        values = self.repo("app-name: ok\nname: x\nimage: travel-app\n")
        chart.write_chart(self.base / "out", PROFILE, values, "0.1.0", SHA, IMAGES, tgz)
        values = self.repo("- a\n- b\n")
        with self.assertRaises(chart.ChartError) as ctx:
            chart.write_chart(self.base / "out", PROFILE, values, "0.1.0", SHA, IMAGES, tgz)
        self.assertIn("mapping", str(ctx.exception))

    def test_ci_mode_allows_images_without_digests(self):
        values = self.repo("name: travel-app\nimage: travel-app\n")
        tgz = self.fake_wac_app()
        images = chart.ci_images(PROFILE)
        with self.assertRaises(chart.ChartError):
            chart.write_chart(self.base / "strict", PROFILE, values, "0.1.0", SHA, images, tgz)
        chart_dir = chart.write_chart(
            self.base / "out", PROFILE, values, "0.1.0", SHA, images, tgz, ci=True
        )
        meta = json.loads((chart_dir / "Chart.yaml").read_text())
        self.assertNotIn("wacwini.com/images", meta["annotations"])
        pkg = self.base / "pkg"
        pkg.mkdir()
        self.helm("package", str(chart_dir), "--destination", str(pkg))
        rendered = self.helm("template", "travel", str(pkg / "travel-0.1.0.tgz"))
        self.assertIn('tag: "ci"', rendered)


class CliPackageTests(unittest.TestCase):
    def test_package_cli_prints_the_chart_dir_and_fails_in_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / ".github").mkdir(parents=True)
            (repo / "deploy").mkdir()
            (repo / "deploy" / "values.yaml").write_text("name: travel-app\nimage: travel-app\n")
            (repo / ".github" / "wacd.json").write_text(json.dumps(PROFILE))
            tgz = Path(tmp) / "wac-app-1.0.0.tgz"
            tgz.write_bytes(b"fake")
            out = Path(tmp) / "out"
            args = [
                "package", "--profile", str(repo / ".github" / "wacd.json"),
                "--version", "0.1.0", "--source-sha", SHA,
                "--images", json.dumps(IMAGES), "--wac-app-tgz", str(tgz), "--out", str(out),
            ]
            ok = run_cli(*args)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(ok.stdout, f"chart_dir={out / 'travel'}\n")
            self.assertTrue((out / "travel" / "Chart.yaml").is_file())
            bad = run_cli(*args[:-4], "--wac-app-tgz", str(Path(tmp) / "missing.tgz"), "--out", str(out))
            self.assertEqual(bad.returncode, 1)
            self.assertTrue(bad.stderr.startswith("wacd chart: "), bad.stderr)
            self.assertEqual(len(bad.stderr.strip().splitlines()), 1)
            unknown = run_cli(*args[:-6], "--images", json.dumps({"other": IMAGES["travel-app"]}),
                              "--wac-app-tgz", str(tgz), "--out", str(out))
            self.assertEqual(unknown.returncode, 1)
            self.assertIn("travel-app", unknown.stderr)

    def test_ci_images_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / ".github").mkdir(parents=True)
            (repo / "deploy").mkdir()
            (repo / "deploy" / "values.yaml").write_text("name: x\n")
            (repo / ".github" / "wacd.json").write_text(json.dumps(PROFILE))
            result = run_cli("ci-images", "--profile", str(repo / ".github" / "wacd.json"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout[len("images="):]),
                {"travel-app": {"repository": "localhost:30500/travel-app", "tag": "ci"}},
            )


if __name__ == "__main__":
    unittest.main()
