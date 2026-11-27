//! The server's HTTP calls the client needs for signing in (docs/PROTOCOL.md, part 1).

use std::time::Duration;

use anyhow::{Context, Result};
use reqwest::{StatusCode, header};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::server::Server;
use crate::socket::Auth;

pub const COOKIE: &str = "optimux_session";

pub fn user_agent() -> String {
    format!("optimux-tui/{} ({})", env!("OPTIMUX_VERSION"), std::env::consts::OS)
}

/// `GET /auth/status`: open to anyone.
#[derive(Debug, Deserialize)]
pub struct AuthStatus {
    #[serde(default)]
    pub setup_needed: bool,
    #[serde(default)]
    pub totp: bool,
    #[serde(default)]
    pub team: bool,
}

/// One chat of `GET /api/sessions`.
#[derive(Debug, Clone, Deserialize)]
pub struct ChatInfo {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub updated: f64,
    #[serde(default)]
    pub turns: i64,
    #[serde(default)]
    pub cost: f64,
    /// "working" or "waiting" (a question is open) while a request runs in it.
    #[serde(default)]
    pub state: Option<String>,
    /// No window is on its running request (it was left running, or its line dropped).
    #[serde(default)]
    pub away: bool,
}

/// One project of `GET /api/projects/overview`.
#[derive(Debug, Clone, Deserialize)]
pub struct ProjectInfo {
    pub project: String,
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub archived: bool,
    #[serde(default)]
    pub exists: bool,
    #[serde(default)]
    pub chats: i64,
    #[serde(default)]
    pub month_cost: f64,
}

/// What `POST /auth/login` answered.
#[derive(Debug, PartialEq)]
pub enum Login {
    /// Signed in: the session cookie's value.
    Ok(String),
    /// Two-step sign-in is on and no code was sent.
    NeedCode,
    /// Wrong password or code (the server's words).
    Refused(String),
    /// Too many tries: wait this many seconds.
    Wait(u64, String),
    /// No password has been chosen on this server yet.
    NoPassword,
    Other(u16, String),
}

#[derive(Clone)]
pub struct Api {
    http: reqwest::Client,
    server: Server,
}

impl Api {
    pub fn new(server: &Server) -> Result<Api> {
        let mut builder = reqwest::Client::builder()
            .user_agent(user_agent())
            .timeout(Duration::from_secs(20))
            .redirect(reqwest::redirect::Policy::none())
            // A new connection for every call. Between two calls the user may type a
            // password for a while, and the server closes idle connections (uvicorn
            // after 5 s): a kept one would fail with "connection closed".
            .pool_max_idle_per_host(0);
        if server.is_local() {
            builder = builder.no_proxy(); // a session for this computer never goes through a proxy
        }
        Ok(Api {
            http: builder.build()?,
            server: server.clone(),
        })
    }

    pub async fn status(&self) -> Result<AuthStatus> {
        let resp = self
            .http
            .get(self.server.url("/auth/status"))
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {} (is it running?)", self.server.base))?;
        if !resp.status().is_success() {
            anyhow::bail!(
                "{} answered {} to /auth/status: is this Optimux?",
                self.server.base,
                resp.status()
            );
        }
        resp.json()
            .await
            .context("The server's answer is not Optimux's: is this the right address?")
    }

    pub async fn login(&self, password: &str, code: &str, admin: bool) -> Result<Login> {
        let resp = self
            .http
            .post(self.server.url("/auth/login"))
            .header(header::ORIGIN, &self.server.base)
            .json(&json!({"password": password, "code": code, "admin": admin}))
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        let status = resp.status();
        let cookie = session_cookie(resp.headers());
        let retry = resp
            .headers()
            .get(header::RETRY_AFTER)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.parse().ok())
            .unwrap_or(60);
        let body: Value = resp.json().await.unwrap_or(Value::Null);
        Ok(read_login(status, cookie, retry, &body))
    }

    /// Is this session still signed in? (`GET /api/auth` needs a sign-in.)
    pub async fn check(&self, cookie: &str) -> Result<bool> {
        let resp = self
            .http
            .get(self.server.url("/api/auth"))
            .header(header::COOKIE, format!("{COOKIE}={cookie}"))
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        match resp.status() {
            s if s.is_success() => Ok(true),
            StatusCode::UNAUTHORIZED => Ok(false),
            s => anyhow::bail!("{} answered {s} to /api/auth", self.server.base),
        }
    }

    pub async fn logout(&self, cookie: &str) -> Result<()> {
        let resp = self
            .http
            .post(self.server.url("/auth/logout"))
            .header(header::ORIGIN, &self.server.base)
            .header(header::COOKIE, format!("{COOKIE}={cookie}"))
            .send()
            .await?;
        anyhow::ensure!(resp.status().is_success(), "the server answered {}", resp.status());
        Ok(())
    }
}

