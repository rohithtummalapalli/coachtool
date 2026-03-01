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

from services.mcp_client import (
    call_graph_tool,
    call_survey_payload_tool,
    call_survey_tool,
    refresh_and_hydrate_survey_data,
)

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


def _extract_stock_plan(question: str) -> dict[str, Any]:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    system_prompt = (
        "You extract stock query parameters.\n"
        "Return strict JSON: {\"tickers\":[\"...\"],\"period\":\"...\"}.\n"
        "tickers must be uppercase stock symbols.\n"
        "If user asks multiple companies/tickers, include all relevant symbols.\n"
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
        tickers_raw = parsed.get("tickers") or []
        tickers: list[str] = []
        if isinstance(tickers_raw, list):
            for token in tickers_raw:
                symbol = str(token).strip().upper()
                if symbol and symbol not in tickers:
                    tickers.append(symbol)
        elif isinstance(tickers_raw, str):
            symbol = tickers_raw.strip().upper()
            if symbol:
                tickers.append(symbol)
        legacy_ticker = str(parsed.get("ticker", "")).strip().upper()
        if legacy_ticker and legacy_ticker not in tickers:
            tickers.append(legacy_ticker)
        period = str(parsed.get("period", "6mo")).strip()
        if period not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}:
            period = "6mo"
        if tickers:
            return {"tickers": tickers[:5], "period": period}
    except Exception:
        pass
    return {"tickers": [], "period": "6mo"}


def _fallback_extract_tickers(question: str) -> list[str]:
    import re

    candidates = re.findall(r"\b[A-Z]{1,5}\b", question)
    blocked = {"I", "A", "AN", "THE", "AND", "OR", "TO", "FOR", "WITH", "IN", "ON"}
    tickers: list[str] = []
    for token in candidates:
        if token in blocked:
            continue
        if token not in tickers:
            tickers.append(token)
    return tickers


def _fetch_single_ticker_payload(ticker: str, period: str) -> dict[str, Any]:
    import yfinance as yf

    try:
        history = yf.Ticker(ticker).history(period=period)
    except Exception:
        return {"error": f"Unable to fetch stock data for {ticker} right now."}

    if history is None or history.empty or "Close" not in history.columns:
        return {"error": f"No stock data found for {ticker}."}

    closes = history["Close"].dropna()
    if closes.empty:
        return {"error": f"No closing price data found for {ticker}."}

    start_price = float(closes.iloc[0])
    end_price = float(closes.iloc[-1])
    delta = end_price - start_price
    pct = (delta / start_price * 100) if start_price else 0.0
    high = float(closes.max())
    low = float(closes.min())

    x_values = [idx.strftime("%Y-%m-%d") for idx in closes.index.to_pydatetime()]
    y_values = [float(v) for v in closes.tolist()]
    return {
        "ticker": ticker,
        "period": period,
        "x": x_values,
        "y": y_values,
        "start": start_price,
        "end": end_price,
        "delta": delta,
        "pct": pct,
        "high": high,
        "low": low,
    }


def _fetch_stock_payload(question: str, user_profile: dict[str, str] | None = None) -> dict[str, Any]:
    if not question.strip():
        return {"error": "Stock query is empty."}
    try:
        import yfinance as yf
    except Exception:
        return {"error": "Stock data service is not available right now."}

    plan = _extract_stock_plan(question)
    tickers = plan.get("tickers") or []
    if not isinstance(tickers, list):
        tickers = []
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
    explicit_tickers = _fallback_extract_tickers(question)
    if explicit_tickers:
        tickers = [t for t in tickers if t in explicit_tickers] or explicit_tickers
    else:
        # Prevent unrelated/hallucinated symbols when user didn't provide any ticker.
        tickers = []
        industry = str((user_profile or {}).get("industry") or "").strip()
        requested_count = _extract_requested_company_count(question)
        discovered = _discover_tickers_for_industry(industry=industry, count=requested_count)
        if discovered:
            tickers = discovered
    tickers = list(dict.fromkeys(tickers))[:5]
    period = plan.get("period") or "6mo"
    if not tickers:
        return {
            "error": "I could not identify the stock ticker(s). Please include symbols like AAPL or MSFT."
        }

    series_payloads: list[dict[str, Any]] = []
    errors: list[str] = []
    for ticker in tickers:
        item = _fetch_single_ticker_payload(ticker, period)
        if item.get("error"):
            errors.append(str(item["error"]))
            continue
        series_payloads.append(item)

    if not series_payloads:
        return {"error": errors[0] if errors else "Unable to fetch stock data right now."}

    if len(series_payloads) == 1:
        item = series_payloads[0]
        ticker = str(item["ticker"])
        end_price = float(item["end"])
        delta = float(item["delta"])
        pct = float(item["pct"])
        high = float(item["high"])
        low = float(item["low"])
        x_values = item["x"]
        y_values = item["y"]
        return {
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
                "y_title": "Close Price ($)",
            },
            "table_rows": [
                {"ticker": ticker, "date": date_val, "close": float(close_val)}
                for date_val, close_val in zip(x_values, y_values)
            ],
            "tickers": [ticker],
            "period": period,
            "source": "yfinance",
        }

    ranked = sorted(series_payloads, key=lambda s: float(s["pct"]), reverse=True)
    summary_parts = []
    for item in ranked:
        summary_parts.append(
            (
                f"{item['ticker']}: latest {float(item['end']):.2f}, "
                f"change {float(item['delta']):+.2f} ({float(item['pct']):+.2f}%), "
                f"high {float(item['high']):.2f}, low {float(item['low']):.2f}"
            )
        )
    summary = (
        f"Stock comparison over {period}:\n- "
        + "\n- ".join(summary_parts)
    )
    if errors:
        summary += "\n\nUnavailable tickers: " + ", ".join(errors)

    return {
        "summary": summary,
        "graph": {
            "kind": "line_multi",
            "series": [
                {"name": str(item["ticker"]), "x": item["x"], "y": item["y"]}
                for item in series_payloads
            ],
            "title": f"Stock Price Comparison ({period})",
            "x_title": "Date",
            "y_title": "Close Price ($)",
        },
        "table_rows": [
            {
                "ticker": str(item["ticker"]),
                "latest_close": float(item["end"]),
                "change": float(item["delta"]),
                "change_pct": float(item["pct"]),
            }
            for item in ranked
        ],
        "tickers": [str(item["ticker"]) for item in series_payloads],
        "period": period,
        "source": "yfinance",
    }


