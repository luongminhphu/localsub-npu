import { useCallback, useEffect, useState } from "react";
import { formatDuration, formatLocal, isStale, offsetLabel, parallelText } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";
import type { JobHistory } from "../lib/types";
import type { WorkerBridge } from "../lib/worker";

const LIMIT = 10;


export function HistoryPanel(props: { bridge: WorkerBridge | null; refreshKey: number; onOpen?: (path: string) => void }) {
  const { bridge, refreshKey, onOpen } = props;
  const { t, locale } = useI18n();
  const [data, setData] = useState<JobHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [now, setNow] = useState(() => new Date());

  const load = useCallback(async () => {
    if (!bridge) return;
    setBusy(true);
    setError(null);
    try {
      setData(await bridge.history(LIMIT));
      setFetchedAt(new Date());
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }, [bridge]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const localeTag = locale === "vi" ? "vi-VN" : "en-GB";
  const stale = isStale(fetchedAt, now);
  const stats = data?.stats;
  const stateKey = (s: string | null): MessageKey | null =>
    s === "completed" || s === "failed" || s === "cancelled" ? (`history.state.${s}` as MessageKey) : null;

  return (
    <section className="panel history" aria-label={t("history.title")}>
      <div className="panel-title">
        <div>
          <h2>{t("history.title")}</h2>
          {fetchedAt && (
            <p className={`help-text${stale ? " stale" : ""}`}>
              {t("history.updated", {
                time: fetchedAt.toLocaleTimeString(localeTag, { hour12: false }),
                tz: offsetLabel(fetchedAt),
              })}
              {stale ? ` · ${t("history.stale")}` : ""}
            </p>
          )}
        </div>
        <button type="button" className="link" onClick={() => void load()} disabled={!bridge || busy}>
          {busy ? t("history.loading") : t("history.refresh")}
        </button>
      </div>
      {error && <p className="error">{t("history.failed", { error })}</p>}
      {!data && !error && <p className="help-text">{t("history.loading")}</p>}
      {data && data.jobs.length === 0 && <p className="help-text">{t("history.empty")}</p>}
      {data && data.jobs.length > 0 && stats && (
        <>
          <p className="stats">
            {t("history.stats", {
              completed: stats.completed,
              total: stats.total,
              npu: stats.npu_share === null ? "—" : `${Math.round(stats.npu_share * 100)}%`,
              rtf: stats.median_rtf ?? "—",
              fallbacks: stats.fallbacks,
            })}
          </p>
          <div className="table-wrap">
            <table className="history-table">
              <thead>
                <tr>
                  <th>{t("history.col.time")} ({offsetLabel(now)})</th>
                  <th>{t("history.col.file")}</th>
                  <th>{t("history.col.lang")}</th>
                  <th>{t("history.col.device")}</th>
                  <th>{t("history.col.duration")}</th>
                  <th title={t("history.rtfHelp")}>{t("history.col.rtf")}</th>
                  <th>{t("history.col.state")}</th>
                </tr>
              </thead>
              <tbody>
                {data.jobs.map((j, i) => {
                  const key = stateKey(j.state);
                  const lang = `${j.detected_language ?? j.language ?? "?"}${j.translate_to ? ` → ${j.translate_to}` : ""}`;
                  const dev = j.device?.resolved ?? "—";
                  const tdev = j.translate_to ? ` / ${j.translation_device?.resolved ?? "—"}` : "";
                  const fallback = j.device?.fallback_reason ?? j.translation_device?.fallback_reason;
                  return (
                    <tr key={j.job_id ?? i}>
                      <td className="num">{formatLocal(j.started_at, localeTag)}</td>
                      <td className="file" title={j.source_name ?? ""}>{j.source_name ?? "—"}</td>
                      <td>{lang}</td>
                      <td title={fallback ?? undefined}>{dev}{tdev}{fallback ? " *" : ""}</td>
                      <td className="num">{formatDuration(j.source_duration_ms)}</td>
                      <td
                        className="num"
                        title={t("history.breakdown", {
                          asr: formatDuration(j.asr_ms),
                          translation: j.translate_to ? formatDuration(j.translation_ms) : "—",
                          total: formatDuration(j.total_ms),
                        }) + (j.overlap && j.translation_after_asr_ms != null ? ` · ${t("history.overlap", { after: formatDuration(j.translation_after_asr_ms) })}` : "") + (parallelText(j.translation_parallel) ? ` · ${t("history.parallel", { n: parallelText(j.translation_parallel) })}` : "")}
                      >
                        {j.total_rtf ?? j.real_time_factor ?? "—"}
                        {j.total_ms ? (
                          <span className="sub-num">
                            {j.asr_ms
                              ? t("history.breakdownShort", {
                                  asr: formatDuration(j.asr_ms),
                                  translation: j.translate_to ? formatDuration(j.translation_ms) : "—",
                                }) + (j.overlap ? ` · ${t("history.overlapShort")}` : "")
                              : formatDuration(j.total_ms)}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <span className={`state-pill ${j.state ?? ""}`} title={j.error_code ?? undefined}>
                          {key ? t(key) : (j.state ?? "—")}
                        </span>
                        {j.error_code && <span className="issue error">{j.error_code}</span>}
                        {j.project_path && onOpen && (
                          <button type="button" className="link" onClick={() => onOpen(j.project_path as string)}>
                            {t("queue.openFile")}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {data.skipped_lines > 0 && <p className="warn">{t("history.skipped", { n: data.skipped_lines })}</p>}
        </>
      )}
    </section>
  );
}
