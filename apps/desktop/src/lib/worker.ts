/**
 * Bridge to the Tauri backend. When the UI runs in a normal browser (vite dev / preview),
 * a deterministic preview worker is used so the interface can be reviewed without Windows.
 */
import type {
  Diagnostics,
  DownloadEvent,
  DownloadHandlers,
  JobConfig,
  JobHistory,
  LocalSubProject,
  ModelInfo,
  WorkerEvent,
} from "./types";

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
  downloadModel(modelId: string, handlers: DownloadHandlers): Promise<() => void>;
  cancelDownload(): Promise<boolean>;
  removeModel(modelId: string): Promise<void>;
  history(limit: number): Promise<JobHistory>;
  confirm(message: string, title: string): Promise<boolean>;
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
    downloadModel: async (modelId, handlers) => {
      const unlisten = await Promise.all([
        listen<string>("download-event", (e) => {
          try {
            handlers.onEvent(JSON.parse(e.payload) as DownloadEvent);
          } catch {
            /* ignore non-JSON lines */
          }
        }),
        listen<string>("download-log", (e) => {
          // Errors are printed as one JSON line on stderr by the worker.
          try {
            const parsed = JSON.parse(e.payload) as DownloadEvent;
            if (parsed.error_code) handlers.onEvent({ ...parsed, type: "error" });
          } catch {
            /* progress bars and warnings from huggingface_hub */
          }
        }),
        listen<{ code: number | null; cancelled: boolean }>("download-exit", (e) =>
          handlers.onExit(e.payload.code, e.payload.cancelled),
        ),
      ]);
      const stop = () => unlisten.forEach((fn) => fn());
      try {
        await invoke("download_model", { modelId });
      } catch (err) {
        stop();
        throw err;
      }
      return stop;
    },
    cancelDownload: () => invoke<boolean>("cancel_download"),
    removeModel: async (modelId) => {
      await invoke<string>("remove_model", { modelId });
    },
    history: async (limit) => JSON.parse(await invoke<string>("job_history", { limit })) as JobHistory,
    confirm: (message, title) => dialog.ask(message, { title, kind: "warning" }),
  };
}

const PREVIEW_CUES = [
  ["Good morning, everyone, and welcome to the weekly market briefing.", "Chào buổi sáng mọi người, chào mừng đến với bản tin thị trường hằng tuần."],
  ["The VN-Index closed higher on strong banking volume.", "VN-Index đóng cửa tăng nhờ thanh khoản mạnh ở nhóm ngân hàng."],
  ["Foreign investors were net buyers for the third session.", "Khối ngoại mua ròng phiên thứ ba liên tiếp."],
  ["We will review the portfolio risk limits after the break.", "Chúng ta sẽ rà soát hạn mức rủi ro danh mục sau giờ nghỉ."],
] as const;

const PREVIEW_MODELS: Array<[string, "asr" | "translation", number]> = [
  ["whisper-tiny-int8", "asr", 70],
  ["whisper-base-int8", "asr", 120],
  ["whisper-small-int8", "asr", 250],
  ["whisper-medium-int8", "asr", 800],
  ["whisper-large-v3-turbo-int8", "asr", 850],
  ["whisper-large-v3-int8", "asr", 1600],
  ["qwen3-8b-int4-cw", "translation", 5000],
  ["qwen2.5-7b-instruct-int4", "translation", 4500],
  ["qwen3-4b-int4", "translation", 2300],
  ["qwen2.5-1.5b-instruct-int4", "translation", 1000],
  ["qwen3-0.6b-int4", "translation", 500],
];

