from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def generate_answer(question: str, context: str) -> str:
    model = os.getenv("LLM_MODEL", os.getenv("AZURE_OPENAI_MODEL", "gpt-4o-mini"))
    client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": f"Use this context to answer accurately:\n\n{context}",
            },
            {"role": "user", "content": question},
        ],
    )
    return response.choices[0].message.content or ""
