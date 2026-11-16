"""The chat page: the two arrows, the time under a reply, the room under the pinned prompt, its half row."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "index.html").read_text()
APP = (ROOT / "web" / "app.js").read_text()


def _strip():
    start = PAGE.index("One line above the chat")
    return PAGE[start:PAGE.index('id="chat-container"', start)]


def test_the_top_arrow_goes_to_the_very_top_and_the_bottom_one_hops_one_prompt():
    top = _strip()
    assert "scrollChatToTop" in top and "jumpToPinned" not in top
    assert "Scroll to the top of the chat" in top
    fn = APP[APP.index("const scrollChatToTop"):][:300]
    assert "scrollTop = 0" in fn
    assert "@click=\"hopUp\"" in PAGE                                    # the bottom one keeps its job
    assert "const hopUp" in APP and "scrollChatToTop" in APP[APP.index("fromHistory, backToHistory"):][:2500] + APP


def test_a_reply_says_when_it_was_made_down_to_the_second_on_its_own_line():
    footer = PAGE[PAGE.index("data-reply-actions"):]
    line = footer[footer.index("data-reply-time"):][:600]
    assert "msg.at" in line and "stampOf(msg.at)" in line
    at = PAGE.index("data-reply-time")
    assert PAGE[PAGE.rindex("<", 0, at):at].startswith("<div")                                    # a line of its own
    stamp = APP[APP.index("const stampOf"):][:300]
    assert "second: '2-digit'" in stamp and "year: 'numeric'" in stamp
    replay = APP[APP.index("for (const [n, ev] of data.events.entries())"):][:2600]
    assert "data.times" in APP[APP.index("replayRatings = data.ratings"):][:400] or "data.times" in replay
    assert ".at = " in replay
    live = APP[APP.index("const handleMessage"):][:3500]
    assert "lastMsg.at = " in APP[APP.index("const handleMessage"):][:6000] or ".at = Date.now()" in live


def test_there_is_room_between_the_pinned_prompt_and_the_chat():
    assert "pt-3 pb-3 pr-4" in _strip()


def test_the_pinned_prompt_takes_half_the_row_then_dots():
    bar = _strip()
    text = bar[bar.index("pinnedPrompt.text") - 260:bar.index("pinnedPrompt.text")]
    assert "max-w-[50%]" in text and "truncate" in text
    assert "whitespace-pre-wrap" in text                                  # More still opens it whole


def test_each_model_in_a_replys_line_says_how_many_calls_it_made():
    fn = APP[APP.index("const callsLabel"):][:700]
    many = fn[fn.index("entries.map"):]
    assert "tr('1 call')" in many and "tr('{n} calls'" in many        # "gpt-5-nano 1 call + claude-opus-5-5 4 calls"
    assert "' calls'" not in many                                      # no English word glued on the end, untranslated
