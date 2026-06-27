"""L2 — the service: the only door.

FastAPI exposing each verb as an endpoint, authenticating the caller as an
authority, and fanning out the event spine over SSE. Adds transport + auth only;
all rules live below in app/core.
"""
