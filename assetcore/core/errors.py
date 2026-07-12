"""Domain errors the ports may raise — importable by app + infra (both depend
inward on core), so a storage-level condition can be handled as a domain concept
without leaking the backend's exception type upward.
"""
from __future__ import annotations


class VersionConflict(Exception):
    """Two writers raced to add the same facet version number.

    bind_source/bind_runtime compute the next version as max+1 then insert; under
    concurrency two publishers can pick the same number, which the schema's
    ``UNIQUE(asset_id, version_num)`` / ``one_latest_*`` indexes reject. The repos
    raise this typed error instead of a backend-specific IntegrityError so the verb
    can re-read and retry rather than 500.
    """
