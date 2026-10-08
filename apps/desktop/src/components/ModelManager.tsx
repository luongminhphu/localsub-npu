import { useCallback, useEffect, useRef, useState } from "react";
import { formatBytes, percent } from "../lib/format";
import { errorMessage, useI18n } from "../lib/i18n";
import type { DownloadEvent, ModelInfo } from "../lib/types";
import type { WorkerBridge } from "../lib/worker";

interface Progress {
  modelId: string;
  done: number;
  total: number | null;
}

type Notice = { kind: "success" | "warn" | "error"; text: string } | null;

export function ModelManager(props: {
  bridge: WorkerBridge | null;
  selected: string[];
  jobRunning: boolean;
  onChanged: () => void;
}) {
  const { bridge, selected, jobRunning, onChanged } = props;
  const { t, locale } = useI18n();
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const failure = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (!bridge) return;
    setLoadError(null);
    try {
      setModels(await bridge.models());
    } catch (err) {
      setLoadError(String(err));
    }
  }, [bridge]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => () => stop.current?.(), []);

  const finish = (modelId: string, code: number | null, cancelled: boolean) => {
    stop.current?.();
    stop.current = null;
    setProgress(null);
    if (cancelled) setNotice({ kind: "warn", text: t("models.stopped", { model: modelId }) });
    else if (code === 0) setNotice({ kind: "success", text: t("models.done", { model: modelId }) });
    else
      setNotice({
        kind: "error",
        text: t("models.failed", {
          model: modelId,
          error: errorMessage(t, failure.current ?? `exit code ${code ?? "?"}`),
        }),
      });
    failure.current = null;
    void refresh();
    onChanged();
  };

  const download = async (modelId: string) => {
    if (!bridge || progress) return;
    setNotice(null);
    failure.current = null;
    setProgress({ modelId, done: 0, total: null });
    const onEvent = (e: DownloadEvent) => {
      if (e.type === "error") {
        failure.current = JSON.stringify(e);
      } else if (e.type === "download" && e.state === "downloading") {
        setProgress({ modelId, done: e.downloaded_bytes ?? 0, total: e.total_bytes ?? null });
      }
    };
    try {
      stop.current = await bridge.downloadModel(modelId, {
        onEvent,
        onExit: (code, cancelled) => finish(modelId, code, cancelled),
      });
    } catch (err) {
      setProgress(null);
      setNotice({ kind: "error", text: t("models.failed", { model: modelId, error: errorMessage(t, String(err)) }) });
    }
  };

  const remove = async (model: ModelInfo) => {
    if (!bridge) return;
    const ok = await bridge.confirm(
      t("models.confirmRemove", { model: model.id, size: formatBytes(model.size_bytes) }),
      t("models.confirmTitle"),
    );
    if (!ok) return;
    setRemoving(model.id);
    try {
      await bridge.removeModel(model.id);
      setNotice({ kind: "success", text: t("models.removed", { model: model.id }) });
    } catch (err) {
      setNotice({ kind: "error", text: errorMessage(t, String(err)) });
    } finally {
      setRemoving(null);
      void refresh();
      onChanged();
    }
  };

  const pct = progress ? percent(progress.done, progress.total) : null;

  const renderGroup = (kind: ModelInfo["kind"]) => (
    <div className="model-group">
      <h3>{t(kind === "asr" ? "models.asr" : "models.translation")}</h3>
      <ul className="model-list">
        {(models ?? [])
          .filter((m) => m.kind === kind)
          .map((m) => {
            const active = progress?.modelId === m.id;
            return (
              <li key={m.id} className={`model-row ${m.status}`}>
                <div className="model-name">
                  <code>{m.id}</code>
                  {selected.includes(m.id) && <span className="tag">{t("models.inUse")}</span>}
                  {m.experimental && <span className="tag muted" title={m.hint?.[locale]}>{t("config.experimental")}</span>}
                  {m.languages?.length ? <span className="tag muted">{m.languages.map((l) => l.toUpperCase()).join("/")}</span> : null}
                  {m.devices && m.devices.length < 3 && <span className="tag muted">{m.devices.join("/")}</span>}
                </div>
                <span className={`model-status ${m.status}`}>{t(`models.status.${m.status}`)}</span>
                <span className="model-size">
                  {m.status === "installed"
                    ? formatBytes(m.size_bytes)
                    : t("models.approx", { size: formatBytes((m.approx_size_mb ?? 0) * 1_048_576) })}
                </span>
                <span className="model-actions">
                  {active ? (
                    <button type="button" className="link danger-text" onClick={() => void bridge?.cancelDownload()}>
                      {t("models.cancel")}
                    </button>
                  ) : m.status === "installed" ? (
                    <button
                      type="button"
                      className="link danger-text"
                      onClick={() => void remove(m)}
                      disabled={Boolean(progress) || removing !== null || (jobRunning && selected.includes(m.id))}
                    >
                      {t("models.remove")}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="link"
                      onClick={() => void download(m.id)}
                      disabled={Boolean(progress) || removing !== null}
                      title={progress ? t("models.busy") : undefined}
                    >
                      {t(m.status === "incomplete" ? "models.resume" : "models.download")}
                    </button>
                  )}
                </span>
              </li>
            );
          })}
      </ul>
    </div>
  );

  return (
    <section className="panel models" aria-label={t("models.title")}>
      <div className="panel-title">
        <div>
          <h2>{t("models.title")}</h2>
          <p className="help-text">{t("models.subtitle")}</p>
        </div>
        <button type="button" className="link" onClick={() => void refresh()} disabled={!bridge || Boolean(progress)}>
          {t("models.refresh")}
        </button>
      </div>
      {progress && (
        <div className="download-progress" role="status" aria-live="polite">
          <div className="progress-label">
            {progress.total
              ? t("models.downloading", { model: progress.modelId, done: formatBytes(progress.done), total: formatBytes(progress.total) })
              : t("models.downloadingNoTotal", { model: progress.modelId, done: formatBytes(progress.done) })}
            {pct !== null && <strong>{pct}%</strong>}
          </div>
          <div
            className="progress-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct ?? undefined}
            aria-label={progress.modelId}
          >
            <span style={{ width: `${pct ?? 5}%` }} className={pct === null ? "indeterminate" : ""} />
          </div>
        </div>
      )}
      {notice && <p className={notice.kind}>{notice.text}</p>}
      {loadError && <p className="error">{t("models.loadFailed", { error: loadError })}</p>}
      {!models && !loadError && <p className="help-text">{t("models.loading")}</p>}
      {models && (
        <div className="model-groups">
          {renderGroup("asr")}
          {renderGroup("translation")}
        </div>
      )}
    </section>
  );
}
