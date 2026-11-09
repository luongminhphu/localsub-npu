import { describe, expect, it } from "vitest";
import {
  CLAUDE_CODE_PERMISSION,
  claudeCodeCommand,
  claudeCodeSettingsJson,
  claudeConnectorUrl,
  countBySeverity,
  dataAgeMs,
  maskedClaudeConnectorUrl,
  mcpConfigJson,
  mergeClaudeDesktopConfig,
  psQuote,
  REMOTE_TARGET,
  sortFindings,
} from "./mcp";
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

describe("Claude Desktop config merge", () => {
  const entry = { command: launch.command, args: ["mcp"], env: launch.env };

  it("creates a config when the file is missing or empty", () => {
    for (const existing of [null, "", "  \n"]) {
      const merged = mergeClaudeDesktopConfig(existing, launch);
      expect(JSON.parse(merged.text)).toEqual({ mcpServers: { "localsub-npu": entry } });
      expect(merged.replaced).toBe(false);
      expect(merged.unchanged).toBe(false);
      expect(merged.text.endsWith("\n")).toBe(true);
    }
  });

  it("keeps other servers and settings", () => {
    const existing = JSON.stringify({
      globalShortcut: "Ctrl+Space",
      mcpServers: { filesystem: { command: "npx", args: ["-y", "fs"] } },
    });
    const cfg = JSON.parse(mergeClaudeDesktopConfig(existing, launch).text);
    expect(cfg.globalShortcut).toBe("Ctrl+Space");
    expect(Object.keys(cfg.mcpServers)).toEqual(["filesystem", "localsub-npu"]);
    expect(cfg.mcpServers["localsub-npu"]).toEqual(entry);
  });

  it("replaces an outdated entry and detects an identical one", () => {
    const old = JSON.stringify({ mcpServers: { "localsub-npu": { command: "old.exe", args: ["mcp"] } } });
    const merged = mergeClaudeDesktopConfig(old, launch);
    expect(merged.replaced).toBe(true);
    expect(merged.unchanged).toBe(false);
    const again = mergeClaudeDesktopConfig(merged.text, launch);
    expect(again.unchanged).toBe(true);
  });

  it("accepts a UTF-8 BOM", () => {
    expect(() => mergeClaudeDesktopConfig("\uFEFF{}", launch)).not.toThrow();
  });

  it("refuses to overwrite invalid JSON", () => {
    for (const bad of ["{ mcpServers: ", "[]", "42", '{"mcpServers": []}', '{"mcpServers": "x"}']) {
      expect(() => mergeClaudeDesktopConfig(bad, launch)).toThrow(/^INVALID_JSON/);
    }
  });
});

describe("Claude Code", () => {
  it("quotes PowerShell arguments", () => {
    expect(psQuote("mcp")).toBe("mcp");
    expect(psQuote("-m")).toBe("-m");
    expect(psQuote("C:\\Program Files\\x.exe")).toBe("'C:\\Program Files\\x.exe'");
    expect(psQuote("it's")).toBe("'it''s'");
    expect(psQuote("$env:X")).toBe("'$env:X'");
  });

  it("builds a user-scoped claude mcp add command", () => {
    const cmd = claudeCodeCommand(launch);
    expect(cmd).toBe(
      "claude mcp add --transport stdio --scope user " +
        "--env 'LOCALSUB_OUTPUT_DIR=C:\\Users\\u\\AppData\\Local\\com.luongminhphu.localsubnpu\\output' " +
        "--env 'PYTHONUTF8=1' localsub-npu -- 'C:\\Program Files\\LocalSub NPU\\worker\\localsub-worker.exe' mcp",
    );
    expect(mcpConfigJson(launch, "claudecode")).toBe(cmd);
  });

  it("source mode keeps python -m app.main mcp after --", () => {
    const source = { ...launch, command: "C:\\py\\python.exe", args: ["-m", "app.main", "mcp"], env: {} };
    expect(claudeCodeCommand(source)).toBe(
      "claude mcp add --transport stdio --scope user localsub-npu -- 'C:\\py\\python.exe' -m app.main mcp",
    );
  });

  it("allows the server's tools in settings", () => {
    expect(CLAUDE_CODE_PERMISSION).toBe("mcp__localsub-npu");
    expect(JSON.parse(claudeCodeSettingsJson())).toEqual({ permissions: { allow: ["mcp__localsub-npu"] } });
  });
});

describe("Claude web connector", () => {
  it("puts the key in the URL path", () => {
    expect(claudeConnectorUrl("https://a-b.trycloudflare.com/", "k_e-y")).toBe(
      "https://a-b.trycloudflare.com/mcp/k_e-y",
    );
    expect(claudeConnectorUrl("https://x.trycloudflare.com", "a+b/c")).toBe(
      "https://x.trycloudflare.com/mcp/a%2Bb%2Fc",
    );
  });

  it("masks the key for display", () => {
    const shown = maskedClaudeConnectorUrl("https://x.trycloudflare.com", "abcdefghijklmnop");
    expect(shown).toBe("https://x.trycloudflare.com/mcp/abcd••••••mnop");
  });

  it("maps clients to remote targets", () => {
    expect(REMOTE_TARGET.claudeai).toBe("claude");
    expect(REMOTE_TARGET.perplexity).toBe("perplexity");
    expect(REMOTE_TARGET.claude).toBeUndefined();
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
