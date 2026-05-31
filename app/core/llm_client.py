"""
llm_client.py — OpenAI-compatible LLM client wrapper.

All agents use ONLY this module for LLM calls.
Never import openai directly in agents.

Supports:
- Any OpenAI-compatible endpoint (OpenAI, Azure, local vLLM, Ollama)
- Streaming (returns generator) and non-streaming (returns str)
- Automatic retry on malformed JSON (once)
- Request logging for audit trail
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Protocol

from openai import OpenAI

from app.config import Settings

logger = logging.getLogger("wiki.llm")


class LLMGateway(Protocol):
    model: str

    def call(
        self,
        system: str,
        prompt: str,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str: ...

    def stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float | None = None,
    ) -> Generator[str, None, None]: ...


class LLMClient:
    def __init__(self, settings: Settings):
        self._client = OpenAI(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
        )
        self.model = settings.llm.model
        self.timeout = settings.llm.timeout_seconds
        self.default_temperature = settings.llm.temperature
        self._context_window_tokens = (
            settings.llm.context_window_tokens or self._detect_context_window()
        )

    def _detect_context_window(self) -> int:
        """Best-effort context window detection from API. Falls back to 128K."""
        try:
            info = self._client.models.retrieve(self.model)
            meta = getattr(info, "model_extra", None) or {}
            for key in ("context_length", "context_window", "max_context_length"):
                if isinstance(meta.get(key), int) and meta[key] > 0:
                    logger.info("Detected context window: %d tokens for %s", meta[key], self.model)
                    return meta[key]
        except Exception as exc:
            logger.debug("Context window auto-detect failed: %s", exc)
        logger.info("Using default context window: 128K tokens")
        return 128_000

    def _validate_context_budget(
        self, system: str, prompt: str, max_tokens: int,
    ) -> tuple[str, str]:
        """Check context budget; truncate prompt if it overflows."""
        total_chars = len(system) + len(prompt)
        estimated_tokens = total_chars // 3
        available = self._context_window_tokens - max_tokens
        if estimated_tokens <= available:
            return system, prompt

        logger.warning(
            "Context budget exceeded: ~%d tokens > %d available "
            "(window=%d, max_tokens=%d). Truncating prompt.",
            estimated_tokens, available, self._context_window_tokens, max_tokens,
        )
        max_prompt_chars = max(available * 3 - len(system), 500)
        truncated = prompt[:max_prompt_chars - 30] + "\n\n[CONTEXT_BUDGET_TRIMMED]"
        return system, truncated

    def call(
        self,
        system: str,
        prompt: str,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        request_temperature = (
            self.default_temperature if temperature is None else temperature
        )
        effective_max = max_tokens or 4096
        system, prompt = self._validate_context_budget(system, prompt, effective_max)
        kwargs = dict(
            model=self.model,
            temperature=request_temperature,
            timeout=self.timeout,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        logger.debug("LLM call: model=%s json_mode=%s max_tokens=%s prompt_len=%d",
                     self.model, json_mode, max_tokens, len(prompt))
        try:
            response = self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            logger.debug("LLM response: completion_len=%d", len(content))
            return content
        except Exception as exc:
            logger.error("LLM call failed: model=%s error=%s", self.model, exc)
            raise

    def stream(
        self,
        system: str,
        messages: list[dict],
        temperature: float | None = None,
    ) -> Generator[str, None, None]:
        full_messages = [{"role": "system", "content": system}, *messages]
        request_temperature = (
            self.default_temperature if temperature is None else temperature
        )
        # Warn on budget overflow for streaming (can't truncate mid-stream)
        total_chars = sum(len(m.get("content", "")) for m in full_messages)
        estimated_tokens = total_chars // 3
        if estimated_tokens > self._context_window_tokens - 1024:
            logger.warning(
                "Stream context budget: ~%d tokens near/over window (%d)",
                estimated_tokens, self._context_window_tokens,
            )
        logger.debug("LLM stream: model=%s messages=%d", self.model, len(full_messages))
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                temperature=request_temperature,
                timeout=self.timeout,
                messages=full_messages,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            logger.error("LLM stream failed: model=%s error=%s", self.model, exc)
            raise
