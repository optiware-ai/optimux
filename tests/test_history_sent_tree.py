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
    assert "/api/sent/requests?" in APP
    fetch = APP[APP.index("const fetchHistoryPage"):][:900]
    assert "historyBlock.value === 'sent'" in fetch and "fetchSentRequests" in fetch   # project, status and search
    assert "watch(() => historyPage.value.period" in APP                       # the period too
    assert "historyBlock.value === 'sent'" in APP[APP.index("const toggleHistoryBlock"):][:500]
    search = HISTORY[HISTORY.index("$t('Search')"):][:700]
    assert "historySearchHint" in search                                            # title only, or title, model and request


def _sent_block():
    return HISTORY[HISTORY.index("historyBlock === 'sent'"):]


def test_the_kept_prompts_are_a_flat_list_of_requests_with_no_chat_level():
    sent = _sent_block()
    assert "data-sent-chat" not in sent and "toggleSentChat" not in sent and "sentTurnGroups" not in sent + APP
    for marker in ("data-sent-request", "data-sent-meta", "data-sent-step", "<sent-call"):
        assert marker in sent, marker
    order = [sent.index(m) for m in ("data-sent-request", "data-sent-meta", "data-sent-step")]
    assert order == sorted(order)
    assert "keep_sent_days" in sent and "showSettingsSaved" in sent                  # the days setting stays with the block
    row = sent[sent.index("data-sent-request"):][:2200]
    assert "r.prompt" in row and "sentRoleName(" in row                              # the request, or what made a lone call
    assert "r.title" in row                                                          # the chat it came from, as a small label
    assert "openChatFromDashboard" not in sent                                       # a row opens in the list, it never leaves


def test_an_opened_request_is_meta_then_conductor_then_executor_with_no_drilling():
    sent = _sent_block()
    opened = sent[sent.index("data-sent-meta"):]
    assert opened.index("data-sent-meta") < opened.index("data-sent-step")
    step = opened[opened.index("data-sent-step"):][:1800]
    assert "sentRoleName(st.role" in step and "st.model" in step and "sentWhen(" in step + APP   # who, which model, when (seconds)
    assert ':flat="true"' in step and "st.view" in step                             # only what the call added, as rows right away
    assert "st.earlier" in step                                                      # the earlier conversation is counted, not repeated
    assert "wholePrompt" in step                                                     # and the whole prompt is one click away


def test_every_call_shows_its_date_and_time_with_seconds():
    when = APP[APP.index("const sentWhen"):][:400]
    assert "second: '2-digit'" in when and "hour: '2-digit'" in when and "month: 'short'" in when


def test_the_search_reads_requests_and_an_open_row_is_read_again_with_the_new_words():
    load = APP[APP.index("const loadSentRequest"):][:900]
    assert "/api/sent/request?session=" in load and "sentTree.detail" in load
    assert "request" in APP[APP.index("const historySearchHint"):][:300]
    assert "sentTree.detail = {}" in APP[APP.index("const fetchSentRequests"):][:1800]


def test_the_parts_of_a_whole_prompt_are_grouped_by_the_request_they_answer():
    assert "const sentPartItems" in APP
    items = APP[APP.index("const sentPartItems"):][:1800]
    assert "'user'" in items and "'assistant'" in items and "'tools'" in items
    tpl = PAGE[PAGE.index('id="sent-call-tpl"'):PAGE.index("</script>", PAGE.index('id="sent-call-tpl"'))]
    for marker in ("data-sent-group", "data-sent-part", ":aria-expanded"):
        assert marker in tpl, marker
    assert ".component('sent-call'" in APP
    assert "flat: Boolean" in APP[APP.index("const SentCall"):][:300]               # a call's new parts only, without the grouping


def test_the_peek_of_a_prompt_takes_half_the_row_and_ends_in_dots():
    tpl = PAGE[PAGE.index('id="sent-call-tpl"'):PAGE.index("</script>", PAGE.index('id="sent-call-tpl"'))]
    at = tpl.index("peek(r.p.text)")
    row = tpl[tpl.rindex("<span", 0, at):at]
    assert "max-w-[50%]" in row and "truncate" in row and "flex-1" not in row           # not the whole row
    assert "ml-auto" in tpl                                                              # the size and arrow go right
    sent = _sent_block()
    at = sent.index("r.prompt")
    line = sent[sent.rindex("<span", 0, at):at]
    assert "max-w-[50%]" in line and "truncate" in line                                 # the request in the list too


def test_the_calls_made_by_optimux_itself_are_not_called_you():
    assert r"Optimux\'s request" in APP and "commit_message" in APP[APP.index("const SENT_OWN"):][:200]


def test_one_shared_template_serves_the_dialog_and_the_history():
    assert PAGE.count("<sent-call") == 4                                  # the dialog, the meta-prompt, a step's rows, a whole prompt
    start = PAGE.index('<!-- v0.8: the parts of one model call')
    assert PAGE[:start].rstrip().endswith("</div>") and PAGE[start:].count("<script") == 2   # after the app's root, which would drop a script


