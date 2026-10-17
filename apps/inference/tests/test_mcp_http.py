"""MCP Streamable HTTP transport: auth, origin, header validation, limits, audit, real socket."""

from __future__ import annotations

import base64
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from app import mcp_http
from app.config import Settings
from tests.test_analysis import job, write_log
from tests.test_mcp import meta

TOKEN = "t" * 40
V = mcp_http.LATEST_VERSION


@pytest.fixture
def app(settings: Settings) -> mcp_http.McpHttpApp:
    write_log(settings.output_dir, [job("a"), job("b")])
    return mcp_http.McpHttpApp(settings, TOKEN)


def modern_headers(method: str, name: str | None = None, **extra: str) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {TOKEN}",
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": V,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    headers.update(extra)
    return headers


def body(method: str, params: dict | None = None, i: int | None = 1) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if i is not None:
        message["id"] = i
    if params is not None:
        message["params"] = params
    return json.dumps(message).encode()


def post(app, headers, payload, path="/mcp"):
    response = app.process("POST", path, headers, payload)
    return response.status, response.body, response.headers


# ------------------------------------------------------------------- auth


def test_missing_key_is_401_with_bearer_challenge(app):
    headers = modern_headers("tools/list")
    del headers["authorization"]
    status, payload, extra = post(app, headers, body("tools/list", {"_meta": meta()}))
    assert status == 401
    assert extra["WWW-Authenticate"].startswith("Bearer")
    assert TOKEN not in json.dumps(payload)


@pytest.mark.parametrize(
    "header",
    [
        {"authorization": f"Bearer {TOKEN}"},
        {"authorization": f"bearer {TOKEN}"},
        {"authorization": TOKEN},
        {"x-api-key": TOKEN},
    ],
)
def test_accepted_key_forms(app, header):
    headers = modern_headers("tools/list")
    del headers["authorization"]
    headers.update(header)
    status, payload, _ = post(app, headers, body("tools/list", {"_meta": meta()}))
    assert status == 200
    assert len(payload["result"]["tools"]) == 7


def test_wrong_key_is_401(app):
    headers = modern_headers("tools/list", authorization="Bearer " + "x" * 40)
    assert post(app, headers, body("tools/list", {"_meta": meta()}))[0] == 401


def test_failed_auth_is_rate_limited(settings):
    now = [1000.0]
    app = mcp_http.McpHttpApp(settings, TOKEN, clock=lambda: now[0])
    bad = modern_headers("ping", authorization="Bearer nope")
    statuses = [post(app, bad, body("ping", {"_meta": meta()}))[0] for _ in range(12)]
    assert statuses[:10] == [401] * 10
    assert statuses[10:] == [429, 429]
    # even the right key is locked out until the window passes
    good = modern_headers("ping")
    status, _, extra = post(app, good, body("ping", {"_meta": meta()}))
    assert status == 429 and int(extra["Retry-After"]) >= 1
    now[0] += 61
    assert post(app, good, body("ping", {"_meta": meta()}))[0] == 200


def test_request_rate_limit(settings):
    now = [0.0]
    app = mcp_http.McpHttpApp(settings, TOKEN, clock=lambda: now[0])
    headers = modern_headers("ping")
    statuses = [post(app, headers, body("ping", {"_meta": meta()}))[0] for _ in range(121)]
    assert statuses[-1] == 429 and statuses[:120] == [200] * 120


# ------------------------------------------------------------------- origin / verbs / limits


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        ("https://evil.example", 403),
        ("http://www.perplexity.ai", 403),  # https only for remote origins
        ("https://perplexity.ai.evil.example", 403),
        ("null", 403),
        ("https://www.perplexity.ai", 200),
        ("http://localhost:5173", 200),
        ("http://127.0.0.1", 200),
    ],
)
def test_origin_validation(app, origin, status):
    headers = modern_headers("ping", origin=origin)
    assert post(app, headers, body("ping", {"_meta": meta()}))[0] == status


