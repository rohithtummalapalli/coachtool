from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import AzureChatOpenAI
from openai import AzureOpenAI

from services.mcp_client import call_graph_tool, call_survey_payload_tool, call_survey_tool

_CURRENT_USER_ID: ContextVar[str] = ContextVar("current_user_id", default="")
_CURRENT_WEB_PROFILE: ContextVar[dict[str, str]] = ContextVar("current_web_profile", default={})
_SURVEY_TOOL_BLOCKED: ContextVar[bool] = ContextVar("survey_tool_blocked", default=False)


def _resolve_retrieve_documents() -> Callable[[str, int], list[str]]:
    candidates = [
        os.getenv("RAG_RETRIEVE_FUNCTION", "").strip(),
        "rag.retrieve.retrieve_documents",
        "rag.retriever.retrieve_documents",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            module_path, func_name = candidate.rsplit(".", 1)
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            return func
        except Exception:
            continue

    my_ai_app_path = Path.cwd() / "my-ai-app"
    if my_ai_app_path.exists():
        sys.path.insert(0, str(my_ai_app_path))
        try:
            module = importlib.import_module("rag.retrieve")
            func = getattr(module, "retrieve_documents")
            return func
        except Exception:
            pass

    def _fallback(_: str, __: int = 3) -> list[str]:
        return []

    return _fallback


_retrieve_documents = _resolve_retrieve_documents()


@tool("query_survey_data")
def mcp_survey_tool(question: str) -> str:
    """Use this tool to answer questions about user survey results, scores, comparisons, statistics, or analytics."""
    user_id = _CURRENT_USER_ID.get().strip()
    if not user_id:
        return "Survey data is not available for this session."
    if _SURVEY_TOOL_BLOCKED.get():
        return "Use web_search for this request because external benchmarking/market context is required."
    if not question.strip():
        return "Please provide a valid question."
    try:
        return asyncio.run(call_survey_tool(user_id=user_id, question=question))
    except Exception:
        return "Survey tools are currently unavailable."


@tool("retrieve_knowledge_base")
def rag_tool(question: str) -> str:
    """Use this tool to answer questions related to company policies, documents, and knowledge base."""
    if not question.strip():
        return "No question provided."
    top_k = int(os.getenv("RAG_TOP_K", "3"))
    try:
        docs = _retrieve_documents(question, top_k)
    except Exception:
        return "Knowledge base retrieval is currently unavailable."

    if not docs:
        return "No relevant knowledge base context found."
    return "\n\n".join(docs[:top_k])


@tool("web_search")
def web_search_tool(question: str) -> str:
    """Use this tool for current events, public facts, news, and external knowledge not covered by survey or internal docs."""
    if not question.strip():
        return "No question provided."
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return "Web search is not configured right now."
    profile = _CURRENT_WEB_PROFILE.get() or {}
    industry = str(profile.get("industry", "")).strip()
    company_size = str(profile.get("company_size", "")).strip()
    if not industry or not company_size:
        return (
            "Web search needs user metadata (industry and company_size), "
            "but it is unavailable for this session."
        )

    try:
        from tavily import TavilyClient

        max_results = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
        client = TavilyClient(api_key=api_key)
        contextual_query = (
            f"{question}\n\n"
            f"User context:\n"
            f"- Industry: {industry}\n"
            f"- Company size: {company_size}\n"
            "Prioritize results relevant to this industry and company size context."
        )
        result = client.search(query=contextual_query, max_results=max_results)
        rows = result.get("results") or []
        if not rows:
            return "No relevant web results found."
        lines: list[str] = [
            "Applied user context for search:",
            f"- Industry: {industry}",
            f"- Company size: {company_size}",
            "",
        ]
        for item in rows[:max_results]:
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            url = str(item.get("url", "")).strip()
            if not any([title, content, url]):
                continue
            lines.append(f"Title: {title}\nSummary: {content}\nSource: {url}")
        return "\n\n".join(lines) if lines else "No relevant web results found."
    except Exception:
        return "Web search is temporarily unavailable."


def _extract_stock_plan(question: str) -> dict[str, str]:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    system_prompt = (
        "You extract stock query parameters.\n"
        "Return strict JSON: {\"ticker\":\"...\",\"period\":\"...\"}.\n"
        "ticker must be uppercase stock symbol.\n"
        "period must be one of: 5d,1mo,3mo,6mo,1y,2y,5y,max.\n"
        "If unclear, choose sensible defaults."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        ticker = str(parsed.get("ticker", "")).strip().upper()
        period = str(parsed.get("period", "6mo")).strip()
        if period not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}:
            period = "6mo"
        if ticker:
            return {"ticker": ticker, "period": period}
    except Exception:
        pass
    return {"ticker": "", "period": "6mo"}


def _fallback_extract_ticker(question: str) -> str:
    import re

    candidates = re.findall(r"\b[A-Z]{1,5}\b", question)
    blocked = {"I", "A", "AN", "THE", "AND", "OR", "TO", "FOR", "WITH", "IN", "ON"}
    for token in candidates:
        if token in blocked:
            continue
        return token
    return ""


@tool("stock_market_data")
def stock_market_data_tool(question: str) -> str:
    """Use this tool for stock price, trend, ticker performance, and stock chart requests."""
    if not question.strip():
        return "Stock query is empty."
    try:
        import yfinance as yf
    except Exception:
        return "Stock data service is not available right now."

    plan = _extract_stock_plan(question)
    ticker = plan.get("ticker") or _fallback_extract_ticker(question)
    period = plan.get("period") or "6mo"
    if not ticker:
        return "I could not identify the stock ticker. Please include a symbol like AAPL or MSFT."

    try:
        history = yf.Ticker(ticker).history(period=period)
    except Exception:
        return f"Unable to fetch stock data for {ticker} right now."

    if history is None or history.empty or "Close" not in history.columns:
        return f"No stock data found for {ticker}."

    closes = history["Close"].dropna()
    if closes.empty:
        return f"No closing price data found for {ticker}."

    start_price = float(closes.iloc[0])
    end_price = float(closes.iloc[-1])
    delta = end_price - start_price
    pct = (delta / start_price * 100) if start_price else 0.0
    high = float(closes.max())
    low = float(closes.min())

    x_values = [idx.strftime("%Y-%m-%d") for idx in closes.index.to_pydatetime()]
    y_values = [float(v) for v in closes.tolist()]

    payload = {
        "summary": (
            f"{ticker} over {period}: latest close {end_price:.2f}, "
            f"change {delta:+.2f} ({pct:+.2f}%), high {high:.2f}, low {low:.2f}."
        ),
        "graph": {
            "kind": "line",
            "x": x_values,
            "y": y_values,
            "title": f"{ticker} Closing Price ({period})",
            "x_title": "Date",
            "y_title": "Close Price",
        },
        "ticker": ticker,
        "period": period,
        "source": "yfinance",
    }
    return json.dumps(payload)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _create_llm() -> AzureChatOpenAI:
    deployment = os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    return AzureChatOpenAI(
        azure_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        api_version=_required_env("AZURE_OPENAI_API_VERSION"),
        azure_deployment=deployment,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        streaming=True,
    )


_SYSTEM_PROMPT = (
    "You are an assistant for enterprise support.\n"
    "Use tools when needed:\n"
    "- query_survey_data for user-specific survey analytics/questions.\n"
    "- retrieve_knowledge_base for policy/document knowledge.\n"
    "- web_search for external/public/current information.\n"
    "- stock_market_data for stock prices, trends, and stock charts.\n"
    "When using web_search, always apply available user industry and company size context.\n"
    "If no tool is needed, answer directly."
)

_AGENT = create_agent(
    model=_create_llm(),
    tools=[mcp_survey_tool, rag_tool, web_search_tool, stock_market_data_tool],
    system_prompt=_SYSTEM_PROMPT,
    debug=os.getenv("LANGCHAIN_VERBOSE", "false").lower() in {"1", "true", "yes", "on"},
)


def _get_router_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        api_version=_required_env("AZURE_OPENAI_API_VERSION"),
    )


