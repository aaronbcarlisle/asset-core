"""Operator/CI gate: validate a studio's assetcore.toml before it goes live.

    python -m scripts.validate_config [path/to/assetcore.toml]

Validates the WHOLE file (every wired capability): unknown sections, unknown
provider names, missing required config keys, and unset ${ENV} references. Exits 0
if clean, 1 with a listed report otherwise — so a typo'd provider or a forgotten
secret fails fast in CI, not at first use in production.

It imports every provider-registration module first (so provider names are known
to the registry); the config layer itself can't, by the firewall — registration is
always the composition root's job.
"""
import sys
import tomllib

from assetcore.sdk import providers
from assetcore.sdk.settings import ConfigError, Settings


def _register_all() -> None:
    # importing these runs the @providers.register side-effects
    import assetcore.infra._providers      # noqa: F401 — repo providers
    import assetcore.integrations._register  # noqa: F401 — tracker providers


def run(path: str = "assetcore.toml") -> int:
    _register_all()
    try:
        Settings.load(path).validate()
    except FileNotFoundError:
        print(f"[FAIL] config not found: {path}", file=sys.stderr)
        return 1
    except tomllib.TOMLDecodeError as err:
        print(f"[FAIL] {path}: malformed TOML: {err}", file=sys.stderr)
        return 1
    except ConfigError as err:
        print(f"[FAIL] {path}:", file=sys.stderr)
        for problem in err.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"[OK] {path} valid (capabilities checked: {providers.capabilities()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else "assetcore.toml"))