def test_extra_allowed_origin(settings, monkeypatch):
    monkeypatch.setenv(mcp_http.ORIGINS_ENV, "https://my.example/, https://two.example")
    app = mcp_http.McpHttpApp(settings, TOKEN, allowed_origins=mcp_http.allowed_origins_from_env())
    headers = modern_headers("ping", origin="https://my.example")
    assert post(app, headers, body("ping", {"_meta": meta()}))[0] == 200


@pytest.mark.parametrize("verb", ["GET", "DELETE", "PUT"])
def test_non_post_is_405(app, verb):
    response = app.process(verb, "/mcp", modern_headers("ping"), b"")
    assert response.status == 405 and response.headers["Allow"] == "POST"


def test_unknown_path_is_404(app):
    assert post(app, modern_headers("ping"), body("ping"), path="/other")[0] == 404


def test_trailing_slash_and_query_are_accepted(app):
    payload = body("ping", {"_meta": meta()})
    assert post(app, modern_headers("ping"), payload, path="/mcp/?x=1")[0] == 200


def test_body_too_large_and_wrong_content_type(app):
    assert post(app, modern_headers("ping"), None)[0] == 413
    headers = modern_headers("ping", **{"content-type": "text/plain"})
    assert post(app, headers, body("ping", {"_meta": meta()}))[0] == 415


def test_parse_error_and_batch(app):
    status, payload, _ = post(app, modern_headers("ping"), b"{not json")
    assert status == 400 and payload["error"]["code"] == mcp_http.PARSE_ERROR
    status, payload, _ = post(app, modern_headers("ping"), b"[]")
    assert status == 400 and "Batch" in payload["error"]["message"]


# ------------------------------------------------------------------- protocol headers


def test_notification_is_202_without_body(app):
    headers = modern_headers("notifications/cancelled")
    status, payload, _ = post(
        app, headers, body("notifications/cancelled", {"_meta": meta()}, i=None)
    )
    assert status == 202 and payload is None


def test_missing_version_header_is_header_mismatch(app):
    headers = modern_headers("ping")
    del headers["mcp-protocol-version"]
    status, payload, _ = post(app, headers, body("ping", {"_meta": meta()}))
    assert status == 400 and payload["error"]["code"] == mcp_http.HEADER_MISMATCH
    assert payload["id"] == 1


def test_version_header_mismatch(app):
    headers = modern_headers("ping", **{"mcp-protocol-version": "2025-11-25"})
    status, payload, _ = post(app, headers, body("ping", {"_meta": meta()}))
    assert status == 400 and payload["error"]["code"] == -32020


def test_method_header_missing_or_mismatched(app):
    headers = modern_headers("ping")
    del headers["mcp-method"]
    assert post(app, headers, body("ping", {"_meta": meta()}))[1]["error"]["code"] == -32020
    headers = modern_headers("tools/list")
    assert post(app, headers, body("ping", {"_meta": meta()}))[1]["error"]["code"] == -32020


def test_name_header_required_for_tools_call(app):
    params = {"name": "localsub_list_jobs", "arguments": {}, "_meta": meta()}
    status, payload, _ = post(app, modern_headers("tools/call"), body("tools/call", params))
    assert status == 400 and payload["error"]["code"] == -32020
    wrong = modern_headers("tools/call", "localsub_diagnostics")
    assert post(app, wrong, body("tools/call", params))[0] == 400
    ok = modern_headers("tools/call", "localsub_list_jobs")
    status, payload, _ = post(app, ok, body("tools/call", params))
    assert status == 200
    assert payload["result"]["structuredContent"]["jobs"]


def test_base64_name_header_is_decoded(app):
    params = {"uri": "localsub://jobs/recent", "_meta": meta()}
    encoded = "=?base64?" + base64.b64encode(b"localsub://jobs/recent").decode() + "?="
    status, payload, _ = post(
        app, modern_headers("resources/read", encoded), body("resources/read", params)
    )
    assert status == 200, payload
    bad = modern_headers("resources/read", "=?base64?@@@?=")
    assert post(app, bad, body("resources/read", params))[0] == 400


