# omni change impact check

A GitHub Actions workflow that checks an Omni model PR against Omni's schema and content validators, so a model edit can't silently break a dashboard you've labelled verified. This is a template, part of the [omni-ci-workflows](../../README.md) collection, meant to be copied into your own Omni repo rather than run here.

If you're checking an upstream dbt change instead of an Omni model PR, see [dbt-change-impact-check](../dbt-change-impact-check/README.md), it's a different trigger shape (cross repo dispatch, no Omni PR at all) so it lives as its own workflow.

## What it does

On a pull request, `branchPerPullRequest` has already created a matching Omni branch. The workflow resolves it, refreshes its schema, runs semantic validation, then runs content validation against every dashboard labelled verified. If anything fails, the check fails and the PR is blocked. Each `.yml` is a thin wrapper, the actual logic lives in its matching `.py` script, the same split as [omni-ai-eval](../omni-ai-eval/README.md).

There are three interchangeable variants, pick one depending on what you're validating against:

| Variant | Validated against | Routing |
|---|---|---|
| `gate-production.yml` + `gate_production.py` | Production | None, the branch just inherits prod |
| `gate-connection-env.yml` + `gate_connection_env.py` | A separate dev database or schema | A connection environment, selected by a user attribute |
| `gate-dbt-env.yml` + `gate_dbt_env.py` | Dev dbt schemas, same connection | One API call, setting the dbt environment on the branch |

## Connection environment vs dbt environment

These sound similar but are two different switches, relevant if you're choosing between `gate-connection-env.yml` and `gate-dbt-env.yml`.

A connection environment swaps the database connection itself. It points Omni at a different database or schema, and optionally different credentials, and works with any warehouse. You don't choose it per run, you pin it once during setup by setting the validator user's environment user attribute, so every gated run routes to the same dev connection. Use it when your dev data lives in a separate database or schema.

A dbt environment is part of Omni's dbt integration. It doesn't change the connection, it changes which dbt schemas the `omni_dbt` views resolve to within the same connection, and it's set explicitly per branch with one API call. Use it when your Omni views are generated from dbt.

In CI terms, a dbt environment is deterministic, set by id, per branch, straight from the workflow. A connection environment is resolved from the validator user's attribute, pinned once at setup rather than per run. If that attribute goes missing, the branch quietly reads production, a false green.

In both cases a branch's selected environment takes precedence over user-selected and user-attribute assignments, which is what makes branch-scoped validation possible at all.

Docs: [dynamic environments](https://docs.omni.co/connect-data/dynamic-environments), [dbt integration](https://docs.omni.co/integrations/dbt/index), [set dbt environment on branch](https://docs.omni.co/api/dbt/set-dbt-environment-on-model-branch), [refresh schema](https://docs.omni.co/api/models/refresh-schema)

## Prerequisites

Set these up in Omni once, before wiring up any variant:

* `branchPerPullRequest` enabled on the model's git config, so a branch reliably exists for every Omni PR
* Branch based schema refresh enabled on the connection, since the refresh call passes `--branch-id`
* A validator user with querier access or higher, and folder access to the verified dashboards you're gating
* The dashboards you want gated are labelled verified in Omni

`gate-connection-env.yml` only, enable the branch connection environment override toggle (`branchConnectionEnvironmentOverridesUserAttr`) on the connection, and give the validator user the attribute that selects the dev environment.

`gate-dbt-env.yml` only, the dbt integration needs to be enabled and a dbt environment (for example staging) configured in Omni.

## Secrets

Add these under Settings, Secrets and variables, Actions.

| Secret | What it is | Used by |
|---|---|---|
| `OMNI_API_KEY` | Organisation level Omni API token (the CLI reads it as `OMNI_API_TOKEN`) | all three |
| `OMNI_BASE_URL` | Full host of your instance, e.g. `https://yourorg.exploreomni.dev` | all three |
| `OMNI_BASE_MODEL_ID` | Id of the core production shared model the branch forks from | all three |
| `OMNI_VALIDATOR_USER_ID` | UUID of the user content validation is scoped to (folder access) | all three |
| `OMNI_CONNECTION_ID` | Connection id, from the URL when editing the connection | `gate-connection-env.yml`, `gate-dbt-env.yml` |

`POLL_TIMEOUT` (default 900s) and `POLL_INTERVAL` (default 10s) control how long each script waits for the schema refresh, set them as env vars in the workflow if the defaults don't suit your warehouse.

## Copying this into your repo

Pick one variant and copy its `.yml` to `.github/workflows/omni-pr-gate.yml` and its `.py` to `.github/scripts/` in your Omni repo (e.g. `gate_production.py` to `.github/scripts/gate_production.py`), keeping only the pair you chose. Make the `omni-cli-gate` job a required status check on `main`.

## Checklist

* Map the secret to `OMNI_API_TOKEN`, not `OMNI_API_KEY`, in the env block, that's what the CLI reads
* The scripts parse the validator JSON and fail themselves, `validate` and `content-validator-get` both exit 0 even on hard errors
* The scripts poll the async refresh to `COMPLETED` before validating, or you'd validate a stale schema
* Verify the route once by checking the compiled SQL, especially for `gate-connection-env.yml`, where a missing attribute gives a silent false green

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "No Omni branch named …" | The branch name doesn't match `github.head_ref`, or `branchPerPullRequest` is off. The error lists the real branch names. |
| HTTP 400 on `models list` | BRANCH models aren't listable directly. Use `--modelid <base> --include activeBranches`, not `--modelkind BRANCH`. |
| Content validation skipped | `OMNI_VALIDATOR_USER_ID` is unset, or no labels are marked verified in Omni. |
| Every PR passes | Likely a false green, the branch is reading prod. Check the connection environment user attribute (`gate-connection-env.yml`) or the dbt environment id POST (`gate-dbt-env.yml`), and confirm the compiled SQL's `FROM` schema. |
| CLI auth errors | The CLI expects `OMNI_API_TOKEN` and `OMNI_BASE_URL`, this workflow maps `OMNI_API_KEY` to `OMNI_API_TOKEN` for you. |
| Refresh runs long | Expected, it hits the warehouse. If main's schema is already current, the refresh step can be dropped (`gate-production.yml`). |
