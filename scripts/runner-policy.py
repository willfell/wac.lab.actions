"""Validate fleet routing, retaining the pre-v1.18 legacy scanner unchanged."""

import argparse
import os
from pathlib import Path
import re
import sys

import yaml

HOSTED = re.compile(r"^(ubuntu|macos|windows)-", re.I)
HOSTED_ARM = 'ubuntu-24.04-arm'
SIZE = re.compile(r'^vars\.CI_RUNNER_(?:SMALL|STANDARD|LARGE)$')


class Size(str):
    pass


INPUT_OR_SIZE = re.compile(r'^inputs\.runner\s*\|\|\s*(vars\.CI_RUNNER_(?:SMALL|STANDARD|LARGE))$')


def legacy_scan(path):
    lines = path.read_text().splitlines()
    matrix = {}
    for line in lines:
        hit = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_-]*):\s*\[(.+)\]\s*$", line)
        if hit:
            matrix.setdefault(hit[1], []).extend(v.strip().strip("'\"") for v in hit[2].split(',') if v.strip())
    found = []
    for i, line in enumerate(lines):
        for hit in re.finditer(r"runs-on:\s*(.*)$", line):
            value = hit[1].split('#')[0].strip()
            labels = []
            if not value:
                # block sequence on the following lines
                for nxt in lines[i + 1:]:
                    if not nxt.strip().startswith('-'):
                        break
                    labels.append(nxt.strip()[1:].strip().strip("'\""))
            elif value.startswith('['):
                # inline sequence; ignore anything after the closing bracket,
                # which is flow-mapping punctuation rather than a label
                end = value.find(']')
                labels = [v.strip().strip("'\"") for v in value[1:end if end != -1 else len(value)].split(',') if v.strip()]
            else:
                if not value.startswith('$'):
                    # a plain scalar picks up flow-mapping punctuation when the
                    # job is written inline, e.g. `jobs: {a: {runs-on: x}}`
                    value = value.rstrip('}').rstrip(',').strip()
                expr = re.search(r"\$\{\{(.+?)\}\}", value)
                if expr:
                    # Only matrix references resolve to labels we can see here.
                    # inputs/vars/secrets are supplied by the caller at run time
                    # and carry no literal, which is what lets a break-glass
                    # `inputs.runner || 'pool'` pass.
                    for ref in re.findall(r'matrix\.([A-Za-z0-9_-]+)', expr[1]):
                        labels.extend(matrix.get(ref, []))
                else:
                    labels = [value.strip("'\"")]
            found.extend((i + 1, label) for label in labels if HOSTED.match(label))
    return found


def labels_for(value, matrix, reusable=False):
    """Every label or size a runner value can resolve to; sizes are returned as `vars.*`."""
    if isinstance(value, list):
        if not value:
            raise ValueError('runner must resolve to a nonempty label')
        return [label for item in value for label in labels_for(item, matrix, reusable)]
    if isinstance(value, dict):
        if not value.get('labels'):
            raise ValueError('runner must resolve to a nonempty label')
        return labels_for(value['labels'], matrix, reusable)
    if not isinstance(value, str) or not value:
        raise ValueError('runner must resolve to a nonempty label')
    if '${{' not in value:
        return [value]
    expr = re.fullmatch(r'\$\{\{\s*(.*?)\s*\}\}', value)
    if not expr:
        raise ValueError('unsupported runner interpolation')
    body = expr[1]
    if SIZE.fullmatch(body):
        return [Size(body)]
    size = INPUT_OR_SIZE.fullmatch(body)
    if size:
        return [Size(size[1])]
    ref = re.fullmatch(r'matrix\.([A-Za-z0-9_-]+)', body)
    if ref:
        values = matrix.get(ref[1], [])
        values += [row[ref[1]] for row in matrix.get('include', []) if ref[1] in row]
        if not values:
            raise ValueError('runner matrix must have visible values')
        return labels_for(values, {}, reusable)
    if body == 'inputs.runner' and reusable:
        return []
    raise ValueError('unsupported dynamic runner expression: name a size, vars.CI_RUNNER_SMALL, vars.CI_RUNNER_STANDARD or vars.CI_RUNNER_LARGE')


KEY = r'[A-Za-z_][A-Za-z0-9_-]*'
ROUTED = re.compile(
    r'\$\{\{\s*(?i:fromjson)\(\s*needs\.(' + KEY + r')\.outputs\.runners\s*\)'
    r"(?:\.(" + KEY + r")|\[\s*'(" + KEY + r")'\s*\])\s*\}\}"
)
ROUTER_ACTION = re.compile(r'^willfell/wac\.lab\.actions/lab-route@[A-Za-z0-9._/-]+$')
LOADER_ENV = re.compile(r'^(PATH|BASH_ENV|ENV|LD_.*|PYTHON.*)$', re.I)