def _should_use_survey_tool(question: str, history_messages: list[dict[str, str]]) -> bool:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a strict routing classifier for an enterprise assistant.\n"
        "Understand user messages in any language.\n"
        "Return JSON only with exact schema: {\"route\":\"SURVEY_TOOL\"|\"AGENT\"}.\n"
        "Choose SURVEY_TOOL if the answer should come from the user's survey results/statistics.\n"
        "Choose AGENT for everything else, especially when question needs external data, "
        "industry benchmarking, market trends, comparison with other companies, "
        "or public/current information."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Current user question:\n{question}\n\n"
        "Return route JSON."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        payload = json.loads(content)
        route = str(payload.get("route", "")).strip().upper()
        return route == "SURVEY_TOOL"
    except Exception:
        return False


def _requires_external_benchmarking(question: str, history_messages: list[dict[str, str]]) -> bool:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a strict classifier.\n"
        "Return JSON only: {\"external_benchmarking_needed\": true|false}.\n"
        "Set true when answering requires external/public benchmarks, market trends, "
        "industry peer comparisons, other companies, or outside data beyond user survey/internal docs."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Current user question:\n{question}\n\n"
        "Return JSON."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        payload = json.loads(content)
        return bool(payload.get("external_benchmarking_needed", False))
    except Exception:
        return False


