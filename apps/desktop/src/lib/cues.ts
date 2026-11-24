import type { JobView, LocalSubProject, ProjectCue, WorkerEvent } from "./types";

export const MAX_CUE_CHARS = 84;
/** Japanese: ~2 lines x 16 full-width characters (mirrors the worker's segmentation). */
export const MAX_CUE_CHARS_CJK = 32;
/** Korean: word-separated but dense Hangul, ~2 lines x 16 characters plus spaces. */
export const MAX_CUE_CHARS_KO = 36;

const CJK = /[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]/g;

export function isCjk(text: string): boolean {
  const dense = text.replace(/ /g, "");
  return dense.length > 0 && (dense.match(CJK)?.length ?? 0) >= 0.3 * dense.length;
}

const HANGUL = /[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]/g;

export function isHangul(text: string): boolean {
  const dense = text.replace(/ /g, "");
  return dense.length > 0 && (dense.match(HANGUL)?.length ?? 0) >= 0.3 * dense.length;
}

export function maxCharsFor(text: string): number {
  if (isCjk(text)) return MAX_CUE_CHARS_CJK;
  return isHangul(text) ? MAX_CUE_CHARS_KO : MAX_CUE_CHARS;
}

export interface CueIssue {
  index: number;
  message: string;
  severity: "error" | "warning";
  code: "timing" | "empty" | "overlap" | "too_long";
  limit?: number;
}

/** Mirrors the worker's validate_cues so export errors are caught before saving. */
export function validateCues(cues: ProjectCue[]): CueIssue[] {
  const issues: CueIssue[] = [];
  cues.forEach((cue, index) => {
    if (!(cue.end_ms > cue.start_ms) || cue.start_ms < 0) {
      issues.push({ index, severity: "error", code: "timing", message: "End must be after start" });
    }
    if (!cue.text.trim()) {
      issues.push({ index, severity: "error", code: "empty", message: "Source text is empty" });
    }
    const prev = cues[index - 1];
    if (prev && cue.start_ms < prev.end_ms) {
      issues.push({ index, severity: "error", code: "overlap", message: "Overlaps the previous cue" });
    }
    for (const value of [cue.text, cue.translation ?? ""]) {
      const limit = maxCharsFor(value);
      if (value.length > limit) {
        issues.push({
          index,
          severity: "warning",
          code: "too_long",
          limit,
          message: `Longer than ${limit} characters`,
        });
        break;
      }
    }
  });
  return issues;
}

export function updateCue(
  project: LocalSubProject,
  index: number,
  patch: Partial<ProjectCue>,
): LocalSubProject {
  return {
    ...project,
    cues: project.cues.map((cue, i) => (i === index ? { ...cue, ...patch } : cue)),
  };
}

/** Split a cue at its midpoint; the new cue gets empty text for the user to fill in. */
export function splitCue(project: LocalSubProject, index: number): LocalSubProject {
  const cue = project.cues[index];
  if (!cue || cue.end_ms - cue.start_ms < 400) return project;
  const mid = Math.round((cue.start_ms + cue.end_ms) / 2);
  const first = { ...cue, end_ms: mid };
  const second: ProjectCue = {
    start_ms: mid,
    end_ms: cue.end_ms,
    text: "…",
    translation: cue.translation === null ? null : "…",
  };
  const cues = [...project.cues];
  cues.splice(index, 1, first, second);
  return { ...project, cues };
}

export function mergeWithNext(project: LocalSubProject, index: number): LocalSubProject {
  const cue = project.cues[index];
  const next = project.cues[index + 1];
  if (!cue || !next) return project;
  const join = (a: string | null, b: string | null) =>
    a === null && b === null ? null : [a, b].filter(Boolean).join(" ");
  const merged: ProjectCue = {
    start_ms: cue.start_ms,
    end_ms: next.end_ms,
    text: `${cue.text} ${next.text}`.trim(),
    translation: join(cue.translation, next.translation),
  };
  const cues = [...project.cues];
  cues.splice(index, 2, merged);
  return { ...project, cues };
}

export function deleteCue(project: LocalSubProject, index: number): LocalSubProject {
  return { ...project, cues: project.cues.filter((_, i) => i !== index) };
}

export function isProject(value: unknown): value is LocalSubProject {
  if (!value || typeof value !== "object") return false;
  const cues = (value as { cues?: unknown }).cues;
  return (
    Array.isArray(cues) &&
    cues.every(
      (c) =>
        c &&
        typeof c === "object" &&
        typeof (c as ProjectCue).start_ms === "number" &&
        typeof (c as ProjectCue).end_ms === "number" &&
        typeof (c as ProjectCue).text === "string",
    )
  );
}

