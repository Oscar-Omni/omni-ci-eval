# dashboard gate

GitHub Actions workflows that gate a PR on Omni's validators, so a model edit or an upstream dbt change can't silently break a dashboard you've labelled as verified. These are templates, part of the [omni-ci-workflows](../README.md) collection, meant to be copied into your own repos rather than run here.

## What it does

On a pull request, the gate resolves the right Omni model branch, refreshes its schema, runs semantic validation, then runs content validation against every dashboard labelled verified. If anything fails, the check fails and the PR is blocked.

There are four ways to wire this up, depending on two things: what changed, and what you're validating against.

| Scenario | What changed | Validated against | Routing |
|---|---|---|---|
| 1, dbt PR triggers Omni | Upstream data (dbt) | The dev schema the dbt PR built | `repository_dispatch` from the dbt repo, with a commit status posted back |
| 2, Omni PR, production | Omni model YAML | Production | None, the branch just inherits prod |
| 3, Omni PR, connection environment | Omni model YAML | A separate dev database or schema | A connection environment, selected by a user attribute |
| 4, Omni PR, dbt environment | Omni model YAML | Dev dbt schemas, same connection | One API call, setting the dbt environment on the branch |

Only scenario 1 spans two repos, because that's the only case where the change originates outside the Omni repo.

## Connection environment vs dbt environment

These sound similar but are two different switches.

A connection environment swaps the database connection itself. It points Omni at a different database or schema, and optionally different credentials, and works with any warehouse. You don't choose it per run, you pin it once during setup by setting the validator user's environment user attribute, so every gated run routes to the same dev connection. Use it when your dev data lives in a separate database or schema.

A dbt environment is part of Omni's dbt integration. It doesn't change the connection, it changes which dbt schemas the `omni_dbt` views resolve to within the same connection, and it's set explicitly per branch with one API call. Use it when your Omni views are generated from dbt.

In CI terms, a dbt environment is deterministic, set by id, per branch, straight from the workflow. A connection environment is resolved from the validator user's attribute, pinned once at setup rather than per run. If that attribute goes missing, the branch quietly reads production, a false green.

In both cases a branch's selected environment takes precedence over user-selected and user-attribute assignments, which is what makes branch-scoped validation possible at all.

Docs: [dynamic environments](https://docs.omni.co/connect-data/dynamic-environments), [dbt integration](https://docs.omni.co/integrations/dbt/index), [set dbt environment on branch](https://docs.omni.co/api/dbt/set-dbt-environment-on-model-branch), [refresh schema](https://docs.omni.co/api/models/refresh-schema)

## Prerequisites

Set these up in Omni once, before wiring up any scenario:

* `branchPerPullRequest` enabled on the model's git config (scenarios 2 to 4), so a branch reliably exists for every Omni PR
* Branch based schema refresh enabled on the connection, since the refresh call passes `--branch-id`
* A validator user with querier access or higher, and folder access to the verified dashboards you're gating
* The dashboards you want gated are labelled verified in Omni

Scenario 3 only, enable the branch connection environment override toggle (`branchConnectionEnvironmentOverridesUserAttr`) on the connection, and give the validator user the attribute that selects the dev environment.

Scenario 4 only, the dbt integration needs to be enabled and a dbt environment (for example staging) configured in Omni.

## Secrets

Add these under Settings, Secrets and variables, Actions.

| Secret | What it is | Used by |
|---|---|---|
| `OMNI_API_KEY` | Organisation level Omni API token (the CLI reads it as `OMNI_API_TOKEN`) | all |
| `OMNI_BASE_URL` | Full host of your instance, e.g. `https://yourorg.exploreomni.dev` | all |
| `OMNI_BASE_MODEL_ID` | Id of the core production shared model the branch forks from | all |
| `OMNI_VALIDATOR_USER_ID` | UUID of the user content validation is scoped to (folder access) | all |
| `OMNI_CONNECTION_ID` | Connection id, from the URL when editing the connection | 1, 3, 4 |
| `OMNI_REPO_DISPATCH_TOKEN` | PAT with repo scope on the Omni repo, fires the dispatch | 1, dbt repo |
| `DBT_STATUS_TOKEN` | PAT with commit status write on the dbt repo, posts the result back | 1, Omni repo |
| `DBT_SNOWFLAKE_*` | Warehouse credentials for the dbt build (account, user, password, role) | 1, dbt repo |

## Files in this folder

* `scenario-1-dbt-repo.yml`, copy to `.github/workflows/dbt-ci.yml` in the dbt repo
* `scenario-1-omni-repo.yml`, copy to `.github/workflows/validate-from-dbt.yml` in the Omni repo
* `scenario-2-omni-pr-prod.yml`, `scenario-3-omni-pr-connection-env.yml`, `scenario-4-omni-pr-dbt-env.yml`, mutually exclusive alternatives, pick one and copy it to `.github/workflows/omni-pr-gate.yml` in the Omni repo

Scenario 1 also needs its `dbt build` step's `ci` target to write into the schema your Omni dbt or connection environment reads, edit your `profiles.yml` accordingly.

On the dbt repo, make `omni-dashboard-gate` a required status check on `main`, so the PR waits for Omni to report. On scenarios 2 to 4, make `omni-cli-gate` a required status check instead.

### Simpler alternative to scenario 1's dispatch and callback

If both repos can share secrets, you don't need `repository_dispatch` or a manual status callback. Call the Omni gate as a reusable workflow straight from the dbt PR, the reusable job is itself a status check, so gating is automatic:

```yaml
omni-gate:
  needs: build
  uses: your-org/omni-repo/.github/workflows/omni-ci-gate.yml@main
  with: { target_env: "staging" }
  secrets: inherit
```

A reusable workflow (`uses:`) is synchronous and simplest, but couples the run graphs and needs shared or org secrets. Best when one team owns both repos. Dispatch and callback, what scenario 1's files do, is asynchronous and decoupled, the Omni run is owned and observed separately. Best when a different team owns Omni, or validation is slow.

## Choosing a scenario

Omni YAML change, querying prod is fine, use scenario 2. Want CI off prod, use scenario 3 (separate dev database or schema) or scenario 4 (dbt native, explicit switch). dbt or data change, use scenario 1.

## Checklist

* Map the secret to `OMNI_API_TOKEN`, not `OMNI_API_KEY`, in the env block, that's what the CLI reads
* Parse the validator JSON and fail yourself, `validate` and `content-validator-get` both exit 0 even on hard errors
* Poll the async refresh to `COMPLETED` before validating, or you validate a stale schema
* Verify the route once by checking the compiled SQL, especially for connection environments, where a missing attribute gives a silent false green
* Make the gate a required status check, and serialise runs that share a dev schema

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "No Omni branch named …" | The branch name doesn't match `github.head_ref`, or `branchPerPullRequest` is off. The error lists the real branch names. |
| HTTP 400 on `models list` | BRANCH models aren't listable directly. Use `--modelid <base> --include activeBranches`, not `--modelkind BRANCH`. |
| Content validation skipped | `OMNI_VALIDATOR_USER_ID` is unset, or no labels are marked verified in Omni. |
| Every PR passes | Likely a false green, the branch is reading prod. Check the connection environment user attribute (scenario 3) or the dbt environment id POST (scenario 4), and confirm the compiled SQL's `FROM` schema. |
| CLI auth errors | The CLI expects `OMNI_API_TOKEN` and `OMNI_BASE_URL`, these workflows map `OMNI_API_KEY` to `OMNI_API_TOKEN` for you. |
| Refresh runs long | Expected, it hits the warehouse. If main's schema is already current, the refresh step can be dropped (scenario 2). |