@tool("stock_market_data")
def stock_market_data_tool(question: str) -> str:
    """Use this tool for stock price, trend, ticker performance, and stock chart requests."""
    payload = _fetch_stock_payload(question)
    if payload.get("error"):
        return str(payload["error"])
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
    "For numeric/statistical questions, do not just summarize raw numbers.\n"
    "Always provide interpretation-first insights: what the numbers imply, likely drivers, and practical next actions.\n"
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


def _maybe_answer_from_metadata(
    question: str,
    history_messages: list[dict[str, str]],
    user_metadata: dict[str, Any],
) -> str | None:
    metadata = user_metadata if isinstance(user_metadata, dict) else {}
    available = {
        "industry": str(metadata.get("industry") or "").strip(),
        "company_size": str(metadata.get("company_size") or "").strip(),
        "team_name": str(metadata.get("team_name") or "").strip(),
        "company_name": str(metadata.get("company_name") or "").strip(),
        "company_id": str(metadata.get("company_id") or "").strip(),
        "year": str(metadata.get("year") or "").strip(),
    }
    if not any(available.values()):
        return None

    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a strict metadata router.\n"
        "Return JSON only: {\"use_metadata\": true|false, \"answer\":\"...\"}.\n"
        "Set use_metadata=true only if the question can be answered directly from metadata.\n"
        "If true, answer with exact metadata values and no invention.\n"
        "If false, answer should be empty."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"Question:\n{question}\n\n"
        f"Metadata:\n{json.dumps(available)}\n\n"
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
        parsed = json.loads(resp.choices[0].message.content or "{}")
        if bool(parsed.get("use_metadata", False)):
            answer = str(parsed.get("answer", "")).strip()
            return answer or None
    except Exception:
        return None
    return None


def _extract_requested_company_count(question: str) -> int:
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    system_prompt = (
        "Extract requested number of companies for stock comparison.\n"
        "Return JSON only: {\"count\": number}.\n"
        "If not specified, return 2. Clamp to 1..5."
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
        count = int(parsed.get("count", 2))
        return max(1, min(5, count))
    except Exception:
        return 2


def _discover_tickers_for_industry(industry: str, count: int) -> list[str]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key or not industry.strip():
        return []
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key)
        result = client.search(
            query=(
                f"Top publicly traded companies in {industry} industry "
                "with stock ticker symbols by market cap"
            ),
            max_results=8,
        )
        snippets = []
        for item in result.get("results") or []:
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            if title or content:
                snippets.append(f"Title: {title}\nContent: {content}")
        if not snippets:
            return []

        model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
        resp = _get_router_client().chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract ticker symbols from snippets. "
                        f"Return JSON only: {{\"tickers\":[...]}} with max {count} symbols."
                    ),
                },
                {"role": "user", "content": "\n\n".join(snippets[:12])},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
        raw = parsed.get("tickers") or []
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for token in raw:
            symbol = str(token).strip().upper()
            if symbol and symbol not in out:
                out.append(symbol)
        return out[:count]
    except Exception:
        return []


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


