"""The SDK dependency firewall (ARCHITECTURE Part 8).

`sdk` may import only stdlib + http (httpx) — never assetcore.core / app / infra /
service. It talks to the running service over HTTP. This is a source-level check
standing in for the full import-linter contract that lands in Phase 8.
"""
import ast
import pathlib

import assetcore.integrations as integrations_pkg
import assetcore.sdk as sdk_pkg

_SDK_DIR = pathlib.Path(sdk_pkg.__file__).parent
_INTEGRATIONS_DIR = pathlib.Path(integrations_pkg.__file__).parent
_FORBIDDEN = ("assetcore.core", "assetcore.app", "assetcore.infra", "assetcore.service")


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _offenders_in(directory: pathlib.Path, allowed_prefix: str) -> dict:
    offenders = {}
    for py in directory.glob("*.py"):
        bad = {m for m in _imported_modules(py)
               if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN)}
        bad = {m for m in bad if not m.startswith(allowed_prefix)}
        if bad:
            offenders[py.name] = sorted(bad)
    return offenders


def test_sdk_imports_only_stdlib_and_http():
    # the SDK may import its own submodules (assetcore.sdk.*) and nothing else internal
    offenders = _offenders_in(_SDK_DIR, "assetcore.sdk")
    assert offenders == {}, f"SDK reached past the HTTP boundary into the core: {offenders}"


def test_integrations_import_only_the_sdk():
    # L4 integrations may import assetcore.sdk.* (and external tool modules), never
    # core/app/infra/service.
    offenders = _offenders_in(_INTEGRATIONS_DIR, "assetcore.sdk")
    assert offenders == {}, f"an integration reached past the SDK into the core: {offenders}"
