from __future__ import annotations

import os

from openai import AzureOpenAI


def _router_model() -> str:
    return (
        os.getenv("ROUTER_MODEL")
        or os.getenv("LLM_MODEL")
        or os.getenv("AZURE_OPENAI_MODEL")
        or "gpt-4o-mini"
    )


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


def decide_tool_usage(question: str) -> str:
    if not question.strip():
        return "DIRECT_ANSWER"

    system_prompt = (
        "You are a strict classifier.\n"
        "Return exactly one token:\n"
        "USE_MCP if the question requires accessing user survey/tabular data.\n"
        "DIRECT_ANSWER if it can be answered without survey data.\n"
        "Return only USE_MCP or DIRECT_ANSWER."
    )

    try:
        response = _get_client().chat.completions.create(
            model=_router_model(),
            temperature=0,
            max_tokens=4,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
        )
        output = (
            (response.choices[0].message.content or "").strip().upper()
            if response and getattr(response, "choices", None)
            else ""
        )
        if "USE_MCP" in output:
            return "USE_MCP"
        if "DIRECT_ANSWER" in output:
            return "DIRECT_ANSWER"
    except Exception:
        return "DIRECT_ANSWER"

    return "DIRECT_ANSWER"
