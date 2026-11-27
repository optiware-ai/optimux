//! `optimux-tui usage ...` and `optimux-tui performance`: the numbers of Usage and
//! Performance, as tables or JSON.

use anyhow::{Result, bail};
use clap::{Subcommand, ValueEnum};
use serde_json::Value;

use super::{Client, confirm, num, print_json, table};
use crate::api::enc;
use crate::dash::{fmt, performance, usage};

#[derive(Clone, Copy, ValueEnum)]
pub enum Period {
    Today,
    Week,
    Month,
    All,
}

impl Period {
    fn key(self) -> &'static str {
        match self {
            Period::Today => "today",
            Period::Week => "last_7_days",
            Period::Month => "this_month",
            Period::All => "all_time",
        }
    }
}

#[derive(Clone, Copy, ValueEnum)]
pub enum By {
    Project,
    Role,
    Job,
    Model,
    Skill,
    SkillDrafts,
    MemoryUpdates,
}

impl By {
    fn key(self) -> &'static str {
        match self {
            By::Project => "by_project",
            By::Role => "by_role",
            By::Job => "by_job",
            By::Model => "by_model",
            By::Skill => "by_skill",
            By::SkillDrafts => "skill_drafts",
            By::MemoryUpdates => "memory_updates",
        }
    }
}

#[derive(Subcommand)]
pub enum UsageCmd {
    /// Cost, calls and tokens of a period, by project (or --by model, role...).
    Show {
        #[arg(long, value_enum, default_value = "month")]
        period: Period,
        #[arg(long, value_enum, default_value = "project")]
        by: By,
        /// Only this project's numbers.
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Every task record (one per request) as CSV: optimux-tui usage tasks-csv > tasks.csv
    TasksCsv,
    /// Delete every task record (chats and usage stay).
    DeleteTasks {
        #[arg(long)]
        yes: bool,
    },
}

pub async fn run(c: &Client, cmd: UsageCmd) -> Result<()> {
    match cmd {
        UsageCmd::Show {
            period,
            by,
            project,
            json,
        } => {
            let path = match &project {
                Some(p) => format!("/api/usage?project={}", enc(p)),
                None => "/api/usage".to_string(),
            };
            let u = c.get(&path).await?;
            if json {
                return print_json(u.get(period.key()).unwrap_or(&Value::Null));
            }
            let deleted = c.get("/api/projects/deleted").await?;
            if let Some(t) = u.get(period.key()).and_then(|p| p.get("total")) {
                let n = |k: &str| num(t, k).unwrap_or(0.0);
                println!(
                    "Cost {}{}   Calls {:.0}   Input {} (cached {})   Output {}\n",
                    fmt::cost(n("cost")),
                    if n("calls_without_cost") > 0.0 { "+" } else { "" },
                    n("calls"),
                    fmt::tokens(n("prompt_tokens")),
                    fmt::tokens(n("cached_tokens")),
                    fmt::tokens(n("completion_tokens"))
                );
            }
            let rows: Vec<Vec<String>> = usage::group_rows(&u, period.key(), by.key(), &deleted)
                .into_iter()
                .map(|(name, b, _)| [vec![name], usage::cells(&b)].concat())
                .collect();
            if rows.is_empty() {
                println!("Nothing in this period.");
            } else {
                println!(
                    "{}",
                    table(
                        &[
                            "NAME", "CALLS", "INPUT", "CACHED", "OUTPUT", "THINKING", "COST", "AVG TIME"
                        ],
                        &rows
                    )
                );
            }
            if rows.iter().any(|r| r[6].ends_with('+')) {
                println!("\n+: some calls have no known price (set prices in Models).");
            }
        }
        UsageCmd::TasksCsv => print!("{}", c.text("/api/tasks.csv").await?),
        UsageCmd::DeleteTasks { yes } => {
            let n = c
                .get("/api/tasks")
                .await?
                .get("count")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            if n == 0 {
                println!("There are no task records.");
                return Ok(());
            }
            confirm(&format!("Delete all {n} task records? Chats and usage stay."), yes)?;
            let v = c.delete("/api/tasks").await?;
            println!(
                "{} task records deleted.",
                v.get("deleted").and_then(Value::as_u64).unwrap_or(0)
            );
        }
    }
    Ok(())
}

/// `optimux-tui performance`: every model's cost, speed and success.
pub async fn performance(c: &Client, days: u32, project: Option<String>, json: bool) -> Result<()> {
    if !matches!(days, 0 | 7 | 30 | 90) {
        bail!("--days: 7, 30, 90, or 0 for all time.");
    }
    let mut path = format!("/api/analytics?days={days}");
    if let Some(p) = &project {
        path.push_str(&format!("&project={}", enc(p)));
    }
    let v = c.get(&path).await?;
    if json {
        return print_json(&v);
    }
    let rows: Vec<Vec<String>> = v
        .get("models")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(performance::cells)
        .collect();
    if rows.is_empty() {
        println!("No calls in this period.");
        return Ok(());
    }
    println!(
        "{}",
        table(
            &[
                "MODEL",
                "CALLS",
                "SHARE",
                "COST",
                "$/CALL",
                "MEDIAN",
                "SPEED",
                "WORKS",
                "ESCAL.",
                "THUMBS UP"
            ],
            &rows
        )
    );
    Ok(())
}
