/** Pure helpers for the CPU / GPU / NPU monitor: buffering, freshness, status and charts. */

import type { MetricKey, MetricSample, MetricsSnapshot } from "./types";

export const WINDOW_MS = 5 * 60_000; // chart shows the last 5 minutes
export const MAX_SAMPLES = 300;
export const STALE_AFTER_MS = 5_000; // > 5 missed 1 s samples

export type Freshness = "live" | "stale" | "none";
export type DeviceStatus = "available" | "unsupported" | "waiting";

/** Append samples newer than the buffer's last one, keep order, cap the length. */
export function mergeSamples(buffer: MetricSample[], incoming: MetricSample[], max = MAX_SAMPLES): MetricSample[] {
  const last = buffer.length ? buffer[buffer.length - 1].at_ms : -Infinity;
  const fresh = incoming.filter((s) => Number.isFinite(s.at_ms) && s.at_ms > last).sort((a, b) => a.at_ms - b.at_ms);
  const merged = fresh.length ? [...buffer, ...fresh] : buffer;
  return merged.length > max ? merged.slice(merged.length - max) : merged;
}

export function freshness(lastAt: number | null, now: number, staleAfter = STALE_AFTER_MS): Freshness {
  if (lastAt === null) return "none";
  return now - lastAt > staleAfter ? "stale" : "live";
}

/** A device is unsupported when counters are unavailable or the last samples never had it. */
export function deviceStatus(snapshot: MetricsSnapshot | null, samples: MetricSample[], key: MetricKey): DeviceStatus {
  if (!snapshot || snapshot.source === "unsupported" || snapshot.source === "error") return "unsupported";
  if (!samples.length) return "waiting";
  return samples.slice(-5).some((s) => typeof s[key] === "number") ? "available" : "unsupported";
}

export interface SeriesStats {
  current: number | null;
  average: number | null;
  peak: number | null;
}

export function seriesStats(samples: MetricSample[], key: MetricKey): SeriesStats {
  const values = samples.map((s) => s[key]).filter((v): v is number => typeof v === "number");
  if (!values.length) return { current: null, average: null, peak: null };
  const sum = values.reduce((a, b) => a + b, 0);
  return {
    current: values[values.length - 1],
    average: Math.round((sum / values.length) * 10) / 10,
    peak: Math.max(...values),
  };
}

/** SVG path over a fixed time window ending at `end`; gaps (null) break the line. */
export function linePath(
  samples: MetricSample[],
  key: MetricKey,
  end: number,
  width: number,
  height: number,
  windowMs = WINDOW_MS,
): string {
  const start = end - windowMs;
  let path = "";
  let pen = false;
  for (const s of samples) {
    const v = s[key];
    if (s.at_ms < start || typeof v !== "number") {
      pen = false;
      continue;
    }
    const x = ((s.at_ms - start) / windowMs) * width;
    const y = height - (Math.min(100, Math.max(0, v)) / 100) * height;
    path += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)} `;
    pen = true;
  }
  return path.trim();
}

/** x position (0..width) of a timestamp inside the window, or null when outside it. */
export function markerX(at: number, end: number, width: number, windowMs = WINDOW_MS): number | null {
  const start = end - windowMs;
  if (at < start || at > end) return null;
  return ((at - start) / windowMs) * width;
}

export function memoryPercent(sample: MetricSample | undefined): number | null {
  if (!sample?.mem_total_bytes || sample.mem_used_bytes === null || sample.mem_used_bytes === undefined) return null;
  return Math.round((sample.mem_used_bytes / sample.mem_total_bytes) * 1000) / 10;
}
