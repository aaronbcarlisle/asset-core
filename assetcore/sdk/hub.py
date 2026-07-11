# assetcore/sdk/hub.py
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from typing import Mapping, TypedDict

import httpx

_HEALTH_TIMEOUT = 2.0

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def make_builtins(config_path: str) -> dict:
    return {
        "PIPELINE_ROOT": os.path.dirname(os.path.abspath(config_path)),
        "PATHSEP": os.pathsep,
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", os.path.expanduser("~/.local/share")),
    }

def expand_vars(value: str, env: Mapping[str, str], builtins: Mapping[str, str]) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in builtins:
            return builtins[name]
        if name in env:
            return env[name]
        raise KeyError(name)
    return _VAR_RE.sub(sub, value)

class HubContext(TypedDict):
    workspace_root: str
    changelist: str
    uproject_path: str
    depot_path: str
    user_name: str
    synced_project_folder: str
    pipeline_config_path: str

def load_hub_context(path: str) -> HubContext:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

@dataclass
class LaunchTarget:
    id: str
    label: str
    executable: str          # raw, may contain ${VAR}
    env_template: str
    icon: str = ""
    platform: str | None = None

@dataclass
class PipelineConfig:
    central_url: str
    local_cache: str
    outbox_path: str
    config_path: str
    local_reader_port: int = 8741
    hydrate_on_sync: bool = True
    recent_days: int = 14
    scope: dict = field(default_factory=dict)
    env_templates: dict[str, dict[str, str]] = field(default_factory=dict)
    launch_targets: list[LaunchTarget] = field(default_factory=list)

def load_pipeline_config(path: str, env: Mapping[str, str] | None = None) -> PipelineConfig:
    env = os.environ if env is None else env
    builtins = make_builtins(path)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    hub = data["hub"]
    # required keys expand eagerly: an unset var here is a hard config error
    return PipelineConfig(
        central_url=expand_vars(hub["central_url"], env, builtins),
        local_cache=expand_vars(hub["local_cache"], env, builtins),
        outbox_path=expand_vars(hub["outbox_path"], env, builtins),
        config_path=os.path.abspath(path),
        local_reader_port=int(hub.get("local_reader_port", 8741)),
        hydrate_on_sync=bool(hub.get("hydrate_on_sync", True)),
        recent_days=int(hub.get("recent_days", 14)),
        scope=hub.get("scope", {}),
        env_templates=hub.get("env_templates", {}),
        launch_targets=[LaunchTarget(**t) for t in hub.get("launch_targets", [])],
    )

def resolve_target(target: LaunchTarget, env: Mapping[str, str],
                   builtins: Mapping[str, str]) -> tuple[str | None, str | None]:
    if target.platform and target.platform != sys.platform:
        return None, f"platform {target.platform} != {sys.platform}"
    try:
        exe = expand_vars(target.executable, env, builtins)
    except KeyError as e:
        return None, f"env var {e.args[0]} not set"
    if not os.path.isfile(exe):
        return None, f"executable not found: {exe}"
    return exe, None

def merge_env(pipeline: PipelineConfig, target: LaunchTarget, ctx: HubContext,
              os_env: Mapping[str, str]) -> dict[str, str]:
    builtins = make_builtins(pipeline.config_path)
    merged = dict(os_env)
    template = pipeline.env_templates.get(target.env_template, {})
    for key, raw in template.items():
        merged[key] = expand_vars(raw, os_env, builtins)
    merged["ASSETCORE_PROJECT"] = pipeline.scope.get("assetcore_project", "")
    merged["ASSETCORE_USER"] = ctx["user_name"]
    merged["ASSETCORE_WORKSPACE_ROOT"] = ctx["workspace_root"]
    return merged

def launch_dcc(target: LaunchTarget, pipeline: PipelineConfig, ctx: HubContext,
               os_env: Mapping[str, str] | None = None) -> dict:
    os_env = dict(os.environ) if os_env is None else dict(os_env)
    builtins = make_builtins(pipeline.config_path)
    exe, reason = resolve_target(target, os_env, builtins)
    if exe is None:
        return {"ok": False, "pid": None, "error": reason}
    try:
        env = merge_env(pipeline, target, ctx, os_env)
    except KeyError as e:
        return {"ok": False, "pid": None, "error": f"env var {e.args[0]} not set in template"}
    flags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    proc = subprocess.Popen([exe], env=env, cwd=ctx["workspace_root"], creationflags=flags)
    return {"ok": True, "pid": proc.pid, "error": None}

def _local_reader_identity(pipeline: PipelineConfig) -> dict | None:
    """Return /health payload if something answers on the local port, else None."""
    try:
        r = httpx.get(f"http://127.0.0.1:{pipeline.local_reader_port}/health",
                      timeout=_HEALTH_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def _is_our_reader(pipeline: PipelineConfig, body: dict | None) -> bool:
    if not body or body.get("app") != "assetcore-local-reader":
        return False
    project = pipeline.scope.get("assetcore_project", "")
    return not project or body.get("project") == project

def health_check(pipeline: PipelineConfig) -> dict:
    central = "down"
    try:
        httpx.get(pipeline.central_url.rstrip("/") + "/health",
                  timeout=_HEALTH_TIMEOUT).raise_for_status()
        central = "up"
    except Exception:
        pass
    local = "up" if _is_our_reader(pipeline, _local_reader_identity(pipeline)) else "down"
    from assetcore.sdk.offline import open_outbox, pending_count, failed_count
    conn = open_outbox(pipeline.outbox_path)
    pending, failed = pending_count(conn), failed_count(conn)
    conn.close()
    return {"central": central, "local_reader": local,
            "outbox_pending": pending, "outbox_failed": failed}

def ensure_local_reader(pipeline: PipelineConfig) -> dict:
    body = _local_reader_identity(pipeline)
    if body is not None:
        if _is_our_reader(pipeline, body):
            return {"ok": True, "started": False, "error": None}
        return {"ok": False, "started": False,
                "error": f"port {pipeline.local_reader_port} is occupied by "
                         f"{body.get('app', 'unknown')} (project {body.get('project')}); "
                         f"set local_reader_port in pipeline.toml"}
    flags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    subprocess.Popen(
        ["assetcore", "hub", "serve-local", "--config", pipeline.config_path],
        creationflags=flags)
    for _ in range(20):                      # up to ~10s for cold start
        time.sleep(0.5)
        if _is_our_reader(pipeline, _local_reader_identity(pipeline)):
            return {"ok": True, "started": True, "error": None}
    return {"ok": False, "started": True, "error": "local reader did not become healthy"}
