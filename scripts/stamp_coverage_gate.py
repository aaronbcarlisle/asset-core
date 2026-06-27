"""CI gate: fail the build if engine stamp coverage is below threshold.

Stripped identity is the one unrecoverable failure mode, so a build that would
ship unstamped assets must not pass. Wire this to your engine adapter in CI:

    from assetcore.sdk.client import AssetcoreClient
    from assetcore.integrations.unreal import UnrealAdapter
    adapter = UnrealAdapter(AssetcoreClient(token="build-token", base_url=...))
    raise SystemExit(run(adapter, threshold=100.0))

The threshold check itself is `run()`, kept import-clean so it's unit-tested.
"""
import sys

from assetcore.app.observability import stamp_coverage_gate


def run(adapter, threshold: float = 100.0) -> int:
    ok, report = stamp_coverage_gate(adapter, threshold)
    status = "OK" if ok else "FAIL"
    print(f"[{status}] stamp coverage: {report['stamped']}/{report['total']} = "
          f"{report['coverage_pct']}% (threshold {threshold}%)")
    return 0 if ok else 1


if __name__ == "__main__":   # pragma: no cover - wired per site
    print("construct your EngineAdapter and call run(adapter); see docs/OPERATIONS.md",
          file=sys.stderr)
    raise SystemExit(2)
