import { describe, expect, it } from "vitest";
import { deviceStatus, freshness, linePath, markerX, memoryPercent, mergeSamples, seriesStats } from "./metrics";
import type { MetricSample, MetricsSnapshot } from "./types";
import { previewMetrics } from "./worker";

const s = (at: number, cpu: number | null, gpu: number | null = null, npu: number | null = null): MetricSample => ({
  at_ms: at, cpu, gpu, npu, mem_used_bytes: 4, mem_total_bytes: 16,
});
const snap = (source: MetricsSnapshot["source"]): MetricsSnapshot => ({
  source, interval_ms: 1000, gpu_name: null, npu_name: null, error: null, samples: [],
});

describe("metrics helpers", () => {
  it("merges only newer samples in order and caps the buffer", () => {
    const merged = mergeSamples([s(1, 1), s(2, 2)], [s(2, 9), s(4, 4), s(3, 3)], 3);
    expect(merged.map((x) => x.at_ms)).toEqual([2, 3, 4]);
    expect(merged[0].cpu).toBe(2); // duplicate timestamp not overwritten
  });

  it("classifies freshness", () => {
    expect(freshness(null, 10)).toBe("none");
    expect(freshness(1000, 3000)).toBe("live");
    expect(freshness(1000, 7001)).toBe("stale");
  });

  it("reports per-device status", () => {
    const samples = [s(1, 10, 5, null), s(2, 12, 6, null)];
    expect(deviceStatus(snap("windows-pdh"), samples, "gpu")).toBe("available");
    expect(deviceStatus(snap("windows-pdh"), samples, "npu")).toBe("unsupported");
    expect(deviceStatus(snap("windows-pdh"), [], "cpu")).toBe("waiting");
    expect(deviceStatus(snap("unsupported"), samples, "cpu")).toBe("unsupported");
    expect(deviceStatus(null, samples, "cpu")).toBe("unsupported");
  });

  it("computes stats ignoring gaps", () => {
    expect(seriesStats([s(1, 10), s(2, null), s(3, 30)], "cpu")).toEqual({ current: 30, average: 20, peak: 30 });
    expect(seriesStats([], "gpu")).toEqual({ current: null, average: null, peak: null });
  });

  it("draws a clamped path over the window and breaks on gaps", () => {
    const d = linePath([s(0, 50), s(50, 150), s(60, null), s(100, 0)], "cpu", 100, 100, 100, 100);
    expect(d).toBe("M0.0,50.0 L50.0,0.0 M100.0,100.0");
    expect(linePath([s(0, 50)], "cpu", 1000, 100, 100, 100)).toBe("");
  });

  it("positions markers and memory percent", () => {
    expect(markerX(50, 100, 200, 100)).toBe(100);
    expect(markerX(-1, 100, 200, 100)).toBeNull();
    expect(memoryPercent(s(1, 1))).toBe(25);
    expect(memoryPercent(undefined)).toBeNull();
  });

  it("preview data is incremental and bounded", () => {
    const now = 1_000_000_000;
    const all = previewMetrics(null, now);
    expect(all.samples.length).toBe(181);
    const inc = previewMetrics(now - 3000, now);
    expect(inc.samples.map((x) => x.at_ms)).toEqual([now - 2000, now - 1000, now]);
    expect(all.samples.every((x) => (x.cpu ?? 0) >= 0 && (x.cpu ?? 0) <= 100)).toBe(true);
  });
});
