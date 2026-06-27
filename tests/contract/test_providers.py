"""Provider layer contract (Phase 9) — registry mechanics + config-only swap.

These tests need NO service: the registry and settings layers are pure SDK +
stdlib. The headline is the swap test — the SAME application line
(`settings.tracker("production")`) yields different live providers chosen purely
by a toml string, with secrets pulled from the environment at load.
"""
import textwrap

import pytest

# importing these runs the @providers.register side-effects, so the real provider
# names (shotgrid/jira, sqlite/postgres/memory) exist for the validation tests.
import assetcore.infra._providers      # noqa: F401
import assetcore.integrations._register  # noqa: F401
from assetcore.sdk import providers
from assetcore.sdk.settings import ConfigError, Settings


# --- two fake tracker providers, self-registering under distinct names so they
#     never clash with the real shotgrid/jira registrations -------------------
class _FakeTracker:
    def __init__(self, config, client, name):
        self.config, self.client, self.name = config, client, name
        self.pushed = []

    def push_identity(self, asset_id, fields):
        self.pushed.append((asset_id, fields))


@providers.register("tracker", "fake_shotgrid")
def _make_fake_sg(config, client):
    assert "base_url" in config            # proves config arrived, env-expanded
    return _FakeTracker(config, client, "fake_shotgrid")


@providers.register("tracker", "fake_jira")
def _make_fake_jira(config, client):
    assert "project" in config
    return _FakeTracker(config, client, "fake_jira")


def _load(toml_text, **kw):
    return Settings(__import__("tomllib").loads(textwrap.dedent(toml_text)), **kw)


# --- registry mechanics -----------------------------------------------------
def test_register_build_available():
    cap = "test_only_cap"

    @providers.register(cap, "alpha")
    def _a(config):
        return ("alpha", config)

    @providers.register(cap, "beta")
    def _b(config):
        return ("beta", config)

    assert providers.available(cap) == ["alpha", "beta"]
    assert providers.build(cap, "beta", {"x": 1}) == ("beta", {"x": 1})


def test_unknown_provider_raises_listing_available():
    with pytest.raises(KeyError) as exc:
        providers.build("tracker", "no_such_tracker", {})
    msg = str(exc.value)
    assert "no_such_tracker" in msg and "fake_shotgrid" in msg   # lists what IS available


def test_real_tracker_integrations_register():
    """Importing the integrations makes shotgrid + jira discoverable by name."""
    import assetcore.integrations._register  # noqa: F401 — runs registrations
    assert {"shotgrid", "jira"} <= set(providers.available("tracker"))


# --- the headline: ShotGrid <-> Jira swap is config-only --------------------
def _run_app(settings):
    """The application code that must be byte-identical across both backends."""
    tracker = settings.tracker("production")          # <- identical line in both cases
    tracker.push_identity("uuid-123", {"display_name": "Barrel"})
    return tracker.name, tracker.pushed


SG_TOML = """
    [trackers.production]
    provider = "fake_shotgrid"
    [trackers.production.config]
    base_url = "https://studio.shotgrid.com"
    api_key  = "${TEST_SG_KEY}"
"""
JIRA_TOML = """
    [trackers.production]
    provider = "fake_jira"
    [trackers.production.config]
    base_url  = "https://studio.atlassian.net"
    project   = "PIPE"
    api_token = "${TEST_JIRA_TOK}"
"""


def test_config_only_swap(monkeypatch):
    monkeypatch.setenv("TEST_SG_KEY", "secret-sg")
    monkeypatch.setenv("TEST_JIRA_TOK", "secret-jira")

    name1, pushed1 = _run_app(_load(SG_TOML, client="<client>"))
    name2, pushed2 = _run_app(_load(JIRA_TOML, client="<client>"))

    assert name1 == "fake_shotgrid" and name2 == "fake_jira"   # different live provider
    assert pushed1 == pushed2 == [("uuid-123", {"display_name": "Barrel"})]  # same app behaviour


