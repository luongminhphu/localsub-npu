/**
 * Bridge to the Tauri backend. When the UI runs in a normal browser (vite dev / preview),
 * a deterministic preview worker is used so the interface can be reviewed without Windows.
 */
import type {
  AnalysisReport,
  ClaudeDesktopConfigFile,
  ClaudeDesktopWriteResult,
  Diagnostics,
  DownloadEvent,
  DownloadHandlers,
  JobConfig,
  JobHistory,
  MetricsSnapshot,
  LocalSubProject,
  McpHttpEvent,
  McpHttpHandlers,
  McpHttpStatus,
  McpLaunch,
  ModelInfo,
  TunnelHandlers,
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
  /** One or more media files (several are queued); null when cancelled. */
  pickMedia(): Promise<string[] | null>;
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
  analyze(limit: number): Promise<AnalysisReport>;
  mcpConfig(): Promise<McpLaunch>;
  /** Read Claude Desktop's config file (nothing is changed). */
  claudeDesktopConfig(): Promise<ClaudeDesktopConfigFile>;
  /** Replace Claude Desktop's config file with `text`, keeping a backup of the old one. */
  writeClaudeDesktopConfig(text: string): Promise<ClaudeDesktopWriteResult>;
  /** Perplexity: read-only MCP over HTTP on 127.0.0.1 (returns an unsubscribe function). */
  startMcpHttp(port: number, rotate: boolean, handlers: McpHttpHandlers): Promise<() => void>;
  stopMcpHttp(): Promise<boolean>;
  mcpHttpStatus(): Promise<McpHttpStatus>;
  /** Cloudflare quick tunnel giving the local server a public HTTPS URL. */
  startTunnel(port: number, handlers: TunnelHandlers): Promise<() => void>;
  stopTunnel(): Promise<boolean>;
  systemMetrics(sinceMs: number | null): Promise<MetricsSnapshot>;
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
        multiple: true,
        directory: false,
        filters: [{ name: "Video or audio", extensions: MEDIA_EXTENSIONS }],
      });
      if (chosen === null) return null;
      return Array.isArray(chosen) ? chosen : [chosen];
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
            keepAwake: config.keepAwake,
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
    analyze: async (limit) => JSON.parse(await invoke<string>("analyze_performance", { limit })) as AnalysisReport,
    mcpConfig: () => invoke<McpLaunch>("mcp_config"),
    claudeDesktopConfig: () => invoke<ClaudeDesktopConfigFile>("claude_desktop_config"),
    writeClaudeDesktopConfig: (text) => invoke<ClaudeDesktopWriteResult>("write_claude_desktop_config", { text }),
    startMcpHttp: async (port, rotate, handlers) => {
      const unlisten = await Promise.all([
        listen<string>("mcp-http-event", (e) => {
          try {
            handlers.onEvent(JSON.parse(e.payload) as McpHttpEvent);
          } catch {
            handlers.onLog(e.payload);
          }
        }),
        listen<string>("mcp-http-log", (e) => handlers.onLog(e.payload)),
        listen<{ code: number | null; cancelled: boolean }>("mcp-http-exit", (e) =>
          handlers.onExit(e.payload.code, e.payload.cancelled),
        ),
      ]);
      const stop = () => unlisten.forEach((fn) => fn());
      try {
        await invoke("start_mcp_http", { port, rotate });
      } catch (err) {
        stop();
        throw err;
      }
      return stop;
    },
    stopMcpHttp: () => invoke<boolean>("stop_mcp_http"),
    mcpHttpStatus: () => invoke<McpHttpStatus>("mcp_http_status"),
    startTunnel: async (port, handlers) => {
      const unlisten = await Promise.all([
        listen<string>("mcp-tunnel-event", (e) => handlers.onUrl(e.payload)),
        listen<string>("mcp-tunnel-log", (e) => handlers.onLog(e.payload)),
        listen<{ code: number | null; cancelled: boolean }>("mcp-tunnel-exit", (e) =>
          handlers.onExit(e.payload.code, e.payload.cancelled),
        ),
      ]);
      const stop = () => unlisten.forEach((fn) => fn());
      try {
        await invoke("start_tunnel", { port });
      } catch (err) {
        stop();
        throw err;
      }
      return stop;
    },
    stopTunnel: () => invoke<boolean>("stop_tunnel"),
    systemMetrics: (sinceMs) => invoke<MetricsSnapshot>("system_metrics", { sinceMs }),
    confirm: (message, title) => dialog.ask(message, { title, kind: "warning" }),
  };
}

