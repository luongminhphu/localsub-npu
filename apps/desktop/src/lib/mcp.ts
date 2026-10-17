import type { AnalysisReport, Finding, McpHttpEvent, McpHttpRequestEvent, McpLaunch, Severity } from "./types";

export type McpClient = "claude" | "vscode" | "perplexity" | "generic";
export const MCP_CLIENTS: McpClient[] = ["claude", "vscode", "perplexity", "generic"];
export const DEFAULT_HTTP_PORT = 8765;
export const PERPLEXITY_CONNECTOR_NAME = "LocalSub NPU";
export const CLOUDFLARED_INSTALL = "winget install --id Cloudflare.cloudflared";
export const MCP_SERVER_NAME = "localsub-npu";

const SEVERITY_RANK: Record<Severity, number> = { high: 0, medium: 1, low: 2, info: 3 };

/** JSON snippet for each client. Claude Desktop has no `cwd`, so env carries the data folders. */
export function mcpConfigJson(launch: McpLaunch, client: McpClient): string {
  const server = { command: launch.command, args: launch.args, env: launch.env };
  if (client === "claude") return JSON.stringify({ mcpServers: { [MCP_SERVER_NAME]: server } }, null, 2);
  if (client === "vscode") {
    return JSON.stringify(
      { servers: { [MCP_SERVER_NAME]: { type: "stdio", ...server, cwd: launch.cwd } } },
      null,
      2,
    );
  }
  return JSON.stringify({ name: MCP_SERVER_NAME, transport: "stdio", ...server, cwd: launch.cwd }, null, 2);
}

export function sortFindings(findings: Finding[]): Finding[] {
  return [...findings].sort((a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9));
}

export function countBySeverity(findings: Finding[]): Record<Severity, number> {
  const out: Record<Severity, number> = { high: 0, medium: 0, low: 0, info: 0 };
  for (const f of findings) if (f.severity in out) out[f.severity] += 1;
  return out;
}

/** Freshness of the analysed data: age of the newest job relative to `now`, in ms (null = none). */
export function dataAgeMs(report: AnalysisReport | null, now: Date): number | null {
  if (!report?.newest_job_at) return null;
  const at = new Date(report.newest_job_at).getTime();
  return Number.isNaN(at) ? null : Math.max(0, now.getTime() - at);
}

/** Port typed by the user: 1024-65535 only (the worker refuses privileged ports). */
export function parsePort(text: string): number | null {
  if (!/^\d{4,5}$/.test(text.trim())) return null;
  const port = Number(text.trim());
  return port >= 1024 && port <= 65535 ? port : null;
}

/** Public MCP endpoint for the Perplexity connector form. */
export function connectorUrl(publicUrl: string): string {
  return `${publicUrl.replace(/\/+$/, "")}/mcp`;
}

export function maskToken(token: string): string {
  return token.length <= 10 ? "••••" : `${token.slice(0, 4)}••••••${token.slice(-4)}`;
}

export function isCloudflaredMissing(error: string): boolean {
  return error.includes("CLOUDFLARED_MISSING");
}

/** Fields for Perplexity Portable Computer → Settings → MCP servers → Local (stdio). */
export function portableStdioFields(launch: McpLaunch): { command: string; args: string; cwd: string; env: string } {
  return {
    command: launch.command,
    args: launch.args.join("\n"),
    cwd: launch.cwd,
    env: Object.entries(launch.env)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
  };
}

export type ServerPhase = "stopped" | "starting" | "listening" | "error";
export type TunnelPhase = "off" | "starting" | "open" | "error";

export interface McpHttpView {
  server: ServerPhase;
  serverSince: string | null;
  localUrl: string | null;
  token: string | null;
  tokenSource: string | null;
  auditLog: string | null;
  tunnel: TunnelPhase;
  tunnelSince: string | null;
  publicUrl: string | null;
  lastRequest: McpHttpRequestEvent | null;
  requests: number;
  authFailures: number;
  error: string | null;
}

