import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { HistoryPanel } from "./components/HistoryPanel";
import { ModelManager } from "./components/ModelManager";
import { MonitorPanel, type StageMarker } from "./components/MonitorPanel";
import { estimateRemainingMs, formatDuration, formatLocal, offsetLabel } from "./lib/format";
import {
  errorMessage,
  I18nContext,
  initialLocale,
  LOCALE_STORAGE_KEY,
  safeStorage,
  translate,
  type Locale,
  type MessageKey,
  type Translate,
} from "./lib/i18n";
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
  { id: "whisper-small-int8", label: "Whisper small" },
  { id: "whisper-base-int8", label: "Whisper base" },
  { id: "whisper-tiny-int8", label: "Whisper tiny" },
  { id: "whisper-medium-int8", label: "Whisper medium" },
  { id: "whisper-large-v3-turbo-int8", label: "Whisper large-v3 turbo" },
  { id: "whisper-large-v3-int8", label: "Whisper large-v3" },
];
const LLM_MODELS = [
  { id: "qwen3-8b-int4-cw", label: "Qwen3 8B" },
  { id: "qwen2.5-7b-instruct-int4", label: "Qwen2.5 7B" },
  { id: "qwen3-4b-int4", label: "Qwen3 4B" },
  { id: "qwen2.5-1.5b-instruct-int4", label: "Qwen2.5 1.5B" },
  { id: "qwen3-0.6b-int4", label: "Qwen3 0.6B" },
];
const DEVICES: Array<{ value: DevicePreference; label: string }> = [
  { value: "auto", label: "device.auto" },
  { value: "NPU", label: "NPU" },
  { value: "GPU", label: "GPU" },
  { value: "CPU", label: "CPU" },
];
const JA_WEAK_MODELS = new Set(["whisper-tiny-int8", "whisper-base-int8", "whisper-small-int8"]);
const FORMATS: ExportFormat[] = ["srt", "vtt", "txt", "json"];
const STEPS: WorkerStage[] = [
  "probing",
  "extracting_audio",
  "loading_model",
  "transcribing",
  "translating",
  "rendering",
];