const PREVIEW_CUES = [
  ["Good morning, everyone, and welcome to the weekly market briefing.", "Chào buổi sáng mọi người, chào mừng đến với bản tin thị trường hằng tuần."],
  ["The VN-Index closed higher on strong banking volume.", "VN-Index đóng cửa tăng nhờ thanh khoản mạnh ở nhóm ngân hàng."],
  ["Foreign investors were net buyers for the third session.", "Khối ngoại mua ròng phiên thứ ba liên tiếp."],
  ["We will review the portfolio risk limits after the break.", "Chúng ta sẽ rà soát hạn mức rủi ro danh mục sau giờ nghỉ."],
] as const;

type PreviewModel = [string, "asr" | "translation", number, string, Partial<ModelInfo>?];
const EXPERIMENTAL_GPU_CPU: Partial<ModelInfo> = { runtime: "vlm", devices: ["GPU", "CPU"], experimental: true };
const PREVIEW_MODELS: PreviewModel[] = [
  ["whisper-small-int8", "asr", 250, "Whisper small"],
  ["whisper-base-int8", "asr", 120, "Whisper base"],
  ["whisper-tiny-int8", "asr", 70, "Whisper tiny"],
  ["whisper-medium-int8", "asr", 800, "Whisper medium"],
  ["whisper-large-v3-turbo-int8", "asr", 850, "Whisper large-v3 turbo"],
  ["whisper-large-v3-int8", "asr", 1600, "Whisper large-v3"],
  ["distil-whisper-large-v3-int8", "asr", 780, "Distil-Whisper large-v3 (EN)", { languages: ["en"], experimental: true, hint: { vi: "Chỉ tiếng Anh: gần bằng large-v3 nhưng nhanh hơn nhiều (2 lớp decoder).", en: "English only: close to large-v3 accuracy, much faster (2 decoder layers)." } }],
  ["qwen3-8b-int4-cw", "translation", 5000, "Qwen3 8B"],
  ["qwen2.5-7b-instruct-int4", "translation", 4500, "Qwen2.5 7B"],
  ["qwen3-4b-int4", "translation", 2300, "Qwen3 4B"],
  ["qwen2.5-1.5b-instruct-int4", "translation", 1000, "Qwen2.5 1.5B"],
  ["qwen3-0.6b-int4", "translation", 500, "Qwen3 0.6B"],
  ["qwen3.5-9b-int4", "translation", 6300, "Qwen3.5 9B", { ...EXPERIMENTAL_GPU_CPU, hint: { vi: "Thế hệ mới (09/2026), đa ngôn ngữ tốt hơn; GPU/CPU. Thử nghiệm: đo tốc độ trước.", en: "Newer generation (09/2026), stronger multilingual; GPU/CPU. Experimental: benchmark." } }],
  ["qwen3.5-4b-int4", "translation", 3600, "Qwen3.5 4B", { ...EXPERIMENTAL_GPU_CPU, hint: { vi: "Thế hệ mới, cân bằng; GPU/CPU. Thử nghiệm: đo tốc độ trước.", en: "Newer generation, balanced; GPU/CPU. Experimental: benchmark first." } }],
  ["qwen3.5-2b-int4", "translation", 2100, "Qwen3.5 2B", { ...EXPERIMENTAL_GPU_CPU, hint: { vi: "Thế hệ mới, nhẹ; GPU/CPU. Thử nghiệm: đo tốc độ trước.", en: "Newer generation, light; GPU/CPU. Experimental: benchmark first." } }],
];

