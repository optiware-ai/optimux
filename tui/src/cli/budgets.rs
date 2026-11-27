//! `optimux-tui budgets ...`: monthly budgets per project, and the cost warnings.

use anyhow::{Result, bail};
use clap::Subcommand;
use serde_json::{Value, json};

use super::models::OnOff;
use super::{Client, num, print_json, s, table};
use crate::dash::budgets::{WARNINGS, state_text};
use crate::dash::fmt;

#[derive(Subcommand)]
pub enum BudgetsCmd {
    /// Every project: spent this month, its budget and its state.
    List {
        #[arg(long)]
        json: bool,
    },
    /// Set a project's monthly budget in USD ("none" removes it).
    Set {
        project: String,
        amount: String,
        /// Only warn at 100% (default: block new requests and pause a running one).
        #[arg(long)]
        warn: bool,
    },
    /// The cost warnings: show them, or turn one on or off.
    Warnings {
        /// update-memory, escalate or auto-escalate.
        which: Option<String>,
        state: Option<OnOff>,
    },
}

pub async fn run(c: &Client, cmd: BudgetsCmd) -> Result<()> {
    match cmd {
        BudgetsCmd::List { json } => {
            let budgets = c.get("/api/budgets").await?;
            if json {
                return print_json(&budgets);
            }
            let projects = c.get("/api/projects").await?;
            let usage = c.get("/api/usage").await?;
            let deleted = c.get("/api/projects/deleted").await?;
            let mut names: Vec<String> = projects
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect();
            for k in budgets.as_object().into_iter().flat_map(|o| o.keys()) {
                if !names.contains(k) {
                    names.push(k.clone());
                }
            }
            names.retain(|n| deleted.get(n).is_none());
            names.sort();
            let rows: Vec<Vec<String>> = names
                .iter()
                .map(|p| {
                    let b = budgets.get(p).filter(|v| !v.is_null());
                    let spent = b.and_then(|b| num(b, "spent")).unwrap_or_else(|| {
                        usage
                            .get("this_month")
                            .and_then(|m| m.get("by_project"))
                            .and_then(|bp| bp.get(p))
                            .and_then(|x| num(x, "cost"))
                            .unwrap_or(0.0)
                    });
                    match b {
                        Some(b) => vec![
                            p.clone(),
                            fmt::cost(spent),
                            fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
                            if s(b, "on_limit") == "warn" { "warn" } else { "block" }.into(),
                            state_text(b).0,
                        ],
                        None => vec![p.clone(), fmt::cost(spent), "none".into(), "".into(), "".into()],
                    }
                })
                .collect();
            println!(
                "{}",
                table(&["PROJECT", "THIS MONTH", "BUDGET", "AT 100%", "STATE"], &rows)
            );
        }
        BudgetsCmd::Set { project, amount, warn } => {
            let monthly = match amount.trim() {
                "none" | "" => None,
                t => match t.trim_start_matches('$').replace(',', ".").parse::<f64>() {
                    Ok(n) if n > 0.0 && n.is_finite() => Some(n),
                    _ => bail!("The amount: dollars more than 0, like 5 or 12.50, or none."),
                },
            };
            let v = c
                .post(
                    "/api/budgets",
                    json!({"project": project, "monthly_usd": monthly, "on_limit": if warn { "warn" } else { "block" }}),
                )
                .await?;
            match v.get("budget").filter(|b| !b.is_null()) {
                Some(b) => println!(
                    "Budget of {project}: {} a month, {} at 100%. Spent so far: {}.",
                    fmt::cost(num(b, "monthly_usd").unwrap_or(0.0)),
                    if warn { "warn" } else { "block" },
                    fmt::cost(num(b, "spent").unwrap_or(0.0))
                ),
                None => println!("{project} has no budget now."),
            }
        }
        BudgetsCmd::Warnings { which, state } => {
            let names = [("update-memory", 0), ("escalate", 1), ("auto-escalate", 2)];
            match (which, state) {
                (None, _) => {
                    let settings = c.get("/api/settings").await?;
                    for (name, i) in names {
                        let (key, label) = WARNINGS[i];
                        let on = settings.get(key).and_then(Value::as_bool) != Some(false);
                        println!("{:<14}{:<4}{label}", name, if on { "on" } else { "off" });
                    }
                }
                (Some(_), None) => bail!("on or off? Like: optimux-tui budgets warnings escalate off"),
                (Some(w), Some(st)) => {
                    let Some((_, i)) = names.iter().find(|(n, _)| *n == w) else {
                        bail!("Which warning: update-memory, escalate or auto-escalate.");
                    };
                    let (key, label) = WARNINGS[*i];
                    let on = matches!(st, OnOff::On);
                    c.save_settings(json!({ key: on })).await?;
                    println!("{label}: {}.", if on { "on" } else { "off" });
                }
            }
        }
    }
    Ok(())
}
