"""F3.5: BetterAuth core tables (user/session/account/verification).

Revision ID: 004
Revises: 003
Create Date: 2026-05-04

Schema mirrors the BetterAuth canonical schema (see better-auth.com/docs/concepts/database).
Tables are quoted (`"user"` etc.) because BetterAuth uses singular names and
`user` is a Postgres reserved keyword.

The existing tables in F0–F3 (`journal_trades.user_id`, `alert_rules.user_id`, …)
hold `user_id text DEFAULT 'me'`. This migration does NOT add FK constraints to
`"user".id` yet — first the data migration script `scripts/migrate_me_to.py`
moves all 'me' rows to the real first-user id, then a follow-up migration in F4
can add the FKs cleanly.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE "user" (
            id              text PRIMARY KEY,
            email           text NOT NULL UNIQUE,
            "emailVerified" boolean NOT NULL DEFAULT false,
            name            text NOT NULL,
            image           text,
            "createdAt"     timestamptz NOT NULL DEFAULT now(),
            "updatedAt"     timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE session (
            id              text PRIMARY KEY,
            token           text NOT NULL UNIQUE,
            "userId"        text NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            "expiresAt"     timestamptz NOT NULL,
            "ipAddress"     text,
            "userAgent"     text,
            "createdAt"     timestamptz NOT NULL DEFAULT now(),
            "updatedAt"     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX session_user_idx ON session (\"userId\")")
    op.execute("CREATE INDEX session_token_idx ON session (token)")

    op.execute(
        """
        CREATE TABLE account (
            id                       text PRIMARY KEY,
            "accountId"              text NOT NULL,
            "providerId"             text NOT NULL,
            "userId"                 text NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            "accessToken"            text,
            "refreshToken"           text,
            "idToken"                text,
            "accessTokenExpiresAt"   timestamptz,
            "refreshTokenExpiresAt"  timestamptz,
            scope                    text,
            password                 text,
            "createdAt"              timestamptz NOT NULL DEFAULT now(),
            "updatedAt"              timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX account_user_idx ON account (\"userId\")")
    op.execute(
        "CREATE UNIQUE INDEX account_provider_account_idx "
        "ON account (\"providerId\", \"accountId\")"
    )

    op.execute(
        """
        CREATE TABLE verification (
            id           text PRIMARY KEY,
            identifier   text NOT NULL,
            value        text NOT NULL,
            "expiresAt"  timestamptz NOT NULL,
            "createdAt"  timestamptz NOT NULL DEFAULT now(),
            "updatedAt"  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX verification_identifier_idx ON verification (identifier)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification CASCADE")
    op.execute("DROP TABLE IF EXISTS account CASCADE")
    op.execute("DROP TABLE IF EXISTS session CASCADE")
    op.execute('DROP TABLE IF EXISTS "user" CASCADE')
