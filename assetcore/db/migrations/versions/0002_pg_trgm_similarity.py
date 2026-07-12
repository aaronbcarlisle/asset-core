"""pg_trgm GIN indexes for search_candidates (the dedupe-nudge narrowing)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12

Postgres-only: pg_trgm + GIN trigram indexes on facet_identity.display_name and
.taxonomy accelerate the ILIKE-per-token candidate query in
PostgresRepo.search_candidates. On sqlite this is a no-op (the sqlite repo keeps
its own FTS5 index locally). Correctness never depends on these — without them
the same queries run unindexed.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS identity_name_trgm"
               " ON facet_identity USING gin (display_name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS identity_taxonomy_trgm"
               " ON facet_identity USING gin (taxonomy gin_trgm_ops)")


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS identity_taxonomy_trgm")
    op.execute("DROP INDEX IF EXISTS identity_name_trgm")
    # the extension is left installed: other objects may use it, and dropping it
    # needs privileges the migration role may not have.