function previewHistory(): JobHistory {
  const now = Date.now();
  const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();
  const npu = { requested: "auto", resolved: "NPU", fallback_reason: null };
  const jobs: JobHistory["jobs"] = [
    { job_id: "a1", started_at: iso(12), finished_at: iso(10), state: "completed", source_name: "market-briefing.mp4", language: "auto", detected_language: "en", translate_to: "vi", model_id: "whisper-small-int8", translation_model: "qwen3-8b-int4-cw", device: npu, translation_device: npu, source_duration_ms: 612_000, total_ms: 98_400, real_time_factor: 0.161, cue_count: 142, translation_fallback_lines: 0, error_code: null },
    { job_id: "a2", started_at: iso(95), finished_at: iso(94), state: "failed", source_name: "interview-ja.mkv", language: "ja", detected_language: null, translate_to: "en", model_id: "whisper-large-v3-turbo-int8", translation_model: "qwen3-8b-int4-cw", device: null, translation_device: null, source_duration_ms: null, total_ms: 1_200, real_time_factor: null, cue_count: null, translation_fallback_lines: null, error_code: "MODEL_INCOMPLETE" },
    { job_id: "a3", started_at: iso(1440), finished_at: iso(1436), state: "completed", source_name: "town-hall.mp4", language: "auto", detected_language: "vi", translate_to: "en", model_id: "whisper-small-int8", translation_model: "qwen3-4b-int4", device: { requested: "auto", resolved: "GPU", fallback_reason: "NPU compile failed" }, translation_device: npu, source_duration_ms: 1_800_000, total_ms: 240_000, real_time_factor: 0.133, cue_count: 410, translation_fallback_lines: 2, error_code: null },
  ];
  return {
    log_path: "data/output/logs/jobs.jsonl",
    jobs,
    skipped_lines: 0,
    stats: { total: 3, completed: 2, failed: 1, npu_share: 0.5, fallbacks: 1, median_rtf: 0.147 },
  };
}

function previewBridge(): WorkerBridge {
  let timers: number[] = [];
  let exitHandler: JobHandlers["onExit"] | null = null;
  const installed = new Set(["whisper-small-int8", "qwen3-8b-int4-cw"]);
  let downloadTimers: number[] = [];
  let downloadExit: DownloadHandlers["onExit"] | null = null;
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
      installed_models: [...installed],
    }),
    models: async () =>
      PREVIEW_MODELS.map(([id, kind, mb]) => ({
        id,
        kind,
        source: `OpenVINO/${id}`,
        status: installed.has(id) ? "installed" : id === "whisper-large-v3-turbo-int8" ? "incomplete" : "missing",
        installed: installed.has(id),
        size_bytes: installed.has(id) ? mb * 1_048_576 : id === "whisper-large-v3-turbo-int8" ? 310 * 1_048_576 : 0,
        approx_size_mb: mb,
        path: `data/models/${id}`,
      })),
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
    downloadModel: async (modelId, handlers) => {
      downloadExit = handlers.onExit;
      const total = (PREVIEW_MODELS.find(([id]) => id === modelId)?.[2] ?? 100) * 1_048_576;
      const ticks = 8;
      downloadTimers = Array.from({ length: ticks + 1 }, (_, i) =>
        window.setTimeout(() => {
          if (i < ticks) {
            handlers.onEvent({ type: "download", state: "downloading", model_id: modelId, downloaded_bytes: Math.round((total * i) / ticks), total_bytes: total });
          } else {
            installed.add(modelId);
            handlers.onEvent({ type: "download", state: "completed", model_id: modelId, downloaded_bytes: total, total_bytes: total });
            handlers.onExit(0, false);
          }
        }, 400 * (i + 1)),
      );
      return () => downloadTimers.forEach((t) => window.clearTimeout(t));
    },
    cancelDownload: async () => {
      downloadTimers.forEach((t) => window.clearTimeout(t));
      downloadExit?.(null, true);
      return true;
    },
    removeModel: async (modelId) => {
      installed.delete(modelId);
    },
    history: async () => previewHistory(),
    confirm: async (message) => window.confirm(message),
  };
}

let bridge: Promise<WorkerBridge> | null = null;

export function getBridge(): Promise<WorkerBridge> {
  bridge ??= isTauri() ? tauriBridge() : Promise.resolve(previewBridge());
  return bridge;
}
