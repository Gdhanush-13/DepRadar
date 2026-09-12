"""Store unresolved dependency and snapshot resolution state."""
from sqlalchemy import Column, Integer, String

from alembic import op

revision = "0004_scan_hardening"
down_revision = "0003_osv_severity"


def upgrade():
    op.add_column("scans", Column("unresolved_count", Integer(), server_default="0"))
    op.add_column("dependency_snapshots", Column("resolution_status", String(30), server_default="resolved"))


def downgrade():
    op.drop_column("dependency_snapshots", "resolution_status")
    op.drop_column("scans", "unresolved_count")
