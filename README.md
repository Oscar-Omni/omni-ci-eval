# omni ci eval

A GitHub Actions workflow that runs an Omni AI eval on pull requests touching model YAML.

## What it does

When a PR changes the model, the workflow finds the matching Omni model branch (created during the developer's omni sync session), runs the same prompt set against that branch and against main, then posts a per prompt regression report as a PR comment.

The check fails if any prompt regresses (passed on main, failed on the branch), if no matching Omni branch is found, or on an operational error such as an API failure or timeout. Cost changes never gate the check.

Only up to 2 Omni eval runs can be in progress org wide, so prompt sets run one at a time.

## Setup

Add these to the repo under Settings, Secrets and variables, Actions.

Secrets:

* `OMNI_API_KEY` organization API key
* `OMNI_BASE_URL` for example `https://<your org>.omniapp.co/api`

Variables:

* `OMNI_MODEL_ID` the Omni model id to evaluate

Which prompt sets run is controlled by the `PROMPT_SETS` list in `.github/scripts/omni_eval.py`, not a repo variable. Edit that list directly to add or remove suites.

## Status

The workflow currently only runs manually via `workflow_dispatch`. To turn on automatic runs on PRs, uncomment the `pull_request` trigger in `.github/workflows/omni-ai-eval.yml`.

## Using this in another repo

Copy `.github/workflows/omni-ai-eval.yml` and `.github/scripts/omni_eval.py`, set the secrets and variable above, update `PROMPT_SETS` in the script, and adjust the workflow's `paths` filter to match where your model YAML lives.
