//! Where the terminal client keeps its sign-ins: one small JSON file, readable only by
//! you (folder 0700, file 0600). It lives outside ~/.optimux on purpose: that folder is
//! the server's data, and its backups must never carry a session.
//!
//! Folder: $OPTIMUX_TUI_HOME, else $XDG_CONFIG_HOME/optimux-tui, else ~/.config/optimux-tui.

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{ErrorKind, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

const FILE: &str = "sessions.json";

#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct Store {
    /// The server used when none is given.
    #[serde(default)]
    pub default: Option<String>,
    /// One sign-in per server address (`Server::base`).
    #[serde(default)]
    pub servers: BTreeMap<String, Saved>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Saved {
    /// The value of the `optimux_session` cookie.
    pub cookie: String,
    /// Unix time of the sign-in.
    pub signed_in_at: u64,
}

pub fn folder() -> Result<PathBuf> {
    let var = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    if let Some(dir) = var("OPTIMUX_TUI_HOME") {
        return Ok(PathBuf::from(dir));
    }
    if let Some(dir) = var("XDG_CONFIG_HOME").filter(|d| Path::new(d).is_absolute()) {
        return Ok(PathBuf::from(dir).join("optimux-tui"));
    }
    let home = var("HOME").context("HOME is not set, so there is nowhere to keep the sign-in.")?;
    Ok(PathBuf::from(home).join(".config").join("optimux-tui"))
}

impl Store {
    pub fn load(dir: &Path) -> Result<Store> {
        let path = dir.join(FILE);
        let text = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(e) if e.kind() == ErrorKind::NotFound => return Ok(Store::default()),
            Err(e) => return Err(e).with_context(|| format!("Can't read {}", path.display())),
        };
        tighten(&path)?;
        serde_json::from_str(&text)
            .with_context(|| format!("{} is damaged: delete it and sign in again.", path.display()))
    }

    /// Written to a new file first and then renamed, so a crash never leaves half a file.
    pub fn save(&self, dir: &Path) -> Result<()> {
        fs::create_dir_all(dir).with_context(|| format!("Can't create {}", dir.display()))?;
        fs::set_permissions(dir, fs::Permissions::from_mode(0o700))?;
        let tmp = dir.join(format!("{FILE}.tmp"));
        let _ = fs::remove_file(&tmp); // a left-over from a crash may have other permissions
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&tmp)
            .with_context(|| format!("Can't write {}", tmp.display()))?;
        file.write_all(serde_json::to_string_pretty(self)?.as_bytes())?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(&tmp, dir.join(FILE))?;
        Ok(())
    }
}

/// A sessions file others can read is fixed at once, and you are told.
fn tighten(path: &Path) -> Result<()> {
    let mode = fs::metadata(path)?.permissions().mode();
    if mode & 0o077 != 0 {
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        eprintln!(
            "Warning: {} could be read by other users ({:o}); it is now private (600). \
             If others had access, sign out and in again.",
            path.display(),
            mode & 0o777
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Store {
        let mut s = Store {
            default: Some("http://127.0.0.1:8000".into()),
            ..Store::default()
        };
        s.servers.insert(
            "http://127.0.0.1:8000".into(),
            Saved {
                cookie: "abc".into(),
                signed_in_at: 1,
            },
        );
        s
    }

    #[test]
    fn a_missing_file_is_an_empty_store() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(Store::load(dir.path()).unwrap(), Store::default());
    }

    #[test]
    fn saved_and_read_back_private() {
        let dir = tempfile::tempdir().unwrap();
        let sub = dir.path().join("optimux-tui");
        sample().save(&sub).unwrap();
        assert_eq!(Store::load(&sub).unwrap(), sample());
        let mode = |p: &Path| fs::metadata(p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode(&sub.join(FILE)), 0o600);
        assert_eq!(mode(&sub), 0o700);
        assert!(!sub.join(format!("{FILE}.tmp")).exists());
    }

    #[test]
    fn a_file_others_can_read_is_made_private() {
        let dir = tempfile::tempdir().unwrap();
        sample().save(dir.path()).unwrap();
        let path = dir.path().join(FILE);
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        Store::load(dir.path()).unwrap();
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn a_damaged_file_says_what_to_do() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join(FILE), "{ not json").unwrap();
        let err = Store::load(dir.path()).unwrap_err().to_string();
        assert!(err.contains("sign in again"), "{err}");
    }
}