def test_env_expansion_resolves_secret(monkeypatch):
    monkeypatch.setenv("TEST_SG_KEY", "secret-sg")
    tracker = _load(SG_TOML, client="<client>").tracker("production")
    assert tracker.config["api_key"] == "secret-sg"       # ${...} expanded at load
    assert "${" not in tracker.config["api_key"]          # raw placeholder never reaches factory


def test_secrets_never_live_in_the_file():
    # the config text carries only the ${ENV} reference, never the secret value
    assert "${TEST_SG_KEY}" in SG_TOML
    assert "secret-sg" not in SG_TOML


def test_caching_returns_same_instance(monkeypatch):
    monkeypatch.setenv("TEST_SG_KEY", "secret-sg")
    settings = _load(SG_TOML, client="<client>")
    assert settings.tracker("production") is settings.tracker("production")


def test_missing_instance_raises():
    with pytest.raises(KeyError):
        _load(SG_TOML).tracker("staging")   # only "production" is defined


# --- repo providers: empty path normalizes to :memory: (Bugbot, high) -------
@pytest.mark.parametrize("config", [{}, {"path": ""}])
def test_sqlite_empty_path_falls_back_to_memory(monkeypatch, config):
    """An unset ${ASSETCORE_SQLITE_PATH} expands to "" (settings._expand) and
    reaches the factory as a key-present empty string — it must become :memory:,
    not an anonymous on-disk temp db. Factory gets the post-expansion config."""
    import assetcore.infra._providers as infra_providers

    captured = {}

    class _RecordingSqliteRepo:
        def __init__(self, path, check_same_thread=False):
            captured["path"] = path

    monkeypatch.setattr(infra_providers, "SqliteRepo", _RecordingSqliteRepo)
    from assetcore.sdk import providers
    providers.build("repo", "sqlite", config)
    assert captured["path"] == ":memory:"


def test_settings_expands_unset_sqlite_env_to_empty(monkeypatch):
    """End-to-end: an unset env in the toml expands to "" at load, which the
    factory then normalizes to :memory: (the two fixes compose)."""
    monkeypatch.delenv("ASSETCORE_SQLITE_PATH", raising=False)
    import assetcore.infra._providers  # noqa: F401 — register repo providers
    cfg = __import__("tomllib").loads(
        '[repos.main]\nprovider = "sqlite"\n[repos.main.config]\npath = "${ASSETCORE_SQLITE_PATH}"\n')
    repo = Settings(cfg).repo("main")
    assert repo.__class__.__name__ == "SqliteRepo"   # built cleanly, no crash on ""


def test_sqlite_real_path_passes_through(monkeypatch):
    import assetcore.infra._providers as infra_providers

    captured = {}

    class _RecordingSqliteRepo:
        def __init__(self, path, check_same_thread=False):
            captured["path"] = path

    monkeypatch.setattr(infra_providers, "SqliteRepo", _RecordingSqliteRepo)
    from assetcore.sdk import providers
    providers.build("repo", "sqlite", {"path": "/data/assets.db"})
    assert captured["path"] == "/data/assets.db"


# --- jira field mapping: Jira-correct types (Bugbot, medium) -----------------
def test_jira_taxonomy_maps_to_a_label_list():
    from assetcore.integrations.jira import _RealJiraSite

    data = _RealJiraSite._to_jira_fields(
        {"display_name": "Barrel", "taxonomy": "props/containers/barrel", "status": "active"})
    assert data["summary"] == "Barrel"
    assert data["labels"] == ["props/containers/barrel"]   # a list, not a scalar
    assert "status" not in data                            # status is a transition, not a field


def test_jira_field_mapping_skips_none():
    from assetcore.integrations.jira import _RealJiraSite

    assert _RealJiraSite._to_jira_fields({"display_name": "Barrel"}) == {"summary": "Barrel"}
    assert _RealJiraSite._to_jira_fields({}) == {}


# --- the load-bearing invariant: a built tracker is a VIEW ------------------
def test_tracker_stays_a_view():
    """A TrackerAdapter exposes only identity-facing calls — never path verbs."""
    from assetcore.integrations.shotgrid import ShotGridAdapter

    class _FakeSite:
        def upsert(self, asset_id, fields): ...
        def get(self, external_id): return {}

    adapter = ShotGridAdapter(client=None, site=_FakeSite())
    assert hasattr(adapter, "push_identity") and hasattr(adapter, "pull_identity")
    for forbidden in ("bind_source", "bind_runtime", "relate", "set_binding"):
        assert not hasattr(adapter, forbidden), f"tracker must not expose {forbidden}"


