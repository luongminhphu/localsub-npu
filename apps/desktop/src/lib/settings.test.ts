import { describe, expect, it } from "vitest";
import { DEFAULT_RUN_SETTINGS, parseRunSettings } from "./settings";

describe("remembered run settings", () => {
  it("round-trips what was saved", () => {
    const saved = {
      language: "zh",
      model: "whisper-large-v3-turbo-int8",
      device: "GPU",
      translateTo: "",
      translationModel: "qwen3.5-4b-int4",
      translationDevice: "CPU",
      formats: ["vtt", "srt"],
    };
    expect(parseRunSettings(JSON.stringify(saved))).toEqual({ ...saved, formats: ["srt", "vtt"] });
  });

  it("falls back per field on missing or damaged values", () => {
    expect(parseRunSettings(null)).toEqual(DEFAULT_RUN_SETTINGS);
    expect(parseRunSettings("not json")).toEqual(DEFAULT_RUN_SETTINGS);
    expect(parseRunSettings("[1]")).toEqual(DEFAULT_RUN_SETTINGS);
    const bad = parseRunSettings(
      JSON.stringify({ language: "fr", model: "../evil", device: "TPU", translateTo: "en", formats: ["exe"] }),
    );
    expect(bad).toEqual({ ...DEFAULT_RUN_SETTINGS, translateTo: "en" });
  });
});
