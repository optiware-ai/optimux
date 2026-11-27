//! The chat window (T3): the terminal's version of the web page's chat. It is only a
//! client: the server runs everything, as described in docs/PROTOCOL.md.

pub mod app;
pub mod editor;
pub mod link;
pub mod state;
pub mod text;
pub mod view;

use anyhow::Result;

use crate::server::Server;
use link::Cred;

/// Opens the chat window until the user quits: now in panes and tabs (src/panes),
/// one chat in one pane to begin with. Err(refusal) when the server refused the
/// sign-in on the way. `mouse`: take the mouse (clicks, wheel). Without it the terminal
/// keeps it, and a plain drag selects text.
pub async fn run(server: Server, cred: Cred, mouse: bool) -> Result<()> {
    crate::panes::run(server, cred, mouse).await
}
