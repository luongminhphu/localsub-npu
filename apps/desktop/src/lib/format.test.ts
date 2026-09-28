import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, formatLocal, estimateRemainingMs, isStale, splitText, offsetLabel, percent } from "./format";

describe("format", () => {
  it("formats byte sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(250 * 1_048_576)).toBe("250 MB");
    expect(formatBytes(2.3 * 1024 ** 3)).toBe("2.3 GB");
    expect(formatBytes(70 * 1_048_576)).toBe("70 MB");
    expect(formatBytes(1000 * 1_048_576)).toBe("1.0 GB");
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });

  it("computes clamped percentages", () => {
    expect(percent(50, 200)).toBe(25);
    expect(percent(300, 200)).toBe(100); // hf metadata can undercount by a few bytes
    expect(percent(10, null)).toBeNull();
    expect(percent(10, 0)).toBeNull();
  });

  it("formats durations", () => {
    expect(formatDuration(59_000)).toBe("59s");
    expect(formatDuration(98_400)).toBe("1m38s");
    expect(formatDuration(3_720_000)).toBe("1h02m");
    expect(formatDuration(null)).toBe("—");
  });

  it("formats local timestamps and rejects bad input", () => {
    expect(formatLocal("not a date")).toBe("—");
    expect(formatLocal(null)).toBe("—");
    expect(formatLocal("2026-09-26T16:16:41+00:00")).toMatch(/\d{2}/);
  });

  it("labels the UTC offset", () => {
    expect(offsetLabel(new Date())).toMatch(/^GMT[+-]\d{1,2}(:\d{2})?$/);
  });

  it("detects stale data", () => {
    const now = new Date("2026-09-26T10:00:00Z");
    expect(isStale(null, now)).toBe(true);
    expect(isStale(new Date("2026-09-26T09:59:00Z"), now)).toBe(false);
    expect(isStale(new Date("2026-09-26T09:57:00Z"), now)).toBe(true);
  });
});

describe("estimateRemainingMs", () => {
  it("extrapolates linearly once there is signal", () => {
    expect(estimateRemainingMs(25, 100, 10_000)).toBe(30_000);
    expect(estimateRemainingMs(0, 100, 10_000)).toBeNull();
    expect(estimateRemainingMs(50, 100, 1_000)).toBeNull();
    expect(estimateRemainingMs(100, 100, 10_000)).toBeNull();
  });
});

describe("splitText", () => {
  it("shows the per-device split only for hybrid runs", () => {
    expect(splitText({ GPU: 900, NPU: 300 })).toBe(" · GPU 900 · NPU 300");
    expect(splitText({ GPU: 900 })).toBe("");
    expect(splitText({ GPU: 900, NPU: 0 })).toBe("");
    expect(splitText(null)).toBe("");
  });
});
