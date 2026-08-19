# omni ci workflows

A collection of GitHub Actions workflow templates for Omni repos. Each folder under `workflows/` is a self contained CI workflow with its own README, covering a different concern, not just AI evals. None of it runs in this repo, it's a library to copy from into your own Omni or dbt repo.

## Workflows

* [omni-ai-eval](workflows/omni-ai-eval/README.md), runs an Omni AI eval on PRs touching model YAML, regression tests a prompt set between the PR's Omni model branch and main, and posts the result as a PR comment.
* Dashboard gate, gates a PR on Omni's schema and content validators so a model edit or an upstream dbt change can't silently break a dashboard you've labelled verified. Split into two workflows since the trigger shape differs:
  * [dashboard-gate-omni-pr](workflows/dashboard-gate-omni-pr/README.md), an Omni model PR, gated against production, a dev connection environment, or a dev dbt environment (pick one of three variants).
  * [dashboard-gate-dbt-trigger](workflows/dashboard-gate-dbt-trigger/README.md), an upstream dbt PR, which dispatches to the Omni repo to gate against the schema the dbt PR built.

## Using a workflow from here

1. Open the workflow's own folder and read its README first, prerequisites and secrets differ per workflow.
2. Copy the `.yml` file(s) it names into your repo's `.github/workflows/` (and any accompanying script into `.github/scripts/`), the README says exactly where each file lands.
3. Add the secrets and variables the README lists, under Settings, Secrets and variables, Actions.
4. Follow any setup steps that live in Omni itself (branch config, connection environments, verified labels), each README calls these out.

## Adding a new workflow

Give it its own folder under `workflows/`, named after what it does, with a README covering what it does, prerequisites, secrets, and where each file is meant to land in a consumer repo. Then add it to the list above.
