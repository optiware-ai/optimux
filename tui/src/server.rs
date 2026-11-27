//! The address of an Optimux server, checked and written one way only, so it can be
//! the key of a saved sign-in.

use anyhow::{Result, bail};

/// The address Optimux listens on after a local install.
pub const DEFAULT: &str = "http://127.0.0.1:8000";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Server {
    /// `scheme://host[:port]`, lower case, no path, no trailing slash.
    pub base: String,
    host: String,
    https: bool,
}

impl Server {
    /// Reads an address like `127.0.0.1:8000`, `http://localhost:8000` or
    /// `https://optimux.example.com`. Without a scheme, this computer gets http and
    /// any other host gets https. Plain http to another computer is refused unless
    /// `allow_http`: the password and the session would travel unencrypted.
    pub fn parse(input: &str, allow_http: bool) -> Result<Server> {
        let raw = input.trim();
        if raw.is_empty() {
            bail!("The server address is empty.");
        }
        let (scheme, rest) = match raw.split_once("://") {
            Some((s, r)) => (s.to_ascii_lowercase(), r),
            None => (String::new(), raw),
        };
        let rest = rest.strip_suffix('/').unwrap_or(rest);
        if rest.contains('/') || rest.contains('?') || rest.contains('#') {
            bail!("Give only the server's address, like https://optimux.example.com (no path).");
        }
        if rest.is_empty() || rest.contains('@') || rest.chars().any(char::is_whitespace) {
            bail!("'{raw}' is not a server address.");
        }
        let authority = rest.to_ascii_lowercase();
        let host = host_of(&authority).to_string();
        let loopback = is_loopback(&host);
        let scheme = match scheme.as_str() {
            "" if loopback => "http".to_string(),
            "" => "https".to_string(),
            "http" | "https" => scheme,
            other => bail!("Unknown scheme '{other}': use http or https."),
        };
        if scheme == "http" && !loopback && !allow_http {
            bail!(
                "Refusing plain http to {host}: your password would travel unencrypted. \
                 Use https, or an SSH tunnel to 127.0.0.1 (see `optimux open` on the server). \
                 To accept the risk on a network you trust, add --allow-http."
            );
        }
        Ok(Server {
            base: format!("{scheme}://{authority}"),
            https: scheme == "https",
            host,
        })
    }

    /// The chat socket: `ws://.../ws` or `wss://.../ws`.
    pub fn ws_url(&self) -> String {
        let rest = self.base.split_once("://").map(|(_, r)| r).unwrap_or("");
        format!("{}://{rest}/ws", if self.https { "wss" } else { "ws" })
    }

    pub fn url(&self, path: &str) -> String {
        format!("{}{path}", self.base)
    }

    /// This computer: no proxy is used for it.
    pub fn is_local(&self) -> bool {
        is_loopback(&self.host)
    }
}

/// `host` from `host:port`, `[v6]:port`, `host`.
fn host_of(authority: &str) -> &str {
    if let Some(end) = authority.find(']') {
        return &authority[..=end];
    }
    authority.split(':').next().unwrap_or(authority)
}

fn is_loopback(host: &str) -> bool {
    host == "localhost" || host == "[::1]" || host.starts_with("127.")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base(s: &str) -> String {
        Server::parse(s, false).unwrap().base
    }

    #[test]
    fn this_computer_gets_http_others_https() {
        assert_eq!(base("127.0.0.1:8000"), "http://127.0.0.1:8000");
        assert_eq!(base("localhost:8001/"), "http://localhost:8001");
        assert_eq!(base("[::1]:8000"), "http://[::1]:8000");
        assert_eq!(base("Optimux.Example.com"), "https://optimux.example.com");
        assert_eq!(base("HTTPS://optimux.example.com/"), "https://optimux.example.com");
    }

    #[test]
    fn plain_http_to_another_computer_needs_a_yes() {
        assert!(Server::parse("http://10.0.0.5:8000", false).is_err());
        assert_eq!(
            Server::parse("http://10.0.0.5:8000", true).unwrap().base,
            "http://10.0.0.5:8000"
        );
    }

    #[test]
    fn paths_and_odd_input_are_refused() {
        for bad in [
            "",
            "  ",
            "https://x.com/optimux",
            "ftp://x.com",
            "user@x.com",
            "x.com?a=1",
            "a b",
        ] {
            assert!(Server::parse(bad, true).is_err(), "{bad:?} should be refused");
        }
    }

    #[test]
    fn the_socket_address() {
        assert_eq!(
            Server::parse("127.0.0.1:8000", false).unwrap().ws_url(),
            "ws://127.0.0.1:8000/ws"
        );
        assert_eq!(
            Server::parse("optimux.example.com", false).unwrap().ws_url(),
            "wss://optimux.example.com/ws"
        );
    }

    #[test]
    fn local_or_not() {
        assert!(Server::parse("127.0.0.1:8000", false).unwrap().is_local());
        assert!(!Server::parse("optimux.example.com", false).unwrap().is_local());
    }
}
