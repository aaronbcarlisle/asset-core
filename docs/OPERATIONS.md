# assetcore — Operations Runbook (Phase 8)

Hardening for a studio rollout: how to enforce the firewall, run migrations,
monitor health, gate stamp coverage, and back up / restore the binding DB. The
binding DB is the only thing assetcore owns the bytes of (it stores *pointers*,
never asset bytes — Perforce/git/the engine own those), so its backup is small,
fast, and the one thing that must be recoverable.

## Dependency firewall (the thesis as a build gate)

```
lint-imports          # or: python -c "from importlinter.cli import lint_imports; lint_imports()"
```

Contracts live in `pyproject.toml [tool.importlinter]`: inward-only layering
(service → infra → app → core), the SDK-over-HTTP firewall, and the integrations
firewall. CI must run this; a PR that does `import maya` in `core/` fails here.
The AST source-scan in `tests/contract/test_sdk_firewall.py` is a zero-dependency
backstop that runs in the normal test suite.

## Migrations (Postgres)

The canonical reference DDL is `assetcore/infra/schema.sql`; the *managed* path for
the live database is Alembic (`assetcore/db/migrations`). The migration uses
portable types, so it is verified on sqlite in CI and applied on Postgres in prod:

```
# verify (sqlite, no server):
ASSETCORE_DSN=sqlite:///check.db alembic -c assetcore/db/alembic.ini upgrade head
# production:
ASSETCORE_DSN=postgresql://user:pass@host/assetcore alembic -c assetcore/db/alembic.ini upgrade head
```

> Keep `schema.sql` and the migration in sync (they are two expressions of the
> same five tables). A future cleanup can have `postgres_repo` bootstrap via
> Alembic so there is a single source.

## Observability

`GET /metrics` returns operational health (no auth; scrape it):

- `assets_total`, `lifecycle` mix
- `source_coverage_pct` / `runtime_coverage_pct` — assets with a latest facet
- `provisional_count`, `oldest_provisional_age_seconds` — catch a provisional
  graveyard before it forms (groom via `GET /worklist/provisional`)
- `events_emitted`, `request_count`, `avg_latency_ms`, `max_latency_ms`

Resolve-latency budget is enforced by alerting on `avg/max_latency_ms`.

## Stamp-coverage gate (existential)

Stripped identity is the one unrecoverable failure mode, so a build that would
ship unstamped assets must fail. Wire `scripts/stamp_coverage_gate.py` to your
engine adapter in CI:

```python
from assetcore.sdk.client import AssetcoreClient
from assetcore.integrations.unreal import UnrealAdapter
from scripts.stamp_coverage_gate import run
adapter = UnrealAdapter(AssetcoreClient(token="build-token", base_url=SERVICE_URL))
raise SystemExit(run(adapter, threshold=100.0))   # non-zero exit fails the build
```

## Event delivery (at-least-once + reconnect)

- Each `Event` carries a stable `id`; subscribers dedupe on it, so a redelivery
  after reconnect is harmless (idempotent).
- The durable log is the source of truth; the live push (SSE / Postgres NOTIFY) is
  the low-latency hint. A dropped SSE connection resumes by sending the last seq it
  saw as the `Last-Event-ID` header — the server replays the gap, then follows.
- In production, `infra/notify_sink.NotifySink` is the `EventSink` for the *emit*
  side (durable `event` table + Postgres NOTIFY) — a clean swap for BroadcastSink's
  emit. It does **not** implement the subscribe/stream API, so it does not by
  itself power the `/events` SSE endpoint: that needs a small LISTEN→queue bridge
  process (or keep BroadcastSink for live SSE and NotifySink for the durable
  cross-process log). `/events` returns 501 if handed a non-subscribable sink, so
  the degradation is explicit rather than a runtime break.

## Backup / restore of the binding DB

**Postgres (production):**
```
# backup (nightly; the DB is small — pointers only)
pg_dump --format=custom --file assetcore_$(date +%F).dump "$ASSETCORE_DSN"
# restore into a fresh database
pg_restore --clean --if-exists --dbname "$ASSETCORE_DSN" assetcore_YYYY-MM-DD.dump
```
Recovery objective: identity + relationships are irreplaceable (source/runtime
bytes live in Perforce/the engine and are re-pointable), so prioritize this dump.

**Sqlite (dev/small studio):** the database is a single file — copy it
(`cp assetcore.db backup/`) with the service stopped, or use `sqlite3 .backup`.

## Resolver load test

Resolution (`location_uri` → bytes) is the hot path designers hit. Load-test the
resolver registry against realistic asset counts and concurrent `fetch` calls;
budget p95 fetch latency per scheme (Perforce sync dominates). The registry is
pure routing, so the cost is the backend (`p4 sync`, `git fetch`) — cache/warm
workspaces accordingly.
```
