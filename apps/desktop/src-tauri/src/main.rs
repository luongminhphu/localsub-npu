#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod claude_desktop;
mod mcp_http;
mod metrics;
mod worker;

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Default)]
struct JobState {
    child: Arc<Mutex<Option<Child>>>,
}

/// Model downloads use their own slot so a download never blocks (or cancels) a job.
#[derive(Default)]
struct DownloadState {
    child: Arc<Mutex<Option<Child>>>,
}

/// Perplexity remote access: the worker's HTTP MCP server and the HTTPS tunnel in front of it.
#[derive(Default)]
struct McpHttpState {
    server: Arc<Mutex<Option<Child>>>,
    tunnel: Arc<Mutex<Option<Child>>>,
}

#[derive(Clone, serde::Serialize)]
struct McpHttpStatus {
    server: bool,
    tunnel: bool,
    cloudflared: Option<String>,
}

#[derive(Clone, serde::Serialize)]
struct ExitPayload {
    code: Option<i32>,
    cancelled: bool,
}

fn run_to_string(args: &[String], stdin: Option<&str>) -> Result<String, String> {
    let location = worker::locate()?;
    let mut cmd = worker::command(&location, args);
    if stdin.is_some() {
        cmd.stdin(Stdio::piped());
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to start worker: {e}"))?;
    if let Some(input) = stdin {
        let mut pipe = child.stdin.take().ok_or("worker stdin unavailable")?;
        pipe.write_all(input.as_bytes())
            .map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    if output.status.success() {
        Ok(stdout)
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let last = stderr.lines().last().unwrap_or("worker failed").to_string();
        Err(last)
    }
}

/// Latest CPU/GPU/NPU samples newer than `since_ms`; starts the sampler on first call.
#[tauri::command]
fn system_metrics(
    monitor: State<'_, metrics::Monitor>,
    since_ms: Option<u64>,
) -> metrics::Snapshot {
    monitor.snapshot(since_ms)
}

#[tauri::command]
fn worker_location() -> Result<worker::WorkerLocation, String> {
    worker::locate()
}

#[tauri::command]
async fn run_diagnostics() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| run_to_string(&["diagnostics".into()], None))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn list_models() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| run_to_string(&["models".into(), "list".into()], None))
        .await
        .map_err(|e| e.to_string())?
}

/// Spawn the worker, stream stdout lines as `event` and stderr as `log`, emit `exit` at the end.
fn spawn_streaming(
    app: AppHandle,
    slot_ref: &Arc<Mutex<Option<Child>>>,
    args: Vec<String>,
    names: (&'static str, &'static str, &'static str),
    busy: &str,
) -> Result<(), String> {
    let (event, log, exit) = names;
    let mut slot = slot_ref.lock().map_err(|_| "worker lock poisoned")?;
    if let Some(child) = slot.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err(busy.to_string());
        }
    }
    let location = worker::locate()?;
    let mut child = worker::command(&location, &args)
        .spawn()
        .map_err(|e| format!("failed to start worker: {e}"))?;
    let stdout = child.stdout.take().ok_or("worker stdout unavailable")?;
    let stderr = child.stderr.take().ok_or("worker stderr unavailable")?;
    *slot = Some(child);

    let log_app = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = log_app.emit(log, line);
        }
    });

    let shared = Arc::clone(slot_ref);
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                let _ = app.emit(event, line);
            }
        }
        let status = shared
            .lock()
            .ok()
            .and_then(|mut guard| guard.take())
            .and_then(|mut child| child.wait().ok());
        let payload = match status {
            Some(s) => ExitPayload {
                code: s.code(),
                cancelled: false,
            },
            None => ExitPayload {
                code: None,
                cancelled: true,
            },
        };
        let _ = app.emit(exit, payload);
    });
    Ok(())
}

fn kill_slot(slot_ref: &Arc<Mutex<Option<Child>>>) -> Result<bool, String> {
    let mut slot = slot_ref.lock().map_err(|_| "worker lock poisoned")?;
    match slot.as_mut() {
        Some(child) => {
            child.kill().map_err(|e| e.to_string())?;
            let _ = child.wait();
            *slot = None;
            Ok(true)
        }
        None => Ok(false),
    }
}

