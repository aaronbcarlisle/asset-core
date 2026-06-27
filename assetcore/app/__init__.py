"""L1 — the application layer: the universal verbs.

Each verb orchestrates core rules through the ports and emits an event after a
write. This is the only place rules + persistence meet. Depends on core only.
"""
