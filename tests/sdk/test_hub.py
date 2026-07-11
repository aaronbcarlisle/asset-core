# tests/sdk/test_hub.py
import os
import pytest
from assetcore.sdk.hub import expand_vars, make_builtins, load_pipeline_config, load_hub_context

MINIMAL_TOML = """
[hub]
central_url = "${AC_URL}"
local_cache = "/tmp/cache.db"
outbox_path = "/tmp/outbox.db"
[hub.scope]
assetcore_project = "MyGame"
[hub.env_templates.dcc_standard]
ASSETCORE_MODE = "hybrid"
[[hub.launch_targets]]
id = "maya"
label = "Maya"
executable = "${MAYA_BIN}"
env_template = "dcc_standard"
"""

def test_expand_vars_builtin_wins_over_env():
    out = expand_vars("${PATHSEP}", {"PATHSEP": "WRONG"}, {"PATHSEP": os.pathsep})
    assert out == os.pathsep

def test_expand_vars_unset_raises_with_name():
    with pytest.raises(KeyError, match="NOPE"):
        expand_vars("${NOPE}/bin", {}, {})

def test_load_config_expands_required_keys(tmp_path):
    cfg = tmp_path / "pipeline.toml"
    cfg.write_text(MINIMAL_TOML, encoding="utf-8")
    pipeline = load_pipeline_config(str(cfg), env={"AC_URL": "http://central:8080"})
    assert pipeline.central_url == "http://central:8080"
    assert pipeline.local_reader_port == 8741            # default
    assert pipeline.launch_targets[0].id == "maya"
    assert pipeline.launch_targets[0].executable == "${MAYA_BIN}"  # raw: lazy expansion

def test_load_config_unset_required_key_errors(tmp_path):
    cfg = tmp_path / "pipeline.toml"
    cfg.write_text(MINIMAL_TOML, encoding="utf-8")
    with pytest.raises(KeyError, match="AC_URL"):
        load_pipeline_config(str(cfg), env={})

def test_load_hub_context(tmp_path):
    ctx_file = tmp_path / "ctx.json"
    ctx_file.write_text(
        '{"workspace_root": "D:/p4/MyGame", "changelist": "123", '
        '"uproject_path": "D:/p4/MyGame/My.uproject", "depot_path": "//MyGame/Main/", '
        '"user_name": "jsmith", "synced_project_folder": "MyGame", '
        '"pipeline_config_path": "D:/p4/MyGame/Pipeline/pipeline.toml"}',
        encoding="utf-8")
    ctx = load_hub_context(str(ctx_file))
    assert ctx["user_name"] == "jsmith"

from assetcore.sdk.hub import LaunchTarget, PipelineConfig, resolve_target, launch_dcc

def _pipeline(tmp_path):
    return PipelineConfig(
        central_url="http://central:8080", local_cache=str(tmp_path / "c.db"),
        outbox_path=str(tmp_path / "o.db"), config_path=str(tmp_path / "pipeline.toml"),
        scope={"assetcore_project": "MyGame"},
        env_templates={"dcc_standard": {"ASSETCORE_MODE": "hybrid",
                                        "ASSETCORE_LOCAL_URL": "http://127.0.0.1:8741"}},
    )

def _ctx(tmp_path):
    return {"workspace_root": str(tmp_path), "changelist": "1", "uproject_path": "",
            "depot_path": "//d/", "user_name": "jsmith", "synced_project_folder": "G",
            "pipeline_config_path": str(tmp_path / "pipeline.toml")}

def test_resolve_target_unset_var_is_unavailable():
    t = LaunchTarget(id="maya", label="Maya", executable="${MAYA_BIN}", env_template="dcc_standard")
    exe, reason = resolve_target(t, env={}, builtins={})
    assert exe is None and "MAYA_BIN" in reason

def test_resolve_target_wrong_platform():
    t = LaunchTarget(id="ps", label="PS", executable="x", env_template="dcc_standard", platform="notreal")
    exe, reason = resolve_target(t, env={}, builtins={})
    assert exe is None and "platform" in reason

def test_launch_dcc_merges_env(monkeypatch, tmp_path):
    exe = tmp_path / "maya.exe"
    exe.write_text("stub")
    captured = {}
    class FakePopen:
        pid = 999
        def __init__(self, args, env=None, **kw):
            captured["args"], captured["env"] = args, env
    monkeypatch.setattr("assetcore.sdk.hub.subprocess.Popen", FakePopen)
    t = LaunchTarget(id="maya", label="Maya", executable="${MAYA_BIN}", env_template="dcc_standard")
    result = launch_dcc(t, _pipeline(tmp_path), _ctx(tmp_path), os_env={"MAYA_BIN": str(exe)})
    assert result == {"ok": True, "pid": 999, "error": None}
    assert captured["env"]["ASSETCORE_MODE"] == "hybrid"
    assert captured["env"]["ASSETCORE_USER"] == "jsmith"
    assert captured["args"][0] == str(exe)

def test_launch_dcc_unavailable_returns_error(tmp_path):
    t = LaunchTarget(id="maya", label="Maya", executable="${MAYA_BIN}", env_template="dcc_standard")
    result = launch_dcc(t, _pipeline(tmp_path), _ctx(tmp_path), os_env={})
    assert result["ok"] is False and "MAYA_BIN" in result["error"]
