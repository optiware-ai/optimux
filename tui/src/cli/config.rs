//! `optimux-tui execution ...`, `optimux-tui memory ...` and `optimux-tui skills ...`.

use std::io::{IsTerminal, Read};

use anyhow::{Result, bail};
use clap::{Subcommand, ValueEnum};
use serde_json::{Map, Value, json};

use super::models::OnOff;
use super::{Client, confirm, num, print_json, s, table};
use crate::api::enc;
use crate::dash::execution::{LIMITS, split_list, split_paths};
use crate::dash::fmt;
use crate::dash::memory::{MAX_CHARS, path_of, save_body};
use crate::dash::project::LEVELS;
use crate::dash::projects::ago;
use crate::dash::skills::{MODES, name_ok};
use crate::ui::external;

#[derive(Subcommand)]
pub enum ExecutionCmd {
    /// Autonomy, sandbox, command environment, limits, steps per turn.
    Show {
        #[arg(long)]
        json: bool,
    },
    /// Change some of them; only what you give changes.
    Set {
        /// supervised, checkpointed or autonomous (the default for every project).
        #[arg(long)]
        autonomy: Option<String>,
        #[arg(long)]
        sandbox: Option<OnOff>,
        #[arg(long)]
        network: Option<OnOff>,
        /// Extra read-only folders, separated by commas ("" for none).
        #[arg(long)]
        ro_paths: Option<String>,
        /// Extra variable names for commands, separated by commas or spaces ("" for none).
        #[arg(long)]
        env: Option<String>,
        #[arg(long)]
        memory_mb: Option<i64>,
        #[arg(long)]
        file_mb: Option<i64>,
        #[arg(long)]
        processes: Option<i64>,
        /// Seconds, 10 to 1800.
        #[arg(long)]
        timeout: Option<i64>,
        #[arg(long)]
        steps_simple: Option<i64>,
        #[arg(long)]
        steps_complex: Option<i64>,
    },
}

#[derive(Clone, Copy, ValueEnum)]
pub enum File {
    Personal,
    Professional,
    Project,
}

impl File {
    fn tab(self) -> &'static str {
        match self {
            File::Personal => "personal",
            File::Professional => "professional",
            File::Project => "project",
        }
    }
}

#[derive(Subcommand)]
pub enum MemoryCmd {
    /// Print a memory file (project: the active project's, or --project).
    Show {
        file: File,
        #[arg(long)]
        project: Option<String>,
    },
    /// Edit a memory file in your editor ($EDITOR), then save it.
    Edit {
        file: File,
        #[arg(long)]
        project: Option<String>,
    },
    /// Replace a memory file with standard input: optimux-tui memory set personal < me.md
    Set {
        file: File,
        #[arg(long)]
        project: Option<String>,
    },
    /// The project memory's history: every change, newest first.
    History {
        #[arg(long)]
        project: Option<String>,
        /// Also the changes themselves.
        #[arg(long)]
        diff: bool,
    },
    /// Undo one change of the project memory (its number from history).
    Undo {
        id: i64,
        #[arg(long)]
        yes: bool,
    },
    /// Update the project memory now from its latest chat (one paid model call).
    Update {
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        yes: bool,
    },
    /// Automatic project memory updates: on or off, and after how many idle minutes.
    Auto {
        state: Option<OnOff>,
        #[arg(long)]
        idle_minutes: Option<i64>,
    },
}

#[derive(Subcommand)]
pub enum SkillsCmd {
    /// Every skill, and when skills run.
    List {
        #[arg(long)]
        json: bool,
    },
    /// Print a skill's SKILL.md.
    Show {
        name: String,
        /// global or project (default: the one in use).
        #[arg(long)]
        scope: Option<String>,
    },
    /// Write a new skill in your editor (from the template), or from standard input.
    New {
        name: String,
        /// global or project (default: project when a project is active).
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        stdin: bool,
    },
    /// Edit a skill in your editor, or replace it with standard input (--stdin).
    Edit {
        name: String,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        stdin: bool,
    },
    /// manual (runs only on /name) or auto (listed in every request).
    Mode {
        name: String,
        mode: String,
        #[arg(long)]
        scope: Option<String>,
    },
    /// Move a skill to ~/.optimux/legacy/skills/ (not deleted).
    Remove {
        name: String,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        yes: bool,
    },
    /// When skills run: off, manual or auto.
    When { mode: String },
}

