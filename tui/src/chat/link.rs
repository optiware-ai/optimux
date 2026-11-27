//! The line to the server: one task that keeps `/ws` open. It passes the server's
//! messages to the screen, sends the screen's messages, pings every 5 seconds (so the
//! server knows when this client is gone: PROTOCOL.md, 5) and opens the line again when
//! it drops, waiting a little longer each time.

use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::sync::mpsc;
use tokio::time::{Instant, interval, sleep};
use tokio_tungstenite::tungstenite::Message;

use crate::server::Server;
use crate::socket::{self, Auth, close_reason};

/// Sent by the screen to reconnect at once (after a take-over, or to skip the wait).
/// Never reaches the server.
pub const RECONNECT: &str = "__reconnect";
/// Sent by the screen to close this line and open a fresh one at once: after `detach`,
/// so the running request stays on the server (left on purpose) while this window opens
/// another chat. Never reaches the server.
pub const REOPEN: &str = "__reopen";

const PING_EVERY: Duration = Duration::from_secs(5);
/// Every ping gets a `pong`: this long without a word means the line is dead, even if
/// it was never closed (a sleeping laptop, a lost network).
const DEAD_AFTER: Duration = Duration::from_secs(20);

#[derive(Debug)]
pub enum Event {
    /// A message from the server (`auth_ok` first, after every connect).
    Message(Value),
    /// The line dropped: trying again in this many seconds.
    Down(String, u64),
    /// The server refused the sign-in (a close code): no point in trying again.
    Refused(u16),
    /// Another window picked up this client's request (close code 4409, after this
    /// client was silent). No automatic reconnect: two windows on one chat would fight
    /// over it. The line waits for `RECONNECT`.
    TakenOver,
}

/// Owned copy of the sign-in, for the task.
#[derive(Clone)]
pub enum Cred {
    Session(String),
    Token(String),
}

impl Cred {
    pub fn auth(&self) -> Auth<'_> {
        match self {
            Cred::Session(c) => Auth::Session(c),
            Cred::Token(t) => Auth::Token(t),
        }
    }
}

/// Starts the line. Messages sent while it is down are dropped: the screen knows the
/// line is down and says so instead of sending.
pub fn start(server: Server, cred: Cred) -> (mpsc::UnboundedSender<Value>, mpsc::UnboundedReceiver<Event>) {
    let (out_tx, mut out_rx) = mpsc::unbounded_channel::<Value>();
    let (in_tx, in_rx) = mpsc::unbounded_channel::<Event>();
    tokio::spawn(async move {
        let mut wait = 1u64;
        loop {
            let reason = match socket::connect(&server, &cred.auth()).await {
                Ok(ws) => {
                    let started = Instant::now();
                    let why = run(ws, &mut out_rx, &in_tx).await;
                    if started.elapsed() > Duration::from_secs(30) {
                        wait = 1; // it was a good line: try again soon
                    }
                    why
                }
                Err(e) => Gone::Failed(format!("{e:#}")),
            };
            match reason {
                Gone::Refused(code) => {
                    let _ = in_tx.send(Event::Refused(code));
                    return;
                }
                Gone::Closed => return,   // the screen has quit
                Gone::Reopen => wait = 1, // again, at once
                Gone::TakenOver => {
                    if in_tx.send(Event::TakenOver).is_err() {
                        return;
                    }
                    // wait for the user's Ctrl+L; anything else sent meanwhile is too late
                    loop {
                        match out_rx.recv().await {
                            Some(m) if is_reconnect(&m) => break,
                            Some(_) => {}
                            None => return,
                        }
                    }
                    wait = 1;
                }
                Gone::Failed(why) => {
                    if in_tx.send(Event::Down(why, wait)).is_err() {
                        return;
                    }
                    // wait, unless the user asks to try now (Ctrl+L)
                    let pause = sleep(Duration::from_secs(wait));
                    tokio::pin!(pause);
                    loop {
                        tokio::select! {
                            _ = &mut pause => break,
                            m = out_rx.recv() => match m {
                                Some(m) if is_reconnect(&m) => break,
                                Some(_) => {}   // too late for anything sent meanwhile
                                None => return,
                            },
                        }
                    }
                    wait = (wait * 2).min(15);
                }
            }
        }
    });
    (out_tx, in_rx)
}

fn is_reconnect(m: &Value) -> bool {
    matches!(m.get("type").and_then(Value::as_str), Some(RECONNECT | REOPEN))
}

fn is_reopen(m: &Value) -> bool {
    m.get("type").and_then(Value::as_str) == Some(REOPEN)
}

enum Gone {
    Refused(u16),
    /// Closed on purpose to open a fresh line at once.
    Reopen,
    TakenOver,
    Failed(String),
    Closed,
}

async fn run(
    mut ws: socket::Socket,
    out_rx: &mut mpsc::UnboundedReceiver<Value>,
    in_tx: &mpsc::UnboundedSender<Event>,
) -> Gone {
    let mut ping = interval(PING_EVERY);
    ping.tick().await;
    let mut heard = Instant::now();
    loop {
        tokio::select! {
            incoming = ws.next() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    heard = Instant::now();
                    let Ok(msg) = serde_json::from_str::<Value>(&text) else { continue };
                    if msg.get("type").and_then(Value::as_str) == Some("error")
                        && msg.get("content").and_then(Value::as_str) == Some("Unauthorized")
                    {
                        return Gone::Refused(4401);
                    }
                    if in_tx.send(Event::Message(msg)).is_err() {
                        let _ = ws.close(None).await;
                        return Gone::Closed;
                    }
                }
                Some(Ok(Message::Close(frame))) => {
                    let code = frame.map(|f| u16::from(f.code)).unwrap_or(1005);
                    return if matches!(code, 1008 | 4401 | 4403 | 4429) {
                        Gone::Refused(code)
                    } else if code == 4409 {
                        Gone::TakenOver
                    } else {
                        Gone::Failed(close_reason(code))
                    };
                }
                Some(Ok(_)) => {}
                Some(Err(e)) => return Gone::Failed(e.to_string()),
                None => return Gone::Failed("The server closed the line.".into()),
            },
            outgoing = out_rx.recv() => match outgoing {
                Some(msg) if is_reopen(&msg) => {
                    let _ = ws.close(None).await;   // after detach: the request stays, left on purpose
                    return Gone::Reopen;
                }
                Some(msg) if is_reconnect(&msg) => {}   // already connected
                Some(msg) => {
                    if let Err(e) = ws.send(Message::text(msg.to_string())).await {
                        return Gone::Failed(e.to_string());
                    }
                }
                None => {
                    let _ = ws.close(None).await;
                    return Gone::Closed;
                }
            },
            _ = ping.tick() => {
                if heard.elapsed() > DEAD_AFTER {
                    return Gone::Failed("No answer from the server for 20 seconds.".into());
                }
                let t = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_millis() as u64)
                    .unwrap_or(0);
                if let Err(e) = ws.send(Message::text(json!({"type": "ping", "t": t}).to_string())).await {
                    return Gone::Failed(e.to_string());
                }
            }
        }
    }
}
