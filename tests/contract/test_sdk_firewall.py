"""The SDK + integrations dependency firewall (ARCHITECTURE Part 8).

`sdk` may import only stdlib + http (never the core); `integrations` may import only
`sdk`. This source-level check is the zero-dependency backstop for the full
import-linter contract (Phase 8). It recurses subpackages, resolves relative
imports, and flags ANY internal `assetcore.*` import outside the allowed prefix
(so e.g. importing the prototype `assetcore.api` fails too).
"""
import ast
import pathlib

import assetcore.integrations as integrations_pkg
import assetcore.sdk as sdk_pkg

_REPO_ROOT = pathlib.Path(sdk_pkg.__file__).resolve().parents[2]   # dir containing 'assetcore'
_SDK_DIR = pathlib.Path(sdk_pkg.__file__).parent
_INTEGRATIONS_DIR = pathlib.Path(integrations_pkg.__file__).parent


def _package_parts(path: pathlib.Path) -> list[str]:
    rel = path.resolve().relative_to(_REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    return parts[:-1] if path.name != "__init__.py" else parts


def _imported_modules(path: pathlib.Path) -> set[str]:
    pkg_parts = _package_parts(path)
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    names.add(node.module)
            else:   # resolve relative imports to their absolute dotted module
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                resolved = ".".join(base + ([node.module] if node.module else []))
                if resolved:
                    names.add(resolved)
    return names


def _offenders_in(directory: pathlib.Path, allowed_prefix: str) -> dict:
    offenders = {}
    for py in directory.rglob("*.py"):
        bad = {m for m in _imported_modules(py)
               if m.startswith("assetcore.")
               and not (m == allowed_prefix or m.startswith(allowed_prefix + "."))}
        if bad:
            offenders[str(py.relative_to(directory))] = sorted(bad)
    return offenders


def test_sdk_imports_only_stdlib_and_http():
    offenders = _offenders_in(_SDK_DIR, "assetcore.sdk")
    assert offenders == {}, f"SDK reached past the HTTP boundary into the core: {offenders}"


def test_integrations_import_only_the_sdk():
    offenders = _offenders_in(_INTEGRATIONS_DIR, "assetcore.sdk")
    assert offenders == {}, f"an integration reached past the SDK into the core: {offenders}"
