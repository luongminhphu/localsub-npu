import type { AnalysisReport, Finding, McpLaunch, Severity } from "./types";

export type McpClient = "claude" | "vscode" | "generic";
export const MCP_CLIENTS: McpClient[] = ["claude", "vscode", "generic"];
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