fn on(v: Option<OnOff>) -> Option<bool> {
    v.map(|o| matches!(o, OnOff::On))
}

pub async fn run_execution(c: &Client, cmd: ExecutionCmd) -> Result<()> {
    match cmd {
        ExecutionCmd::Show { json } => {
            let st = c.get("/api/settings").await?;
            let sb = c.get("/api/sandbox").await?;
            let au = c.get("/api/autonomy").await?;
            if json {
                let keys = ["sandbox", "sandbox_network", "sandbox_ro_paths", "command_env"];
                let mut out: Map<String, Value> = keys
                    .iter()
                    .map(|k| (k.to_string(), st.get(*k).cloned().unwrap_or(Value::Null)))
                    .collect();
                for (k, ..) in LIMITS {
                    out.insert(k.to_string(), st.get(*k).cloned().unwrap_or(Value::Null));
                }
                out.insert("autonomy".into(), au);
                out.insert("sandbox_check".into(), sb);
                return print_json(&Value::Object(out));
            }
            println!("{:<26}{}", "Default autonomy:", s(&au, "default"));
            if let Some(o) = au.get("projects").and_then(Value::as_object).filter(|o| !o.is_empty()) {
                let l: Vec<String> = o
                    .iter()
                    .map(|(p, v)| format!("{p}: {}", v.as_str().unwrap_or("")))
                    .collect();
                println!("{:<26}{}", "Projects with their own:", l.join(", "));
            }
            let on_off = |k: &str| {
                if st.get(k).and_then(Value::as_bool) == Some(true) {
                    "on"
                } else {
                    "off"
                }
            };
            let works = if sb.get("works").and_then(Value::as_bool) == Some(true) {
                "works here".into()
            } else {
                s(&sb, "error")
            };
            println!(
                "{:<26}{} · {works}",
                "Sandbox:",
                if sb.get("required").and_then(Value::as_bool) == Some(true) {
                    "on (forced)"
                } else {
                    on_off("sandbox")
                }
            );
            println!("{:<26}{}", "Network in the sandbox:", on_off("sandbox_network"));
            let list = |k: &str| {
                st.get(k)
                    .and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", "))
                    .unwrap_or_default()
            };
            println!("{:<26}{}", "Extra read-only folders:", list("sandbox_ro_paths"));
            println!("{:<26}{}", "Extra variables:", list("command_env"));
            for (k, label, ..) in LIMITS {
                println!(
                    "{:<26}{}",
                    format!("{label}:"),
                    st.get(*k).map(|v| v.to_string()).unwrap_or_default()
                );
            }
        }
        ExecutionCmd::Set {
            autonomy,
            sandbox,
            network,
            ro_paths,
            env,
            memory_mb,
            file_mb,
            processes,
            timeout,
            steps_simple,
            steps_complex,
        } => {
            let mut ch = Map::new();
            for (key, v) in [
                ("limit_memory_mb", memory_mb),
                ("limit_file_mb", file_mb),
                ("limit_processes", processes),
                ("command_timeout", timeout),
                ("max_steps_simple", steps_simple),
                ("max_steps_complex", steps_complex),
            ] {
                if let Some(n) = v {
                    let (_, label, min, max) = LIMITS.iter().find(|(k, ..)| *k == key).copied().expect("a limit");
                    if !(min..=max).contains(&n) {
                        bail!("{label}: {min} to {max}.");
                    }
                    ch.insert(key.into(), json!(n));
                }
            }
            if let Some(b) = on(sandbox) {
                ch.insert("sandbox".into(), json!(b));
            }
            if let Some(b) = on(network) {
                ch.insert("sandbox_network".into(), json!(b));
            }
            if let Some(p) = ro_paths {
                ch.insert("sandbox_ro_paths".into(), json!(split_paths(&p)));
            }
            if let Some(e) = env {
                ch.insert("command_env".into(), json!(split_list(&e)));
            }
            if ch.is_empty() && autonomy.is_none() {
                bail!("Nothing to change: see optimux-tui execution set --help.");
            }
            if !ch.is_empty() {
                c.save_settings(Value::Object(ch)).await?;
            }
            if let Some(level) = autonomy {
                if !LEVELS.iter().any(|(id, _)| *id == level) {
                    bail!("--autonomy: supervised, checkpointed or autonomous.");
                }
                c.post("/api/autonomy", json!({"project": null, "level": level}))
                    .await?;
            }
            println!("Saved. New commands use it:");
            Box::pin(run_execution(c, ExecutionCmd::Show { json: false })).await?;
        }
    }
    Ok(())
}

