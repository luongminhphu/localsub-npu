import { describe, expect, it } from "vitest";
import { countBySeverity, dataAgeMs, mcpConfigJson, sortFindings } from "./mcp";
import type { AnalysisReport, Finding, McpLaunch } from "./types";

const launch: McpLaunch = {
  command: "C:\\Program Files\\LocalSub NPU\\worker\\localsub-worker.exe",
  args: ["mcp"],
  cwd: "C:\\Program Files\\LocalSub NPU\\worker",
  env: { LOCALSUB_OUTPUT_DIR: "C:\\Users\\u\\AppData\\Local\\com.luongminhphu.localsubnpu\\output", PYTHONUTF8: "1" },
  bundled: true,
};

const finding = (id: string, severity: Finding["severity"]): Finding => ({
  id,
  severity,
  category: "load",
  title: { vi: id, en: id },
  detail: { vi: "", en: "" },
  recommendation: { vi: "", en: "" },
  job_ids: [],
  evidence: {},
  code_refs: [],
});

describe("mcp config", () => {
  it("builds Claude Desktop config without cwd", () => {
    const cfg = JSON.parse(mcpConfigJson(launch, "claude"));
    expect(cfg.mcpServers["localsub-npu"]).toEqual({ command: launch.command, args: ["mcp"], env: launch.env });
  });

  it("builds VS Code mcp.json with stdio type and cwd", () => {
    const cfg = JSON.parse(mcpConfigJson(launch, "vscode"));
    expect(cfg.servers["localsub-npu"].type).toBe("stdio");
    expect(cfg.servers["localsub-npu"].cwd).toBe(launch.cwd);
  });

  it("keeps Windows backslashes valid in JSON", () => {
    expect(JSON.parse(mcpConfigJson(launch, "generic")).command).toBe(launch.command);
  });
});

describe("findings helpers", () => {
  it("sorts by severity and counts", () => {
    const list = [finding("a", "low"), finding("b", "high"), finding("c", "info"), finding("d", "medium")];
    expect(sortFindings(list).map((f) => f.id)).toEqual(["b", "d", "a", "c"]);
    expect(countBySeverity(list)).toEqual({ high: 1, medium: 1, low: 1, info: 1 });
  });

  it("computes data age from the newest job", () => {
    const report = { newest_job_at: "2026-10-17T00:00:00Z" } as AnalysisReport;
    expect(dataAgeMs(report, new Date("2026-10-17T01:00:00Z"))).toBe(3_600_000);
    expect(dataAgeMs(null, new Date())).toBeNull();
    expect(dataAgeMs({ newest_job_at: "bad" } as AnalysisReport, new Date())).toBeNull();
  });
});
