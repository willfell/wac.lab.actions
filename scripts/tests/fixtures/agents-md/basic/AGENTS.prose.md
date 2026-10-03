# fixture-basic

A fixture repo for the agents-md gate tests, with just enough files for every
command form the gate resolves.

## The one thing to understand

Every command below runs from the repo root.

## Commands

```sh
npm run build
npm run lint
cd app && npm test  # the app's tests live in app/
bash scripts/hello.sh
make lint
```

## Layout

- `app/`: the app package, with its own `package.json`.
- `scripts/`: helper scripts.

## Invariants

- Nothing here is real.

## Boundaries

- Do not edit the fixture during a test; copy it first.

## Local notes

A repo may add its own H2 sections anywhere before the generated tail.