def test_unsupported_body_version_is_400(app):
    headers = modern_headers("ping", **{"mcp-protocol-version": "2099-01-01"})
    status, payload, _ = post(app, headers, body("ping", {"_meta": meta("2099-01-01")}))
    assert status == 400 and payload["error"]["code"] == -32022


def test_unknown_method_is_404_for_modern_clients(app):
    headers = modern_headers("nope/never")
    status, payload, _ = post(app, headers, body("nope/never", {"_meta": meta()}))
    assert status == 404 and payload["error"]["code"] == -32601


def test_legacy_client_initialize_and_tools(app):
    headers = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
    init = {"protocolVersion": "2025-06-18", "capabilities": {}}
    status, payload, extra = post(app, headers, body("initialize", init))
    assert status == 200 and payload["result"]["protocolVersion"] == "2025-06-18"
    assert "Mcp-Session-Id" not in extra  # stateless: never mint session ids
    headers["mcp-protocol-version"] = "2025-06-18"
    headers["mcp-session-id"] = "ignored"
    status, _, _ = post(app, headers, body("notifications/initialized", i=None))
    assert status == 202
    status, payload, _ = post(app, headers, body("tools/list", i=2))
    assert status == 200 and len(payload["result"]["tools"]) == 7


def test_legacy_unsupported_version_header(app):
    headers = {
        "authorization": f"Bearer {TOKEN}",
        "content-type": "application/json",
        "mcp-protocol-version": "2024-11-05",
    }
    status, payload, _ = post(app, headers, body("tools/list"))
    assert status == 400 and payload["error"]["data"]["supported"]


def test_latest_header_without_body_meta_is_mismatch(app):
    headers = {
        "authorization": f"Bearer {TOKEN}",
        "content-type": "application/json",
        "mcp-protocol-version": V,
        "mcp-method": "tools/list",
    }
    assert post(app, headers, body("tools/list"))[1]["error"]["code"] == -32020


# ------------------------------------------------------------------- audit / privacy


def test_audit_log_records_requests_without_secrets(app, settings):
    events: list[dict] = []
    app.on_request = events.append
    params = {"name": "localsub_get_job", "arguments": {"job_id": "a"}, "_meta": meta()}
    headers = modern_headers("tools/call", "localsub_get_job", **{"cf-connecting-ip": "1.2.3.4"})
    post(app, headers, body("tools/call", params))
    post(app, {"content-type": "application/json"}, body("ping"))
    lines = (settings.output_dir / "logs" / "mcp_http.jsonl").read_text().splitlines()
    first, second = (json.loads(line) for line in lines)
    assert first["method"] == "tools/call" and first["name"] == "localsub_get_job"
    assert first["status"] == 200 and first["auth"] == "ok" and first["client_ip"] == "1.2.3.4"
    assert second["status"] == 401 and second["auth"] == "failed"
    text = "\n".join(lines)
    assert TOKEN not in text and "job_id" not in text
    assert first["ts"].endswith("Z") and len(events) == 2


def test_paths_redacted_over_http(settings):
    write_log(settings.output_dir, [job("secret", source_path=r"C:\Users\phu\Phim\STAR-1.mp4")])
    app = mcp_http.McpHttpApp(settings, TOKEN)
    params = {"name": "localsub_get_job", "arguments": {"job_id": "secret"}, "_meta": meta()}
    _, payload, _ = post(
        app, modern_headers("tools/call", "localsub_get_job"), body("tools/call", params)
    )
    assert "STAR-1" not in json.dumps(payload)


# ------------------------------------------------------------------- token


def test_token_is_created_persisted_and_rotated(settings, monkeypatch):
    monkeypatch.delenv(mcp_http.TOKEN_ENV, raising=False)
    first, source = mcp_http.load_token(settings)
    assert source == "new" and len(first) >= 40
    again, source = mcp_http.load_token(settings)
    assert again == first and source == "file"
    rotated, source = mcp_http.load_token(settings, rotate=True)
    assert rotated != first and source == "new"
    path = mcp_http.token_path(settings)
    assert path.read_text() == rotated
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_token_from_env(settings, monkeypatch):
    monkeypatch.setenv(mcp_http.TOKEN_ENV, "e" * 30)
    assert mcp_http.load_token(settings) == ("e" * 30, "env")
    monkeypatch.setenv(mcp_http.TOKEN_ENV, "short")
    with pytest.raises(mcp_http.TokenError):
        mcp_http.load_token(settings)


