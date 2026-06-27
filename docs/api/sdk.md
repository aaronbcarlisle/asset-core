# sdk — L3 (the contract)

What integrations are allowed to touch: the HTTP client, the CLI, the DCC adapter
base, the production tools, event automation, URI resolvers, and the config/provider
machinery. This layer talks to the system **only over HTTP** — it imports nothing
from `core`/`app`/`infra`/`service` (firewall-enforced).

## HTTP client

::: assetcore.sdk.client

## CLI

::: assetcore.sdk.cli

## DCC adapter base

::: assetcore.sdk.dcc_adapter

## Production tools

::: assetcore.sdk.tools

## Event-driven automation

::: assetcore.sdk.automation

## Resolvers (URI → bytes)

::: assetcore.sdk.resolvers

## Provider registry

::: assetcore.sdk.providers

## Settings (config)

::: assetcore.sdk.settings
