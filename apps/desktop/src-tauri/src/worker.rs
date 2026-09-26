//! Locates and runs the Python inference worker (`python -m app.main`).
//!
//! The worker is always spawned with an explicit argument vector (never a shell string) and
//! every user-selectable value is checked against an allow-list before it reaches the process.

use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

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
    pub python: PathBuf,
    pub worker_dir: PathBuf,
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
    Ok(WorkerLocation { python, worker_dir })
}

/// Data directories default to `<repo>/data/{models,output}` (same as the PowerShell scripts)
/// unless set in the real environment or in the repository `.env` file.
fn data_dir_defaults(worker_dir: &Path) -> Vec<(&'static str, PathBuf)> {
    let Some(repo) = worker_dir.parent().and_then(Path::parent) else {
        return Vec::new();
    };
    let dotenv = std::fs::read_to_string(repo.join(".env")).unwrap_or_default();
    let defined = |key: &str| {
        env::var_os(key).is_some()
            || dotenv
                .lines()
                .any(|l| l.trim_start().starts_with(key) && l.contains('='))
    };
    [
        ("LOCALSUB_MODEL_DIR", repo.join("data").join("models")),
        ("LOCALSUB_OUTPUT_DIR", repo.join("data").join("output")),
    ]
    .into_iter()
    .filter(|(key, _)| !defined(key))
    .collect()
}

pub fn command(location: &WorkerLocation, args: &[String]) -> Command {
    let mut cmd = Command::new(&location.python);
    cmd.envs(data_dir_defaults(&location.worker_dir));
    cmd.arg("-m")
        .arg("app.main")
        .args(args)
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
    let ok = !value.is_empty()
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

    /// Spawns the real worker. Run with LOCALSUB_WORKER_DIR/LOCALSUB_WORKER_PYTHON set:
    /// `cargo test -- --ignored`.
    #[test]
    fn accepts_japanese_as_spoken_language_only() {
        assert!(allowed("language", "ja", LANGUAGES).is_ok());
        assert!(allowed("translate_to", "ja", TARGETS).is_err());
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
