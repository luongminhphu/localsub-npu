import type { DevicePreference, ModelInfo, SpokenLanguage } from "./types";

/** One entry of a model picker. Built from `models list` (the worker's catalog). */
export interface ModelChoice {
  id: string;
  kind: "asr" | "translation";
  label: string;
  approxMb: number | null;
  hint: { vi: string; en: string } | null;
  devices: string[];
  languages: string[] | null;
  experimental: boolean;
}

const ALL = ["NPU", "GPU", "CPU"];

/** Used until the worker answers (or in an old worker without catalog metadata). */
export const FALLBACK_MODELS: ModelChoice[] = [
  ["whisper-small-int8", "asr", "Whisper small", 250],
  ["whisper-base-int8", "asr", "Whisper base", 120],
  ["whisper-tiny-int8", "asr", "Whisper tiny", 70],
  ["whisper-medium-int8", "asr", "Whisper medium", 800],
  ["whisper-large-v3-turbo-int8", "asr", "Whisper large-v3 turbo", 850],
  ["whisper-large-v3-int8", "asr", "Whisper large-v3", 1600],
  ["qwen3-8b-int4-cw", "translation", "Qwen3 8B", 5000],
  ["qwen2.5-7b-instruct-int4", "translation", "Qwen2.5 7B", 4500],
  ["qwen3-4b-int4", "translation", "Qwen3 4B", 2300],
  ["qwen2.5-1.5b-instruct-int4", "translation", "Qwen2.5 1.5B", 1000],
  ["qwen3-0.6b-int4", "translation", "Qwen3 0.6B", 500],
].map(([id, kind, label, mb]) => ({
  id: id as string,
  kind: kind as ModelChoice["kind"],
  label: label as string,
  approxMb: mb as number,
  hint: null,
  devices: ALL,
  languages: null,
  experimental: false,
}));

export function choicesFrom(models: ModelInfo[]): ModelChoice[] {
  const rich = models.filter((m) => typeof m.label === "string" && m.label);
  if (!rich.length) return FALLBACK_MODELS;
  return rich.map((m) => ({
    id: m.id,
    kind: m.kind,
    label: m.label as string,
    approxMb: m.approx_size_mb,
    hint: m.hint ?? null,
    devices: m.devices?.length ? m.devices : ALL,
    languages: m.languages ?? null,
    experimental: Boolean(m.experimental),
  }));
}

export function findChoice(choices: ModelChoice[], id: string): ModelChoice | undefined {
  return choices.find((c) => c.id === id);
}

/** Single-language ASR (Distil-Whisper EN) needs that language chosen explicitly. */
export function languageSupported(choice: ModelChoice | undefined, language: SpokenLanguage): boolean {
  if (!choice?.languages?.length) return true;
  return choice.languages.includes(language);
}

export function deviceSupported(choice: ModelChoice | undefined, device: DevicePreference): boolean {
  return device === "auto" || !choice || choice.devices.includes(device);
}
