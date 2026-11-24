import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import {
  Activity,
  AudioLines,
  Boxes,
  CircleCheck,
  CircleX,
  ChevronDown,
  Circle,
  Clapperboard,
  Download,
  FileVideo,
  FolderOpen,
  History,
  Loader2,
  Merge,
  Monitor,
  Moon,
  Play,
  Redo2,
  RefreshCw,
  Scissors,
  Sparkles,
  Square,
  TriangleAlert,
  Sun,
  Subtitles,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { AnalysisPanel } from "./components/AnalysisPanel";
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
  maxCpsFor,
  readingSpeed,
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
  TranslationDevice,
  Diagnostics,
  ExportFormat,
  JobConfig,
  JobView,
  LocalSubProject,
  SpokenLanguage,
  TargetLanguage,
  WorkerStage,
} from "./lib/types";
import { DEFAULT_RUN_SETTINGS, parseRunSettings, RUN_SETTINGS_KEY, type SavedRunSettings } from "./lib/settings";
import { choicesFrom, deviceSupported, FALLBACK_MODELS, findChoice, languageSupported, type ModelChoice } from "./lib/catalog";
import { getBridge, type WorkerBridge } from "./lib/worker";

const KEEP_AWAKE_KEY = "localsub.keepAwake";
const AUTO_SAVE_KEY = "localsub.autoSave";
const THEME_KEY = "localsub.theme";
type Theme = "system" | "light" | "dark";
const THEMES: Array<{ id: Theme; icon: ReactNode }> = [
  { id: "system", icon: <Monitor size={16} aria-hidden="true" /> },
  { id: "light", icon: <Sun size={16} aria-hidden="true" /> },
  { id: "dark", icon: <Moon size={16} aria-hidden="true" /> },
];
const DEVICES: Array<{ value: DevicePreference; label: string }> = [
  { value: "auto", label: "device.auto" },
  { value: "NPU", label: "NPU" },
  { value: "GPU", label: "GPU" },
  { value: "CPU", label: "CPU" },
];
const TRANSLATION_DEVICES: Array<{ value: TranslationDevice; label: string }> = DEVICES;
type Tab = "editor" | "monitor" | "history" | "analysis" | "models";
const TABS: Tab[] = ["editor", "monitor", "history", "analysis", "models"];
const TAB_ICONS: Record<Tab, ReactNode> = {
  editor: <Subtitles size={18} aria-hidden="true" />,
  monitor: <Activity size={18} aria-hidden="true" />,
  history: <History size={18} aria-hidden="true" />,
  analysis: <Sparkles size={18} aria-hidden="true" />,
  models: <Boxes size={18} aria-hidden="true" />,
};
// shortcut: rows are appended in chunks while scrolling (bounded first render and tab stops),
// not windowed; switch to a virtualised list if episodes with thousands of cues get slow.
const ROW_CHUNK = 150;
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
  // ~22 characters per line at the editor's minimum column width, plus one spare line.
  return Math.min(7, Math.max(2, Math.ceil(text.length / 22) + 1));
}

/** One finished file of a queue run, for the summary shown when the queue ends. */
interface RunResult {
  file: string;
  status: JobView["status"];
  asr: string | null;
  mt: string | null;
  fallback: string | null;
  ms: number;
  project: string | null; // .localsub.json written by the job, to reopen it from the summary
}

/** Job settings frozen when a file is queued, so later changes do not retarget it. */
type RunConfig = Omit<JobConfig, "source">;
interface QueueItem {
  file: string;
  config: RunConfig;
}

function TimeField(props: { id: string; label: string; invalidText: string; value: number; onCommit: (ms: number) => void }) {
  const [draft, setDraft] = useState(formatTime(props.value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => setDraft(formatTime(props.value)), [props.value]);
  return (
    <>
      <input
        aria-label={props.label}
        aria-invalid={invalid}
        aria-describedby={invalid ? `${props.id}-err` : undefined}
        className={`time-input${invalid ? " invalid" : ""}`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur(); // commit like leaving the field
        }}
        onBlur={() => {
          const ms = parseTime(draft);
          setInvalid(ms === null);
          if (ms !== null) props.onCommit(ms);
        }}
      />
      {invalid && <span className="issue error" id={`${props.id}-err`}>{props.invalidText}</span>}
    </>
  );
}

/** Alt+Up/Down in a cue text box moves to the same column of the previous/next visible cue. */
function moveBetweenCues(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
  if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
  const col = e.currentTarget.dataset.col;
  const boxes = Array.from(document.querySelectorAll<HTMLTextAreaElement>(`.cue-table textarea[data-col="${col}"]`));
  const next = boxes[boxes.indexOf(e.currentTarget) + (e.key === "ArrowUp" ? -1 : 1)];
  if (!next) return;
  e.preventDefault();
  next.focus();
}