/// The project a memory command is about: --project, else the active one.
async fn project_of(c: &Client, given: Option<String>) -> Result<String> {
    if let Some(p) = given {
        return Ok(p);
    }
    let active = s(&c.get("/api/settings").await?, "active_project");
    if active.is_empty() {
        bail!("No project is active: give --project <name>.");
    }
    Ok(active)
}

async fn memory_target(c: &Client, file: File, project: Option<String>) -> Result<(String, String)> {
    let p = if matches!(file, File::Project) {
        project_of(c, project).await?
    } else {
        String::new()
    };
    Ok((file.tab().to_string(), p))
}

async fn save_memory(c: &Client, tab: &str, p: &str, text: &str) -> Result<()> {
    let n = text.chars().count();
    if n > MAX_CHARS {
        bail!("Too long: {n} characters, the limit is {MAX_CHARS}. Nothing was saved.");
    }
    let path = if tab == "project" {
        path_of(tab, p)
    } else {
        "/api/memory".into()
    };
    c.post(&path, save_body(tab, text)).await?;
    println!("Saved ({n} characters, ≈{} tokens on every request).", n.div_ceil(4));
    Ok(())
}

fn read_stdin() -> Result<String> {
    if std::io::stdin().is_terminal() {
        bail!("Pipe the text in: … < file.md");
    }
    let mut t = String::new();
    std::io::stdin().read_to_string(&mut t)?;
    Ok(t)
}

