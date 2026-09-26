import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  defaultOutputBase,
  deleteCue,
  initialJob,
  isProject,
  jobReducer,
  mergeWithNext,
  normalizeProject,
  outputBaseFromSavePath,
  splitCue,
  updateCue,
  validateCues,
} from "./lib/cues";
import { formatTime, parseTime } from "./lib/time";
import type {
  DevicePreference,
  Diagnostics,
  ExportFormat,
  JobConfig,
  LocalSubProject,
  SpokenLanguage,
  TargetLanguage,
  WorkerStage,
} from "./lib/types";
import { getBridge, type WorkerBridge } from "./lib/worker";

const WHISPER_MODELS = [
  { id: "whisper-small-int8", label: "Whisper small", hint: "Recommended default for the NPU." },
  { id: "whisper-base-int8", label: "Whisper base", hint: "Faster, slightly less accurate." },
  { id: "whisper-tiny-int8", label: "Whisper tiny", hint: "Smoke tests only." },
  { id: "whisper-medium-int8", label: "Whisper medium", hint: "Better accuracy; GPU or CPU." },
  { id: "whisper-large-v3-turbo-int8", label: "Whisper large-v3 turbo", hint: "Best speed/accuracy on the Arc GPU." },
  { id: "whisper-large-v3-int8", label: "Whisper large-v3", hint: "Highest accuracy, slowest." },
];
const LLM_MODELS = [
  { id: "qwen3-8b-int4-cw", label: "Qwen3 8B", hint: "Best quality; INT4-CW layout for the NPU." },
  { id: "qwen2.5-7b-instruct-int4", label: "Qwen2.5 7B", hint: "Good quality; best on the Arc GPU." },
  { id: "qwen3-4b-int4", label: "Qwen3 4B", hint: "Balanced speed and quality." },
  { id: "qwen2.5-1.5b-instruct-int4", label: "Qwen2.5 1.5B", hint: "Fast draft; review carefully." },
  { id: "qwen3-0.6b-int4", label: "Qwen3 0.6B", hint: "Smoke tests only; poor translations." },
];
const DEVICES: Array<{ value: DevicePreference; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "NPU", label: "NPU" },
  { value: "GPU", label: "GPU" },
  { value: "CPU", label: "CPU" },
];
const JA_WEAK_MODELS = new Set(["whisper-tiny-int8", "whisper-base-int8", "whisper-small-int8"]);
const FORMATS: ExportFormat[] = ["srt", "vtt", "txt", "json"];
const STEPS: Array<{ stage: WorkerStage; label: string }> = [
  { stage: "probing", label: "Check" },
  { stage: "extracting_audio", label: "Audio" },
  { stage: "loading_model", label: "Load model" },
  { stage: "transcribing", label: "Transcribe" },
  { stage: "translating", label: "Translate" },
  { stage: "rendering", label: "Write files" },
];

function baseName(path: string | null): string {
  if (!path) return "";
  return path.split(/[\\/]/).pop() ?? path;
}

function rowsFor(text: string): number {
  return Math.min(6, Math.max(2, Math.ceil(text.length / 24)));
}

function TimeField(props: { label: string; value: number; onCommit: (ms: number) => void }) {
  const [draft, setDraft] = useState(formatTime(props.value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => setDraft(formatTime(props.value)), [props.value]);
  return (
    <input
      aria-label={props.label}
      className={`time-input${invalid ? " invalid" : ""}`}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const ms = parseTime(draft);
        setInvalid(ms === null);
        if (ms !== null) props.onCommit(ms);
      }}
    />
  );
}

