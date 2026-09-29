//! Locates and runs the inference worker.
//!
//! Lookup order: explicit env overrides (`LOCALSUB_WORKER_DIR` / `LOCALSUB_WORKER_PYTHON`), then the
//! frozen worker bundled by the installer (`<resource_dir>/worker/localsub-worker.exe`), then the
//! source checkout (`apps/inference` + `.venv`, run as `python -m app.main`).
//!
//! The worker is always spawned with an explicit argument vector (never a shell string) and
//! every user-selectable value is checked against an allow-list before it reaches the process.

use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub const LANGUAGES: &[&str] = &["auto", "en", "ja", "vi"];
pub const TARGETS: &[&str] = &["en", "vi"];
pub const DEVICES: &[&str] = &["auto", "NPU", "GPU", "CPU"];
pub const TASKS: &[&str] = &["transcribe", "translate"];
pub const FORMATS: &[&str] = &["srt", "vtt", "txt", "json"];

#[derive(Debug, Clone, serde::Serialize)]
pub struct WorkerLocation {
    /// Python interpreter (source mode) or the frozen worker executable (bundled mode).
    pub python: PathBuf,
    pub worker_dir: PathBuf,
    pub bundled: bool,
}

/// Directories known only at runtime (set once from the Tauri `setup` hook).
#[derive(Debug, Clone, Default)]
pub struct AppPaths {
    pub resource_dir: Option<PathBuf>,
    pub data_dir: Option<PathBuf>,
}

static APP_PATHS: OnceLock<AppPaths> = OnceLock::new();

pub fn set_app_paths(paths: AppPaths) {
    let _ = APP_PATHS.set(paths);
}

fn app_paths() -> AppPaths {
    APP_PATHS.get().cloned().unwrap_or_default()
}

pub const BUNDLED_EXE: &str = if cfg!(windows) {
    "localsub-worker.exe"
} else {
    "localsub-worker"
};

/// Frozen worker shipped by the installer, if present.
pub fn bundled_worker(resource_dir: &Path) -> Option<PathBuf> {
    let exe = resource_dir.join("worker").join(BUNDLED_EXE);
    exe.is_file().then_some(exe)
}

#[derive(Debug, Clone, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JobArgs {
    pub source: String,
    pub language: String,
    pub task: String,
    pub model: String,
    pub device: String,
    pub translate_to: Option<String>,
    pub translation_model: Option<String>,
    pub translation_device: Option<String>,
    pub formats: Vec<String>,
}

fn venv_python(worker_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        worker_dir.join(".venv").join("Scripts").join("python.exe")
    } else {
        worker_dir.join(".venv").join("bin").join("python")
    }
}

