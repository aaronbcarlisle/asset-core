# assetcore/sdk/hub.py
from __future__ import annotations
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import Mapping, TypedDict

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