def _is_explicit_non_survey_request(question: str, history_messages: list[dict[str, str]]) -> bool:
    """
    Survey-first policy:
    - Return False by default.
    - Return True only when user explicitly asks for non-survey domains.
    """
    model = os.getenv("ROUTER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a strict router for domain selection.\n"
        "Default to survey domain unless user explicitly requests a different domain.\n"
        "Return JSON only: {\"non_survey\": true|false}.\n"
        "Set non_survey=true ONLY if the user explicitly asks for one of:\n"
        "1) Internal docs/policies/knowledge base/manual/SOP/procedure.\n"
        "2) External web/public/current events/news/industry benchmarking outside personal survey.\n"
        "3) Stock/ticker/market price/trading chart.\n"
        "Otherwise set non_survey=false."
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
        return bool(payload.get("non_survey", False))
    except Exception:
        # Keep survey-first behavior on classifier failure.
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
        "You are a survey analytics assistant.\n"
        "Use ONLY the provided survey tool result as source of truth.\n"
        "Do not invent values, trends, rows, or benchmarks.\n"
        "Do not dump raw tables or just repeat the dataset.\n"
        "For numeric/statistical questions, produce insight-style output with:\n"
        "1) Key finding(s) directly tied to the question,\n"
        "2) What the finding implies for this user/team,\n"
        "3) One to three practical recommendations.\n"
        "If applicable, include explicit numeric comparisons and gaps from provided data.\n"
        "Keep answer concise but analytical.\n"
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


def _render_stock_answer(
    question: str,
    stock_summary: str,
    history_messages: list[dict[str, str]],
) -> str:
    if not stock_summary.strip():
        return "I couldn't build a stock summary right now."
    model = os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
    history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_messages[-10:]])
    system_prompt = (
        "You are a financial insights assistant.\n"
        "Use ONLY the provided stock summary.\n"
        "Do not invent values.\n"
        "Do not just summarize numbers.\n"
        "For numeric questions, provide:\n"
        "1) Key movement/trend insight,\n"
        "2) What it implies for the asked context,\n"
        "3) One concise caveat or next-check recommendation.\n"
        "Keep it concise and decision-oriented.\n"
        "Do not include markdown tables or raw code blocks."
    )
    user_prompt = (
        f"Conversation history:\n{history_text}\n\n"
        f"User question:\n{question}\n\n"
        f"Stock summary:\n{stock_summary}\n\n"
        "Write the final response."
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
        return (resp.choices[0].message.content or "").strip() or stock_summary
    except Exception:
        return stock_summary


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

    missing_markers = [
        "no survey data is available for this user",
        "survey tool returned no data",
    ]
    if any(marker in summary.lower() for marker in missing_markers):
        refreshed = await refresh_and_hydrate_survey_data(user_id=user_id)
        if refreshed:
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


async def _run_stock_pipeline(
    question: str,
    history_messages: list[dict[str, str]],
    user_profile: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = await asyncio.to_thread(_fetch_stock_payload, question, user_profile)
    error = str(payload.get("error", "")).strip()
    if error:
        return {"answer": error, "graph": {}}
    summary = str(payload.get("summary", "")).strip()
    answer = await asyncio.to_thread(_render_stock_answer, question, summary, history_messages)
    graph = payload.get("graph")
    return {
        "answer": answer,
        "graph": graph if isinstance(graph, dict) else {},
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
    explicit_non_survey = _is_explicit_non_survey_request(question, normalized_history)
    survey_block_token = _SURVEY_TOOL_BLOCKED.set(explicit_non_survey)
    try:
        if not explicit_non_survey:
            metadata_answer = _maybe_answer_from_metadata(question, normalized_history, metadata)
            if metadata_answer:
                return {
                    "answer": metadata_answer,
                    "graph": {},
                    "trace": {"route": "METADATA", "tools_used": []},
                }
            # Survey-first behavior. If question hints at non-survey context,
            # ask for confirmation instead of auto-switching to web/RAG/stock.
            if _requires_external_benchmarking(question, normalized_history):
                return {
                    "answer": (
                        "I can answer this from your survey data by default. "
                        "Do you want me to also use external market/industry sources for comparison?"
                    ),
                    "graph": {},
                    "trace": {"route": "SURVEY_CONFIRMATION", "tools_used": []},
                }
            survey_result = asyncio.run(
                _run_survey_pipeline(
                    user_id=user_id,
                    question=question,
                    history_messages=normalized_history,
                    previous_graph_rows=previous_graph_rows,
                    previous_graph_metric=previous_graph_metric,
                )
            )
            survey_trace_tools = ["query_survey_data"]
            if (survey_result or {}).get("graph"):
                survey_trace_tools.append("create_survey_graph")
            survey_result["trace"] = {
                "route": "SURVEY_PIPELINE",
                "tools_used": survey_trace_tools,
            }
            return survey_result

        # Explicit non-survey request path
        stage = predict_loading_stage(question, normalized_history)
        if stage == "STOCK":
            stock_result = asyncio.run(
                _run_stock_pipeline(
                    question=question,
                    history_messages=normalized_history,
                    user_profile=profile,
                )
            )
            stock_result["trace"] = {
                "route": "STOCK_PIPELINE",
                "tools_used": ["stock_market_data"],
            }
            return stock_result

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
