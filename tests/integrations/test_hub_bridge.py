# tests/integrations/test_hub_bridge.py
import json
from assetcore.integrations.hub_bridge import main

TOML = """
[hub]
central_url = "http://127.0.0.1:1"
local_cache = "{tmp}/assetcore.db"
outbox_path = "{tmp}/outbox.db"
[hub.scope]
assetcore_project = "MyGame"
[hub.env_templates.dcc_standard]
ASSETCORE_MODE = "hybrid"
[[hub.launch_targets]]
id = "maya"
label = "Maya"
executable = "${{MAYA_BIN}}"
env_template = "dcc_standard"
"""

def _write_cfg(tmp_path):
    cfg = tmp_path / "pipeline.toml"
    cfg.write_text(TOML.format(tmp=tmp_path.as_posix()), encoding="utf-8")
    return str(cfg)

def test_list_targets_reports_unavailable_when_var_unset(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MAYA_BIN", raising=False)
    rc = main(["hub", "list-targets", "--config", _write_cfg(tmp_path)])
    assert rc == 0
    targets = json.loads(capsys.readouterr().out)
    assert targets[0]["id"] == "maya"
    assert targets[0]["available"] is False
    assert "MAYA_BIN" in targets[0]["reason"]

def test_health_emits_json(tmp_path, capsys):
    rc = main(["hub", "health", "--config", _write_cfg(tmp_path)])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["central"] == "down"          # nothing listens on port 1
    assert body["outbox_pending"] == 0

def test_launch_unknown_tool_id_fails(tmp_path, capsys):
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({
        "workspace_root": str(tmp_path), "changelist": "1", "uproject_path": "",
        "depot_path": "//d/", "user_name": "j", "synced_project_folder": "G",
        "pipeline_config_path": _write_cfg(tmp_path)}), encoding="utf-8")
    rc = main(["hub", "launch", "houdini", "--context", str(ctx)])
    assert rc == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False and "houdini" in body["error"]
