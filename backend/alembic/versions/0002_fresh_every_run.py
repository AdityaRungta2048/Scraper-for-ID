"""every run starts fresh: drop data saved by earlier runs, scope review verdicts per job

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # lookups, profiles, social links and picture hashes saved by earlier runs are never read
    # again; remove them so no earlier profile data stays on disk
    for table in ("api_cache", "social_links", "platform_accounts", "profile_images"):
        op.execute(f"DELETE FROM {table}")
    # verdicts not tied to a job could otherwise never be cleaned up
    op.execute("DELETE FROM manual_reviews WHERE job_id IS NULL")
    with op.batch_alter_table("manual_reviews", schema=None) as batch_op:
        batch_op.drop_constraint("uq_manual_review_pair", type_="unique")
        batch_op.create_unique_constraint(
            "uq_manual_review_pair",
            ["job_id", "source_platform", "source_key", "target_platform", "target_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("manual_reviews", schema=None) as batch_op:
        batch_op.drop_constraint("uq_manual_review_pair", type_="unique")
        batch_op.create_unique_constraint(
            "uq_manual_review_pair", ["source_platform", "source_key", "target_platform", "target_key"]
        )
