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
