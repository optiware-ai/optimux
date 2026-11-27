//! `optimux-tui guide ...` and `optimux-tui start ...`: the Guide and Getting started
//! (Dashboard → Help).

use std::io::IsTerminal;

use anyhow::{Result, bail};
use clap::{Subcommand, ValueEnum};
use serde_json::{Value, json};

use super::{Client, print_json, s, table};
use crate::dash::guide::{find, parse, pick, whole};
use crate::dash::start::{PLAN, SKILLS, mark, target};

#[derive(Subcommand)]
pub enum GuideCmd {
    /// The headings (the default).
    Contents,
    /// The sections holding every word, with where they are.
    Find {
        #[arg(required = true)]
        words: Vec<String>,
    },
    /// One section (a chapter with its sections), as Markdown: its title or words of it.
    Read {
        #[arg(required = true)]
        title: Vec<String>,
    },
    /// The whole guide, as Markdown (pipe it into less).
    All,
}

#[derive(Clone, Copy, ValueEnum)]
pub enum OnOff {
    On,
    Off,
}

#[derive(Subcommand)]
pub enum StartCmd {
    /// The steps and what was found (the default).
    List {
        #[arg(long)]
        json: bool,
    },
    /// Choose your features: memory updates, plan mode, skills (any of them).
    Features {
        #[arg(long, value_enum)]
        memory: Option<OnOff>,
        /// off, ask or auto.
        #[arg(long)]
        plan: Option<String>,
        /// off, manual or auto.
        #[arg(long)]
        skills: Option<String>,
    },
    /// The features are fine as they are (ticks the step).
    Fine,
    /// Show the checklist on the web page's chat again.
    Show,
    /// Hide it from the web page's chat.
    Hide,
}

pub async fn guide(c: &Client, cmd: Option<GuideCmd>) -> Result<()> {
    let v = c.get("/api/guide").await?;
    let md = s(&v, "content");
    let (_, secs) = parse(&md);
    match cmd.unwrap_or(GuideCmd::Contents) {
        GuideCmd::Contents => {
            for sec in &secs {
                if sec.level == 2 {
                    println!("{}", sec.title);
                } else {
                    println!("    {}", sec.title);
                }
            }
            if std::io::stdout().is_terminal() {
                eprintln!("\nRead one: optimux-tui guide read <title> · find words: optimux-tui guide find <words>");
            }
        }
        GuideCmd::Find { words } => {
            let found = find(&secs, &words.join(" "));
            if found.is_empty() {
                bail!("Nothing found. Try another word.");
            }
            for (i, snip) in found {
                println!("{}\n    {snip}", secs[i].path);
            }
        }
        GuideCmd::Read { title } => match pick(&secs, &title.join(" ")) {
            Some(i) => print!("{}", whole(&secs, i)),
            None => bail!("No section like that: optimux-tui guide (the headings) or guide find <words>."),
        },
        GuideCmd::All => print!("{md}"),
    }
    Ok(())
}

pub async fn start(c: &Client, cmd: Option<StartCmd>) -> Result<()> {
    let act = |a: &'static str| async move { c.post("/api/onboarding", json!({"action": a})).await };
    match cmd.unwrap_or(StartCmd::List { json: false }) {
        StartCmd::List { json } => {
            let v = c.get("/api/onboarding").await?;
            if json {
                return print_json(&v);
            }
            print_steps(&v);
        }
        StartCmd::Features { memory, plan, skills } => {
            let check = |v: &Option<String>, list: &[(&str, &str)], what: &str| -> Result<()> {
                if let Some(x) = v {
                    if !list.iter().any(|(id, _)| id == x) {
                        bail!(
                            "{what}: one of {}.",
                            list.iter().map(|(id, _)| *id).collect::<Vec<_>>().join(", ")
                        );
                    }
                }
                Ok(())
            };
            check(&plan, PLAN, "--plan")?;
            check(&skills, SKILLS, "--skills")?;
            let mut changes = serde_json::Map::new();
            if let Some(m) = memory {
                changes.insert("memory_curator".into(), json!(matches!(m, OnOff::On)));
            }
            if let Some(p) = plan {
                changes.insert("plan_mode".into(), json!(p));
            }
            if let Some(sk) = skills {
                changes.insert("skills_mode".into(), json!(sk));
            }
            if changes.is_empty() {
                let st = c.get("/api/settings").await?;
                let label = |list: &[(&str, &str)], v: String, d: &str| {
                    let v = if v.is_empty() { d.to_string() } else { v };
                    list.iter()
                        .find(|(id, _)| *id == v)
                        .map(|(_, l)| l.to_string())
                        .unwrap_or(v)
                };
                println!(
                    "Update memory after each chat: {}",
                    if st.get("memory_curator").and_then(Value::as_bool) == Some(false) {
                        "off"
                    } else {
                        "on"
                    }
                );
                println!("Plan big tasks: {}", label(PLAN, s(&st, "plan_mode"), "off"));
                println!("Skills: {}", label(SKILLS, s(&st, "skills_mode"), "auto"));
                eprintln!("Change them with --memory on|off, --plan off|ask|auto, --skills off|manual|auto.");
                return Ok(());
            }
            c.save_settings(Value::Object(changes)).await?;
            println!("Saved: your features.");
        }
        StartCmd::Fine => {
            act("features_ok").await?;
            println!("The features are fine as they are.");
        }
        StartCmd::Show => {
            act("reopen").await?;
            println!("The checklist shows on the web page's chat again.");
        }
        StartCmd::Hide => {
            act("dismiss").await?;
            println!("The checklist no longer shows on the web page's chat.");
        }
    }
    Ok(())
}

fn print_steps(v: &Value) {
    let steps = v.get("steps").and_then(Value::as_array).cloned().unwrap_or_default();
    let rows: Vec<Vec<String>> = steps
        .iter()
        .enumerate()
        .map(|(i, st)| {
            let optional = st.get("optional").and_then(Value::as_bool) == Some(true);
            vec![
                mark(st, i),
                format!("{}{}", s(st, "title"), if optional { " (optional)" } else { "" }),
                s(st, "check"),
            ]
        })
        .collect();
    println!("{}", table(&["", "Step", "Found"], &rows));
    if v.get("ready").and_then(Value::as_bool) == Some(true) {
        println!("✓ All set.");
        return;
    }
    println!(
        "{}/{} done.",
        v.get("done").and_then(Value::as_u64).unwrap_or(0),
        v.get("total").and_then(Value::as_u64).unwrap_or(0)
    );
    if let Some(next) = steps.iter().find(|st| s(st, "id") == s(v, "next")) {
        println!("\nNext: {}", s(next, "title"));
        println!(
            "  {}",
            if s(next, "info").is_empty() {
                s(next, "why")
            } else {
                s(next, "info")
            }
        );
        if let Some((sec, cmd)) = target(&s(next, "target")) {
            let place = if sec == "Chat" {
                "the chat window".to_string()
            } else {
                format!("Dashboard → {sec}")
            };
            println!("  {place} · {cmd}");
        }
    }
}
