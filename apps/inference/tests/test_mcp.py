"""MCP server: protocol (2026-07-28 + legacy initialize), tools, privacy and stdout hygiene."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app import mcp_server
from app.config import Settings
from tests.test_analysis import job, write_log

V = mcp_server.LATEST_VERSION


def meta(version: str = V) -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1"},
    }


def rpc(server: mcp_server.LocalSubMcp, method: str, params: dict | None = None, i: int = 1):
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def call(server, name: str, arguments: dict | None = None) -> dict[str, Any]:
    response = rpc(
        server, "tools/call", {"name": name, "arguments": arguments or {}, "_meta": meta()}
    )
    return response["result"]


@pytest.fixture
def server(settings: Settings) -> mcp_server.LocalSubMcp:
    rows = [
        job(
            "slow",
            source_path=r"\\192.168.1.8\smb\STAR-888.mp4",
            translate_to="vi",
            translation_model="qwen3-8b-int4-cw",
            translation_device={"requested": "auto", "resolved": "NPU", "fallback_reason": None},
            timings={"load_translation_model": 410_000, "load_model": 9_000},
        ),
        job("fast"),
    ]
    write_log(settings.output_dir, rows)
    return mcp_server.LocalSubMcp(settings)


def test_server_discover(server) -> None:
    result = rpc(server, "server/discover", {"_meta": meta()})["result"]
    assert result["resultType"] == "complete"
    assert V in result["supportedVersions"] and "2025-11-25" in result["supportedVersions"]
    assert set(result["capabilities"]) == {"tools", "resources", "prompts"}
    assert result["ttlMs"] > 0 and result["cacheScope"] == "private"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "localsub-npu"


def test_legacy_initialize_negotiates(server) -> None:
    result = rpc(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})[
        "result"
    ]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["version"]
    unknown = rpc(server, "initialize", {"protocolVersion": "2024-01-01"})["result"]
    assert unknown["protocolVersion"] == "2025-11-25"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc(server, "ping")["result"]["resultType"] == "complete"


def test_unsupported_version_error(server) -> None:
    error = rpc(server, "tools/list", {"_meta": meta("1900-01-01")})["error"]
    assert error["code"] == -32022
    assert error["data"]["requested"] == "1900-01-01" and V in error["data"]["supported"]


def test_unknown_method_and_bad_message(server) -> None:
    assert rpc(server, "tools/delete", {"_meta": meta()})["error"]["code"] == -32601
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    assert server.handle([1])["error"]["code"] == -32600


def test_tools_list_is_read_only(server) -> None:
    result = rpc(server, "tools/list", {"_meta": meta()})["result"]
    names = [t["name"] for t in result["tools"]]
    assert names == [
        "localsub_diagnostics",
        "localsub_list_jobs",
        "localsub_get_job",
        "localsub_analyze_performance",
        "localsub_compare_jobs",
        "localsub_models",
        "localsub_optimization_plan",
    ]
    for tool in result["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False


def test_analyze_tool_finds_slow_translator_and_redacts(server) -> None:
    result = call(server, "localsub_analyze_performance", {"limit": 10})
    assert not result.get("isError")
    found = {f["id"] for f in result["structuredContent"]["findings"]}
    assert "slow_translation_model_load" in found
    assert "STAR-888" not in json.dumps(result)


def test_list_and_get_job(server) -> None:
    listed = call(server, "localsub_list_jobs", {"limit": 1})["structuredContent"]
    assert listed["count"] == 1 and listed["jobs"][0]["job_id"] == "slow"
    assert listed["jobs"][0]["source_kind"] == "network"
    assert listed["jobs"][0]["load_translation_model_ms"] == 410_000
    record = call(server, "localsub_get_job", {"job_id": "fast"})["structuredContent"]
    assert record["timings_ms"]["load_model"] == 8_000
    assert "market-briefing" not in json.dumps(record)


def test_compare_and_plan(server) -> None:
    cmp = call(server, "localsub_compare_jobs", {"job_a": "fast", "job_b": "slow"})
    assert cmp["structuredContent"]["timings"]["load_model"]["delta_ms"] == 1000
    plan = call(server, "localsub_optimization_plan", {"language": "vi"})
    assert plan["content"][0]["text"].startswith("# LocalSub NPU")
    assert "slow_translation_model_load" in plan["structuredContent"]["markdown"]


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("localsub_list_jobs", {"limit": 0}, "between 1 and 200"),
        ("localsub_list_jobs", {"limit": "5"}, "integer"),
        ("localsub_list_jobs", {"state": "running"}, "state"),
        ("localsub_get_job", {}, "job_id"),
        ("localsub_get_job", {"job_id": "../etc/passwd"}, "job_id"),
        ("localsub_get_job", {"job_id": "missing"}, "not found"),
        ("localsub_analyze_performance", {"job_ids": []}, "job_ids"),
        ("localsub_diagnostics", {"x": 1}, "unknown argument"),
        ("localsub_optimization_plan", {"language": "fr"}, "language"),
    ],
)
def test_invalid_tool_arguments_are_tool_errors(server, name, arguments, message) -> None:
    result = call(server, name, arguments)
    assert result["isError"] is True
    assert message in result["content"][0]["text"]


def test_unknown_tool_is_protocol_error(server) -> None:
    response = rpc(server, "tools/call", {"name": "rm_rf", "arguments": {}, "_meta": meta()})
    assert response["error"]["code"] == -32602


def test_resources_and_prompts(server) -> None:
    resources = rpc(server, "resources/list", {"_meta": meta()})["result"]["resources"]
    assert {r["uri"] for r in resources} == {
        "localsub://jobs/recent",
        "localsub://analysis/latest",
        "localsub://catalog",
    }
    read = rpc(server, "resources/read", {"uri": "localsub://jobs/recent", "_meta": meta()})
    payload = json.loads(read["result"]["contents"][0]["text"])
    assert payload["count"] == 2
    missing = rpc(server, "resources/read", {"uri": "file:///etc/passwd", "_meta": meta()})
    assert missing["error"]["code"] == -32002
    prompts = rpc(server, "prompts/list", {"_meta": meta()})["result"]["prompts"]
    assert [p["name"] for p in prompts] == ["optimize_localsub", "diagnose_job"]
    got = rpc(
        server,
        "prompts/get",
        {"name": "optimize_localsub", "arguments": {"focus": "load"}, "_meta": meta()},
    )["result"]
    assert "category 'load'" in got["messages"][0]["content"]["text"]
    bad = rpc(server, "prompts/get", {"name": "diagnose_job", "arguments": {}, "_meta": meta()})
    assert bad["error"]["code"] == -32602


def test_models_tool(server) -> None:
    models = call(server, "localsub_models")["structuredContent"]["models"]
    assert any(m["kind"] == "translation" for m in models)


def test_serve_loop_handles_parse_errors(settings: Settings) -> None:
    lines = [
        b"not json\n",
        b"\n",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}).encode() + b"\n",
    ]
    out = io.StringIO()
    assert mcp_server.serve(settings, stdin=io.BytesIO(b"".join(lines)), stdout=out) == 0
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in responses] == [None, 7]
    assert responses[0]["error"]["code"] == -32700


def test_stdio_subprocess_stdout_is_protocol_only(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "LOCALSUB_OUTPUT_DIR": str(tmp_path / "out"),
        "LOCALSUB_MODEL_DIR": str(tmp_path / "models"),
    }
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "localsub_diagnostics", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "server/discover", "params": {"_meta": meta()}},
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "app.main", "mcp"],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=root,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.splitlines()]  # every line is JSON
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert all("result" in r for r in responses)
    assert "ready" in proc.stderr