def test_short_token_rejected_by_app(settings):
    with pytest.raises(mcp_http.TokenError):
        mcp_http.McpHttpApp(settings, "short")


def test_non_loopback_bind_refused(app):
    with pytest.raises(ValueError):
        mcp_http.make_server(app, 0, host="0.0.0.0")


# ------------------------------------------------------------------- real socket


def _request(url: str, payload: bytes | None, headers: dict[str, str], method: str = "POST"):
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def test_real_server_round_trip(app):
    server = mcp_http.make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
        headers = {key.title(): value for key, value in modern_headers("server/discover").items()}
        status, raw, resp_headers = _request(
            url, body("server/discover", {"_meta": meta()}), headers
        )
        assert status == 200 and resp_headers["Content-Type"] == "application/json"
        assert json.loads(raw)["result"]["supportedVersions"][0] == V
        assert _request(url, None, headers, method="GET")[0] == 405
        assert _request(url, b"{}", {"Content-Type": "application/json"})[0] == 401
        status, raw, _ = _request(url, body("notifications/x", {"_meta": meta()}, i=None), headers)
        assert status == 202 and raw == b""
        big = b"{" + b" " * (mcp_http.MAX_BODY + 10) + b"}"
        try:
            assert _request(url, big, headers)[0] == 413
        except (ConnectionError, urllib.error.URLError):
            pass  # the server may close before the client finishes sending
    finally:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_cli_http_events_and_parent_watch(tmp_path: Path):
    """`python -m app.main mcp --http --events` prints the listening event and serves requests."""
    port = _free_port()
    env = {
        **os.environ,
        "LOCALSUB_OUTPUT_DIR": str(tmp_path / "out"),
        "LOCALSUB_MODEL_DIR": str(tmp_path / "models"),
        mcp_http.TOKEN_ENV: TOKEN,
    }
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main", "mcp", "--http", "--port", str(port), "--events"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        event = json.loads(line)
        assert event["type"] == "mcp_http" and event["state"] == "listening"
        assert event["url"] == f"http://127.0.0.1:{port}/mcp" and event["token_source"] == "env"
        headers = {key.title(): value for key, value in modern_headers("ping").items()}
        status, raw, _ = _request(event["url"], body("ping", {"_meta": meta()}), headers)
        assert status == 200 and json.loads(raw)["result"]["resultType"] == "complete"
        request_event = json.loads(proc.stdout.readline())
        assert request_event["type"] == "mcp_http_request" and request_event["method"] == "ping"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()


def test_cli_rejects_privileged_port(tmp_path: Path):
    env = {**os.environ, "LOCALSUB_OUTPUT_DIR": str(tmp_path / "out")}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "app.main", "mcp", "--http", "--port", "80"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2 and "1024-65535" in result.stderr


def test_parent_watch_stops_server(settings, monkeypatch):
    monkeypatch.setenv(mcp_http.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(mcp_http, "_pid_alive", lambda pid: False)
    out = __import__("io").StringIO()
    started = time.time()
    code = mcp_http.serve_http(settings, port=0, events=True, parent_pid=12345, out=out)
    assert code == 0 and time.time() - started < 10
    states = [json.loads(line)["state"] for line in out.getvalue().splitlines()]
    assert states == ["listening", "stopped"]


def test_port_in_use_reports_error(settings, monkeypatch):
    monkeypatch.setenv(mcp_http.TOKEN_ENV, TOKEN)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        out = __import__("io").StringIO()
        code = mcp_http.serve_http(settings, port=sock.getsockname()[1], events=True, out=out)
    event = json.loads(out.getvalue().splitlines()[0])
    assert code == 3 and event["code"] == "PORT_IN_USE"
