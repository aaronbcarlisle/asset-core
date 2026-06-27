"""The three scenarios + invariants against InMemoryRepo — no database, microseconds.

The scenario bodies live in tests/scenarios_common.py so the exact same assertions
also run against the SQL backends in tests/integration/test_backends.py. This file
is the no-I/O (unit) execution of that shared suite.
"""
import pytest

from assetcore.infra.inmemory_repo import InMemoryRepo, InMemorySink
from tests.scenarios_common import ALL_SCENARIOS


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_scenario_in_memory(scenario):
    scenario(InMemoryRepo(), InMemorySink())
