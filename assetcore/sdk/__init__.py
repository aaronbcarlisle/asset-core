"""L3 — the Adapter SDK: the contract every integration builds on.

The silhouette a tool fits into: AssetcoreClient (the HTTP wrapper), the three
adapter base classes (DCC / Engine / Tracker), the stamping protocol, and the
resolver registry. Writing an integration is filling in a handful of methods.

Firewall (ARCHITECTURE Part 8): this package imports ONLY stdlib + http. It does
NOT import assetcore.core / app / infra / service — it speaks to the running
service over HTTP and traffics in JSON dicts + string UUIDs. That is what keeps
the SDK (and every tool on it) decoupled from how the core is implemented.
"""
