"""Narrated event-automation demo (in-memory, zero setup):

    python scripts/demo_automation.py

Shows the pattern where asset management becomes pipeline: an EventRouter with
reactive recipes, fed a stream of events. Here the events are synthetic (so no
service is needed); in production `automation.stream_events(url, token)` tails the
live SSE spine and feeds the same router.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assetcore.sdk.automation import EventRouter

router = EventRouter()


@router.on("source.published")
def on_published(ev):
    print(f"  [recipe] source.published {ev['asset_id']} -> notify ShotGrid + queue a cook")


@router.on("identity.claimed")
def on_claimed(ev):
    print(f"  [recipe] identity.claimed {ev['asset_id']} -> mirror name to the tracker")


@router.on("source.relocated")
def on_relocated(ev):
    print(f"  [recipe] source.relocated {ev['asset_id']} -> update downstream references")


@router.on("*")
def audit(ev):
    print(f"  [audit]  {ev['event_type']}")


# a synthetic stream (production: automation.stream_events(url, token))
events = [
    {"event_type": "declared", "asset_id": "a1"},
    {"event_type": "source.published", "asset_id": "a1"},
    {"event_type": "identity.claimed", "asset_id": "a1"},
    {"event_type": "source.relocated", "asset_id": "a1"},
]

print("\n== Event-driven automation: recipes react to the spine ==")
n = router.run(events)
print(f"\n[OK] dispatched {n} events. Studios register handlers; the core never learns the recipe.\n")
