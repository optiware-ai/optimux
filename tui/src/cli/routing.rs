//! `optimux-tui routing ...`: which model handles which tier, planning, the scout.

use anyhow::{Result, bail};
use clap::Subcommand;
use serde_json::{Value, json};

use super::models::OnOff;
use super::{Client, print_json, s};
use crate::dash::routing::{PLAN, ROLES};

#[derive(Subcommand)]
pub enum RoutingCmd {
    /// The models of the three roles, planning and the scout.
    Show {
        #[arg(long)]
        json: bool,
    },
    /// Change roles; only what you give changes. A model ID, or "none"; the Conductor
    /// also takes "off" (always start on Simple).
    Set {
        #[arg(long)]
        conductor: Option<String>,
        #[arg(long)]
        simple: Option<String>,
        #[arg(long)]
        complex: Option<String>,
        /// Plan big tasks: off, ask, auto.
        #[arg(long)]
        plan: Option<String>,
        /// The scout before Complex: on or off.
        #[arg(long)]
        scout: Option<OnOff>,
    },
}

pub async fn run(c: &Client, cmd: RoutingCmd) -> Result<()> {
    match cmd {
        RoutingCmd::Show { json } => {
            let routing = c.get("/api/routing").await?;
            let settings = c.get("/api/settings").await?;
            let own = c.get("/api/routing/projects").await?;
            if json {
                return print_json(&json!({
                    "routing": routing,
                    "plan_mode": settings.get("plan_mode"),
                    "scout": settings.get("scout"),
                    "projects": own.get("projects"),
                }));
            }
            for (k, label, _) in ROLES {
                let v = s(&routing, k);
                println!(
                    "{:<18}{}",
                    format!("{label}:"),
                    if v.is_empty() { "– none –".into() } else { v }
                );
            }
            let plan = settings.get("plan_mode").and_then(Value::as_str).unwrap_or("off");
            println!("{:<18}{plan}", "Plan big tasks:");
            let scout = settings.get("scout").and_then(Value::as_bool) != Some(false);
            println!("{:<18}{}", "Scout:", if scout { "on" } else { "off" });
            if let Some(o) = own.get("projects").and_then(Value::as_object).filter(|o| !o.is_empty()) {
                println!("\nProjects with their own models:");
                for (name, m) in o {
                    let parts: Vec<String> = ROLES
                        .iter()
                        .filter(|(k, ..)| !s(m, k).is_empty())
                        .map(|(k, label, _)| format!("{}: {}", label.split(' ').next().unwrap_or(label), s(m, k)))
                        .collect();
                    println!("  {name}  {}", parts.join(", "));
                }
            }
        }
        RoutingCmd::Set {
            conductor,
            simple,
            complex,
            plan,
            scout,
        } => {
            if conductor.is_none() && simple.is_none() && complex.is_none() && plan.is_none() && scout.is_none() {
                bail!("Nothing to change: give --conductor, --simple, --complex, --plan or --scout.");
            }
            if conductor.is_some() || simple.is_some() || complex.is_some() {
                let mut routing = c.get("/api/routing").await?;
                let config = c.get("/api/config").await?;
                let known: Vec<String> = ["available", "requires_setup"]
                    .iter()
                    .flat_map(|k| config.get(*k).and_then(Value::as_array).cloned().unwrap_or_default())
                    .map(|m| s(&m, "id"))
                    .collect();
                for (key, given) in [
                    ("conductor_model", &conductor),
                    ("simple_model", &simple),
                    ("complex_model", &complex),
                ] {
                    let Some(v) = given.as_deref().map(str::trim) else {
                        continue;
                    };
                    let v = match v {
                        "none" | "" => "",
                        "off" if key == "conductor_model" => "off",
                        "off" => bail!("Only the Conductor can be off."),
                        id if known.iter().any(|k| k == id) => id,
                        id => bail!("{id} is not configured. See: optimux-tui models list"),
                    };
                    routing[key] = json!(v);
                }
                let body = json!({
                    "conductor_model": s(&routing, "conductor_model"),
                    "simple_model": s(&routing, "simple_model"),
                    "complex_model": s(&routing, "complex_model"),
                });
                c.post("/api/routing", body).await?;
            }
            let mut changes = serde_json::Map::new();
            if let Some(p) = plan {
                if !PLAN.iter().any(|(v, ..)| *v == p) {
                    bail!("--plan: off, ask or auto.");
                }
                changes.insert("plan_mode".into(), json!(p));
            }
            if let Some(o) = scout {
                changes.insert("scout".into(), json!(matches!(o, OnOff::On)));
            }
            if !changes.is_empty() {
                c.save_settings(Value::Object(changes)).await?;
            }
            println!("Saved. New messages use it:");
            Box::pin(run(c, RoutingCmd::Show { json: false })).await?;
        }
    }
    Ok(())
}
