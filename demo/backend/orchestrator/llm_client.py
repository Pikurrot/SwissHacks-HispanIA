from __future__ import annotations

import os
from typing import Protocol

import requests
from dotenv import load_dotenv

from .errors import LLMClientError


class LLMClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class PhoeniqsLLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 45,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("PHOENIQS_API_KEY", "")
        self.api_url = (api_url or os.getenv("PHOENIQS_API_URL", "")).rstrip("/")
        self.model = model or os.getenv("PHOENIQS_MODEL", "inference-gpt-oss-120b")
        self.timeout_seconds = timeout_seconds
        if not self.api_key or not self.api_url:
            raise LLMClientError("PHOENIQS_API_KEY and PHOENIQS_API_URL are required")

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = requests.post(
                f"{self.api_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.25,
                    "max_tokens": 2200,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload.get("choices", [{}])[0].get("message", {}).get("content")
            if not content:
                raise LLMClientError("LLM returned empty content")
            return str(content)
        except requests.RequestException as exc:
            raise LLMClientError(f"LLM request failed: {exc}") from exc
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMClientError(f"Unexpected LLM response: {exc}") from exc


class StaticLLMClient:
    """Deterministic client for tests and local integration work."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response
