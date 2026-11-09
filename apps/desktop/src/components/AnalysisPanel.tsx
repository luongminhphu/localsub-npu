import { useCallback, useEffect, useMemo, useState } from "react";
import { formatDuration, formatLocal, offsetLabel } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";
import {
  claudeCodeSettingsJson,
  countBySeverity,
  dataAgeMs,
  MCP_CLIENTS,
  mcpConfigJson,
  mergeClaudeDesktopConfig,
  REMOTE_TARGET,
  sortFindings,
  type McpClient,
  type RemoteTarget,
} from "../lib/mcp";
import type { AnalysisReport, McpLaunch, Severity } from "../lib/types";
import type { WorkerBridge } from "../lib/worker";
import { RemoteConnect } from "./RemoteConnect";

const LIMIT = 50;
const SEVERITIES: Severity[] = ["high", "medium", "low", "info"];
const STAGES = ["extract", "load_model", "inference", "load_translation_model", "translation", "total"] as const;

type CopyState = { target: string; ok: boolean } | null;
type InstallNote = { kind: "ok" | "error"; text: string; backup?: string | null } | null;

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Runs the worker only while the tab is visible (each run spawns a short worker process). */
export function AnalysisPanel(props: { bridge: WorkerBridge | null; refreshKey: number; active: boolean }) {
  const { bridge, refreshKey, active } = props;
  const { t, locale } = useI18n();
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [launch, setLaunch] = useState<McpLaunch | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [client, setClient] = useState<McpClient>("claude");
  const [copied, setCopied] = useState<CopyState>(null);
  const [remoteTarget, setRemoteTarget] = useState<RemoteTarget>("perplexity");
  const [installing, setInstalling] = useState(false);
  const [installNote, setInstallNote] = useState<InstallNote>(null);
  const [now, setNow] = useState(() => new Date());

  const load = useCallback(async () => {
    if (!bridge) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await bridge.analyze(LIMIT));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }, [bridge]);

  useEffect(() => {
    if (active) void load();
  }, [active, load, refreshKey]);

  useEffect(() => {
    if (!bridge || !active || launch) return;
    bridge
      .mcpConfig()
      .then((value) => {
        setLaunch(value);
        setLaunchError(null);
      })
      .catch((err) => setLaunchError(String(err)));
  }, [bridge, active, launch]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const localeTag = locale === "vi" ? "vi-VN" : "en-GB";
  const findings = useMemo(() => sortFindings(report?.findings ?? []), [report]);
  const counts = countBySeverity(findings);
  const age = dataAgeMs(report, now);
  const generated = report ? new Date(report.generated_at) : null;
  const remote = REMOTE_TARGET[client];
  const isRemote = remote !== undefined;
  const config = launch && !isRemote ? mcpConfigJson(launch, client) : "";
  const prompt = t("analysis.aiPrompt");

  const copy = async (target: string, text: string) => {
    setCopied({ target, ok: await copyText(text) });
    window.setTimeout(() => setCopied(null), 2500);
  };
  const copyLabel = (target: string, idle: MessageKey) =>
    copied?.target === target ? (copied.ok ? t("analysis.copied") : t("analysis.copyFailed")) : t(idle);

  const selectClient = (next: McpClient) => {
    setClient(next);
    setInstallNote(null);
    const target = REMOTE_TARGET[next];
    if (target) setRemoteTarget(target);
  };

  /** Merge only mcpServers.localsub-npu into Claude Desktop's config, after confirmation. */
  const installClaudeDesktop = async () => {
    if (!bridge || !launch) return;
    setInstalling(true);
    setInstallNote(null);
    let path = "claude_desktop_config.json";
    try {
      const file = await bridge.claudeDesktopConfig();
      path = file.path;
      let merged;
      try {
        merged = mergeClaudeDesktopConfig(file.text, launch);
      } catch (err) {
        const detail = String(err instanceof Error ? err.message : err).replace(/^INVALID_JSON: /, "");
        setInstallNote({ kind: "error", text: t("claude.invalidJson", { path, error: detail }) });
        return;
      }
      if (merged.unchanged) {
        setInstallNote({ kind: "ok", text: t("claude.unchanged", { path }) });
        return;
      }
      const ok = await bridge.confirm(
        t(merged.replaced ? "claude.confirmReplace" : "claude.confirmInstall", { path }),
        t("claude.confirmTitle"),
      );
      if (!ok) return;
      const result = await bridge.writeClaudeDesktopConfig(merged.text);
      setInstallNote({ kind: "ok", text: t("claude.installed", { path: result.path }), backup: result.backup });
    } catch (err) {
      setInstallNote({ kind: "error", text: t("claude.installFailed", { error: String(err) }) });
    } finally {
      setInstalling(false);
    }
  };

  return (
    <section className="panel analysis" aria-label={t("analysis.title")}>
      <div className="panel-title">
        <div>
          <h2>{t("analysis.title")}</h2>
          <p className="help-text">{t("analysis.intro")}</p>
          {generated && report && (
            <p className="help-text">
              {t("analysis.generated", {
                time: generated.toLocaleTimeString(localeTag, { hour12: false }),
                tz: offsetLabel(generated),
                jobs: report.jobs_analyzed,
              })}
              {report.newest_job_at
                ? ` · ${t("analysis.freshness", {
                    time: formatLocal(report.newest_job_at, localeTag),
                    age: age === null ? "—" : formatDuration(age),
                  })}`
                : ""}
            </p>
          )}
        </div>
        <button type="button" className="link" onClick={() => void load()} disabled={!bridge || busy}>
          {busy ? t("analysis.loading") : t("analysis.refresh")}
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {t("analysis.failed", { error })}
        </p>
      )}
      {!report && !error && <p className="help-text">{t("analysis.loading")}</p>}
      {report?.status === "no_data" && <p className="help-text">{t("analysis.empty")}</p>}
      {report && report.status !== "no_data" && (
        <>
          <div className="severity-summary" aria-label={t("analysis.summary")}>
            {SEVERITIES.map((s) => (
              <span key={s} className={`sev-pill ${s}${counts[s] ? "" : " zero"}`}>
                {t(`analysis.sev.${s}` as MessageKey)} · {counts[s]}
              </span>
            ))}
          </div>
          {findings.length === 0 && <p className="ok-text">{t("analysis.ok")}</p>}
          <ol className="findings">
            {findings.map((f) => (
              <li key={f.id} className={`finding ${f.severity}`}>
                <div className="finding-head">
                  <span className={`sev-pill ${f.severity}`}>{t(`analysis.sev.${f.severity}` as MessageKey)}</span>
                  <strong>{f.title[locale]}</strong>
                  <code className="finding-id">{f.id}</code>
                </div>
                <p>{f.detail[locale]}</p>
                <p className="finding-rec">
                  <span>{t("analysis.recommendation")}</span> {f.recommendation[locale]}
                </p>
                <details>
                  <summary>{t("analysis.evidence")}</summary>
                  {f.job_ids.length > 0 && (
                    <p className="help-text">
                      {t("analysis.jobs")}: {f.job_ids.join(", ")}
                    </p>
                  )}
                  <p className="help-text">
                    {t("analysis.code")}: {f.code_refs.join(", ")}
                  </p>
                  <pre className="evidence">{JSON.stringify(f.evidence, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ol>
          {Object.keys(report.stats.stages).length > 0 && (
            <div className="table-wrap">
              <table className="stage-table">
                <caption>{t("analysis.stages")}</caption>
                <thead>
                  <tr>
                    <th>{t("analysis.stage")}</th>
                    <th>{t("analysis.median")}</th>
                    <th>{t("analysis.max")}</th>
                  </tr>
                </thead>
                <tbody>
                  {STAGES.filter((s) => report.stats.stages[s]).map((s) => (
                    <tr key={s}>
                      <td>{t(`analysis.stage.${s}` as MessageKey)}</td>
                      <td className="num">{formatDuration(report.stats.stages[s].median_ms)}</td>
                      <td className="num">{formatDuration(report.stats.stages[s].max_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {report.skipped_lines > 0 && <p className="warn">{t("history.skipped", { n: report.skipped_lines })}</p>}
        </>
      )}

      <div className="mcp-box">
        <div className="panel-title">
          <div>
            <h3>{t("analysis.mcpTitle")}</h3>
            <p className="help-text">{t("analysis.mcpIntro")}</p>
            <p className="help-text">
              {t("analysis.mcpStatus", {
                transport: isRemote ? "Streamable HTTP" : "stdio",
                mode: launch ? (launch.bundled ? t("analysis.mcpBundled") : t("analysis.mcpSource")) : "—",
              })}
            </p>
          </div>
        </div>
        {launchError && <p className="error">{t("analysis.mcpFailed", { error: launchError })}</p>}
        {launch && (
          <>
            <div className="mcp-clients" role="radiogroup" aria-label={t("analysis.mcpClient")}>
              {MCP_CLIENTS.map((c) => (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={client === c}
                  className={client === c ? "on" : ""}
                  onClick={() => selectClient(c)}
                >
                  {t(`analysis.client.${c}` as MessageKey)}
                </button>
              ))}
            </div>
            {!isRemote && (
              <>
                <p className="help-text">{t(`analysis.where.${client}` as MessageKey)}</p>
                <pre className="mcp-config" aria-label={t("analysis.mcpConfig")} tabIndex={0}>
                  {config}
                </pre>
              </>
            )}
            {client === "claudecode" && (
              <div className="mcp-extra">
                <strong>{t("claudecode.settingsTitle")}</strong>
                <p className="help-text">{t("claudecode.settingsHelp")}</p>
                <pre className="mcp-config" tabIndex={0}>
                  {claudeCodeSettingsJson()}
                </pre>
              </div>
            )}
            {client === "claude" && installNote && (
              <div className={installNote.kind === "ok" ? "ok-text" : "error"} role="status">
                <p>{installNote.text}</p>
                {installNote.backup && <p className="help-text">{t("claude.backup", { path: installNote.backup })}</p>}
              </div>
            )}
          </>
        )}
        <RemoteConnect
          bridge={bridge}
          launch={launch}
          visible={isRemote}
          target={remote ?? remoteTarget}
          onCopy={(target, text) => void copy(target, text)}
          copyLabel={copyLabel}
        />
        {launch && (
          <div className="mcp-actions">
            {client === "claude" && (
              <button type="button" onClick={() => void installClaudeDesktop()} disabled={!bridge || installing}>
                {t("claude.install")}
              </button>
            )}
            {!isRemote && (
              <button
                type="button"
                className={client === "claude" ? "secondary" : undefined}
                onClick={() => void copy("config", config)}
              >
                {copyLabel("config", client === "claudecode" ? "analysis.copyCommand" : "analysis.copyConfig")}
              </button>
            )}
            {client === "claudecode" && (
              <button type="button" className="secondary" onClick={() => void copy("settings", claudeCodeSettingsJson())}>
                {copyLabel("settings", "claudecode.copySettings")}
              </button>
            )}
            <button type="button" className="secondary" onClick={() => void copy("prompt", prompt)}>
              {copyLabel("prompt", "analysis.copyPrompt")}
            </button>
          </div>
        )}
        {!isRemote && <p className="help-text mcp-privacy">{t("analysis.privacy")}</p>}
      </div>
    </section>
  );
}
