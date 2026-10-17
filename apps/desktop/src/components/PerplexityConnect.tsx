import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { formatLocal, offsetLabel } from "../lib/format";
import { useI18n, type MessageKey } from "../lib/i18n";
import {
  CLOUDFLARED_INSTALL,
  connectorUrl,
  DEFAULT_HTTP_PORT,
  INITIAL_HTTP_VIEW,
  isCloudflaredMissing,
  maskToken,
  mcpHttpReducer,
  ORPHAN,
  parsePort,
  PERPLEXITY_CONNECTOR_NAME,
  portableStdioFields,
} from "../lib/mcp";
import type { McpLaunch } from "../lib/types";
import type { WorkerBridge } from "../lib/worker";

const nowIso = () => new Date().toISOString();

/**
 * Perplexity custom remote connector: starts the read-only HTTP MCP server on 127.0.0.1 and a
 * Cloudflare quick tunnel in front of it, then shows the URL + API key for the connector form.
 * Kept mounted (hidden) so listeners survive switching between AI apps.
 */
export function PerplexityConnect(props: {
  bridge: WorkerBridge | null;
  launch: McpLaunch | null;
  visible: boolean;
  onCopy: (target: string, text: string) => void;
  copyLabel: (target: string, idle: MessageKey) => string;
}) {
  const { bridge, launch, visible, onCopy, copyLabel } = props;
  const { t, locale } = useI18n();
  const [view, dispatch] = useReducer(mcpHttpReducer, INITIAL_HTTP_VIEW);
  const [portText, setPortText] = useState(String(DEFAULT_HTTP_PORT));
  const [cloudflared, setCloudflared] = useState<string | null | undefined>(undefined);
  const [showKey, setShowKey] = useState(false);
  const [lastLog, setLastLog] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const unsubServer = useRef<(() => void) | null>(null);
  const unsubTunnel = useRef<(() => void) | null>(null);
  const localeTag = locale === "vi" ? "vi-VN" : "en-GB";
  const port = parsePort(portText);

  const refreshStatus = useCallback(async () => {
    if (!bridge) return;
    try {
      const status = await bridge.mcpHttpStatus();
      setCloudflared(status.cloudflared);
      dispatch({ kind: "sync", server: status.server, tunnel: status.tunnel, at: nowIso() });
    } catch (err) {
      setLastLog(String(err));
    }
  }, [bridge]);

  useEffect(() => {
    if (visible) void refreshStatus();
  }, [visible, refreshStatus]);

  useEffect(
    () => () => {
      unsubServer.current?.();
      unsubTunnel.current?.();
    },
    [],
  );

  const startServer = async (rotate: boolean) => {
    if (!bridge || port === null) return;
    const ok = await bridge.confirm(
      t(rotate ? "pplx.confirmRotate" : "pplx.confirmStart", { port }),
      t("pplx.confirmTitle"),
    );
    if (!ok) return;
    setBusy(true);
    try {
      if (view.server !== "stopped" && view.server !== "error") {
        await bridge.stopMcpHttp();
        unsubServer.current?.();
        unsubTunnel.current?.();
      }
      dispatch({ kind: "starting", at: nowIso() });
      unsubServer.current = await bridge.startMcpHttp(port, rotate, {
        onEvent: (event) => dispatch({ kind: "event", event, at: nowIso() }),
        onLog: (line) => setLastLog(line),
        onExit: (code, cancelled) => {
          dispatch({ kind: "exit", code, cancelled, at: nowIso() });
          unsubServer.current?.();
          unsubServer.current = null;
        },
      });
    } catch (err) {
      dispatch({ kind: "failed", message: String(err), at: nowIso() });
    } finally {
      setBusy(false);
    }
  };

  const stopServer = async () => {
    if (!bridge) return;
    setBusy(true);
    try {
      await bridge.stopMcpHttp();
    } catch (err) {
      setLastLog(String(err));
    } finally {
      dispatch({ kind: "exit", code: null, cancelled: true, at: nowIso() });
      dispatch({ kind: "tunnel-exit", code: null, cancelled: true, at: nowIso() });
      unsubServer.current?.();
      unsubTunnel.current?.();
      unsubServer.current = unsubTunnel.current = null;
      setBusy(false);
    }
  };

  const startTunnel = async () => {
    if (!bridge || port === null) return;
    const ok = await bridge.confirm(t("pplx.confirmTunnel"), t("pplx.confirmTitle"));
    if (!ok) return;
    setBusy(true);
    try {
      dispatch({ kind: "tunnel-starting", at: nowIso() });
      unsubTunnel.current = await bridge.startTunnel(port, {
        onUrl: (url) => dispatch({ kind: "tunnel-url", url, at: nowIso() }),
        onLog: (line) => setLastLog(line),
        onExit: (code, cancelled) => {
          dispatch({ kind: "tunnel-exit", code, cancelled, at: nowIso() });
          unsubTunnel.current?.();
          unsubTunnel.current = null;
        },
      });
    } catch (err) {
      const message = String(err);
      if (isCloudflaredMissing(message)) setCloudflared(null);
      dispatch({ kind: "tunnel-failed", message, at: nowIso() });
    } finally {
      setBusy(false);
    }
  };

  const stopTunnel = async () => {
    if (!bridge) return;
    setBusy(true);
    try {
      await bridge.stopTunnel();
    } catch (err) {
      setLastLog(String(err));
    } finally {
      dispatch({ kind: "tunnel-exit", code: null, cancelled: true, at: nowIso() });
      setBusy(false);
    }
  };

  const at = (iso: string | null) =>
    iso ? `${formatLocal(iso, localeTag)} (${offsetLabel(new Date(iso))})` : "—";
  const serverOn = view.server === "listening" || view.server === "starting";
  const tunnelOn = view.tunnel === "open" || view.tunnel === "starting";
  const endpoint = view.publicUrl ? connectorUrl(view.publicUrl) : null;
  const orphan = view.error === ORPHAN;
  const errorText = view.error && !orphan ? view.error : null;
  const stdio = launch ? portableStdioFields(launch) : null;
  const req = view.lastRequest;

  return (
    <div className="pplx" hidden={!visible}>
      <p className="help-text">{t("pplx.intro")}</p>

      <dl className="pplx-status" aria-live="polite">
        <div>
          <dt>{t("pplx.server")}</dt>
          <dd>
            <span className={`state-pill ${view.server}`}>{t(`pplx.server.${view.server}` as MessageKey)}</span>{" "}
            {view.serverSince && <span className="help-text">{t("pplx.since", { time: at(view.serverSince) })}</span>}
          </dd>
        </div>
        <div>
          <dt>{t("pplx.tunnel")}</dt>
          <dd>
            <span className={`state-pill ${view.tunnel}`}>{t(`pplx.tunnel.${view.tunnel}` as MessageKey)}</span>{" "}
            {view.tunnelSince && <span className="help-text">{t("pplx.since", { time: at(view.tunnelSince) })}</span>}
          </dd>
        </div>
        <div>
          <dt>{t("pplx.lastRequest")}</dt>
          <dd>
            {req ? (
              <span>
                <code>{req.name ?? req.method ?? req.http}</code> · HTTP {req.status} · {at(req.ts)}
                <span className="help-text">
                  {" "}
                  · {t("pplx.requestCount", { n: view.requests, failed: view.authFailures })}
                </span>
              </span>
            ) : (
              <span className="help-text">{t("pplx.noRequests")}</span>
            )}
          </dd>
        </div>
      </dl>

      {orphan && <p className="warn">{t("pplx.orphan")}</p>}
      {errorText && (
        <p className="error" role="alert">
          {t("pplx.failed", { error: errorText })}
          {lastLog ? ` — ${lastLog}` : ""}
        </p>
      )}

      <ol className="pplx-steps">
        <li>
          <strong>{t("pplx.step1")}</strong>
          <div className="pplx-row">
            <label className="pplx-port">
              {t("pplx.port")}
              <input
                type="text"
                inputMode="numeric"
                value={portText}
                onChange={(e) => setPortText(e.target.value)}
                disabled={serverOn}
                aria-invalid={port === null}
                aria-describedby="pplx-port-help"
              />
            </label>
            {serverOn ? (
              <button type="button" className="danger" onClick={() => void stopServer()} disabled={busy}>
                {t("pplx.stopServer")}
              </button>
            ) : (
              <button type="button" onClick={() => void startServer(false)} disabled={!bridge || busy || port === null}>
                {t("pplx.startServer")}
              </button>
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => void startServer(true)}
              disabled={!bridge || busy || port === null}
            >
              {t("pplx.rotate")}
            </button>
          </div>
          <p id="pplx-port-help" className={port === null ? "error" : "help-text"}>
            {port === null ? t("pplx.portInvalid") : view.localUrl ? t("pplx.local", { url: view.localUrl }) : t("pplx.portHelp")}
          </p>
        </li>

        <li>
          <strong>{t("pplx.step2")}</strong>
          {cloudflared === null && (
            <div className="pplx-install">
              <p className="warn">{t("pplx.noCloudflared")}</p>
              <div className="pplx-row">
                <code className="pplx-value">{CLOUDFLARED_INSTALL}</code>
                <button type="button" className="secondary" onClick={() => onCopy("install", CLOUDFLARED_INSTALL)}>
                  {copyLabel("install", "pplx.copy")}
                </button>
                <button type="button" className="link" onClick={() => void refreshStatus()}>
                  {t("pplx.recheck")}
                </button>
              </div>
            </div>
          )}
          <div className="pplx-row">
            {tunnelOn ? (
              <button type="button" className="danger" onClick={() => void stopTunnel()} disabled={busy}>
                {t("pplx.stopTunnel")}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void startTunnel()}
                disabled={!bridge || busy || view.server !== "listening" || orphan || cloudflared === null}
              >
                {t("pplx.startTunnel")}
              </button>
            )}
            {view.tunnel === "starting" && <span className="help-text">{t("pplx.tunnelWaiting")}</span>}
          </div>
          <p className="help-text">{t("pplx.tunnelHelp")}</p>
        </li>

        <li>
          <strong>{t("pplx.step3")}</strong>
          <p className="help-text">{t("pplx.where")}</p>
          <table className="pplx-fields">
            <tbody>
              <tr>
                <th scope="row">Name</th>
                <td>
                  <code className="pplx-value">{PERPLEXITY_CONNECTOR_NAME}</code>
                </td>
                <td />
              </tr>
              <tr>
                <th scope="row">MCP Server URL</th>
                <td>
                  {endpoint ? (
                    <code className="pplx-value">{endpoint}</code>
                  ) : (
                    <span className="pplx-pending">{t("pplx.urlPending")}</span>
                  )}
                </td>
                <td>
                  <button type="button" className="secondary" disabled={!endpoint} onClick={() => endpoint && onCopy("url", endpoint)}>
                    {copyLabel("url", "pplx.copy")}
                  </button>
                </td>
              </tr>
              <tr>
                <th scope="row">Authentication</th>
                <td>
                  <code className="pplx-value">API Key</code>
                </td>
                <td />
              </tr>
              <tr>
                <th scope="row">API Key</th>
                <td>
                  {view.token ? (
                    <code className="pplx-value">{showKey ? view.token : maskToken(view.token)}</code>
                  ) : (
                    <span className="pplx-pending">{t("pplx.keyPending")}</span>
                  )}
                </td>
                <td className="pplx-key-actions">
                  <button type="button" className="link" disabled={!view.token} onClick={() => setShowKey((v) => !v)}>
                    {showKey ? t("pplx.hide") : t("pplx.show")}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={!view.token}
                    onClick={() => view.token && onCopy("key", view.token)}
                  >
                    {copyLabel("key", "pplx.copy")}
                  </button>
                </td>
              </tr>
              <tr>
                <th scope="row">Transport</th>
                <td>
                  <code className="pplx-value">Streamable HTTP</code>
                </td>
                <td />
              </tr>
            </tbody>
          </table>
          <p className="help-text">{t("pplx.urlChanges")}</p>
        </li>
      </ol>

      <p className="help-text mcp-privacy">
        {t("pplx.security")}
        {view.auditLog ? ` ${t("pplx.audit", { path: view.auditLog })}` : ""}
      </p>

      {stdio && (
        <details className="pplx-portable">
          <summary>{t("pplx.portableTitle")}</summary>
          <p className="help-text">{t("pplx.portableHelp")}</p>
          <table className="pplx-fields">
            <tbody>
              {(
                [
                  ["Command", stdio.command],
                  ["Arguments", stdio.args],
                  ["Working directory", stdio.cwd],
                  ["Environment variables", stdio.env],
                ] as const
              ).map(([label, value]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td>
                    <pre className="pplx-value">{value}</pre>
                  </td>
                  <td>
                    <button type="button" className="secondary" onClick={() => onCopy(label, value)}>
                      {copyLabel(label, "pplx.copy")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
