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

    def test_organization_accepts_the_large_size(self):
        for value in ['${{ vars.CI_RUNNER_LARGE }}', '${{ inputs.runner || vars.CI_RUNNER_LARGE }}']:
            with self.subTest(value=value):
                self.assertEqual(self.scan('jobs:\n  a:\n    runs-on: ' + value, repository=self.ORG), [])
                self.assertEqual(self.scan('jobs:\n  a:\n    uses: willfell/wac.lab.actions/.github/workflows/techdocs.yml@v1.20.0\n    with:\n      runner: ' + value, repository=self.ORG), [])
        mixed = 'jobs:\n  a:\n    strategy:\n      matrix:\n        size: ["${{ vars.CI_RUNNER_LARGE }}", "${{ vars.CI_RUNNER_STANDARD }}"]\n    runs-on: ${{ matrix.size }}'
        self.assertEqual(self.scan(mixed, repository=self.ORG), [])

    def test_large_literal_and_misspelling_fail(self):
        for value in ['vars.CI_RUNNER_LARGE', '${{ vars.CI_RUNNER_LARG }}', 'vars.CI_RUNNER_LARG']:
            with self.subTest(value=value):
                self.assertTrue(self.scan('jobs:\n  a:\n    runs-on: ' + value, repository=self.ORG))

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

    ROUTER_STEP = '      - id: route\n        uses: {uses}\n        with:\n          lab: {lab}\n          overflow: {overflow}\n          jobs: persistence base\n'

    def routed(self, runs_on='${{ fromJSON(needs.route.outputs.runners).persistence }}', needs='[route]', router='route', uses='willfell/wac.lab.actions/lab-route@v1.22.0', lab='${{ vars.CI_RUNNER_STANDARD }}', overflow='${{ vars.CI_RUNNER_LARGE }}', output='${{ steps.route.outputs.runners }}'):
        return (
            'jobs:\n'
            f'  {router}:\n'
            '    runs-on: ${{ vars.CI_RUNNER_SMALL }}\n'
            '    outputs:\n'
            f'      runners: {output}\n'
            '    steps:\n'
            + self.ROUTER_STEP.format(uses=uses, lab=lab, overflow=overflow)
            + '  persistence:\n'
            f'    needs: {needs}\n'
            f'    runs-on: {runs_on}\n'
        )

    def assertRejected(self, source, reason):
        findings = self.scan(source, repository=self.ORG)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn(reason, findings[0][2])

    def test_router_wiring_is_accepted(self):
        for runs_on in ['${{ fromJSON(needs.route.outputs.runners).persistence }}', "${{ fromJSON(needs.route.outputs.runners)['base'] }}", '${{fromJson(needs.route.outputs.runners).base}}']:
            with self.subTest(runs_on=runs_on):
                self.assertEqual(self.scan(self.routed(runs_on=runs_on), repository=self.ORG), [])
        self.assertEqual(self.scan(self.routed(needs='route'), repository=self.ORG), [])
        self.assertEqual(self.scan(self.routed(lab='${{ inputs.runner || vars.CI_RUNNER_STANDARD }}'), repository=self.ORG), [])
        self.assertEqual(self.scan(self.routed(uses='./lab-route'), repository='willfell/wac.lab.actions'), [])

    def test_router_missing_from_needs_fails(self):
        for needs in ['[]', '[other]', 'other']:
            with self.subTest(needs=needs):
                self.assertRejected(self.routed(needs=needs), 'must be in needs')
        self.assertRejected(self.routed().replace('    needs: [route]\n', ''), 'must be in needs')

    def test_router_with_literal_lab_or_overflow_fails(self):
        for change in [{'lab': 'ci-standard'}, {'overflow': 'ubuntu-24.04-arm'}, {'overflow': "${{ inputs.runner || 'ubuntu-latest' }}"}, {'lab': '${{ matrix.size }}'}, {'lab': "''"}]:
            with self.subTest(change=change):
                self.assertRejected(self.routed(**change), 'must name a size variable')
        self.assertRejected(self.routed().replace('          overflow: ${{ vars.CI_RUNNER_LARGE }}\n', ''), 'with.overflow must name a size variable')

    def test_router_output_must_come_from_the_router_step(self):
        for output in ['${{ steps.other.outputs.runners }}', '{"persistence": "ubuntu-latest"}', '${{ steps.route.outputs.reason }}']:
            with self.subTest(output=output):
                self.assertRejected(self.routed(output=output), 'from a willfell/wac.lab.actions/lab-route step')

    def test_router_key_must_be_one_of_its_jobs(self):
        self.assertRejected(self.routed(runs_on='${{ fromJSON(needs.route.outputs.runners).browser }}'), "router key 'browser'")

    def test_other_needs_and_fromjson_forms_fail(self):
        for runs_on in [
            '${{ fromJSON(needs.route.outputs.labels).persistence }}',
            '${{ fromJSON(needs.route.outputs.runners) }}',
            '${{ needs.route.outputs.runners }}',
            '${{ needs.route.outputs.persistence }}',
            "${{ fromJSON('[\"ci-standard\"]') }}",
            '${{ fromJSON(vars.RUNNERS).persistence }}',
            '${{ fromJSON(needs.route.outputs.runners).persistence || vars.CI_RUNNER_SMALL }}',
        ]:
            with self.subTest(runs_on=runs_on):
                self.assertRejected(self.routed(runs_on=runs_on), 'unsupported dynamic runner expression')

    def test_fromjson_of_another_jobs_output_fails(self):
        source = self.routed(runs_on='${{ fromJSON(needs.build.outputs.runners).persistence }}', needs='[route, build]') + '  build:\n    runs-on: ${{ vars.CI_RUNNER_SMALL }}\n    outputs:\n      runners: ${{ steps.pick.outputs.runners }}\n    steps:\n      - id: pick\n        run: echo runners={} >> "$GITHUB_OUTPUT"\n'
        self.assertRejected(source, "router job 'build' must export outputs.runners from a willfell/wac.lab.actions/lab-route step")
        self.assertRejected(self.routed(runs_on='${{ fromJSON(needs.ghost.outputs.runners).persistence }}', needs='[ghost]'), "router job 'ghost' is not a job")

    def test_non_router_job_named_route_fails(self):
        source = (
            'jobs:\n'
            '  route:\n'
            '    runs-on: ${{ vars.CI_RUNNER_SMALL }}\n'
            '    outputs:\n'
            '      runners: ${{ steps.route.outputs.runners }}\n'
            '    steps:\n'
            '      - id: route\n'
            '        run: echo \'runners={"persistence":"ubuntu-latest"}\' >> "$GITHUB_OUTPUT"\n'
            '  persistence:\n'
            '    needs: [route]\n'
            '    runs-on: ${{ fromJSON(needs.route.outputs.runners).persistence }}\n'
        )
        self.assertRejected(source, 'from a willfell/wac.lab.actions/lab-route step')

    def test_router_step_from_another_action_fails(self):
        for uses in ['someone/wac.lab.actions/lab-route@v1.22.0', 'willfell/wac.lab.actions/lab-tools@v1.22.0', 'willfell/wac.lab.actions/lab-route', './lab-route', 'willfell/wac.lab.actions/lab-route-fork@v1.22.0']:
            with self.subTest(uses=uses):
                self.assertRejected(self.routed(uses=uses), 'from a willfell/wac.lab.actions/lab-route step')

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
