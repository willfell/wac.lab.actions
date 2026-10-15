"""lab-route sends heavy jobs to Lab only when Prometheus says there is room."""

import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('lab_route', Path(__file__).parents[1] / 'lab-route.py')
route = importlib.util.module_from_spec(spec)
sys.modules['lab_route'] = route
spec.loader.exec_module(route)

LAB = 'ci-standard'
HOSTED = 'ubuntu-24.04-arm'
JOBS = ['persistence', 'base', 'browser']


def config(**changes):
    values = {
        'lab': LAB,
        'overflow': HOSTED,
        'jobs': ' '.join(JOBS),
        'capacity': '3',
        'pool': '',
        'prometheus': 'http://prometheus.invalid:9090',
        'max-cpu-pressure': '0.4',
        'max-probe-p95-ms': '30',
    }
    values.update(changes)
    return route.parse_config(values)


def measured(pods=0, cpu=0.27, probe=0.012, error=''):
    return route.Measurement(pods=pods, cpu=cpu, probe_seconds=probe, error=error)


class DecideTests(unittest.TestCase):
    def test_all_free_sends_every_job_to_lab(self):
        runners, reason = route.decide(config(), measured(pods=0))
        self.assertEqual(runners, {'persistence': LAB, 'base': LAB, 'browser': LAB})
        self.assertEqual(list(runners), JOBS)
        self.assertIn('3 of 3', reason)

    def test_partly_free_fills_lab_in_priority_order(self):
        runners, reason = route.decide(config(), measured(pods=1))
        self.assertEqual(runners, {'persistence': LAB, 'base': LAB, 'browser': HOSTED})
        self.assertIn('2 of 3', reason)

    def test_none_free_sends_every_job_to_overflow(self):
        for pods in [3, 5]:
            with self.subTest(pods=pods):
                runners, reason = route.decide(config(), measured(pods=pods))
                self.assertEqual(set(runners.values()), {HOSTED})
                self.assertIn('0 of 3', reason)

    def test_own_pod_in_the_pool_is_not_a_used_slot(self):
        runners, _ = route.decide(config(), measured(pods=3), own_pod_in_pool=True)
        self.assertEqual(runners, {'persistence': LAB, 'base': HOSTED, 'browser': HOSTED})

    def test_cpu_pressure_above_the_limit_overflows(self):
        runners, reason = route.decide(config(), measured(cpu=0.41))
        self.assertEqual(set(runners.values()), {HOSTED})
        self.assertIn('cpu pressure 0.41 above 0.4', reason)
        runners, _ = route.decide(config(), measured(cpu=0.4))
        self.assertEqual(set(runners.values()), {LAB})

    def test_probe_p95_above_the_limit_overflows(self):
        runners, reason = route.decide(config(), measured(probe=0.031))
        self.assertEqual(set(runners.values()), {HOSTED})
        self.assertIn('probe p95 31ms above 30ms', reason)
        runners, _ = route.decide(config(), measured(probe=0.030))
        self.assertEqual(set(runners.values()), {LAB})

    def test_failed_query_overflows_and_says_why(self):
        runners, reason = route.decide(config(), measured(pods=None, cpu=None, probe=None, error='pods: connection refused'))
        self.assertEqual(set(runners.values()), {HOSTED})
        self.assertEqual(reason, 'prometheus query failed (pods: connection refused)')

    def test_lab_equal_to_overflow_needs_no_measurement(self):
        runners, reason = route.decide(config(overflow=LAB), None)
        self.assertEqual(set(runners.values()), {LAB})
        self.assertIn('same label', reason)

    def test_hosted_lab_label_equal_to_overflow_is_not_an_error(self):
        runners, reason = route.decide(config(lab=HOSTED), None)
        self.assertEqual(set(runners.values()), {HOSTED})
        self.assertIn('same label', reason)

    def test_lab_label_that_cannot_name_a_pool_overflows(self):
        cfg = config(lab='ubuntu-24.04-arm', overflow='ci-standard')
        self.assertTrue(cfg.pool_error)
        runners, reason = route.decide(cfg, None)
        self.assertEqual(set(runners.values()), {'ci-standard'})
        self.assertIn("lab label 'ubuntu-24.04-arm' is not a Lab pool", reason)
        self.assertFalse(route.own_pod_in_pool(cfg, 'ubuntu-24.04-arm-x-runner-y'))

    def test_capacity_zero_overflows(self):
        runners, _ = route.decide(config(capacity='0'), measured(pods=0))
        self.assertEqual(set(runners.values()), {HOSTED})