export const INITIAL_HTTP_VIEW: McpHttpView = {
  server: "stopped",
  serverSince: null,
  localUrl: null,
  token: null,
  tokenSource: null,
  auditLog: null,
  tunnel: "off",
  tunnelSince: null,
  publicUrl: null,
  lastRequest: null,
  requests: 0,
  authFailures: 0,
  error: null,
};

export type McpHttpAction =
  | { kind: "starting"; at: string }
  | { kind: "event"; event: McpHttpEvent; at: string }
  | { kind: "exit"; code: number | null; cancelled: boolean; at: string }
  | { kind: "failed"; message: string; at: string }
  | { kind: "tunnel-starting"; at: string }
  | { kind: "tunnel-url"; url: string; at: string }
  | { kind: "tunnel-exit"; code: number | null; cancelled: boolean; at: string }
  | { kind: "tunnel-failed"; message: string; at: string }
  | { kind: "sync"; server: boolean; tunnel: boolean; at: string };

/** Error marker: a server from an earlier session is running (key not known to this view). */
export const ORPHAN = "ORPHAN";

const tunnelOff = { tunnel: "off" as const, tunnelSince: null, publicUrl: null };

export function mcpHttpReducer(state: McpHttpView, action: McpHttpAction): McpHttpView {
  switch (action.kind) {
    case "starting":
      return { ...state, server: "starting", serverSince: action.at, error: null };
    case "event": {
      const e = action.event;
      if (e.type === "mcp_http_request") {
        return {
          ...state,
          lastRequest: e,
          requests: state.requests + 1,
          authFailures: state.authFailures + (e.auth === "failed" ? 1 : 0),
        };
      }
      if (e.state === "listening") {
        return {
          ...state,
          server: "listening",
          serverSince: e.at ?? action.at,
          localUrl: e.url ?? null,
          token: e.token ?? null,
          tokenSource: e.token_source ?? null,
          auditLog: e.audit_log ?? null,
          error: null,
        };
      }
      if (e.state === "error") {
        return { ...state, server: "error", serverSince: action.at, error: e.message ?? e.code ?? "error" };
      }
      return { ...state, server: "stopped", serverSince: e.at ?? action.at, localUrl: null, ...tunnelOff };
    }
    case "exit": {
      // A non-zero exit before "listening" keeps the error message from the event.
      const failed = !action.cancelled && action.code !== 0;
      if (state.server === "error") return { ...state, ...tunnelOff };
      return {
        ...state,
        server: failed ? "error" : "stopped",
        serverSince: action.at,
        localUrl: null,
        error: failed ? `exit code ${action.code ?? "?"}` : null,
        ...tunnelOff,
      };
    }
    case "failed":
      return { ...state, server: "error", serverSince: action.at, error: action.message };
    case "tunnel-starting":
      return { ...state, tunnel: "starting", tunnelSince: action.at, publicUrl: null, error: null };
    case "tunnel-url":
      return { ...state, tunnel: "open", tunnelSince: action.at, publicUrl: action.url };
    case "tunnel-exit": {
      if (state.tunnel === "off") return state;
      const failed = !action.cancelled && action.code !== 0;
      return {
        ...state,
        tunnel: failed ? "error" : "off",
        tunnelSince: action.at,
        publicUrl: null,
        error: failed ? `cloudflared exit code ${action.code ?? "?"}` : state.error,
      };
    }
    case "tunnel-failed":
      return { ...state, tunnel: "error", tunnelSince: action.at, publicUrl: null, error: action.message };
    case "sync": {
      // Processes outlive a webview reload: their URL/key are unknown until the server restarts.
      let next = state;
      if (action.server && state.server === "stopped") {
        next = { ...next, server: "listening", serverSince: action.at, error: ORPHAN };
      } else if (!action.server && (state.server === "listening" || state.server === "starting")) {
        next = { ...next, server: "stopped", serverSince: action.at, localUrl: null };
      }
      if (action.tunnel && next.tunnel === "off") next = { ...next, tunnel: "open", tunnelSince: action.at };
      if (!action.tunnel && (next.tunnel === "open" || next.tunnel === "starting")) next = { ...next, ...tunnelOff };
      return next;
    }
  }
}
