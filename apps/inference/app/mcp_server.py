"""Read-only Model Context Protocol (MCP) server over stdio.

Lets an AI assistant (Claude Desktop, Claude Code, claude.ai, VS Code, Perplexity, ...) read
LocalSub's job audit log, diagnostics and model catalog, run the performance analyzer and get
an optimization brief that points at the code to change. It never starts jobs, downloads or
deletes models.

Dependency-free JSON-RPC 2.0 so it runs inside the frozen PyInstaller worker
(``localsub-worker.exe mcp``). Supports MCP 2026-07-28 (stateless: ``server/discover`` and
per-request ``_meta`` protocol version) and the legacy ``initialize`` handshake
(2025-11-25 / 2025-06-18 / 2025-03-26) that many clients still use.

stdout carries protocol messages only: the real stdout is duplicated for the protocol and
file descriptor 1 is pointed at stderr, so native libraries (OpenVINO) cannot corrupt it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import IO, Any

from app import __version__, analysis, history, model_store
from app.audit import to_jsonable
from app.config import Settings

LATEST_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = (LATEST_VERSION, *LEGACY_VERSIONS)
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

LIST_TTL_MS = 300_000  # tool/prompt/resource lists only change with a new app version
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

INSTRUCTIONS = (
    "LocalSub NPU is a Windows desktop app that makes subtitles locally with OpenVINO "
    "(Whisper speech recognition + Qwen translation on NPU/GPU/CPU). This server is read-only: "
    "it cannot start jobs or change settings.\n"
    "Workflow: 1) localsub_analyze_performance (findings ranked by severity, with job_ids and "
    "code_refs); 2) evidence with localsub_get_job / localsub_compare_jobs on those job_ids; "
    "3) localsub_optimization_plan for a code-change brief "
    "(source: github.com/luongminhphu/localsub-npu). Use localsub_diagnostics for setup "
    "problems (devices, FFmpeg, models) and localsub_list_jobs to find job ids. "
    "Prefer small limits; null fields are omitted from job summaries.\n"
    "Timestamps are UTC; the user is in Asia/Ho_Chi_Minh (GMT+7) and reads Vietnamese. "
    "Paths are redacted unless the server was started with --no-redact. Propose code changes "
    "as pull requests; never claim a fix is verified without a new job record."
)


SERVER_INFO = {
    "name": "localsub-npu",
    "title": "LocalSub NPU",
    "version": __version__,
    "description": "Read-only performance, log and diagnostics access for LocalSub NPU",
    "websiteUrl": "https://github.com/luongminhphu/localsub-npu",
}
CAPABILITIES = {"tools": {}, "resources": {}, "prompts": {}}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ToolError(Exception):
    """Reported to the model as ``isError: true`` so it can self-correct."""


def drop_nulls(value: Any) -> Any:
    """Omit ``None`` fields (recursively) so job summaries cost fewer model tokens."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def compact_json(value: Any) -> str:
    """Tool text for the model: no indentation (about 40% fewer tokens than indent=1)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------- argument checks


def _int_arg(args: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"'{name}' must be an integer between {low} and {high}")
    if not low <= value <= high:
        raise ToolError(f"'{name}' must be between {low} and {high}, got {value}")
    return value


def _job_id_arg(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not JOB_ID_RE.match(value):
        raise ToolError(f"'{name}' must be a job id string (letters, digits, - _ . :)")
    return value


def _str_choice(args: dict[str, Any], name: str, choices: tuple[str, ...], default: str) -> str:
    value = args.get(name, default)
    if value not in choices:
        raise ToolError(f"'{name}' must be one of: {', '.join(choices)}")
    return value


def _no_extra(args: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(args) - allowed)
    if extra:
        raise ToolError(f"unknown argument(s): {', '.join(extra)}")


# --------------------------------------------------------------------------- server


class LocalSubMcp:
    def __init__(self, settings: Settings, redact: bool = True) -> None:
        self.settings = settings
        self.redact = redact

    @property
    def log_path(self) -> Path:
        return self.settings.output_dir / "logs" / "jobs.jsonl"

    # ----------------------------------------------------------------- data helpers

    def _scrub(self, value: Any) -> Any:
        if not self.redact:
            return value
        if isinstance(value, str):
            return analysis.scrub_text(value)
        if isinstance(value, dict):
            return {k: self._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        return value

    def _find(self, job_id: str) -> dict[str, Any]:
        records, _ = analysis.read_records(self.log_path, analysis.MAX_LIMIT)
        for record in records:
            if record.get("job_id") == job_id:
                return record
        raise ToolError(f"job '{job_id}' not found in the last {analysis.MAX_LIMIT} jobs")

    def _summary(self, record: dict[str, Any]) -> dict[str, Any]:
        out = history.summarize(record)
        out.pop("project_path", None)  # a full local path; never sent to the AI assistant
        out["source_name"] = analysis.display_name(record.get("source_path"), self.redact)
        out["source_kind"] = analysis.source_kind(record.get("source_path"))
        out["openvino_version"] = record.get("openvino_version")
        out["load_model_ms"] = (record.get("timings_ms") or {}).get("load_model")
        out["load_translation_model_ms"] = (record.get("timings_ms") or {}).get(
            "load_translation_model"
        )
        return out

    # ----------------------------------------------------------------- tools

    def tool_diagnostics(self, args: dict[str, Any]) -> dict[str, Any]:
        _no_extra(args, set())
        from app.diagnostics import build_diagnostic_report

        report = asdict(build_diagnostic_report(self.settings))
        records, skipped = analysis.read_records(self.log_path, 1)
        report.update(
            output_dir=str(self.settings.output_dir),
            model_dir=str(self.settings.model_dir),
            installed_models=model_store.installed_models(self.settings.model_dir),
            log_found=self.log_path.is_file(),
            last_job_at=records[0].get("started_at") if records else None,
            ov_cache_present=(self.settings.model_dir / ".ov-cache").is_dir(),
        )
        return self._scrub(to_jsonable(report))

    def tool_list_jobs(self, args: dict[str, Any]) -> dict[str, Any]:
        _no_extra(args, {"limit", "state", "since"})
        limit = _int_arg(args, "limit", 20, 1, analysis.MAX_LIMIT)
        state = args.get("state")
        if state is not None and state not in ("completed", "failed", "cancelled"):
            raise ToolError("'state' must be completed, failed or cancelled")
        since = args.get("since")
        if since is not None and (not isinstance(since, str) or len(since) < 10):
            raise ToolError("'since' must be an ISO 8601 date or datetime, e.g. 2026-10-01")
        records, skipped = analysis.read_records(self.log_path, analysis.MAX_LIMIT)
        if state:
            records = [r for r in records if r.get("state") == state]
        if since:
            records = [r for r in records if str(r.get("started_at") or "") >= since]
        jobs = [drop_nulls(self._summary(r)) for r in records[:limit]]
        return to_jsonable(
            {
                "log_found": self.log_path.is_file(),
                "skipped_lines": skipped,
                "count": len(jobs),
                "jobs": jobs,
            }
        )

    def tool_get_job(self, args: dict[str, Any]) -> dict[str, Any]:
        _no_extra(args, {"job_id"})
        record = self._find(_job_id_arg(args, "job_id"))
        return to_jsonable(analysis.redact_record(record, self.redact))

    def tool_analyze(self, args: dict[str, Any]) -> dict[str, Any]:
        _no_extra(args, {"limit", "job_ids"})
        limit = _int_arg(args, "limit", analysis.DEFAULT_LIMIT, 1, analysis.MAX_LIMIT)
        job_ids = args.get("job_ids")
        if job_ids is not None:
            if not isinstance(job_ids, list) or not job_ids or len(job_ids) > 50:
                raise ToolError("'job_ids' must be a list of 1-50 job ids")
            for job_id in job_ids:
                if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
                    raise ToolError(f"invalid job id: {job_id!r}")
        return to_jsonable(analysis.analyze(self.log_path, limit, job_ids, redact=self.redact))

    def tool_compare(self, args: dict[str, Any]) -> dict[str, Any]:
        _no_extra(args, {"job_a", "job_b"})
        a = self._find(_job_id_arg(args, "job_a"))
        b = self._find(_job_id_arg(args, "job_b"))
        return to_jsonable(analysis.compare_jobs(a, b))

    def tool_models(self, args: dict[str, Any]) -> dict[str, Any]:
        _no_extra(args, set())
        models = model_store.list_models(self.settings.model_dir)
        return self._scrub(to_jsonable({"models": models}))

    def tool_plan(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        _no_extra(args, {"limit", "language"})
        limit = _int_arg(args, "limit", analysis.DEFAULT_LIMIT, 1, analysis.MAX_LIMIT)
        language = _str_choice(args, "language", ("en", "vi"), "en")
        report = analysis.analyze(self.log_path, limit, redact=self.redact)
        markdown = analysis.optimization_plan(report, language)
        return markdown, to_jsonable(
            {"markdown": markdown, "findings": report["findings"], "status": report["status"]}
        )

    def tools(self) -> list[dict[str, Any]]:
        limit = {
            "type": "integer",
            "minimum": 1,
            "maximum": analysis.MAX_LIMIT,
            "description": "How many of the most recent jobs to read",
        }
        job_id = {
            "type": "string",
            "pattern": JOB_ID_RE.pattern,
            "description": "Job id from localsub_list_jobs or a finding's job_ids",
        }
        read_only = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}

        def tool(name, title, description, properties=None, required=None):
            schema: dict[str, Any] = {
                "type": "object",
                "properties": properties or {},
                "additionalProperties": False,
            }
            if required:
                schema["required"] = required
            return {
                "name": name,
                "title": title,
                "description": description,
                "inputSchema": schema,
                "annotations": {"title": title, **read_only, "idempotentHint": True},
            }

        return [
            tool(
                "localsub_diagnostics",
                "Diagnostics",
                "Setup health: FFmpeg/OpenVINO status, available devices (NPU/GPU/CPU), installed "
                "models, whether the compile cache (.ov-cache) exists and when the last job ran. "
                "Use first when jobs fail or a device is missing.",
            ),
            tool(
                "localsub_list_jobs",
                "List jobs",
                "Recent jobs from the audit log, newest first: state, models, devices, media "
                "duration, stage timings (ms), real-time factors, OpenVINO version. Null fields "
                "are omitted. Use to find job ids; keep limit small (default 20).",
                {
                    "limit": {**limit, "default": 20},
                    "state": {
                        "type": "string",
                        "enum": ["completed", "failed", "cancelled"],
                        "description": "Only jobs that ended in this state",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO 8601 UTC lower bound for started_at",
                    },
                },
            ),
            tool(
                "localsub_get_job",
                "Get job",
                "Full audit record of one job: state transitions with timestamps, every timing, "
                "device attempts and fallbacks, translation tuning, recoveries, error. Use on the "
                "job_ids of a finding to explain it with numbers.",
                {"job_id": job_id},
                ["job_id"],
            ),
            tool(
                "localsub_analyze_performance",
                "Analyze performance",
                "Start here. Rule-based analysis of recent jobs: findings (id, severity, "
                "evidence, recommendation, job_ids, code_refs) and per-stage/per-model "
                "statistics. Optionally restrict to job_ids.",
                {
                    "limit": {**limit, "default": analysis.DEFAULT_LIMIT},
                    "job_ids": {
                        "type": "array",
                        "items": job_id,
                        "minItems": 1,
                        "maxItems": 50,
                        "description": "Analyze only these jobs (overrides limit)",
                    },
                },
            ),
            tool(
                "localsub_compare_jobs",
                "Compare jobs",
                "Stage-by-stage timing delta (b minus a) and configuration differences between "
                "two jobs, e.g. a slow job against a fast one with the same model and device, or "
                "before and after an update.",
                {
                    "job_a": {**job_id, "description": "Baseline job id"},
                    "job_b": {**job_id, "description": "Job id compared against job_a"},
                },
                ["job_a", "job_b"],
            ),
            tool(
                "localsub_models",
                "Models",
                "Model catalog with install status and size for speech recognition and "
                "translation models.",
            ),
            tool(
                "localsub_optimization_plan",
                "Optimization plan",
                "Markdown brief for a coding agent with repository access: each finding with "
                "evidence, the source functions to inspect and a test-first workflow ending in a "
                "pull request. Use language 'vi' when answering the user directly.",
                {
                    "limit": {**limit, "default": analysis.DEFAULT_LIMIT},
                    "language": {
                        "type": "string",
                        "enum": ["en", "vi"],
                        "default": "en",
                        "description": "Language of the brief",
                    },
                },
            ),
        ]

    def call_tool(self, name: Any, args: Any) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "localsub_diagnostics": self.tool_diagnostics,
            "localsub_list_jobs": self.tool_list_jobs,
            "localsub_get_job": self.tool_get_job,
            "localsub_analyze_performance": self.tool_analyze,
            "localsub_compare_jobs": self.tool_compare,
            "localsub_models": self.tool_models,
            "localsub_optimization_plan": self.tool_plan,
        }
        if not isinstance(name, str) or name not in handlers:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "'arguments' must be an object")
        try:
            result = handlers[name](args)
        except ToolError as exc:
            return {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "structuredContent": {"error": str(exc)},
                "isError": True,
            }
        except Exception as exc:  # unexpected: still give the model something to act on
            message = analysis.scrub_text(f"{type(exc).__name__}: {exc}", self.redact)
            return {
                "content": [{"type": "text", "text": f"Internal error: {message}"}],
                "structuredContent": {"error": message},
                "isError": True,
            }
        if isinstance(result, tuple):
            text, structured = result
        else:
            structured = result
            text = compact_json(result)
        return {"content": [{"type": "text", "text": text}], "structuredContent": structured}

    # ----------------------------------------------------------------- resources

    RESOURCES = (
        ("localsub://jobs/recent", "Recent jobs", "The 50 most recent job summaries (JSON)."),
        (
            "localsub://analysis/latest",
            "Latest analysis",
            "Performance findings for the last 50 jobs (JSON).",
        ),
        ("localsub://catalog", "Model catalog", "Models with install status (JSON)."),
    )

    def resources(self) -> list[dict[str, Any]]:
        return [
            {"uri": uri, "name": uri.split("://", 1)[1], "title": t, "description": d,
             "mimeType": "application/json"}
            for uri, t, d in self.RESOURCES
        ]  # fmt: skip

    def read_resource(self, uri: Any) -> dict[str, Any]:
        if uri == "localsub://jobs/recent":
            data = self.tool_list_jobs({"limit": 50})
        elif uri == "localsub://analysis/latest":
            data = self.tool_analyze({})
        elif uri == "localsub://catalog":
            data = self.tool_models({})
        else:
            raise RpcError(-32002, "Resource not found", {"uri": uri})
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": compact_json(data),
                }
            ],
            "ttlMs": 0,
            "cacheScope": "private",
        }

    # ----------------------------------------------------------------- prompts

    def prompts(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "optimize_localsub",
                "title": "Optimize LocalSub",
                "description": "Find the slowest stage in recent jobs and propose a tested code "
                "change as a pull request.",
                "arguments": [
                    {
                        "name": "focus",
                        "description": "load | asr | translation | io | all (default all)",
                        "required": False,
                    }
                ],
            },
            {
                "name": "diagnose_job",
                "title": "Diagnose a job",
                "description": "Explain why one job was slow or failed.",
                "arguments": [{"name": "job_id", "description": "Job id", "required": True}],
            },
        ]

    def get_prompt(self, name: Any, args: Any) -> dict[str, Any]:
        args = args if isinstance(args, dict) else {}
        if name == "optimize_localsub":
            focus = args.get("focus") or "all"
            if focus not in ("load", "asr", "translation", "io", "all"):
                raise RpcError(INVALID_PARAMS, "focus must be load, asr, translation, io or all")
            text = (
                "You are optimizing LocalSub NPU (github.com/luongminhphu/localsub-npu).\n"
                "1. Call localsub_diagnostics and localsub_analyze_performance.\n"
                f"2. Focus on findings with category '{focus}'"
                + (" (or all categories)" if focus == "all" else "")
                + "; rank by severity and by seconds the user waits.\n"
                "3. For the top finding, call localsub_get_job on its job_ids and "
                "localsub_compare_jobs against a fast job with the same model and device.\n"
                "4. Read the functions in code_refs, state the root cause with evidence, then "
                "write a failing pytest, patch, run `ruff check` and `pytest` (and `npm test` "
                "for desktop changes).\n"
                "5. Open a pull request describing evidence, change, tests run and what is still "
                "unverified on real NPU hardware. Do not merge or tag a release.\n"
                "Answer the user in Vietnamese."
            )
            description = "Optimize LocalSub from its job log"
        elif name == "diagnose_job":
            job_id = args.get("job_id")
            if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
                raise RpcError(INVALID_PARAMS, "job_id is required")
            text = (
                f"Call localsub_get_job with job_id={job_id}. Using its transitions and "
                "timings_ms, explain which stage took the longest and why (device, fallback, "
                "recoveries, translation tuning). Compare with a similar fast job via "
                "localsub_list_jobs and localsub_compare_jobs. Answer in Vietnamese with "
                "concrete numbers and the next action."
            )
            description = f"Diagnose job {job_id}"
        else:
            raise RpcError(INVALID_PARAMS, f"Unknown prompt: {name}")
        return {
            "description": description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }

    # ----------------------------------------------------------------- dispatch

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Process one JSON-RPC message; returns the response, or None for notifications."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Invalid JSON-RPC 2.0 message")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message
        if not isinstance(method, str):
            if is_notification or "result" in message or "error" in message:
                return None  # stray response: nothing to do
            return _error(msg_id, INVALID_REQUEST, "Missing method")
        if is_notification:
            return None  # notifications/initialized, notifications/cancelled, ...
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object")
        try:
            result = self._dispatch(method, params)
        except RpcError as exc:
            return _error(msg_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            text = analysis.scrub_text(f"{type(exc).__name__}: {exc}", self.redact)
            return _error(msg_id, INTERNAL_ERROR, text)
        result.setdefault("resultType", "complete")
        result.setdefault("_meta", {})[META_SERVER] = SERVER_INFO
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":  # legacy (<= 2025-11-25) handshake
            requested = params.get("protocolVersion")
            negotiated = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
            return {
                "protocolVersion": negotiated,
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        requested = meta.get(META_VERSION)
        if requested is not None and requested not in SUPPORTED_VERSIONS:
            raise RpcError(
                UNSUPPORTED_PROTOCOL_VERSION,
                "Unsupported protocol version",
                {"supported": list(SUPPORTED_VERSIONS), "requested": requested},
            )
        cache = {"ttlMs": LIST_TTL_MS, "cacheScope": "private"}
        if method == "server/discover":
            return {
                "supportedVersions": list(SUPPORTED_VERSIONS),
                "capabilities": CAPABILITIES,
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
                **cache,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools(), **cache}
        if method == "tools/call":
            return self.call_tool(params.get("name"), params.get("arguments"))
        if method == "resources/list":
            return {"resources": self.resources(), **cache}
        if method == "resources/templates/list":
            return {"resourceTemplates": [], **cache}
        if method == "resources/read":
            return self.read_resource(params.get("uri"))
        if method == "prompts/list":
            return {"prompts": self.prompts(), **cache}
        if method == "prompts/get":
            return self.get_prompt(params.get("name"), params.get("arguments"))
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def _protocol_stdout() -> IO[str]:
    """Keep a private handle on the real stdout and route fd 1 to stderr."""
    try:
        sys.stdout.flush()
        fd = os.dup(1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        return os.fdopen(fd, "w", encoding="utf-8", newline="\n")
    except (OSError, ValueError, AttributeError):  # e.g. pytest capture without real fds
        return sys.stdout


def serve(
    settings: Settings,
    redact: bool = True,
    stdin: IO[bytes] | None = None,
    stdout: IO[str] | None = None,
) -> int:
    server = LocalSubMcp(settings, redact=redact)
    out = stdout or _protocol_stdout()
    source = stdin or sys.stdin.buffer
    print(
        f"localsub mcp: ready (protocol {LATEST_VERSION} + legacy), redact={redact}",
        file=sys.stderr,
        flush=True,
    )
    for raw in source:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, PARSE_ERROR, "Parse error")
        else:
            if isinstance(message, list):
                response = _error(None, INVALID_REQUEST, "Batch requests are not supported")
            else:
                response = server.handle(message)
        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()
    return 0
