//! optimux-tui: Optimux in the terminal. A client of the Optimux server, like the web
//! page; it talks the protocol in docs/PROTOCOL.md.
//!
//! `optimux-tui` opens the chat window; `login`, `logout` and `status` manage the sign-in;
//! `models`, `keys` (and the other sections' commands) change settings from scripts.

mod api;
mod chat;
mod cli;
mod dash;
mod panes;
mod server;
mod socket;
mod store;
mod ui;

use std::io::{BufRead, IsTerminal, Write};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};

use api::{Api, Login};
use server::Server;
use socket::Auth;
use store::{Saved, Store};

/// Exit code when the client is not signed in (scripts can test for it).
const NOT_SIGNED_IN: u8 = 3;

#[derive(Parser)]
#[command(name = "optimux-tui", version = env!("OPTIMUX_VERSION"), about = "Optimux in the terminal")]
struct Cli {
    /// The Optimux server, like 127.0.0.1:8000 or https://optimux.example.com.
    /// Default: $OPTIMUX_URL, else the last one you signed in to, else 127.0.0.1:8000.
    #[arg(long, short, global = true)]
    server: Option<String>,
    /// Allow plain http to another computer (the password travels unencrypted).
    #[arg(long, global = true)]
    allow_http: bool,
    /// Leave the mouse to the terminal: a plain drag selects text, but nothing is
    /// clickable (with the mouse on, Shift+drag selects text).
    #[arg(long, global = true)]
    no_mouse: bool,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Open the chat window (the default).
    Chat,
    /// Sign in with your Optimux password (and two-step code, when it is on).
    Login {
        /// Read the password (and the code, on the next line) from standard input.
        #[arg(long)]
        password_stdin: bool,
    },
    /// Sign out: ends the session on the server and forgets it here.
    Logout,
    /// Show the server, whether you are signed in, and its version.
    Status,
    /// The models: list, test, add, edit, delete, the catalog (Dashboard → Models).
    Models {
        #[command(subcommand)]
        cmd: cli::models::ModelsCmd,
    },
    /// API keys, saved in ~/.optimux/secrets.env on the server; never shown.
    Keys {
        #[command(subcommand)]
        cmd: cli::keys::KeysCmd,
    },
    /// Which model handles which tier, planning, the scout (Dashboard → Routing).
    Routing {
        #[command(subcommand)]
        cmd: cli::routing::RoutingCmd,
    },
    /// Monthly budgets per project and the cost warnings (Dashboard → Budgets).
    Budgets {
        #[command(subcommand)]
        cmd: cli::budgets::BudgetsCmd,
    },
    /// Tokens, cost and time; the task records (Dashboard → Usage).
    Usage {
        #[command(subcommand)]
        cmd: cli::usage::UsageCmd,
    },
    /// Projects: list, new, switch, archive, restore, delete, clone (Dashboard → Projects).
    Projects {
        #[command(subcommand)]
        cmd: cli::projects::ProjectsCmd,
    },
    /// One project's own settings (Dashboard → Project).
    Project {
        #[command(subcommand)]
        cmd: cli::projects::ProjectCmd,
    },
    /// The Workspace folder (Dashboard → Scope).
    Scope {
        #[command(subcommand)]
        cmd: cli::projects::ScopeCmd,
    },
    /// Autonomy, sandbox, command environment and limits (Dashboard → Execution).
    Execution {
        #[command(subcommand)]
        cmd: cli::config::ExecutionCmd,
    },
    /// The memory files and the project memory's history (Dashboard → Memory).
    Memory {
        #[command(subcommand)]
        cmd: cli::config::MemoryCmd,
    },
    /// Skills: list, new, edit, mode, remove (Dashboard → Skills).
    Skills {
        #[command(subcommand)]
        cmd: cli::config::SkillsCmd,
    },
    /// Every stored chat, and what was sent to the models (Dashboard → History).
    History {
        #[command(subcommand)]
        cmd: cli::system::HistoryCmd,
    },
    /// Spending, budgets, models and recent chats at a glance (Dashboard → Overview).
    Overview {
        #[arg(long)]
        json: bool,
    },
    /// Files, sign-in, security, language, cooldowns (Dashboard → System).
    System {
        #[command(subcommand)]
        cmd: cli::system::SystemCmd,
    },
    /// The active project's files and its Git pane (Dashboard → Studio).
    Studio {
        #[command(subcommand)]
        cmd: cli::studio::StudioCmd,
    },
    /// The user guide: contents, find, read (Dashboard → Guide).
    Guide {
        #[command(subcommand)]
        cmd: Option<cli::help::GuideCmd>,
    },
    /// The setup steps, each ticked when done (Dashboard → Getting started).
    #[command(alias = "getting-started")]
    Start {
        #[command(subcommand)]
        cmd: Option<cli::help::StartCmd>,
    },
    /// The GitHub token and who commits (Dashboard → GitHub).
    #[command(name = "github")]
    GitHub {
        #[command(subcommand)]
        cmd: cli::github::GitHubCmd,
    },
    /// Snapshots, daily database backups and restoring (Dashboard → Backups).
    Backups {
        #[command(subcommand)]
        cmd: cli::backups::BackupsCmd,
    },
    /// Cost, speed and success of every model (Dashboard → Performance).
    Performance {
        /// 7, 30, 90, or 0 for all time.
        #[arg(long, default_value_t = 30)]
        days: u32,
        #[arg(long)]
        project: Option<String>,
        #[arg(long)]
        json: bool,
    },
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    // `optimux-tui models list | head`: a closed pipe ends the program quietly, as with
    // other Unix tools (Rust ignores SIGPIPE, so printing would panic instead)
    #[cfg(unix)]
    unsafe {
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
    }
    let cli = Cli::parse();
    match run(cli).await {
        Ok(code) => code,
        Err(e) if e.downcast_ref::<cli::Failed>().is_some() => ExitCode::FAILURE,
        Err(e) if e.downcast_ref::<cli::NotSignedIn>().is_some() => {
            eprintln!("{e}");
            ExitCode::from(NOT_SIGNED_IN)
        }
        Err(e) => {
            eprintln!("Error: {e:#}");
            ExitCode::FAILURE
        }
    }
}

