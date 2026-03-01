from __future__ import annotations

import os
from typing import Optional

from openai import AzureOpenAI


def _answer_model() -> str:
    return os.getenv("LLM_MODEL") or os.getenv("AZURE_OPENAI_MODEL") or "gpt-4o-mini"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _get_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        api_version=_required_env("AZURE_OPENAI_API_VERSION"),
    )


def generate_answer(question: str, context: Optional[str]) -> str:
    messages = []
    if context:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Use the following trusted context when relevant. "
                    "If context is insufficient, answer with best effort and say briefly what is missing.\n\n"
                    f"{context}"
                ),
            }
        )
    messages.append({"role": "user", "content": question})

    response = _get_client().chat.completions.create(
        model=_answer_model(),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "800")),
        messages=messages,
    )
    return (response.choices[0].message.content or "").strip()
