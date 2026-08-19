#!/usr/bin/env python3
"""Check an Omni model PR branch against dev dbt schemas.

One of three interchangeable variants, see the workflows/omni-change-impact-check
README. Unlike gate_connection_env.py, routing is explicit: one API call
points the branch at a dbt environment by id before the refresh. Same
connection, different dbt-built schemas.

  OMNI_API_TOKEN      Bearer token for the Omni API (required)
  OMNI_BASE_URL       e.g. https://<your-org>.omniapp.co/api             (required)
  BASE_MODEL_ID       Id of the production shared model                  (required)
  OMNI_CONNECTION_ID  Connection id, from the URL when editing the connection (required)
  BRANCH_NAME         git head branch, resolved to an Omni branch of the same name (required)
  DBT_ENV_NAME        Name of the dbt environment configured in Omni, e.g. staging (required)
  USER_ID             Validator user id, content validation is skipped if unset
  POLL_TIMEOUT        seconds to wait for the schema refresh              (default: 900)
  POLL_INTERVAL       seconds between refresh polls                      (default: 10)

Exit codes:
  0  gate passed
  1  gate failed (no matching branch, no matching dbt environment, semantic
     or content validation issues)
  2  operational failure (CLI or API error, refresh timeout)
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
        self.branch_name = require_env("BRANCH_NAME")
        self.dbt_env_name = require_env("DBT_ENV_NAME")
        self.user_id = os.environ.get("USER_ID")
        self.poll_timeout = int(env_or("POLL_TIMEOUT", "900"))
        self.poll_interval = int(env_or("POLL_INTERVAL", "10"))


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


def resolve_branch_id(cfg):
    """BRANCH models aren't listable directly, so pull activeBranches off the base model."""
    print(f"[info] Resolving Omni branch '{cfg.branch_name}' off {cfg.base_model_id}...")
    out = omni_cli("models", "list", "--modelid", cfg.base_model_id, "--include", "activeBranches")
    for branch in out.get("activeBranches") or []:
        if branch.get("name") == cfg.branch_name:
            print(f"[info] Branch model id: {branch['id']}")
            return branch["id"]
    names = [b.get("name") for b in out.get("activeBranches") or []]
    sys.stderr.write(f"::error::No Omni branch named '{cfg.branch_name}' found. Available branches: {names}\n")
    sys.exit(1)


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


def semantic_validate(cfg, branch_model_id):
    print(f"[info] Running semantic model validation against dbt env '{cfg.dbt_env_name}'...")
    issues = omni_cli("models", "validate", branch_model_id)
    errors = [r for r in issues if not r.get("is_warning", False)]
    print(f"[info] semantic validation: {len(errors)} blocking issue(s)")
    if errors:
        sys.stderr.write(
            f"::error::Semantic validation found {len(errors)} blocking issue(s) "
            f"against '{cfg.dbt_env_name}':\n")
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
        sys.stderr.write(
            f"::error::Content validation failed: {issues} verified item(s) with issues "
            f"against '{cfg.dbt_env_name}'\n")
        sys.exit(1)


def main():
    cfg = Config()
    branch_model_id = resolve_branch_id(cfg)
    point_branch_at_dbt_environment(cfg)
    refresh_and_wait(cfg, branch_model_id)
    semantic_validate(cfg, branch_model_id)
    content_validate(cfg, branch_model_id)
    print(f"[ok] Gate passed (dbt environment '{cfg.dbt_env_name}').")


if __name__ == "__main__":
    main()
