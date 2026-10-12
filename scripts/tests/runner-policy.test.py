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

    ORG = 'wacwini/example'
    SMALL = '${{ vars.CI_RUNNER_SMALL }}'
    STANDARD = '${{ vars.CI_RUNNER_STANDARD }}'

    def test_organization_jobs_name_a_size(self):
        for value in [self.SMALL, self.STANDARD, '${{ inputs.runner || vars.CI_RUNNER_STANDARD }}']:
            with self.subTest(value=value):
                self.assertEqual(self.scan('jobs:\n  a:\n    runs-on: ' + value, repository=self.ORG), [])
                self.assertEqual(self.scan('jobs:\n  a:\n    uses: willfell/wac.lab.actions/.github/workflows/techdocs.yml@v1.20.0\n    with:\n      runner: ' + value, repository=self.ORG), [])

    def test_organization_literals_and_old_expressions_fail(self):
        for value in ['ci-build', 'ci-standard', 'ubuntu-24.04-arm', 'ubuntu-latest', "${{ vars.CI_HOSTED_LABEL || 'ubuntu-24.04-arm' }}", "${{ github.repository_owner == 'wacwini' && 'ci-standard' || 'finance' }}", "${{ inputs.runner || 'ci-light' }}", "${{ vars.CI_RUNNER_SMALL || 'ci-light' }}", '${{ vars.CI_RUNNER_SMAL }}', '${{ secrets.RUNNER }}']:
            with self.subTest(value=value):
                self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value, repository=self.ORG))

    def test_bare_vars_text_is_a_literal_label(self):
        for value in ['vars.CI_RUNNER_SMAL', 'vars.CI_RUNNER_SMALL', 'vars.CI_RUNNER_STANDARD', '[vars.CI_RUNNER_SMALL]']:
            with self.subTest(value=value):
                self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value, repository=self.ORG))
        for label in ['vars.CI_RUNNER_SMAL', 'vars.CI_RUNNER_SMALL']:
            with self.subTest(matrix=label):
                source = 'jobs:\n  a:\n    strategy:\n      matrix:\n        size: ["${{ vars.CI_RUNNER_SMALL }}", ' + label + ']\n    runs-on: ${{ matrix.size }}'
                self.assertTrue(self.scan(source, repository=self.ORG))

    def test_empty_runner_values_fail(self):
        for value in ['{group: big}', '{labels: []}', '[]', '{}']:
            for repository in [self.ORG, 'willfell/example']:
                with self.subTest(value=value, repository=repository):
                    self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value, repository=repository))

    def test_host_labels_stay_literal_inside_the_organization(self):
        source = 'jobs:\n  a:\n    runs-on: rpo-mac-mini\n  b:\n    runs-on: [self-hosted, rpo-mac-mini]\n  c:\n    runs-on: ' + self.STANDARD
        self.assertEqual(self.scan(source, repository=self.ORG, hosts=('self-hosted', 'rpo-mac-mini')), [])
        self.assertTrue(self.scan(source, repository=self.ORG))

    def test_shared_workflow_without_a_runner_is_fine_now(self):
        self.assertEqual(self.scan('jobs:\n  a:\n    uses: willfell/wac.lab.actions/.github/workflows/techdocs.yml@v1.20.0', repository=self.ORG), [])

    def test_matrix_values_are_checked_one_by_one(self):
        good = 'jobs:\n  a:\n    strategy:\n      matrix:\n        size: ["${{ vars.CI_RUNNER_SMALL }}", "${{ vars.CI_RUNNER_STANDARD }}"]\n    runs-on: ${{ matrix.size }}'
        self.assertEqual(self.scan(good, repository=self.ORG), [])
        bad = 'jobs:\n  a:\n    strategy:\n      matrix:\n        size: ["${{ vars.CI_RUNNER_SMALL }}", ci-build]\n    runs-on: ${{ matrix.size }}'
        self.assertTrue(self.scan(bad, repository=self.ORG))

    def test_reusable_definition_accepts_its_input_and_the_small_default(self):
        self.assertEqual(self.scan('on:\n  workflow_call:\njobs:\n  a:\n    runs-on: ${{ inputs.runner }}'), [])
        self.assertEqual(self.scan('on:\n  workflow_call:\njobs:\n  a:\n    runs-on: ${{ inputs.runner || vars.CI_RUNNER_SMALL }}'), [])
        self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ${{ inputs.runner }}'))

    def test_personal_repositories_keep_hosted_arm_and_declared_hosts(self):
        for value in ['ubuntu-24.04-arm', self.SMALL, '[self-hosted, rpo-mac-mini]']:
            with self.subTest(value=value):
                self.assertEqual(self.scan('jobs:\n  a:\n    runs-on: ' + value, hosts=('self-hosted', 'rpo-mac-mini')), [])
        for value in ['ubuntu-latest', 'ubuntu-22.04', 'macos-14', 'ci-build']:
            with self.subTest(value=value):
                self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value))

    def test_transitional_labels_only_outside_the_organization(self):
        source = 'jobs:\n  a:\n    runs-on: finance'
        self.assertEqual(self.scan(source, transitional=('finance',)), [])
        self.assertTrue(self.scan(source, repository=self.ORG, transitional=('finance',)))

    def test_action_library_is_the_only_ubuntu_latest_exception(self):
        source = 'jobs:\n  a:\n    runs-on: ubuntu-latest'
        self.assertEqual(self.scan(source, repository='willfell/wac.lab.actions', hosts=('ubuntu-latest',)), [])
        with self.assertRaises(ValueError):
            self.scan(source, hosts=('ubuntu-latest',))

    def test_legacy_retains_old_expression_and_with_runner_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ci.yml'
            path.write_text("jobs:\n  a:\n    runs-on: ${{ inputs.runner || 'ubuntu-latest' }}\n  b:\n    uses: example/repo/.github/workflows/ci.yml@v1\n    with:\n      runner: ubuntu-latest\n")
            self.assertEqual(policy.legacy_scan(path), [])
            path.write_text('jobs: {a: {runs-on: ubuntu-latest}}')
            self.assertEqual(policy.legacy_scan(path), [(1, 'ubuntu-latest')])

    def test_legacy_scan_stays_unarmed_for_an_empty_runner(self):
        import contextlib
        import io
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'ci.yml').write_text('jobs: {a: {runs-on: ubuntu-latest}}')
            argv = ['runner-policy.py', '--policy', 'legacy', '--runner', '', '--directory', tmp]
            out = io.StringIO()
            with mock.patch('sys.argv', argv), contextlib.redirect_stdout(out):
                code = policy.main()
            self.assertEqual(code, 0)
            self.assertIn('legacy scan is unarmed', out.getvalue())


if __name__ == '__main__':
    unittest.main()
