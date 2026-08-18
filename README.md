# omni ci eval

A GitHub Actions workflow that runs an Omni AI eval on pull requests touching model YAML. This repo is a template. It doesn't run on anything itself, its whole purpose is to be copied into your own Omni repo.

## What it does

When a PR changes the model, the workflow finds the matching Omni model branch (created during the developer's omni sync session), runs the same prompt set against that branch and against main, then posts a per prompt regression report as a PR comment, including the LLM spend for each side.

The check fails if any prompt regresses (passed on main, failed on the branch), if no matching Omni branch is found, or on an operational error such as an API failure or timeout. Cost changes are reported but never gate the check.

Only up to 2 Omni eval runs can be in progress org wide, so prompt sets run one at a time.

## What it talks to

Everything goes to `OMNI_BASE_URL` (your org's Omni API base, e.g. `https://<your org>.omniapp.co/api`), authenticated with `OMNI_API_KEY` as a bearer token. Three endpoints, all Omni's public v1 API:

* `GET /v1/models` — paged through to find the model branch matching the PR's git branch name
* `POST /v1/ai/eval/runs` — starts an eval run for a prompt set, once against `main` and once against that branch
* `GET /v1/ai/eval/runs/{id}` — polled until both runs are complete and every prompt is scored

No other services or endpoints are involved. Nothing is sent anywhere outside your Omni org and GitHub's own PR comment API.

## Example output

This is what lands as a PR comment once it's wired up:

> ### 🔴 Omni AI eval: 1 prompt(s) regressed on this branch
>
> branch `add-churn-segment` (`a1b2c3d`) vs `main` · 1 prompt set
>
> ### Core revenue prompts
>
> Prompt set `core-revenue`
>
> | | main | branch | Δ |
> |---|---|---|---|
> | **Accuracy** | 87.5% (7/8) | 75.0% (6/8) | -12.5 pts |
> | **LLM spend (USD)** | $0.0412 | $0.0447 | +0.0035 (+8%) |
>
> #### 🔴 Regressions (1)
> - What was total revenue by region last quarter? — _judge marked branch answer incomplete_
>
> #### 🟢 Improvements (1)
> - Which customers churned in the last 30 days?
>
> <details><summary>Per-prompt detail</summary>
>
> table of every prompt with main/branch score, cost, query count and timing
>
> </details>
>
> _This check **fails on any regression** (a prompt that passed on `main` but failed on the branch), in any prompt set. Cost changes never gate._

If nothing regressed you just get the green headline and the accuracy and spend table, no regressions or improvements sections.

## Prerequisite

You need the prompt sets you want to run already set up in Omni before wiring this up. The workflow evaluates existing prompt sets, it doesn't create them.

## Copying this into your repo

1. Copy `.github/workflows/omni-ai-eval.yml` and `.github/scripts/omni_eval.py` into your repo, keeping the same paths.
2. In your repo, go to Settings, Secrets and variables, Actions and add:

   Secrets:
   * `OMNI_API_KEY` organization API key
   * `OMNI_BASE_URL` for example `https://<your org>.omniapp.co/api`

   Variables:
   * `OMNI_MODEL_ID` the Omni model id to evaluate

3. Open `omni_eval.py` and edit the `PROMPT_SETS` list to the prompt sets you already have set up in Omni. This isn't a repo variable, it's edited directly in the script.
4. Open `omni-ai-eval.yml` and update the `paths` filter under the (commented out) `pull_request` trigger to match where your model YAML lives.
5. The workflow ships with only `workflow_dispatch` enabled, so you can test it manually first. Once you're happy, uncomment the `pull_request` trigger block to have it run automatically on PRs.

That's it, no other changes needed to adopt it.
