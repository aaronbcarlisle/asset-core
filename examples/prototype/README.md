# The prototype seed (frozen reference)

This folder is the **original single-file prototype** that seeded assetcore, kept
for reference. It is **not** the product and is not imported by the `assetcore`
package or exercised by the test suite.

```
api.py          the prototype verbs over a backend-agnostic db handle
connection.py   SqliteDB / PostgresDB (translates the PG schema to sqlite on the fly)
schema.sql      the prototype schema (old column names: depot_path, dcc, p4_changelist)
demo.py         the narrated 3-scenario walkthrough on the prototype
```

Run it:

```bash
python examples/prototype/demo.py
```

## Where it went

The layered product is the `assetcore/` package. The prototype maps onto it like so:

| Prototype (here) | Product (maintained) |
|---|---|
| `api.py` free functions taking a `db` | `assetcore/app/verbs.py` over `core.ports.AssetRepo` |
| `connection.py` `SqliteDB`/`PostgresDB` | `assetcore/infra/sqlite_repo.py`, `postgres_repo.py` |
| `schema.sql` (depot_path/dcc/p4_changelist) | `assetcore/infra/schema.sql` (location_uri/tool/revision) |
| `demo.py` | the repo-root `demo.py` (layered stack, in-memory) |
| — | `assetcore/service` (FastAPI), `assetcore/sdk` (client/CLI/adapters) |

The column renames (`depot_path`/`engine_path` → `location_uri`, `dcc` → `tool`,
`p4_changelist:int` → `revision:str`) are the tool-agnostic abstraction the product
settled on. See `docs/ARCHITECTURE.md` Appendix A for the full mapping.
