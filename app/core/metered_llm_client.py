"""
metered_llm_client.py — Token-metered LLM client wrapper.

Wraps LLMClient to:
1. Estimate input tokens before call
2. Check quota before call (multi-user mode only)
3. Call underlying LLMClient
4. Read provider usage if available
5. Record usage_events
6. Consume tokens from credit buckets

Personal modes: metering is a no-op pass-through.
Multi-user mode: full metering with quota enforcement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings
    from app.core.control_store import ControlStore
    from app.core.llm_client import LLMClient

logger = logging.getLogger("wiki.metered_llm")

# Approximate chars per token for estimation
CHARS_PER_TOKEN = 3.5


@dataclass
class LLMUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


class MeteredLLMClient:
    """LLM client that records usage and enforces token quotas.

    Billing: charges only output tokens (generated content).
    Logging: records both input and output tokens in usage_events.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        settings: Settings,
        control_store: ControlStore | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        operation: str = "chat",
    ):
        self._llm = llm_client
        self._settings = settings
        self._store = control_store
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._operation = operation

    @property
    def is_reasoning_model(self) -> bool:
        """Check if current model is a reasoning model (o1, o3, deepseek-r1, etc.)."""
        name = self._llm.model.lower()
        return any(tag in name for tag in ("o1", "o3", "o4", "reason", "r1", "deepseek-r"))

    @property
    def model(self) -> str:
        return self._llm.model

    def call(
        self,
        system: str,
        prompt: str,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        # Estimate tokens
        input_chars = len(system) + len(prompt)
        estimated_input = max(1, int(input_chars / CHARS_PER_TOKEN))
        # Billing: reserve based on estimated output tokens only
        estimated_output = max_tokens or max(1, estimated_input)
        billed_estimate = estimated_output
        # Reasoning models: apply budget multiplier for thinking tokens
        if self.is_reasoning_model:
            billed_estimate = int(billed_estimate * 1.5)

        # Reserve quota in multi-user mode before provider call
        if self._is_multi_user:
            try:
                self._store.consume_tokens(self._user_id, billed_estimate)
            except Exception as exc:
                from app.core.control_store import InsufficientCreditsError
                if isinstance(exc, InsufficientCreditsError):
                    raise QuotaExceededError(
                        required=exc.required, available=exc.available,
                    ) from exc
                raise

        # Make the actual LLM call
        try:
            response = self._llm.call(
                system=system, prompt=prompt,
                temperature=temperature, json_mode=json_mode, max_tokens=max_tokens,
            )
        except Exception:
            if self._is_multi_user:
                self._store.refund_tokens(self._user_id, billed_estimate)
            raise

        # Calculate actual usage
        output_tokens = max(1, int(len(response) / CHARS_PER_TOKEN))
        total_tokens = estimated_input + output_tokens

        if self._is_multi_user:
            self._reconcile_reserved_tokens(billed_estimate, output_tokens)

        # Record usage (both input and output for analytics)
        if self._is_multi_user:
            try:
                self._record_usage(
                    input_tokens=estimated_input,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                )
            except Exception as exc:
                logger.error("Failed to record LLM usage: %s", exc)
                raise UsageRecordingError(str(exc)) from exc

        return response

    def stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float | None = None,
    ):
        # For streaming, estimate output and reserve based on that
        input_chars = len(system) + sum(len(m.get("content", "")) for m in messages)
        estimated_input = max(1, int(input_chars / CHARS_PER_TOKEN))
        # Billing: reserve based on estimated output (rough: input size as output estimate)
        billed_estimate = max(1, estimated_input)
        if self.is_reasoning_model:
            billed_estimate = int(billed_estimate * 1.5)

        if self._is_multi_user:
            try:
                self._store.consume_tokens(self._user_id, billed_estimate)
            except Exception as exc:
                from app.core.control_store import InsufficientCreditsError
                if isinstance(exc, InsufficientCreditsError):
                    raise QuotaExceededError(
                        required=exc.required, available=exc.available,
                    ) from exc
                raise

        # Stream the response
        full_response = []
        try:
            for chunk in self._llm.stream(system=system, messages=messages, temperature=temperature):
                full_response.append(chunk)
                yield chunk
        except Exception:
            if self._is_multi_user:
                self._store.refund_tokens(self._user_id, billed_estimate)
            raise

        # Record actual usage
        output_text = "".join(full_response)
        output_tokens = max(1, int(len(output_text) / CHARS_PER_TOKEN))
        total_tokens = estimated_input + output_tokens

        if self._is_multi_user:
            self._reconcile_reserved_tokens(billed_estimate, output_tokens)

        if self._is_multi_user:
            try:
                self._record_usage(
                    input_tokens=estimated_input,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                )
            except Exception as exc:
                logger.error("Failed to record streaming LLM usage: %s", exc)

    @property
    def _is_multi_user(self) -> bool:
        return (
            self._settings.app_mode == "multi_user"
            and self._store is not None
            and self._user_id is not None
        )

    def _record_usage(
        self, input_tokens: int, output_tokens: int, total_tokens: int,
    ) -> None:
        from app.core.control_store import UsageEvent
        event = UsageEvent(
            user_id=self._user_id,
            workspace_id=self._workspace_id or "",
            operation=self._operation,
            model=self._llm.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            is_estimated=True,
        )
        self._store.record_usage(event)
        logger.info(
            "Usage: model=%s in=%d out=%d total=%d billed=%d reason=%s op=%s",
            self._llm.model, input_tokens, output_tokens, total_tokens,
            output_tokens,  # billing is output-only
            self.is_reasoning_model, self._operation,
        )

    def _reconcile_reserved_tokens(self, reserved_tokens: int, actual_tokens: int) -> None:
        delta = reserved_tokens - actual_tokens
        if delta > 0:
            self._store.refund_tokens(self._user_id, delta)
        elif delta < 0:
            self._store.consume_tokens(self._user_id, -delta)


class QuotaExceededError(Exception):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(
            f"Token quota exceeded: need {required}, have {available} remaining. "
            f"Try again after daily reset or contact support."
        )


class UsageRecordingError(Exception):
    pass
