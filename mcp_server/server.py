from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server.fastmcp import FastMCP
from openai import AzureOpenAI


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from accounts.survey_cache import get_user_survey_dataframe  # noqa: E402


ALLOWED_METRICS = {"topbox", "bottombox", "mean", "variance", "correlation"}
ALLOWED_OPERATIONS = {"rank", "aggregate", "count", "list_dimensions", "describe", "compare"}

logging.basicConfig(
    level=os.getenv("MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - mcp_server - %(message)s",
)
logger = logging.getLogger("mcp_server")
_USER_DF_CACHE: dict[str, pd.DataFrame] = {}


def get_user_dataframe(user_id: str) -> pd.DataFrame:
    cached = _USER_DF_CACHE.get(user_id)
    if cached is not None:
        return cached
    return get_user_survey_dataframe(user_id)


def _set_user_dataframe(user_id: str, rows: Any) -> pd.DataFrame:
    if isinstance(rows, list):
        df = pd.DataFrame(rows)
    elif isinstance(rows, dict):
        data_rows = rows.get("data")
        df = pd.DataFrame(data_rows if isinstance(data_rows, list) else [rows])
    else:
        df = pd.DataFrame()
    _USER_DF_CACHE[user_id] = df
    return df


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [str(col).strip().lower() for col in normalized.columns]
    return normalized


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _get_azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        api_version=_required_env("AZURE_OPENAI_API_VERSION"),
    )


def _sanitize_single_plan(raw: dict[str, Any], default_limit: int = 5) -> dict[str, Any]:
    operation = str(raw.get("operation", "rank")).strip().lower()
    if operation not in ALLOWED_OPERATIONS or operation == "compare":
        operation = "rank"

    metric = str(raw.get("metric", "mean")).strip().lower()
    if metric not in ALLOWED_METRICS:
        metric = "mean"

    sort = str(raw.get("sort", "asc")).strip().lower()
    if sort not in {"asc", "desc"}:
        sort = "asc"

    agg = str(raw.get("aggregate", "mean")).strip().lower()
    if agg not in {"mean", "sum", "min", "max", "count"}:
        agg = "mean"

    try:
        limit = int(raw.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(20, limit))

    filters = raw.get("filters") or {}
    if not isinstance(filters, dict):
        filters = {}
    dimension_includes = str(filters.get("dimension_includes", "") or "").strip()
    label_includes = str(filters.get("label_includes", "") or "").strip()
    label_id_in = filters.get("label_id_in") or []
    if not isinstance(label_id_in, list):
        label_id_in = []
    label_id_in = [str(item).strip() for item in label_id_in if str(item).strip()]

    return {
        "operation": operation,
        "metric": metric,
        "sort": sort,
        "aggregate": agg,
        "limit": limit,
        "filters": {
            "dimension_includes": dimension_includes,
            "label_includes": label_includes,
            "label_id_in": label_id_in,
        },
    }


def _sanitize_plan(raw: dict[str, Any], default_limit: int = 5) -> dict[str, Any]:
    operation = str(raw.get("operation", "rank")).strip().lower()
    if operation == "compare":
        queries = raw.get("queries") or []
        if not isinstance(queries, list):
            queries = []
        sanitized_queries: list[dict[str, Any]] = []
        for query in queries[:4]:
            if isinstance(query, dict):
                sub = _sanitize_single_plan(query, default_limit=1)
                sub_question = str(query.get("question", "") or "").strip()
                if sub_question:
                    sub["question"] = sub_question
                sanitized_queries.append(sub)
        if len(sanitized_queries) < 2:
            # Fallback to normal single-plan behavior if compare is malformed.
            return _sanitize_single_plan(raw, default_limit=default_limit)
        return {
            "operation": "compare",
            "comparison_mode": str(raw.get("comparison_mode", "pairwise")).strip().lower() or "pairwise",
            "queries": sanitized_queries[:2],
        }

    return _sanitize_single_plan(raw, default_limit=default_limit)


