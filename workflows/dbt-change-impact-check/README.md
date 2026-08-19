# dbt change impact check

A pair of GitHub Actions workflows that check a dbt PR against Omni's schema and content validators, so an upstream data change can't silently break a dashboard you've labelled verified. These are templates, part of the [omni-ci-workflows](../../README.md) collection, meant to be copied into your dbt repo and your Omni repo rather than run here.

Unlike [omni-change-impact-check](../omni-change-impact-check/README.md), there's no Omni PR here at all, the change originates in the dbt repo. That's a different trigger shape (a cross repo dispatch instead of a plain `pull_request`), which is why this is its own workflow rather than another variant of that one.

## What it does

On a dbt PR, `dbt-repo.yml` builds a dev schema and dispatches to the Omni repo. `omni-repo.yml` runs `validate_from_dbt.py`, which creates an ephemeral model branch, points it at the dbt environment named in the dispatch payload, refreshes its schema, runs semantic validation, then runs content validation against every dashboard labelled verified. It deletes the branch, then posts the result back onto the dbt PR's commit as the `dbt-change-impact-check` status check, whatever the outcome. `dbt-repo.yml` stays plain YAML, there's no branching logic in it worth a script, all the validation logic lives in `validate_from_dbt.py`, the same split as [omni-ai-eval](../omni-ai-eval/README.md).

## Prerequisites

Set these up once, before wiring this up:

* The dbt integration enabled and a dbt environment (for example staging) configured in Omni
* A validator user with querier access or higher, and folder access to the verified dashboards you're gating
* The dashboards you want gated are labelled verified in Omni
* dbt's `ci` target must write into the schema your Omni dbt environment reads, see the `profiles.yml` excerpt in `dbt-repo.yml`'s header comment

## Secrets

Add these under Settings, Secrets and variables, Actions.

| Secret | What it is | Repo |
|---|---|---|
| `OMNI_API_KEY` | Organisation level Omni API token (the CLI reads it as `OMNI_API_TOKEN`) | Omni |
| `OMNI_BASE_URL` | Full host of your instance, e.g. `https://yourorg.exploreomni.dev` | Omni |
| `OMNI_BASE_MODEL_ID` | Id of the core production shared model the ephemeral branch forks from | Omni |
| `OMNI_VALIDATOR_USER_ID` | UUID of the user content validation is scoped to (folder access) | Omni |
| `OMNI_CONNECTION_ID` | Connection id, from the URL when editing the connection | Omni |
| `DBT_STATUS_TOKEN` | PAT with commit status write on the dbt repo, posts the result back | Omni |
| `OMNI_REPO_DISPATCH_TOKEN` | PAT with repo scope on the Omni repo, fires the dispatch | dbt |
| `DBT_SNOWFLAKE_*` | Warehouse credentials for the dbt build (account, user, password, role) | dbt |

## Copying this into your repos

* `dbt-repo.yml`, copy to `.github/workflows/dbt-ci.yml` in the dbt repo
* `omni-repo.yml`, copy to `.github/workflows/validate-from-dbt.yml` in the Omni repo, and `validate_from_dbt.py` to `.github/scripts/validate_from_dbt.py` in the same repo

On the dbt repo, make `dbt-change-impact-check` a required status check on `main`, so the PR waits for Omni to report.

### Simpler alternative, skip the dispatch and callback

If both repos can share secrets, you don't need `repository_dispatch` or a manual status callback. Call the Omni gate as a reusable workflow straight from the dbt PR, the reusable job is itself a status check, so gating is automatic:

```yaml
omni-gate:
  needs: build
  uses: your-org/omni-repo/.github/workflows/omni-ci-gate.yml@main
  with: { target_env: "staging" }
  secrets: inherit
```

A reusable workflow (`uses:`) is synchronous and simplest, but couples the run graphs and needs shared or org secrets. Best when one team owns both repos. Dispatch and callback, what `dbt-repo.yml` and `omni-repo.yml` do, is asynchronous and decoupled, the Omni run is owned and observed separately. Best when a different team owns Omni, or validation is slow.

`POLL_TIMEOUT` (default 900s) and `POLL_INTERVAL` (default 10s) control how long `validate_from_dbt.py` waits for the schema refresh, set them as env vars in `omni-repo.yml` if the defaults don't suit your warehouse.

## Checklist

* Map the secret to `OMNI_API_TOKEN`, not `OMNI_API_KEY`, in the env block, that's what the CLI reads
* `validate_from_dbt.py` parses the validator JSON and fails itself, `validate` and `content-validator-get` both exit 0 even on hard errors
* `validate_from_dbt.py` polls the async refresh to `COMPLETED` before validating, or it would validate a stale schema
* `validate_from_dbt.py` always posts a status back, whether it passes, fails, or hits an operational error, so the dbt PR never sits stuck on pending
* Serialise runs that share a dev schema, two PRs building into the same schema at once will clobber each other

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| dbt PR never gets a status | `repository_dispatch` didn't fire, check `OMNI_REPO_DISPATCH_TOKEN` has repo scope on the Omni repo, and that the event type matches (`dbt-change-validation`) |
| Omni repo run starts but the dbt PR never updates | `DBT_STATUS_TOKEN` is missing or lacks commit status write on the dbt repo |
| dbt environment not found | `dbt_env` in the dispatch payload doesn't match a configured environment name on the connection |
| HTTP 400 on `models list` | BRANCH models aren't listable directly. Use `--modelid <base> --include activeBranches`, not `--modelkind BRANCH`. |
| Content validation skipped | `OMNI_VALIDATOR_USER_ID` is unset, or no labels are marked verified in Omni |
| CLI auth errors | The CLI expects `OMNI_API_TOKEN` and `OMNI_BASE_URL`, `omni-repo.yml` maps `OMNI_API_KEY` to `OMNI_API_TOKEN` for you |
