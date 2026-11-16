"""Dashboard -> History: two blocks, Chats and What was sent to the models, closed at first,
one open at a time so the filters above always apply to the open one. The kept prompts are a
tree: chat (+) -> the models it used (each once) -> its calls, each opened inline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web/app.js").read_text(encoding="utf-8")
START = PAGE.index("settingsTab === 'history'")
HISTORY = PAGE[START:PAGE.index("settingsTab === 'skills'", START)]
MODAL = PAGE[PAGE.index("data-sent-box"):PAGE.index("syncBox.open")]


def test_both_blocks_start_closed_and_open_one_at_a_time():
    assert "const historyBlock = ref('')" in APP                       # first visit: both closed
    for name in ("chats", "sent"):
        at = HISTORY.index(f'data-history-block="{name}"')
        head = HISTORY[HISTORY.rindex("<button", 0, at):at]
        assert ":aria-expanded=" in head and "toggleHistoryBlock" in head
    assert "historyBlock === 'chats'" in HISTORY and "historyBlock === 'sent'" in HISTORY
    toggle = APP[APP.index("const toggleHistoryBlock"):][:400]
    assert "historyBlock.value === name ? '' : name" in toggle          # opening one closes the other


def test_the_table_of_chats_is_only_shown_while_its_block_is_open():
    table = HISTORY.index("<table")
    assert HISTORY.rindex("historyBlock === 'chats'", 0, table) > HISTORY.rindex('data-history-block="chats"', 0, table)


def test_there_is_no_browse_button_any_more():
    assert "data-sent-browse" not in PAGE and "browseSent" not in PAGE + APP


def test_the_filters_at_the_top_reach_the_open_block():
    assert "/api/sent/chats?" in APP
    fetch = APP[APP.index("const fetchHistoryPage"):][:900]
    assert "historyBlock.value === 'sent'" in fetch and "fetchSentChats" in fetch   # project, status and search
    assert "watch(() => historyPage.value.period" in APP                       # the period too
    assert "historyBlock.value === 'sent'" in APP[APP.index("const toggleHistoryBlock"):][:500]
    search = HISTORY[HISTORY.index("$t('Search')"):][:700]
    assert "historySearchHint" in search                                            # title only, or title and model


def test_the_kept_prompts_are_a_tree_of_chat_then_models_then_calls():
    sent = HISTORY[HISTORY.index("historyBlock === 'sent'"):]
    chat = sent[sent.index("data-sent-chat"):]
    assert ":aria-expanded=" in chat[:600] and "toggleSentChat" in chat[:600]
    assert "data-sent-model" in sent and "data-sent-call" in sent and "data-sent-part" in sent
    assert sent.index("data-sent-chat") < sent.index("data-sent-model") < sent.index("data-sent-call") < sent.index("data-sent-part")
    assert "sentModelsOf(" in sent                                                  # each model once, its calls under it
    assert "keep_sent_days" in sent and "showSettingsSaved" in sent                  # the days setting stays with the block


def test_a_chat_that_is_gone_and_calls_outside_any_chat_are_named():
    sent = HISTORY[HISTORY.index("historyBlock === 'sent'"):]
    assert "Outside a chat" in sent and "Deleted chat" in sent


def test_a_model_is_listed_once_in_the_dialog_of_one_reply():
    assert "const sentModels" in APP and "sentModels," in APP.split("return {")[-1]
    nav = MODAL[MODAL.index("<nav"):MODAL.index("</nav>")]
    assert 'v-for="m in sentModels"' in nav and "data-sent-model" in nav
    assert 'v-for="(c, i) in sentBox.calls"' not in nav                              # not one line per call in order
    grouped = APP[APP.index("const sentModels"):][:600]
    assert "c.model" in grouped
