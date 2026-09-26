export type DevicePreference = "auto" | "NPU" | "GPU" | "CPU";
export type SpokenLanguage = "auto" | "en" | "ja" | "vi";
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
  translationDevice: DevicePreference;
  formats: ExportFormat[];
}

export interface WorkerEvent {
  type: "state" | "progress" | "summary" | "error" | "warning";
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
  progress: { done: number; total: number } | null;
  outputs: string[];
  error: string | null;
  summary: Record<string, unknown> | null;
  project: LocalSubProject | null;
  logs: string[];
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

export interface ModelInfo {
  id: string;
  kind: "asr" | "translation";
  source: string;
  installed: boolean;
  path: string;
}
