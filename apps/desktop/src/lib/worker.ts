/**
 * Bridge to the Tauri backend. When the UI runs in a normal browser (vite dev / preview),
 * a deterministic preview worker is used so the interface can be reviewed without Windows.
 */
import type { Diagnostics, JobConfig, LocalSubProject, ModelInfo, WorkerEvent } from "./types";

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export interface JobHandlers {
  onEvent: (event: WorkerEvent) => void;
  onLog: (line: string) => void;
  onExit: (code: number | null, cancelled: boolean) => void;
}

export interface WorkerBridge {
  preview: boolean;
  diagnostics(): Promise<Diagnostics>;
  models(): Promise<ModelInfo[]>;
  pickMedia(): Promise<string | null>;
  pickProject(): Promise<string | null>;
  pickSaveBase(defaultPath: string): Promise<string | null>;
  loadProject(path: string): Promise<unknown>;
  startJob(config: JobConfig, handlers: JobHandlers): Promise<() => void>;
  cancelJob(): Promise<boolean>;
  exportProject(project: LocalSubProject, base: string, formats: string[]): Promise<string[]>;
}

const MEDIA_EXTENSIONS = ["mp4", "mkv", "mov", "avi", "webm", "m4v", "mp3", "wav", "m4a", "flac", "aac", "ogg"];

async function tauriBridge(): Promise<WorkerBridge> {
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");
  const dialog = await import("@tauri-apps/plugin-dialog");
  return {
    preview: false,
    diagnostics: async () => JSON.parse(await invoke<string>("run_diagnostics")) as Diagnostics,
    models: async () => JSON.parse(await invoke<string>("list_models")) as ModelInfo[],
    pickMedia: async () => {
      const chosen = await dialog.open({
        multiple: false,
        directory: false,
        filters: [{ name: "Video or audio", extensions: MEDIA_EXTENSIONS }],
      });
      return typeof chosen === "string" ? chosen : null;
    },
    pickProject: async () => {
      const chosen = await dialog.open({
        multiple: false,
        filters: [{ name: "LocalSub project", extensions: ["json"] }],
      });
      return typeof chosen === "string" ? chosen : null;
    },
    pickSaveBase: async (defaultPath) =>
      (await dialog.save({
        defaultPath: `${defaultPath}.srt`,
        filters: [{ name: "Subtitles", extensions: ["srt", "vtt", "txt", "json"] }],
      })) ?? null,
    loadProject: async (path) => JSON.parse(await invoke<string>("load_project", { path })),
    startJob: async (config, handlers) => {
      const unlisten = await Promise.all([
        listen<string>("job-event", (e) => {
          try {
            handlers.onEvent(JSON.parse(e.payload) as WorkerEvent);
          } catch {
            handlers.onLog(e.payload);
          }
        }),
        listen<string>("job-log", (e) => handlers.onLog(e.payload)),
        listen<{ code: number | null; cancelled: boolean }>("job-exit", (e) =>
          handlers.onExit(e.payload.code, e.payload.cancelled),
        ),
      ]);
      const stop = () => unlisten.forEach((fn) => fn());
      try {
        await invoke("start_job", {
          job: {
            source: config.source,
            language: config.language,
            task: config.task,
            model: config.model,
            device: config.device,
            translateTo: config.translateTo || null,
            translationModel: config.translateTo ? config.translationModel : null,
            translationDevice: config.translateTo ? config.translationDevice : null,
            formats: config.formats,
          },
        });
      } catch (err) {
        stop();
        throw err;
      }
      return stop;
    },
    cancelJob: () => invoke<boolean>("cancel_job"),
    exportProject: async (project, base, formats) => {
      const out = await invoke<string>("export_project", {
        projectJson: JSON.stringify(project),
        outputBase: base,
        formats,
      });
      return (JSON.parse(out) as { outputs: string[] }).outputs;
    },
  };
}

const PREVIEW_CUES = [
  ["Good morning, everyone, and welcome to the weekly market briefing.", "Chào buổi sáng mọi người, chào mừng đến với bản tin thị trường hằng tuần."],
  ["The VN-Index closed higher on strong banking volume.", "VN-Index đóng cửa tăng nhờ thanh khoản mạnh ở nhóm ngân hàng."],
  ["Foreign investors were net buyers for the third session.", "Khối ngoại mua ròng phiên thứ ba liên tiếp."],
  ["We will review the portfolio risk limits after the break.", "Chúng ta sẽ rà soát hạn mức rủi ro danh mục sau giờ nghỉ."],
] as const;

function previewBridge(): WorkerBridge {
  let timers: number[] = [];
  let exitHandler: JobHandlers["onExit"] | null = null;
  return {
    preview: true,
    diagnostics: async () => ({
      ffmpeg_available: true,
      ffmpeg_path: "ffmpeg",
      openvino_version: "preview",
      available_devices: ["CPU", "GPU", "NPU"],
      device_resolution: { requested: "auto", resolved: "NPU", available_devices: ["CPU", "GPU", "NPU"], fallback_reason: null },
      output_dir: "data/output",
      model_dir: "data/models",
      installed_models: ["whisper-small-int8", "qwen3-8b-int4-cw"],
    }),
    models: async () => [],
    pickMedia: async () => "C:\\Videos\\market-briefing.mp4",
    pickProject: async () => null,
    pickSaveBase: async (base) => base,
    loadProject: async () => null,
    startJob: async (config, handlers) => {
      exitHandler = handlers.onExit;
      const withTranslation = Boolean(config.translateTo);
      const steps: WorkerEvent[] = [
        { type: "state", state: "probing" },
        { type: "state", state: "extracting_audio" },
        { type: "state", state: "loading_model", model_id: config.model },
        { type: "state", state: "transcribing", device: config.device === "auto" ? "NPU" : config.device, fallback_reason: null },
      ];
      if (withTranslation) {
        steps.push(
          { type: "state", state: "translating", model_id: config.translationModel },
          { type: "progress", stage: "translating", done: 0, total: 4, device: "NPU", fallback_reason: null },
          { type: "progress", stage: "translating", done: 4, total: 4 },
        );
      }
      steps.push({ type: "state", state: "rendering" });
      steps.push({
        type: "state",
        state: "completed",
        outputs: ["C:\\Videos\\market-briefing.en.srt"],
        project: {
          schema: "localsub.project/v1",
          source_media: config.source,
          source_language: "en",
          target_language: withTranslation ? config.translateTo : null,
          cues: PREVIEW_CUES.map(([text, vi], i) => ({
            start_ms: i * 4200,
            end_ms: i * 4200 + 3900,
            text,
            translation: withTranslation ? vi : null,
          })),
        },
      });
      timers = steps.map((event, i) => window.setTimeout(() => handlers.onEvent(event), 350 * (i + 1)));
      timers.push(window.setTimeout(() => handlers.onExit(0, false), 350 * (steps.length + 1)));
      return () => timers.forEach((t) => window.clearTimeout(t));
    },
    cancelJob: async () => {
      timers.forEach((t) => window.clearTimeout(t));
      exitHandler?.(null, true);
      return true;
    },
    exportProject: async (_project, base, formats) =>
      formats.flatMap((f) =>
        f === "json" ? [`${base}.localsub.json`] : [`${base}.en.${f}`, `${base}.vi.${f}`],
      ),
  };
}

let bridge: Promise<WorkerBridge> | null = null;

export function getBridge(): Promise<WorkerBridge> {
  bridge ??= isTauri() ? tauriBridge() : Promise.resolve(previewBridge());
  return bridge;
}
