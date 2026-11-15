"""The approval card of the chat (index.html)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_approval_card_never_hides_its_buttons():
    """On a short window, or in the Studio's narrow chat column, the card must scroll
    under its buttons instead of pushing Approve and Reject below the edge of the page."""
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    card = re.search(r'<div v-if="pendingApproval && !pendingForm[^>]*data-approval-card[^>]*>', page).group(0)
    assert "max-h-[calc(100vh-17rem)]" in card and "overflow-y-auto" in card
    buttons = re.search(r'<div data-approval-buttons class="([^"]*)"', page).group(1).split()
    assert "sticky" in buttons and "-bottom-4" in buttons         # at the card's foot, over its padding too
    assert page.index("data-approval-card") < page.index("data-approval-buttons") < page.index("sendApprovalResponse(true, false, true)")
    assert page.count("max-h-[min(24rem,38vh)]") == 2      # the command and the diff give way to the buttons


def test_the_page_stays_hidden_until_vue_has_it():
    """Without v-cloak the raw {{ ... }} template flashes on every load."""
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<div id="app" v-cloak ', page)
    assert "[v-cloak] { display: none !important; }" in (ROOT / "web" / "optimux.css").read_text(encoding="utf-8")
