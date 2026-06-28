# API Reference

This section is **generated from the source docstrings** by
[mkdocstrings](https://mkdocstrings.github.io/) — it rebuilds whenever the code
changes (`mkdocs serve` live-reloads; `mkdocs build` regenerates `site/`). It is the
authoritative signature reference; for *how to use* the API in context, see the
[Cookbook](../COOKBOOK.md), and for the design rationale, the
[Architecture](../ARCHITECTURE.md).

The package is layered, with dependencies pointing **inward only** (enforced by the
import firewall — see [Development Guide](../DEVELOPMENT.md#5-the-dependency-firewall-the-thesis-as-a-build-gate)):

| Layer | Page | Responsibility |
|---|---|---|
| **L0 core** | [core](core.md) | pure domain — entities, types, rules, ports. No I/O, no tool names. |
| **L1 app** | [app](app.md) | the universal verbs + the service facade + observability. |
| **L1 infra** | [infra](infra.md) | adapters for the ports — repos (memory/sqlite/postgres) and event sinks. |
| **L2 service** | [service](service.md) | the only door — FastAPI app, routes, wire schemas, auth. |
| **L3 sdk** | [sdk](sdk.md) | the contract — HTTP client, CLI, adapter bases, tools, automation, resolvers, providers. |
| **L4 integrations** | [integrations](integrations.md) | disposable tool translators — Maya, Max, Blender, Substance, Unreal, Photoshop, ShotGrid, Jira. |

Private members (leading underscore) are hidden by default. The reference reflects
the layered package under `assetcore/`; the original single-file prototype
(`assetcore/api.py`) is intentionally excluded.
