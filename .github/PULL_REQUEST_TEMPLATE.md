<!--
Thanks for the contribution. Keep the description tight — reviewers
should be able to understand WHAT changed and WHY in 30 seconds.
-->

## What

<!-- One paragraph. Reference the issue / ADR / prompts.md turn if relevant. -->

## Why

<!-- Motivation. What problem does this solve? Why is it the right shape? -->

## How

<!-- Optional: notable design choices. Diagrams welcome. -->

## Verification

<!-- How you proved the change works locally: -->

- [ ] `make lint` green (ruff + black, zero warnings)
- [ ] `make type` green (mypy --strict, zero errors)
- [ ] `make test` green
- [ ] `make test-core` ≥ 90 %
- [ ] `make test-api` ≥ 80 %
- [ ] Manually exercised in `make up` if user-facing

## Checklist

- [ ] Tests added or updated (unit + integration where relevant)
- [ ] `make test-core` and `make test-api` coverage gates still pass
- [ ] If a new metric / span / env var was added: dashboard + docs updated
- [ ] If architecture changed: ADR added in `docs/adr/` and linked here
- [ ] If a new dependency was added: justified in this PR description
- [ ] `prompts.md` updated if this PR is part of the Vibe-Coding turn loop
- [ ] No regressions in the existing E2E pipeline
      (`POST /v1/events → AlertService.create_and_enqueue → outbox → /v1/_sink`)

## Out of scope (noted in passing)

<!-- Anything you saw nearby but did NOT change. Helps reviewers and -->
<!-- prevents scope creep — these belong in follow-up PRs/issues. -->
