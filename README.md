# omni ci eval

A GitHub Actions workflow that runs an Omni AI eval on pull requests touching model YAML. This repo is a template. It doesn't run on anything itself, its whole purpose is to be copied into your own Omni repo.

## What it does

When a PR changes the model, the workflow finds the matching Omni model branch (created during the developer's omni sync session), runs the same prompt set against that branch and against main, then posts a per prompt regression report as a PR comment, including the LLM spend for each side.

The check fails if any prompt regresses (passed on main, failed on the branch), if no matching Omni branch is found, or on an operational error such as an API failure or timeout. Cost changes are reported but never gate the check.

Only up to 2 Omni eval runs can be in progress org wide, so prompt sets run one at a time.

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
