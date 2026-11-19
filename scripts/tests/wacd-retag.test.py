"""wacd-retag gives each running image's manifest a new tag, byte for byte, with no layer copy.

Every case runs against a stand-in registry on 127.0.0.1 that records each
request; nothing reaches the in-cluster registry.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "wacd-retag.py"
SPEC = importlib.util.spec_from_file_location("wacd_retag", SCRIPT)
retag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retag)

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"


def manifest(media_type, marker):
    """Manifest bytes as a registry stores them: not canonical JSON, so a re-serialised copy would differ."""
    return (
        '{\n   "schemaVersion": 2,\n   "mediaType": "%s",\n   "marker": "%s"\n}' % (media_type, marker)
    ).encode()


def digest_of(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


class Registry:
    """Holds manifests by (repository, reference) and records every request."""

    def __init__(self):
        self.manifests = {}
        self.requests = []
        self.put_status = 201
        self.wrong_body = False
        self.put_digest = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def path_parts(self):
                prefix, _, rest = self.path.partition("/v2/")
                repo, _, ref = rest.rpartition("/manifests/")
                return prefix, repo, ref

            def do_GET(self):
                _, repo, ref = self.path_parts()
                outer.requests.append(("GET", self.path, dict(self.headers), None))
                found = outer.manifests.get((repo, ref))
                if found is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                media_type, body = found
                if outer.wrong_body:
                    body = body + b" "
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PUT(self):
                _, repo, ref = self.path_parts()
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.requests.append(("PUT", self.path, dict(self.headers), body))
                if outer.put_status == 201:
                    outer.manifests[(repo, ref)] = (self.headers.get("Content-Type"), body)
                self.send_response(outer.put_status)
                self.send_header("Docker-Content-Digest", outer.put_digest or digest_of(body))
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def hold(self, repo, media_type, marker):
        body = manifest(media_type, marker)
        digest = digest_of(body)
        self.manifests[(repo, digest)] = (media_type, body)
        return digest, body

    def puts(self):
        return [(path, headers, body) for method, path, headers, body in self.requests if method == "PUT"]


def running(images):
    """The images map as plan reads it from the running version's chart (chart-images)."""
    return json.dumps(
        {
            name: {"repository": f"localhost:30500/{name}", "tag": "1.4.2", "digest": digest}
            for name, digest in images.items()
        }
    )


class RetagTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry().__enter__()
        self.addCleanup(self.registry.__exit__)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(retag, "PUSH_REGISTRY", self.registry.address), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = retag.main(["wacd-retag.py", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_each_media_type_is_put_under_the_new_tag_with_the_same_bytes_and_type(self):
        held = {}
        for name, media_type in (
            ("web", OCI_MANIFEST),
            ("worker", OCI_INDEX),
            ("cron", DOCKER_MANIFEST),
            ("api", DOCKER_LIST),
        ):
            held[name] = (media_type, *self.registry.hold(name, media_type, name))
        code, out, err = self.cli("--images", running({n: d for n, (_, d, _) in held.items()}), "--version", "1.4.3")
        self.assertEqual(code, 0, err)
        puts = {path: (headers, body) for path, headers, body in self.registry.puts()}
        self.assertEqual(sorted(puts), sorted(f"/v2/{name}/manifests/1.4.3" for name in held))
        for name, (media_type, digest, body) in held.items():
            with self.subTest(image=name):
                headers, sent = puts[f"/v2/{name}/manifests/1.4.3"]
                self.assertEqual(sent, body, "the exact bytes, so the digest the chart names is the one the tag serves")
                self.assertEqual(headers["Content-Type"], media_type)
                self.assertEqual(self.registry.manifests[(name, "1.4.3")], (media_type, body))
        self.assertEqual(
            out,
            "images="
            + json.dumps(
                {
                    name: {"digest": digest, "repository": f"localhost:30500/{name}", "tag": "1.4.3"}
                    for name, (_, digest, _) in held.items()
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n",
        )

    def test_the_manifest_is_read_by_digest_accepting_oci_and_docker_manifests_and_indexes(self):
        digest, _ = self.registry.hold("web", OCI_INDEX, "web")
        code, _, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual(code, 0, err)
        gets = [(path, headers) for method, path, headers, _ in self.registry.requests if method == "GET"]
        self.assertEqual([path for path, _ in gets], ["/v2/web/manifests/1.4.3", f"/v2/web/manifests/{digest}"],
                         "the new tag is read first, then the manifest by digest")
        for path, headers in gets:
            with self.subTest(path=path):
                accepted = {part.strip() for part in headers["Accept"].split(",")}
                self.assertEqual(accepted, {OCI_MANIFEST, OCI_INDEX, DOCKER_MANIFEST, DOCKER_LIST})
        self.assertEqual(
            [method for method, *_ in self.registry.requests], ["GET", "GET", "PUT"],
            "two manifest reads and one manifest write: no blob is read or uploaded",
        )

    def test_a_tag_already_on_the_same_digest_is_left_as_it_is(self):
        digest, body = self.registry.hold("web", OCI_MANIFEST, "web")
        for _ in range(2):
            code, out, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
            self.assertEqual(code, 0, err)
            self.assertIn('"tag":"1.4.3"', out)
        self.assertEqual([sent for _, _, sent in self.registry.puts()], [body], "the re-run writes nothing")

    def test_a_tag_already_on_another_digest_fails_and_is_never_overwritten(self):
        digest, _ = self.registry.hold("web", OCI_MANIFEST, "web")
        other = manifest(OCI_MANIFEST, "a build that already took 1.4.3")
        self.registry.manifests[("web", "1.4.3")] = (OCI_MANIFEST, other)
        code, out, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, f"wacd retag: web:1.4.3 already names {digest_of(other)}, not {digest}; "
                              "a version is never retagged\n")
        self.assertEqual(self.registry.puts(), [])
        self.assertEqual(self.registry.manifests[("web", "1.4.3")], (OCI_MANIFEST, other))

    def test_failures_put_nothing_and_say_why_in_one_line(self):
        digest, _ = self.registry.hold("web", OCI_MANIFEST, "web")
        missing = "sha256:" + "0" * 64
        for label, images, version, needle in (
            ("the digest is not in the registry", running({"web": missing}), "1.4.3", "HTTP 404"),
            ("bad version", running({"web": digest}), "v1.4.3", "--version"),
            ("not JSON", "{", "1.4.3", "--images"),
            ("empty", "{}", "1.4.3", "--images"),
            ("another registry", json.dumps({"web": {"repository": "evil.example/web", "tag": "1", "digest": digest}}),
             "1.4.3", "web"),
            ("bad digest", json.dumps({"web": {"repository": "localhost:30500/web", "tag": "1", "digest": "sha256:x"}}),
             "1.4.3", "web"),
            ("bad name", json.dumps({"We b": {"repository": "localhost:30500/We b", "tag": "1", "digest": digest}}),
             "1.4.3", "We b"),
        ):
            with self.subTest(case=label):
                self.registry.requests.clear()
                code, out, err = self.cli("--images", images, "--version", version)
                self.assertEqual(code, 1, out)
                self.assertEqual(out, "")
                self.assertTrue(err.startswith("wacd retag: "), err)
                self.assertEqual(len(err.strip().splitlines()), 1, err)
                self.assertIn(needle, err)
                self.assertEqual(self.registry.puts(), [])

    def test_bytes_that_do_not_match_the_digest_are_never_written(self):
        digest, _ = self.registry.hold("web", OCI_MANIFEST, "web")
        self.registry.wrong_body = True
        code, _, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)
        self.assertEqual(self.registry.puts(), [])

    def test_a_refused_put_or_another_digest_fails(self):
        digest, _ = self.registry.hold("web", OCI_MANIFEST, "web")
        self.registry.put_status = 400
        code, _, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 400", err)
        self.registry.put_status = 201
        self.registry.put_digest = "sha256:" + "1" * 64
        code, _, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual(code, 1)
        self.assertIn("sha256:1111", err)

    def test_an_unknown_media_type_is_not_written(self):
        body = manifest("application/vnd.example+json", "web")
        digest = digest_of(body)
        self.registry.manifests[("web", digest)] = ("application/vnd.example+json", body)
        code, _, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual(code, 1)
        self.assertIn("application/vnd.example+json", err)
        self.assertEqual(self.registry.puts(), [])

    def test_the_registry_is_the_push_address_and_no_input_moves_it(self):
        self.assertEqual(retag.PUSH_REGISTRY, "registry.network.svc.cluster.local:5000")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                retag.build_parser().parse_args(["--images", "{}", "--version", "1.0.0", "--registry", "x"])

    def test_the_environment_proxy_sees_nothing(self):
        digest, _ = self.registry.hold("web", OCI_MANIFEST, "web")
        with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"}):
            code, _, err = self.cli("--images", running({"web": digest}), "--version", "1.4.3")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
