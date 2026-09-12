"""Store OSV severity alongside vulnerability identifiers."""
from alembic import op
from sqlalchemy import Column, JSON

revision = "0003_osv_severity"
down_revision = "0002_scoring_transparency"


def upgrade():
    op.add_column("dependency_snapshots", Column("cve_severities", JSON(), server_default="[]"))


def downgrade():
    op.drop_column("dependency_snapshots", "cve_severities")