impl Api {
    /// Signs a call in. A change made with the cookie must carry `Origin` (PROTOCOL.md, 1).
    fn signed(&self, rb: reqwest::RequestBuilder, auth: &Auth<'_>) -> reqwest::RequestBuilder {
        match auth {
            Auth::Session(cookie) => rb
                .header(header::COOKIE, format!("{COOKIE}={cookie}"))
                .header(header::ORIGIN, &self.server.base),
            Auth::Token(token) => rb.bearer_auth(token),
        }
    }

    pub async fn get_json<T: serde::de::DeserializeOwned>(&self, auth: &Auth<'_>, path: &str) -> Result<T> {
        let resp = self
            .signed(self.http.get(self.server.url(path)), auth)
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        anyhow::ensure!(
            resp.status().is_success(),
            "{path}: the server answered {}",
            resp.status()
        );
        resp.json().await.with_context(|| format!("{path}: unexpected answer"))
    }

    /// The active project's chats, newest first.
    pub async fn chats(&self, auth: &Auth<'_>) -> Result<Vec<ChatInfo>> {
        self.get_json(auth, "/api/sessions?limit=200").await
    }

    pub async fn projects(&self, auth: &Auth<'_>) -> Result<Vec<ProjectInfo>> {
        self.get_json(auth, "/api/projects/overview").await
    }

    /// The active project ("" is the Workspace itself).
    pub async fn active_project(&self, auth: &Auth<'_>) -> Result<String> {
        let settings: Value = self.get_json(auth, "/api/settings").await?;
        Ok(settings
            .get("active_project")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string())
    }

    /// Makes `name` the active project, like the web page: `POST /api/settings` saves
    /// every setting, so all of them are read first and sent back with only this changed.
    /// Returns the project that is active afterwards.
    pub async fn switch_project(&self, auth: &Auth<'_>, name: &str) -> Result<String> {
        let mut settings: Value = self.get_json(auth, "/api/settings").await?;
        let obj = settings.as_object_mut().context("/api/settings: unexpected answer")?;
        obj.insert("active_project".into(), Value::String(name.to_string()));
        let resp = self
            .signed(self.http.post(self.server.url("/api/settings")), auth)
            .json(&settings)
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        let status = resp.status();
        let body: Value = resp.json().await.unwrap_or(Value::Null);
        if !status.is_success() {
            let detail = body.get("detail").map(|d| d.to_string()).unwrap_or_default();
            anyhow::bail!("Could not switch the project ({status}) {detail}");
        }
        match body.get("active_project").and_then(Value::as_str) {
            Some(active) => Ok(active.to_string()),
            None => self.active_project(auth).await,
        }
    }
}

/// A call the Dashboard makes: GET, POST, DELETE with a JSON answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Get,
    Post,
    Delete,
}

impl Api {
    /// Any `/api/...` call of the Dashboard. A refusal comes back as the server's own
    /// words (its `detail`), so the screen can show why.
    pub async fn call(&self, auth: &Auth<'_>, method: Method, path: &str, body: Option<&Value>) -> Result<Value> {
        let url = self.server.url(path);
        let rb = match method {
            Method::Get => self.http.get(url),
            Method::Post => self.http.post(url),
            Method::Delete => self.http.delete(url),
        };
        let rb = self.signed(rb, auth);
        let rb = match body {
            Some(b) => rb.json(b),
            None => rb,
        };
        let resp = rb
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
        if !status.is_success() {
            anyhow::bail!(
                "{}",
                detail_text(&value).unwrap_or_else(|| format!("The server answered {status}."))
            );
        }
        Ok(value)
    }
}