def predict_loading_stage(question: str, history_messages: list[dict[str, str]] | None = None) -> str:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history = history_messages or []
    history_text = "\n".join([f"{m.get('role')}: {m.get('content')}" for m in history[-10:]])
    system_prompt = (
        "You are a strict planner classifier.\n"
        "Return JSON only: {\"stage\":\"SURVEY\"|\"RAG\"|\"WEB\"|\"STOCK\"|\"DIRECT\"}.\n"
        "SURVEY for user survey analytics/scores.\n"
        "RAG for internal documents/policies/knowledge base.\n"
        "WEB for external/public/current/industry trend questions.\n"
        "STOCK for stock ticker price/trend/chart questions.\n"
        "DIRECT for pure reasoning/chitchat."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Current user question:\n{question}\n\n"
        "Return stage JSON."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        stage = str(parsed.get("stage", "DIRECT")).strip().upper()
        return stage if stage in {"SURVEY", "RAG", "WEB", "STOCK", "DIRECT"} else "DIRECT"
    except Exception:
        return "DIRECT"


def _should_fallback_to_agent_from_survey(survey_answer: str) -> bool:
    text = (survey_answer or "").strip().lower()
    if not text:
        return True
    markers = [
        "no market data",
        "don't have market data",
        "do not have market data",
        "no external data",
        "industry comparison data",
        "comparison data isn’t available",
        "comparison data isn't available",
        "not available for this specific question",
        "cannot compare with other companies",
        "can't compare with other companies",
        "survey tool returned no data",
    ]
    return any(marker in text for marker in markers)


def _invoke_general_agent(question: str, normalized_history: list[dict[str, str]]) -> dict[str, Any]:
    agent_messages = [*normalized_history, {"role": "user", "content": question}]
    result: dict[str, Any] = _AGENT.invoke(
        {"messages": agent_messages}
    )
    output = ""
    messages = result.get("messages") if isinstance(result, dict) else None
    tool_names: list[str] = []
    graph: dict[str, Any] = {}
    if isinstance(messages, list) and messages:
        seen: set[str] = set()
        for msg in messages:
            if getattr(msg, "type", "") != "tool":
                continue
            raw_name = str(getattr(msg, "name", "")).strip()
            if raw_name and raw_name not in seen:
                seen.add(raw_name)
                tool_names.append(raw_name)
            if raw_name == "stock_market_data":
                content = getattr(msg, "content", "")
                stock_text = ""
                if isinstance(content, str):
                    stock_text = content
                elif isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict) and "text" in item:
                            parts.append(str(item["text"]))
                    stock_text = "".join(parts)
                try:
                    parsed = json.loads(stock_text)
                    maybe_graph = parsed.get("graph")
                    if isinstance(maybe_graph, dict):
                        graph = maybe_graph
                except Exception:
                    pass
        last = messages[-1]
        content = getattr(last, "content", "")
        if isinstance(content, str):
            output = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
            output = "\n".join([p for p in parts if p]).strip()
    if not output:
        return {
            "answer": "I could not produce an answer at the moment.",
            "graph": graph,
            "trace": {"route": "AGENT", "tools_used": tool_names},
        }
    return {
        "answer": output,
        "graph": graph,
        "trace": {"route": "AGENT", "tools_used": tool_names},
    }


def _render_survey_answer(
    question: str,
    tool_output: str,
    history_messages: list[dict[str, str]],
) -> str:
    if not tool_output.strip():
        return "Survey tool returned no data. Please try again."

    if any(
        marker in tool_output.lower()
        for marker in [
            "no survey data is available",
            "survey tools are currently unavailable",
            "unable to interpret this survey question",
            "question is required",
            "user id is required",
            "no rows match the requested filters",
        ]
    ):
        return tool_output

    model = os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a survey-answer writer.\n"
        "Use ONLY the provided tool result as source of truth.\n"
        "Do not invent any values or rows.\n"
        "Provide a concise, user-friendly answer.\n"
        "Do not include chart instructions, graph concepts, plotting notes, or visualization suggestions."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"User question:\n{question}\n\n"
        f"Survey tool result:\n{tool_output}\n\n"
        "Write the final answer."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return (resp.choices[0].message.content or "").strip() or tool_output
    except Exception:
        return tool_output


