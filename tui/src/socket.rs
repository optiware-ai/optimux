//! The chat socket `/ws` (docs/PROTOCOL.md). For now only the hello: sign in and read
//! `auth_ok`. The chat itself comes with the chat window (T3).

use std::time::Duration;

use anyhow::{Context, Result, bail};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::time::timeout;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::{Message, http::header};

use crate::api::{COOKIE, user_agent};
use crate::server::Server;

/// The server refused the sign-in (a close code of docs/PROTOCOL.md).
#[derive(Debug)]
pub struct Refused(pub u16);

impl std::fmt::Display for Refused {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&close_reason(self.0))
    }
}

impl std::error::Error for Refused {}

#[derive(Clone, Copy)]
pub enum Auth<'a> {
    Session(&'a str),
    Token(&'a str),
}

/// What `auth_ok` says.
#[derive(Debug)]
pub struct Hello {
    pub version: String,
    /// Seconds a request waits for a client that dropped.
    pub away: u64,
}

pub type Socket = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

/// Opens `/ws` and signs in: the session cookie goes with the opening request, a token
/// is the first message (docs/PROTOCOL.md, 1). The server's first answer is still to
/// be read (`auth_ok`, or a refusal).
pub async fn connect(server: &Server, auth: &Auth<'_>) -> Result<Socket> {
    let mut request = server.ws_url().into_client_request()?;
    let headers = request.headers_mut();
    headers.insert(header::USER_AGENT, HeaderValue::from_str(&user_agent())?);
    // No Origin header: the server lets non-browser clients in (docs/PROTOCOL.md, 1)
    if let Auth::Session(cookie) = auth {
        headers.insert(header::COOKIE, HeaderValue::from_str(&format!("{COOKIE}={cookie}"))?);
    }
    let (mut ws, _) = timeout(Duration::from_secs(15), tokio_tungstenite::connect_async(request))
        .await
        .context("The chat socket did not answer in 15 seconds.")?
        .with_context(|| format!("Can't open the chat socket at {}", server.ws_url()))?;
    if let Auth::Token(token) = auth {
        ws.send(Message::text(json!({"type": "auth", "token": token}).to_string()))
            .await?;
    }
    Ok(ws)
}

/// Signs in, reads `auth_ok` and closes: the check of `login` and `status`.
pub async fn hello(server: &Server, auth: Auth<'_>) -> Result<Hello> {
    let mut ws = connect(server, &auth).await?;
    let result = loop {
        let next = timeout(Duration::from_secs(15), ws.next())
            .await
            .context("The server sent nothing for 15 seconds after the sign-in.")?;
        match next {
            Some(Ok(Message::Text(text))) => break read_first(&text),
            Some(Ok(Message::Close(frame))) => {
                return Err(Refused(frame.map(|f| u16::from(f.code)).unwrap_or(1005)).into());
            }
            Some(Ok(_)) => continue, // ping, pong, binary
            Some(Err(e)) => return Err(e).context("The chat socket failed"),
            None => bail!("The server closed the chat socket."),
        }
    };
    let _ = ws.close(None).await;
    result
}

fn read_first(text: &str) -> Result<Hello> {
    let msg: Value = serde_json::from_str(text).context("The server sent something that is not JSON.")?;
    let content = msg.get("content").and_then(Value::as_str).unwrap_or("");
    match msg.get("type").and_then(Value::as_str) {
        Some("auth_ok") => Ok(Hello {
            version: msg.get("version").and_then(Value::as_str).unwrap_or("?").to_string(),
            away: msg.get("away").and_then(Value::as_u64).unwrap_or(0),
        }),
        Some("error") if content == "Unauthorized" => Err(Refused(4401).into()),
        Some(_) if !content.is_empty() => bail!("{content}"),
        other => bail!("Expected auth_ok, got {:?}.", other.unwrap_or("nothing")),
    }
}

/// The close codes of docs/PROTOCOL.md, in words.
pub fn close_reason(code: u16) -> String {
    match code {
        1008 => "The server refused this client's origin.".into(),
        4401 => "The session ended or the token is wrong. Run: optimux-tui login".into(),
        4403 => "This account can't use Optimux now (disabled, or on no project).".into(),
        4429 => "Too many failed sign-ins from this address. Wait a while and try again.".into(),
        c => format!("The server closed the chat socket (code {c})."),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auth_ok_is_read() {
        let h = read_first(r#"{"type":"auth_ok","build":"b","away":120,"version":"0.8.0"}"#).unwrap();
        assert_eq!((h.version.as_str(), h.away), ("0.8.0", 120));
    }

    #[test]
    fn a_refusal_says_why() {
        let err = read_first(r#"{"type":"error","content":"Unauthorized"}"#)
            .unwrap_err()
            .to_string();
        assert!(err.contains("optimux-tui login"), "{err}");
        let err = read_first(r#"{"type":"no_projects","content":"You are not on any project yet."}"#).unwrap_err();
        assert_eq!(err.to_string(), "You are not on any project yet.");
        assert!(read_first(r#"{"type":"pong"}"#).is_err());
        assert!(read_first("not json").is_err());
    }

    #[test]
    fn close_codes_in_words() {
        assert!(close_reason(4429).contains("Too many"));
        assert!(close_reason(4999).contains("4999"));
    }
}
