"""HTML-Escaping für Telegram-Nachrichten (PROBE-13) + Batch-Runner (VANCE-M3)."""
from services.telegram_notifier import esc


def test_esc_escapes_html_tags():
    assert esc('<b>injection</b>') == '&lt;b&gt;injection&lt;/b&gt;'


def test_esc_keeps_quotes_readable():
    # quote=False: Anführungszeichen bleiben lesbar, nur <>& werden escapet
    assert esc('AT&T "Inc"') == 'AT&amp;T "Inc"'


def test_esc_accepts_non_strings():
    assert esc(ValueError('<kaputt>')) == '&lt;kaputt&gt;'


def test_start_batch_unknown_id(app):
    from services.scenario_runner import start_batch
    ok, error = start_batch(app, 'gibt-es-nicht-xyz')
    assert ok is False
    assert error == 'Batch nicht gefunden'
