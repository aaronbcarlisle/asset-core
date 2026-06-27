"""assetcore — identity-first asset management for production pipelines.

The one idea: an asset is an immutable IDENTITY; three sovereign FACETS
(identity/source/runtime) hang off it, bound by a shared UUID. Nothing is
inferred; each authority writes only its own facet. See docs/DESIGN.md.
"""
from . import api  # noqa: F401
from .db.connection import get_db, SqliteDB, PostgresDB  # noqa: F401

__version__ = "0.1.0"
