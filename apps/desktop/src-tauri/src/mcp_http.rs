//! Remote MCP access for Perplexity: the worker's Streamable HTTP server on 127.0.0.1 plus an
//! optional Cloudflare quick tunnel that gives it a public HTTPS URL. Pure helpers live here so
//! they are unit-testable; process management is in `main.rs`.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

pub const DEFAULT_PORT: u16 = 8765;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub fn validate_port(port: u16) -> Result<u16, String> {
    if port < 1024 {
        return Err(format!("port must be 1024-65535, got {port}"));
    }
    Ok(port)
}

/// Worker arguments for `mcp --http`; `--parent-pid` stops the server if the app dies.
pub fn http_args(port: u16, rotate: bool, parent_pid: u32) -> Result<Vec<String>, String> {
    let port = validate_port(port)?;
    let mut args = vec![
        "mcp".to_string(),
        "--http".to_string(),
        "--port".to_string(),
        port.to_string(),
        "--events".to_string(),
        "--parent-pid".to_string(),
        parent_pid.to_string(),
    ];
    if rotate {
        args.push("--rotate-token".into());
    }
    Ok(args)
}

pub fn cloudflared_args(port: u16) -> Result<Vec<String>, String> {
    let port = validate_port(port)?;
    Ok(vec![
        "tunnel".into(),
        "--no-autoupdate".into(),
        "--url".into(),
        format!("http://127.0.0.1:{port}"),
    ])
}

/// Extract `https://<name>.trycloudflare.com` from a cloudflared log line.
pub fn parse_tunnel_url(line: &str) -> Option<String> {
    let start = line.find("https://")?;
    let rest = &line[start..];
    let end = rest
        .find(|c: char| c.is_whitespace() || c == '|' || c == '"')
        .unwrap_or(rest.len());
    let url = rest[..end].trim_end_matches('/');
    let host = url.strip_prefix("https://")?;
    let label = host.strip_suffix(".trycloudflare.com")?;
    let valid = !label.is_empty()
        && label
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    valid.then(|| url.to_string())
}

/// cloudflared on PATH, or where the official MSI / `winget install Cloudflare.cloudflared` puts it.
pub fn find_cloudflared(path_var: Option<&str>, extra: &[PathBuf]) -> Option<PathBuf> {
    let exe = if cfg!(windows) {
        "cloudflared.exe"
    } else {
        "cloudflared"
    };
    let separator = if cfg!(windows) { ';' } else { ':' };
    let from_path = path_var
        .unwrap_or_default()
        .split(separator)
        .filter(|dir| !dir.trim().is_empty())
        .map(|dir| Path::new(dir.trim()).join(exe));
    from_path
        .chain(extra.iter().cloned())
        .find(|candidate| candidate.is_file())
}

pub fn cloudflared_candidates() -> Vec<PathBuf> {
    let mut list = Vec::new();
    for var in ["ProgramFiles(x86)", "ProgramFiles"] {
        if let Ok(dir) = std::env::var(var) {
            list.push(Path::new(&dir).join("cloudflared").join("cloudflared.exe"));
        }
    }
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        // winget portable installs link here
        list.push(
            Path::new(&local)
                .join("Microsoft")
                .join("WinGet")
                .join("Links")
                .join("cloudflared.exe"),
        );
    }
    list
}

pub fn cloudflared_command(exe: &Path, port: u16) -> Result<Command, String> {
    let mut cmd = Command::new(exe);
    cmd.args(cloudflared_args(port)?)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    Ok(cmd)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn port_range() {
        assert!(validate_port(80).is_err());
        assert!(validate_port(1023).is_err());
        assert_eq!(validate_port(8765), Ok(8765));
        assert_eq!(validate_port(65535), Ok(65535));
    }

    #[test]
    fn worker_http_args() {
        let args = http_args(8765, false, 42).unwrap();
        assert_eq!(
            args,
            [
                "mcp",
                "--http",
                "--port",
                "8765",
                "--events",
                "--parent-pid",
                "42"
            ]
        );
        assert!(http_args(9000, true, 1)
            .unwrap()
            .contains(&"--rotate-token".to_string()));
        assert!(http_args(22, false, 1).is_err());
    }

    #[test]
    fn tunnel_args_point_at_loopback() {
        assert_eq!(
            cloudflared_args(8765).unwrap(),
            [
                "tunnel",
                "--no-autoupdate",
                "--url",
                "http://127.0.0.1:8765"
            ]
        );
        assert!(cloudflared_args(1).is_err());
    }

    #[test]
    fn tunnel_url_from_cloudflared_banner() {
        let line = "2026-10-17T12:00:00Z INF |  https://brave-lion-42.trycloudflare.com  |";
        assert_eq!(
            parse_tunnel_url(line).as_deref(),
            Some("https://brave-lion-42.trycloudflare.com")
        );
        assert_eq!(
            parse_tunnel_url("url=https://a-b.trycloudflare.com/ ok").as_deref(),
            Some("https://a-b.trycloudflare.com")
        );
        // other https links in the logs are ignored
        assert_eq!(
            parse_tunnel_url("see https://developers.cloudflare.com/x"),
            None
        );
        assert_eq!(
            parse_tunnel_url("https://evil.com/.trycloudflare.com"),
            None
        );
        assert_eq!(parse_tunnel_url("https://A_B.trycloudflare.com"), None);
        assert_eq!(parse_tunnel_url("no url here"), None);
    }

    #[test]
    fn finds_cloudflared_on_path_or_candidates() {
        let dir = std::env::temp_dir().join(format!("lsnpu-cf-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let exe = dir.join(if cfg!(windows) {
            "cloudflared.exe"
        } else {
            "cloudflared"
        });
        std::fs::write(&exe, b"").unwrap();
        let path_var = format!(
            "/nonexistent{}{}",
            if cfg!(windows) { ';' } else { ':' },
            dir.display()
        );
        assert_eq!(find_cloudflared(Some(&path_var), &[]), Some(exe.clone()));
        assert_eq!(find_cloudflared(None, &[exe.clone()]), Some(exe.clone()));
        assert_eq!(find_cloudflared(Some("/nonexistent"), &[]), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
