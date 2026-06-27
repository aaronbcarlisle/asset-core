"""Run the real import-linter contracts as part of the suite (ARCHITECTURE Part 8).

This is the canonical dependency-firewall gate; the AST source-scan in
test_sdk_firewall.py is the zero-dependency backstop. Skips if import-linter
isn't installed (it's in the dev extra).
"""
import subprocess
import sys

import pytest

pytest.importorskip("importlinter")


def test_import_linter_contracts_are_kept():
    # invoke via a clean subprocess so click parses no pytest argv
    result = subprocess.run(
        [sys.executable, "-c", "from importlinter.cli import lint_imports; lint_imports()"],
        capture_output=True, text=True,
    )
    # the exit code is the gate; CLI text/format can drift across versions, and a
    # broken contract reports "broken" rather than failing to run, so guard both.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "broken" not in result.stdout.lower() or "0 broken" in result.stdout.lower()