const deviceLabel = (t: Translate, label: string) => (label.startsWith("device.") ? t(label as MessageKey) : label);

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
  const [locale, setLocale] = useState<Locale>(() => initialLocale());
  const t = useCallback<Translate>((key, params) => translate(locale, key, params), [locale]);
  const i18n = useMemo(() => ({ locale, t }), [locale, t]);
  const [modelStatus, setModelStatus] = useState<Record<string, string>>({});
  const [diagAt, setDiagAt] = useState<Date | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
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

  useEffect(() => {
    document.documentElement.lang = locale;
    safeStorage()?.setItem(LOCALE_STORAGE_KEY, locale);
  }, [locale]);

  const runDiagnostics = useCallback(async () => {
    if (!bridge) return;
    setDiagBusy(true);
    setDiagError(null);
    try {
      setDiagnostics(await bridge.diagnostics());
      setDiagAt(new Date());
      const list = await bridge.models().catch(() => []);
      setModelStatus(Object.fromEntries(list.map((m) => [m.id, m.status])));
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
    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      setHistoryKey((k) => k + 1);
    }
  }, [job.status, job.project]);

  const installed = useMemo(() => new Set(diagnostics?.installed_models ?? []), [diagnostics]);
  const issues = useMemo(() => (project ? validateCues(project.cues) : []), [project]);
  const blocking = issues.filter((i) => i.severity === "error");
  const running = job.status === "running";
  // Stage changes of the current job, drawn as markers on the load chart.
  const [markers, setMarkers] = useState<StageMarker[]>([]);
  useEffect(() => {
    if (!job.stage || !STEPS.includes(job.stage as WorkerStage)) return;
    setMarkers((cur) => (cur.length && cur[cur.length - 1].stage === job.stage ? cur : [...cur, { at: Date.now(), stage: job.stage as string }]));
  }, [job.stage]);
  // When the current progress stage started, for a linear ETA.
  const progressStart = useRef<{ stage: string | null; at: number } | null>(null);
  if (job.progress && progressStart.current?.stage !== job.progress.stage) {
    progressStart.current = { stage: job.progress.stage, at: Date.now() };
  }
  const progressText = (stage: string): string => {
    const p = job.progress;
    if (!p || job.stage !== stage || p.stage !== stage || !p.total) return "";
    const shown = stage === "transcribing" ? `${Math.min(100, Math.round((p.done / p.total) * 100))}%` : `${p.done}/${p.total}`;
    const eta = estimateRemainingMs(p.done, p.total, Date.now() - (progressStart.current?.at ?? Date.now()));
    return ` ${shown}${eta === null ? "" : ` · ${t("job.eta", { eta: formatDuration(eta) })}`}`;
  };
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
    setMarkers([]);
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
      setNotice(t("editor.saved", { n: written.length, files: written.map(baseName).join(", ") }));
    } catch (err) {
      setNotice(t("editor.exportFailed", { error: errorMessage(t, String(err)) }));
    }
  };

  const openProject = async () => {
    if (!bridge) return;
    const path = await bridge.pickProject();
    if (!path) return;
    try {
      const data = await bridge.loadProject(path);
      if (!isProject(data)) throw new Error(t("editor.notProject"));
      setProject(normalizeProject(data));
      setSource(data.source_media ?? source);
      setDirty(false);
      setNotice(t("editor.opened", { file: baseName(path) }));
    } catch (err) {
      setNotice(t("editor.openFailed", { error: String(err) }));
    }
  };

  const toggleFormat = (f: ExportFormat) =>
    setFormats((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));

  const activeIndex = STEPS.findIndex((s) => s === job.stage);
  const modelSuffix = (id: string) =>
    !diagnostics || installed.has(id)
      ? ""
      : modelStatus[id] === "incomplete"
        ? t("config.incomplete")
        : t("config.notInstalled");

  return (
    <I18nContext.Provider value={i18n}>
    <main className="app-shell">
      <header className="header">
        <div>
          <p className="eyebrow">{t("app.eyebrow")}</p>
          <h1>LocalSub NPU</h1>
          <p className="subtitle">{t("app.subtitle")}</p>
        </div>
        <div className="header-side">
          <div className="lang-toggle" role="group" aria-label={t("app.language")}>
            {(["vi", "en"] as Locale[]).map((l) => (
              <button key={l} type="button" className={locale === l ? "on" : ""} aria-pressed={locale === l} onClick={() => setLocale(l)}>
                {l.toUpperCase()}
              </button>
            ))}
          </div>
          <span className={`status-chip${bridge?.preview ? " preview" : ""}`}>
            {bridge?.preview ? t("app.chipPreview") : t("app.chipDesktop")}
          </span>
        </div>
      </header>

      <section className="grid" aria-label={t("job.generate")}>
        <article className="panel">
          <h2>{t("media.title")}</h2>
          <button type="button" className="drop-zone" onClick={async () => setSource((await bridge?.pickMedia()) ?? source)} disabled={!bridge || running}>
            <span className="drop-title">{source ? baseName(source) : t("media.choose")}</span>
            <span className="drop-hint">{t("media.hint")}</span>
          </button>
          {source && <p className="path" title={source}>{source}</p>}
          <button type="button" className="secondary full" onClick={openProject} disabled={!bridge || running}>
            {t("media.openProject")}
          </button>
        </article>

        <article className="panel">
          <h2>{t("config.title")}</h2>
          <div className="field-row">
            <div className="field-group">
              <label htmlFor="language">{t("config.spoken")}</label>
              <select id="language" value={language} onChange={(e) => setLanguage(e.target.value as SpokenLanguage)}>
                <option value="auto">{t("config.auto")}</option>
                <option value="en">{t("lang.en")}</option>
                <option value="ja">{t("lang.ja")}</option>
                <option value="vi">{t("lang.vi")}</option>
              </select>
            </div>
            <div className="field-group">
              <label htmlFor="translate-to">{t("config.translateTo")}</label>
              <select id="translate-to" value={translateTo} onChange={(e) => setTranslateTo(e.target.value as TargetLanguage)}>
                <option value="">{t("config.noTranslation")}</option>
                <option value="vi">{t("lang.vi")}</option>
                <option value="en">{t("lang.en")}</option>
              </select>
            </div>
          </div>
          <div className="field-group">
            <label htmlFor="model">{t("config.speechModel")}</label>
            <div className="field-row tight">
              <select id="model" value={model} onChange={(e) => setModel(e.target.value)}>
                {WHISPER_MODELS.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}{modelSuffix(m.id)}</option>
                ))}
              </select>
              <select aria-label={t("config.speechDevice")} value={device} onChange={(e) => setDevice(e.target.value as DevicePreference)}>
                {DEVICES.map((d) => <option key={d.value} value={d.value}>{deviceLabel(t, d.label)}</option>)}
              </select>
            </div>
            <p className="help-text">{t(`hint.${model}` as MessageKey)} {t("config.autoOrder")}</p>
            {language === "ja" && JA_WEAK_MODELS.has(model) && <p className="warn">{t("config.jaWarn")}</p>}
          </div>
          {translateTo && (
            <div className="field-group">
              <label htmlFor="llm">{t("config.translationModel")}</label>
              <div className="field-row tight">
                <select id="llm" value={translationModel} onChange={(e) => setTranslationModel(e.target.value)}>
                  {LLM_MODELS.map((m) => (
                    <option key={m.id} value={m.id}>{m.label}{modelSuffix(m.id)}</option>
                  ))}
                </select>
                <select aria-label={t("config.translationDevice")} value={translationDevice} onChange={(e) => setTranslationDevice(e.target.value as DevicePreference)}>
                  {DEVICES.map((d) => <option key={d.value} value={d.value}>{deviceLabel(t, d.label)}</option>)}
                </select>
              </div>
              <p className="help-text">{t(`hint.${translationModel}` as MessageKey)}</p>
            </div>
          )}
          <fieldset className="formats">
            <legend>{t("config.formats")}</legend>
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
            <h2>{t("runtime.title")}</h2>
            <button type="button" className="link" onClick={runDiagnostics} disabled={diagBusy || !bridge}>
              {diagBusy ? t("runtime.checking") : t("runtime.recheck")}
            </button>
          </div>
          {diagError && <p className="error">{errorMessage(t, diagError)}</p>}
          <dl>
            <div><dt>{t("runtime.ffmpeg")}</dt><dd className={diagnostics?.ffmpeg_available ? "ok" : "bad"}>{diagnostics ? (diagnostics.ffmpeg_available ? t("runtime.found") : t("runtime.missing")) : "—"}</dd></div>
            <div><dt>{t("runtime.openvino")}</dt><dd>{diagnostics?.openvino_version?.split("-")[0] ?? "—"}</dd></div>
            <div><dt>{t("runtime.devices")}</dt><dd>{diagnostics?.available_devices.join(" · ") || "—"}</dd></div>
            <div><dt>{t("runtime.autoResolves")}</dt><dd className={diagnostics?.device_resolution?.resolved === "NPU" ? "ok" : ""}>{diagnostics?.device_resolution?.resolved ?? "—"}</dd></div>
            <div><dt>{t("runtime.speechUsed")}</dt><dd>{job.asrDevice ?? "—"}</dd></div>
            <div><dt>{t("runtime.translationUsed")}</dt><dd>{job.translationDevice ?? "—"}</dd></div>
          </dl>
          {diagnostics && !diagnostics.ffmpeg_available && <p className="warn">{t("runtime.ffmpegHint")}</p>}
          {(job.asrFallback || job.translationFallback) && (
            <p className="help-text">{t("runtime.fallback", { reason: job.asrFallback ?? job.translationFallback ?? "" })}</p>
          )}
          {missingModels.length > 0 && <p className="warn">{t("runtime.needModels", { models: missingModels.join(", ") })}</p>}
          {diagAt && (
            <p className="help-text">{t("runtime.checkedAt", { time: formatLocal(diagAt.toISOString(), locale === "vi" ? "vi-VN" : "en-GB"), tz: offsetLabel(diagAt) })}</p>
          )}
        </article>
      </section>

      <section className="panel action-panel" aria-label={t("job.generate")}>
        <ol className="stepper">
          {STEPS.filter((s) => s !== "translating" || translateTo).map((s) => {
            const idx = STEPS.indexOf(s);
            const state =
              job.status === "completed" ? "done"
              : idx < activeIndex ? "done"
              : idx === activeIndex ? (job.status === "failed" ? "failed" : "active")
              : "";
            return <li key={s} className={state}>{t(`steps.${s}` as MessageKey)}{progressText(s)}</li>;
          })}
        </ol>
        <div className="actions">
          {running ? (
            <button type="button" className="danger" onClick={() => void bridge?.cancelJob()}>{t("job.cancel")}</button>
          ) : (
            <button type="button" onClick={start} disabled={!source || !bridge || formats.length === 0 || missingModels.length > 0}>
              {t("job.generate")}
            </button>
          )}
        </div>
        {job.status === "failed" && <p className="error wide">{errorMessage(t, job.error)}</p>}
        {job.status === "cancelled" && <p className="warn wide">{t("job.cancelled")}</p>}
        {job.status === "completed" && job.outputs.length > 0 && (
          <p className="success wide">{t("job.wrote", { files: job.outputs.map(baseName).join(", ") })}</p>
        )}
        {job.logs.length > 0 && (
          <details className="wide">
            <summary>{t("job.log", { n: job.logs.length })}</summary>
            <pre>{job.logs.join("\n")}</pre>
          </details>
        )}
      </section>

      {project && (
        <section className="panel editor" aria-label={t("editor.title")}>
          <div className="panel-title">
            <div>
              <h2>{t("editor.title")} {dirty && <span className="badge">{t("editor.unsaved")}</span>}</h2>
              <p className="help-text">
                {t("editor.summary", { n: project.cues.length, src: project.source_language ?? "?", dst: project.target_language ? ` → ${project.target_language}` : "" })}
              </p>
            </div>
            <button type="button" onClick={exportFiles} disabled={blocking.length > 0 || formats.length === 0}>
              {t("editor.export", { formats: formats.map((f) => f.toUpperCase()).join(" + ") })}
            </button>
          </div>
          {notice && <p className="notice">{notice}</p>}
          {blocking.length > 0 && <p className="error">{t("editor.fix", { n: blocking.length })}</p>}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th><th>{t("editor.col.start")}</th><th>{t("editor.col.end")}</th><th>{t("editor.col.source")}</th>
                  {hasTranslation && <th>{t("editor.col.translation")}</th>}
                  <th aria-label={t("editor.col.actions")} />
                </tr>
              </thead>
              <tbody>
                {project.cues.map((cue, i) => {
                  const rowIssues = issues.filter((x) => x.index === i);
                  const n = i + 1;
                  return (
                    <tr key={`${i}-${cue.start_ms}`} className={rowIssues.some((x) => x.severity === "error") ? "row-error" : ""}>
                      <td className="num">{n}</td>
                      <td><TimeField label={t("editor.start", { n })} value={cue.start_ms} onCommit={(ms) => edit(updateCue(project, i, { start_ms: ms }))} /></td>
                      <td><TimeField label={t("editor.end", { n })} value={cue.end_ms} onCommit={(ms) => edit(updateCue(project, i, { end_ms: ms }))} /></td>
                      <td>
                        <textarea aria-label={t("editor.sourceLabel", { n })} value={cue.text} rows={rowsFor(cue.text)} onChange={(e) => edit(updateCue(project, i, { text: e.target.value }))} />
                        {rowIssues.map((x) => <span key={x.code} className={`issue ${x.severity}`}>{t(`issue.${x.code}` as MessageKey, { limit: x.limit ?? "" })}</span>)}
                      </td>
                      {hasTranslation && (
                        <td><textarea aria-label={t("editor.translationLabel", { n })} value={cue.translation ?? ""} rows={rowsFor(cue.translation ?? "")} onChange={(e) => edit(updateCue(project, i, { translation: e.target.value }))} /></td>
                      )}
                      <td className="row-actions">
                        <button type="button" className="link" onClick={() => edit(splitCue(project, i))} title={t("editor.splitTitle")}>{t("editor.split")}</button>
                        <button type="button" className="link" onClick={() => edit(mergeWithNext(project, i))} disabled={i === project.cues.length - 1} title={t("editor.mergeTitle")}>{t("editor.merge")}</button>
                        <button type="button" className="link danger-text" onClick={() => edit(deleteCue(project, i))} title={t("editor.deleteTitle")}>{t("editor.delete")}</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <ModelManager
        bridge={bridge}
        selected={[model, ...(translateTo ? [translationModel] : [])]}
        jobRunning={running}
        onChanged={() => void runDiagnostics()}
      />
      <MonitorPanel bridge={bridge} markers={markers} />
      <HistoryPanel bridge={bridge} refreshKey={historyKey} />
    </main>
    </I18nContext.Provider>
  );
}
