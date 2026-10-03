1. No comments in config, manifest or infrastructure files: YAML, HCL and Terraform, JSON, TOML, INI, `.env`, Helm templates, Dockerfiles. Reasoning goes, in order of preference, into a test, the commit message, the PR description, or a doc under `docs/`. Never strip a human's existing comments.
2. No emoji or glyph icons in code, docs, commits or UI. Where a repo's Invariants name exemptions (wac's do), those govern that repo.
3. A merge to `main` reconciled by Argo CD is the only deploy. Never change cluster state by hand, outside the bootstrap exceptions lab documents.
4. No secret or token in a tracked file. Secrets are declared in lab's secrets registry and reach the cluster as Secrets.
5. Fleet procedures live as skills in `willfell/wac.plugins` under `plugins/wac/skills/<name>/SKILL.md`. Read the matching one before onboarding a repo, deploying, releasing the UI kit, or changing CI runners.
6. Catalog entity names are load-bearing; see the `docs-onboard-repo` skill before touching `catalog-info.yaml`.

Where a fleet rule and this repo's Invariants disagree, the Invariants win; fix the rule upstream in wac.lab.actions.
