//! Long texts (memory files, skills) are edited in the user's own editor: $VISUAL or
//! $EDITOR, else nano, vim or vi. The text goes to a private temporary file (0600, in
//! a 0700 folder), the editor opens it, and the file is read back and removed.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result, bail};

/// A text to edit, asked for by a Dashboard section.
#[derive(Debug, Clone)]
pub struct Edit {
    pub text: String,
    /// The file's ending, so the editor colours it: ".md".
    pub suffix: String,
}

/// The editor command: $VISUAL, $EDITOR, or the first of nano, vim, vi found.
pub fn editor() -> Option<String> {
    for var in ["VISUAL", "EDITOR"] {
        if let Ok(v) = std::env::var(var) {
            if !v.trim().is_empty() {
                return Some(v);
            }
        }
    }
    ["nano", "vim", "vi"].iter().find(|e| on_path(e)).map(|e| e.to_string())
}

fn on_path(name: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|p| std::env::split_paths(&p).any(|d| d.join(name).is_file()))
}

/// Edits `text` in the editor; Some(new text) when it changed, None when not. Runs in
/// the current terminal (the caller has left the full-screen mode).
pub fn edit(text: &str, suffix: &str) -> Result<Option<String>> {
    let Some(editor) = editor() else {
        bail!("No editor found: set $EDITOR (for example export EDITOR=nano).");
    };
    let dir = private_dir()?;
    let path = dir.join(format!("edit{suffix}"));
    let result = (|| {
        write_private(&path, text)?;
        // through the shell, so $EDITOR may carry options ("code --wait", "vim -u NONE")
        let status = Command::new("sh")
            .arg("-c")
            .arg(format!("{editor} \"$1\""))
            .arg("optimux-edit")
            .arg(&path)
            .status()
            .with_context(|| format!("Couldn't start the editor ({editor})"))?;
        if !status.success() {
            bail!("The editor ({editor}) ended with an error: nothing was changed.");
        }
        let new = std::fs::read_to_string(&path).context("Couldn't read the edited text back")?;
        Ok(if new == text { None } else { Some(new) })
    })();
    let _ = std::fs::remove_dir_all(&dir);
    result
}

fn private_dir() -> Result<PathBuf> {
    let base = std::env::temp_dir();
    for i in 0..100u32 {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0);
        let dir = base.join(format!("optimux-tui-{}-{nanos}-{i}", std::process::id()));
        let mut b = std::fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            b.mode(0o700);
        }
        match b.create(&dir) {
            Ok(()) => return Ok(dir),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e).context("Couldn't make a temporary folder"),
        }
    }
    bail!("Couldn't make a temporary folder")
}

fn write_private(path: &Path, text: &str) -> Result<()> {
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(path).context("Couldn't write the temporary file")?;
    f.write_all(text.as_bytes())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // One test: it sets $VISUAL, which other tests must not see half-way.
    #[test]
    fn an_editor_changes_the_text_and_the_file_goes_away() {
        // "editor" scripts: one appends a line, one changes nothing, one fails
        unsafe { std::env::set_var("VISUAL", "sh -c 'echo added >> \"$0\"'") };
        let out = edit("hello\n", ".md").unwrap();
        assert_eq!(out.as_deref(), Some("hello\nadded\n"));
        unsafe { std::env::set_var("VISUAL", "true") };
        assert_eq!(edit("same\n", ".md").unwrap(), None);
        unsafe { std::env::set_var("VISUAL", "false") };
        assert!(edit("x", ".md").is_err());
        unsafe { std::env::remove_var("VISUAL") };
        let left: Vec<_> = std::fs::read_dir(std::env::temp_dir())
            .unwrap()
            .flatten()
            .filter(|e| {
                e.file_name()
                    .to_string_lossy()
                    .starts_with(&format!("optimux-tui-{}-", std::process::id()))
            })
            .collect();
        assert!(left.is_empty(), "temporary folders left: {left:?}");
    }
}
