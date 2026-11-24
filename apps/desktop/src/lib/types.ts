export type DevicePreference = "auto" | "NPU" | "GPU" | "CPU";
export type TranslationDevice = DevicePreference;
export const SPOKEN_LANGUAGES = ["auto", "en", "ja", "ko", "vi", "zh"] as const;
export type SpokenLanguage = (typeof SPOKEN_LANGUAGES)[number];
export type TargetLanguage = "" | "en" | "vi";
export type ExportFormat = "srt" | "vtt" | "txt" | "json";
export type WorkerStage =
  | "queued"
  | "probing"
  | "extracting_audio"
  | "loading_model"
  | "transcribing"
  | "translating"
  | "rendering"
  | "completed"
  | "failed"
  | "cancelled";

export interface ProjectCue {
  start_ms: number;
  end_ms: number;
  text: string;
  translation: string | null;
}

export interface LocalSubProject {
  schema: "localsub.project/v1";
  source_media: string | null;
  source_language: string | null;
  target_language: string | null;
  cues: ProjectCue[];
}

export interface JobConfig {
  source: string;
  language: SpokenLanguage;
  task: "transcribe" | "translate";
  model: string;
  device: DevicePreference;
  translateTo: TargetLanguage;
  translationModel: string;
  translationDevice: TranslationDevice;
  formats: ExportFormat[];
  /** Hold off Windows idle sleep and power throttling while the job runs (default on). */
  keepAwake: boolean;
}

/** Keep-awake state the worker applied for the job (`power` event). */
export interface PowerState {
  keep_awake: boolean;
  display_on: boolean;
  throttling_disabled: boolean;
  reason: string | null;
}

export interface WorkerEvent {
  type: "state" | "progress" | "summary" | "error" | "warning" | "power" | "device";
  keep_awake?: boolean;
  display_on?: boolean;
  throttling_disabled?: boolean;
  reason?: string | null;
  state?: WorkerStage;
  stage?: string;
  done?: number;
  total?: number;
  device?: string;
  fallback_reason?: string | null;
  model_id?: string;
  output_path?: string;
  outputs?: string[];
  cue_count?: number;
  project?: LocalSubProject;
  error_code?: string;
  error_message?: string;
  message?: string;
  summary?: Record<string, unknown>;
}

export type JobStatus = "idle" | "running" | "completed" | "failed" | "cancelled";

export interface JobView {
  status: JobStatus;
  stage: WorkerStage | null;
  asrDevice: string | null;
  asrFallback: string | null;
  translationDevice: string | null;
  translationFallback: string | null;
  progress: { stage: string | null; done: number; total: number } | null;
  /** Latest progress per stage: translation reports while transcription still runs. */
  progressByStage: Record<string, { done: number; total: number }>;
  outputs: string[];
  error: string | null;
  summary: Record<string, unknown> | null;
  project: LocalSubProject | null;
  logs: string[];
  power: PowerState | null;
}

export interface Diagnostics {
  ffmpeg_available: boolean;
  ffmpeg_path: string | null;
  openvino_version: string | null;
  available_devices: string[];
  device_resolution: {
    requested: string;
    resolved: string;
    available_devices: string[];
    fallback_reason: string | null;
  } | null;
  output_dir: string;
  model_dir: string;
  installed_models: string[];
}

export type ModelStatus = "installed" | "incomplete" | "missing";

export interface ModelInfo {
  id: string;
  kind: "asr" | "translation";
  source: string;
  status: ModelStatus;
  installed: boolean;
  size_bytes: number;
  approx_size_mb: number | null;
  path: string;
  /** Catalog metadata (worker 0.9+). */
  label?: string;
  runtime?: "whisper" | "llm" | "vlm";
  hint?: { vi: string; en: string };
  devices?: string[];
  languages?: string[] | null;
  experimental?: boolean;
}

/** JSON Lines emitted by `models download --events`. */
export interface DownloadEvent {
  type: "download" | "summary" | "error";
  state?: "downloading" | "completed";
  model_id?: string;
  downloaded_bytes?: number;
  total_bytes?: number | null;
  already_installed?: boolean;
  status?: ModelStatus;
  error_code?: string;
  error_message?: string;
}

export interface DownloadHandlers {
  onEvent: (event: DownloadEvent) => void;
  onExit: (code: number | null, cancelled: boolean) => void;
}

export interface HistoryDevice {
  requested: string | null;
  resolved: string | null;
  fallback_reason: string | null;
}

