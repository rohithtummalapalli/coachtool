import os
import base64
import inspect
import socket
import subprocess
import sys
import asyncio
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import chainlit as cl
import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from chainlit.input_widget import Select, Slider
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data.storage_clients.base import BaseStorageClient
from chainlit.data.acl import is_thread_author
from chainlit.server import app as chainlit_app
from chainlit.server import get_data_layer as get_chainlit_server_data_layer
from chainlit.server import UserParam
from openai import AsyncAzureOpenAI
import plotly.graph_objects as go
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from services.langchain_agent import predict_loading_stage, run_agent
from services.mcp_client import hydrate_survey_data


Path(".files").mkdir(parents=True, exist_ok=True)
_django_process: subprocess.Popen | None = None
_django_log_handle = None
_mcp_process: subprocess.Popen | None = None


def _encode_blob_key(blob_key: str) -> str:
    return base64.urlsafe_b64encode(blob_key.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_blob_key(encoded: str) -> str:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")


class LocalFileStorageClient(BaseStorageClient):
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _safe_rel_key(self, object_key: str) -> str:
        raw = (object_key or "").strip().replace("\\", "/")
        raw = raw.lstrip("/")
        safe_parts: list[str] = []
        for part in raw.split("/"):
            if not part or part in {".", ".."}:
                continue
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", part)
            if safe:
                safe_parts.append(safe)
        if not safe_parts:
            safe_parts = ["unknown"]
        return "/".join(safe_parts)

    def _object_path(self, object_key: str) -> Path:
        rel_key = self._safe_rel_key(object_key)
        target = (self.root_dir / rel_key).resolve()
        if self.root_dir not in target.parents and target != self.root_dir:
            raise ValueError("Invalid object_key path")
        return target

    def _meta_path(self, object_key: str) -> Path:
        target = self._object_path(object_key)
        return Path(str(target) + ".meta.json")

    async def upload_file(
        self,
        object_key: str,
        data: bytes | str,
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> Dict[str, Any]:
        target = self._object_path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"File already exists for key: {object_key}")
        payload = data.encode("utf-8") if isinstance(data, str) else data
        target.write_bytes(payload)
        meta = {
            "mime": mime or "application/octet-stream",
            "content_disposition": content_disposition or "",
        }
        self._meta_path(object_key).write_text(json.dumps(meta), encoding="utf-8")
        url = await self.get_read_url(object_key)
        return {"object_key": object_key, "url": url}

    async def delete_file(self, object_key: str) -> bool:
        deleted = False
        target = self._object_path(object_key)
        meta = self._meta_path(object_key)
        if target.exists():
            target.unlink()
            deleted = True
        if meta.exists():
            meta.unlink()
            deleted = True
        return deleted

    async def get_read_url(self, object_key: str) -> str:
        encoded_key = _encode_blob_key(object_key)
        # Keep URL relative to avoid cross-origin/cookie issues between localhost and 127.0.0.1.
        return f"/project/local-blob/{encoded_key}"

    async def close(self) -> None:
        return


_storage_client = LocalFileStorageClient(Path(".files") / "blob_storage")


def get_chainlit_database_url() -> str:
    configured = os.getenv("CHAINLIT_DATABASE_URL", "").strip()
    if configured:
        return configured

    backend_db_path = (Path.cwd() / "backend" / "db.sqlite3").resolve()
    return f"sqlite+aiosqlite:///{backend_db_path.as_posix()}"


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_client() -> AsyncAzureOpenAI:
    return AsyncAzureOpenAI(
        azure_endpoint=get_required_env("AZURE_OPENAI_ENDPOINT"),
        api_key=get_required_env("AZURE_OPENAI_API_KEY"),
        api_version=get_required_env("AZURE_OPENAI_API_VERSION"),
    )


def get_chat_settings() -> dict[str, Any]:
    return cl.user_session.get("chat_settings") or get_default_settings()


def get_default_settings() -> dict[str, Any]:
    return {
        "model": get_required_env("AZURE_OPENAI_MODEL"),
        "temperature": 0.2,
        "history_window": 20,
        "response_style": "balanced",
        "language": "English",
    }


def get_agent_history_window() -> int:
    raw = os.getenv("CHAINLIT_AGENT_HISTORY_WINDOW", "10").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 10
    return max(1, min(50, value))


def get_model_options() -> list[str]:
    configured = os.getenv("AZURE_OPENAI_MODELS", "").strip()
    if configured:
        options = [m.strip() for m in configured.split(",") if m.strip()]
        if options:
            return options
    return [get_required_env("AZURE_OPENAI_MODEL")]


def build_user_preference_prompt(settings: dict[str, Any]) -> str:
    language = settings.get("language", "English")
    style = settings.get("response_style", "balanced")
    return (
        "Follow user preferences.\n"
        f"Language: {language}\n"
        f"Response style: {style}\n"
    )


def get_auth_validate_url() -> str:
    configured = os.getenv("DJANGO_AUTH_VALIDATE_URL")
    if configured:
        return configured
    host, port = get_django_host_port()
    return f"http://{host}:{port}/api/accounts/me/"


def get_auth_login_url() -> str:
    configured = os.getenv("DJANGO_AUTH_LOGIN_URL")
    if configured:
        return configured
    host, port = get_django_host_port()
    return f"http://{host}:{port}/api/accounts/chainlit-login/"


def get_django_favorites_url() -> str:
    configured = os.getenv("DJANGO_FAVORITES_URL")
    if configured:
        return configured
    host, port = get_django_host_port()
    return f"http://{host}:{port}/api/accounts/favorites/"


def get_django_host_port() -> tuple[str, int]:
    raw = os.getenv("DJANGO_RUN_ADDR", "127.0.0.1:8001")
    if ":" not in raw:
        return raw, 8001
    host, port = raw.rsplit(":", 1)
    try:
        return host, int(port)
    except ValueError:
        return host, 8001


def get_mcp_host_port() -> tuple[str, int]:
    server_url = os.getenv("MCP_SERVER_URL", "").strip()
    if server_url:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(server_url)
            host = parsed.hostname or "127.0.0.1"
            port = int(parsed.port or 8765)
            return host, port
        except Exception:
            pass

    host = os.getenv("MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.getenv("MCP_PORT", "8765"))
    except ValueError:
        port = 8765
    return host, port


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def start_django_backend() -> None:
    global _django_process, _django_log_handle
    host, port = get_django_host_port()
    if is_port_open(host, port):
        return

    backend_dir = Path("backend")
    manage_py = backend_dir / "manage.py"
    if not manage_py.exists():
        return

    child_env = os.environ.copy()
    # Force local-dev backend behavior when auto-started by Chainlit.
    child_env.setdefault("DJANGO_DEBUG", "True")
    child_env["SECURE_SSL_REDIRECT"] = "False"
    # Canonical DB env key for Django backend.
    db_url = child_env.get("DATABASE_URL", "").strip()
    if not db_url or db_url.startswith("sqlite+aiosqlite://"):
        db_url = "sqlite:///db.sqlite3"
    child_env["DATABASE_URL"] = db_url

    log_to_file = os.getenv("DJANGO_AUTOSTART_LOG_TO_FILE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    log_path: Path | None = None
    popen_stdout = None
    popen_stderr = None

    if log_to_file:
        log_dir = Path(".files")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "django_autostart.log"
        _django_log_handle = open(log_path, "a", encoding="utf-8")
        popen_stdout = _django_log_handle
        popen_stderr = subprocess.STDOUT

    _django_process = subprocess.Popen(
        [
            sys.executable,
            "manage.py",
            "runserver",
            f"{host}:{port}",
            "--noreload",
        ],
        cwd=backend_dir,
        env=child_env,
        stdout=popen_stdout,
        stderr=popen_stderr,
        text=True,
    )
    # Wait briefly for backend readiness; keep process alive for Chainlit lifetime.
    for _ in range(30):
        if is_port_open(host, port):
            return
        if _django_process.poll() is not None:
            output = ""
            if log_path:
                try:
                    output = log_path.read_text(encoding="utf-8")[-2000:]
                except Exception:
                    output = ""
            raise RuntimeError(
                f"Django backend failed to start on {host}:{port}. {output[:1000]}"
            )
        import time

        time.sleep(0.2)
    raise RuntimeError(f"Django backend did not become ready on {host}:{port}.")


def start_mcp_server() -> None:
    global _mcp_process
    host, port = get_mcp_host_port()
    if is_port_open(host, port):
        return

    mcp_server_file = Path("mcp_server") / "server.py"
    if not mcp_server_file.exists():
        return

    child_env = os.environ.copy()
    child_env.setdefault("MCP_HOST", host)
    child_env.setdefault("MCP_PORT", str(port))

    _mcp_process = subprocess.Popen(
        [sys.executable, str(mcp_server_file)],
        cwd=Path.cwd(),
        env=child_env,
        text=True,
    )

    for _ in range(40):
        if is_port_open(host, port):
            return
        if _mcp_process.poll() is not None:
            raise RuntimeError(f"MCP server failed to start on {host}:{port}.")
        import time

        time.sleep(0.2)
    raise RuntimeError(f"MCP server did not become ready on {host}:{port}.")


@cl.data_layer
def get_data_layer():
    return SQLAlchemyDataLayer(
        conninfo=get_chainlit_database_url(),
        storage_provider=_storage_client,
        show_logger=False,
    )


@chainlit_app.put("/project/thread/favorite")
async def favorite_thread(payload: dict, current_user: UserParam):
    data_layer = get_chainlit_server_data_layer()
    if not data_layer:
        raise HTTPException(status_code=400, detail="Data persistence is not enabled")
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    thread_id = str(payload.get("threadId", "")).strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="threadId is required")

    is_favorite = bool(payload.get("isFavorite"))

    await is_thread_author(current_user.identifier, thread_id)

    favorites_url = get_django_favorites_url()
    internal_token = os.getenv("CHAINLIT_INTERNAL_API_TOKEN", "").strip()
    headers: dict[str, str] = {}
    if internal_token:
        headers["X-Internal-Token"] = internal_token

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.put(
                favorites_url,
                json={
                    "user_id": str(current_user.identifier),
                    "thread_id": thread_id,
                    "is_favorite": is_favorite,
                },
                headers=headers,
            )
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to persist favorite state")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Favorites service unavailable")

    thread = await data_layer.get_thread(thread_id=thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    metadata = thread.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            import json

            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    metadata = dict(metadata)
    metadata["is_favorite"] = is_favorite
    if is_favorite:
        metadata["favorite_at"] = datetime.now(timezone.utc).isoformat()
    else:
        metadata.pop("favorite_at", None)

    await data_layer.update_thread(thread_id=thread_id, metadata=metadata)
    return JSONResponse(content={"success": True})


@chainlit_app.get("/project/favorites")
async def list_favorites(current_user: UserParam):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    favorites_url = get_django_favorites_url()
    internal_token = os.getenv("CHAINLIT_INTERNAL_API_TOKEN", "").strip()
    headers: dict[str, str] = {}
    if internal_token:
        headers["X-Internal-Token"] = internal_token

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                favorites_url,
                params={"user_id": str(current_user.identifier)},
                headers=headers,
            )
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch favorites")
        payload = res.json() if res.content else {}
        thread_ids = payload.get("thread_ids") or []
        if not isinstance(thread_ids, list):
            thread_ids = []
        return JSONResponse(content={"thread_ids": [str(tid) for tid in thread_ids]})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Favorites service unavailable")


@chainlit_app.get("/project/local-blob/{encoded_key}")
async def read_local_blob(encoded_key: str, current_user: UserParam):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        blob_key = _decode_blob_key(encoded_key)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid blob key")

    key_owner = blob_key.split("/", 1)[0].strip()
    identifier = str(current_user.identifier)
    allowed_owners = {identifier}

    data_layer = get_chainlit_server_data_layer()
    if data_layer:
        try:
            persisted_user = await data_layer.get_user(identifier=identifier)
            if persisted_user and getattr(persisted_user, "id", None):
                allowed_owners.add(str(persisted_user.id))
        except Exception:
            pass

    if key_owner and key_owner not in allowed_owners:
        authorized_via_thread = False
        if data_layer:
            try:
                rows = await data_layer.execute_sql(
                    query='SELECT "threadId" FROM "elements" WHERE "objectKey" = :object_key LIMIT 1',
                    parameters={"object_key": blob_key},
                )
                if isinstance(rows, list) and rows:
                    thread_id = str((rows[0] or {}).get("threadId") or "").strip()
                    if thread_id:
                        thread = await data_layer.get_thread(thread_id=thread_id)
                        if isinstance(thread, dict):
                            thread_user_identifier = str(thread.get("userIdentifier") or "").strip()
                            thread_user_id = str(thread.get("userId") or "").strip()
                            if (
                                thread_user_identifier == identifier
                                or thread_user_id in allowed_owners
                            ):
                                authorized_via_thread = True
            except Exception:
                authorized_via_thread = False

        if not authorized_via_thread:
            raise HTTPException(status_code=403, detail="Forbidden")

    file_path = _storage_client._object_path(blob_key)
    if not file_path.exists():
        # DB fallback: if plotly JSON was persisted in element props, serve it directly.
        if data_layer:
            try:
                rows = await data_layer.execute_sql(
                    query='SELECT "props", "mime" FROM "elements" WHERE "objectKey" = :object_key LIMIT 1',
                    parameters={"object_key": blob_key},
                )
                if isinstance(rows, list) and rows:
                    row = rows[0] or {}
                    raw_props = row.get("props")
                    props_obj: dict[str, Any] = {}
                    if isinstance(raw_props, str) and raw_props.strip():
                        try:
                            parsed = json.loads(raw_props)
                            if isinstance(parsed, dict):
                                props_obj = parsed
                        except Exception:
                            props_obj = {}
                    elif isinstance(raw_props, dict):
                        props_obj = raw_props
                    figure_json = props_obj.get("figure_json")
                    if isinstance(figure_json, dict):
                        return JSONResponse(content=figure_json)
            except Exception:
                pass
        raise HTTPException(status_code=404, detail="Blob not found")

    mime = "application/octet-stream"
    meta_path = _storage_client._meta_path(blob_key)
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            parsed_mime = str(meta.get("mime", "")).strip()
            if parsed_mime:
                mime = parsed_mime
        except Exception:
            pass

    return FileResponse(path=file_path, media_type=mime, filename=file_path.name)


def stop_django_backend() -> None:
    global _django_process, _django_log_handle
    if _django_process and _django_process.poll() is None:
        _django_process.terminate()
        try:
            _django_process.wait(timeout=5)
        except Exception:
            _django_process.kill()
    _django_process = None
    if _django_log_handle:
        try:
            _django_log_handle.close()
        except Exception:
            pass
    _django_log_handle = None


def stop_mcp_server() -> None:
    global _mcp_process
    if _mcp_process and _mcp_process.poll() is None:
        _mcp_process.terminate()
        try:
            _mcp_process.wait(timeout=5)
        except Exception:
            _mcp_process.kill()
    _mcp_process = None


def is_header_auth_enabled() -> bool:
    return os.getenv("CHAINLIT_ENABLE_HEADER_AUTH", "false").strip().lower() in {"1", "true", "yes", "on"}


def extract_bearer_token(headers: dict) -> str | None:
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if not auth_header:
        return None
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    return token or None


def _pick_first_non_empty(source: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def build_user_metadata(user_payload: dict[str, Any], root_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root_payload if isinstance(root_payload, dict) else {}
    organization = user_payload.get("organization")
    if not isinstance(organization, dict):
        organization = root.get("organization")
    if not isinstance(organization, dict):
        organization = {}

    industry = (
        _pick_first_non_empty(user_payload, ["industry", "Industry"])
        or _pick_first_non_empty(organization, ["industry", "Industry"])
        or _pick_first_non_empty(root, ["industry", "Industry"])
    )
    company_size = (
        _pick_first_non_empty(user_payload, ["company_size", "companySize", "size"])
        or _pick_first_non_empty(organization, ["company_size", "companySize", "size"])
        or _pick_first_non_empty(root, ["company_size", "companySize", "size"])
    )
    first_name = _pick_first_non_empty(user_payload, ["first_name", "firstName"])
    last_name = _pick_first_non_empty(user_payload, ["last_name", "lastName"])
    username = _pick_first_non_empty(user_payload, ["username"])
    email = _pick_first_non_empty(user_payload, ["email"])
    team_name = _pick_first_non_empty(user_payload, ["team_name", "teamName"])
    company_name = (
        _pick_first_non_empty(user_payload, ["company_name", "companyName"])
        or _pick_first_non_empty(organization, ["company_name", "companyName"])
    )
    company_id = (
        _pick_first_non_empty(user_payload, ["company_id", "companyId"])
        or _pick_first_non_empty(organization, ["company_id", "companyId"])
    )
    year = (
        _pick_first_non_empty(user_payload, ["year", "jahr"])
        or _pick_first_non_empty(organization, ["year", "jahr"])
    )
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    display_name = full_name or username or email
    subtitle = " | ".join(part for part in [team_name, company_name] if part).strip()

    return {
        "email": email,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "team_name": team_name,
        "company_name": company_name,
        "company_id": company_id,
        "year": year,
        "industry": industry,
        "company_size": company_size,
        "display_name": display_name,
        "subtitle": subtitle,
        "organization": organization,
    }


def resolve_user_display_name(
    user_payload: dict[str, Any], metadata: dict[str, Any], user_id: str
) -> str:
    first_name = str(metadata.get("first_name") or "").strip()
    last_name = str(metadata.get("last_name") or "").strip()
    full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    if full_name:
        return full_name

    for key in ("display_name",):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value

    for key in ("first_name", "last_name"):
        value = str(user_payload.get(key) or "").strip()
        if value:
            return value

    return "User"


if is_header_auth_enabled():
    @cl.header_auth_callback
    async def header_auth_callback(headers: dict) -> cl.User | None:
        token = extract_bearer_token(headers)
        if not token:
            return None

        validate_url = get_auth_validate_url()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    validate_url,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if response.status_code != 200:
                return None
            payload = response.json()
            user = payload.get("user") or {}
            user_id = str(user.get("id", "")).strip()
            if not user_id:
                return None
            metadata = build_user_metadata(user, payload)
            return cl.User(
                identifier=user_id,
                display_name=resolve_user_display_name(user, metadata, user_id),
                metadata={
                    **metadata,
                    "auth_source": "django-header",
                },
            )
        except Exception:
            return None


@cl.password_auth_callback
async def password_auth_callback(username: str, password: str) -> cl.User | None:
    login_url = get_auth_login_url()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                login_url,
                json={"email": username, "password": password},
            )
        if response.status_code != 200:
            return None
        payload = response.json()
        user = payload.get("user") or {}
        user_id = str(user.get("id", "")).strip()
        if not user_id:
            return None
        metadata = build_user_metadata(user, payload)
        survey_data = payload.get("survey_data")
        print(
            f"[Auth] login success user_id={user_id} survey_data_type={type(survey_data).__name__}",
            flush=True,
        )
        if isinstance(survey_data, (list, dict)):
            hydrated = await hydrate_survey_data(user_id=user_id, survey_rows=survey_data)
            print(f"[Auth] MCP hydration status for {user_id}: {hydrated}", flush=True)
        else:
            print(f"[Auth] survey_data missing or unsupported for {user_id}", flush=True)
        return cl.User(
            identifier=user_id,
            display_name=resolve_user_display_name(user, metadata, user_id),
            metadata={
                **metadata,
                "auth_source": "django-password",
            },
        )
    except Exception:
        return None


async def ensure_chainlit_history_schema() -> None:
    database_url = get_chainlit_database_url()
    if not database_url:
        return

    engine = create_async_engine(database_url)
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            identifier TEXT UNIQUE NOT NULL,
            "createdAt" TEXT NOT NULL,
            metadata TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            "createdAt" TEXT,
            name TEXT,
            "userId" TEXT,
            "userIdentifier" TEXT,
            tags TEXT,
            metadata TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS steps (
            id TEXT PRIMARY KEY,
            name TEXT,
            type TEXT,
            "threadId" TEXT,
            "parentId" TEXT,
            streaming BOOLEAN,
            "waitForAnswer" BOOLEAN,
            "isError" BOOLEAN,
            metadata TEXT,
            tags TEXT,
            input TEXT,
            output TEXT,
            "createdAt" TEXT,
            start TEXT,
            "end" TEXT,
            generation TEXT,
            "showInput" TEXT,
            language TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS feedbacks (
            id TEXT PRIMARY KEY,
            "forId" TEXT,
            value REAL,
            comment TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS elements (
            id TEXT PRIMARY KEY,
            "threadId" TEXT,
            type TEXT,
            "chainlitKey" TEXT,
            url TEXT,
            "objectKey" TEXT,
            name TEXT,
            props TEXT,
            display TEXT,
            size TEXT,
            language TEXT,
            page INTEGER,
            "autoPlay" BOOLEAN,
            "playerConfig" TEXT,
            "forId" TEXT,
            mime TEXT
        )
        """,
    ]

    required_columns: dict[str, dict[str, str]] = {
        "users": {
            "id": "TEXT",
            "identifier": "TEXT",
            "createdAt": "TEXT",
            "metadata": "TEXT",
        },
        "threads": {
            "id": "TEXT",
            "createdAt": "TEXT",
            "name": "TEXT",
            "userId": "TEXT",
            "userIdentifier": "TEXT",
            "tags": "TEXT",
            "metadata": "TEXT",
        },
        "steps": {
            "id": "TEXT",
            "name": "TEXT",
            "type": "TEXT",
            "threadId": "TEXT",
            "parentId": "TEXT",
            "streaming": "BOOLEAN",
            "waitForAnswer": "BOOLEAN",
            "isError": "BOOLEAN",
            "metadata": "TEXT",
            "tags": "TEXT",
            "input": "TEXT",
            "output": "TEXT",
            "createdAt": "TEXT",
            "start": "TEXT",
            "end": "TEXT",
            "generation": "TEXT",
            "showInput": "TEXT",
            "defaultOpen": "BOOLEAN",
            "language": "TEXT",
        },
        "feedbacks": {
            "id": "TEXT",
            "forId": "TEXT",
            "value": "REAL",
            "comment": "TEXT",
        },
        "elements": {
            "id": "TEXT",
            "threadId": "TEXT",
            "type": "TEXT",
            "chainlitKey": "TEXT",
            "url": "TEXT",
            "objectKey": "TEXT",
            "name": "TEXT",
            "props": "TEXT",
            "display": "TEXT",
            "size": "TEXT",
            "language": "TEXT",
            "page": "INTEGER",
            "autoPlay": "BOOLEAN",
            "playerConfig": "TEXT",
            "forId": "TEXT",
            "mime": "TEXT",
        },
    }

    try:
        async with engine.begin() as conn:
            for statement in ddl:
                await conn.execute(text(statement))

            for table_name, columns in required_columns.items():
                result = await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
                existing = {row[1] for row in result.fetchall()}
                for column_name, column_type in columns.items():
                    if column_name in existing:
                        continue
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{table_name}" '
                            f'ADD COLUMN "{column_name}" {column_type}'
                        )
                    )

            # Normalize previously stored absolute local-blob URLs to relative paths.
            # This avoids auth cookie mismatch when app host is `localhost` but URL stored with `127.0.0.1`.
            try:
                rows_result = await conn.execute(
                    text(
                        'SELECT "id", "url" FROM "elements" '
                        'WHERE "url" IS NOT NULL'
                    )
                )
                rows = rows_result.fetchall()
                for row in rows:
                    row_id = row[0]
                    raw_url = str(row[1] or "").strip()
                    if not raw_url:
                        continue
                    lowered = raw_url.lower()
                    marker = "/project/local-blob/"
                    if marker not in lowered:
                        continue
                    if raw_url.startswith(marker):
                        continue
                    idx = lowered.find(marker)
                    if idx < 0:
                        continue
                    normalized_url = raw_url[idx:]
                    await conn.execute(
                        text('UPDATE "elements" SET "url" = :url WHERE "id" = :id'),
                        {"url": normalized_url, "id": row_id},
                    )
            except Exception:
                # URL normalization is best-effort and must not block startup.
                pass

            # Backfill plotly figure JSON into props when blob file exists.
            # This gives us a DB fallback if file storage becomes unavailable later.
            try:
                plotly_rows_result = await conn.execute(
                    text(
                        'SELECT "id", "objectKey", "props" FROM "elements" '
                        'WHERE "type" = :etype AND "objectKey" IS NOT NULL'
                    ),
                    {"etype": "plotly"},
                )
                for row in plotly_rows_result.fetchall():
                    element_id = row[0]
                    object_key = str(row[1] or "").strip()
                    raw_props = row[2]
                    if not object_key:
                        continue

                    props_obj: dict[str, Any] = {}
                    if isinstance(raw_props, str) and raw_props.strip():
                        try:
                            parsed = json.loads(raw_props)
                            if isinstance(parsed, dict):
                                props_obj = parsed
                        except Exception:
                            props_obj = {}
                    elif isinstance(raw_props, dict):
                        props_obj = raw_props

                    if isinstance(props_obj.get("figure_json"), dict):
                        continue

                    blob_path = _storage_client._object_path(object_key)
                    if not blob_path.exists():
                        continue
                    try:
                        figure_json = json.loads(blob_path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(figure_json, dict):
                        continue

                    props_obj = dict(props_obj)
                    props_obj["figure_json"] = figure_json
                    await conn.execute(
                        text('UPDATE "elements" SET "props" = :props WHERE "id" = :id'),
                        {"props": json.dumps(props_obj), "id": element_id},
                    )
            except Exception:
                # Backfill is best-effort and must not block startup.
                pass
    finally:
        await engine.dispose()


@cl.on_app_startup
async def on_app_startup() -> None:
    start_django_backend()
    start_mcp_server()
    await ensure_chainlit_history_schema()


@cl.on_app_shutdown
async def on_app_shutdown() -> None:
    stop_mcp_server()
    stop_django_backend()


def extract_conversation_from_thread(thread: dict[str, Any]) -> list[dict[str, Any]]:
    conversation: list[dict[str, Any]] = []
    for step in thread.get("steps", []):
        step_type = str(step.get("type", ""))
        output = str(step.get("output") or "").strip()
        if not output:
            continue
        if step_type == "user_message":
            conversation.append({"role": "user", "content": output})
        elif step_type == "assistant_message":
            conversation.append({"role": "assistant", "content": output})
    return conversation


def get_profile_payload() -> dict[str, Any]:
    user = cl.user_session.get("user")
    if not user:
        return {
            "display_name": "Guest",
            "subtitle": "Not signed in",
            "initials": "G",
            "is_authenticated": False,
        }

    metadata = getattr(user, "metadata", {}) or {}
    username = str(metadata.get("username") or "").strip()
    email = str(metadata.get("email") or "").strip()
    display_name = username or email or str(getattr(user, "identifier", "User"))
    subtitle = email or str(getattr(user, "identifier", ""))
    initials = "".join([part[:1].upper() for part in display_name.split()[:2]]) or "U"

    return {
        "display_name": display_name,
        "subtitle": subtitle,
        "initials": initials,
        "is_authenticated": True,
    }


async def push_profile_to_ui() -> None:
    await cl.send_window_message(
        {
            "event": "user_profile",
            "payload": get_profile_payload(),
        }
    )


@cl.step(type="tool")
async def execute_tool(tool_name: str, payload: dict[str, Any]) -> str:
    if tool_name == "utc_time":
        return datetime.now(timezone.utc).isoformat()
    if tool_name == "echo":
        return str(payload.get("text", ""))
    return f"Unknown tool: {tool_name}"


async def read_uploaded_files(message: cl.Message) -> str:
    collected: list[str] = []
    for element in message.elements or []:
        file_path = getattr(element, "path", None)
        if not file_path:
            continue
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            if text.strip():
                collected.append(f"[{getattr(element, 'name', 'file')}]\n{text[:4000]}")
        except Exception:
            continue
    return "\n\n".join(collected)


async def stream_text_response(text: str, citation: str | None = None) -> None:
    reply = cl.Message(content="")
    await reply.send()
    parts = re.split(r"(\s+)", text)
    for part in parts:
        if not part:
            continue
        await reply.stream_token(part)
    if citation:
        await reply.stream_token(f"\n\n{citation}")
    await reply.update()


async def send_graph_response(graph: dict[str, Any]) -> None:
    if not graph:
        return
    kind = str(graph.get("kind", "")).lower()
    x = graph.get("x") or []
    y = graph.get("y") or []
    title = str(graph.get("title", "Survey Graph"))
    if kind == "line_multi":
        series = graph.get("series") or []
        if not isinstance(series, list) or not series:
            return
        traces: list[go.Scatter] = []
        for idx, item in enumerate(series):
            if not isinstance(item, dict):
                continue
            sx = item.get("x") or []
            sy = item.get("y") or []
            if not isinstance(sx, list) or not isinstance(sy, list) or not sx or not sy:
                continue
            try:
                sy_values = [float(v) for v in sy]
            except (TypeError, ValueError):
                continue
            name = str(item.get("name", f"Series {idx + 1}"))
            traces.append(
                go.Scatter(
                    x=[str(v) for v in sx],
                    y=sy_values,
                    mode="lines",
                    name=name,
                    line=dict(width=3),
                    hovertemplate="<b>%{x}</b><br>"
                    + f"{name}: "
                    + "%{y:.2f}<extra></extra>",
                )
            )
        if not traces:
            return
        fig = go.Figure(data=traces)
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor="center"),
            paper_bgcolor="#0b1020",
            plot_bgcolor="#0b1020",
            font=dict(color="#e5e7eb"),
            xaxis=dict(
                title=str(graph.get("x_title", "Items")),
                showgrid=False,
                zeroline=False,
                color="#9ca3af",
            ),
            yaxis=dict(
                title=str(graph.get("y_title", "Value")),
                gridcolor="rgba(255,255,255,0.12)",
                zeroline=False,
                color="#9ca3af",
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(color="#e5e7eb"),
            ),
            hoverlabel=dict(bgcolor="#111827", font_color="#f9fafb"),
            height=520,
            margin=dict(l=60, r=40, t=90, b=60),
        )
        plotly_element = cl.Plotly(name="survey_graph", figure=fig, display="inline")
        plotly_element.props = {"figure_json": fig.to_plotly_json()}
        await cl.Message(
            content="Interactive chart generated. Hover bars to see full labels.",
            elements=[plotly_element],
        ).send()
        return

    if kind not in {"bar", "line"} or not isinstance(x, list) or not isinstance(y, list) or not x or not y:
        return
    labels = [str(v) for v in x]
    values: list[float] = []
    for val in y:
        try:
            values.append(float(val))
        except (TypeError, ValueError):
            values.append(0.0)
    if kind == "bar":
        x_axis = [str(i + 1) for i in range(len(labels))]
        data = [
            go.Bar(
                x=x_axis,
                y=values,
                customdata=labels,
                marker=dict(
                    color=values,
                    colorscale=[
                        [0.0, "#0ea5e9"],
                        [0.5, "#22d3ee"],
                        [1.0, "#34d399"],
                    ],
                    line=dict(color="rgba(255,255,255,0.25)", width=1),
                ),
                hovertemplate="<b>%{customdata}</b><br>"
                + f"{str(graph.get('y_title', 'Value'))}: "
                + "%{y}<extra></extra>",
            )
        ]
    else:
        x_axis = labels
        data = [
            go.Scatter(
                x=x_axis,
                y=values,
                mode="lines",
                line=dict(color="#34d399", width=3),
                hovertemplate="<b>%{x}</b><br>"
                + f"{str(graph.get('y_title', 'Value'))}: "
                + "%{y:.2f}<extra></extra>",
            )
        ]
    fig = go.Figure(data=data)
    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center"),
        paper_bgcolor="#0b1020",
        plot_bgcolor="#0b1020",
        font=dict(color="#e5e7eb"),
        xaxis=dict(
            title=str(graph.get("x_title", "Items")),
            showgrid=False,
            tickmode="array" if kind == "bar" else "auto",
            tickvals=x_axis if kind == "bar" else None,
            ticktext=[""] * len(x_axis) if kind == "bar" else None,
            zeroline=False,
            color="#9ca3af",
        ),
        yaxis=dict(
            title=str(graph.get("y_title", "Value")),
            gridcolor="rgba(255,255,255,0.12)",
            zeroline=False,
            color="#9ca3af",
        ),
        hoverlabel=dict(bgcolor="#111827", font_color="#f9fafb"),
        height=520,
        margin=dict(l=60, r=40, t=70, b=60),
    )
    plotly_element = cl.Plotly(name="survey_graph", figure=fig, display="inline")
    # Persist a DB fallback copy so charts can be reconstructed on thread resume
    # even if blob file storage is unavailable.
    plotly_element.props = {"figure_json": fig.to_plotly_json()}
    await cl.Message(
        content="Interactive chart generated. Hover bars to see full labels.",
        elements=[plotly_element],
    ).send()


def _tool_label(tool_name: str) -> str:
    mapping = {
        "query_survey_data": "Survey Data",
        "retrieve_knowledge_base": "RAG Knowledge Base",
        "web_search": "Web Search",
        "stock_market_data": "Stock Market Data",
        "create_survey_graph": "Chart Generator",
    }
    return mapping.get(tool_name, tool_name)


def build_loading_text(stage: str) -> str:
    stage_upper = str(stage).strip().upper()
    if stage_upper == "STOCK":
        return "Fetching stock market data..."
    if stage_upper == "SURVEY":
        return "Fetching your survey insights..."
    if stage_upper == "RAG":
        return "Checking your knowledge base..."
    if stage_upper == "WEB":
        return "Searching relevant market and industry sources..."
    return "Thinking through your request..."


def get_loading_steps(stage: str) -> list[str]:
    stage_upper = str(stage).strip().upper()
    if stage_upper == "STOCK":
        return [
            "Resolving ticker and time range",
            "Fetching market data",
            "Building stock trend view",
        ]
    if stage_upper == "SURVEY":
        return [
            "Fetching survey data",
            "Creating insights",
            "Insights generated",
        ]
    if stage_upper == "RAG":
        return [
            "Checking knowledge base",
            "Retrieving relevant documents",
            "Composing grounded response",
        ]
    if stage_upper == "WEB":
        return [
            "Doing web search",
            "Analyzing market sources",
            "Synthesizing benchmark insights",
        ]
    return [
        "Understanding your request",
        "Reasoning through the answer",
        "Finalizing response",
    ]


async def run_loading_steps(status_message: cl.Message, stage: str) -> None:
    spinner = ["◐", "◓", "◑", "◒"]
    steps = get_loading_steps(stage)
    step_index = 0
    spin_index = 0
    while True:
        label = steps[min(step_index, len(steps) - 1)]
        status_message.content = f"{spinner[spin_index % len(spinner)]} {label}..."
        await status_message.update()
        await asyncio.sleep(0.7)
        spin_index += 1
        if spin_index % 3 == 0 and step_index < len(steps) - 1:
            step_index += 1


async def dismiss_status_message(status_message: cl.Message) -> None:
    remove_fn = getattr(status_message, "remove", None)
    if callable(remove_fn):
        maybe_awaitable = remove_fn()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
        return
    status_message.content = ""
    await status_message.update()


def build_citation_text(trace: dict[str, Any]) -> str:
    tools = trace.get("tools_used") if isinstance(trace, dict) else []
    if not isinstance(tools, list):
        tools = []
    hidden_tools = {"create_survey_graph"}
    labels = [
        _tool_label(str(t).strip())
        for t in tools
        if str(t).strip() and str(t).strip() not in hidden_tools
    ]
    unique_labels: list[str] = []
    for label in labels:
        if label not in unique_labels:
            unique_labels.append(label)
    if not unique_labels:
        return "Source: Direct model reasoning"
    return "Sources: " + " · ".join(unique_labels)


async def stream_llm_response(model: str, messages: List[Dict[str, Any]]) -> str:
    client = get_client()
    settings = get_chat_settings()

    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=float(settings.get("temperature", 0.2)),
        stream=True,
    )

    streamed_text = ""
    reply = cl.Message(content="")
    await reply.send()

    try:
        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            token = (delta.content or "") if delta else ""
            if token:
                streamed_text += token
                await reply.stream_token(token)
    finally:
        close_fn = getattr(stream, "aclose", None) or getattr(stream, "close", None)
        if close_fn:
            maybe_awaitable = close_fn()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

    await reply.update()
    return streamed_text


@cl.on_chat_start
async def start():
    settings_state = get_chat_settings()
    cl.user_session.set("conversation", [])
    cl.user_session.set("graph_rows", [])
    cl.user_session.set("graph_metric", "")
    cl.user_session.set("session_started_at", datetime.now(timezone.utc).isoformat())
    model_options = get_model_options()
    current_model = str(settings_state.get("model") or model_options[0])
    initial_index = model_options.index(current_model) if current_model in model_options else 0
    settings = await cl.ChatSettings(
        [
            Select(
                id="model",
                label="Model",
                values=model_options,
                initial_index=initial_index,
            ),
            Slider(
                id="temperature",
                label="Temperature",
                initial=float(settings_state.get("temperature", 0.2)),
                min=0.0,
                max=2.0,
                step=0.1,
            ),
            Slider(
                id="history_window",
                label="History Window",
                initial=int(settings_state.get("history_window", 20)),
                min=4,
                max=50,
                step=1,
            ),
            Select(
                id="response_style",
                label="Response Style",
                values=["concise", "balanced", "detailed"],
                initial_index=["concise", "balanced", "detailed"].index(
                    str(settings_state.get("response_style", "balanced"))
                )
                if str(settings_state.get("response_style", "balanced")) in ["concise", "balanced", "detailed"]
                else 1,
            ),
            Select(
                id="language",
                label="Language",
                values=["English", "Hindi"],
                initial_index=0 if str(settings_state.get("language", "English")) == "English" else 1,
            ),
        ]
    ).send()
    merged_settings = get_default_settings()
    merged_settings.update(settings_state)
    merged_settings.update(settings)
    cl.user_session.set("chat_settings", merged_settings)
    await push_profile_to_ui()
    await cl.Message(
        content="Hello! I'm your Culture Coach Assistant."
    ).send()


@cl.on_chat_resume
async def on_chat_resume(thread: dict[str, Any]) -> None:
    cl.user_session.set("conversation", extract_conversation_from_thread(thread))
    cl.user_session.set("graph_rows", [])
    cl.user_session.set("graph_metric", "")
    await push_profile_to_ui()


@cl.on_settings_update
async def on_settings_update(settings: dict[str, Any]) -> None:
    current = get_chat_settings()
    current.update(settings)
    cl.user_session.set("chat_settings", current)
    await cl.Message(content=f"Settings updated: {current}").send()


@cl.on_message
async def main(message: cl.Message):
    user = cl.user_session.get("user")
    if not user:
        await cl.Message(content="Unauthorized. Please sign in via Django-authenticated token.").send()
        return

    settings = get_chat_settings()
    history: List[Dict[str, Any]] = cl.user_session.get("conversation") or []
    user_input = message.content.strip()
    user_id = str(getattr(user, "identifier", "")).strip()
    if not user_id:
        await cl.Message(content="Unable to resolve your session. Please sign in again.").send()
        return

    if user_input.startswith("/tool "):
        tool_name = user_input.replace("/tool ", "", 1).strip()
        result = await execute_tool(tool_name, {})
        await cl.Message(content=f"Tool `{tool_name}` result:\n{result}").send()
        return

    uploaded_context = await read_uploaded_files(message)
    history_for_agent = history[-get_agent_history_window():]
    status_message: cl.Message | None = None
    loading_task: asyncio.Task | None = None

    try:
        effective_question = user_input
        if uploaded_context:
            effective_question = (
                f"{user_input}\n\n"
                "Additional user-uploaded context for this turn:\n"
                f"{uploaded_context}"
            )
        stage = await asyncio.to_thread(
            predict_loading_stage,
            effective_question,
            history_for_agent,
        )
        status_message = cl.Message(content=build_loading_text(stage))
        await status_message.send()
        loading_task = asyncio.create_task(run_loading_steps(status_message, stage))

        agent_result = await asyncio.to_thread(
            run_agent,
            user_id=user_id,
            question=effective_question,
            user_metadata=(getattr(user, "metadata", {}) or {}),
            history_messages=history_for_agent,
            previous_graph_rows=cl.user_session.get("graph_rows") or [],
            previous_graph_metric=str(cl.user_session.get("graph_metric") or ""),
        )
        answer = str((agent_result or {}).get("answer") or "")
        graph = (agent_result or {}).get("graph") or {}
        trace = (agent_result or {}).get("trace") or {}
        graph_rows = (agent_result or {}).get("graph_rows") or []
        graph_metric = str((agent_result or {}).get("graph_metric") or "")
        if loading_task:
            loading_task.cancel()
            try:
                await loading_task
            except asyncio.CancelledError:
                pass
        if status_message:
            await dismiss_status_message(status_message)

        await stream_text_response(answer, citation=build_citation_text(trace))
        await send_graph_response(graph)
        if isinstance(graph_rows, list):
            cl.user_session.set("graph_rows", graph_rows)
            cl.user_session.set("graph_metric", graph_metric)

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": answer})
        history_window = int(settings.get("history_window", 20))
        cl.user_session.set("conversation", history[-history_window:])
    except Exception:
        if loading_task:
            loading_task.cancel()
            try:
                await loading_task
            except asyncio.CancelledError:
                pass
        if status_message:
            await dismiss_status_message(status_message)
        await cl.Message(
            content="I could not process your request right now. Please try again."
        ).send()


@cl.on_stop
async def on_stop() -> None:
    cl.user_session.set("last_stop_at", datetime.now(timezone.utc).isoformat())


@cl.on_chat_end
async def on_chat_end() -> None:
    cl.user_session.set("conversation", [])
    cl.user_session.set("graph_rows", [])
    cl.user_session.set("graph_metric", "")