def _enforce_business_defaults(question: str, plan: dict[str, Any]) -> dict[str, Any]:
    if str(plan.get("operation")) == "compare":
        queries = plan.get("queries") or []
        enforced_queries: list[dict[str, Any]] = []
        for sub in queries:
            if not isinstance(sub, dict):
                continue
            sub_question = str(sub.get("question", "") or "").strip() or question
            enforced = _enforce_business_defaults(sub_question, sub)
            enforced_queries.append(enforced)
        plan["queries"] = enforced_queries
        return plan
    # No keyword/rule matching in code: planner decides, sanitizer validates shape.
    return plan


def _plan_query_with_llm(question: str, df: pd.DataFrame) -> dict[str, Any]:
    logger.info("Planner start: question=%s", question)
    columns = df.columns.tolist()
    unique_dimensions = (
        df["dimension"].dropna().astype(str).str.strip().loc[lambda s: s != ""].unique().tolist()
        if "dimension" in df.columns
        else []
    )

    plan_schema = {
        "operation": "rank|aggregate|count|list_dimensions|describe|compare",
        "metric": "topbox|bottombox|mean|variance|correlation",
        "sort": "asc|desc",
        "aggregate": "mean|sum|min|max|count",
        "limit": 5,
        "filters": {
            "dimension_includes": "",
            "label_includes": "",
            "label_id_in": [],
            "label_id_prefix": "",
            "exclude_label_id_prefix": "",
            "exclude_label_id_exact": [],
        },
        "queries": [
            {
                "question": "",
                "operation": "rank|aggregate|count|list_dimensions|describe",
                "metric": "topbox|bottombox|mean|variance|correlation",
                "sort": "asc|desc",
                "aggregate": "mean|sum|min|max|count",
                "limit": 1,
                "filters": {
                    "dimension_includes": "",
                    "label_includes": "",
                    "label_id_in": [],
                    "label_id_prefix": "",
                    "exclude_label_id_prefix": "",
                    "exclude_label_id_exact": [],
                },
            }
        ],
    }

    system_prompt = (
        "You are a planner for survey analytics over a tabular dataframe.\n"
        "You must understand user questions in any language and still produce the same JSON schema.\n"
        "Return JSON only. No markdown, no explanation, no extra keys.\n"
        "The output MUST be a valid JSON object exactly following this schema and enum values.\n"
        f"Schema: {json.dumps(plan_schema)}\n"
        "Business defaults (must follow):\n"
        "1) For general survey results questions, default metric=topbox and exclude label_id prefix 'index_' and label_id 'TI'.\n"
        "2) For dimension/index questions, default metric=topbox and filter label_id prefix 'index_'.\n"
        "3) For Trust Index or TI questions, default metric=topbox and filter label_id exactly 'TI'.\n"
        "4) If a question asks to compare two entities (for example highest item vs trust index), "
        "set operation='compare' and provide two query objects in 'queries'.\n"
        "Choose the best metric/filter from the user question.\n"
        "If user asks lowest/least/minimum, use sort=asc. "
        "If highest/top/maximum, use sort=desc.\n"
        "If no explicit aggregate, prefer operation=rank.\n"
    )
    user_prompt = (
        "Create a JSON plan for this question.\n"
        f"Question: {question}\n"
        f"Available columns: {columns}\n"
        f"Known dimension values: {unique_dimensions[:100]}\n"
        "Respond with JSON."
    )

    try:
        client = _get_azure_client()
        model = os.getenv("MCP_PLANNER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
        logger.info("Planner call: model=%s columns=%s", model, columns)
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        logger.info("Planner raw output: %s", content)
        raw_plan = json.loads(content)
        if not isinstance(raw_plan, dict):
            raise ValueError("Planner output is not a JSON object.")
        sanitized = _sanitize_plan(raw_plan)
        enforced = _enforce_business_defaults(question, sanitized)
        logger.info("Planner final plan: %s", enforced)
        return enforced
    except Exception as exc:
        logger.exception("Planner failed")
        raise RuntimeError(f"Planner failed: {exc}") from exc


def _apply_filters(df: pd.DataFrame, plan: dict[str, Any]) -> pd.DataFrame:
    logger.info("Applying filters: %s", plan.get("filters", {}))
    filtered = df.copy()
    filters = plan.get("filters", {})
    dimension_includes = str(filters.get("dimension_includes", "") or "").strip()
    label_includes = str(filters.get("label_includes", "") or "").strip()
    label_id_in = filters.get("label_id_in") or []
    label_id_prefix = str(filters.get("label_id_prefix", "") or "").strip()
    exclude_label_id_prefix = str(filters.get("exclude_label_id_prefix", "") or "").strip()
    exclude_label_id_exact = filters.get("exclude_label_id_exact") or []

    if dimension_includes and "dimension" in filtered.columns:
        filtered = filtered[
            filtered["dimension"].astype(str).str.lower().str.contains(re.escape(dimension_includes.lower()), regex=True)
        ]
    if label_includes and "label" in filtered.columns:
        filtered = filtered[
            filtered["label"].astype(str).str.lower().str.contains(re.escape(label_includes.lower()), regex=True)
        ]
    if label_id_in and "label_id" in filtered.columns:
        as_str = {str(v).strip() for v in label_id_in}
        filtered = filtered[filtered["label_id"].astype(str).str.strip().isin(as_str)]
    if label_id_prefix and "label_id" in filtered.columns:
        filtered = filtered[filtered["label_id"].astype(str).str.startswith(label_id_prefix)]
    if exclude_label_id_prefix and "label_id" in filtered.columns:
        filtered = filtered[~filtered["label_id"].astype(str).str.startswith(exclude_label_id_prefix)]
    if exclude_label_id_exact and "label_id" in filtered.columns:
        exclude = {str(v).strip() for v in exclude_label_id_exact if str(v).strip()}
        filtered = filtered[~filtered["label_id"].astype(str).str.strip().isin(exclude)]

    logger.info("Filter result rows: %s -> %s", len(df), len(filtered))
    return filtered


def _format_rows(rows: pd.DataFrame, metric: str) -> str:
    lines: list[str] = []
    for _, row in rows.iterrows():
        lines.append(
            "label_id={label_id}, label='{label}', dimension='{dimension}', {metric}={metric_value}".format(
                label_id=row.get("label_id", ""),
                label=str(row.get("label", "")).strip(),
                dimension=str(row.get("dimension", "")).strip(),
                metric=metric,
                metric_value=row.get(metric),
            )
        )
    return "\n".join(lines)


def _execute_plan(df: pd.DataFrame, question: str, plan: dict[str, Any]) -> str:
    payload = _execute_plan_payload(df, question, plan)
    return str(payload.get("summary", "")).strip() or "No result."


def _execute_plan_payload(df: pd.DataFrame, question: str, plan: dict[str, Any]) -> dict[str, Any]:
    logger.info("Execute plan start: operation=%s metric=%s", plan.get("operation"), plan.get("metric"))
    required = {"label_id", "label", "dimension", "topbox", "bottombox", "mean", "variance", "correlation"}
    missing = sorted(required - set(df.columns))
    if missing:
        logger.warning("Missing expected columns: %s", missing)
        return {
            "summary": f"Survey data is missing expected columns: {', '.join(missing)}.",
            "operation": "error",
            "metric": "",
            "rows": [],
        }

    operation = str(plan["operation"])
    if operation == "compare":
        return _execute_compare_plan_payload(df, question, plan)

    metric = str(plan["metric"])
    sort = str(plan["sort"])
    aggregate = str(plan["aggregate"])
    limit = int(plan["limit"])

    if operation == "count":
        logger.info("Execute count result rows=%s", len(df))
        return {
            "summary": f"Survey data has {len(df)} records.",
            "operation": "count",
            "metric": metric,
            "rows": [],
        }

    if operation == "list_dimensions":
        dims = sorted(df["dimension"].dropna().astype(str).str.strip().loc[lambda s: s != ""].unique().tolist())
        logger.info("Execute list_dimensions result count=%s", len(dims))
        return {
            "summary": f"Dimensions: {', '.join(dims)}.",
            "operation": "list_dimensions",
            "metric": metric,
            "rows": [],
            "dimensions": dims,
        }

    filtered = _apply_filters(df, plan)
    if filtered.empty:
        return {
            "summary": "No rows match the requested filters.",
            "operation": operation,
            "metric": metric,
            "rows": [],
        }

    filtered = filtered.copy()
    filtered[metric] = pd.to_numeric(filtered[metric], errors="coerce")
    filtered = filtered.dropna(subset=[metric])
    if filtered.empty:
        logger.warning("No numeric rows after metric conversion for metric=%s", metric)
        return {
            "summary": f"No valid numeric values found for metric '{metric}'.",
            "operation": operation,
            "metric": metric,
            "rows": [],
        }

    if operation == "aggregate":
        series = filtered[metric]
        if aggregate == "mean":
            value = round(float(series.mean()), 4)
        elif aggregate == "sum":
            value = round(float(series.sum()), 4)
        elif aggregate == "min":
            value = round(float(series.min()), 4)
        elif aggregate == "max":
            value = round(float(series.max()), 4)
        else:
            value = int(series.count())
        logger.info("Execute aggregate result metric=%s aggregate=%s value=%s", metric, aggregate, value)
        return {
            "summary": f"{aggregate} {metric} = {value} (rows={len(filtered)}).",
            "operation": "aggregate",
            "metric": metric,
            "rows": [],
            "aggregate": aggregate,
            "value": value,
        }

    ranked = filtered.sort_values(by=metric, ascending=(sort == "asc")).head(limit)
    direction = "lowest" if sort == "asc" else "highest"
    logger.info("Execute rank result direction=%s limit=%s rows=%s", direction, limit, len(ranked))
    ranked_rows = ranked.loc[:, ["label_id", "label", "dimension", metric]].copy()
    ranked_rows[metric] = pd.to_numeric(ranked_rows[metric], errors="coerce").round(6)
    rows = ranked_rows.to_dict(orient="records")
    return {
        "summary": f"Top {len(ranked)} {direction} rows by {metric}:\n{_format_rows(ranked, metric)}",
        "operation": "rank",
        "metric": metric,
        "sort": sort,
        "limit": limit,
        "rows": rows,
    }


def _extract_primary_value(payload: dict[str, Any]) -> float | None:
    operation = str(payload.get("operation", ""))
    if operation == "aggregate":
        value = payload.get("value")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    rows = payload.get("rows") or []
    metric = str(payload.get("metric", ""))
    if isinstance(rows, list) and rows and metric:
        val = rows[0].get(metric)
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    return None


def _render_compare_summary_with_llm(
    question: str,
    comparison_items: list[dict[str, Any]],
    metric: str,
) -> str:
    try:
        client = _get_azure_client()
        model = os.getenv("MCP_PLANNER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
        system_prompt = (
            "You are a strict comparison writer.\n"
            "Use only provided structured results.\n"
            "Do not invent values.\n"
            "Return concise comparison with ranking and key differences.\n"
            "Do not include graph/chart concepts or plotting suggestions."
        )
        reduced_items = [
            {
                "index": item.get("index"),
                "question": item.get("question"),
                "label": item.get("label"),
                "dimension": item.get("dimension"),
                "value": item.get("value"),
            }
            for item in comparison_items
        ]
        user_prompt = (
            f"User question: {question}\n"
            f"Metric: {metric}\n"
            f"Comparison items: {json.dumps(reduced_items)}\n"
            "Write the final comparison."
        )
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or "Comparison complete."
    except Exception:
        valid = [item for item in comparison_items if item.get("value") is not None]
        if valid:
            ranking = sorted(valid, key=lambda item: float(item["value"]), reverse=True)
            top = ranking[0]
            bottom = ranking[-1]
            delta = round(float(top["value"]) - float(bottom["value"]), 4)
            return (
                f"Comparison complete for {len(valid)} items. "
                f"Highest: {top.get('label') or top.get('question')}={top.get('value')}; "
                f"Lowest: {bottom.get('label') or bottom.get('question')}={bottom.get('value')}; "
                f"delta={delta}."
            )
        return "Comparison complete."


def _execute_compare_plan_payload(df: pd.DataFrame, question: str, plan: dict[str, Any]) -> dict[str, Any]:
    queries = plan.get("queries") or []
    if not isinstance(queries, list) or len(queries) < 2:
        return {
            "summary": "Comparison plan is missing required sub-queries.",
            "operation": "compare",
            "metric": "",
            "rows": [],
        }

    items: list[dict[str, Any]] = []
    chosen_metric = ""
    for idx, sub_plan in enumerate(queries):
        if not isinstance(sub_plan, dict):
            continue
        sub_question = str(sub_plan.get("question", "") or question)
        sub_payload = _execute_plan_payload(df, sub_question, sub_plan)
        metric = str(sub_payload.get("metric", "") or "")
        if metric and not chosen_metric:
            chosen_metric = metric

        rows = sub_payload.get("rows") or []
        first_row = rows[0] if isinstance(rows, list) and rows else {}
        item = {
            "index": idx + 1,
            "question": sub_question,
            "payload": sub_payload,
            "value": _extract_primary_value(sub_payload),
            "label_id": str(first_row.get("label_id", "") or f"item_{idx + 1}"),
            "label": str(first_row.get("label", "") or sub_question),
            "dimension": str(first_row.get("dimension", "") or ""),
        }
        items.append(item)

    valid_items = [item for item in items if item.get("value") is not None]
    ranked_items = sorted(valid_items, key=lambda item: float(item["value"]), reverse=True)
    summary = _render_compare_summary_with_llm(question, items, chosen_metric or "value")

    compare_rows: list[dict[str, Any]] = []
    metric_key = chosen_metric or "value"
    for item in ranked_items:
        compare_rows.append(
            {
                "label_id": item.get("label_id", ""),
                "label": item.get("label", ""),
                "dimension": item.get("dimension", ""),
                metric_key: float(item["value"]),
            }
        )

    winner = ranked_items[0] if ranked_items else None
    return {
        "summary": summary,
        "operation": "compare",
        "metric": metric_key,
        "rows": compare_rows,
        "comparisons": items,
        "winner": winner.get("label") if winner else "unknown",
    }


def _analyze_dataframe(df: pd.DataFrame, question: str) -> str:
    logger.info("Analyze start: question=%s", question)
    if df.empty:
        logger.warning("Analyze aborted: empty dataframe")
        return "No survey data is available for this user."

    normalized = _normalize_dataframe(df)
    logger.info("Dataframe loaded rows=%s cols=%s", len(normalized), len(normalized.columns))
    try:
        plan = _plan_query_with_llm(question, normalized)
    except Exception:
        logger.exception("Analyze aborted: planner error")
        return "Unable to interpret this survey question right now. Please rephrase and try again."
    result = _execute_plan(normalized, question, plan)
    logger.info("Analyze complete")
    return result


def _analyze_dataframe_payload(df: pd.DataFrame, question: str) -> dict[str, Any]:
    logger.info("Analyze payload start: question=%s", question)
    if df.empty:
        logger.warning("Analyze payload aborted: empty dataframe")
        return {
            "summary": "No survey data is available for this user.",
            "operation": "empty",
            "metric": "",
            "rows": [],
        }

    normalized = _normalize_dataframe(df)
    logger.info("Payload dataframe loaded rows=%s cols=%s", len(normalized), len(normalized.columns))
    try:
        plan = _plan_query_with_llm(question, normalized)
    except Exception:
        logger.exception("Analyze payload aborted: planner error")
        return {
            "summary": "Unable to interpret this survey question right now. Please rephrase and try again.",
            "operation": "planner_error",
            "metric": "",
            "rows": [],
        }
    payload = _execute_plan_payload(normalized, question, plan)
    logger.info("Analyze payload complete")
    return payload


def _build_graph_spec(question: str, rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    if not rows:
        return {}
    x = [str(row.get("label", row.get("label_id", ""))) for row in rows]
    y: list[float] = []
    for row in rows:
        try:
            y.append(float(row.get(metric, 0)))
        except (TypeError, ValueError):
            y.append(0.0)
    return {
        "kind": "bar",
        "title": f"{metric.title()} by Label",
        "x": x,
        "y": y,
        "x_title": "Label",
        "y_title": metric.title(),
        "question": question,
    }


def _extract_stock_plan(question: str) -> dict[str, Any]:
    system_prompt = (
        "Extract stock query parameters.\n"
        "Return strict JSON: {\"tickers\":[\"...\"],\"companies\":[\"...\"],\"period\":\"...\"}.\n"
        "tickers should be Yahoo Finance symbols when user explicitly provides or implies them.\n"
        "companies should contain company names when user asks by company name.\n"
        "If user asks multiple companies/tickers, include all relevant entities.\n"
        "period must be one of: 5d,1mo,3mo,6mo,1y,2y,5y,max.\n"
        "If unclear, choose sensible defaults."
    )
    try:
        client = _get_azure_client()
        model = os.getenv("MCP_PLANNER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
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
        period = str(parsed.get("period", "6mo")).strip()
        if period not in {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}:
            period = "6mo"
        companies_raw = parsed.get("companies") or []
        companies: list[str] = []
        if isinstance(companies_raw, list):
            for name in companies_raw:
                company = str(name).strip()
                if company and company not in companies:
                    companies.append(company)
        elif isinstance(companies_raw, str):
            company = companies_raw.strip()
            if company:
                companies.append(company)

        return {"tickers": tickers[:5], "companies": companies[:5], "period": period}
    except Exception:
        return {"tickers": [], "companies": [], "period": "6mo"}


def _fallback_extract_tickers(question: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][A-Z0-9.\-]{0,9}\b", question)
    blocked = {"I", "A", "AN", "THE", "AND", "OR", "TO", "FOR", "WITH", "IN", "ON"}
    tickers: list[str] = []
    for token in candidates:
        if token in blocked:
            continue
        if token not in tickers:
            tickers.append(token)
    return tickers


def _resolve_tickers_from_company_names(company_names: list[str], max_count: int = 5) -> list[str]:
    if not company_names:
        return []
    try:
        import yfinance as yf
    except Exception:
        return []

    resolved: list[str] = []
    for company in company_names[:10]:
        query = str(company).strip()
        if not query:
            continue
        try:
            search = yf.Search(query, max_results=8, news_count=0, lists_count=0, include_research=False)
            quotes = getattr(search, "quotes", []) or []
        except Exception:
            quotes = []

        best_symbol = ""
        for item in quotes:
            if not isinstance(item, dict):
                continue
            quote_type = str(item.get("quoteType") or item.get("typeDisp") or "").lower()
            symbol = str(item.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            if quote_type and quote_type not in {"equity", "etf"}:
                continue
            best_symbol = symbol
            break
        if best_symbol and best_symbol not in resolved:
            resolved.append(best_symbol)
        if len(resolved) >= max_count:
            break
    return resolved[:max_count]


def _extract_requested_company_count(question: str) -> int:
    system_prompt = (
        "Extract requested number of companies for stock comparison.\n"
        "Return JSON only: {\"count\": number}.\n"
        "If not specified, return 2. Clamp to 1..5."
    )
    try:
        client = _get_azure_client()
        model = os.getenv("MCP_PLANNER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
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

        extract_prompt = (
            "Extract ticker symbols from snippets. "
            f"Return JSON only: {{\"tickers\":[...]}} with max {count} symbols."
        )
        azure = _get_azure_client()
        model = os.getenv("MCP_PLANNER_MODEL") or os.getenv("LLM_MODEL") or _required_env("AZURE_OPENAI_MODEL")
        resp = azure.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": extract_prompt},
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


def _fetch_single_ticker_payload(ticker: str, period: str) -> dict[str, Any]:
    try:
        import yfinance as yf
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


def _fetch_stock_payload(question: str, industry: str = "") -> dict[str, Any]:
    if not question.strip():
        return {"error": "Stock query is empty."}
    try:
        import yfinance  # noqa: F401
    except Exception:
        return {"error": "Stock data service is not available right now."}

    plan = _extract_stock_plan(question)
    tickers = plan.get("tickers") or []
    if not isinstance(tickers, list):
        tickers = []
    tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
    company_names = plan.get("companies") or []
    if not isinstance(company_names, list):
        company_names = []
    company_names = [str(name).strip() for name in company_names if str(name).strip()]

    explicit_tickers = _fallback_extract_tickers(question)
    if explicit_tickers:
        tickers = [t for t in tickers if t in explicit_tickers] or explicit_tickers
    else:
        requested_count = _extract_requested_company_count(question)
        if company_names:
            resolved = _resolve_tickers_from_company_names(company_names, max_count=requested_count)
            if resolved:
                tickers = resolved
        if not tickers:
            discovered = _discover_tickers_for_industry(industry=industry, count=requested_count)
            if discovered:
                tickers = discovered

    tickers = list(dict.fromkeys(tickers))[:5]
    period = str(plan.get("period") or "6mo")
    if not tickers:
        return {"error": "I could not identify the stock ticker(s). Please include symbols like AAPL or MSFT."}

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
    summary = f"Stock comparison over {period}:\n- " + "\n- ".join(summary_parts)
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


mcp = FastMCP(
    "survey-data-server",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8765")),
    streamable_http_path="/mcp",
)


@mcp.tool(name="query_survey_data")
def query_survey_data(user_id: str, question: str) -> str:
    logger.info("Tool call: query_survey_data user_id=%s", user_id)
    if not user_id.strip():
        logger.warning("Tool call rejected: missing user_id")
        return "User id is required."
    if not question.strip():
        logger.warning("Tool call rejected: missing question")
        return "Question is required."

    df = get_user_dataframe(user_id)
    logger.info("Tool dataframe fetched: rows=%s", len(df))
    return _analyze_dataframe(df, question)


@mcp.tool(name="query_survey_data_payload")
def query_survey_data_payload(user_id: str, question: str) -> dict[str, Any]:
    logger.info("Tool call: query_survey_data_payload user_id=%s", user_id)
    if not user_id.strip():
        return {"summary": "User id is required.", "operation": "error", "metric": "", "rows": []}
    if not question.strip():
        return {"summary": "Question is required.", "operation": "error", "metric": "", "rows": []}
    df = get_user_dataframe(user_id)
    logger.info("Tool payload dataframe fetched: rows=%s", len(df))
    return _analyze_dataframe_payload(df, question)


@mcp.tool(name="create_survey_graph")
def create_survey_graph(question: str, metric: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    logger.info("Tool call: create_survey_graph rows=%s metric=%s", len(rows) if isinstance(rows, list) else -1, metric)
    if not isinstance(rows, list) or not rows:
        return {"summary": "No rows available for graph.", "graph": {}}
    graph = _build_graph_spec(question=question, rows=rows, metric=metric or "topbox")
    return {"summary": "Graph generated.", "graph": graph}


@mcp.tool(name="query_stock_data_payload")
def query_stock_data_payload(question: str, industry: str = "", company_size: str = "") -> dict[str, Any]:
    logger.info(
        "Tool call: query_stock_data_payload industry=%s company_size=%s",
        industry,
        company_size,
    )
    _ = company_size  # Reserved for future expansion.
    return _fetch_stock_payload(question=question, industry=industry)


@mcp.tool(name="query_stock_data")
def query_stock_data(question: str, industry: str = "", company_size: str = "") -> str:
    payload = query_stock_data_payload(question=question, industry=industry, company_size=company_size)
    if payload.get("error"):
        return str(payload.get("error"))
    return json.dumps(payload)


@mcp.tool(name="hydrate_survey_data")
def hydrate_survey_data(user_id: str, rows: list[dict[str, Any]]) -> str:
    logger.info("Tool call: hydrate_survey_data user_id=%s", user_id)
    if not user_id.strip():
        return "User id is required."
    df = _set_user_dataframe(user_id, rows)
    logger.info("Hydrated dataframe rows=%s cols=%s", len(df), len(df.columns))
    return f"Hydrated {len(df)} survey rows for user."


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
