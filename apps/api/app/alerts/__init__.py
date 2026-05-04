"""F3 alert engine.

Three layers:
  - `dsl`        : Pydantic schema for the rule spec (jsonb-stored).
  - `evaluator`  : pure functions over an indicator panel — testable without DB.
  - `runtime`    : long-lived asyncio task (started by FastAPI lifespan) that
                   subscribes to Valkey market channels, evaluates active rules
                   on every closed candle, and fans out hits via Valkey to
                   `/ws/alerts` subscribers. Bias auto-promotion via Postgres
                   LISTEN/NOTIFY (`notify_bias_high` trigger from migration 003).

The agent never invokes the runtime; it only writes/reads through the chat
tools (`create_alert`, `list_alerts`, `delete_alert`) and the REST endpoints
in `app.api.alerts`.
"""

from app.alerts.dsl import Condition, Operator, RuleSpec
from app.alerts.evaluator import evaluate_rule

__all__ = ["Condition", "Operator", "RuleSpec", "evaluate_rule"]