def test_a_model_is_listed_once_in_the_dialog_of_one_reply():
    assert "const sentModels" in APP and "sentModels," in APP.split("return {")[-1]
    nav = MODAL[MODAL.index("<nav"):MODAL.index("</nav>")]
    assert 'v-for="m in sentModels"' in nav and "data-sent-model" in nav
    assert 'v-for="(c, i) in sentBox.calls"' not in nav                              # not one line per call in order
    grouped = APP[APP.index("const sentModels"):][:600]
    assert "c.model" in grouped


def test_a_request_row_has_a_go_to_chat_icon_right_after_its_prompt():
    row = _sent_block()[_sent_block().index("data-sent-request"):]
    row = row[:row.index("data-sent-meta")]
    assert "<button" in row and "goToSentChat(r)" in row and "@click.stop" in row     # not a button inside a button
    assert row.index("r.prompt") < row.index("goToSentChat(r)") < row.index("r.title")    # right after the dots
    assert 'v-if="r.known && r.session && !r.private"' in row                                         # a deleted chat has nowhere to go
    assert "role=\"button\"" in row or "aria-expanded" in row


def test_going_to_the_chat_leaves_a_way_back_to_the_history():
    go = APP[APP.index("const goToSentChat"):][:700]
    assert "openSession(" in go and "fromHistory.value = true" in go and "showChatPage()" in go
    back = APP[APP.index("const backToHistory"):][:400]
    assert "fromHistory.value = false" in back and "openSettings('history')" in back
    assert "fromHistory.value = false" in APP[APP.index("const newChat"):][:300]          # any other way on ends it
    assert "fromHistory.value = false" in APP[APP.index("const goHome"):][:300]
    assert "data-back-to-history" in PAGE and "backToHistory" in PAGE and "v-if=\"fromHistory\"" in PAGE
    assert "fromHistory, backToHistory, goToSentChat" in APP


def test_the_sent_header_has_a_counter_even_when_closed():
    head = PAGE[PAGE.index('data-history-block="sent"'):][:900]
    counter = head[head.index("<span class=\"text-[13px] text-gray-500\""):head.index("</button>")] if "<span class=\"text-[13px] text-gray-500\"" in head else head
    assert "historyBlock === 'sent' && sentTree.loaded" not in head          # it no longer waits for the block to be open
    assert "sentTree.count" in head
    assert "count_only=true" in APP and "fetchSentCount" in APP
    page = APP[APP.index("const fetchHistoryPage"):][:900]
    assert "fetchSentCount()" in page                                        # read when the History is, not only when opened


def test_a_shorter_keeping_period_is_confirmed_before_anything_is_deleted():
    block = _sent_block()
    assert "@change=\"changeKeepDays\"" in block and "@change=\"saveSettings\"" not in block[:block.index("data-sent-request")]
    for marker in ("data-keep-ask", "confirmKeepDays", "cancelKeepDays", "keepAsk.requests", "keepAsk.calls"):
        assert marker in block, marker
    ask = APP[APP.index("const changeKeepDays"):][:1400]
    assert "/api/sent/prune-preview?days=" in ask and "keepAsk.value = " in ask and "d.current" in ask
    assert "return;" in ask[ask.index("keepAsk.value = "):][:200]               # nothing is saved until they confirm
    assert APP.index("saveSettings()", APP.index("const confirmKeepDays")) < APP.index("/api/sent/prune'", APP.index("const confirmKeepDays"))
    assert "keep_sent_days = keepAsk.value.current" in APP[APP.index("const cancelKeepDays"):][:300]


def test_going_to_a_chat_lands_on_the_request_not_on_the_end():
    go = APP[APP.index("const goToSentChat"):][:500]
    assert "pendingJump.value" in go and "r.ids" in go
    jump = APP[APP.index("const jumpToRequest"):][:1200]
    assert "usage.sent" in jump and "data-user-msg" in jump and "pendingJump.value = null" in jump
    assert jump.index("if (at < 0) return false") < jump.index("pendingJump.value = null;\n            let from")   # kept until its own chat is shown
    assert "if (!jumpToRequest()) scrollToBottom(true)" in APP                              # the replay ends there


def test_opening_a_chat_from_the_dashboard_always_shows_the_chat_page_never_the_studio():
    show = APP[APP.index("const showChatPage"):][:600]
    assert show.index("backTo.value = 'chat'") < show.index("closeSettings()")      # the Dashboard must not return to the Studio
    assert "studio.open" in show and "closeStudio(" in show
    for name in ("const goToSentChat", "const openChatFromDashboard"):
        assert "showChatPage()" in APP[APP.index(name):][:500], name
        assert "closeSettings()" not in APP[APP.index(name):][:500], name