#[tauri::command]
fn start_job(
    app: AppHandle,
    state: State<'_, JobState>,
    job: worker::JobArgs,
) -> Result<(), String> {
    let args = worker::transcribe_args(&job)?;
    spawn_streaming(
        app,
        &state.child,
        args,
        ("job-event", "job-log", "job-exit"),
        "a job is already running",
    )
}

#[tauri::command]
fn cancel_job(state: State<'_, JobState>) -> Result<bool, String> {
    kill_slot(&state.child)
}

#[tauri::command]
fn download_model(
    app: AppHandle,
    state: State<'_, DownloadState>,
    model_id: String,
) -> Result<(), String> {
    let args = worker::download_args(&model_id)?;
    spawn_streaming(
        app,
        &state.child,
        args,
        ("download-event", "download-log", "download-exit"),
        "a model download is already running",
    )
}

#[tauri::command]
fn cancel_download(state: State<'_, DownloadState>) -> Result<bool, String> {
    // Partial files stay on disk; the next download of the same model resumes them.
    kill_slot(&state.child)
}

#[tauri::command]
async fn remove_model(model_id: String) -> Result<String, String> {
    let model_id = worker::model_id_arg(&model_id)?;
    tauri::async_runtime::spawn_blocking(move || {
        run_to_string(&["models".into(), "remove".into(), model_id], None)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn job_history(limit: u32) -> Result<String, String> {
    let limit = limit.clamp(1, 200).to_string();
    tauri::async_runtime::spawn_blocking(move || {
        run_to_string(&["history".into(), "--limit".into(), limit], None)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Performance findings over the last `limit` jobs (local UI: real file names, not redacted).
#[tauri::command]
async fn analyze_performance(limit: u32) -> Result<String, String> {
    let limit = limit.clamp(1, 200).to_string();
    tauri::async_runtime::spawn_blocking(move || {
        run_to_string(&["analyze".into(), "--limit".into(), limit], None)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Launch command for external MCP clients; read-only, nothing is started here.
#[tauri::command]
fn mcp_config() -> Result<worker::McpLaunch, String> {
    worker::mcp_config()
}

/// Claude Desktop's config file (path + current text); nothing is changed here.
#[tauri::command]
fn claude_desktop_config() -> Result<claude_desktop::ConfigFile, String> {
    claude_desktop::read_config(&claude_desktop::config_path()?)
}

/// Write the merged Claude Desktop config (built and confirmed in the UI) after a backup.
#[tauri::command]
fn write_claude_desktop_config(text: String) -> Result<claude_desktop::WriteResult, String> {
    claude_desktop::write_config(&claude_desktop::config_path()?, &text)
}

#[tauri::command]
async fn export_project(
    project_json: String,
    output_base: String,
    formats: Vec<String>,
) -> Result<String, String> {
    let formats = worker::formats_arg(&formats)?;
    if output_base.trim().is_empty() {
        return Err("choose an output location".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let args = vec![
            "export".into(),
            "--input".into(),
            "-".into(),
            "--output-base".into(),
            output_base,
            "--formats".into(),
            formats,
        ];
        run_to_string(&args, Some(&project_json))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn load_project(path: String) -> Result<String, String> {
    if !path.to_lowercase().ends_with(".json") {
        return Err("choose a .localsub.json project file".into());
    }
    std::fs::read_to_string(&path).map_err(|e| format!("cannot read {path}: {e}"))
}

fn slot_running(slot_ref: &Arc<Mutex<Option<Child>>>) -> bool {
    slot_ref
        .lock()
        .ok()
        .and_then(|mut slot| {
            slot.as_mut()
                .map(|child| matches!(child.try_wait(), Ok(None)))
        })
        .unwrap_or(false)
}

fn locate_cloudflared() -> Option<std::path::PathBuf> {
    let path_var = std::env::var("PATH").ok();
    mcp_http::find_cloudflared(path_var.as_deref(), &mcp_http::cloudflared_candidates())
}

/// Start the read-only MCP server on http://127.0.0.1:<port>/mcp (asked for confirmation in the UI).
#[tauri::command]
fn start_mcp_http(
    app: AppHandle,
    state: State<'_, McpHttpState>,
    port: u16,
    rotate: bool,
) -> Result<(), String> {
    let args = mcp_http::http_args(port, rotate, std::process::id())?;
    spawn_streaming(
        app,
        &state.server,
        args,
        ("mcp-http-event", "mcp-http-log", "mcp-http-exit"),
        "the MCP HTTP server is already running",
    )
}

/// Stops the tunnel first (nothing should stay public), then the server.
#[tauri::command]
fn stop_mcp_http(state: State<'_, McpHttpState>) -> Result<bool, String> {
    let tunnel = kill_slot(&state.tunnel)?;
    let server = kill_slot(&state.server)?;
    Ok(tunnel || server)
}

#[tauri::command]
fn mcp_http_status(state: State<'_, McpHttpState>) -> McpHttpStatus {
    McpHttpStatus {
        server: slot_running(&state.server),
        tunnel: slot_running(&state.tunnel),
        cloudflared: locate_cloudflared().map(|p| p.to_string_lossy().into_owned()),
    }
}

/// Cloudflare quick tunnel to the local server; emits `mcp-tunnel-event` with the public URL.
#[tauri::command]
fn start_tunnel(app: AppHandle, state: State<'_, McpHttpState>, port: u16) -> Result<(), String> {
    if !slot_running(&state.server) {
        return Err("start the MCP HTTP server first".into());
    }
    let exe = locate_cloudflared().ok_or(
        "CLOUDFLARED_MISSING: install it with `winget install --id Cloudflare.cloudflared`",
    )?;
    let mut slot = state.tunnel.lock().map_err(|_| "tunnel lock poisoned")?;
    if let Some(child) = slot.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err("the HTTPS tunnel is already running".into());
        }
    }
    let mut child = mcp_http::cloudflared_command(&exe, port)?
        .spawn()
        .map_err(|e| format!("failed to start cloudflared: {e}"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or("cloudflared stdout unavailable")?;
    let stderr = child
        .stderr
        .take()
        .ok_or("cloudflared stderr unavailable")?;
    *slot = Some(child);

    let out_app = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(url) = mcp_http::parse_tunnel_url(&line) {
                let _ = out_app.emit("mcp-tunnel-event", url);
            }
        }
    });
    let shared = Arc::clone(&state.tunnel);
    thread::spawn(move || {
        // cloudflared logs (including the URL banner) go to stderr
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if let Some(url) = mcp_http::parse_tunnel_url(&line) {
                let _ = app.emit("mcp-tunnel-event", url);
            }
            let _ = app.emit("mcp-tunnel-log", line);
        }
        let status = shared
            .lock()
            .ok()
            .and_then(|mut guard| guard.take())
            .and_then(|mut child| child.wait().ok());
        let payload = match status {
            Some(s) => ExitPayload {
                code: s.code(),
                cancelled: false,
            },
            None => ExitPayload {
                code: None,
                cancelled: true,
            },
        };
        let _ = app.emit("mcp-tunnel-exit", payload);
    });
    Ok(())
}

#[tauri::command]
fn stop_tunnel(state: State<'_, McpHttpState>) -> Result<bool, String> {
    kill_slot(&state.tunnel)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let paths = app.path();
            worker::set_app_paths(worker::AppPaths {
                resource_dir: paths.resource_dir().ok(),
                data_dir: paths.app_local_data_dir().ok(),
            });
            Ok(())
        })
        .manage(JobState::default())
        .manage(DownloadState::default())
        .manage(metrics::Monitor::default())
        .manage(McpHttpState::default())
        .invoke_handler(tauri::generate_handler![
            worker_location,
            system_metrics,
            run_diagnostics,
            list_models,
            start_job,
            cancel_job,
            download_model,
            cancel_download,
            remove_model,
            job_history,
            analyze_performance,
            mcp_config,
            claude_desktop_config,
            write_claude_desktop_config,
            start_mcp_http,
            stop_mcp_http,
            mcp_http_status,
            start_tunnel,
            stop_tunnel,
            export_project,
            load_project
        ])
        .build(tauri::generate_context!())
        .expect("error while building LocalSub NPU")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                // Never leave a public tunnel or the HTTP server running after the app closes.
                let state = app.state::<McpHttpState>();
                let _ = kill_slot(&state.tunnel);
                let _ = kill_slot(&state.server);
            }
        });
}
