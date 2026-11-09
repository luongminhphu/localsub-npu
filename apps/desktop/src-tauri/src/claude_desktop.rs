//! Claude Desktop's `claude_desktop_config.json`: find it, read it and write it back with a
//! backup. The JSON merge (adding only `mcpServers.localsub-npu`) happens in the UI, where it is
//! unit-tested; this side only touches the one fixed file, so the webview cannot write elsewhere.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

pub const CONFIG_FILE: &str = "claude_desktop_config.json";
pub const BACKUP_SUFFIX: &str = ".localsub.bak";
const MAX_CONFIG_BYTES: usize = 1_000_000;

#[derive(Debug, Clone, serde::Serialize)]
pub struct ConfigFile {
    pub path: PathBuf,
    pub exists: bool,
    pub text: Option<String>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct WriteResult {
    pub path: PathBuf,
    pub backup: Option<PathBuf>,
}

/// Microsoft Store (MSIX) builds of Claude live in `%LOCALAPPDATA%\Packages\Claude_<id>` and
/// keep their roaming data under `LocalCache\Roaming\Claude`.
pub fn msix_config_paths(package_names: &[String], packages_dir: &Path) -> Vec<PathBuf> {
    let mut names: Vec<&String> = package_names
        .iter()
        .filter(|name| name.starts_with("Claude_"))
        .collect();
    names.sort();
    names
        .into_iter()
        .map(|name| {
            packages_dir
                .join(name)
                .join("LocalCache")
                .join("Roaming")
                .join("Claude")
                .join(CONFIG_FILE)
        })
        .collect()
}

/// An existing MSIX config wins (that build ignores `%APPDATA%`); otherwise the standard
/// installer's `%APPDATA%\Claude\claude_desktop_config.json`.
pub fn resolve_config_path(appdata: Option<&Path>, msix: &[PathBuf]) -> Option<PathBuf> {
    if let Some(found) = msix.iter().find(|p| p.is_file()) {
        return Some(found.clone());
    }
    appdata.map(|dir| dir.join("Claude").join(CONFIG_FILE))
}

pub fn config_path() -> Result<PathBuf, String> {
    let appdata = std::env::var_os("APPDATA").map(PathBuf::from);
    let msix = std::env::var_os("LOCALAPPDATA")
        .map(|local| {
            let packages = Path::new(&local).join("Packages");
            let names: Vec<String> = fs::read_dir(&packages)
                .map(|entries| {
                    entries
                        .filter_map(|e| e.ok())
                        .map(|e| e.file_name().to_string_lossy().into_owned())
                        .collect()
                })
                .unwrap_or_default();
            msix_config_paths(&names, &packages)
        })
        .unwrap_or_default();
    resolve_config_path(appdata.as_deref(), &msix)
        .ok_or_else(|| "APPDATA is not set: Claude Desktop config not found".to_string())
}

pub fn read_config(path: &Path) -> Result<ConfigFile, String> {
    if !path.is_file() {
        return Ok(ConfigFile {
            path: path.to_path_buf(),
            exists: false,
            text: None,
        });
    }
    let bytes = fs::read(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    if bytes.len() > MAX_CONFIG_BYTES {
        return Err(format!("{} is larger than 1 MB", path.display()));
    }
    // Notepad may save a UTF-8 BOM; JSON.parse in the UI would reject it.
    let text = String::from_utf8_lossy(&bytes)
        .trim_start_matches('\u{feff}')
        .to_string();
    Ok(ConfigFile {
        path: path.to_path_buf(),
        exists: true,
        text: Some(text),
    })
}

pub fn backup_path(path: &Path) -> PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(BACKUP_SUFFIX);
    path.with_file_name(name)
}

/// Copy the current file to `<name>.localsub.bak`, then replace it atomically.
pub fn write_config(path: &Path, text: &str) -> Result<WriteResult, String> {
    if text.len() > MAX_CONFIG_BYTES {
        return Err("config text is larger than 1 MB".into());
    }
    if !text.trim_start().starts_with('{') {
        return Err("config must be a JSON object".into());
    }
    let dir = path
        .parent()
        .ok_or_else(|| format!("invalid config path {}", path.display()))?;
    fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let backup = if path.is_file() {
        let target = backup_path(path);
        fs::copy(path, &target).map_err(|e| format!("cannot back up {}: {e}", path.display()))?;
        Some(target)
    } else {
        None
    };
    let tmp = path.with_extension("json.localsub.tmp");
    {
        let mut file =
            fs::File::create(&tmp).map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
        file.write_all(text.as_bytes())
            .and_then(|_| file.sync_all())
            .map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
    }
    fs::rename(&tmp, path).map_err(|e| {
        let _ = fs::remove_file(&tmp);
        format!("cannot replace {}: {e}", path.display())
    })?;
    Ok(WriteResult {
        path: path.to_path_buf(),
        backup,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "localsub-claude-{name}-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn msix_paths_only_for_claude_packages() {
        let names = vec![
            "Microsoft.WindowsTerminal_8wekyb3d8bbwe".to_string(),
            "Claude_pzs8sxrjxfjjc".to_string(),
        ];
        let paths = msix_config_paths(&names, Path::new("P"));
        assert_eq!(paths.len(), 1);
        assert!(paths[0].ends_with(Path::new(
            "Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/claude_desktop_config.json"
        )));
    }

    #[test]
    fn resolve_prefers_existing_msix_config() {
        let dir = temp_dir("resolve");
        let msix = dir.join("pkg").join(CONFIG_FILE);
        let appdata = dir.join("roaming");
        assert_eq!(
            resolve_config_path(Some(&appdata), std::slice::from_ref(&msix)),
            Some(appdata.join("Claude").join(CONFIG_FILE))
        );
        fs::create_dir_all(msix.parent().unwrap()).unwrap();
        fs::write(&msix, "{}").unwrap();
        assert_eq!(
            resolve_config_path(Some(&appdata), std::slice::from_ref(&msix)),
            Some(msix.clone())
        );
        assert_eq!(resolve_config_path(None, &[]), None);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn read_missing_and_strip_bom() {
        let dir = temp_dir("read");
        let path = dir.join("Claude").join(CONFIG_FILE);
        let missing = read_config(&path).unwrap();
        assert!(!missing.exists && missing.text.is_none());
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, "\u{feff}{\"a\":1}").unwrap();
        assert_eq!(
            read_config(&path).unwrap().text.as_deref(),
            Some("{\"a\":1}")
        );
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn write_creates_then_backs_up() {
        let dir = temp_dir("write");
        let path = dir.join("Claude").join(CONFIG_FILE);
        let first = write_config(&path, "{\"v\":1}").unwrap();
        assert!(first.backup.is_none());
        let second = write_config(&path, "{\"v\":2}").unwrap();
        let backup = second.backup.unwrap();
        assert_eq!(fs::read_to_string(&backup).unwrap(), "{\"v\":1}");
        assert_eq!(fs::read_to_string(&path).unwrap(), "{\"v\":2}");
        assert!(backup
            .to_string_lossy()
            .ends_with("claude_desktop_config.json.localsub.bak"));
        assert!(write_config(&path, "not json").is_err());
        assert_eq!(fs::read_to_string(&path).unwrap(), "{\"v\":2}");
        let _ = fs::remove_dir_all(dir);
    }
}