pub async fn run_memory(c: &Client, cmd: MemoryCmd) -> Result<()> {
    match cmd {
        MemoryCmd::Show { file, project } => {
            let (tab, p) = memory_target(c, file, project).await?;
            print!("{}", s(&c.get(&path_of(&tab, &p)).await?, "content"));
        }
        MemoryCmd::Edit { file, project } => {
            let (tab, p) = memory_target(c, file, project).await?;
            let text = s(&c.get(&path_of(&tab, &p)).await?, "content");
            match tokio::task::spawn_blocking(move || external::edit(&text, ".md")).await?? {
                Some(new) => save_memory(c, &tab, &p, &new).await?,
                None => println!("No change."),
            }
        }
        MemoryCmd::Set { file, project } => {
            let (tab, p) = memory_target(c, file, project).await?;
            save_memory(c, &tab, &p, &read_stdin()?).await?;
        }
        MemoryCmd::History { project, diff } => {
            let p = project_of(c, project).await?;
            let v = c.get(&format!("/api/memory/project/{}/versions", enc(&p))).await?;
            let list = v.as_array().cloned().unwrap_or_default();
            if list.is_empty() {
                println!("No changes recorded yet.");
            }
            for ver in list {
                let who = match s(&ver, "source").as_str() {
                    "curator" => "auto",
                    "user" => "you",
                    o => o,
                }
                .to_string();
                println!(
                    "#{:<5} {:<6} {}  {}{}",
                    s(&ver, "id"),
                    who,
                    num(&ver, "ts").map(ago).unwrap_or_default(),
                    s(&ver, "summary"),
                    num(&ver, "cost")
                        .map(|c| format!(" ({})", fmt::cost(c)))
                        .unwrap_or_default()
                );
                if diff {
                    for l in s(&ver, "diff").lines() {
                        println!("        {l}");
                    }
                }
            }
        }
        MemoryCmd::Undo { id, yes } => {
            confirm(
                &format!("Undo change #{id}? The memory goes back to before it (the undo is recorded too)."),
                yes,
            )?;
            c.post(&format!("/api/memory/versions/{id}/undo"), json!({})).await?;
            println!("Undone.");
        }
        MemoryCmd::Update { project, yes } => {
            let p = project_of(c, project).await?;
            let est = c.get("/api/costs/estimates").await?;
            let cost = est
                .get("memory_update")
                .filter(|v| !v.is_null())
                .map(|e| format!(" About {} per update.", fmt::cost(num(e, "avg").unwrap_or(0.0))))
                .unwrap_or_default();
            confirm(
                &format!("Update the memory of {p} from its latest chat? One paid model call.{cost}"),
                yes,
            )?;
            let v = c
                .post(&format!("/api/memory/project/{}/curate", enc(&p)), json!({}))
                .await?;
            if !s(&v, "chat_title").is_empty() {
                print!("Read “{}”. ", s(&v, "chat_title"));
            }
            if v.get("changed").and_then(Value::as_bool) == Some(true) {
                println!("Updated: {}", s(&v, "summary"));
            } else {
                println!("No update: {}", s(&v, "reason"));
            }
        }
        MemoryCmd::Auto { state, idle_minutes } => {
            let mut ch = Map::new();
            if let Some(b) = on(state) {
                ch.insert("memory_curator".into(), json!(b));
            }
            if let Some(n) = idle_minutes {
                if !(1..=240).contains(&n) {
                    bail!("--idle-minutes: 1 to 240.");
                }
                ch.insert("curator_idle_minutes".into(), json!(n));
            }
            let st = if ch.is_empty() {
                c.get("/api/settings").await?
            } else {
                c.save_settings(Value::Object(ch)).await?
            };
            println!(
                "Automatic project memory updates: {}, after {} idle minutes.",
                if st.get("memory_curator").and_then(Value::as_bool) == Some(true) {
                    "on"
                } else {
                    "off"
                },
                st.get("curator_idle_minutes")
                    .map(|v| v.to_string())
                    .unwrap_or_default()
            );
        }
    }
    Ok(())
}

/// The skill of this name: in `scope`, or the one in use (project before global).
async fn find_skill(c: &Client, name: &str, scope: Option<&str>) -> Result<Value> {
    let v = c.get("/api/skills").await?;
    let all: Vec<Value> = v.get("skills").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut found: Vec<&Value> = all
        .iter()
        .filter(|sk| s(sk, "name") == name && scope.is_none_or(|sc| s(sk, "scope") == sc))
        .collect();
    found.sort_by_key(|sk| {
        if sk.get("active").and_then(Value::as_bool) == Some(true) {
            0
        } else {
            1
        }
    });
    match found.first() {
        Some(sk) => Ok((*sk).clone()),
        None => bail!(
            "No skill /{name}{}. See: optimux-tui skills list",
            scope.map(|s| format!(" ({s})")).unwrap_or_default()
        ),
    }
}

async fn save_skill(c: &Client, scope: &str, name: &str, text: &str) -> Result<()> {
    c.post(&format!("/api/skills/{scope}/{}", enc(name)), json!({"content": text}))
        .await?;
    println!("/{name} saved ({scope}).");
    Ok(())
}

