#!/usr/bin/env python3
"""Expose the default upstream schema registry through verified GitHub API TLS."""

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        raise HTTPError(request.full_url, 502, "upstream redirect refused", headers, file)


urlopen = build_opener(NoRedirects()).open

SCHEMA_PATH = re.compile(
    r"/(?:master|v[0-9]+\.[0-9]+\.[0-9]+)-standalone(?:-strict)?/[a-z0-9.-]+\.json"
)
CRD_PATH = re.compile(r"/crds/([a-z0-9.-]+/[a-z0-9._-]+\.json)")


def fetch_schema(path):
    crd = CRD_PATH.fullmatch(path)
    if ".." in path or not (SCHEMA_PATH.fullmatch(path) or crd):
        return 400, b"unsupported schema path"
    if crd:
        source = "datreeio/CRDs-catalog/contents/" + crd.group(1) + "?ref=main"
    else:
        source = "yannh/kubernetes-json-schema/contents" + path + "?ref=master"
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": "lab-kubeconform"}
    if token := os.environ.get("SCHEMA_GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    request = Request(
        "https://api.github.com/repos/" + source,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
        schema = json.loads(body)
        if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("upstream response is not a JSON schema")
        if "$schema" in schema and not re.fullmatch(r"https?://json-schema.org/[^\s]+", str(schema["$schema"])):
            raise ValueError("upstream schema dialect is invalid")
        return 200, body
    except HTTPError as error:
        code = error.code
        error.close()
        if code == 404:
            return 404, b"upstream schema absent"
        return 502, b"upstream API failed"
    except (URLError, TimeoutError, OSError, ValueError):
        return 502, b"upstream schema retrieval failed"


class SchemaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, body = fetch_schema(self.path)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port_file", type=Path)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", 0), SchemaHandler) as server:
        args.port_file.write_text(str(server.server_port))
        server.serve_forever()


if __name__ == "__main__":
    main()
