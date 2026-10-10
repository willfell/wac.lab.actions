import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('runner_policy', Path(__file__).parents[1] / 'runner-policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class RunnerPolicyTests(unittest.TestCase):
    def scan(self, source, repository='willfell/example', transitional=(), hosts=()):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ci.yml'
            path.write_text(source)
            return policy.fleet_scan(path, repository, transitional, hosts)

    def test_literals_arrays_and_expressions(self):
        for value in ['ci-build', "${{ vars.CI_HOSTED_LABEL || 'ubuntu-24.04-arm' }}", "${{ github.repository_owner == 'wacwini' && 'ci-standard' || 'finance' }}", '[self-hosted, rpo-mac-mini]']:
            with self.subTest(value=value):
                self.assertEqual(self.scan('jobs:\n  a:\n    runs-on: ' + value, transitional=('finance',), hosts=('self-hosted', 'rpo-mac-mini')), [])

    def test_rejects_unlisted_and_other_hosted_labels(self):
        for value in ['ubuntu-latest', 'ubuntu-22.04', 'macos-14', 'windows-latest', 'unknown', "${{ inputs.runner || 'ubuntu-latest' }}", "${{ github.repository_owner == 'wacwini' && 'ci-build' || 'ubuntu-latest' }}"]:
            with self.subTest(value=value):
                self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value))

    def test_reusable_runner_and_visible_matrix(self):
        self.assertTrue(self.scan('jobs:\n  a:\n    uses: owner/repo/workflow.yml@v1\n    with:\n      runner: ubuntu-latest'))
        self.assertTrue(self.scan("jobs:\n  a:\n    strategy:\n      matrix:\n        os: [ci-build, macos-latest]\n    runs-on: ${{ matrix.os }}"))
        self.assertTrue(self.scan('jobs:\n  a:\n    uses: willfell/wac.lab.actions/.github/workflows/techdocs.yml@v1.18.0'))

    def test_transfer_disables_transitional_labels(self):
        source = 'jobs:\n  a:\n    runs-on: finance'
        self.assertEqual(self.scan(source, transitional=('finance',)), [])
        self.assertTrue(self.scan(source, repository='wacwini/example', transitional=('finance',)))
        conditional = "jobs:\n  a:\n    runs-on: ${{ github.repository_owner == 'wacwini' && 'ci-standard' || 'finance' }}"
        self.assertEqual(self.scan(conditional, repository='wacwini/example', transitional=('finance',)), [])
        self.assertTrue(self.scan(conditional, repository='wacwini/example'))

    def test_action_library_is_the_only_ubuntu_latest_exception(self):
        source = 'jobs:\n  a:\n    runs-on: ubuntu-latest'
        self.assertEqual(self.scan(source, repository='willfell/wac.lab.actions', hosts=('ubuntu-latest',)), [])
        with self.assertRaises(ValueError):
            self.scan(source, hosts=('ubuntu-latest',))

    def test_variable_shape_and_unknown_dynamic_values_fail_closed(self):
        for value in ["${{ vars.CI_HOSTED_LABEL || 'ci-light' }}", '${{ vars.CI_HOSTED_LABEL }}', '${{ secrets.RUNNER }}', '${{ matrix.os }}']:
            self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value))

    def test_reusable_definition_accepts_only_its_runner_input(self):
        self.assertEqual(self.scan('on:\n  workflow_call:\njobs:\n  a:\n    runs-on: ${{ inputs.runner }}'), [])
        self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ${{ inputs.runner }}'))

    def test_legacy_retains_old_expression_and_with_runner_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ci.yml'
            path.write_text("jobs:\n  a:\n    runs-on: ${{ inputs.runner || 'ubuntu-latest' }}\n  b:\n    uses: example/repo/.github/workflows/ci.yml@v1\n    with:\n      runner: ubuntu-latest\n")
            self.assertEqual(policy.legacy_scan(path), [])
            path.write_text('jobs: {a: {runs-on: ubuntu-latest}}')
            self.assertEqual(policy.legacy_scan(path), [(1, 'ubuntu-latest')])


if __name__ == '__main__':
    unittest.main()
