#!/usr/bin/env python3
"""Pick a runner label per job: Lab while it has room and is healthy, overflow otherwise.

Prometheus is advisory. Any failure to measure sends every job to the overflow
label and the step still succeeds; only malformed caller input fails it.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

QUERY_TIMEOUT = 5
JOB_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*$')
POOL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]*$')
CPU_QUERY = 'max(rate(node_pressure_cpu_waiting_seconds_total[1m]))'
PROBE_QUERY = 'quantile(0.95, probe_duration_seconds{target=~".*-local"})'


@dataclass
class Config:
    lab: str
    overflow: str
    jobs: list[str]
    capacity: int
    pool: str
    prometheus: str
    max_cpu: float
    max_probe_ms: float


@dataclass
class Measurement:
    pods: float | None = None
    cpu: float | None = None
    probe_seconds: float | None = None
    error: str = ''


def _number(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f'{name} must be a number, got {raw!r}') from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be a finite number of at least 0, got {raw!r}')
    return value


def parse_config(values: dict[str, str]) -> Config:
    lab = values['lab'].strip()
    overflow = values['overflow'].strip()
    if not lab or not overflow:
        raise ValueError('lab and overflow must both name a runner label')
    jobs = values['jobs'].split()
    if not jobs:
        raise ValueError('jobs must name at least one job key')
    for key in jobs:
        if not JOB_KEY.match(key):
            raise ValueError(f'job key {key!r} must start with a letter or _ and hold only letters, digits, _ and -')
    if len(set(jobs)) != len(jobs):
        raise ValueError('jobs must not repeat a key')
    if not re.fullmatch(r'\d+', values['capacity'].strip()):
        raise ValueError(f"capacity must be a whole number, got {values['capacity']!r}")
    pool = values['pool'].strip() or lab
    if not POOL.match(pool):
        raise ValueError(f'pool {pool!r} must hold only letters, digits and -')
    prometheus = values['prometheus'].strip().rstrip('/')
    if urllib.parse.urlparse(prometheus).scheme not in ('http', 'https'):
        raise ValueError(f'prometheus must be an http or https URL, got {prometheus!r}')
    return Config(
        lab=lab,
        overflow=overflow,
        jobs=jobs,
        capacity=int(values['capacity']),
        pool=pool,
        prometheus=prometheus,
        max_cpu=_number('max-cpu-pressure', values['max-cpu-pressure']),
        max_probe_ms=_number('max-probe-p95-ms', values['max-probe-p95-ms']),
    )


def pod_query(config: Config) -> str:
    # `count` of nothing is an empty vector, not 0, so an idle pool needs the
    # explicit fallback to read as zero pods rather than as a failed query.
    return f'count(kube_pod_info{{namespace="arc-runners",pod=~"{config.pool}-.*-runner-.*"}}) or vector(0)'


def own_pod_in_pool(config: Config, runner_name: str) -> bool:
    # ARC names the runner after its pod. The route job normally runs in a
    # smaller pool and never matches; this only matters if a caller puts it in
    # the pool it is measuring.
    return re.fullmatch(f'{config.pool}-.*-runner-.*', runner_name) is not None


def decide(config: Config, measured: Measurement | None, own_pod_in_pool: bool = False) -> tuple[dict[str, str], str]:
    def split(free: int) -> dict[str, str]:
        return {key: config.lab if index < free else config.overflow for index, key in enumerate(config.jobs)}

    if config.lab == config.overflow:
        return split(len(config.jobs)), 'lab and overflow are the same label'
    if measured is None or measured.error:
        return split(0), f'prometheus query failed ({measured.error if measured else "not measured"})'
    if measured.cpu > config.max_cpu:
        return split(0), f'cpu pressure {measured.cpu:g} above {config.max_cpu:g}'
    probe_ms = measured.probe_seconds * 1000
    if probe_ms > config.max_probe_ms:
        return split(0), f'probe p95 {probe_ms:.0f}ms above {config.max_probe_ms:g}ms'
    busy = max(0, round(measured.pods) - (1 if own_pod_in_pool else 0))
    free = max(0, config.capacity - busy)
    return split(free), f'{free} of {config.capacity} Lab slots free'


def query(base: str, expression: str) -> float:
    url = f'{base}/api/v1/query?' + urllib.parse.urlencode({'query': expression})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(url, timeout=QUERY_TIMEOUT)
    except urllib.error.HTTPError as error:
        error.close()
        raise ValueError(f'HTTP {error.code}') from None
    with response:
        body = json.loads(response.read(1048576))
    if body.get('status') != 'success':
        raise ValueError(f"status {body.get('status')!r}")
    result = (body.get('data') or {}).get('result') or []
    if not result:
        raise ValueError('no samples')
    value = float(result[0]['value'][1])
    if not math.isfinite(value):
        raise ValueError(f'value {value}')
    return value


def measure(config: Config) -> Measurement:
    measured = Measurement()
    for field, expression, name in (
        ('pods', pod_query(config), 'pods'),
        ('cpu', CPU_QUERY, 'cpu'),
        ('probe_seconds', PROBE_QUERY, 'probe'),
    ):
        try:
            setattr(measured, field, query(config.prometheus, expression))
        except Exception as error:
            # One failure usually means Prometheus is unreachable, so stop
            # rather than spend another timeout on each remaining query.
            detail = ' '.join(str(error).split()) or type(error).__name__
            measured.error = f'{name}: {detail}'[:200]
            break
    return measured


def summary_line(config: Config, measured: Measurement | None, runners: dict[str, str], reason: str) -> str:
    def shown(value: float | None, scale: float = 1, unit: str = '') -> str:
        if value is None:
            return '?'
        return f'{value * scale:.0f}{unit}' if unit else f'{value:g}'

    if measured is None:
        numbers = 'not measured'
    else:
        numbers = (
            f'pods={shown(measured.pods)}/{config.capacity} '
            f'cpu={shown(measured.cpu)}/{config.max_cpu:g} '
            f'probe_p95={shown(measured.probe_seconds, 1000, "ms")}/{config.max_probe_ms:g}ms'
        )
    decision = ' '.join(f'{key}={label}' for key, label in runners.items())
    return f'lab-route pool={config.pool} {numbers} -> {decision} ({reason})'


def main() -> int:
    env = os.environ
    try:
        config = parse_config({
            'lab': env.get('LAB', ''),
            'overflow': env.get('OVERFLOW', ''),
            'jobs': env.get('JOBS', ''),
            'capacity': env.get('CAPACITY', ''),
            'pool': env.get('POOL', ''),
            'prometheus': env.get('PROMETHEUS', ''),
            'max-cpu-pressure': env.get('MAX_CPU_PRESSURE', ''),
            'max-probe-p95-ms': env.get('MAX_PROBE_P95_MS', ''),
        })
    except ValueError as error:
        print(f'::error::lab-route: {error}')
        return 1

    measured = None
    try:
        if config.lab != config.overflow:
            measured = measure(config)
        runners, reason = decide(config, measured, own_pod_in_pool(config, env.get('RUNNER_NAME', '')))
        line = summary_line(config, measured, runners, reason)
    except Exception as error:
        # Routing is advisory: a bug here must still leave every job a runner.
        runners = {key: config.overflow for key in config.jobs}
        reason = f'lab-route error ({type(error).__name__})'
        line = f'lab-route pool={config.pool} -> every job on {config.overflow} ({reason})'
    print(line)
    with open(env['GITHUB_OUTPUT'], 'a') as output:
        output.write(f"runners={json.dumps(runners, separators=(',', ':'))}\n")
        output.write(f'reason={reason}\n')
    try:
        with open(env['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(line + '\n')
    except (KeyError, OSError) as error:
        print(f'::warning::lab-route could not write the step summary: {error}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
