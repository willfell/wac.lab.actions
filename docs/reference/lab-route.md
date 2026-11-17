# lab-route

Sends a workflow's heavy jobs to a Lab runner pool while the pool has free
slots and the Mac is healthy, and to a fallback label otherwise. It runs in a
small job of its own, and the heavy jobs read their runner from its output, so
a busy or hot Lab costs a hosted runner instead of slowing production.

```yaml
jobs:
  route:
    runs-on: ${{ vars.CI_RUNNER_SMALL }}
    timeout-minutes: 2
    outputs:
      runners: ${{ steps.route.outputs.runners }}
    steps:
      - id: route
        uses: willfell/wac.lab.actions/lab-route@vX.Y.Z
        with:
          lab: ${{ vars.CI_RUNNER_STANDARD }}
          overflow: ${{ vars.CI_RUNNER_LARGE }}
          jobs: persistence base browser

  persistence:
    needs: [route]
    runs-on: ${{ fromJSON(needs.route.outputs.runners).persistence }}
```

| Input | Meaning | Default |
| --- | --- | --- |
| `lab` | Lab runner label, passed as a size variable | required |
| `overflow` | Fallback runner label, passed as a size variable | required |
| `jobs` | Space-separated job keys, highest priority for Lab slots first | required |
| `capacity` | Lab slots in the pool | `3` |
| `pool` | Scale set whose runner pods are counted, as `<pool>-.*-runner-.*` in `arc-runners` | the `lab` value |
| `prometheus` | Prometheus base URL | `http://prometheus-operated.observability.svc:9090` |
| `max-cpu-pressure` | Highest VM CPU pressure (`node_pressure_cpu_waiting_seconds_total` rate over 1m) that still routes to Lab | `0.4` |
| `max-probe-p95-ms` | Highest p95 of `probe_duration_seconds{target=~".*-local"}`, in ms, that still routes to Lab | `30` |

| Output | Meaning |
| --- | --- |
| `runners` | Compact JSON object mapping each job key to its label, e.g. `{"persistence":"ci-standard","base":"ci-standard","browser":"ubuntu-24.04-arm"}` |
| `reason` | Short reason for the decision, e.g. `2 of 3 Lab slots free` or `cpu pressure 0.52 above 0.4` |

**The decision.** The action asks Prometheus three instant queries: the
runner pods in the pool, the VM's CPU pressure, and the p95 of production's
health-probe latency. The five-second timeout applies to each connect and
socket read, not to DNS resolution, so give the route job
`timeout-minutes: 2` as in the example. An idle pool reads as 0 pods; a
missing `kube_pod_info` series reads as a failed query, not as a free Lab. If a query fails or returns no usable
number, if CPU pressure is above `max-cpu-pressure`, or if probe p95 is above
`max-probe-p95-ms`, every job goes to `overflow`. Otherwise free slots are
`capacity` minus the pool's current pods, and the first that many job keys, in
the order given, go to `lab`; the rest go to `overflow`. A route job that runs
in the pool it measures does not count its own pod. When `lab` and `overflow`
are the same label, nothing is measured and every job gets that label. When
`pool` is left empty and `lab` cannot name a Lab scale set (a size variable set
to a hosted label such as `ubuntu-24.04-arm`), every job goes to `overflow`
and the step succeeds.

**Prometheus never fails the job.** A timeout, refused connection, HTTP error
or malformed answer sends every job to `overflow`, and `reason` says which
query failed. Only malformed inputs fail the step: an empty label, an empty or
repeated job key, a key that is not an identifier, a non-numeric limit, or an
explicitly passed `pool` holding anything but letters, digits and `-`.

**It only works inside the Lab cluster.** Prometheus is a cluster-internal
service, reachable from runner pods in `arc-runners` and from nowhere else. The
route job itself must therefore run on `CI_RUNNER_SMALL`; on a GitHub-hosted
runner every query fails and every job overflows. It needs `python3`, which the
`ghcr.io/actions/actions-runner` image carries, and no checkout or credentials.

Each run writes one line to the step summary with the measured numbers and the
decision, for example
`lab-route pool=ci-standard pods=1/3 cpu=0.27/0.4 probe_p95=12ms/30ms -> persistence=ci-standard base=ci-standard browser=ubuntu-24.04-arm (2 of 3 Lab slots free)`,
so routing can be reviewed later from run summaries.

The decision is a snapshot. Two workflows routed in the same few seconds can
both see the same free slot; the extra job queues on Lab until the scale set
has room, which is the behaviour without routing.

The `actionlint` fleet policy accepts
`runs-on: ${{ fromJSON(needs.<route>.outputs.runners).<key> }}` (or `['<key>']`)
only when `<route>` is in the job's `needs`, exports `outputs.runners` from a
`willfell/wac.lab.actions/lab-route@<tag>` step, passes `lab` and `overflow` as
size variables, and lists `<key>` in `jobs`. The route job must hold that one
step and no `container`, `services` or `env`, and the workflow `env` must not set
`PATH`, `BASH_ENV`, `ENV`, `LD_*` or `PYTHON*`: each of those could change which
interpreter or files the action runs. The action runs its script with
`python3 -I`. The policy names the action by its `willfell/` path, so moving
this repository to another owner needs a matching change in
`scripts/runner-policy.py`, or every routed workflow fails lint.

The decision logic and the Prometheus client are tested by
`scripts/tests/lab-route.test.py` against a local stub server. This repo's CI
runs on GitHub-hosted runners, which cannot reach Lab's Prometheus, so it runs
only that test.
