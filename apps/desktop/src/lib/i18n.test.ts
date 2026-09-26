import { describe, expect, it } from "vitest";
import { errorMessage, initialLocale, MESSAGES, translate, type MessageKey } from "./i18n";

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("i18n", () => {
  it("has the same keys and placeholders in vi and en", () => {
    const vi = Object.keys(MESSAGES.vi).sort();
    expect(Object.keys(MESSAGES.en).sort()).toEqual(vi);
    for (const key of vi as MessageKey[]) {
      expect(MESSAGES.vi[key].trim(), key).not.toBe("");
      expect(placeholders(MESSAGES.en[key]), key).toEqual(placeholders(MESSAGES.vi[key]));
    }
  });

  it("interpolates and keeps unknown placeholders visible", () => {
    expect(translate("vi", "models.removed", { model: "whisper-small-int8" })).toBe(
      "Đã xóa whisper-small-int8.",
    );
    expect(translate("en", "job.log", { n: 3 })).toBe("Worker log (3)");
    expect(translate("en", "models.removed")).toBe("Removed {model}.");
  });

  it("defaults to Vietnamese and honours a saved choice", () => {
    expect(initialLocale(null)).toBe("vi");
    expect(initialLocale({ getItem: () => "en" })).toBe("en");
    expect(initialLocale({ getItem: () => "fr" })).toBe("vi");
  });

  it("maps worker error codes to friendly messages", () => {
    const t = (k: MessageKey, p?: Record<string, string | number>) => translate("vi", k, p);
    expect(errorMessage(t, "MODEL_INCOMPLETE: half done")).toContain("Tải tiếp");
    expect(
      errorMessage(t, '{"type":"error","error_code":"INSUFFICIENT_DISK","error_message":"need 5 GB"}'),
    ).toContain("need 5 GB");
    expect(errorMessage(t, "boom")).toBe("Lỗi: boom");
    expect(errorMessage(t, null)).toBe("");
  });
});
