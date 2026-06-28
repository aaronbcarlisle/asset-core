# infra — L1 (adapters for the ports)

Concrete implementations of the `core.ports` interfaces: storage repos and event
sinks. Still "inside" the inward stack — no HTTP, no tool names. Selected by config
through the provider registry, never by an import in application code.

## In-memory repo & sink

::: assetcore.infra.inmemory_repo

## SQLite repo

::: assetcore.infra.sqlite_repo

## Postgres repo

::: assetcore.infra.postgres_repo

## Broadcast sink (powers SSE `/events`)

::: assetcore.infra.broadcast_sink

## Notify sink (Postgres LISTEN/NOTIFY)

::: assetcore.infra.notify_sink