impl Api {
    /// A GET whose answer is text (a CSV file), not JSON.
    pub async fn get_text(&self, auth: &Auth<'_>, path: &str) -> Result<String> {
        let resp = self
            .signed(self.http.get(self.server.url(path)), auth)
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        if !status.is_success() {
            let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
            anyhow::bail!(
                "{}",
                detail_text(&value).unwrap_or_else(|| format!("The server answered {status}."))
            );
        }
        Ok(text)
    }
}

impl Api {
    /// A download saved to `dest` (a new file, 0600; an existing file is never
    /// replaced). Read chunk by chunk, so its size doesn't matter. Returns the bytes.
    pub async fn download_to(&self, auth: &Auth<'_>, path: &str, dest: &std::path::Path) -> Result<u64> {
        use std::io::Write;
        let mut resp = self
            .signed(self.http.get(self.server.url(path)), auth)
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
            anyhow::bail!(
                "{}",
                detail_text(&value).unwrap_or_else(|| format!("The server answered {status}."))
            );
        }
        let mut o = std::fs::OpenOptions::new();
        o.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            o.mode(0o600);
        }
        let mut f = o
            .open(dest)
            .with_context(|| format!("Can't write {} (it may exist already)", dest.display()))?;
        let mut n = 0u64;
        let result: Result<()> = async {
            while let Some(chunk) = resp.chunk().await.context("The download stopped")? {
                f.write_all(&chunk)?;
                n += chunk.len() as u64;
            }
            f.flush()?;
            Ok(())
        }
        .await;
        if let Err(e) = result {
            let _ = std::fs::remove_file(dest); // no half file left behind
            return Err(e);
        }
        Ok(n)
    }

    /// A file's bytes as the raw request body (a snapshot to restore). The file is read
    /// whole: up to UPLOAD_MAX, larger ones go through the web page.
    pub async fn upload_file(&self, auth: &Auth<'_>, path: &str, file: &std::path::Path) -> Result<Value> {
        let size = std::fs::metadata(file)
            .with_context(|| format!("Can't read {}", file.display()))?
            .len();
        if size > UPLOAD_MAX {
            anyhow::bail!(
                "{} is {:.1} GB: the terminal client sends files up to {} GB. Upload it in the web page (Dashboard → Backups).",
                file.display(),
                size as f64 / 1e9,
                UPLOAD_MAX / 1_000_000_000
            );
        }
        let bytes = std::fs::read(file).with_context(|| format!("Can't read {}", file.display()))?;
        let resp = self
            .signed(self.http.post(self.server.url(path)), auth)
            .header("Content-Type", "application/octet-stream")
            .body(bytes)
            .send()
            .await
            .with_context(|| format!("Can't reach Optimux at {}", self.server.base))?;
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        let value: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
        if !status.is_success() {
            anyhow::bail!(
                "{}",
                detail_text(&value).unwrap_or_else(|| format!("The server answered {status}."))
            );
        }
        Ok(value)
    }
}

/// The largest file the client uploads (it is read whole into memory).
pub const UPLOAD_MAX: u64 = 2_000_000_000;

