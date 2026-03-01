from __future__ import annotations

import logging
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


def _mcp_url() -> str:
    return os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp")


def _extract_tool_text(result: Any) -> str:
    text_parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        text_value = getattr(item, "text", None)
        if text_value:
            text_parts.append(str(text_value))
    if text_parts:
        return "\n".join(text_parts).strip()
    if isinstance(result, dict):
        return str(result.get("content") or result).strip()
    return ""


def _extract_tool_structured(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    if isinstance(result, dict):
        if "structuredContent" in result:
            sc = result["structuredContent"]
            if isinstance(sc, dict) and "result" in sc:
                return sc["result"]
            return sc
    return None


async def call_survey_tool(user_id: str, question: str) -> str:
    if not user_id.strip():
        return "Unable to access survey data for this session."
    if not question.strip():
        return "Please ask a valid question."

    timeout_seconds = float(os.getenv("MCP_CLIENT_TIMEOUT_SECONDS", "15"))
    try:
        async with streamablehttp_client(
            _mcp_url(),
            timeout=timeout_seconds,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "query_survey_data",
                    {"user_id": user_id, "question": question},
                )

        extracted = _extract_tool_text(result)
        return extracted or "Survey tool returned an empty response."
    except Exception as exc:
        logger.exception("MCP call failed: %s", exc)
        return "Survey tools are currently unavailable. Please try again shortly."


async def call_survey_payload_tool(user_id: str, question: str) -> dict[str, Any]:
    if not user_id.strip():
        return {"summary": "Unable to access survey data for this session.", "rows": [], "metric": ""}
    if not question.strip():
        return {"summary": "Please ask a valid question.", "rows": [], "metric": ""}

    timeout_seconds = float(os.getenv("MCP_CLIENT_TIMEOUT_SECONDS", "15"))
    try:
        async with streamablehttp_client(
            _mcp_url(),
            timeout=timeout_seconds,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "query_survey_data_payload",
                    {"user_id": user_id, "question": question},
                )
        structured = _extract_tool_structured(result)
        if isinstance(structured, dict):
            return structured
        return {"summary": _extract_tool_text(result) or "No payload returned.", "rows": [], "metric": ""}
    except Exception as exc:
        logger.exception("MCP payload call failed: %s", exc)
        return {"summary": "Survey tools are currently unavailable. Please try again shortly.", "rows": [], "metric": ""}


async def call_graph_tool(question: str, metric: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(rows, list) or not rows:
        return {}
    timeout_seconds = float(os.getenv("MCP_CLIENT_TIMEOUT_SECONDS", "15"))
    try:
        async with streamablehttp_client(
            _mcp_url(),
            timeout=timeout_seconds,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "create_survey_graph",
                    {"question": question, "metric": metric, "rows": rows},
                )
        structured = _extract_tool_structured(result)
        if isinstance(structured, dict):
            graph = structured.get("graph")
            return graph if isinstance(graph, dict) else {}
        return {}
    except Exception as exc:
        logger.exception("MCP graph call failed: %s", exc)
        return {}


async def hydrate_survey_data(user_id: str, survey_rows: Any) -> bool:
    if not user_id.strip():
        return False
    if not isinstance(survey_rows, (list, dict)):
        return False

    timeout_seconds = float(os.getenv("MCP_CLIENT_TIMEOUT_SECONDS", "15"))
    try:
        async with streamablehttp_client(
            _mcp_url(),
            timeout=timeout_seconds,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "hydrate_survey_data",
                    {"user_id": user_id, "rows": survey_rows},
                )
        extracted = _extract_tool_text(result)
        logger.info("MCP hydrate response: %s", extracted)
        print(f"[MCP] hydrate response user_id={user_id}: {extracted}", flush=True)
        return True
    except Exception as exc:
        logger.exception("MCP hydrate failed: %s", exc)
        print(f"[MCP] hydrate failed user_id={user_id}: {exc}", flush=True)
        return False
