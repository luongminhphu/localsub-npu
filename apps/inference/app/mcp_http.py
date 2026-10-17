"""Streamable HTTP transport for the read-only MCP server (for Perplexity custom connectors).

Perplexity (web, Windows app, Comet, Computer) only talks to *remote* MCP servers over HTTPS.
This module serves the same :class:`app.mcp_server.LocalSubMcp` dispatcher on
``http://127.0.0.1:<port>/mcp``; the desktop app then opens an HTTPS tunnel (cloudflared) to it.

Security model (the endpoint may be reachable from the internet through the tunnel):

* binds to loopback only; every request needs the API key (``Authorization: Bearer <key>``,
  a raw ``Authorization: <key>`` or ``X-API-Key: <key>``), compared in constant time;
* the key is random (``secrets.token_urlsafe(32)``), stored in ``<data>/mcp/http-token`` and
  can be rotated; ``LOCALSUB_MCP_TOKEN`` (>= 24 chars) overrides it;
* ``Origin`` is validated (DNS rebinding): absent, localhost or ``*.perplexity.ai`` only;
* 1 MB body limit, no batches, request and failed-auth rate limits (HTTP 429);
* tools stay read-only and paths are redacted unless ``--no-redact``;
* every request is written to ``<output>/logs/mcp_http.jsonl`` (no key, no arguments).

Implements MCP 2026-07-28 Streamable HTTP (POST only, JSON responses, 202 for notifications,
``MCP-Protocol-Version`` / ``Mcp-Method`` / ``Mcp-Name`` validation with ``-32020``) and keeps
legacy clients (``initialize`` without a session id) working. Standard library only.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

from app.config import Settings
from app.mcp_server import (
    INVALID_REQUEST,
    LATEST_VERSION,
    META_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
    LocalSubMcp,
    _error,
    _protocol_stdout,
)

ENDPOINT = "/mcp"
DEFAULT_PORT = 8765
HOST = "127.0.0.1"
MAX_BODY = 1_000_000
RATE_LIMIT = 120  # requests per window
AUTH_FAIL_LIMIT = 10  # failed authentications per window
RATE_WINDOW_S = 60.0
MIN_TOKEN_LEN = 24
AUDIT_MAX_BYTES = 5_000_000
TOKEN_ENV = "LOCALSUB_MCP_TOKEN"
ORIGINS_ENV = "LOCALSUB_MCP_ALLOWED_ORIGINS"
HEADER_MISMATCH = -32020
NAMED_METHODS = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
LEGACY_DEFAULT_VERSION = "2025-03-26"  # clients older than 2025-06-18 send no version header


class TokenError(ValueError):
    pass


# --------------------------------------------------------------------------- token


def token_path(settings: Settings) -> Path:
    return settings.output_dir.parent / "mcp" / "http-token"


def load_token(settings: Settings, rotate: bool = False) -> tuple[str, str]:
    """Return ``(token, source)``; source is ``env``, ``file`` or ``new``."""
    env = os.getenv(TOKEN_ENV, "").strip()
    if env:
        if len(env) < MIN_TOKEN_LEN:
            raise TokenError(f"{TOKEN_ENV} must be at least {MIN_TOKEN_LEN} characters")
        return env, "env"
    path = token_path(settings)
    if not rotate and path.is_file():
        stored = path.read_text(encoding="utf-8").strip()
        if len(stored) >= MIN_TOKEN_LEN:
            return stored, "file"
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    os.replace(tmp, path)
    return token, "new"


def extract_token(headers: Mapping[str, str]) -> str | None:
    auth = headers.get("authorization", "").strip()
    if auth:
        scheme, _, rest = auth.partition(" ")
        return rest.strip() if scheme.lower() == "bearer" and rest.strip() else auth
    key = headers.get("x-api-key", "").strip()
    return key or None


def token_matches(given: str | None, expected: str) -> bool:
    if not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


# --------------------------------------------------------------------------- origin / headers


def allowed_origins_from_env() -> set[str]:
    raw = os.getenv(ORIGINS_ENV, "")
    return {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}


def origin_allowed(origin: str | None, extra: set[str] | None = None) -> bool:
    """Absent Origin (server-to-server) is fine; browsers must come from localhost or Perplexity."""
    if origin is None:
        return True
    origin = origin.strip().rstrip("/")
    if extra and origin in extra:
        return True
    try:
        parts = urlsplit(origin)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    return parts.scheme == "https" and (host == "perplexity.ai" or host.endswith(".perplexity.ai"))


def decode_header_value(value: str) -> str:
    """MCP lets clients Base64-encode non-ASCII header values as ``=?base64?<data>?=``."""
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError("invalid Base64 header value") from exc
    return value


def validate_headers(message: dict[str, Any], headers: Mapping[str, str]) -> tuple[int, str] | None:
    """Return ``(code, message)`` when the HTTP envelope disagrees with the JSON-RPC body."""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    body_version = meta.get(META_VERSION)
    header_version = headers.get("mcp-protocol-version")
    method = message.get("method")
    is_request = "id" in message
    if body_version is None:  # legacy era (initialize handshake, per-connection version)
        if header_version is None:
            return None
        if header_version not in SUPPORTED_VERSIONS:
            return UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version"
        if header_version == LATEST_VERSION and is_request and method != "initialize":
            return HEADER_MISMATCH, (
                f"Header mismatch: MCP-Protocol-Version {header_version} requires "
                f"_meta.{META_VERSION} in the request body"
            )
        return None
    if header_version is None:
        return HEADER_MISMATCH, "Header mismatch: MCP-Protocol-Version header is missing"
    if header_version != body_version:
        return HEADER_MISMATCH, (
            f"Header mismatch: MCP-Protocol-Version header value '{header_version}' does not "
            f"match body value '{body_version}'"
        )
    if not is_request:
        return None
    header_method = headers.get("mcp-method")
    if header_method is None:
        return HEADER_MISMATCH, "Header mismatch: Mcp-Method header is missing"
    if header_method != method:
        return HEADER_MISMATCH, (
            f"Header mismatch: Mcp-Method header value '{header_method}' does not match "
            f"body value '{method}'"
        )
    field = NAMED_METHODS.get(str(method))
    if field:
        raw_name = headers.get("mcp-name")
        if raw_name is None:
            return HEADER_MISMATCH, "Header mismatch: Mcp-Name header is missing"
        try:
            name = decode_header_value(raw_name)
        except ValueError:
            return HEADER_MISMATCH, "Header mismatch: Mcp-Name header is malformed"
        if name != params.get(field):
            return HEADER_MISMATCH, (
                f"Header mismatch: Mcp-Name header value '{name}' does not match body value "
                f"'{params.get(field)}'"
            )
    return None


# --------------------------------------------------------------------------- rate limit / audit


class RateLimiter:
    def __init__(
        self, limit: int, window_s: float = RATE_WINDOW_S, clock: Callable[[], float] = time.time
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self._hits and now - self._hits[0] >= self.window_s:
            self._hits.popleft()

    def allow(self) -> bool:
        """Record a hit and return False once the window is full."""
        with self._lock:
            now = self.clock()
            self._trim(now)
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True

    def exceeded(self) -> bool:
        with self._lock:
            self._trim(self.clock())
            return len(self._hits) >= self.limit

    def retry_after(self) -> int:
        with self._lock:
            if not self._hits:
                return 1
            return max(1, int(self.window_s - (self.clock() - self._hits[0])) + 1)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.is_file() and self.path.stat().st_size > AUDIT_MAX_BYTES:
                    os.replace(self.path, self.path.with_suffix(".jsonl.1"))
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError as exc:  # auditing must never take the endpoint down
                print(f"localsub mcp-http: audit log write failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- core


class HttpResponse:
    def __init__(self, status: int, body: dict[str, Any] | None = None, **headers: str) -> None:
        self.status = status
        self.body = body
        self.headers = {key.replace("_", "-"): value for key, value in headers.items()}

    def encoded(self) -> bytes:
        if self.body is None:
            return b""
        return json.dumps(self.body, ensure_ascii=False).encode("utf-8")


def _status_for(response: dict[str, Any], modern: bool) -> int:
    error = response.get("error")
    if not isinstance(error, dict):
        return 200
    code = error.get("code")
    if code in (UNSUPPORTED_PROTOCOL_VERSION, HEADER_MISMATCH, PARSE_ERROR, INVALID_REQUEST):
        return 400
    if code == METHOD_NOT_FOUND and modern:
        return 404
    return 200


class McpHttpApp:
    """Socket-free request processing so every rule is unit-testable."""

    def __init__(
        self,
        settings: Settings,
        token: str,
        redact: bool = True,
        allowed_origins: set[str] | None = None,
        on_request: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(token) < MIN_TOKEN_LEN:
            raise TokenError("token too short")
        self.mcp = LocalSubMcp(settings, redact=redact)
        self.token = token
        self.allowed_origins = allowed_origins or set()
        self.audit = AuditLog(settings.output_dir / "logs" / "mcp_http.jsonl")
        self.on_request = on_request
        self.requests = RateLimiter(RATE_LIMIT, clock=clock)
        self.auth_failures = RateLimiter(AUTH_FAIL_LIMIT, clock=clock)
        self._lock = threading.Lock()

    def process(
        self, verb: str, path: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResponse:
        """``headers`` must use lower-case names."""
        started = time.perf_counter()
        info: dict[str, Any] = {"http": verb, "rpc": None, "name": None, "auth": "skipped"}
        response = self._process(verb, path, headers, body, info)
        entry = {
            "ts": utc_now(),
            "http": verb,
            "path": path.split("?", 1)[0][:80],
            "method": info["rpc"],
            "name": info["name"],
            "status": response.status,
            "auth": info["auth"],
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if headers.get("origin"):
            entry["origin"] = headers["origin"][:120]
        if headers.get("cf-connecting-ip"):
            entry["client_ip"] = headers["cf-connecting-ip"][:64]
        if headers.get("user-agent"):
            entry["user_agent"] = headers["user-agent"][:120]
        self.audit.write(entry)
        if self.on_request:
            self.on_request(entry)
        return response

    def _process(
        self,
        verb: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes | None,
        info: dict[str, Any],
    ) -> HttpResponse:
        if path.split("?", 1)[0].rstrip("/") != ENDPOINT:
            return HttpResponse(404, {"error": "not found", "endpoint": ENDPOINT})
        if not origin_allowed(headers.get("origin"), self.allowed_origins):
            return HttpResponse(403, _error(None, INVALID_REQUEST, "Origin not allowed"))
        if self.auth_failures.exceeded():
            return HttpResponse(
                429,
                {"error": "too many failed authentications"},
                Retry_After=str(self.auth_failures.retry_after()),
            )
        if not self.requests.allow():
            return HttpResponse(
                429, {"error": "rate limit exceeded"}, Retry_After=str(self.requests.retry_after())
            )
        if not token_matches(extract_token(headers), self.token):
            info["auth"] = "failed"
            self.auth_failures.allow()
            return HttpResponse(
                401,
                {"error": "missing or invalid API key"},
                WWW_Authenticate='Bearer realm="localsub-npu"',
            )
        info["auth"] = "ok"
        if verb != "POST":
            return HttpResponse(405, {"error": "method not allowed"}, Allow="POST")
        if body is None:
            return HttpResponse(413, _error(None, INVALID_REQUEST, "Request body too large"))
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return HttpResponse(
                415, _error(None, INVALID_REQUEST, "Content-Type must be application/json")
            )
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HttpResponse(400, _error(None, PARSE_ERROR, "Parse error"))
        if isinstance(message, list):
            return HttpResponse(
                400, _error(None, INVALID_REQUEST, "Batch requests are not supported")
            )
        if not isinstance(message, dict):
            return HttpResponse(400, _error(None, INVALID_REQUEST, "Invalid JSON-RPC 2.0 message"))
        method = message.get("method")
        info["rpc"] = method if isinstance(method, str) else None
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        field = NAMED_METHODS.get(str(method))
        if field and isinstance(params.get(field), str):
            info["name"] = params[field][:120]
        mismatch = validate_headers(message, headers)
        if mismatch:
            code, text = mismatch
            unsupported = code == UNSUPPORTED_PROTOCOL_VERSION
            data = {"supported": list(SUPPORTED_VERSIONS)} if unsupported else None
            return HttpResponse(400, _error(message.get("id"), code, text, data))
        with self._lock:
            result = self.mcp.handle(message)
        if result is None:
            return HttpResponse(202)
        modern = isinstance(params.get("_meta"), dict) and META_VERSION in params["_meta"]
        return HttpResponse(_status_for(result, modern), result)


# --------------------------------------------------------------------------- server


def _make_handler(app: McpHttpApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalSubMCP"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 30

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - audited instead
            return

        def _serve(self) -> None:
            headers = {key.lower(): value for key, value in self.headers.items()}
            body: bytes | None = b""
            try:
                length = int(headers.get("content-length", "0") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                body = None
                self.close_connection = True
            elif length > 0:
                body = self.rfile.read(length)
            response = app.process(self.command, self.path, headers, body)
            payload = response.encoded()
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            if payload:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        do_POST = do_GET = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = _serve

    return Handler


def make_server(app: McpHttpApp, port: int, host: str = HOST) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the MCP HTTP server only binds to loopback; use a tunnel for HTTPS")
    if not 0 <= port <= 65535:
        raise ValueError("port must be 0-65535")
    server = ThreadingHTTPServer((host, port), _make_handler(app))
    server.daemon_threads = True
    return server


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def serve_http(
    settings: Settings,
    port: int = DEFAULT_PORT,
    redact: bool = True,
    events: bool = False,
    rotate: bool = False,
    parent_pid: int = 0,
    out: IO[str] | None = None,
) -> int:
    stream = out or (_protocol_stdout() if events else None)
    emit_lock = threading.Lock()

    def emit(payload: dict[str, Any]) -> None:
        if stream is None:
            return
        with emit_lock:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()

    try:
        token, source = load_token(settings, rotate=rotate)
    except (TokenError, OSError) as exc:
        emit({"type": "mcp_http", "state": "error", "code": "TOKEN", "message": str(exc)})
        print(f"localsub mcp-http: {exc}", file=sys.stderr)
        return 2

    def on_request(entry: dict[str, Any]) -> None:
        emit({"type": "mcp_http_request", **entry})

    app = McpHttpApp(
        settings,
        token,
        redact=redact,
        allowed_origins=allowed_origins_from_env(),
        on_request=on_request if events else None,
    )
    try:
        server = make_server(app, port)
    except OSError as exc:
        message = f"cannot listen on 127.0.0.1:{port}: {exc}"
        emit({"type": "mcp_http", "state": "error", "code": "PORT_IN_USE", "message": message})
        print(f"localsub mcp-http: {message}", file=sys.stderr)
        return 3
    bound = server.server_address[1]
    url = f"http://{HOST}:{bound}{ENDPOINT}"
    emit(
        {
            "type": "mcp_http",
            "state": "listening",
            "url": url,
            "port": bound,
            "token": token,
            "token_source": source,
            "token_path": str(token_path(settings)),
            "audit_log": str(app.audit.path),
            "redact": redact,
            "at": utc_now(),
        }
    )
    print(
        f"localsub mcp-http: listening on {url} (redact={redact}, key from {source}: "
        f"{token_path(settings) if source != 'env' else TOKEN_ENV})",
        file=sys.stderr,
        flush=True,
    )
    if parent_pid:

        def watch_parent() -> None:
            while _pid_alive(parent_pid):
                time.sleep(2)
            print("localsub mcp-http: desktop app exited, stopping", file=sys.stderr)
            server.shutdown()

        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        emit({"type": "mcp_http", "state": "stopped", "at": utc_now()})
    return 0
