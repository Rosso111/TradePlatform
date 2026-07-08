"""Positions-Abgleich DB ↔ IBKR (compare_positions ist rein, ohne IBKR-Verbindung testbar)."""
from services.position_reconciler import compare_positions


def _ibkr(*rows):
    return [{'symbol': s, 'qty': q} for s, q in rows]


def test_identical_positions_no_diffs():
    diffs = compare_positions([('AAPL', 10), ('BAS.DE', 50)], _ibkr(('AAPL', 10), ('BAS', 50)))
    assert diffs == []


def test_short_at_ibkr_is_flagged():
    diffs = compare_positions([('TNE.AX', 1226)], _ibkr(('TNE', -17164)))
    assert any('SHORT' in d for d in diffs)


def test_only_in_ibkr_flagged():
    diffs = compare_positions([], _ibkr(('KLAC', 108)))
    assert diffs == ['KLAC: 108 Stk nur bei IBKR (fehlt in DB)']


def test_only_in_db_flagged():
    diffs = compare_positions([('ROP.SW', 56)], _ibkr())
    assert diffs == ['ROP: 56 Stk nur in DB (fehlt bei IBKR)']


def test_qty_mismatch_flagged():
    diffs = compare_positions([('AI.PA', 120)], _ibkr(('AI', 141)))
    assert diffs == ['AI: IBKR 141 vs DB 120 Stk']


def test_alias_hona_matches_hon():
    diffs = compare_positions([('HON', 8)], _ibkr(('HONA', 8)))
    assert diffs == []


def test_multiple_ibkr_lots_are_summed():
    diffs = compare_positions([('AAPL', 30)], _ibkr(('AAPL', 10), ('AAPL', 20)))
    assert diffs == []
