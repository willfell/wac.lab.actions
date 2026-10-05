import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

spec = importlib.util.spec_from_file_location('adapter', Path(__file__).parents[1] / 'schema-api-proxy.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
PATH = '/v1.35.0-standalone-strict/service-v1.json'
VALID = b'{"$schema":"http://json-schema.org/draft-04/schema#","type":"object","properties":{}}'


class AdapterTests(unittest.TestCase):
    def test_same_upstream_ref_path_and_verified_api_transport(self):
        with patch.object(adapter, 'urlopen', return_value=io.BytesIO(VALID)) as fetch:
            self.assertEqual(adapter.fetch_schema(PATH), (200, VALID))
            request = fetch.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.github.com/repos/yannh/kubernetes-json-schema/contents' + PATH + '?ref=master')
            self.assertEqual(request.get_header('Accept'), 'application/vnd.github.raw+json')

    def test_only_genuine_upstream_404_remains_missing(self):
        for code in (401, 403, 404, 429, 500):
            with self.subTest(code=code), patch.object(adapter, 'urlopen', side_effect=HTTPError('api', code, 'failure', {}, None)):
                self.assertEqual(adapter.fetch_schema(PATH)[0], 404 if code == 404 else 502)

    def test_existing_crd_catalog_and_implicit_draft_are_preserved(self):
        schema = b'{"type":"object","properties":{}}'
        with patch.object(adapter, 'urlopen', return_value=io.BytesIO(schema)) as fetch:
            self.assertEqual(adapter.fetch_schema('/crds/traefik.io/middleware_v1alpha1.json'), (200, schema))
            self.assertEqual(fetch.call_args.args[0].full_url, 'https://api.github.com/repos/datreeio/CRDs-catalog/contents/traefik.io/middleware_v1alpha1.json?ref=main')

    def test_api_metadata_malformed_json_and_arrays_fail_closed(self):
        for body in (b'{}', b'{"content":"metadata"}', b'[]', b'{broken}'):
            with self.subTest(body=body), patch.object(adapter, 'urlopen', return_value=io.BytesIO(body)):
                self.assertEqual(adapter.fetch_schema(PATH)[0], 502)

    def test_transport_failures_remain_errors(self):
        for error in (URLError('TLS failed'), TimeoutError(), OSError()):
            with self.subTest(error=error), patch.object(adapter, 'urlopen', side_effect=error):
                self.assertEqual(adapter.fetch_schema(PATH)[0], 502)

    def test_unrelated_paths_cannot_receive_credentials(self):
        with patch.object(adapter, 'urlopen') as fetch:
            for path in ('/../secret', PATH + '?ref=evil', '/master-standalone-strict/../service-v1.json', '/unrelated.json', '//example.com/schema.json'):
                self.assertEqual(adapter.fetch_schema(path)[0], 400)
            fetch.assert_not_called()

    def test_optional_token_is_sent_only_to_fixed_api_host(self):
        with patch.dict(os.environ, {'SCHEMA_GITHUB_TOKEN': 'fixture-token'}), patch.object(adapter, 'urlopen', return_value=io.BytesIO(VALID)) as fetch:
            adapter.fetch_schema(PATH)
            self.assertEqual(fetch.call_args.args[0].get_header('Authorization'), 'Bearer fixture-token')

    def test_redirects_are_refused(self):
        request = adapter.Request('https://api.github.com/upstream')
        with self.assertRaises(HTTPError) as error:
            adapter.NoRedirects().redirect_request(request, None, 302, 'redirect', {}, 'https://example.com')
        self.assertEqual(error.exception.code, 502)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