pub async fn run_skills(c: &Client, cmd: SkillsCmd) -> Result<()> {
    match cmd {
        SkillsCmd::List { json } => {
            let v = c.get("/api/skills").await?;
            if json {
                return print_json(&v);
            }
            let mode = s(&v, "skills_mode");
            let label = MODES
                .iter()
                .find(|(id, ..)| *id == mode)
                .map(|(_, l, _)| *l)
                .unwrap_or("");
            println!("When skills run: {label}.\n");
            let rows: Vec<Vec<String>> = v
                .get("skills")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|sk| {
                    vec![
                        format!("/{}", s(sk, "name")),
                        s(sk, "scope"),
                        s(sk, "mode"),
                        format!(
                            "{:.0} calls",
                            sk.pointer("/all_time/calls").and_then(Value::as_f64).unwrap_or(0.0)
                        ),
                        s(sk, "description"),
                    ]
                })
                .collect();
            if rows.is_empty() {
                println!("No skills yet. Make one: optimux-tui skills new <name>");
            } else {
                println!("{}", table(&["SKILL", "SCOPE", "MODE", "USED", "DESCRIPTION"], &rows));
            }
        }
        SkillsCmd::Show { name, scope } => {
            let sk = find_skill(c, &name, scope.as_deref()).await?;
            print!(
                "{}",
                s(
                    &c.get(&format!("/api/skills/{}/{}", s(&sk, "scope"), enc(&name)))
                        .await?,
                    "content"
                )
            );
        }
        SkillsCmd::New { name, scope, stdin } => {
            if !name_ok(&name) {
                bail!("Use lowercase letters, digits and dashes (e.g. release-notes).");
            }
            let scope = match scope {
                Some(sc) if sc == "global" || sc == "project" => sc,
                Some(_) => bail!("--scope: global or project."),
                None if s(&c.get("/api/settings").await?, "active_project").is_empty() => "global".into(),
                None => "project".into(),
            };
            if find_skill(c, &name, Some(&scope)).await.is_ok() {
                bail!("A {scope} skill /{name} exists already: optimux-tui skills edit {name}");
            }
            let text = if stdin {
                read_stdin()?
            } else {
                let t = s(
                    &c.get(&format!("/api/skills/template?name={}", enc(&name))).await?,
                    "content",
                );
                let t2 = t.clone();
                tokio::task::spawn_blocking(move || external::edit(&t2, ".md"))
                    .await??
                    .unwrap_or(t)
            };
            save_skill(c, &scope, &name, &text).await?;
        }
        SkillsCmd::Edit { name, scope, stdin } => {
            let sk = find_skill(c, &name, scope.as_deref()).await?;
            let scope = s(&sk, "scope");
            let text = if stdin {
                read_stdin()?
            } else {
                let t = s(&c.get(&format!("/api/skills/{scope}/{}", enc(&name))).await?, "content");
                match tokio::task::spawn_blocking(move || external::edit(&t, ".md")).await?? {
                    Some(new) => new,
                    None => {
                        println!("No change.");
                        return Ok(());
                    }
                }
            };
            save_skill(c, &scope, &name, &text).await?;
        }
        SkillsCmd::Mode { name, mode, scope } => {
            if !matches!(mode.as_str(), "manual" | "auto") {
                bail!("The mode: manual or auto.");
            }
            let sk = find_skill(c, &name, scope.as_deref()).await?;
            c.post(
                &format!("/api/skills/{}/{}/mode", s(&sk, "scope"), enc(&name)),
                json!({"mode": mode}),
            )
            .await?;
            println!("/{name} is {mode}.");
        }
        SkillsCmd::Remove { name, scope, yes } => {
            let sk = find_skill(c, &name, scope.as_deref()).await?;
            confirm(
                &format!(
                    "Remove /{name} ({})? Its folder is moved to ~/.optimux/legacy/skills/, not deleted.",
                    s(&sk, "scope")
                ),
                yes,
            )?;
            let v = c
                .post(
                    &format!("/api/skills/{}/{}/remove", s(&sk, "scope"), enc(&name)),
                    json!({}),
                )
                .await?;
            println!("/{name} moved to {}.", s(&v, "moved_to"));
        }
        SkillsCmd::When { mode } => {
            if !MODES.iter().any(|(id, ..)| *id == mode) {
                bail!("off, manual or auto.");
            }
            c.save_settings(json!({"skills_mode": mode})).await?;
            println!("When skills run: {mode}.");
        }
    }
    Ok(())
}
