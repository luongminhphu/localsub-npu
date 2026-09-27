import { useCallback, useEffect, useRef, useState } from "react";
import { formatBytes, offsetLabel } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";
import {
  WINDOW_MS,
  deviceStatus,
  freshness,
  linePath,
  markerX,
  memoryPercent,
  mergeSamples,
  seriesStats,
} from "../lib/metrics";
import type { MetricKey, MetricSample, MetricsSnapshot } from "../lib/types";
import type { WorkerBridge } from "../lib/worker";

export interface StageMarker {
  at: number;
  stage: string;
}

const SERIES: Array<{ key: MetricKey; label: string; color: string }> = [
  { key: "cpu", label: "CPU", color: "#60a5fa" },
  { key: "gpu", label: "GPU", color: "#f59e0b" },
  { key: "npu", label: "NPU", color: "#34d399" },
];
const W = 600;
const H = 160;
const POLL_MS = 1_000;

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v)}%`);

export function MonitorPanel(props: { bridge: WorkerBridge | null; markers: StageMarker[] }) {
  const { bridge, markers } = props;
  const { t, locale } = useI18n();
  const [snapshot, setSnapshot] = useState<MetricsSnapshot | null>(null);
  const [samples, setSamples] = useState<MetricSample[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const lastAt = useRef<number | null>(null);

  const poll = useCallback(async () => {
    if (!bridge) return;
    try {
      const snap = await bridge.systemMetrics(lastAt.current);
      setSnapshot(snap);
      setError(null);
      setSamples((cur) => {
        const merged = mergeSamples(cur, snap.samples);
        lastAt.current = merged.length ? merged[merged.length - 1].at_ms : null;
        return merged;
      });
    } catch (err) {
      setError(String(err));
    }
  }, [bridge]);

  useEffect(() => {
    if (!bridge || paused) return;
    let timer = 0;
    let cancelled = false;
    const tick = async () => {
      if (document.visibilityState === "visible") await poll();
      setNow(Date.now());
      if (!cancelled) timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [bridge, paused, poll]);

  const last = samples[samples.length - 1];
  const fresh = freshness(last?.at_ms ?? null, now);
  const end = paused && last ? last.at_ms : now;
  const localeTag = locale === "vi" ? "vi-VN" : "en-GB";
  const source = snapshot?.source ?? null;
  const unavailable = source === "unsupported" || source === "error";
  const stats = Object.fromEntries(SERIES.map((s) => [s.key, seriesStats(samples, s.key)])) as Record<
    MetricKey,
    ReturnType<typeof seriesStats>
  >;
  const names: Record<MetricKey, string | null> = {
    cpu: null,
    gpu: snapshot?.gpu_name ?? null,
    npu: snapshot?.npu_name ?? null,
  };
  const mem = memoryPercent(last);
  const visibleMarkers = markers
    .map((m) => ({ ...m, x: markerX(m.at, end, W) }))
    .filter((m): m is StageMarker & { x: number } => m.x !== null);

  return (
    <section className="panel monitor" aria-label={t("monitor.title")}>
      <div className="panel-title">
        <div>
          <h2>{t("monitor.title")}</h2>
          <p className="help-text">{t("monitor.subtitle", { interval: (snapshot?.interval_ms ?? POLL_MS) / 1000 })}</p>
        </div>
        <button type="button" className="link" onClick={() => setPaused((p) => !p)} disabled={!bridge || unavailable}>
          {paused ? t("monitor.resume") : t("monitor.pause")}
        </button>
      </div>

      <p className={`help-text monitor-status ${fresh}`} role="status" aria-live="polite">
        {last ? (
          <>
            <span className={`dot ${paused ? "stale" : fresh}`} aria-hidden="true" />
            <span>
            {t("monitor.updated", {
              time: new Date(last.at_ms).toLocaleTimeString(localeTag, { hour12: false }),
              tz: offsetLabel(new Date(last.at_ms)),
            })}
            {" · "}
            {paused ? t("monitor.pause") : fresh === "live" ? t("monitor.live") : t("monitor.stale", { age: Math.round((now - last.at_ms) / 1000) })}
            {" · "}
            {source === "preview" ? t("monitor.preview") : t("monitor.source.pdh")}
            </span>
          </>
        ) : !snapshot && !error ? (
          t("monitor.loading")
        ) : source === "unsupported" ? (
          t("monitor.unsupported")
        ) : source === "error" ? (
          t("monitor.error", { error: snapshot?.error ?? "?" })
        ) : (
          t("monitor.waiting")
        )}
      </p>
      {error && <p className="error">{t("monitor.fetchFailed", { error })}</p>}
      {snapshot?.error && source === "windows-pdh" && <p className="warn">{snapshot.error}</p>}

      {!unavailable && (
        <>
          <div className="monitor-legend">
            {SERIES.map((s) => {
              const status = deviceStatus(snapshot, samples, s.key);
              const st = stats[s.key];
              return (
                <div key={s.key} className={`metric-card ${status}`}>
                  <div className="metric-head">
                    <span className="swatch" style={{ background: s.color }} aria-hidden="true" />
                    <strong>{s.label}</strong>
                    <span className="metric-state">{t(`monitor.status.${status}` as MessageKey)}</span>
                  </div>
                  <div className="metric-value">{status === "available" ? pct(st.current) : "—"}</div>
                  <div className="metric-sub" title={names[s.key] ?? undefined}>
                    {status === "available"
                      ? t("monitor.stats", { average: pct(st.average), peak: pct(st.peak) })
                      : (names[s.key] ?? "—")}
                  </div>
                  {names[s.key] && status === "available" && <div className="metric-name">{names[s.key]}</div>}
                </div>
              );
            })}
            <div className="metric-card memory">
              <div className="metric-head">
                <strong>RAM</strong>
              </div>
              <div className="metric-value">{mem === null ? "—" : `${Math.round(mem)}%`}</div>
              <div className="metric-sub">
                {last?.mem_total_bytes
                  ? t("monitor.memory", {
                      used: formatBytes(last.mem_used_bytes),
                      total: formatBytes(last.mem_total_bytes),
                      pct: mem === null ? "—" : `${mem}%`,
                    })
                  : "—"}
              </div>
            </div>
          </div>

          <div className="chart-wrap">
            <div className="chart-y" aria-hidden="true">
              <span>100%</span>
              <span>50%</span>
              <span>0%</span>
            </div>
            <div className="chart-area">
              <svg
                viewBox={`0 0 ${W} ${H}`}
                preserveAspectRatio="none"
                role="img"
                aria-label={t("monitor.chartLabel", {
                  cpu: pct(stats.cpu.current),
                  gpu: pct(stats.gpu.current),
                  npu: pct(stats.npu.current),
                })}
              >
                {[0, 25, 50, 75, 100].map((g) => (
                  <line key={g} x1={0} x2={W} y1={H - (g / 100) * H} y2={H - (g / 100) * H} className="grid" />
                ))}
                {visibleMarkers.map((m, i) => (
                  <line key={`m${i}`} x1={m.x} x2={m.x} y1={0} y2={H} className="marker" />
                ))}
                {SERIES.map((s) => (
                  <path key={s.key} d={linePath(samples, s.key, end, W, H)} stroke={s.color} className="series" />
                ))}
              </svg>
              {visibleMarkers.map((m, i) => {
                // Skip a label that would collide with the next one; keep the latest.
                const next = visibleMarkers[i + 1];
                if (next && next.x - m.x < W * 0.12) return null;
                const right = m.x > W * 0.85;
                return (
                  <span
                    key={`l${i}`}
                    className={`marker-label${right ? " right" : ""}`}
                    style={{ left: `${(m.x / W) * 100}%` }}
                  >
                    {t(`steps.${m.stage}` as MessageKey)}
                  </span>
                );
              })}
              {!samples.length && <div className="chart-empty">{t("monitor.waiting")}</div>}
            </div>
          </div>
          <div className="chart-x" aria-hidden="true">
            <span>-{WINDOW_MS / 60_000}m</span>
            <span>-{WINDOW_MS / 120_000}m</span>
            <span>{new Date(end).toLocaleTimeString(localeTag, { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
          </div>
          {visibleMarkers.length > 0 && <p className="help-text">{t("monitor.markers")}</p>}
        </>
      )}
    </section>
  );
}
