import { describe, expect, it } from "vitest";
import {
  MAX_CUE_CHARS,
  MAX_CUE_CHARS_CJK,
  defaultOutputBase,
  deleteCue,
  initialJob,
  isCjk,
  isHangul,
  MAX_CUE_CHARS_KO,
  isProject,
  jobReducer,
  maxCharsFor,
  mergeWithNext,
  outputBaseFromSavePath,
  parseWorkerLine,
  splitCue,
  updateCue,
  validateCues,
} from "./cues";
import type { LocalSubProject } from "./types";

const project: LocalSubProject = {
  schema: "localsub.project/v1",
  source_media: "C:\\v\\talk.mp4",
  source_language: "en",
  target_language: "vi",
  cues: [
    { start_ms: 0, end_ms: 2000, text: "Hello", translation: "Xin chào" },
    { start_ms: 2000, end_ms: 4000, text: "World", translation: "Thế giới" },
  ],
};

describe("cue editing", () => {
  it("validates like the worker", () => {
    expect(validateCues(project.cues)).toEqual([]);
    const bad = updateCue(project, 1, { start_ms: 1500, text: " " });
    const messages = validateCues(bad.cues).map((i) => i.message);
    expect(messages).toContain("Overlaps the previous cue");
    expect(messages).toContain("Source text is empty");
    const inverted = updateCue(project, 0, { end_ms: 0 });
    expect(validateCues(inverted.cues)[0].message).toBe("End must be after start");
  });

  it("warns on long lines without blocking", () => {
    const long = updateCue(project, 0, { text: "x".repeat(90) });
    expect(validateCues(long.cues)).toEqual([
      expect.objectContaining({ severity: "warning", index: 0 }),
    ]);
  });

  it("splits, merges and deletes immutably", () => {
    const split = splitCue(project, 0);
    expect(split.cues).toHaveLength(3);
    expect(split.cues[0].end_ms).toBe(1000);
    expect(split.cues[1]).toMatchObject({ start_ms: 1000, end_ms: 2000 });
    expect(project.cues).toHaveLength(2);
    const merged = mergeWithNext(project, 0);
    expect(merged.cues).toEqual([
      { start_ms: 0, end_ms: 4000, text: "Hello World", translation: "Xin chào Thế giới" },
    ]);
    expect(mergeWithNext(project, 1)).toBe(project);
    expect(deleteCue(project, 0).cues[0].text).toBe("World");
  });

  it("recognises project files", () => {
    expect(isProject(project)).toBe(true);
    expect(isProject({ cues: [{ start_ms: "0" }] })).toBe(false);
    expect(isProject(null)).toBe(false);
  });

  it("derives output bases", () => {
    expect(defaultOutputBase("C:\\v\\talk.mp4")).toBe("C:\\v\\talk");
    expect(defaultOutputBase("/home/a.b/clip")).toBe("/home/a.b/clip");
    expect(outputBaseFromSavePath("C:\\v\\talk.vi.srt")).toBe("C:\\v\\talk");
    expect(outputBaseFromSavePath("/x/talk.localsub.json")).toBe("/x/talk");
    expect(outputBaseFromSavePath("/x/talk.srt")).toBe("/x/talk");
  });
});

