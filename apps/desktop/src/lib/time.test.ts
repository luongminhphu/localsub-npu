import { describe, expect, it } from "vitest";
import { formatTime, parseTime } from "./time";

describe("time", () => {
  it("formats milliseconds", () => {
    expect(formatTime(0)).toBe("00:00:00.000");
    expect(formatTime(3_723_004)).toBe("01:02:03.004");
    expect(formatTime(-5)).toBe("00:00:00.000");
  });

  it("parses full, short and comma forms", () => {
    expect(parseTime("01:02:03.004")).toBe(3_723_004);
    expect(parseTime("00:00:05,923")).toBe(5_923);
    expect(parseTime("2:05.5")).toBe(125_500);
    expect(parseTime("7")).toBe(7_000);
  });

  it("rejects invalid input", () => {
    expect(parseTime("")).toBeNull();
    expect(parseTime("abc")).toBeNull();
    expect(parseTime("00:61:00")).toBeNull();
  });

  it("round-trips", () => {
    for (const ms of [0, 999, 61_001, 5_400_250]) expect(parseTime(formatTime(ms))).toBe(ms);
  });
});
