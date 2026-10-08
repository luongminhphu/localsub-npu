import { describe, expect, it } from "vitest";
import { choicesFrom, deviceSupported, FALLBACK_MODELS, findChoice, languageSupported } from "./catalog";
import { initialJob, jobReducer } from "./cues";
import type { ModelInfo } from "./types";

const base = { source: "", status: "missing", installed: false, size_bytes: 0, path: "" } as const;
const list: ModelInfo[] = [
  { ...base, id: "whisper-small-int8", kind: "asr", approx_size_mb: 250, label: "Whisper small", hint: { vi: "Mặc định", en: "Default" }, devices: ["NPU", "GPU", "CPU"], languages: null, experimental: false },
  { ...base, id: "distil-whisper-large-v3-int8", kind: "asr", approx_size_mb: 780, label: "Distil-Whisper large-v3 (EN)", devices: ["NPU", "GPU", "CPU"], languages: ["en"], experimental: true },
  { ...base, id: "qwen3.5-4b-int4", kind: "translation", approx_size_mb: 3600, label: "Qwen3.5 4B", runtime: "vlm", devices: ["GPU", "CPU"], languages: null, experimental: true },
];

describe("model catalog", () => {
  it("builds pickers from the worker list, new models included", () => {
    const choices = choicesFrom(list);
    expect(choices.map((c) => c.id)).toEqual(list.map((m) => m.id));
    expect(findChoice(choices, "qwen3.5-4b-int4")?.experimental).toBe(true);
    expect(findChoice(choices, "whisper-small-int8")?.hint?.vi).toBe("Mặc định");
  });

  it("falls back to the built-in list for an old worker without metadata", () => {
    const old = list.map((m) => ({ ...m, label: undefined }));
    expect(choicesFrom(old)).toBe(FALLBACK_MODELS);
    expect(choicesFrom([])).toBe(FALLBACK_MODELS);
  });

  it("checks single-language ASR and device limits", () => {
    const choices = choicesFrom(list);
    const distil = findChoice(choices, "distil-whisper-large-v3-int8");
    expect(languageSupported(distil, "en")).toBe(true);
    expect(languageSupported(distil, "auto")).toBe(false);
    expect(languageSupported(distil, "ja")).toBe(false);
    expect(languageSupported(findChoice(choices, "whisper-small-int8"), "ja")).toBe(true);
    const q35 = findChoice(choices, "qwen3.5-4b-int4");
    expect(deviceSupported(q35, "NPU")).toBe(false);
    expect(deviceSupported(q35, "GPU")).toBe(true);
    expect(deviceSupported(q35, "auto")).toBe(true);
    expect(deviceSupported(undefined, "NPU")).toBe(true);
  });
});

describe("power event", () => {
  it("records the keep-awake state for the job", () => {
    const running = jobReducer(initialJob, { type: "start" });
    const next = jobReducer(running, {
      type: "event",
      event: { type: "power", keep_awake: true, display_on: false, throttling_disabled: true, reason: null },
    });
    expect(next.power).toEqual({ keep_awake: true, display_on: false, throttling_disabled: true, reason: null });
    expect(jobReducer(next, { type: "start" }).power).toBeNull();
  });
});
