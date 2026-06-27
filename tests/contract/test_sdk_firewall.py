"""The SDK dependency firewall (ARCHITECTURE Part 8).

`sdk` may import only stdlib + http (httpx) — never assetcore.core / app / infra /
service. It talks to the running service over HTTP. This is a source-level check
standing in for the full import-linter contract that lands in Phase 8; it recurses
subpackages and resolves relative imports so `from ..core import x` can't slip by.
"""
import ast
import pathlib

import assetcore.sdk as sdk_pkg

_REPO_ROOT = pathlib.Path(sdk_pkg.__file__).resolve().parents[2]   # dir containing 'assetcore'
_SDK_DIR = pathlib.Path(sdk_pkg.__file__).parent
_FORBIDDEN = ("assetcore.core", "assetcore.app", "assetcore.infra", "assetcore.service")


def _package_parts(path: pathlib.Path) -> list[str]:
    rel = path.resolve().relative_to(_REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    return parts[:-1] if path.name != "__init__.py" else parts   # the file's package


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
            else:   # resolve a relative import to its absolute dotted module
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                resolved = ".".join(base + ([node.module] if node.module else []))
                if resolved:
                    names.add(resolved)
    return names


def _offenders_in(directory: pathlib.Path, allowed_prefix: str) -> dict:
    offenders = {}
    for py in directory.rglob("*.py"):
        bad = {m for m in _imported_modules(py)
               if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN)}
        bad = {m for m in bad if not m.startswith(allowed_prefix)}
        if bad:
            offenders[str(py.relative_to(directory))] = sorted(bad)
    return offenders


def test_sdk_imports_only_stdlib_and_http():
    offenders = _offenders_in(_SDK_DIR, "assetcore.sdk")
    assert offenders == {}, f"SDK reached past the HTTP boundary into the core: {offenders}"
