# service — L2 (the only door)

The FastAPI surface: the app factory (composition root), one route per verb plus the
SSE event stream, the pydantic wire schemas, and token→authority auth. This layer
only translates HTTP ↔ service calls and maps domain errors to status codes — every
rule lives below.

## App factory

::: assetcore.service.app

## Routes

::: assetcore.service.routes

## Wire schemas

::: assetcore.service.schemas

## Auth (authority enforcement)

::: assetcore.service.auth

## Event stream (SSE)

::: assetcore.service.events