export function App() {
  const [locale, setLocale] = useState<Locale>(() => initialLocale());
  const t = useCallback<Translate>((key, params) => translate(locale, key, params), [locale]);
  const i18n = useMemo(() => ({ locale, t }), [locale, t]);
  const [modelStatus, setModelStatus] = useState<Record<string, string>>({});
  const [choices, setChoices] = useState<ModelChoice[]>(FALLBACK_MODELS);
  const [keepAwake, setKeepAwake] = useState(() => safeStorage()?.getItem(KEEP_AWAKE_KEY) !== "0");
  const [autoSave, setAutoSave] = useState(() => safeStorage()?.getItem(AUTO_SAVE_KEY) === "1");
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = safeStorage()?.getItem(THEME_KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  });
  const [diagAt, setDiagAt] = useState<Date | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [bridge, setBridge] = useState<WorkerBridge | null>(null);
  const [source, setSource] = useState<string | null>(null);
  // Files waiting to run after the current job, each with the settings in place when it starts.
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [bannersHidden, setBannersHidden] = useState(false);
  const fileStartedAt = useRef(0);
  // Files finished since the last manual start: the queue position and the end-of-queue summary.
  const [runLog, setRunLog] = useState<RunResult[]>([]);
  // Overnight queue control: settings of the running file, "stop after this file", "skip this file".
  const [runConfig, setRunConfig] = useState<RunConfig | null>(null);
  const [stopAfter, setStopAfter] = useState(false);
  const skipCurrent = useRef(false);
  const cancelMenu = useRef<HTMLDetailsElement>(null);
  const [keptEdits, setKeptEdits] = useState(false);
  // Last-used run settings, read once at launch.
  const [saved] = useState(() => parseRunSettings(safeStorage()?.getItem(RUN_SETTINGS_KEY)));
  const [language, setLanguage] = useState<SpokenLanguage>(saved.language);
  const [model, setModel] = useState(saved.model);
  const [device, setDevice] = useState<DevicePreference>(saved.device);
  const [translateTo, setTranslateTo] = useState<TargetLanguage>(saved.translateTo);
  const [translationModel, setTranslationModel] = useState(saved.translationModel);
  const [translationDevice, setTranslationDevice] = useState<TranslationDevice>(saved.translationDevice);
  const [tab, setTab] = useState<Tab>("editor");
  const [formats, setFormats] = useState<ExportFormat[]>(saved.formats);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [diagError, setDiagError] = useState<string | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const [job, dispatch] = useReducer(jobReducer, initialJob);
  const [project, setProject] = useState<LocalSubProject | null>(null);
  const [dirty, setDirty] = useState(false);
  // Snapshots for Undo/Redo of cue edits; a run of typing in one box is one step.
  const [undoStack, setUndoStack] = useState<LocalSubProject[]>([]);
  const [redoStack, setRedoStack] = useState<LocalSubProject[]>([]);
  const lastEditKey = useRef<string | null>(null);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [rowLimit, setRowLimit] = useState(ROW_CHUNK);
  const moreRowsRef = useRef<HTMLTableRowElement | null>(null);
  const quickSaveRef = useRef<() => void>(() => undefined);
  const [jumpTo, setJumpTo] = useState("");
  // Where the editor's project was last written (job output, opened file or export), for auto-save.
  const [projectBase, setProjectBase] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const stopListening = useRef<(() => void) | null>(null);

  useEffect(() => {
    void getBridge().then(setBridge);
  }, []);

  useEffect(() => {
    safeStorage()?.setItem(KEEP_AWAKE_KEY, keepAwake ? "1" : "0");
    safeStorage()?.setItem(AUTO_SAVE_KEY, autoSave ? "1" : "0");
  }, [keepAwake, autoSave]);

  useEffect(() => {
    const settings: SavedRunSettings = { language, model, device, translateTo, translationModel, translationDevice, formats };
    safeStorage()?.setItem(RUN_SETTINGS_KEY, JSON.stringify(settings));
  }, [language, model, device, translateTo, translationModel, translationDevice, formats]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    safeStorage()?.setItem(THEME_KEY, theme);
  }, [theme]);

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
      if (list.length) {
        setChoices(choicesFrom(list));
        // A remembered model that is no longer in the catalog falls back to the default.
        const known = (kind: string, id: string) => list.some((m) => m.kind === kind && m.id === id);
        setModel((id) => (known("asr", id) ? id : DEFAULT_RUN_SETTINGS.model));
        setTranslationModel((id) => (known("translation", id) ? id : DEFAULT_RUN_SETTINGS.translationModel));
      }
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
    if (job.status !== "running") {
      stopListening.current?.();
      stopListening.current = null;
    }
    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      setHistoryKey((k) => k + 1);
    }
  }, [job.status]);

  const installed = useMemo(() => new Set(diagnostics?.installed_models ?? []), [diagnostics]);
  const issues = useMemo(() => (project ? validateCues(project.cues) : []), [project]);
  const blocking = issues.filter((i) => i.severity === "error");
  const issuesByRow = useMemo(() => {
    const byRow = new Map<number, typeof issues>();
    for (const issue of issues) byRow.set(issue.index, [...(byRow.get(issue.index) ?? []), issue]);
    return byRow;
  }, [issues]);
  const running = job.status === "running";
  // Stage changes of the current job, drawn as markers on the load chart.
  const [markers, setMarkers] = useState<StageMarker[]>([]);
  useEffect(() => {
    if (!job.stage || !STEPS.includes(job.stage as WorkerStage)) return;
    setMarkers((cur) => (cur.length && cur[cur.length - 1].stage === job.stage ? cur : [...cur, { at: Date.now(), stage: job.stage as string }]));
  }, [job.stage]);
  // When the current progress stage started, for a linear ETA.
  // Per stage, because overlapped translation interleaves its events with transcription.
  // ETA baseline per stage, taken when the stage becomes the current one, so lines that
  // overlapped translation finished earlier do not skew the rate.
  const progressStart = useRef<Record<string, { at: number; done: number }>>({});
  if (!running) progressStart.current = {};
  if (job.stage && job.progressByStage[job.stage] && !progressStart.current[job.stage]) {
    progressStart.current[job.stage] = { at: Date.now(), done: job.progressByStage[job.stage].done };
  }
  // Translation may already run (on the GPU) while speech recognition is still going.
  const overlapping = running && job.stage === "transcribing" && Boolean(job.progressByStage.translating);
  const progressText = (stage: string): string => {
    const p = job.progressByStage[stage];
    const live = job.stage === stage || (stage === "translating" && overlapping);
    if (!p || !running || !live || !p.total) return "";
    const shown = stage === "transcribing" ? `${Math.min(100, Math.round((p.done / p.total) * 100))}%` : `${p.done}/${p.total}`;
    // While overlapping, the translation total still grows, so no ETA for it.
    if (job.stage !== stage) return ` ${shown}`;
    const base = progressStart.current[stage] ?? { at: Date.now(), done: 0 };
    const eta = estimateRemainingMs(p.done - base.done, p.total - base.done, Date.now() - base.at);
    return ` ${shown}${eta === null ? "" : ` · ${t("job.eta", { eta: formatDuration(eta) })}`}`;
  };
  const hasTranslation = Boolean(project?.cues.some((c) => c.translation !== null));
  // Indices of the cues the table shows (all, or only those with issues).
  const shownRows = (project?.cues ?? []).map((_, i) => i).filter((i) => !onlyIssues || issuesByRow.has(i));
  // Pickers list downloaded models only (plus the current pick, so the select stays truthful);
  // before diagnostics answer nothing is known, so everything is listed.
  const listed = (c: ModelChoice) => !diagnostics || installed.has(c.id) || c.id === model || c.id === translationModel;
  const whisperChoices = choices.filter((c) => c.kind === "asr" && listed(c));
  const llmChoices = choices.filter((c) => c.kind === "translation" && listed(c));
  const hiddenModels = choices.length - choices.filter(listed).length;
  const asrChoice = findChoice(choices, model);
  const llmChoice = findChoice(choices, translationModel);
  const asrLanguageOk = languageSupported(asrChoice, language);
  const llmDeviceOk = !translateTo || deviceSupported(llmChoice, translationDevice);
  const asrDeviceOk = deviceSupported(asrChoice, device);
  const hintFor = (c: ModelChoice | undefined, id: string) =>
    c?.hint?.[locale] ?? t(`hint.${id}` as MessageKey);
  const optionLabel = (c: ModelChoice) =>
    `${c.label}${c.experimental ? ` · ${t("config.experimental")}` : ""}${modelSuffix(c.id)}`;
  const missingModels = [model, ...(translateTo ? [translationModel] : [])].filter(
    (m) => diagnostics && !installed.has(m),
  );

  const currentConfig = (): RunConfig => ({
    language,
    task: "transcribe",
    model,
    device,
    translateTo,
    translationModel,
    translationDevice,
    formats,
    keepAwake,
  });

  /** ``auto``: started by the queue, so the view the owner is on is left alone. */
  const start = async (src: string | null = source, settings: RunConfig = currentConfig(), auto = false) => {
    if (!bridge || !src) return;
    const config: JobConfig = { ...settings, source: src };
    setRunConfig(settings);
    dispatch({ type: "start" });
    setMarkers([]);
    setBannersHidden(false);
    fileStartedAt.current = Date.now();
    if (!auto) setTab("monitor"); // watch device load while a job the owner started runs
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

  /** Write the editor's project to ``base``; on success it is no longer dirty. */
  const saveTo = async (base: string, message: "editor.saved" | "editor.autoSaved"): Promise<boolean> => {
    if (!bridge || !project) return false;
    try {
      const written = await bridge.exportProject(project, base, formats);
      setProjectBase(base);
      setDirty(false);
      setKeptEdits(false);
      setNotice(t(message, { n: written.length, files: written.map(baseName).join(", ") }));
      return true;
    } catch (err) {
      setNotice(t("editor.exportFailed", { error: errorMessage(t, String(err)) }));
      return false;
    }
  };

  /** Show a finished job's subtitles; unsaved edits are auto-saved first, or kept as they are. */
  const takeResult = async (next: LocalSubProject, outputs: string[], showEditor: boolean) => {
    const canAutoSave = autoSave && projectBase !== null && blocking.length === 0;
    if (dirty && !(canAutoSave && (await saveTo(projectBase, "editor.autoSaved")))) {
      setKeptEdits(true); // never replace unsaved edits; the new job's files are already on disk
      return;
    }
    const json = outputs.find((o) => o.endsWith(".localsub.json")) ?? outputs[0];
    showProject(next);
    setProjectBase(json ? outputBaseFromSavePath(json) : null);
    setKeptEdits(false);
    if (showEditor) {
      setTab("editor");
      setDrawerOpen(false); // editing starts: give the cue table the full width
    }
  };

  // A finished or failed job is logged, shows its result and starts the next queued file;
  // Cancel stops the queue unless it only skipped this file; "stop after this file" holds the rest.
  useEffect(() => {
    const finished = job.status === "completed" || job.status === "failed";
    if (!finished && job.status !== "cancelled") return;
    const skipped = job.status === "cancelled" && skipCurrent.current;
    skipCurrent.current = false;
    const hold = stopAfter;
    if (hold) setStopAfter(false);
    const project = job.outputs.find((o) => o.endsWith(".localsub.json")) ?? null;
    const log = source
      ? [...runLog, { file: source, status: job.status, asr: job.asrDevice, mt: job.translationDevice, fallback: job.asrFallback ?? job.translationFallback, ms: Date.now() - fileStartedAt.current, project }]
      : runLog;
    setRunLog(log);
    if (job.status === "completed" && job.project) void takeResult(job.project, job.outputs, !queue.length || hold);
    if (!(finished || skipped) || hold || !queue.length) {
      if (log.length > 1) notifyQueueDone(log);
      return;
    }
    const [next, ...rest] = queue;
    setQueue(rest);
    setSource(next.file);
    void start(next.file, next.config, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per finished job, with that render's queue and settings
  }, [job.status]);

  /** The queue finished: say so in the window title and, if allowed, a Windows notification. */
  const notifyQueueDone = (log: RunResult[]) => {
    const ok = log.filter((r) => r.status === "completed").length;
    const text = t("queue.summary", { ok, total: log.length });
    document.title = `(${ok}/${log.length}) LocalSub NPU`;
    try {
      if ("Notification" in window && Notification.permission === "granted") new Notification("LocalSub NPU", { body: text });
    } catch {
      // the window title already carries the result
    }
  };

  const closeCancelMenu = () => {
    if (cancelMenu.current) cancelMenu.current.open = false;
  };
  const skipThisFile = () => {
    skipCurrent.current = true;
    closeCancelMenu();
    void bridge?.cancelJob();
  };
  const cancelAll = () => {
    closeCancelMenu();
    void bridge?.cancelJob();
  };
  const configText = (c: RunConfig) =>
    `${c.language === "auto" ? t("config.auto") : t(`lang.${c.language}` as MessageKey)} · ${findChoice(choices, c.model)?.label ?? c.model}${c.translateTo ? ` → ${t(`lang.${c.translateTo}` as MessageKey)}` : ""}`;

  const pickMedia = async () => {
    const picked = await bridge?.pickMedia();
    if (!picked?.length) return;
    if (!running) setSource(picked.shift() ?? null);
    const config = currentConfig();
    setQueue((q) => [...q, ...picked.map((file) => ({ file, config }))]);
  };

  /** Replace the editor's project (job result or opened file); its undo history starts empty. */
  const showProject = (next: LocalSubProject) => {
    setProject(next);
    setDirty(false);
    setUndoStack([]);
    setRedoStack([]);
    setRowLimit(ROW_CHUNK);
    lastEditKey.current = null;
  };

  /** Apply an edit. ``key`` names a typing run (one undo step per run); null = always a step. */
  const edit = (next: LocalSubProject, key: string | null = null) => {
    if (project && (key === null || key !== lastEditKey.current)) {
      setUndoStack((stack) => [...stack.slice(-99), project]);
      setRedoStack([]);
    }
    lastEditKey.current = key;
    setProject(next);
    setDirty(true);
  };

  /** Split/merge/delete: one undo step, announced with how to take it back. */
  const structural = (next: LocalSubProject, message: "editor.didSplit" | "editor.didMerge" | "editor.didDelete", n: number) => {
    edit(next);
    setNotice(t(message, { n }));
  };

  const undo = useCallback(() => {
    if (!project || !undoStack.length) return;
    setRedoStack((stack) => [...stack, project]);
    setProject(undoStack[undoStack.length - 1]);
    setUndoStack((stack) => stack.slice(0, -1));
    setDirty(true);
    lastEditKey.current = null;
  }, [project, undoStack]);

  const redo = useCallback(() => {
    if (!project || !redoStack.length) return;
    setUndoStack((stack) => [...stack, project]);
    setProject(redoStack[redoStack.length - 1]);
    setRedoStack((stack) => stack.slice(0, -1));
    setDirty(true);
    lastEditKey.current = null;
  }, [project, redoStack]);

  // Ctrl+S saves to where the project was last written, or asks where (like the Export button).
  useEffect(() => {
    quickSaveRef.current = () => {
      if (!project || blocking.length || formats.length === 0) return;
      if (projectBase) void saveTo(projectBase, "editor.saved");
      else void exportFiles();
    };
  });

  // Ctrl+S, and Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y in the editor; inside a text box the browser's own
  // undo runs.
  useEffect(() => {
    if (tab !== "editor") return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        quickSaveRef.current();
        return;
      }
      if (e.key.toLowerCase() === "b") {
        e.preventDefault();
        setDrawerOpen((open) => !open);
        return;
      }
      const target = e.target as HTMLElement | null;
      if (target?.closest("textarea, input")) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) undo();
      else if ((key === "z" && e.shiftKey) || key === "y") redo();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, undo, redo]);

  const jumpToCue = () => {
    const n = Number(jumpTo);
    if (!project || !Number.isInteger(n) || n < 1 || n > project.cues.length) return;
    setOnlyIssues(false);
    setRowLimit((limit) => Math.max(limit, n + 20));
    // after the filter is off and the row is rendered, focus its source text box
    window.setTimeout(() => {
      const box = document.querySelector<HTMLTextAreaElement>(`.cue-table textarea[data-col="src"][data-row="${n - 1}"]`);
      box?.scrollIntoView({ block: "center" });
      box?.focus();
    });
  };

  // Load the next chunk of rows when the "more rows" row comes near the visible area.
  useEffect(() => {
    const row = moreRowsRef.current;
    if (!row || tab !== "editor") return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setRowLimit((limit) => limit + ROW_CHUNK);
      },
      { rootMargin: "600px 0px" },
    );
    observer.observe(row);
    return () => observer.disconnect();
  }, [tab, rowLimit, project, onlyIssues]);

  const exportFiles = async () => {
    if (!bridge || !project || blocking.length) return;
    const chosen = await bridge.pickSaveBase(defaultOutputBase(project.source_media ?? source));
    if (!chosen) return;
    await saveTo(outputBaseFromSavePath(chosen), "editor.saved");
  };

  const openProject = async () => {
    const path = await bridge?.pickProject();
    if (path) await openProjectFile(path);
  };

  /** Load a .localsub.json into the editor; unsaved edits are only dropped after a confirm. */
  const openProjectFile = async (path: string) => {
    if (!bridge) return;
    if (dirty && !(await bridge.confirm(t("editor.discardConfirm"), t("editor.title")))) return;
    try {
      const data = await bridge.loadProject(path);
      if (!isProject(data)) throw new Error(t("editor.notProject"));
      showProject(normalizeProject(data));
      setProjectBase(outputBaseFromSavePath(path));
      setTab("editor");
      setDrawerOpen(false);
      setSource(data.source_media ?? source);
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

  const stepIcon = (state: string) =>
    state === "done" ? <CircleCheck size={14} aria-hidden="true" />
    : state === "failed" ? <CircleX size={14} aria-hidden="true" />
    : state === "active" ? <Loader2 size={14} className="spin" aria-hidden="true" />
    : <Circle size={14} aria-hidden="true" />;
  // The first unmet condition, shown next to the disabled Generate button.
  const blockReason = !source
    ? t("why.noFile")
    : formats.length === 0
      ? t("why.noFormat")
      : missingModels.length > 0
        ? t("why.needModels", { models: missingModels.join(", ") })
        : !asrLanguageOk || !asrDeviceOk || !llmDeviceOk
          ? t("why.config")
          : null;
  const canStart = Boolean(bridge) && blockReason === null;
  const queueTotal = runLog.length + 1 + queue.length;
  // shortcut: assumes queued files take as long as the ones already finished in this run; use
  // media durations x History RTF if episode lengths vary a lot.
  const doneRuns = runLog.filter((r) => r.status === "completed");
  const etaAfterThis = doneRuns.length && queue.length ? (doneRuns.reduce((sum, r) => sum + r.ms, 0) / doneRuns.length) * queue.length : null;

  const moveTab = (from: Tab, key: string) => {
    const delta = key === "ArrowDown" || key === "ArrowRight" ? 1 : key === "ArrowUp" || key === "ArrowLeft" ? -1 : 0;
    const next = key === "Home" ? TABS[0] : key === "End" ? TABS[TABS.length - 1] : delta ? TABS[(TABS.indexOf(from) + delta + TABS.length) % TABS.length] : null;
    if (!next) return false;
    setTab(next);
    document.getElementById(`tab-${next}`)?.focus();
    return true;
  };

  const setup = (
    <div className="setup">
      <section className="card">
        <h2 className="card-title"><span className="step-no" aria-hidden="true">1</span>{t("media.title")}</h2>
        <button type="button" className="drop-zone" onClick={() => void pickMedia()} disabled={!bridge}>
          <FileVideo size={22} aria-hidden="true" />
          <span className="drop-title">{source ? baseName(source) : t("media.choose")}</span>
          <span className="drop-hint">{running ? t("queue.add") : t("media.hint")}</span>
        </button>
        {source && <p className="path" title={source}>{source}</p>}
        {queue.length > 0 && (
          <div className="queue">
            <p className="queue-title">{t("queue.title", { n: queue.length })}</p>
            <ol className="queue-list">
              {running && source && runConfig && (
                <li className="current">
                  <span title={source}>{baseName(source)}</span>
                  <span className="queue-config">{t("queue.running")} · {configText(runConfig)}</span>
                </li>
              )}
              {queue.map((file, i) => (
                <li key={`${i}-${file.file}`}>
                  <span title={file.file}>{baseName(file.file)}</span>
                  <span className="queue-config">{configText(file.config)}</span>
                  <button type="button" className="icon-btn icon-danger" aria-label={t("queue.remove", { file: baseName(file.file) })} onClick={() => setQueue((q) => q.filter((_, j) => j !== i))}>
                    <X size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ol>
            <p className="hint">{t("queue.hint")}</p>
          </div>
        )}
        <button type="button" className="btn btn-secondary btn-block" onClick={openProject} disabled={!bridge || running}>
          <FolderOpen size={16} aria-hidden="true" />
          {t("media.openProject")}
        </button>
      </section>

      <section className="card">
        <h2 className="card-title"><span className="step-no" aria-hidden="true">2</span>{t("config.title")}</h2>
        <div className="field-row">
          <div className="field">
            <label htmlFor="language">{t("config.spoken")}</label>
            <select id="language" value={language} onChange={(e) => setLanguage(e.target.value as SpokenLanguage)}>
              <option value="auto">{t("config.auto")}</option>
              <option value="en">{t("lang.en")}</option>
              <option value="ja">{t("lang.ja")}</option>
              <option value="ko">{t("lang.ko")}</option>
              <option value="zh">{t("lang.zh")}</option>
              <option value="vi">{t("lang.vi")}</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="translate-to">{t("config.translateTo")}</label>
            <select id="translate-to" value={translateTo} onChange={(e) => setTranslateTo(e.target.value as TargetLanguage)}>
              <option value="">{t("config.noTranslation")}</option>
              <option value="vi">{t("lang.vi")}</option>
              <option value="en">{t("lang.en")}</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="model">{t("config.speechModel")}</label>
          <div className="field-row tight">
            <select id="model" value={model} onChange={(e) => setModel(e.target.value)}>
              {whisperChoices.map((m) => (
                <option key={m.id} value={m.id}>{optionLabel(m)}</option>
              ))}
            </select>
            <select aria-label={t("config.speechDevice")} value={device} onChange={(e) => setDevice(e.target.value as DevicePreference)}>
              {DEVICES.map((d) => (
                <option key={d.value} value={d.value} disabled={!deviceSupported(asrChoice, d.value)}>{deviceLabel(t, d.label)}</option>
              ))}
            </select>
          </div>
          <p className="hint">{hintFor(asrChoice, model)} {t("config.autoOrder")}</p>
          {hiddenModels > 0 && (
            <p className="hint">
              {t("config.moreModels", { count: hiddenModels })}{" "}
              <button type="button" className="link" onClick={() => setTab("models")}>{t("config.openModels")}</button>
            </p>
          )}
          {!asrLanguageOk && (
            <p className="msg msg-error" role="alert">{t("config.languageOnly", { model: asrChoice?.label ?? model, langs: (asrChoice?.languages ?? []).map((l) => t(`lang.${l}` as MessageKey)).join(", ") })}</p>
          )}
          {!asrDeviceOk && <p className="msg msg-error" role="alert">{t("config.deviceOnly", { model: asrChoice?.label ?? model, devices: asrChoice?.devices.join("/") ?? "" })}</p>}
          {language === "ja" && JA_WEAK_MODELS.has(model) && <p className="msg msg-warn">{t("config.jaWarn")}</p>}
          {language === "ko" && JA_WEAK_MODELS.has(model) && <p className="msg msg-warn">{t("config.koWarn")}</p>}
          {language === "zh" && JA_WEAK_MODELS.has(model) && <p className="msg msg-warn">{t("config.zhWarn")}</p>}
        </div>
        {translateTo && (
          <div className="field">
            <label htmlFor="llm">{t("config.translationModel")}</label>
            <div className="field-row tight">
              <select id="llm" value={translationModel} onChange={(e) => setTranslationModel(e.target.value)}>
                {llmChoices.map((m) => (
                  <option key={m.id} value={m.id}>{optionLabel(m)}</option>
                ))}
              </select>
              <select aria-label={t("config.translationDevice")} value={translationDevice} onChange={(e) => setTranslationDevice(e.target.value as TranslationDevice)}>
                {TRANSLATION_DEVICES.map((d) => (
                  <option key={d.value} value={d.value} disabled={!deviceSupported(llmChoice, d.value)}>{deviceLabel(t, d.label)}</option>
                ))}
              </select>
            </div>
            <p className="hint">{hintFor(llmChoice, translationModel)}</p>
            {!llmDeviceOk && <p className="msg msg-error" role="alert">{t("config.deviceOnly", { model: llmChoice?.label ?? translationModel, devices: llmChoice?.devices.join("/") ?? "" })}</p>}
          </div>
        )}
        <fieldset className="field formats">
          <legend>{t("config.formats")}</legend>
          <div className="chip-group">
            {FORMATS.map((f) => (
              <label key={f} className="chip-check">
                <input type="checkbox" checked={formats.includes(f)} onChange={() => toggleFormat(f)} />
                <span>{f.toUpperCase()}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="switch">
          <input type="checkbox" role="switch" checked={keepAwake} onChange={(e) => setKeepAwake(e.target.checked)} disabled={running} />
          <span className="switch-track" aria-hidden="true" />
          <span>{t("config.keepAwake")}</span>
        </label>

        <label className="switch">
          <input type="checkbox" role="switch" checked={autoSave} onChange={(e) => setAutoSave(e.target.checked)} />
          <span className="switch-track" aria-hidden="true" />
          <span>{t("config.autoSave")}</span>
        </label>
        <details className="more">
          <summary>{t("config.switchHelp")}</summary>
          <p className="hint">{t("config.keepAwakeHint")}</p>
          <p className="hint">{t("config.autoSaveHint")}</p>
        </details>
      </section>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">{t("runtime.title")}</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={runDiagnostics} disabled={diagBusy || !bridge}>
            <RefreshCw size={14} className={diagBusy ? "spin" : ""} aria-hidden="true" />
            {diagBusy ? t("runtime.checking") : t("runtime.recheck")}
          </button>
        </div>
        {diagError && <p className="msg msg-error">{errorMessage(t, diagError)}</p>}
        <dl className="kv">
          <div><dt>{t("runtime.ffmpeg")}</dt><dd className={diagnostics?.ffmpeg_available ? "ok" : "bad"}>{diagnostics ? (diagnostics.ffmpeg_available ? t("runtime.found") : t("runtime.missing")) : "—"}</dd></div>
          <div><dt>{t("runtime.openvino")}</dt><dd>{diagnostics?.openvino_version?.split("-")[0] ?? "—"}</dd></div>
          <div><dt>{t("runtime.devices")}</dt><dd>{diagnostics?.available_devices.join(" · ") || "—"}</dd></div>
          <div><dt>{t("runtime.autoResolves")}</dt><dd className={diagnostics?.device_resolution?.resolved === "NPU" ? "ok" : ""}>{diagnostics?.device_resolution?.resolved ?? "—"}</dd></div>
          <div><dt>{t("runtime.speechUsed")}</dt><dd>{job.asrDevice ?? "—"}</dd></div>
          <div><dt>{t("runtime.translationUsed")}</dt><dd>{job.translationDevice ?? "—"}</dd></div>
          <div>
            <dt>{t("runtime.keepAwake")}</dt>
            <dd className={job.power?.keep_awake ? "ok" : ""} title={job.power?.reason ?? undefined}>
              {!job.power ? "—" : job.power.keep_awake ? (job.power.throttling_disabled ? t("runtime.awakeFull") : t("runtime.awakeOn")) : job.power.reason === "disabled" ? t("runtime.awakeOff") : t("runtime.awakeUnsupported")}
            </dd>
          </div>
        </dl>
        {diagnostics && !diagnostics.ffmpeg_available && <p className="msg msg-warn">{t("runtime.ffmpegHint")}</p>}
        {(job.asrFallback || job.translationFallback) && (
          <p className="hint">{t("runtime.fallback", { reason: job.asrFallback ?? job.translationFallback ?? "" })}</p>
        )}
        {missingModels.length > 0 && <p className="msg msg-warn">{t("runtime.needModels", { models: missingModels.join(", ") })}</p>}
        {diagAt && (
          <p className="hint">{t("runtime.checkedAt", { time: formatLocal(diagAt.toISOString(), locale === "vi" ? "vi-VN" : "en-GB"), tz: offsetLabel(diagAt) })}</p>
        )}
      </section>
    </div>
  );

  return (
    <I18nContext.Provider value={i18n}>
    <div className="app">
      <a className="skip-link" href="#main">{t("app.skip")}</a>
      <nav className="rail" aria-label={t("tabs.label")}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true"><AudioLines size={20} /></span>
          <span className="brand-text">
            <strong>LocalSub</strong>
            <span>NPU</span>
          </span>
        </div>
        <div className="nav" role="tablist" aria-orientation="vertical" aria-label={t("tabs.label")}>
          {TABS.map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`tab-${id}`}
              aria-controls={`panel-${id}`}
              aria-selected={tab === id}
              tabIndex={tab === id ? 0 : -1}
              className="nav-item"
              title={t(`tabs.${id}` as MessageKey)}
              onClick={() => setTab(id)}
              onKeyDown={(e) => {
                if (moveTab(id, e.key)) e.preventDefault();
              }}
            >
              {TAB_ICONS[id]}
              <span className="nav-label">{t(`tabs.${id}` as MessageKey)}</span>
              {id === "editor" && project ? <span className="count">{project.cues.length}</span> : null}
              {id === "editor" && dirty ? (
                <>
                  <span className="dot-unsaved" aria-hidden="true" />
                  <span className="sr-only">{t("editor.unsaved")}</span>
                </>
              ) : null}
            </button>
          ))}
        </div>
        <div className="rail-foot">
          <div className="segmented" role="group" aria-label={t("theme.label")}>
            {THEMES.map((m) => (
              <button key={m.id} type="button" aria-pressed={theme === m.id} title={t(`theme.${m.id}` as MessageKey)} aria-label={t(`theme.${m.id}` as MessageKey)} onClick={() => setTheme(m.id)}>
                {m.icon}
              </button>
            ))}
          </div>
          <div className="segmented" role="group" aria-label={t("app.language")}>
            {(["vi", "en"] as Locale[]).map((l) => (
              <button key={l} type="button" aria-pressed={locale === l} onClick={() => setLocale(l)}>
                {l.toUpperCase()}
              </button>
            ))}
          </div>
          <span className={`env-chip${bridge?.preview ? " preview" : ""}`} title={t("app.eyebrow")}>
            {bridge?.preview ? t("app.chipPreview") : t("app.chipDesktop")}
          </span>
        </div>
      </nav>

      <div className="main" id="main">
        <header className="topbar">
          <div className="topbar-title">
            <h1>{t(`tabs.${tab}` as MessageKey)}</h1>
            <p>{t(`view.${tab}` as MessageKey)}</p>
          </div>
          {tab === "editor" && (
            <button
              type="button"
              className="btn btn-secondary drawer-toggle"
              aria-expanded={drawerOpen}
              aria-controls="run-drawer"
              title={t("drawer.toggleTitle")}
              onClick={() => setDrawerOpen((open) => !open)}
            >
              <Clapperboard size={16} aria-hidden="true" />
              {drawerOpen ? t("drawer.hide") : t("drawer.show")}
            </button>
          )}
          <div className="jobbar" aria-label={t("job.progress")}>
            {running && source && (
              <span className="job-meta">
                {queueTotal > 1 && <span className="job-pos">{t("queue.position", { n: runLog.length + 1, total: queueTotal })}</span>}
                {etaAfterThis !== null && <span className="job-eta">{t("queue.eta", { eta: formatDuration(etaAfterThis), n: queue.length })}</span>}
                {stopAfter && <span className="job-hold">{t("queue.holdOn")}</span>}
                <span className="job-file" title={source}>{baseName(source)}</span>
              </span>
            )}
            <p className="sr-only" aria-live="polite">
              {running && job.stage ? t(`steps.${job.stage}` as MessageKey) : ""}
            </p>
            {job.status !== "idle" && (
            <ol className="stepper">
              {STEPS.filter((s) => s !== "translating" || translateTo).map((s) => {
                const idx = STEPS.indexOf(s);
                const state =
                  job.status === "completed" ? "done"
                  : idx < activeIndex ? "done"
                  : idx === activeIndex ? (job.status === "failed" ? "failed" : "active")
                  : s === "translating" && overlapping ? "active"
                  : "";
                return (
                  <li key={s} className={state} aria-current={state === "active" ? "step" : undefined}>
                    {stepIcon(state)}
                    <span>{t(`steps.${s}` as MessageKey)}{progressText(s)}</span>
                  </li>
                );
              })}
            </ol>
            )}
            {running && queue.length > 0 ? (
              <details className="cancel-menu" ref={cancelMenu}>
                <summary className="btn btn-danger">
                  <Square size={16} aria-hidden="true" />
                  {t("job.cancel")}
                  <ChevronDown size={14} aria-hidden="true" />
                </summary>
                <div className="cancel-options">
                  <button type="button" className="btn btn-secondary" onClick={skipThisFile}>{t("queue.skipFile")}</button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    aria-pressed={stopAfter}
                    onClick={() => {
                      setStopAfter((on) => !on);
                      closeCancelMenu();
                    }}
                  >
                    {stopAfter ? t("queue.holdOff") : t("queue.holdAfter")}
                  </button>
                  <button type="button" className="btn btn-danger" onClick={cancelAll}>{t("queue.cancelAll", { n: queue.length + 1 })}</button>
                </div>
              </details>
            ) : running ? (
              <button type="button" className="btn btn-danger" onClick={() => void bridge?.cancelJob()}>
                <Square size={16} aria-hidden="true" />
                {t("job.cancel")}
              </button>
            ) : (
              <>
                {blockReason && <span className="cta-reason" id="cta-reason">{blockReason}</span>}
                <button
                  type="button"
                  // The file in the editor was just generated: the next step is review/export, so
                  // running it again steps back. Picking a new file brings the orange CTA back.
                  className={project && runLog.some((r) => r.file === source) ? "btn btn-secondary" : "btn btn-cta"}
                  onClick={() => {
                    setRunLog([]);
                    setStopAfter(false);
                    document.title = "LocalSub NPU";
                    // a queue can finish unattended: ask once, from this click, to notify then
                    if (queue.length && "Notification" in window && Notification.permission === "default") void Notification.requestPermission();
                    void start();
                  }}
                  disabled={!canStart}
                  aria-describedby={blockReason ? "cta-reason" : undefined}
                >
                  <Play size={16} aria-hidden="true" />
                  {t("job.generate")}
                </button>
              </>
            )}
          </div>
        </header>

        {tab === "editor" && !bannersHidden && (job.status === "failed" || job.status === "cancelled" || (job.status === "completed" && job.outputs.length > 0) || job.logs.length > 0 || keptEdits || (!running && runLog.length > 1)) && (
          <div className="banner-stack">
            <button type="button" className="icon-btn banner-close" aria-label={t("banner.close")} title={t("banner.close")} onClick={() => setBannersHidden(true)}>
              <X size={16} aria-hidden="true" />
            </button>
            {job.status === "failed" && <p className="banner banner-error" role="alert"><CircleX size={16} aria-hidden="true" />{errorMessage(t, job.error)}</p>}
            {job.status === "cancelled" && <p className="banner banner-warn"><Square size={16} aria-hidden="true" />{t("job.cancelled")}</p>}
            {job.status === "completed" && job.outputs.length > 0 && runLog.length <= 1 && (
              <p className="banner banner-success" role="status"><CircleCheck size={16} aria-hidden="true" />{t("job.wrote", { files: job.outputs.map(baseName).join(", ") })}</p>
            )}
            {keptEdits && <p className="banner banner-warn" role="status">{t("queue.keptEdits")}</p>}
            {!running && runLog.length > 1 && (
              <section className="banner banner-info queue-summary" role="status" aria-label={t("queue.summaryTitle")}>
                <div className="queue-summary-head">
                  <strong>{t("queue.summary", { ok: runLog.filter((r) => r.status === "completed").length, total: runLog.length })}</strong>
                  <button type="button" className="link" onClick={() => setTab("history")}>{t("queue.openHistory")}</button>
                </div>
                <ol>
                  {runLog.map((r, i) => (
                    <li key={`${i}-${r.file}`} className={r.status}>
                      {r.status === "completed" ? <CircleCheck size={14} aria-hidden="true" /> : <CircleX size={14} aria-hidden="true" />}
                      <span className="queue-file" title={r.file}>{baseName(r.file)}</span>
                      <span>{t(`history.state.${r.status === "running" || r.status === "idle" ? "failed" : r.status}` as MessageKey)}</span>
                      <span className="queue-devices">
                        {[r.asr && t("queue.devAsr", { device: r.asr }), r.mt && t("queue.devMt", { device: r.mt })].filter(Boolean).join(" · ") || "—"}
                      </span>
                      {r.fallback && (
                        <span className="queue-fallback"><TriangleAlert size={14} aria-hidden="true" />{t("queue.fallback", { reason: r.fallback })}</span>
                      )}
                      {r.project && (
                        <button type="button" className="link" onClick={() => void openProjectFile(r.project as string)}>
                          {t("queue.openFile")}
                        </button>
                      )}
                    </li>
                  ))}
                </ol>
              </section>
            )}
            {job.logs.length > 0 && (
              <details className="log">
                <summary>{t("job.log", { n: job.logs.length })}</summary>
                <pre>{job.logs.join("\n")}</pre>
              </details>
            )}
          </div>
        )}

        <div className={`view editor-view${drawerOpen ? " with-drawer" : ""}`} role="tabpanel" id="panel-editor" aria-labelledby="tab-editor" hidden={tab !== "editor"}>
          <section className="card editor-card" aria-label={t("editor.title")}>
            {!project && (
              <div className="empty">
                <Subtitles size={36} aria-hidden="true" />
                <p>{t("editor.empty")}</p>
                <ol className="empty-steps">
                  <li>{t("empty.step1")}</li>
                  <li>{t("empty.step2")}</li>
                  <li>{t("empty.step3")}</li>
                </ol>
                <div className="empty-actions">
                  {!drawerOpen && (
                    <button type="button" className="btn btn-primary" onClick={() => setDrawerOpen(true)}>
                      <Clapperboard size={16} aria-hidden="true" />
                      {t("editor.goRun")}
                    </button>
                  )}
                  {!drawerOpen && (
                    <button type="button" className="btn btn-secondary" onClick={openProject} disabled={!bridge || running}>
                      <FolderOpen size={16} aria-hidden="true" />
                      {t("media.openProject")}
                    </button>
                  )}
                </div>
              </div>
            )}
            {project && (
              <div className="editor">
                <div className="card-head">
                  <div>
                    <h2 className="card-title">
                      {baseName(project.source_media ?? projectBase) || t("editor.title")} {dirty && <span className="badge badge-warn">{t("editor.unsaved")}</span>}
                    </h2>
                    <p className="hint">
                      {t("editor.summary", { n: project.cues.length, src: project.source_language ?? "?", dst: project.target_language ? ` → ${project.target_language}` : "" })}
                    </p>
                  </div>
                  <button type="button" className="btn btn-primary" onClick={exportFiles} disabled={blocking.length > 0 || formats.length === 0}>
                    <Download size={16} aria-hidden="true" />
                    {t("editor.export", { formats: formats.map((f) => f.toUpperCase()).join(" + ") })}
                  </button>
                </div>
                <div className="editor-tools">
                  <button type="button" className="icon-btn" onClick={undo} disabled={!undoStack.length} title={t("editor.undoTitle")} aria-label={t("editor.undo")}>
                    <Undo2 size={16} aria-hidden="true" />
                  </button>
                  <button type="button" className="icon-btn" onClick={redo} disabled={!redoStack.length} title={t("editor.redoTitle")} aria-label={t("editor.redo")}>
                    <Redo2 size={16} aria-hidden="true" />
                  </button>
                  <label className="switch">
                    <input type="checkbox" role="switch" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} />
                    <span className="switch-track" aria-hidden="true" />
                    <span>{t("editor.onlyIssues", { n: new Set(issues.map((x) => x.index)).size })}</span>
                  </label>
                  <form
                    className="jump"
                    onSubmit={(e) => {
                      e.preventDefault();
                      jumpToCue();
                    }}
                  >
                    <label htmlFor="jump-cue">{t("editor.jump")}</label>
                    <input id="jump-cue" type="number" min={1} max={project.cues.length} value={jumpTo} onChange={(e) => setJumpTo(e.target.value)} />
                  </form>
                  <span className="hint">{t("editor.keys")}</span>
                </div>
                {notice && <p className="msg msg-info" role="status">{notice}</p>}
                {blocking.length > 0 && <p className="msg msg-error" role="alert">{t("editor.fix", { n: blocking.length })}</p>}
                {onlyIssues && issues.length === 0 && <p className="hint">{t("editor.noIssues")}</p>}
                <div className="table-wrap">
                  <table className="cue-table">
                    <thead>
                      <tr>
                        <th scope="col">#</th><th scope="col">{t("editor.col.start")} / {t("editor.col.end")}</th><th scope="col">{t("editor.col.source")}</th>
                        {hasTranslation && <th scope="col">{t("editor.col.translation")}</th>}
                        <th scope="col"><span className="sr-only">{t("editor.col.actions")}</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownRows.slice(0, rowLimit).map((i) => {
                        const cue = project.cues[i];
                        const rowIssues = issuesByRow.get(i) ?? [];
                        const n = i + 1;
                        const issueIds = rowIssues.map((x) => `issue-${i}-${x.code}`).join(" ") || undefined;
                        return (
                          <tr key={`${i}-${cue.start_ms}`} className={rowIssues.some((x) => x.severity === "error") ? "row-error" : ""}>
                            <td className="num">{n}</td>
                            <td className="times">
                              <div>
                                <TimeField id={`start-${i}`} label={t("editor.start", { n })} invalidText={t("editor.timeFormat")} value={cue.start_ms} onCommit={(ms) => edit(updateCue(project, i, { start_ms: ms }))} />
                                <TimeField id={`end-${i}`} label={t("editor.end", { n })} invalidText={t("editor.timeFormat")} value={cue.end_ms} onCommit={(ms) => edit(updateCue(project, i, { end_ms: ms }))} />
                                {(() => {
                                  const cps = readingSpeed(cue.text, cue.end_ms - cue.start_ms);
                                  const fast = cps !== null && cps > maxCpsFor(cue.text);
                                  return (
                                    <span className={`cue-meta${fast ? " fast" : ""}`}>
                                      {t("editor.meta", {
                                        s: ((cue.end_ms - cue.start_ms) / 1000).toLocaleString(locale === "vi" ? "vi-VN" : "en-GB", { maximumFractionDigits: 1 }),
                                        cps: cps === null ? "—" : Math.round(cps),
                                      })}
                                    </span>
                                  );
                                })()}
                              </div>
                            </td>
                            <td>
                              <textarea data-row={i} data-col="src" aria-label={t("editor.sourceLabel", { n })} aria-describedby={issueIds} value={cue.text} rows={rowsFor(cue.text)} onKeyDown={moveBetweenCues} onChange={(e) => edit(updateCue(project, i, { text: e.target.value }), `src-${i}`)} />
                              {rowIssues.map((x) => <span key={x.code} id={`issue-${i}-${x.code}`} className={`issue ${x.severity}`}>{t(`issue.${x.code}` as MessageKey, { limit: x.limit ?? "" })}</span>)}
                            </td>
                            {hasTranslation && (
                              <td><textarea data-row={i} data-col="tr" aria-label={t("editor.translationLabel", { n })} value={cue.translation ?? ""} rows={rowsFor(cue.translation ?? "")} onKeyDown={moveBetweenCues} onChange={(e) => edit(updateCue(project, i, { translation: e.target.value }), `tr-${i}`)} /></td>
                            )}
                            <td className="row-actions">
                              <button type="button" className="icon-btn" onClick={() => structural(splitCue(project, i), "editor.didSplit", n)} title={t("editor.splitTitle")} aria-label={`${t("editor.split")} ${n}`}><Scissors size={16} aria-hidden="true" /></button>
                              <button type="button" className="icon-btn" onClick={() => structural(mergeWithNext(project, i), "editor.didMerge", n)} disabled={i === project.cues.length - 1} title={t("editor.mergeTitle")} aria-label={`${t("editor.merge")} ${n}`}><Merge size={16} aria-hidden="true" /></button>
                              <button type="button" className="icon-btn icon-danger" onClick={() => structural(deleteCue(project, i), "editor.didDelete", n)} title={t("editor.deleteTitle")} aria-label={`${t("editor.delete")} ${n}`}><Trash2 size={16} aria-hidden="true" /></button>
                            </td>
                          </tr>
                        );
                      })}
                      {shownRows.length > rowLimit && (
                        <tr ref={moreRowsRef} className="more-rows">
                          <td colSpan={hasTranslation ? 5 : 4}>
                            <button type="button" className="link" onClick={() => setRowLimit((limit) => limit + ROW_CHUNK)}>
                              {t("editor.moreRows", { n: shownRows.length - rowLimit })}
                            </button>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
          {drawerOpen && (
            <div
              className="drawer"
              id="run-drawer"
              onKeyDown={(e) => {
                if (e.key === "Escape") setDrawerOpen(false);
              }}
            >
              <div className="drawer-head">
                <h2>{t("drawer.title")}</h2>
                <button type="button" className="icon-btn" aria-label={t("drawer.hide")} title={t("drawer.hide")} onClick={() => setDrawerOpen(false)}>
                  <X size={16} aria-hidden="true" />
                </button>
              </div>
              {setup}
            </div>
          )}
        </div>
        <div className="view" role="tabpanel" id="panel-monitor" aria-labelledby="tab-monitor" hidden={tab !== "monitor"}>
          {queue.length > 0 && (
            <p className="queue-next" role="status">{t("queue.next", { n: queue.length, files: queue.map((q) => baseName(q.file)).join(", ") })}</p>
          )}
          <section className="card"><MonitorPanel bridge={bridge} markers={markers} active={tab === "monitor"} /></section>
        </div>
        <div className="view" role="tabpanel" id="panel-history" aria-labelledby="tab-history" hidden={tab !== "history"}>
          <section className="card"><HistoryPanel bridge={bridge} refreshKey={historyKey} onOpen={(path) => void openProjectFile(path)} onModels={() => setTab("models")} /></section>
        </div>
        <div className="view" role="tabpanel" id="panel-analysis" aria-labelledby="tab-analysis" hidden={tab !== "analysis"}>
          <section className="card"><AnalysisPanel bridge={bridge} refreshKey={historyKey} active={tab === "analysis"} /></section>
        </div>
        <div className="view" role="tabpanel" id="panel-models" aria-labelledby="tab-models" hidden={tab !== "models"}>
          <section className="card">
            <ModelManager
              bridge={bridge}
              selected={[model, ...(translateTo ? [translationModel] : [])]}
              jobRunning={running}
              onChanged={() => void runDiagnostics()}
            />
          </section>
        </div>
      </div>
    </div>
    </I18nContext.Provider>
  );
}
