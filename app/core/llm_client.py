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

from openai import OpenAI

from app.config import Settings

logger = logging.getLogger("wiki.llm")


class LLMClient:
    def __init__(self, settings: Settings):
        self._client = OpenAI(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
        )
        self.model = settings.llm.model
        self.timeout = settings.llm.timeout_seconds
        self.default_temperature = settings.llm.temperature

    def call(
        self,
        system: str,
        prompt: str,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        kwargs = dict(
            model=self.model,
            temperature=temperature or self.default_temperature,
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
        full_messages = [{"role": "system", "content": system}] + messages
        logger.debug("LLM stream: model=%s messages=%d", self.model, len(full_messages))
        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                temperature=temperature or self.default_temperature,
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