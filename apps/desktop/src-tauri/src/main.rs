#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod worker;

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use tauri::{AppHandle, Emitter, State};

#[derive(Default)]
struct JobState {
    child: Arc<Mutex<Option<Child>>>,
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

#[tauri::command]
fn start_job(
    app: AppHandle,
    state: State<'_, JobState>,
    job: worker::JobArgs,
) -> Result<(), String> {
    let mut slot = state.child.lock().map_err(|_| "job lock poisoned")?;
    if let Some(child) = slot.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err("a job is already running".into());
        }
    }
    let args = worker::transcribe_args(&job)?;
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
            let _ = log_app.emit("job-log", line);
        }
    });

    let shared = Arc::clone(&state.child);
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                let _ = app.emit("job-event", line);
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
        let _ = app.emit("job-exit", payload);
    });
    Ok(())
}

#[tauri::command]
fn cancel_job(state: State<'_, JobState>) -> Result<bool, String> {
    let mut slot = state.child.lock().map_err(|_| "job lock poisoned")?;
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

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(JobState::default())
        .invoke_handler(tauri::generate_handler![
            worker_location,
            run_diagnostics,
            list_models,
            start_job,
            cancel_job,
            export_project,
            load_project
        ])
        .run(tauri::generate_context!())
        .expect("error while running LocalSub NPU");
}
