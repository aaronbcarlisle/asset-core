"""Infrastructure — implementations of the L0 ports.

These satisfy core.ports.AssetRepo / EventSink. Phase 1 ships only the dict-backed
InMemoryRepo (for fast, database-free tests); SqliteRepo / PostgresRepo arrive in
Phase 2 behind the same ports.
"""
