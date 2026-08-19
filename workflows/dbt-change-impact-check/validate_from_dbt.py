#!/usr/bin/env python3
"""Validate an ephemeral Omni branch against a dbt-triggered change.

Omni repo side of the dbt change impact check, see dbt-repo.yml for the
other half. There is no Omni PR here, so this script creates an ephemeral
branch, points it at the dbt environment named in the dispatch payload,
refreshes its schema, runs semantic validation, then runs content
validation against every dashboard labelled verified. It deletes the
branch and posts success or failure back onto the dbt PR's commit,
whatever happens.

  OMNI_API_TOKEN      Bearer token for the Omni API (required)
  OMNI_BASE_URL       e.g. https://<your-org>.omniapp.co/api             (required)
  BASE_MODEL_ID       Id of the production shared model                  (required)
  OMNI_CONNECTION_ID  Connection id, from the URL when editing the connection (required)
  DBT_ENV_NAME        dbt environment named in the dispatch payload       (required)
  BRANCH_NAME         name for the ephemeral branch this script creates   (required)
  DBT_STATUS_TOKEN    PAT with commit status write on the dbt repo        (required)
  DBT_REPO            dbt repo to post the status back to, from the dispatch payload (required)
  DBT_SHA             dbt commit to post the status back to, from the dispatch payload (required)
  USER_ID             Validator user id, content validation is skipped if unset
  POLL_TIMEOUT        seconds to wait for the schema refresh              (default: 900)
  POLL_INTERVAL       seconds between refresh polls                      (default: 10)

The GITHUB_REPOSITORY and GITHUB_RUN_ID environment variables that GitHub
Actions sets automatically are used to build the status's target_url.

Exit codes:
  0  validation passed
  1  validation failed (no matching dbt environment, semantic or content
     validation issues)
  2  operational failure (CLI or API error, refresh timeout)

Whatever the exit code, a status is posted back to the dbt PR's commit
before this script exits.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


class Config:
    def __init__(self):
        self.api_token = require_env("OMNI_API_TOKEN")
        self.base_url = require_env("OMNI_BASE_URL").rstrip("/")
        self.base_model_id = require_env("BASE_MODEL_ID")
        self.connection_id = require_env("OMNI_CONNECTION_ID")
        self.dbt_env_name = require_env("DBT_ENV_NAME")
        self.branch_name = require_env("BRANCH_NAME")
        self.dbt_status_token = require_env("DBT_STATUS_TOKEN")
        self.dbt_repo = require_env("DBT_REPO")
        self.dbt_sha = require_env("DBT_SHA")
        self.user_id = os.environ.get("USER_ID")
        self.poll_timeout = int(env_or("POLL_TIMEOUT", "900"))
        self.poll_interval = int(env_or("POLL_INTERVAL", "10"))
        self.github_repository = os.environ.get("GITHUB_REPOSITORY", "")
        self.github_run_id = os.environ.get("GITHUB_RUN_ID", "")


def require_env(name):
    val = os.environ.get(name)
    if not val:
        sys.stderr.write(f"Missing required environment variable: {name}\n")
        sys.exit(2)
    return val


def env_or(name, default):
    """Like os.environ.get, but treats an empty value as unset. GitHub Actions
    passes `${{ secrets.X }}` as an empty string when the secret is undefined."""
    val = os.environ.get(name)
    return val if val else default


def api(cfg, method, path, body=None, must_succeed=True):
    url = f"{cfg.base_url}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {cfg.api_token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if must_succeed:
            sys.stderr.write(f"::error::API {method} {path} -> {e.code}: {detail}\n")
            sys.exit(2)
        print(f"::warning::API {method} {path} -> {e.code}: {detail}")
        return None
    except urllib.error.URLError as e:
        if must_succeed:
            sys.stderr.write(f"::error::API {method} {path} failed: {e}\n")
            sys.exit(2)
        print(f"::warning::API {method} {path} failed: {e}")
        return None


def omni_cli(*args):
    """Run `omni <args> --format json` and return the parsed JSON."""
    proc = subprocess.run(
        ["omni", *args, "--format", "json"], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"::error::omni {' '.join(args)} failed:\n{proc.stderr}\n")
        sys.exit(2)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"::error::omni {' '.join(args)} did not return JSON:\n{proc.stdout}\n")
        sys.exit(2)


def create_branch(cfg):
    print(f"[info] Creating ephemeral branch '{cfg.branch_name}' off {cfg.base_model_id}...")
    out = omni_cli("models", "create-branch", cfg.base_model_id, "--name", cfg.branch_name)
    branch_model_id = (out.get("model") or {}).get("id")
    if not branch_model_id:
        sys.stderr.write(f"::error::create-branch did not return .model.id:\n{json.dumps(out)}\n")
        sys.exit(2)
    print(f"[info] Branch model id: {branch_model_id}")
    return branch_model_id


def cleanup_branch(cfg):
    print("[info] Cleaning up branch...")
    subprocess.run(
        ["omni", "models", "delete-branch", cfg.base_model_id, cfg.branch_name, "--format", "json"],
        capture_output=True, text=True)


def point_branch_at_dbt_environment(cfg):
    print(f"[info] Resolving dbt environment '{cfg.dbt_env_name}'...")
    envs = api(cfg, "GET", f"/v1/connections/{cfg.connection_id}/dbt/environments")
    dbt_env_id = next((e["id"] for e in envs or [] if e.get("name") == cfg.dbt_env_name), None)
    if not dbt_env_id:
        sys.stderr.write(
            f"::error::dbt environment '{cfg.dbt_env_name}' not found on connection {cfg.connection_id}\n")
        sys.exit(1)
    print(f"[info] dbt environment id: {dbt_env_id}")
    api(
        cfg, "POST", f"/v1/models/{cfg.base_model_id}/branch/{cfg.branch_name}/dbt",
        {"dbt_environment_id": dbt_env_id}, must_succeed=False)


def refresh_and_wait(cfg, branch_model_id):
    print("[info] Refreshing schema...")
    out = omni_cli("models", "refresh", cfg.base_model_id, "--branch-id", branch_model_id)
    job_id = out.get("jobId")
    deadline = time.time() + cfg.poll_timeout
    while True:
        status = omni_cli("models", "jobs-get-status", job_id)
        state = status.get("status")
        print(f"[info] refresh job {job_id}: {state}")
        if state == "COMPLETED":
            return
        if state == "FAILED":
            sys.stderr.write(f"::error::schema refresh job failed:\n{json.dumps(status)}\n")
            sys.exit(2)
        if time.time() > deadline:
            sys.stderr.write("::error::schema refresh did not finish within timeout\n")
            sys.exit(2)
        time.sleep(cfg.poll_interval)


def semantic_validate(branch_model_id):
    print("[info] Running semantic model validation...")
    issues = omni_cli("models", "validate", branch_model_id)
    errors = [r for r in issues if not r.get("is_warning", False)]
    print(f"[info] semantic validation: {len(errors)} blocking issue(s)")
    if errors:
        sys.stderr.write(f"::error::Semantic validation found {len(errors)} blocking issue(s):\n")
        for e in errors:
            sys.stderr.write(f" - {e.get('yaml_path')}: {e.get('message')}\n")
        sys.exit(1)


def content_validate(cfg, branch_model_id):
    if not cfg.user_id:
        print("::warning::USER_ID not set, skipping content validation.")
        return
    labels_out = omni_cli("labels", "list")
    verified = [l["name"] for l in labels_out.get("labels") or [] if l.get("verified")]
    print(f"[info] verified labels: {verified or '<none>'}")
    if not verified:
        print("::warning::No verified labels found, nothing to content-validate.")
        return
    result = omni_cli(
        "models", "content-validator-get", cfg.base_model_id,
        "--branch-id", branch_model_id, "--userid", cfg.user_id,
        "--labels", ",".join(verified))
    issues = sum(
        1
        for item in result.get("content") or []
        for qi in item.get("queries_and_issues") or []
        if qi.get("issues")
    )
    print(f"[info] content validation: {issues} verified item(s) with issues")
    if issues:
        sys.stderr.write(f"::error::Content validation failed: {issues} verified item(s) with issues\n")
        sys.exit(1)


def report_status(cfg, state):
    target_url = (
        f"https://github.com/{cfg.github_repository}/actions/runs/{cfg.github_run_id}"
        if cfg.github_repository and cfg.github_run_id else None
    )
    body = {
        "state": state,
        "context": "dbt-change-impact-check",
        "description": f"Omni dashboard validation: {state}",
    }
    if target_url:
        body["target_url"] = target_url
    req = urllib.request.Request(
        f"https://api.github.com/repos/{cfg.dbt_repo}/statuses/{cfg.dbt_sha}",
        data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {cfg.dbt_status_token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60):
            pass
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        # Don't let a failed callback mask the real validation result in the exit code.
        print(f"::warning::Failed to report status back to the dbt PR: {e}")


def main():
    cfg = Config()
    branch_model_id = None
    exit_code = 0
    try:
        branch_model_id = create_branch(cfg)
        point_branch_at_dbt_environment(cfg)
        refresh_and_wait(cfg, branch_model_id)
        semantic_validate(branch_model_id)
        content_validate(cfg, branch_model_id)
        print("[ok] Validation passed.")
    except SystemExit as e:
        exit_code = e.code or 1
    finally:
        if branch_model_id:
            cleanup_branch(cfg)
        report_status(cfg, "success" if exit_code == 0 else "failure")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
