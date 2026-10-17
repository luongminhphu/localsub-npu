import { describe, expect, it } from "vitest";
import {
  connectorUrl,
  INITIAL_HTTP_VIEW,
  isCloudflaredMissing,
  maskToken,
  MCP_CLIENTS,
  mcpHttpReducer,
  ORPHAN,
  parsePort,
  portableStdioFields,
  type McpHttpAction,
  type McpHttpView,
} from "./mcp";
import type { McpHttpRequestEvent } from "./types";

const at = "2026-10-17T05:00:00.000Z";
const run = (actions: McpHttpAction[], from: McpHttpView = INITIAL_HTTP_VIEW) => actions.reduce(mcpHttpReducer, from);
const listening: McpHttpAction = {
  kind: "event",
  at,
  event: {
    type: "mcp_http",
    state: "listening",
    url: "http://127.0.0.1:8765/mcp",
    token: "abcd1234efgh5678ijkl9012mnop3456",
    token_source: "file",
    audit_log: "C:\\out\\logs\\mcp_http.jsonl",
    at,
  },
};
const request = (auth: McpHttpRequestEvent["auth"], status: number): McpHttpAction => ({
  kind: "event",
  at,
  event: { type: "mcp_http_request", ts: at, http: "POST", method: "tools/call", name: "localsub_list_jobs", status, auth, duration_ms: 3 },
});

describe("perplexity helpers", () => {
  it("lists Perplexity as an MCP client", () => {
    expect(MCP_CLIENTS).toContain("perplexity");
  });

  it("validates ports", () => {
    expect(parsePort("8765")).toBe(8765);
    expect(parsePort(" 65535 ")).toBe(65535);
    for (const bad of ["80", "1023", "65536", "87a5", "", "8765.5", "-8765"]) expect(parsePort(bad)).toBeNull();
  });

  it("builds the connector URL and masks keys", () => {
    expect(connectorUrl("https://quiet-river.trycloudflare.com/")).toBe("https://quiet-river.trycloudflare.com/mcp");
    expect(maskToken("abcd1234efgh5678ijkl9012mnop3456")).toBe("abcd••••••3456");
    expect(maskToken("short")).toBe("••••");
    expect(isCloudflaredMissing("CLOUDFLARED_MISSING: install it")).toBe(true);
  });

  it("formats Portable Computer stdio fields one per line", () => {
    const fields = portableStdioFields({
      command: "C:\\w\\localsub-worker.exe",
      args: ["mcp"],
      cwd: "C:\\w",
      env: { LOCALSUB_OUTPUT_DIR: "C:\\o", PYTHONUTF8: "1" },
      bundled: true,
    });
    expect(fields.args).toBe("mcp");
    expect(fields.env).toBe("LOCALSUB_OUTPUT_DIR=C:\\o\nPYTHONUTF8=1");
  });
});

describe("mcpHttpReducer", () => {
  it("goes starting → listening and keeps the key and audit path", () => {
    const view = run([{ kind: "starting", at }, listening]);
    expect(view.server).toBe("listening");
    expect(view.token).toMatch(/^abcd/);
    expect(view.localUrl).toBe("http://127.0.0.1:8765/mcp");
    expect(view.auditLog).toContain("mcp_http.jsonl");
  });

  it("counts requests and failed authentications", () => {
    const view = run([listening, request("ok", 200), request("failed", 401), request("ok", 200)]);
    expect(view.requests).toBe(3);
    expect(view.authFailures).toBe(1);
    expect(view.lastRequest?.status).toBe(200);
  });

  it("opens and closes the tunnel", () => {
    let view = run([listening, { kind: "tunnel-starting", at }, { kind: "tunnel-url", url: "https://x.trycloudflare.com", at }]);
    expect(view.tunnel).toBe("open");
    expect(view.publicUrl).toBe("https://x.trycloudflare.com");
    view = run([{ kind: "tunnel-exit", code: null, cancelled: true, at }], view);
    expect(view.tunnel).toBe("off");
    expect(view.publicUrl).toBeNull();
    expect(view.error).toBeNull();
  });

  it("reports an unexpected tunnel exit as an error", () => {
    const view = run([listening, { kind: "tunnel-starting", at }, { kind: "tunnel-exit", code: 1, cancelled: false, at }]);
    expect(view.tunnel).toBe("error");
    expect(view.error).toContain("cloudflared");
  });

  it("server exit closes the tunnel too", () => {
    const view = run([listening, { kind: "tunnel-url", url: "https://x.trycloudflare.com", at }, { kind: "exit", code: null, cancelled: true, at }]);
    expect(view.server).toBe("stopped");
    expect(view.tunnel).toBe("off");
    expect(view.localUrl).toBeNull();
  });

  it("keeps the worker error message when it exits after a port conflict", () => {
    const view = run([
      { kind: "starting", at },
      { kind: "event", at, event: { type: "mcp_http", state: "error", code: "PORT_IN_USE", message: "cannot listen on 127.0.0.1:8765" } },
      { kind: "exit", code: 3, cancelled: false, at },
    ]);
    expect(view.server).toBe("error");
    expect(view.error).toContain("cannot listen");
  });

  it("marks a server left running from an earlier session", () => {
    const view = run([{ kind: "sync", server: true, tunnel: false, at }]);
    expect(view.server).toBe("listening");
    expect(view.error).toBe(ORPHAN);
    expect(view.token).toBeNull();
    const stopped = run([{ kind: "sync", server: false, tunnel: false, at }], run([listening]));
    expect(stopped.server).toBe("stopped");
  });

  it("starting clears a previous error", () => {
    const view = run([{ kind: "failed", message: "boom", at }, { kind: "starting", at }]);
    expect(view.error).toBeNull();
    expect(view.server).toBe("starting");
  });
});