fn find_worker_dir() -> Option<PathBuf> {
    if let Ok(dir) = env::var("LOCALSUB_WORKER_DIR") {
        let dir = PathBuf::from(dir);
        if dir.join("app").join("main.py").is_file() {
            return Some(dir);
        }
    }
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = env::current_exe() {
        starts.extend(exe.parent().map(Path::to_path_buf));
    }
    if let Ok(cwd) = env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        for dir in start.ancestors() {
            let candidate = dir.join("apps").join("inference");
            if candidate.join("app").join("main.py").is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

pub fn locate() -> Result<WorkerLocation, String> {
    locate_with(app_paths().resource_dir.as_deref())
}

pub fn locate_with(resource_dir: Option<&Path>) -> Result<WorkerLocation, String> {
    let overridden = env::var_os("LOCALSUB_WORKER_DIR").is_some()
        || env::var_os("LOCALSUB_WORKER_PYTHON").is_some();
    if !overridden {
        if let Some(exe) = resource_dir.and_then(bundled_worker) {
            let worker_dir = exe.parent().map(Path::to_path_buf).unwrap_or_default();
            return Ok(WorkerLocation {
                python: exe,
                worker_dir,
                bundled: true,
            });
        }
    }
    let worker_dir = find_worker_dir().ok_or_else(|| {
        "Cannot find apps/inference. Set LOCALSUB_WORKER_DIR to the worker folder.".to_string()
    })?;
    let python = match env::var("LOCALSUB_WORKER_PYTHON") {
        Ok(p) => PathBuf::from(p),
        Err(_) => {
            let venv = venv_python(&worker_dir);
            if venv.is_file() {
                venv
            } else {
                return Err(format!(
                    "Worker virtualenv not found at {}. Run scripts/setup-wave2.ps1 first.",
                    venv.display()
                ));
            }
        }
    };
    Ok(WorkerLocation {
        python,
        worker_dir,
        bundled: false,
    })
}

/// Data directories. Source mode: `<repo>/data/{models,output}` (same as the PowerShell scripts)
/// unless set in the real environment or the repository `.env`. Bundled mode: the per-user app data
/// folder (`%LOCALAPPDATA%\com.luongminhphu.localsubnpu\{models,output}`) unless set in the env.
pub fn data_dir_defaults(
    location: &WorkerLocation,
    app_data: Option<&Path>,
) -> Vec<(&'static str, PathBuf)> {
    let (base, dotenv) = if location.bundled {
        match app_data {
            Some(dir) => (dir.to_path_buf(), String::new()),
            None => return Vec::new(),
        }
    } else {
        let Some(repo) = location.worker_dir.parent().and_then(Path::parent) else {
            return Vec::new();
        };
        let dotenv = std::fs::read_to_string(repo.join(".env")).unwrap_or_default();
        (repo.join("data"), dotenv)
    };
    let defined = |key: &str| {
        env::var_os(key).is_some()
            || dotenv
                .lines()
                .any(|l| l.trim_start().starts_with(key) && l.contains('='))
    };
    [
        ("LOCALSUB_MODEL_DIR", base.join("models")),
        ("LOCALSUB_OUTPUT_DIR", base.join("output")),
    ]
    .into_iter()
    .filter(|(key, _)| !defined(key))
    .collect()
}

pub fn command(location: &WorkerLocation, args: &[String]) -> Command {
    let mut cmd = Command::new(&location.python);
    cmd.envs(data_dir_defaults(location, app_paths().data_dir.as_deref()));
    if !location.bundled {
        cmd.arg("-m").arg("app.main");
    }
    cmd.args(args)
        .current_dir(&location.worker_dir)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

fn allowed(field: &str, value: &str, list: &[&str]) -> Result<(), String> {
    if list.contains(&value) {
        Ok(())
    } else {
        Err(format!("invalid {field}: {value}"))
    }
}

fn model_id(field: &str, value: &str) -> Result<(), String> {
    // First char alphanumeric: rejects "..", ".hidden" and other path tricks.
    let ok = value
        .chars()
        .next()
        .is_some_and(|c| c.is_ascii_alphanumeric())
        && value.len() <= 64
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.');
    if ok {
        Ok(())
    } else {
        Err(format!("invalid {field}: {value}"))
    }
}

/// Validated model id for `models download|remove`; the worker re-checks it against its catalog.
pub fn model_id_arg(value: &str) -> Result<String, String> {
    model_id("model_id", value)?;
    Ok(value.to_string())
}

pub fn download_args(value: &str) -> Result<Vec<String>, String> {
    let id = model_id_arg(value)?;
    Ok(vec![
        "models".into(),
        "download".into(),
        id,
        "--events".into(),
    ])
}

pub fn formats_arg(formats: &[String]) -> Result<String, String> {
    if formats.is_empty() {
        return Err("choose at least one export format".into());
    }
    for f in formats {
        allowed("format", f, FORMATS)?;
    }
    Ok(formats.join(","))
}

/// Build the `transcribe` argument vector after validating every field.
pub fn transcribe_args(job: &JobArgs) -> Result<Vec<String>, String> {
    if job.source.trim().is_empty() || !Path::new(&job.source).is_file() {
        return Err(format!("source file not found: {}", job.source));
    }
    allowed("language", &job.language, LANGUAGES)?;
    allowed("task", &job.task, TASKS)?;
    allowed("device", &job.device, DEVICES)?;
    model_id("model", &job.model)?;
    let mut args = vec![
        "transcribe".to_string(),
        job.source.clone(),
        "--language".into(),
        job.language.clone(),
        "--task".into(),
        job.task.clone(),
        "--model".into(),
        job.model.clone(),
        "--device".into(),
        job.device.clone(),
        "--formats".into(),
        formats_arg(&job.formats)?,
        "--events".into(),
    ];
    if let Some(target) = job.translate_to.as_deref().filter(|t| !t.is_empty()) {
        allowed("translate_to", target, TARGETS)?;
        args.extend(["--translate-to".into(), target.to_string()]);
        if let Some(m) = job.translation_model.as_deref().filter(|m| !m.is_empty()) {
            model_id("translation_model", m)?;
            args.extend(["--translation-model".into(), m.to_string()]);
        }
        if let Some(d) = job.translation_device.as_deref().filter(|d| !d.is_empty()) {
            allowed("translation_device", d, DEVICES)?;
            args.extend(["--translation-device".into(), d.to_string()]);
        }
    }
    Ok(args)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(source: &str) -> JobArgs {
        JobArgs {
            source: source.into(),
            language: "en".into(),
            task: "transcribe".into(),
            model: "whisper-small-int8".into(),
            device: "auto".into(),
            translate_to: Some("vi".into()),
            translation_model: Some("qwen3-8b-int4-cw".into()),
            translation_device: None,
            formats: vec!["srt".into(), "vtt".into()],
        }
    }

    fn fake_bundle(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(name);
        let worker = root.join("worker");
        std::fs::create_dir_all(&worker).unwrap();
        std::fs::write(worker.join(BUNDLED_EXE), b"stub").unwrap();
        root
    }

    #[test]
    fn detects_bundled_worker_and_uses_app_data_dirs() {
        let root = fake_bundle("localsub-bundle-test");
        let exe = bundled_worker(&root).expect("bundled worker");
        assert!(exe.ends_with(Path::new("worker").join(BUNDLED_EXE)));
        assert!(bundled_worker(&std::env::temp_dir().join("localsub-no-bundle")).is_none());

        let location = WorkerLocation {
            python: exe.clone(),
            worker_dir: exe.parent().unwrap().to_path_buf(),
            bundled: true,
        };
        let data = std::env::temp_dir().join("localsub-appdata");
        let defaults = data_dir_defaults(&location, Some(&data));
        for (key, path) in &defaults {
            assert!(path.starts_with(&data), "{key} should live in app data");
        }
        // Frozen executable is run directly, without `-m app.main`.
        let cmd = command(&location, &["models".into(), "list".into()]);
        let args: Vec<_> = cmd
            .get_args()
            .map(|a| a.to_string_lossy().to_string())
            .collect();
        assert_eq!(args, ["models", "list"]);
        assert_eq!(cmd.get_program(), exe.as_os_str());
    }

    #[test]
    fn source_mode_runs_python_module() {
        let location = WorkerLocation {
            python: PathBuf::from("python"),
            worker_dir: PathBuf::from("repo/apps/inference"),
            bundled: false,
        };
        let cmd = command(&location, &["diagnostics".into()]);
        let args: Vec<_> = cmd
            .get_args()
            .map(|a| a.to_string_lossy().to_string())
            .collect();
        assert_eq!(args, ["-m", "app.main", "diagnostics"]);
    }

    /// Runs the real frozen worker. Set LOCALSUB_TEST_BUNDLE_DIR to a folder containing
    /// `worker/localsub-worker(.exe)` (the PyInstaller output renamed to `worker`).
    #[test]
    #[ignore]
    fn spawns_real_bundled_worker() {
        let dir = PathBuf::from(std::env::var("LOCALSUB_TEST_BUNDLE_DIR").expect("set bundle dir"));
        let exe = bundled_worker(&dir).expect("bundled worker missing");
        let location = WorkerLocation {
            python: exe.clone(),
            worker_dir: exe.parent().unwrap().to_path_buf(),
            bundled: true,
        };
        let out = command(&location, &["models".into(), "list".into()])
            .output()
            .expect("spawn frozen worker");
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert!(String::from_utf8_lossy(&out.stdout).contains("whisper-small-int8"));
    }

    #[test]
    fn builds_argument_vector() {
        let file = std::env::temp_dir().join("localsub-arg-test.mp4");
        std::fs::write(&file, b"x").unwrap();
        let args = transcribe_args(&job(file.to_str().unwrap())).unwrap();
        assert_eq!(args[0], "transcribe");
        assert!(args.windows(2).any(|w| w == ["--formats", "srt,vtt"]));
        assert!(args.windows(2).any(|w| w == ["--translate-to", "vi"]));
        assert!(args.contains(&"--events".to_string()));
    }

    #[test]
    fn rejects_unknown_values() {
        let file = std::env::temp_dir().join("localsub-arg-test2.mp4");
        std::fs::write(&file, b"x").unwrap();
        let mut bad = job(file.to_str().unwrap());
        bad.device = "TPU".into();
        assert!(transcribe_args(&bad).is_err());
        let mut bad = job(file.to_str().unwrap());
        bad.model = "x; rm -rf".into();
        assert!(transcribe_args(&bad).is_err());
        let mut bad = job(file.to_str().unwrap());
        bad.formats = vec!["exe".into()];
        assert!(transcribe_args(&bad).is_err());
        assert!(transcribe_args(&job("/definitely/missing.mp4")).is_err());
    }

    #[test]
    fn hybrid_translation_device_is_rejected() {
        // GPU+NPU hybrid translation was removed in 0.8.0 (slower than GPU alone).
        let file = std::env::temp_dir().join("localsub-arg-hybrid.mp4");
        std::fs::write(&file, b"x").unwrap();
        let mut hybrid = job(file.to_str().unwrap());
        hybrid.translation_device = Some("HYBRID".into());
        assert!(transcribe_args(&hybrid).is_err());
    }

    /// Spawns the real worker. Run with LOCALSUB_WORKER_DIR/LOCALSUB_WORKER_PYTHON set:
    /// `cargo test -- --ignored`.
    #[test]
    fn accepts_japanese_as_spoken_language_only() {
        assert!(allowed("language", "ja", LANGUAGES).is_ok());
        assert!(allowed("translate_to", "ja", TARGETS).is_err());
    }

    #[test]
    fn validates_model_ids_for_download_and_remove() {
        assert_eq!(
            download_args("whisper-base-int8").unwrap(),
            vec!["models", "download", "whisper-base-int8", "--events"]
        );
        for bad in [
            "",
            "..",
            ".x",
            "../etc",
            "a/b",
            "a\\b",
            "a b",
            &"x".repeat(65),
        ] {
            assert!(model_id_arg(bad).is_err(), "accepted {bad:?}");
        }
    }

    #[test]
    #[ignore]
    fn spawns_real_worker_models_list() {
        let location = locate().expect("worker not found");
        let out = command(&location, &["models".into(), "list".into()])
            .output()
            .expect("spawn failed");
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let text = String::from_utf8_lossy(&out.stdout);
        assert!(text.contains("whisper-small-int8") && text.contains("qwen3-8b-int4-cw"));
    }
}
