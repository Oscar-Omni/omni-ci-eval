#!/usr/bin/env python3
"""Check an Omni model PR branch against a dev connection environment.

One of three interchangeable variants, see the workflows/omni-change-impact-check
README. Routing is not in this script: it comes from the validator user's
user attribute plus the connection's branch-override toggle, set once at
setup. This script only surfaces that routing and validates, it doesn't set
it, see check_override_toggle().

  OMNI_API_TOKEN      Bearer token for the Omni API (required)
  OMNI_BASE_URL       e.g. https://<your-org>.omniapp.co/api             (required)
  BASE_MODEL_ID       Id of the production shared model                  (required)
  OMNI_CONNECTION_ID  Connection id, from the URL when editing the connection (required)
  BRANCH_NAME         git head branch, resolved to an Omni branch of the same name (required)
  USER_ID             Validator user id, content validation is skipped if unset
  TARGET_ENV          Cosmetic label for logs, the real routing is the user attribute (default: "dev")
  POLL_TIMEOUT        seconds to wait for the schema refresh              (default: 900)
  POLL_INTERVAL       seconds between refresh polls                      (default: 10)

Exit codes:
  0  gate passed
  1  gate failed (no matching branch, semantic or content validation issues)
  2  operational failure (CLI error, refresh timeout)
"""

import json
import os
import subprocess
import sys
import time


class Config:
    def __init__(self):
        self.api_token = require_env("OMNI_API_TOKEN")
        self.base_url = require_env("OMNI_BASE_URL").rstrip("/")
        self.base_model_id = require_env("BASE_MODEL_ID")
        self.connection_id = require_env("OMNI_CONNECTION_ID")
        self.branch_name = require_env("BRANCH_NAME")
        self.user_id = os.environ.get("USER_ID")
        self.target_env = env_or("TARGET_ENV", "dev")
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


def check_override_toggle(cfg):
    """There's no PATCH endpoint for this flag, it's a UI toggle. If it's off,
    the branch silently reads production and every PR passes."""
    out = omni_cli("connections", "list")
    override = None
    for conn in out.get("connections") or []:
        if conn.get("id") == cfg.connection_id:
            override = conn.get("branchConnectionEnvironmentOverridesUserAttr")
            break
    print(f"[info] branchConnectionEnvironmentOverridesUserAttr = {override!r}")
    if override is not True:
        print(f"::warning::Branch override is not 'true', the branch may read "
              f"production instead of '{cfg.target_env}'.")


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


def refresh_and_wait(cfg, branch_model_id):
    # With the override toggle on and the validator user's attribute set to the dev
    # environment, this refresh picks up the dev schema's columns.
    print(f"[info] Refreshing schema (routing to '{cfg.target_env}' via user attribute)...")
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
    print(f"[info] Running semantic model validation against '{cfg.target_env}'...")
    issues = omni_cli("models", "validate", branch_model_id)
    errors = [r for r in issues if not r.get("is_warning", False)]
    print(f"[info] semantic validation: {len(errors)} blocking issue(s)")
    if errors:
        sys.stderr.write(
            f"::error::Semantic validation found {len(errors)} blocking issue(s) against '{cfg.target_env}':\n")
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
            f"::error::Content validation failed: {issues} verified item(s) with issues against '{cfg.target_env}'\n")
        sys.exit(1)


def main():
    cfg = Config()
    check_override_toggle(cfg)
    branch_model_id = resolve_branch_id(cfg)
    refresh_and_wait(cfg, branch_model_id)
    semantic_validate(cfg, branch_model_id)
    content_validate(cfg, branch_model_id)
    print(f"[ok] Gate passed (connection environment '{cfg.target_env}').")


if __name__ == "__main__":
    main()
