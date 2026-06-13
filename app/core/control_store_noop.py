"""
control_store_noop.py — No-op ControlStore implementation for personal modes.
"""

from app.core.control_store import CreditState, UsageEvent, UserRecord, WorkspaceRecord


class NoopControlStore:
    """ControlStore that does nothing — used in personal_local and personal_server modes."""

    def get_user_by_email(self, email: str) -> UserRecord | None:
        return None

    def create_user(self, email: str, password_hash: str) -> UserRecord:
        raise RuntimeError("User creation not available in personal mode")

    def verify_password(self, email: str, password_hash: str) -> UserRecord | None:
        return None

    def password_matches(self, user_id: str, password: str) -> bool:
        return False

    def set_user_admin(self, user_id: str, is_admin: bool) -> None:
        raise RuntimeError("Admin updates not available in personal mode")

    def create_session(self, user_id: str) -> str:
        raise RuntimeError("Sessions not available in personal mode")

    def get_user_by_session_token(self, token: str) -> UserRecord | None:
        return None

    def revoke_session(self, token: str) -> None:
        pass

    def get_default_workspace(self, user_id: str) -> WorkspaceRecord | None:
        return None

    def create_default_workspace(
        self, user_id: str, name: str, slug: str, root_path: str,
    ) -> WorkspaceRecord:
        raise RuntimeError("Workspace creation not available in personal mode")

    def get_credit_state(self, user_id: str) -> CreditState:
        return CreditState(
            daily_limit=0, daily_used=0, daily_remaining=0, daily_reset_at=None,
            welcome_limit=0, welcome_used=0, welcome_remaining=0,
        )

    def consume_tokens(self, user_id: str, amount: int) -> CreditState:
        return self.get_credit_state(user_id)

    def refund_tokens(self, user_id: str, amount: int) -> CreditState:
        return self.get_credit_state(user_id)

    def record_usage(self, event: UsageEvent) -> None:
        pass

    def get_recent_usage(self, user_id: str, limit: int = 20) -> list[dict]:
        return []