export function App() {
  const [bridge, setBridge] = useState<WorkerBridge | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [language, setLanguage] = useState<SpokenLanguage>("auto");
  const [model, setModel] = useState("whisper-small-int8");
  const [device, setDevice] = useState<DevicePreference>("auto");
  const [translateTo, setTranslateTo] = useState<TargetLanguage>("vi");
  const [translationModel, setTranslationModel] = useState("qwen3-8b-int4-cw");
  const [translationDevice, setTranslationDevice] = useState<DevicePreference>("auto");
  const [formats, setFormats] = useState<ExportFormat[]>(["srt", "json"]);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [diagError, setDiagError] = useState<string | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const [job, dispatch] = useReducer(jobReducer, initialJob);
  const [project, setProject] = useState<LocalSubProject | null>(null);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const stopListening = useRef<(() => void) | null>(null);

  useEffect(() => {
    void getBridge().then(setBridge);
  }, []);

  const runDiagnostics = useCallback(async () => {
    if (!bridge) return;
    setDiagBusy(true);
    setDiagError(null);
    try {
      setDiagnostics(await bridge.diagnostics());
    } catch (err) {
      setDiagError(String(err));
    } finally {
      setDiagBusy(false);
    }
  }, [bridge]);

  useEffect(() => {
    void runDiagnostics();
  }, [runDiagnostics]);

  useEffect(() => {
    if (job.status === "completed" && job.project) {
      setProject(job.project);
      setDirty(false);
    }
    if (job.status !== "running") {
      stopListening.current?.();
      stopListening.current = null;
    }
  }, [job.status, job.project]);

  const installed = useMemo(() => new Set(diagnostics?.installed_models ?? []), [diagnostics]);
  const issues = useMemo(() => (project ? validateCues(project.cues) : []), [project]);
  const blocking = issues.filter((i) => i.severity === "error");
  const running = job.status === "running";
  const hasTranslation = Boolean(project?.cues.some((c) => c.translation !== null));
  const missingModels = [model, ...(translateTo ? [translationModel] : [])].filter(
    (m) => diagnostics && !installed.has(m),
  );

  const start = async () => {
    if (!bridge || !source) return;
    const config: JobConfig = {
      source,
      language,
      task: "transcribe",
      model,
      device,
      translateTo,
      translationModel,
      translationDevice,
      formats,
    };
    dispatch({ type: "start" });
    setNotice(null);
    try {
      stopListening.current = await bridge.startJob(config, {
        onEvent: (event) => dispatch({ type: "event", event }),
        onLog: (line) => dispatch({ type: "log", line }),
        onExit: (code, cancelled) => dispatch({ type: "exit", code, cancelled }),
      });
    } catch (err) {
      dispatch({ type: "fail", message: String(err) });
    }
  };

  const edit = (next: LocalSubProject) => {
    setProject(next);
    setDirty(true);
  };

  const exportFiles = async () => {
    if (!bridge || !project || blocking.length) return;
    const chosen = await bridge.pickSaveBase(defaultOutputBase(project.source_media ?? source));
    if (!chosen) return;
    try {
      const written = await bridge.exportProject(project, outputBaseFromSavePath(chosen), formats);
      setDirty(false);
      setNotice(`Saved ${written.length} file(s): ${written.map(baseName).join(", ")}`);
    } catch (err) {
      setNotice(`Export failed: ${String(err)}`);
    }
  };

  const openProject = async () => {
    if (!bridge) return;
    const path = await bridge.pickProject();
    if (!path) return;
    try {
      const data = await bridge.loadProject(path);
      if (!isProject(data)) throw new Error("not a LocalSub project file");
      setProject(normalizeProject(data));
      setSource(data.source_media ?? source);
      setDirty(false);
      setNotice(`Opened ${baseName(path)}`);
    } catch (err) {
      setNotice(`Cannot open project: ${String(err)}`);
    }
  };

  const toggleFormat = (f: ExportFormat) =>
    setFormats((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));

  const activeIndex = STEPS.findIndex((s) => s.stage === job.stage);

  return (
    <main className="app-shell">
      <header className="header">
        <div>
          <p className="eyebrow">LOCAL · PRIVATE · WINDOWS 11</p>
          <h1>LocalSub NPU</h1>
          <p className="subtitle">Offline subtitles and EN↔VI, JA → EN/VI translation on the Intel NPU.</p>
        </div>
        <span className={`status-chip${bridge?.preview ? " preview" : ""}`}>
          {bridge?.preview ? "Browser preview · sample data" : "Desktop · Wave 3"}
        </span>
      </header>

      <section className="grid" aria-label="New subtitle job">
        <article className="panel">
          <h2>1. Media</h2>
          <button type="button" className="drop-zone" onClick={async () => setSource((await bridge?.pickMedia()) ?? source)} disabled={!bridge || running}>
            <span className="drop-title">{source ? baseName(source) : "Choose video or audio"}</span>
            <span className="drop-hint">MP4, MKV, MOV, AVI, WEBM, MP3, WAV, M4A</span>
          </button>
          {source && <p className="path" title={source}>{source}</p>}
          <button type="button" className="secondary full" onClick={openProject} disabled={!bridge || running}>
            Open saved project (.localsub.json)
          </button>
        </article>

        <article className="panel">
          <h2>2. Configure</h2>
          <div className="field-row">
            <div className="field-group">
              <label htmlFor="language">Spoken language</label>
              <select id="language" value={language} onChange={(e) => setLanguage(e.target.value as SpokenLanguage)}>
                <option value="auto">Auto detect</option>
                <option value="en">English</option>
                <option value="ja">Japanese</option>
                <option value="vi">Vietnamese</option>
              </select>
            </div>
            <div className="field-group">
              <label htmlFor="translate-to">Translate to</label>
              <select id="translate-to" value={translateTo} onChange={(e) => setTranslateTo(e.target.value as TargetLanguage)}>
                <option value="">No translation</option>
                <option value="vi">Vietnamese</option>
                <option value="en">English</option>
              </select>
            </div>
          </div>
          <div className="field-group">
            <label htmlFor="model">Speech model · device</label>
            <div className="field-row tight">
              <select id="model" value={model} onChange={(e) => setModel(e.target.value)}>
                {WHISPER_MODELS.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}{diagnostics && !installed.has(m.id) ? " (not installed)" : ""}</option>
                ))}
              </select>
              <select aria-label="Speech device" value={device} onChange={(e) => setDevice(e.target.value as DevicePreference)}>
                {DEVICES.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>
            <p className="help-text">{WHISPER_MODELS.find((m) => m.id === model)?.hint} Auto = NPU → GPU → CPU.</p>
            {language === "ja" && JA_WEAK_MODELS.has(model) && (
              <p className="warn">For Japanese, whisper-large-v3-turbo-int8 (or medium) is recommended for accuracy.</p>
            )}
          </div>
          {translateTo && (
            <div className="field-group">
              <label htmlFor="llm">Translation model · device</label>
              <div className="field-row tight">
                <select id="llm" value={translationModel} onChange={(e) => setTranslationModel(e.target.value)}>
                  {LLM_MODELS.map((m) => (
                    <option key={m.id} value={m.id}>{m.label}{diagnostics && !installed.has(m.id) ? " (not installed)" : ""}</option>
                  ))}
                </select>
                <select aria-label="Translation device" value={translationDevice} onChange={(e) => setTranslationDevice(e.target.value as DevicePreference)}>
                  {DEVICES.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
              </div>
              <p className="help-text">{LLM_MODELS.find((m) => m.id === translationModel)?.hint}</p>
            </div>
          )}
          <fieldset className="formats">
            <legend>Export formats</legend>
            {FORMATS.map((f) => (
              <label key={f} className="check">
                <input type="checkbox" checked={formats.includes(f)} onChange={() => toggleFormat(f)} />
                {f.toUpperCase()}
              </label>
            ))}
          </fieldset>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h2>3. Runtime</h2>
            <button type="button" className="link" onClick={runDiagnostics} disabled={diagBusy || !bridge}>
              {diagBusy ? "Checking…" : "Re-check"}
            </button>
          </div>
          {diagError && <p className="error">{diagError}</p>}
          <dl>
            <div><dt>FFmpeg</dt><dd className={diagnostics?.ffmpeg_available ? "ok" : "bad"}>{diagnostics ? (diagnostics.ffmpeg_available ? "Found" : "Missing") : "—"}</dd></div>
            <div><dt>OpenVINO</dt><dd>{diagnostics?.openvino_version?.split("-")[0] ?? "—"}</dd></div>
            <div><dt>Devices</dt><dd>{diagnostics?.available_devices.join(" · ") || "—"}</dd></div>
            <div><dt>Auto resolves to</dt><dd className={diagnostics?.device_resolution?.resolved === "NPU" ? "ok" : ""}>{diagnostics?.device_resolution?.resolved ?? "—"}</dd></div>
            <div><dt>Speech used</dt><dd>{job.asrDevice ?? "—"}</dd></div>
            <div><dt>Translation used</dt><dd>{job.translationDevice ?? "—"}</dd></div>
          </dl>
          {(job.asrFallback || job.translationFallback) && (
            <p className="help-text">Fallback: {job.asrFallback ?? job.translationFallback}</p>
          )}
          {missingModels.length > 0 && (
            <p className="warn">Download first: <code className="inline">{`python -m app.main models download ${missingModels[0]}`}</code></p>
          )}
        </article>
      </section>

      <section className="panel action-panel" aria-label="Run job">
        <ol className="stepper">
          {STEPS.filter((s) => s.stage !== "translating" || translateTo).map((s) => {
            const idx = STEPS.findIndex((x) => x.stage === s.stage);
            const state =
              job.status === "completed" ? "done"
              : idx < activeIndex ? "done"
              : idx === activeIndex ? (job.status === "failed" ? "failed" : "active")
              : "";
            return <li key={s.stage} className={state}>{s.label}{s.stage === "translating" && job.progress && job.stage === "translating" ? ` ${job.progress.done}/${job.progress.total}` : ""}</li>;
          })}
        </ol>
        <div className="actions">
          {running ? (
            <button type="button" className="danger" onClick={() => void bridge?.cancelJob()}>Cancel</button>
          ) : (
            <button type="button" onClick={start} disabled={!source || !bridge || formats.length === 0 || missingModels.length > 0}>
              Generate subtitles
            </button>
          )}
        </div>
        {job.status === "failed" && <p className="error wide">{job.error}</p>}
        {job.status === "cancelled" && <p className="warn wide">Job cancelled. No files were changed.</p>}
        {job.status === "completed" && job.outputs.length > 0 && (
          <p className="success wide">Wrote {job.outputs.map(baseName).join(", ")}</p>
        )}
        {job.logs.length > 0 && (
          <details className="wide">
            <summary>Worker log ({job.logs.length})</summary>
            <pre>{job.logs.join("\n")}</pre>
          </details>
        )}
      </section>

      {project && (
        <section className="panel editor" aria-label="Subtitle editor">
          <div className="panel-title">
            <div>
              <h2>Subtitle editor {dirty && <span className="badge">unsaved</span>}</h2>
              <p className="help-text">
                {project.cues.length} cues · {project.source_language ?? "?"}
                {project.target_language ? ` → ${project.target_language}` : ""} · edit text or times, then export.
              </p>
            </div>
            <button type="button" onClick={exportFiles} disabled={blocking.length > 0 || formats.length === 0}>
              Export {formats.map((f) => f.toUpperCase()).join(" + ")}
            </button>
          </div>
          {notice && <p className="notice">{notice}</p>}
          {blocking.length > 0 && <p className="error">Fix {blocking.length} issue(s) before exporting.</p>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th><th>Start</th><th>End</th><th>Source</th>
                  {hasTranslation && <th>Translation</th>}
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {project.cues.map((cue, i) => {
                  const rowIssues = issues.filter((x) => x.index === i);
                  return (
                    <tr key={`${i}-${cue.start_ms}`} className={rowIssues.some((x) => x.severity === "error") ? "row-error" : ""}>
                      <td className="num">{i + 1}</td>
                      <td><TimeField label={`Cue ${i + 1} start`} value={cue.start_ms} onCommit={(ms) => edit(updateCue(project, i, { start_ms: ms }))} /></td>
                      <td><TimeField label={`Cue ${i + 1} end`} value={cue.end_ms} onCommit={(ms) => edit(updateCue(project, i, { end_ms: ms }))} /></td>
                      <td>
                        <textarea aria-label={`Cue ${i + 1} source`} value={cue.text} rows={rowsFor(cue.text)} onChange={(e) => edit(updateCue(project, i, { text: e.target.value }))} />
                        {rowIssues.map((x) => <span key={x.message} className={`issue ${x.severity}`}>{x.message}</span>)}
                      </td>
                      {hasTranslation && (
                        <td><textarea aria-label={`Cue ${i + 1} translation`} value={cue.translation ?? ""} rows={rowsFor(cue.translation ?? "")} onChange={(e) => edit(updateCue(project, i, { translation: e.target.value }))} /></td>
                      )}
                      <td className="row-actions">
                        <button type="button" className="link" onClick={() => edit(splitCue(project, i))} title="Split at midpoint">Split</button>
                        <button type="button" className="link" onClick={() => edit(mergeWithNext(project, i))} disabled={i === project.cues.length - 1} title="Merge with next cue">Merge</button>
                        <button type="button" className="link danger-text" onClick={() => edit(deleteCue(project, i))} title="Delete cue">Delete</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </main>
  );
}