def routed_runner(value, name, job, jobs, repository, reusable, workflow_env=None):
    """Check `fromJSON(needs.<J>.outputs.runners).<key>`; None when value is not that form.

    The labels come from a lab-route step whose `lab` and `overflow` are sizes,
    so the job still runs on a size; anything else wired the same way is not
    admitted. The router job must be that one step with nothing around it: an
    earlier step, a container or an environment can change which python3 or
    which files the action runs, and with them the labels it prints.
    """
    hit = ROUTED.fullmatch(value) if isinstance(value, str) else None
    if not hit:
        return None
    router_name, key = hit[1], hit[2] or hit[3]
    needs = job.get('needs') or []
    if router_name not in ([needs] if isinstance(needs, str) else needs):
        return f'router job {router_name!r} must be in needs'
    router = jobs.get(router_name)
    if router_name == name or not isinstance(router, dict):
        return f'router job {router_name!r} is not a job in this workflow'
    output = re.fullmatch(r'\$\{\{\s*steps\.(' + KEY + r')\.outputs\.runners\s*\}\}', str((router.get('outputs') or {}).get('runners', '')))
    step = next((s for s in router.get('steps') or [] if isinstance(s, dict) and output and s.get('id') == output[1]), None)
    uses = (step or {}).get('uses', '')
    if not (ROUTER_ACTION.match(uses) or (uses == './lab-route' and repository == 'willfell/wac.lab.actions')):
        return f'router job {router_name!r} must export outputs.runners from a willfell/wac.lab.actions/lab-route step'
    if len(router.get('steps') or []) != 1:
        return f'router job {router_name!r} must hold only the lab-route step'
    if any(field in router for field in ('container', 'services', 'env')) or 'env' in step:
        return f'router job {router_name!r} must not set container, services or env'
    loader = sorted(key for key in (workflow_env or {}) if LOADER_ENV.match(str(key)))
    if loader:
        return f'workflow env {loader[0]} would change how lab-route runs'
    inputs = step.get('with') or {}
    for field in ('lab', 'overflow'):
        try:
            labels = labels_for(inputs.get(field), {}, reusable)
        except ValueError:
            labels = []
        if not labels or not all(isinstance(label, Size) for label in labels):
            return f'lab-route with.{field} must name a size variable'
    if key not in str(inputs.get('jobs', '')).split():
        return f'router key {key!r} is not in the lab-route jobs input'
    return ''


def fleet_scan(path, repository, transitional=(), hosts=()):
    if 'ubuntu-latest' in hosts and repository != 'willfell/wac.lab.actions':
        raise ValueError('ubuntu-latest exception belongs only to willfell/wac.lab.actions')
    invalid_hosts = [label for label in hosts if HOSTED.match(label) and label != 'ubuntu-latest']
    if invalid_hosts:
        raise ValueError('host exceptions cannot admit other GitHub-hosted labels')
    organization = repository.split('/')[0] == 'wacwini'
    allowed = set(hosts) | ({HOSTED_ARM} | set(transitional) if not organization else set())
    doc = yaml.safe_load(path.read_text()) or {}
    triggers = doc.get('on', doc.get(True, {})) or {}
    reusable = isinstance(triggers, dict) and 'workflow_call' in triggers
    findings = []
    jobs = doc.get('jobs') or {}
    for name, job in jobs.items():
        matrix = (job.get('strategy') or {}).get('matrix') or {}
        if not isinstance(matrix, dict):
            matrix = {}
        values = []
        if 'runs-on' in job:
            routed = routed_runner(job['runs-on'], name, job, jobs, repository, reusable, doc.get('env') if isinstance(doc.get('env'), dict) else {})
            if routed:
                findings.append((name, 'runs-on', routed))
            elif routed is None:
                values.append(('runs-on', job['runs-on']))
        if 'uses' in job and 'runner' in (job.get('with') or {}):
            values.append(('with.runner', job['with']['runner']))
        for key, value in values:
            try:
                labels = labels_for(value, matrix, reusable)
            except ValueError as exc:
                findings.append((name, key, str(exc)))
                continue
            for label in labels:
                if isinstance(label, Size) or label in allowed:
                    continue
                if organization:
                    findings.append((name, key, f'literal runner label {label!r}; organization jobs name a size variable'))
                else:
                    findings.append((name, key, f'runner label {label!r} is not admitted'))
    return findings


def split_labels(value):
    return tuple(part for part in re.split(r'[\s,]+', value) if part)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--policy', choices=['legacy', 'fleet'], default='legacy')
    parser.add_argument('--runner', default='ubuntu-latest')
    parser.add_argument('--transitional-labels', default='')
    parser.add_argument('--allowed-host-labels', default='')
    parser.add_argument('--directory', type=Path, default=Path('.github/workflows'))
    args = parser.parse_args()
    if args.policy == 'legacy' and (not args.runner or args.runner == 'ubuntu-latest'):
        print('runner policy: legacy scan is unarmed')
        return 0
    paths = sorted([*args.directory.glob('*.yml'), *args.directory.glob('*.yaml')])
    failed = False
    for path in paths:
        try:
            findings = legacy_scan(path) if args.policy == 'legacy' else fleet_scan(path, os.environ.get('GITHUB_REPOSITORY', ''), split_labels(args.transitional_labels), split_labels(args.allowed_host_labels))
        except (ValueError, yaml.YAMLError) as exc:
            print(f'::error file={path}::runner policy: {exc}')
            failed = True
            continue
        for finding in findings:
            print(f'::error file={path}::runner policy: {finding}')
            failed = True
    if not paths:
        print('::error::runner policy found no workflows')
        failed = True
    if not failed:
        print(f'runner policy: {args.policy} accepted {len(paths)} workflows')
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