function previewHistory(): JobHistory {
  const now = Date.now();
  const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();
  const npu = { requested: "auto", resolved: "NPU", fallback_reason: null };
  const jobs: JobHistory["jobs"] = [
    { job_id: "a1", started_at: iso(12), finished_at: iso(10), state: "completed", source_name: "market-briefing.mp4", language: "auto", detected_language: "en", translate_to: "vi", model_id: "whisper-small-int8", translation_model: "qwen3-8b-int4-cw", device: npu, translation_device: { requested: "auto", resolved: "GPU", fallback_reason: null }, source_duration_ms: 612_000, total_ms: 98_400, real_time_factor: 0.07, total_rtf: 0.161, asr_ms: 42_800, translation_ms: 51_300, cue_count: 142, translation_fallback_lines: 0, overlap: true, translation_after_asr_ms: 9_300, translation_parallel: 8, error_code: null },
    { job_id: "a2", started_at: iso(95), finished_at: iso(94), state: "failed", source_name: "interview-ja.mkv", language: "ja", detected_language: null, translate_to: "en", model_id: "whisper-large-v3-turbo-int8", translation_model: "qwen3-8b-int4-cw", device: null, translation_device: null, source_duration_ms: null, total_ms: 1_200, real_time_factor: null, total_rtf: null, asr_ms: null, translation_ms: null, cue_count: null, translation_fallback_lines: null, error_code: "MODEL_INCOMPLETE" },
    { job_id: "a3", started_at: iso(1440), finished_at: iso(1436), state: "completed", source_name: "town-hall.mp4", language: "auto", detected_language: "vi", translate_to: "en", model_id: "whisper-small-int8", translation_model: "qwen3-4b-int4", device: { requested: "auto", resolved: "GPU", fallback_reason: "NPU compile failed" }, translation_device: { requested: "auto", resolved: "GPU", fallback_reason: null }, source_duration_ms: 1_800_000, total_ms: 240_000, real_time_factor: 0.05, total_rtf: 0.133, asr_ms: 90_000, translation_ms: 141_000, translation_parallel: 8, cue_count: 410, translation_fallback_lines: 2, error_code: null },
  ];
  return {
    log_path: "data/output/logs/jobs.jsonl",
    jobs,
    skipped_lines: 0,
    stats: { total: 3, completed: 2, failed: 1, npu_share: 0.5, fallbacks: 1, median_rtf: 0.147 },
  };
}