describe("job reducer", () => {
  const lines = [
    '{"type":"state","state":"probing"}',
    '{"type":"state","state":"transcribing","device":"CPU","fallback_reason":"NPU not available"}',
    '{"type":"state","state":"translating","model_id":"qwen3-8b-int4-cw"}',
    '{"type":"progress","stage":"translating","done":0,"total":2,"device":"NPU","fallback_reason":null}',
    '{"type":"progress","stage":"translating","done":2,"total":2}',
    JSON.stringify({ type: "state", state: "completed", outputs: ["/o/talk.en.srt"], project }),
    '{"type":"summary","summary":{"state":"completed"}}',
  ];

  it("follows a full worker event stream", () => {
    let state = jobReducer(initialJob, { type: "start" });
    for (const line of lines) {
      const event = parseWorkerLine(line);
      expect(event).not.toBeNull();
      state = jobReducer(state, { type: "event", event: event! });
    }
    state = jobReducer(state, { type: "exit", code: 0, cancelled: false });
    expect(state.status).toBe("completed");
    expect(state.asrDevice).toBe("CPU");
    expect(state.asrFallback).toBe("NPU not available");
    expect(state.translationDevice).toBe("NPU");
    expect(state.progress).toEqual({ stage: "translating", done: 2, total: 2 });
    expect(state.progressByStage.translating).toEqual({ done: 2, total: 2 });
    expect(state.outputs).toEqual(["/o/talk.en.srt"]);
    expect(state.project?.cues).toHaveLength(2);
  });

  it("keeps transcription and overlapped translation progress apart", () => {
    let state = jobReducer(initialJob, { type: "start" });
    for (const line of [
      '{"type":"state","state":"transcribing","device":"NPU"}',
      '{"type":"progress","stage":"transcribing","done":300000,"total":900000}',
      '{"type":"progress","stage":"translating","done":0,"total":40,"device":"GPU","fallback_reason":null}',
      '{"type":"progress","stage":"translating","done":32,"total":40}',
      '{"type":"progress","stage":"transcribing","done":600000,"total":900000}',
    ]) {
      state = jobReducer(state, { type: "event", event: parseWorkerLine(line)! });
    }
    expect(state.stage).toBe("transcribing");
    expect(state.translationDevice).toBe("GPU");
    expect(state.progressByStage).toEqual({
      transcribing: { done: 600000, total: 900000 },
      translating: { done: 32, total: 40 },
    });
    expect(jobReducer(state, { type: "start" }).progressByStage).toEqual({});
  });

  it("reports worker failures and non-zero exits", () => {
    let state = jobReducer(initialJob, { type: "start" });
    state = jobReducer(state, {
      type: "event",
      event: { type: "state", state: "failed", error_code: "MODEL_NOT_FOUND", error_message: "missing" },
    });
    expect(state.status).toBe("failed");
    expect(state.error).toBe("MODEL_NOT_FOUND: missing");
    let crashed = jobReducer(initialJob, { type: "start" });
    crashed = jobReducer(crashed, { type: "exit", code: 3, cancelled: false });
    expect(crashed.error).toBe("Worker exited with code 3");
  });

  it("marks cancellation and ignores non-JSON lines", () => {
    let state = jobReducer(initialJob, { type: "start" });
    state = jobReducer(state, { type: "exit", code: null, cancelled: true });
    expect(state.status).toBe("cancelled");
    expect(parseWorkerLine("not json")).toBeNull();
    expect(parseWorkerLine("[1,2]")).toBeNull();
  });
});

describe("Japanese cue limits", () => {
  it("detects kana/kanji and uses the 32-character limit", () => {
    expect(isCjk("決算は好調です")).toBe(true);
    expect(isCjk("Toyota の決算は好調です")).toBe(true);
    expect(isCjk("Xin chào các bạn")).toBe(false);
    expect(maxCharsFor("決算")).toBe(MAX_CUE_CHARS_CJK);
    expect(maxCharsFor("今天的会议我们主要讨论预算，谢谢大家")).toBe(MAX_CUE_CHARS_CJK); // Chinese (Han, no kana)
    expect(maxCharsFor("Hello")).toBe(MAX_CUE_CHARS);
    expect(isHangul("오늘 회의를 시작하겠습니다")).toBe(true);
    expect(isCjk("오늘 회의를 시작하겠습니다")).toBe(false);
    expect(maxCharsFor("오늘 회의를 시작하겠습니다")).toBe(MAX_CUE_CHARS_KO);
  });

  it("warns on long Japanese source but not on the same length of Latin text", () => {
    const ja = "あ".repeat(MAX_CUE_CHARS_CJK + 1);
    const latin = "a".repeat(MAX_CUE_CHARS_CJK + 1);
    const issues = validateCues([
      { start_ms: 0, end_ms: 1000, text: ja, translation: "Hello" },
      { start_ms: 1000, end_ms: 2000, text: latin, translation: null },
    ]);
    expect(issues).toEqual([
      { index: 0, severity: "warning", code: "too_long", limit: 32, message: "Longer than 32 characters" },
    ]);
  });
});