async fn run(cli: Cli) -> Result<ExitCode> {
    let dir = store::folder()?;
    let mut saved = Store::load(&dir)?;
    let server = pick_server(&cli, &saved)?;
    match cli.command.unwrap_or(Command::Chat) {
        Command::Chat => chat(&server, &mut saved, &dir, !cli.no_mouse).await,
        Command::Login { password_stdin } => login(&server, &mut saved, &dir, password_stdin).await,
        Command::Logout => logout(&server, &mut saved, &dir).await,
        Command::Status => status(&server, &saved).await,
        Command::Models { cmd } => {
            cli::models::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Keys { cmd } => {
            cli::keys::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Routing { cmd } => {
            cli::routing::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Budgets { cmd } => {
            cli::budgets::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Usage { cmd } => {
            cli::usage::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Projects { cmd } => {
            cli::projects::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Project { cmd } => {
            cli::projects::run_project(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Scope { cmd } => {
            cli::projects::run_scope(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Execution { cmd } => {
            cli::config::run_execution(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Memory { cmd } => {
            cli::config::run_memory(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Skills { cmd } => {
            cli::config::run_skills(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::History { cmd } => {
            cli::system::run_history(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Overview { json } => {
            cli::system::overview(&cli::Client::new(&server, &saved).await?, json).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::System { cmd } => {
            cli::system::run_system(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Studio { cmd } => {
            cli::studio::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Guide { cmd } => {
            cli::help::guide(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Start { cmd } => {
            cli::help::start(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::GitHub { cmd } => {
            cli::github::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Backups { cmd } => {
            cli::backups::run(&cli::Client::new(&server, &saved).await?, cmd).await?;
            Ok(ExitCode::SUCCESS)
        }
        Command::Performance { days, project, json } => {
            cli::usage::performance(&cli::Client::new(&server, &saved).await?, days, project, json).await?;
            Ok(ExitCode::SUCCESS)
        }
    }
}

fn pick_server(cli: &Cli, saved: &Store) -> Result<Server> {
    let env = std::env::var("OPTIMUX_URL").ok().filter(|v| !v.is_empty());
    let chosen = cli.server.clone().or(env).or_else(|| saved.default.clone());
    Server::parse(chosen.as_deref().unwrap_or(server::DEFAULT), cli.allow_http)
}

async fn login(server: &Server, saved: &mut Store, dir: &std::path::Path, from_stdin: bool) -> Result<ExitCode> {
    sign_in(server, saved, dir, from_stdin).await?;
    Ok(ExitCode::SUCCESS)
}

/// Asks for the password (and the two-step code), signs in, checks the chat socket and
/// keeps the session. Returns the session cookie.
async fn sign_in(server: &Server, saved: &mut Store, dir: &std::path::Path, from_stdin: bool) -> Result<String> {
    let api = Api::new(server)?;
    let info = api.status().await?;
    if info.setup_needed {
        bail!(
            "This Optimux has no password yet. Choose one first: open it in the browser \
             (with the access token), or run `optimux password` on the server."
        );
    }
    if info.team {
        // v1 is for one person: in team mode that is the admin. Members use the web page.
        println!("Team mode: signing in as the admin.");
    }
    let mut input = Input::new(from_stdin);
    let password = input.secret("Password: ")?;
    let mut code = if info.totp {
        input.line("Two-step code: ")?
    } else {
        String::new()
    };
    let mut answer = api.login(&password, &code, info.team).await?;
    if answer == Login::NeedCode && code.is_empty() {
        code = input.line("Two-step code: ")?; // turned on after the status check
        answer = api.login(&password, &code, info.team).await?;
    }
    let cookie = match answer {
        Login::Ok(cookie) => cookie,
        Login::NeedCode => bail!("A two-step code is needed."),
        Login::Refused(why) => bail!("{why}"),
        // The server's words already say how long to wait; ours only when it sent none
        Login::Wait(_, why) if !why.is_empty() => bail!("{why}"),
        Login::Wait(secs, _) => bail!("Too many tries. Try again in {}.", minutes(secs)),
        Login::NoPassword => bail!("This Optimux has no password yet. Choose one in the browser first."),
        Login::Other(code, why) => bail!("The server answered {code}: {why}"),
    };
    let hello = socket::hello(server, Auth::Session(&cookie))
        .await
        .context("Signed in, but the chat socket refused the session")?;
    saved.servers.insert(
        server.base.clone(),
        Saved {
            cookie: cookie.clone(),
            signed_in_at: now(),
        },
    );
    saved.default = Some(server.base.clone());
    saved.save(dir)?;
    println!("Signed in to {} (Optimux {}).", server.base, hello.version);
    warn_if_other_version(&hello.version);
    Ok(cookie)
}

async fn logout(server: &Server, saved: &mut Store, dir: &std::path::Path) -> Result<ExitCode> {
    let Some(entry) = saved.servers.remove(&server.base) else {
        println!("Not signed in to {}.", server.base);
        return Ok(ExitCode::SUCCESS);
    };
    let ended = Api::new(server)?.logout(&entry.cookie).await;
    saved.save(dir)?; // forgotten here even when the server can't be reached
    match ended {
        Ok(()) => println!("Signed out of {}.", server.base),
        Err(e) => eprintln!(
            "Forgotten here, but the server could not be told ({e:#}). The session stays valid \
             there until it expires; to end it now, use Dashboard → System → Sign-in → Sign out the others."
        ),
    }
    Ok(ExitCode::SUCCESS)
}

async fn chat(server: &Server, saved: &mut Store, dir: &std::path::Path, mouse: bool) -> Result<ExitCode> {
    let token = std::env::var("OPTIMUX_TOKEN").ok().filter(|t| !t.is_empty());
    let cred = if let Some(token) = token {
        chat::link::Cred::Token(token)
    } else {
        let alive = match saved.servers.get(&server.base) {
            Some(entry) if Api::new(server)?.check(&entry.cookie).await? => Some(entry.cookie.clone()),
            Some(_) => {
                eprintln!("The session with {} ended.", server.base);
                None
            }
            None => {
                eprintln!("Not signed in to {}.", server.base);
                None
            }
        };
        match alive {
            Some(cookie) => chat::link::Cred::Session(cookie),
            // No session: sign in here and go on to the chat. Without a terminal (a
            // script) there is nobody to ask: stop with "not signed in".
            None if std::io::stdin().is_terminal() && std::io::stdout().is_terminal() => {
                chat::link::Cred::Session(sign_in(server, saved, dir, false).await?)
            }
            None => {
                eprintln!("Run: optimux-tui login");
                return Ok(ExitCode::from(NOT_SIGNED_IN));
            }
        }
    };
    match chat::run(server.clone(), cred, mouse).await {
        Err(e) if matches!(e.downcast_ref::<socket::Refused>(), Some(socket::Refused(4401))) => {
            eprintln!("{e}");
            Ok(ExitCode::from(NOT_SIGNED_IN))
        }
        Err(e) => Err(e),
        Ok(()) => Ok(ExitCode::SUCCESS),
    }
}

async fn status(server: &Server, saved: &Store) -> Result<ExitCode> {
    println!("Server:     {}", server.base);
    let api = Api::new(server)?;
    let info = api.status().await?;
    println!("Two-step:   {}", if info.totp { "on" } else { "off" });
    if info.team {
        println!("Mode:       team (this client signs in as the admin)");
    }
    let token = std::env::var("OPTIMUX_TOKEN").ok().filter(|t| !t.is_empty());
    let (auth, how) = if let Some(token) = token.as_deref() {
        (Auth::Token(token), "access token ($OPTIMUX_TOKEN)".to_string())
    } else if let Some(entry) = saved.servers.get(&server.base) {
        if !api.check(&entry.cookie).await? {
            println!("Signed in:  no, the session ended. Run: optimux-tui login");
            return Ok(ExitCode::from(NOT_SIGNED_IN));
        }
        (
            Auth::Session(&entry.cookie),
            format!("yes, since {}", ago(entry.signed_in_at)),
        )
    } else {
        println!("Signed in:  no. Run: optimux-tui login");
        return Ok(ExitCode::from(NOT_SIGNED_IN));
    };
    let hello = match socket::hello(server, auth).await {
        Ok(hello) => hello,
        Err(e) if matches!(e.downcast_ref::<socket::Refused>(), Some(socket::Refused(4401))) => {
            println!("Signed in:  no. {e}");
            return Ok(ExitCode::from(NOT_SIGNED_IN));
        }
        Err(e) => return Err(e),
    };
    println!("Signed in:  {how}");
    println!("Optimux:    {}", hello.version);
    println!("Chat line:  ok (a dropped request waits {})", minutes(hello.away));
    warn_if_other_version(&hello.version);
    Ok(ExitCode::SUCCESS)
}

/// Reads the password without showing it, from the terminal or from standard input.
struct Input {
    stdin: Option<std::io::StdinLock<'static>>,
}

impl Input {
    fn new(from_stdin: bool) -> Input {
        Input {
            stdin: from_stdin.then(|| std::io::stdin().lock()),
        }
    }

    fn secret(&mut self, prompt: &str) -> Result<String> {
        if self.stdin.is_some() {
            return self.line("");
        }
        if !std::io::stdin().is_terminal() {
            bail!("No terminal to ask for the password: use --password-stdin.");
        }
        let password = rpassword::prompt_password(prompt)?;
        if password.is_empty() {
            bail!("No password given.");
        }
        Ok(password)
    }

    fn line(&mut self, prompt: &str) -> Result<String> {
        let mut text = String::new();
        if let Some(stdin) = self.stdin.as_mut() {
            stdin.read_line(&mut text)?;
        } else {
            print!("{prompt}");
            std::io::stdout().flush()?;
            std::io::stdin().read_line(&mut text)?;
        }
        Ok(text.trim_end_matches(['\r', '\n']).to_string())
    }
}

/// The client and the server should be the same release: the protocol can change
/// between releases (major.minor). Only a warning; the server stays in charge.
fn warn_if_other_version(server_version: &str) {
    let mine = env!("OPTIMUX_VERSION");
    if !same_release(mine, server_version) {
        eprintln!(
            "Note: this client is {mine} and the server is {server_version}. \
             If something looks wrong, use the client of the same version."
        );
    }
}

fn same_release(a: &str, b: &str) -> bool {
    fn release(v: &str) -> Option<(u32, u32)> {
        let v = v.trim().trim_start_matches('v');
        let mut parts = v.split(|c: char| !c.is_ascii_digit());
        Some((parts.next()?.parse().ok()?, parts.next()?.parse().ok()?))
    }
    match (release(a), release(b)) {
        (Some(x), Some(y)) => x == y,
        _ => true, // "dev" or unknown: nothing useful to say
    }
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn ago(then: u64) -> String {
    let secs = now().saturating_sub(then);
    match secs {
        0..=59 => "just now".into(),
        60..=3599 => format!("{} min ago", secs / 60),
        3600..=86399 => format!("{} h ago", secs / 3600),
        _ => format!("{} days ago", secs / 86400),
    }
}

fn minutes(secs: u64) -> String {
    if secs < 120 {
        format!("{secs} seconds")
    } else {
        format!("{} minutes", secs.div_ceil(60))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn releases_compare_by_major_and_minor() {
        assert!(same_release("0.8.0", "0.8.3"));
        assert!(same_release("v0.8.0", "0.8.0 (abc123)"));
        assert!(!same_release("0.8.0", "0.9.0"));
        assert!(same_release("dev", "0.9.0"));
    }

    #[test]
    fn times_in_words() {
        assert_eq!(minutes(120), "2 minutes");
        assert_eq!(minutes(45), "45 seconds");
        assert_eq!(minutes(1800), "30 minutes");
        assert_eq!(ago(now()), "just now");
        assert_eq!(ago(now() - 7200), "2 h ago");
    }

    #[test]
    fn every_command_is_well_formed() {
        // clap checks names, groups and flags of every command and sub-command
        use clap::CommandFactory;
        Cli::command().debug_assert();
    }
}
