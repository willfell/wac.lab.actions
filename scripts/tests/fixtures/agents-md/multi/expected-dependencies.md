## Dependencies

Generated from `catalog-info.yaml` by wac.lab.actions `scripts/agents-md.py render`. Change the catalog and re-render; do not edit this section or the next by hand.

### Component `front`

The suite front door. System `suite`, type `website`, lifecycle `production`.
https://docs.wacwini.com/catalog/default/component/front

- Depends on: `component:front-ui`, `resource:oci-registry`, `resource:traefik`
- Provides APIs: `api:bridge-front.thing.get`
- Consumes APIs: `api:bridge-other.Thing.list`, `api:bridge-other.thing.create`

### Component `front-ui`

The design system, folded across two lines. Type `library`, lifecycle `production`.
https://docs.wacwini.com/catalog/default/component/front-ui

- Depends on: (none)
- Provides APIs: (none)
- Consumes APIs: (none)

### Also declared here

- `api:bridge-front.thing.get`: Read a thing
- `resource:front-cache`

Incoming edges (what depends on this repo) are not listed here. They live in the catalog at docs.wacwini.com.
