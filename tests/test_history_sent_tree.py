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


def _sent_block():
    return HISTORY[HISTORY.index("historyBlock === 'sent'"):]


def test_the_kept_prompts_are_a_tree_chat_then_request_then_conductor_and_executor_then_calls():
    sent = _sent_block()
    for marker in ("data-sent-chat", "data-sent-turn", "data-sent-model", "data-sent-call", "<sent-call"):
        assert marker in sent, marker
    order = [sent.index(m) for m in ("data-sent-chat", "data-sent-turn", "data-sent-model", "data-sent-call", "<sent-call")]
    assert order == sorted(order)
    assert "keep_sent_days" in sent and "showSettingsSaved" in sent                  # the days setting stays with the block
    row = sent[sent.index("data-sent-turn"):][:1400]
    assert "t.prompt" in row and "sentRoleName(" in row                              # the request, or what made the call
    group = sent[sent.index("data-sent-model"):][:900]
    assert "sentRoleName(g.role" in group and "g.model" in group                     # "Conductor · model", "Executor · SIMPLE · model"
    assert "k.when" in sent[sent.index("data-sent-call"):][:1500]


def test_the_name_of_a_chat_opens_it_in_the_list_and_does_not_leave_the_page():
    sent = _sent_block()
    chat = sent[sent.index("data-sent-chat"):sent.index("data-sent-turn")]
    assert "openChatFromDashboard" not in chat
    assert chat.count("toggleSentChat(c)") >= 2                                       # the + and the name


def test_every_call_shows_its_date_and_time_with_seconds():
    when = APP[APP.index("const sentWhen"):][:400]
    assert "second: '2-digit'" in when and "hour: '2-digit'" in when and "month: 'short'" in when
    assert "sentWhen(" in APP[APP.index("const loadSentTurns"):][:900]


def test_the_search_reaches_the_requests_and_the_open_chat_gets_it_too():
    assert "'/api/sent/turns?session='" in APP.replace('"', "'") or "/api/sent/turns?" in APP
    load = APP[APP.index("const loadSentTurns"):][:700]
    assert "sentQuery()" in load
    assert "request" in APP[APP.index("const historySearchHint"):][:300]
    assert "sentTree.turns" in APP[APP.index("const fetchSentChats"):][:1500]          # an open chat is read again with the new words


def test_the_parts_of_a_call_are_grouped_by_the_request_they_answer():
    assert "const sentPartItems" in APP
    items = APP[APP.index("const sentPartItems"):][:1800]
    assert "'user'" in items and "'assistant'" in items and "'tools'" in items
    tpl = PAGE[PAGE.index('id="sent-call-tpl"'):PAGE.index("</script>", PAGE.index('id="sent-call-tpl"'))]
    for marker in ("data-sent-group", "data-sent-part", ":aria-expanded"):
        assert marker in tpl, marker
    assert ".component('sent-call'" in APP


def test_the_peek_of_a_part_takes_half_the_row_and_ends_in_dots():
    tpl = PAGE[PAGE.index('id="sent-call-tpl"'):PAGE.index("</script>", PAGE.index('id="sent-call-tpl"'))]
    at = tpl.index("peek(r.p.text)")
    row = tpl[tpl.rindex("<span", 0, at):at]
    assert "max-w-[50%]" in row and "truncate" in row and "flex-1" not in row           # not the whole row
    assert "ml-auto" in tpl                                                              # the size and arrow go right


def test_the_calls_made_by_optimux_itself_are_not_called_you():
    assert r"Optimux\'s request" in APP and "commit_message" in APP[APP.index("const SENT_OWN"):][:200]


def test_one_shared_template_serves_the_dialog_and_the_history():
    assert PAGE.count("<sent-call") == 2
    start = PAGE.index('<!-- v0.8: the parts of one model call')
    assert PAGE[:start].rstrip().endswith("</div>") and PAGE[start:].count("<script") == 2   # after the app's root, which would drop a script


def test_a_model_is_listed_once_in_the_dialog_of_one_reply():
    assert "const sentModels" in APP and "sentModels," in APP.split("return {")[-1]
    nav = MODAL[MODAL.index("<nav"):MODAL.index("</nav>")]
    assert 'v-for="m in sentModels"' in nav and "data-sent-model" in nav
    assert 'v-for="(c, i) in sentBox.calls"' not in nav                              # not one line per call in order
    grouped = APP[APP.index("const sentModels"):][:600]
    assert "c.model" in grouped