/// For a path segment or a query value (model ids hold "/").
pub fn enc(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// The server's reason in words: `detail` is a text, or a list of checks that failed.
pub fn detail_text(v: &Value) -> Option<String> {
    match v.get("detail")? {
        Value::String(s) => Some(s.clone()),
        Value::Array(items) => {
            let parts: Vec<String> = items
                .iter()
                .filter_map(|i| {
                    let msg = i.get("msg").and_then(Value::as_str)?;
                    let field = i
                        .get("loc")
                        .and_then(Value::as_array)
                        .and_then(|l| l.last())
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    Some(if field.is_empty() {
                        msg.to_string()
                    } else {
                        format!("{field}: {msg}")
                    })
                })
                .collect();
            (!parts.is_empty()).then(|| parts.join("; "))
        }
        other => Some(other.to_string()),
    }
}

fn read_login(status: StatusCode, cookie: Option<String>, retry: u64, body: &Value) -> Login {
    let detail = body.get("detail").and_then(Value::as_str).unwrap_or("").to_string();
    match status.as_u16() {
        200 => match cookie {
            Some(c) => Login::Ok(c),
            None => Login::Other(200, "Signed in, but the server sent no session.".into()),
        },
        401 if body.get("need_code").and_then(Value::as_bool) == Some(true) => Login::NeedCode,
        401 => Login::Refused(if detail.is_empty() {
            "Wrong password.".into()
        } else {
            detail
        }),
        429 => Login::Wait(retry, detail),
        409 => Login::NoPassword,
        code => Login::Other(code, detail),
    }
}

/// The `optimux_session` value from the `Set-Cookie` headers.
fn session_cookie(headers: &header::HeaderMap) -> Option<String> {
    headers.get_all(header::SET_COOKIE).iter().find_map(|v| {
        let first = v.to_str().ok()?.split(';').next()?.trim();
        let value = first.strip_prefix(COOKIE)?.strip_prefix('=')?.trim_matches('"');
        (!value.is_empty()).then(|| value.to_string())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::header::{HeaderMap, HeaderValue, SET_COOKIE};

    #[test]
    fn the_session_is_found_among_the_cookies() {
        let mut h = HeaderMap::new();
        h.append(SET_COOKIE, HeaderValue::from_static("other=1; Path=/"));
        h.append(
            SET_COOKIE,
            HeaderValue::from_static("optimux_session=tok-123; HttpOnly; Path=/; SameSite=strict"),
        );
        assert_eq!(session_cookie(&h).as_deref(), Some("tok-123"));
        assert_eq!(session_cookie(&HeaderMap::new()), None);
        let mut deleted = HeaderMap::new();
        deleted.append(
            SET_COOKIE,
            HeaderValue::from_static("optimux_session=\"\"; Max-Age=0; Path=/"),
        );
        assert_eq!(session_cookie(&deleted), None);
    }

    #[test]
    fn the_servers_reason_in_words() {
        assert_eq!(
            detail_text(&json!({"detail": "Model not found."})).as_deref(),
            Some("Model not found.")
        );
        let checks =
            json!({"detail": [{"loc": ["body", "input_cost_per_mtok"], "msg": "Input should be a valid number"}]});
        assert_eq!(
            detail_text(&checks).as_deref(),
            Some("input_cost_per_mtok: Input should be a valid number")
        );
        assert_eq!(detail_text(&json!({"ok": true})), None);
    }

    #[test]
    fn every_login_answer_is_understood() {
        let ok = StatusCode::OK;
        assert_eq!(read_login(ok, Some("c".into()), 0, &Value::Null), Login::Ok("c".into()));
        assert!(matches!(read_login(ok, None, 0, &Value::Null), Login::Other(200, _)));
        let s401 = StatusCode::UNAUTHORIZED;
        assert_eq!(read_login(s401, None, 0, &json!({"need_code": true})), Login::NeedCode);
        assert_eq!(
            read_login(s401, None, 0, &json!({"detail": "Wrong or already used code."})),
            Login::Refused("Wrong or already used code.".into())
        );
        assert_eq!(
            read_login(s401, None, 0, &Value::Null),
            Login::Refused("Wrong password.".into())
        );
        assert_eq!(
            read_login(StatusCode::TOO_MANY_REQUESTS, None, 900, &json!({"detail": "x"})),
            Login::Wait(900, "x".into())
        );
        assert_eq!(
            read_login(StatusCode::CONFLICT, None, 0, &Value::Null),
            Login::NoPassword
        );
        assert!(
            matches!(read_login(StatusCode::SERVICE_UNAVAILABLE, None, 0, &json!({"detail": "add-on"})), Login::Other(503, d) if d == "add-on")
        );
    }
}
