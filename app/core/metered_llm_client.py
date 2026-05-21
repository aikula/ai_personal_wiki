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
    """LLM client that records usage and enforces token quotas."""

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
        # Estimate input tokens
        input_chars = len(system) + len(prompt)
        estimated_input = max(1, int(input_chars / CHARS_PER_TOKEN))
        estimated_total = estimated_input + max_tokens if max_tokens else estimated_input * 2

        # Check quota in multi-user mode
        if self._is_multi_user:
            try:
                self._store.consume_tokens(self._user_id, estimated_total)
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
            # Don't consume tokens if call fails — quota was reserved but not spent
            # In a more sophisticated implementation we'd refund here
            raise

        # Calculate actual usage
        output_tokens = max(1, int(len(response) / CHARS_PER_TOKEN))
        total_tokens = estimated_input + output_tokens

        # Record usage
        if self._is_multi_user:
            try:
                self._record_usage(
                    input_tokens=estimated_input,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                )
            except Exception as exc:
                logger.error("Failed to record LLM usage: %s", exc)
                # In multi-user mode, recording failure is serious
                raise UsageRecordingError(str(exc)) from exc

        return response

    def stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float | None = None,
    ):
        # For streaming, we estimate upfront and record after
        input_chars = len(system) + sum(len(m.get("content", "")) for m in messages)
        estimated_input = max(1, int(input_chars / CHARS_PER_TOKEN))
        estimated_total = estimated_input * 3  # rough estimate for streaming

        if self._is_multi_user:
            try:
                self._store.consume_tokens(self._user_id, estimated_total)
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
            return

        # Record actual usage
        output_text = "".join(full_response)
        output_tokens = max(1, int(len(output_text) / CHARS_PER_TOKEN))
        total_tokens = estimated_input + output_tokens

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
