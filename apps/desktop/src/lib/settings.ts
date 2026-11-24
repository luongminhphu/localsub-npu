import { SPOKEN_LANGUAGES, type DevicePreference, type ExportFormat, type SpokenLanguage, type TargetLanguage } from "./types";

/** Run settings remembered between launches (keep-awake and auto-save have their own keys). */
export interface SavedRunSettings {
  language: SpokenLanguage;
  model: string;
  device: DevicePreference;
  translateTo: TargetLanguage;
  translationModel: string;
  translationDevice: DevicePreference;
  formats: ExportFormat[];
}

export const RUN_SETTINGS_KEY = "localsub.runSettings";

export const DEFAULT_RUN_SETTINGS: SavedRunSettings = {
  language: "auto",
  model: "whisper-small-int8",
  device: "auto",
  translateTo: "vi",
  translationModel: "qwen3-8b-int4-cw",
  translationDevice: "auto",
  formats: ["srt", "json"],
};

const DEVICES: readonly string[] = ["auto", "NPU", "GPU", "CPU"];
const TARGETS: readonly string[] = ["", "en", "vi"];
const FORMATS: readonly string[] = ["srt", "vtt", "txt", "json"];
const MODEL_ID = /^[a-z0-9][a-z0-9.-]{0,63}$/i; // same rule the Tauri side enforces

/** Parse what was saved; each unknown or damaged field falls back to its default. */
export function parseRunSettings(raw: string | null | undefined): SavedRunSettings {
  let saved: Record<string, unknown> = {};
  try {
    const value: unknown = JSON.parse(raw ?? "{}");
    if (value && typeof value === "object" && !Array.isArray(value)) saved = value as Record<string, unknown>;
  } catch {
    // damaged storage: start from the defaults
  }
  const d = DEFAULT_RUN_SETTINGS;
  const pick = <T,>(v: unknown, allowed: readonly string[], fallback: T): T =>
    typeof v === "string" && allowed.includes(v) ? (v as T) : fallback;
  const id = (v: unknown, fallback: string) => (typeof v === "string" && MODEL_ID.test(v) ? v : fallback);
  const formats = Array.isArray(saved.formats)
    ? FORMATS.filter((f) => (saved.formats as unknown[]).includes(f)) as ExportFormat[]
    : [];
  return {
    language: pick(saved.language, SPOKEN_LANGUAGES, d.language),
    model: id(saved.model, d.model),
    device: pick(saved.device, DEVICES, d.device),
    translateTo: pick(saved.translateTo, TARGETS, d.translateTo),
    translationModel: id(saved.translationModel, d.translationModel),
    translationDevice: pick(saved.translationDevice, DEVICES, d.translationDevice),
    formats: formats.length ? formats : d.formats,
  };
}