export interface HistoryJob {
  job_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  state: string | null;
  source_name: string | null;
  language: string | null;
  detected_language: string | null;
  translate_to: string | null;
  model_id: string | null;
  translation_model: string | null;
  device: HistoryDevice | null;
  translation_device: HistoryDevice | null;
  source_duration_ms: number | null;
  total_ms: number | null;
  real_time_factor: number | null;
  cue_count: number | null;
  translation_fallback_lines: number | null;
  error_code: string | null;
  total_rtf?: number | null;
  asr_ms?: number | null;
  translation_ms?: number | null;
  /** Prompts per generate() call the translation settled on after auto-tuning. */
  translation_parallel?: number | null;
  /** Translation ran while speech recognition was still going (Wave 6B). */
  overlap?: boolean | null;
  /** Wall time translation still needed after speech recognition finished. */
  translation_after_asr_ms?: number | null;
}

export interface JobHistory {
  log_path: string;
  jobs: HistoryJob[];
  skipped_lines: number;
  stats: {
    total: number;
    completed: number;
    failed: number;
    npu_share: number | null;
    fallbacks: number;
    median_rtf: number | null;
  };
}

export type MetricKey = "cpu" | "gpu" | "npu";

export interface MetricSample {
  at_ms: number;
  cpu: number | null;
  gpu: number | null;
  npu: number | null;
  mem_used_bytes: number | null;
  mem_total_bytes: number | null;
}

export interface MetricsSnapshot {
  /** windows-pdh = live counters, starting = no sample yet, unsupported/error = unavailable. */
  source: "windows-pdh" | "starting" | "unsupported" | "error" | "preview";
  interval_ms: number;
  gpu_name: string | null;
  npu_name: string | null;
  error: string | null;
  samples: MetricSample[];
}

export type Severity = "high" | "medium" | "low" | "info";

export interface Localized {
  vi: string;
  en: string;
}

/** One rule hit from the worker's `analyze` command (also served to AI clients over MCP). */
export interface Finding {
  id: string;
  severity: Severity;
  category: string;
  title: Localized;
  detail: Localized;
  recommendation: Localized;
  job_ids: string[];
  evidence: Record<string, unknown>;
  code_refs: string[];
}

export interface AnalysisReport {
  generated_at: string;
  log_path: string;
  log_found: boolean;
  skipped_lines: number;
  jobs_analyzed: number;
  newest_job_at: string | null;
  oldest_job_at: string | null;
  status: "ok" | "issues" | "no_data";
  findings: Finding[];
  severity_counts?: Partial<Record<Severity, number>>;
  stats: {
    jobs: number;
    completed: number;
    failed: number;
    stages: Record<string, { median_ms: number; max_ms: number }>;
  };
}

/** How an external MCP client launches the read-only server (from the Rust `mcp_config`). */
export interface McpLaunch {
  command: string;
  args: string[];
  cwd: string;
  env: Record<string, string>;
  bundled: boolean;
}

/** Claude Desktop's claude_desktop_config.json as read by the Rust side. */
export interface ClaudeDesktopConfigFile {
  path: string;
  exists: boolean;
  text: string | null;
}

export interface ClaudeDesktopWriteResult {
  path: string;
  /** Copy of the previous file (`claude_desktop_config.json.localsub.bak`), if there was one. */
  backup: string | null;
}

/** Status line from `mcp --http --events` (the worker's Streamable HTTP MCP server). */
export interface McpHttpServerEvent {
  type: "mcp_http";
  state: "listening" | "stopped" | "error";
  url?: string;
  port?: number;
  token?: string;
  token_source?: "env" | "file" | "new";
  token_path?: string;
  audit_log?: string;
  redact?: boolean;
  at?: string;
  code?: string;
  message?: string;
}

/** One audited HTTP request (no key, no tool arguments). */
export interface McpHttpRequestEvent {
  type: "mcp_http_request";
  ts: string;
  http: string;
  method: string | null;
  name: string | null;
  status: number;
  auth: "ok" | "failed" | "skipped";
  duration_ms: number;
  client_ip?: string;
  user_agent?: string;
}

export type McpHttpEvent = McpHttpServerEvent | McpHttpRequestEvent;

export interface McpHttpStatus {
  server: boolean;
  tunnel: boolean;
  /** Path of cloudflared.exe when installed, otherwise null. */
  cloudflared: string | null;
}

export interface McpHttpHandlers {
  onEvent: (event: McpHttpEvent) => void;
  onLog: (line: string) => void;
  onExit: (code: number | null, cancelled: boolean) => void;
}

export interface TunnelHandlers {
  onUrl: (url: string) => void;
  onLog: (line: string) => void;
  onExit: (code: number | null, cancelled: boolean) => void;
}