class ConfigTests(unittest.TestCase):
    def test_pool_defaults_to_the_lab_label(self):
        self.assertEqual(config().pool, LAB)
        self.assertEqual(config(pool='ci-large').pool, 'ci-large')
        self.assertEqual(route.pod_query(config()), 'count(kube_pod_info{namespace="arc-runners",pod=~"ci-standard-.*-runner-.*"}) or (0 * count(kube_pod_info))')
        self.assertNotIn('vector(', route.pod_query(config()))

    def test_malformed_input_is_rejected(self):
        cases = [
            {'lab': ''},
            {'overflow': ' '},
            {'jobs': ''},
            {'jobs': 'a a'},
            {'jobs': 'a 9b'},
            {'jobs': 'a.b'},
            {'capacity': '-1'},
            {'capacity': 'three'},
            {'max-cpu-pressure': 'high'},
            {'max-cpu-pressure': 'nan'},
            {'max-probe-p95-ms': ''},
            {'pool': 'ci.standard'},
            {'pool': 'ubuntu-24.04-arm', 'lab': 'ubuntu-24.04-arm', 'overflow': 'ubuntu-24.04-arm'},
            {'pool': 'ci"} or vector(9) #'},
            {'prometheus': 'file:///etc/passwd'},
        ]
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    config(**change)

    def test_job_keys_accept_identifiers_with_dashes_and_newlines(self):
        self.assertEqual(config(jobs='persistence\n  base browser-arm').jobs, ['persistence', 'base', 'browser-arm'])


class Prometheus(BaseHTTPRequestHandler):
    answers = {}

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)['query'][0]
        status, body = next((answer for key, answer in self.answers.items() if key in query), (404, '{}'))
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def vector(value):
    return 200, json.dumps({'status': 'success', 'data': {'resultType': 'vector', 'result': [{'metric': {}, 'value': [1, value]}]}})


class ActionTests(unittest.TestCase):
    def serve(self, answers):
        handler = type('Answers', (Prometheus,), {'answers': answers})
        server = HTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def run_action(self, prometheus, **changes):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'output'
            summary = Path(tmp) / 'summary'
            env = {
                'LAB': LAB,
                'OVERFLOW': HOSTED,
                'JOBS': ' '.join(JOBS),
                'CAPACITY': '3',
                'POOL': '',
                'PROMETHEUS': prometheus,
                'MAX_CPU_PRESSURE': '0.4',
                'MAX_PROBE_P95_MS': '30',
                'RUNNER_NAME': 'ci-light-abcde-runner-xyz12',
                'GITHUB_OUTPUT': str(output),
                'GITHUB_STEP_SUMMARY': str(summary),
            }
            env.update(changes)
            out = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
                code = route.main()
            outputs = dict(line.split('=', 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return code, outputs, summary.read_text() if summary.exists() else '', out.getvalue()

    def test_healthy_lab_with_one_busy_slot(self):
        url = self.serve({'kube_pod_info': vector('1'), 'node_pressure': vector('0.27'), 'probe_duration': vector('0.012')})
        code, outputs, summary, _ = self.run_action(url)
        self.assertEqual(code, 0)
        self.assertEqual(outputs['runners'], '{"persistence":"ci-standard","base":"ci-standard","browser":"ubuntu-24.04-arm"}')
        self.assertEqual(outputs['reason'], '2 of 3 Lab slots free')
        self.assertEqual(len(summary.splitlines()), 1)
        for text in ['pods=1', 'cpu=0.27', 'probe_p95=12ms', 'persistence=ci-standard', 'browser=ubuntu-24.04-arm']:
            self.assertIn(text, summary)

    def test_prometheus_down_overflows_and_succeeds(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        code, outputs, summary, _ = self.run_action(f'http://127.0.0.1:{port}')
        self.assertEqual(code, 0)
        self.assertEqual(set(json.loads(outputs['runners']).values()), {HOSTED})
        self.assertTrue(outputs['reason'].startswith('prometheus query failed (pods:'))
        self.assertIn('pods=?', summary)

    def test_malformed_prometheus_answers_overflow(self):
        bad = [
            {'kube_pod_info': (500, '{}')},
            {'kube_pod_info': (200, 'not json')},
            {'kube_pod_info': (200, '{"status":"error","error":"bad query"}')},
            {'kube_pod_info': (200, '{"status":"success","data":{"resultType":"vector","result":[]}}')},
            {'kube_pod_info': vector('1'), 'node_pressure': (200, '{"status":"success","data":{"resultType":"vector","result":[]}}')},
            {'kube_pod_info': vector('1'), 'node_pressure': vector('NaN')},
        ]
        for answers in bad:
            with self.subTest(answers=answers):
                answers.setdefault('node_pressure', vector('0.27'))
                answers.setdefault('probe_duration', vector('0.012'))
                code, outputs, _, _ = self.run_action(self.serve(answers))
                self.assertEqual(code, 0)
                self.assertEqual(set(json.loads(outputs['runners']).values()), {HOSTED})
                self.assertTrue(outputs['reason'].startswith('prometheus query failed'))
                self.assertNotIn('\n', outputs['reason'])

    def test_hosted_lab_label_succeeds_without_measuring(self):
        for lab, overflow in [(HOSTED, HOSTED), (HOSTED, LAB)]:
            with self.subTest(lab=lab, overflow=overflow):
                code, outputs, summary, _ = self.run_action('http://127.0.0.1:9', LAB=lab, OVERFLOW=overflow)
                self.assertEqual(code, 0)
                self.assertEqual(set(json.loads(outputs['runners']).values()), {overflow})
                self.assertIn('not measured', summary)

    def test_malformed_input_fails_the_step(self):
        code, outputs, _, out = self.run_action('http://127.0.0.1:9', JOBS='a a')
        self.assertEqual(code, 1)
        self.assertEqual(outputs, {})
        self.assertIn('::error::', out)


if __name__ == '__main__':
    unittest.main()