function previewAnalysis(): AnalysisReport {
  const now = Date.now();
  const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();
  return {
    generated_at: new Date(now).toISOString(),
    log_path: "data/output/logs/jobs.jsonl",
    log_found: true,
    skipped_lines: 0,
    jobs_analyzed: 3,
    newest_job_at: iso(12),
    oldest_job_at: iso(1440),
    status: "issues",
    severity_counts: { high: 1, medium: 2, low: 1 },
    stats: {
      jobs: 3,
      completed: 2,
      failed: 1,
      stages: {
        extract: { median_ms: 4_100, max_ms: 235_000 },
        load_model: { median_ms: 9_800, max_ms: 14_200 },
        load_translation_model: { median_ms: 210_000, max_ms: 410_000 },
        inference: { median_ms: 66_400, max_ms: 90_000 },
        translation: { median_ms: 96_000, max_ms: 141_000 },
        total: { median_ms: 169_000, max_ms: 240_000 },
      },
    },
    findings: [
      {
        id: "slow_translation_model_load",
        severity: "high",
        category: "load",
        title: { vi: "Nạp model dịch chậm", en: "Slow translation model load" },
        detail: {
          vi: "2/2 lần nạp model dịch qwen3-8b-int4-cw trên NPU chậm hơn 60.0s, lâu nhất 410.0s. Nạp chậm lặp lại nghĩa là bộ đệm biên dịch (.ov-cache) không được dùng lại.",
          en: "2/2 loads of translation model qwen3-8b-int4-cw on NPU exceeded 60.0s, worst 410.0s. Repeated slow loads mean the compile cache (.ov-cache) is not being reused.",
        },
        recommendation: {
          vi: "Kiểm tra CACHE_DIR có được truyền khi compile và thư mục .ov-cache có ghi được; cân nhắc nạp model dịch song song với bước tách audio/nhận dạng.",
          en: "Check CACHE_DIR is passed at compile time and .ov-cache is writable; consider loading the translator in parallel with audio extraction/ASR.",
        },
        job_ids: ["a1", "a3"],
        evidence: { model: "qwen3-8b-int4-cw", device: "NPU", loads_ms: [410_000, 395_000] },
        code_refs: ["app/translation.py:load_translator", "app/devices.py:compile_with_fallback"],
      },
      {
        id: "slow_audio_extraction",
        severity: "medium",
        category: "io",
        title: { vi: "Tách audio chậm", en: "Slow audio extraction" },
        detail: {
          vi: "1 job tách audio mất hơn 2% thời lượng video (lâu nhất 235.0s); 1 job đọc từ đường dẫn mạng (UNC).",
          en: "1 job spent over 2% of the media duration extracting audio (worst 235.0s); 1 read from a network (UNC) path.",
        },
        recommendation: {
          vi: "Chép video về ổ SSD cục bộ trước khi xử lý.",
          en: "Copy media to a local SSD first.",
        },
        job_ids: ["a3"],
        evidence: {},
        code_refs: ["app/audio.py:extract_audio"],
      },
      {
        id: "device_fallback",
        severity: "medium",
        category: "load",
        title: { vi: "Không chạy được trên thiết bị ưu tiên", en: "Preferred device not used" },
        detail: {
          vi: "1 lần phải chuyển thiết bị (ví dụ NPU → GPU/CPU) khi nạp model.",
          en: "1 model load fell back to another device (e.g. NPU -> GPU/CPU).",
        },
        recommendation: { vi: "Đọc fallback_reason và kiểm tra driver NPU.", en: "Read fallback_reason and check the NPU driver." },
        job_ids: ["a3"],
        evidence: {},
        code_refs: ["app/devices.py:compile_with_fallback"],
      },
      {
        id: "overlap_idle",
        severity: "low",
        category: "translation",
        title: { vi: "Dịch chồng không có tác dụng", en: "Overlap translation did nothing" },
        detail: {
          vi: "1 job bật dịch chồng nhưng không dịch được dòng nào trong lúc nhận dạng.",
          en: "1 job enabled overlap translation but translated no lines while ASR was running.",
        },
        recommendation: { vi: "Kiểm tra stable_lines.", en: "Check stable_lines." },
        job_ids: ["a1"],
        evidence: {},
        code_refs: ["app/jobs.py:OverlapTranslation"],
      },
    ],
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
      PREVIEW_MODELS.map(([id, kind, mb, label, extra]) => ({
        label,
        devices: ["NPU", "GPU", "CPU"],
        languages: null,
        ...extra,
        id,
        kind,
        source: `OpenVINO/${id}`,
        status: installed.has(id) ? "installed" : id === "whisper-large-v3-turbo-int8" ? "incomplete" : "missing",
        installed: installed.has(id),
        size_bytes: installed.has(id) ? mb * 1_048_576 : id === "whisper-large-v3-turbo-int8" ? 310 * 1_048_576 : 0,
        approx_size_mb: mb,
        path: `data/models/${id}`,
      })),
    pickMedia: async () => ["C:\\Videos\\market-briefing.mp4", "C:\\Videos\\weekly-standup.mp4"],
    pickProject: async () => null,
    pickSaveBase: async (base) => base,
    loadProject: async () => null,
    startJob: async (config, handlers) => {
      exitHandler = handlers.onExit;
      const withTranslation = Boolean(config.translateTo);
      const steps: WorkerEvent[] = [
        { type: "power", keep_awake: config.keepAwake, display_on: false, throttling_disabled: config.keepAwake, reason: config.keepAwake ? null : "disabled" },
        { type: "state", state: "probing" },
        { type: "state", state: "extracting_audio" },
        { type: "state", state: "loading_model", model_id: config.model },
        { type: "state", state: "transcribing", device: config.device === "auto" ? "NPU" : config.device, fallback_reason: null },
        { type: "progress", stage: "transcribing", done: 300_000, total: 900_000 },
      ];
      const asrDevice = config.device === "auto" ? "NPU" : config.device;
      const mtDevice = config.translationDevice === "auto" ? "GPU" : config.translationDevice;
      // Mirrors the worker: translation overlaps transcription when the devices differ.
      const overlap = withTranslation && asrDevice !== mtDevice;
      if (overlap) {
        steps.push(
          { type: "progress", stage: "translating", done: 0, total: 64, device: mtDevice, fallback_reason: null },
          { type: "progress", stage: "transcribing", done: 600_000, total: 900_000 },
          { type: "progress", stage: "translating", done: 64, total: 128 },
          { type: "progress", stage: "transcribing", done: 900_000, total: 900_000 },
        );
      } else {
        steps.push({ type: "progress", stage: "transcribing", done: 900_000, total: 900_000 });
      }
      if (withTranslation) {
        steps.push(
          { type: "state", state: "translating", model_id: config.translationModel },
          ...(overlap ? [] : [{ type: "progress", stage: "translating", done: 0, total: 4, device: mtDevice, fallback_reason: null } as WorkerEvent]),
          { type: "progress", stage: "translating", done: overlap ? 160 : 4, total: overlap ? 160 : 4 },
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
    analyze: async () => previewAnalysis(),
    mcpConfig: async () => ({
      command: "C:\\Program Files\\LocalSub NPU\\worker\\localsub-worker.exe",
      args: ["mcp"],
      cwd: "C:\\Program Files\\LocalSub NPU\\worker",
      env: {
        LOCALSUB_MODEL_DIR: "C:\\Users\\you\\AppData\\Local\\com.luongminhphu.localsubnpu\\models",
        LOCALSUB_OUTPUT_DIR: "C:\\Users\\you\\AppData\\Local\\com.luongminhphu.localsubnpu\\output",
        PYTHONUTF8: "1",
      },
      bundled: true,
    }),
    ...previewClaudeDesktop(),
    ...previewMcpHttp(),
    systemMetrics: async (sinceMs) => previewMetrics(sinceMs),
    confirm: async (message) => window.confirm(message),
  };
}

/** In-memory claude_desktop_config.json with another server already configured. */
function previewClaudeDesktop(): Pick<WorkerBridge, "claudeDesktopConfig" | "writeClaudeDesktopConfig"> {
  const path = "C:\\Users\\you\\AppData\\Roaming\\Claude\\claude_desktop_config.json";
  let text = JSON.stringify({ mcpServers: { filesystem: { command: "npx", args: ["-y", "fs"] } } }, null, 2);
  return {
    claudeDesktopConfig: async () => ({ path, exists: true, text }),
    writeClaudeDesktopConfig: async (next) => {
      text = next;
      return { path, backup: `${path}.localsub.bak` };
    },
  };
}

/** Simulated HTTP server + tunnel so the Perplexity card can be reviewed in a browser. */
function previewMcpHttp(): Pick<
  WorkerBridge,
  "startMcpHttp" | "stopMcpHttp" | "mcpHttpStatus" | "startTunnel" | "stopTunnel"
> {
  let server: McpHttpHandlers | null = null;
  let tunnel: TunnelHandlers | null = null;
  let timers: number[] = [];
  let token = "preview-key-3f9c1d7a2b6e4c08a1d5f7e9b2c4d6e8";
  const clear = () => {
    timers.forEach((t) => window.clearTimeout(t));
    timers = [];
  };
  const stopTunnel = () => {
    const had = tunnel !== null;
    tunnel?.onExit(null, true);
    tunnel = null;
    return had;
  };
  return {
    startMcpHttp: async (port, rotate, handlers) => {
      if (server) throw new Error("the MCP HTTP server is already running");
      if (rotate) token = `preview-key-${Math.random().toString(16).slice(2).padEnd(32, "0")}`;
      server = handlers;
      timers.push(
        window.setTimeout(() => {
          handlers.onEvent({
            type: "mcp_http",
            state: "listening",
            url: `http://127.0.0.1:${port}/mcp`,
            port,
            token,
            token_source: rotate ? "new" : "file",
            token_path: "C:\\Users\\you\\AppData\\Local\\com.luongminhphu.localsubnpu\\mcp\\http-token",
            audit_log: "C:\\Users\\you\\AppData\\Local\\com.luongminhphu.localsubnpu\\output\\logs\\mcp_http.jsonl",
            redact: true,
            at: new Date().toISOString(),
          });
        }, 300),
      );
      return () => undefined;
    },
    stopMcpHttp: async () => {
      clear();
      const had = server !== null || tunnel !== null;
      stopTunnel();
      server?.onExit(null, true);
      server = null;
      return had;
    },
    mcpHttpStatus: async () => ({
      server: server !== null,
      tunnel: tunnel !== null,
      cloudflared: "C:\\Program Files (x86)\\cloudflared\\cloudflared.exe",
    }),
    startTunnel: async (_port, handlers) => {
      if (!server) throw new Error("start the MCP HTTP server first");
      if (tunnel) throw new Error("the HTTPS tunnel is already running");
      tunnel = handlers;
      const srv = server;
      timers.push(
        window.setTimeout(() => handlers.onUrl("https://quiet-river-demo.trycloudflare.com"), 800),
        window.setTimeout(() => {
          srv.onEvent({
            type: "mcp_http_request",
            ts: new Date().toISOString(),
            http: "POST",
            method: "tools/call",
            name: "localsub_analyze_performance",
            status: 200,
            auth: "ok",
            duration_ms: 12.4,
            client_ip: "203.0.113.7",
          });
        }, 1800),
      );
      return () => undefined;
    },
    stopTunnel: async () => stopTunnel(),
  };
}

let bridge: Promise<WorkerBridge> | null = null;

export function getBridge(): Promise<WorkerBridge> {
  bridge ??= isTauri() ? tauriBridge() : Promise.resolve(previewBridge());
  return bridge;
}

/** Simulated load for the browser preview: NPU busy while transcribing, GPU while translating. */
export function previewMetrics(sinceMs: number | null, now = Date.now()): MetricsSnapshot {
  const samples: MetricsSnapshot["samples"] = [];
  const last = Math.floor(now / 1000) * 1000;
  const from = Math.max(sinceMs === null ? last - 180_000 : sinceMs + 1000, last - 300_000);
  for (let at = Math.ceil(from / 1000) * 1000; at <= last; at += 1000) {
    const t = at / 1000;
    const phase = Math.floor(t / 60) % 3; // 0 idle, 1 ASR on NPU, 2 translation on GPU
    const wave = (k: number) => (Math.sin(t / k) + 1) / 2;
    samples.push({
      at_ms: at,
      cpu: Math.round((12 + 18 * wave(7) + (phase ? 15 : 0)) * 10) / 10,
      gpu: Math.round((phase === 2 ? 70 + 20 * wave(5) : 4 + 4 * wave(9)) * 10) / 10,
      npu: Math.round((phase === 1 ? 65 + 25 * wave(4) : phase === 2 ? 3 : 0) * 10) / 10,
      mem_used_bytes: Math.round((9.5 + (phase ? 5 : 0) + wave(30)) * 1024 ** 3),
      mem_total_bytes: 32 * 1024 ** 3,
    });
  }
  return {
    source: "preview",
    interval_ms: 1000,
    gpu_name: "Intel(R) Arc(TM) 140V GPU (preview)",
    npu_name: "Intel(R) AI Boost (preview)",
    error: null,
    samples,
  };
}