export function normalizeProject(value: LocalSubProject): LocalSubProject {
  return {
    schema: "localsub.project/v1",
    source_media: value.source_media ?? null,
    source_language: value.source_language ?? null,
    target_language: value.target_language ?? null,
    cues: value.cues.map((c) => ({
      start_ms: c.start_ms,
      end_ms: c.end_ms,
      text: c.text,
      translation: c.translation ?? null,
    })),
  };
}

export const initialJob: JobView = {
  status: "idle",
  stage: null,
  asrDevice: null,
  asrFallback: null,
  translationDevice: null,
  translationFallback: null,
  progress: null,
  progressByStage: {},
  outputs: [],
  error: null,
  summary: null,
  project: null,
  logs: [],
  power: null,
};

export type JobAction =
  | { type: "start" }
  | { type: "event"; event: WorkerEvent }
  | { type: "log"; line: string }
  | { type: "exit"; code: number | null; cancelled: boolean }
  | { type: "fail"; message: string }
  | { type: "reset" };

export function jobReducer(state: JobView, action: JobAction): JobView {
  switch (action.type) {
    case "start":
      return { ...initialJob, status: "running", stage: "queued" };
    case "reset":
      return initialJob;
    case "log":
      return { ...state, logs: [...state.logs.slice(-199), action.line] };
    case "fail":
      return { ...state, status: "failed", error: action.message };
    case "exit": {
      if (state.status !== "running") return state;
      if (action.cancelled) return { ...state, status: "cancelled", stage: "cancelled" };
      if (action.code === 0) return { ...state, status: "completed" };
      return {
        ...state,
        status: "failed",
        error: state.error ?? `Worker exited with code ${action.code ?? "unknown"}`,
      };
    }
    case "event":
      return applyEvent(state, action.event);
  }
}

function applyEvent(state: JobView, e: WorkerEvent): JobView {
  if (e.type === "progress") {
    const p = { done: e.done ?? 0, total: e.total ?? 0 };
    const next = {
      ...state,
      progress: { stage: e.stage ?? null, ...p },
      progressByStage: e.stage ? { ...state.progressByStage, [e.stage]: p } : state.progressByStage,
    };
    if (e.device) {
      next.translationDevice = e.device;
      next.translationFallback = e.fallback_reason ?? null;
    }
    return next;
  }
  if (e.type === "summary") return { ...state, summary: e.summary ?? null };
  if (e.type === "device") {
    // Speech recognition moved (or reloaded) after the NPU/GPU driver was reset.
    return {
      ...state,
      asrDevice: e.device ?? state.asrDevice,
      asrFallback: e.fallback_reason ?? state.asrFallback,
    };
  }
  if (e.type === "power") {
    return {
      ...state,
      power: {
        keep_awake: Boolean(e.keep_awake),
        display_on: Boolean(e.display_on),
        throttling_disabled: Boolean(e.throttling_disabled),
        reason: e.reason ?? null,
      },
    };
  }
  if (e.type === "error") {
    return { ...state, status: "failed", error: `${e.error_code}: ${e.error_message}` };
  }
  if (e.type === "warning") {
    return { ...state, logs: [...state.logs, `warning: ${e.message ?? ""}`] };
  }
  if (e.type !== "state" || !e.state) return state;
  const next: JobView = { ...state, stage: e.state };
  if (e.state === "transcribing") {
    next.asrDevice = e.device ?? null;
    next.asrFallback = e.fallback_reason ?? null;
  }
  if (e.state === "transcribing" || e.state === "translating") next.progress = null;
  if (e.state === "completed") {
    next.status = "completed";
    next.outputs = e.outputs ?? [];
    next.project = e.project ? normalizeProject(e.project) : null;
  }
  if (e.state === "failed") {
    next.status = "failed";
    next.error = `${e.error_code ?? "FAILED"}: ${e.error_message ?? "unknown error"}`;
  }
  if (e.state === "cancelled") next.status = "cancelled";
  return next;
}

export function parseWorkerLine(line: string): WorkerEvent | null {
  try {
    const value = JSON.parse(line) as unknown;
    return value && typeof value === "object" && "type" in value ? (value as WorkerEvent) : null;
  } catch {
    return null;
  }
}

/** Default output base: same folder and stem as the media file. */
export function defaultOutputBase(source: string | null): string {
  if (!source) return "subtitles";
  const dot = source.lastIndexOf(".");
  const sep = Math.max(source.lastIndexOf("/"), source.lastIndexOf("\\"));
  return dot > sep ? source.slice(0, dot) : source;
}

/** Strip a user-chosen save path back to a base: "talk.vi.srt" -> "talk". */
export function outputBaseFromSavePath(path: string): string {
  return path.replace(/(\.localsub)?(\.(en|vi|ja|ko|zh|auto))?\.(srt|vtt|txt|json)$/i, "");
}