# --- config validation (Phase 10) -------------------------------------------
def test_validate_accepts_a_good_config():
    _load("""
        [repos.main]
        provider = "sqlite"
        [repos.main.config]
        path = ""
    """).validate(["repo"])   # no raise


def test_validate_flags_unknown_provider():
    with pytest.raises(ConfigError) as exc:
        _load("""
            [repos.main]
            provider = "mysql"
        """).validate(["repo"])
    msg = str(exc.value)
    assert "mysql" in msg and "sqlite" in msg   # names the bad one + lists available


def test_validate_flags_missing_required_key():
    # postgres requires "dsn"
    with pytest.raises(ConfigError) as exc:
        _load("""
            [repos.main]
            provider = "postgres"
            [repos.main.config]
            host = "db"
        """).validate(["repo"])
    assert "dsn" in str(exc.value)


def test_validate_flags_unset_env_ref_in_required_key(monkeypatch):
    monkeypatch.delenv("TEST_MISSING_SECRET", raising=False)
    with pytest.raises(ConfigError) as exc:
        _load("""
            [trackers.production]
            provider = "shotgrid"
            [trackers.production.config]
            base_url = "https://x"
            script_name = "assetcore"
            project = "Demo: Game"
            api_key = "${TEST_MISSING_SECRET}"
        """).validate(["tracker"])
    assert "TEST_MISSING_SECRET" in str(exc.value)


def test_validate_allows_empty_env_ref_in_optional_key(monkeypatch):
    # sqlite "path" is optional (-> :memory:), so an unset env ref must NOT raise
    monkeypatch.delenv("TEST_MISSING_PATH", raising=False)
    _load("""
        [repos.main]
        provider = "sqlite"
        [repos.main.config]
        path = "${TEST_MISSING_PATH}"
    """).validate(["repo"])   # no raise


def test_validate_flags_unknown_section():
    with pytest.raises(ConfigError) as exc:
        _load("""
            [reposss.main]
            provider = "sqlite"
        """).validate(["repo"])
    assert "reposss" in str(exc.value)


def test_validate_flags_missing_provider_key():
    with pytest.raises(ConfigError) as exc:
        _load("""
            [repos.main]
            [repos.main.config]
            path = "x"
        """).validate(["repo"])
    assert "provider" in str(exc.value)


def test_validate_capabilities_filter_scopes_checks():
    settings = _load("""
        [repos.main]
        provider = "sqlite"
        [trackers.production]
        provider = "does_not_exist"
    """)
    settings.validate(["repo"])          # tracker problem ignored -> no raise
    with pytest.raises(ConfigError):
        settings.validate(["tracker"])   # now the bad tracker is in scope


def test_validate_aggregates_all_problems():
    with pytest.raises(ConfigError) as exc:
        _load("""
            [repos.main]
            provider = "mysql"
            [repos.other]
            provider = "postgres"
        """).validate(["repo"])
    assert len(exc.value.problems) >= 2   # both the unknown provider AND missing dsn


def test_validate_config_script_returns_0_and_1(tmp_path, monkeypatch):
    from scripts import validate_config

    good = tmp_path / "good.toml"
    good.write_text('[repos.main]\nprovider = "sqlite"\n[repos.main.config]\npath = ""\n')
    assert validate_config.run(str(good)) == 0

    bad = tmp_path / "bad.toml"
    bad.write_text('[repos.main]\nprovider = "nope"\n')
    assert validate_config.run(str(bad)) == 1

    assert validate_config.run(str(tmp_path / "missing.toml")) == 1

    # malformed TOML must fail cleanly (exit 1), not crash with a traceback
    broken = tmp_path / "broken.toml"
    broken.write_text('[repos.main\nprovider = "sqlite"\n')   # unclosed table header
    assert validate_config.run(str(broken)) == 1