def _should_generate_graph(question: str, history_messages: list[dict[str, str]], payload: dict[str, Any]) -> bool:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    payload_preview = {
        "operation": payload.get("operation"),
        "metric": payload.get("metric"),
        "rows_count": len(payload.get("rows") or []),
    }
    system_prompt = (
        "You are a strict visualization router.\n"
        "Understand any language.\n"
        "Return JSON only: {\"needs_graph\": true|false}.\n"
        "Set needs_graph=true if a chart would help explain numeric/ranked/statistical results."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Question:\n{question}\n\n"
        f"Survey payload preview:\n{payload_preview}\n\n"
        "Return JSON."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
        return bool(parsed.get("needs_graph", False))
    except Exception:
        return False


def _resolve_graph_merge_mode(
    question: str,
    history_messages: list[dict[str, str]],
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> str:
    if not previous_rows:
        return "replace"
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a strict graph-state controller.\n"
        "Understand any language.\n"
        "Return JSON only: {\"mode\":\"append\"|\"replace\"}.\n"
        "Choose append when user asks to add/include/also keep previous graph entities.\n"
        "Choose replace when user asks a new standalone graph or different scope."
    )
    user_prompt = (
        f"History:\n{history_text}\n\n"
        f"Question:\n{question}\n\n"
        f"Previous graph rows count: {len(previous_rows)}\n"
        f"Current graph rows count: {len(current_rows)}\n\n"
        "Return mode JSON."
    )
    try:
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        mode = str(parsed.get("mode", "replace")).strip().lower()
        return "append" if mode == "append" else "replace"
    except Exception:
        return "replace"


def _merge_graph_rows(
    previous_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    metric: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in previous_rows + current_rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("label_id", "") or row.get("label", "")).strip()
        if not key:
            continue
        value = row.get(metric)
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        merged[key] = {
            "label_id": str(row.get("label_id", "") or key),
            "label": str(row.get("label", "") or key),
            "dimension": str(row.get("dimension", "") or ""),
            metric: numeric_value,
        }
    return list(merged.values())


async def _run_survey_pipeline(
    user_id: str,
    question: str,
    history_messages: list[dict[str, str]],
    previous_graph_rows: list[dict[str, Any]] | None = None,
    previous_graph_metric: str | None = None,
) -> dict[str, Any]:
    history_context = "\n".join(
        [f"{m['role']}: {m['content']}" for m in history_messages[-8:]]
    )
    contextual_question = (
        "Use this conversation context to resolve references like "
        "'this graph', 'that item', 'add/remove previous item'.\n"
        f"Context:\n{history_context}\n\n"
        f"Current question:\n{question}"
    )

    payload = await call_survey_payload_tool(user_id=user_id, question=contextual_question)
    summary = str(payload.get("summary", "") or "").strip()
    rows = payload.get("rows") or []
    metric = str(payload.get("metric", "") or "topbox")
    operation = str(payload.get("operation", "")).strip().lower()

    # For compare operations, always use the exact fetched comparison items
    # so chart entities match the textual comparison entities 1:1.
    if operation == "compare":
        comparisons = payload.get("comparisons") or []
        compare_rows: list[dict[str, Any]] = []
        if isinstance(comparisons, list):
            for item in comparisons:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                compare_rows.append(
                    {
                        "label_id": str(item.get("label_id", "") or ""),
                        "label": str(item.get("label", "") or item.get("question", "")),
                        "dimension": str(item.get("dimension", "") or ""),
                        metric: numeric_value,
                    }
                )
        rows = compare_rows

    prev_rows = previous_graph_rows or []
    prev_metric = str(previous_graph_metric or "").strip()
    merge_mode = _resolve_graph_merge_mode(question, history_messages, rows, prev_rows)
    if merge_mode == "append":
        merge_metric = metric or prev_metric or "topbox"
        rows = _merge_graph_rows(prev_rows, rows, merge_metric)
        metric = merge_metric

    # For compare flows, always graph compared entities (possibly appended).
    needs_graph = True if operation == "compare" else _should_generate_graph(question, history_messages, payload)
    answer_task = asyncio.to_thread(_render_survey_answer, question, summary, history_messages)
    graph_task = (
        call_graph_tool(question=question, metric=metric, rows=rows)
        if needs_graph
        else asyncio.sleep(0, result={})
    )
    answer, graph = await asyncio.gather(answer_task, graph_task)
    return {
        "answer": answer,
        "graph": graph if isinstance(graph, dict) else {},
        "graph_rows": rows if isinstance(rows, list) else [],
        "graph_metric": metric,
    }


def run_agent(
    user_id: str,
    question: str,
    user_metadata: dict[str, Any] | None = None,
    history_messages: list[dict[str, str]] | None = None,
    previous_graph_rows: list[dict[str, Any]] | None = None,
    previous_graph_metric: str | None = None,
) -> dict[str, Any]:
    if not user_id.strip():
        return "Unable to resolve your session. Please sign in again."
    if not question.strip():
        return "Please enter a question."

    normalized_history: list[dict[str, str]] = []
    for msg in history_messages or []:
        role = str(msg.get("role", "")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized_history.append({"role": role, "content": content})

    def _pick_profile_field(source: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    metadata = user_metadata if isinstance(user_metadata, dict) else {}
    org = metadata.get("organization")
    org_meta = org if isinstance(org, dict) else {}
    profile = {
        "industry": (
            _pick_profile_field(metadata, ["industry", "Industry"])
            or _pick_profile_field(org_meta, ["industry", "Industry"])
        ),
        "company_size": (
            _pick_profile_field(metadata, ["company_size", "companySize", "size"])
            or _pick_profile_field(org_meta, ["company_size", "companySize", "size"])
        ),
    }

    token = _CURRENT_USER_ID.set(user_id.strip())
    profile_token = _CURRENT_WEB_PROFILE.set(profile)
    external_needed = _requires_external_benchmarking(question, normalized_history)
    survey_block_token = _SURVEY_TOOL_BLOCKED.set(external_needed)
    try:
        if external_needed:
            external_question = (
                f"{question}\n\n"
                "This request needs external benchmark/industry trend data. "
                "Use web_search and incorporate user industry/company-size context."
            )
            result = _invoke_general_agent(external_question, normalized_history)
            result["trace"] = {
                "route": "AGENT_EXTERNAL",
                "tools_used": list((result.get("trace") or {}).get("tools_used") or []),
            }
            return result

        use_survey_tool = _should_use_survey_tool(question, normalized_history)
        if use_survey_tool:
            survey_result = asyncio.run(
                _run_survey_pipeline(
                    user_id=user_id,
                    question=question,
                    history_messages=normalized_history,
                    previous_graph_rows=previous_graph_rows,
                    previous_graph_metric=previous_graph_metric,
                )
            )
            survey_answer = str((survey_result or {}).get("answer") or "")
            if _should_fallback_to_agent_from_survey(survey_answer):
                fallback_block_token = _SURVEY_TOOL_BLOCKED.set(True)
                try:
                    fallback_question = (
                        f"{question}\n\n"
                        "Survey result context:\n"
                        f"{survey_answer}\n\n"
                        "If external benchmarking or trend data is needed, use web_search."
                    )
                    fallback_result = _invoke_general_agent(fallback_question, normalized_history)
                finally:
                    _SURVEY_TOOL_BLOCKED.reset(fallback_block_token)
                fallback_trace = (fallback_result or {}).get("trace") or {}
                prior_tools = ["query_survey_data"]
                merged_tools: list[str] = []
                for name in prior_tools + list(fallback_trace.get("tools_used") or []):
                    if name not in merged_tools:
                        merged_tools.append(name)
                fallback_result["trace"] = {
                    "route": "SURVEY_PIPELINE_FALLBACK_AGENT",
                    "tools_used": merged_tools,
                }
                return fallback_result
            survey_trace_tools = ["query_survey_data"]
            if (survey_result or {}).get("graph"):
                survey_trace_tools.append("create_survey_graph")
            survey_result["trace"] = {
                "route": "SURVEY_PIPELINE",
                "tools_used": survey_trace_tools,
            }
            return survey_result

        return _invoke_general_agent(question, normalized_history)
    except Exception:
        return {
            "answer": "I could not process your request right now. Please try again.",
            "graph": {},
            "trace": {"route": "ERROR", "tools_used": []},
        }
    finally:
        _SURVEY_TOOL_BLOCKED.reset(survey_block_token)
        _CURRENT_WEB_PROFILE.reset(profile_token)
        _CURRENT_USER_ID.reset(token)
