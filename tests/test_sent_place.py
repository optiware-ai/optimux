"""Where "What was sent to the models" lives in the Dashboard: with History (the kept calls are
a log of what happened), not with the Execution settings."""
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")


def _tab(name, after):
    start = PAGE.index(f"settingsTab === '{name}'")
    return PAGE[start:PAGE.index("settingsTab === '", start + 40)]


def test_the_kept_prompts_card_is_in_history_not_in_execution():
    history, execution = _tab("history", None), _tab("execution", None)
    for marker in ("data-sent-browse", "keep_sent_days", "What was sent to the models"):
        assert marker in history, marker
        assert marker not in execution, marker


def test_the_card_shows_that_the_days_were_saved():
    """Execution had the Saved! tick for the whole tab; History has none, so the card carries its own."""
    card = PAGE[PAGE.index("What was sent to the models"):PAGE.index("data-sent-browse")]
    assert "showSettingsSaved" in card
