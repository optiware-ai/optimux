"""The Studio's top bar and the chat column beside it never break when they get narrow."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "optimux.css").read_text(encoding="utf-8")


def test_the_bar_folds_its_labels_instead_of_wrapping():
    bar = re.search(r'<div data-studio-bar class="([^"]*)"', PAGE).group(1).split()
    assert "cm-studio-bar" in bar and "flex-wrap" not in bar
    # the bar measures itself; below a width the words go and the icons stay
    assert re.search(r"\.cm-studio-bar \{[^}]*container-type: inline-size", CSS)
    assert re.search(r"@container studio-bar \(max-width: \d+px\) \{\s*\.cm-studio-bar \.cm-bar-label", CSS)
    assert re.search(r"\.cm-studio-bar > \* \{[^}]*flex-shrink: 0[^}]*white-space: nowrap", CSS)
    start = PAGE.index("data-studio-bar"); block = PAGE[start:PAGE.index("data-files-tree", start)]
    for word in ("Refresh", "Dashboard", "Back to chat"):       # each has an icon and a label that can fold
        assert re.search(r'<span class="cm-bar-label">\{\{ (?:\' \' \+ )?\$t\(\'' + word + r"'\)", block), word
    assert block.count('aria-label="') >= 3 or ":aria-label=" in block    # still named when only the icon shows


def test_the_buttons_under_a_reply_wrap_inside_the_bubble():
    row = re.search(r'<span data-reply-actions class="([^"]*)"', PAGE).group(1).split()
    assert "flex-wrap" in row and "justify-end" in row
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    low = int(re.search(r"dock: \(\) => \[(\d+),", js).group(1))
    assert low >= 380                         # the chat column cannot be dragged thinner than it can be read


def test_send_stays_inside_the_message_box():
    start = PAGE.index("data-composer-row"); block = PAGE[start:start + 6000]
    left = re.search(r'<div class="([^"]*)">\s*<span class="relative flex items-center min-w-0">', block).group(1).split()
    assert "flex-wrap" in left and "min-w-0" in left         # model, New chat and Plan wrap
    assert block.count('class="flex-shrink-0 bg-') == 2      # Stop and Send keep their size
