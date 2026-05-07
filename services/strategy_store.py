"""
Strategy Store — DB-backed
Implements: S-01, S-02, S-05, API-15, API-16, API-17, DB-06
"""
from flask import has_app_context
from models import db, Strategy


def list_strategies() -> dict:
    """Implements: API-15, S-01, S-02"""
    strategies = Strategy.query.order_by(Strategy.created_at).all()
    approved = [s.slug for s in strategies if s.is_approved_live and s.slug]
    return {
        'strategies': [s.to_dict() for s in strategies],
        'active_strategy': None,           # deprecated — per Portfolio via Portfolio.strategy_id
        'approved_live_strategies': approved,
    }


def get_strategy(strategy_id=None) -> dict | None:
    """Implements: S-01, S-02 — Gibt Strategy-Dict zurück das replay_engine erwartet."""
    if not strategy_id:
        s = Strategy.query.filter_by(is_system=True).order_by(Strategy.id).first()
    else:
        s = Strategy.query.filter_by(slug=strategy_id).first()
        if not s:
            # Fallback: nach name suchen
            s = Strategy.query.filter(Strategy.name == strategy_id).first()
    if not s:
        return None
    return {
        'id': s.slug or str(s.id),
        'name': s.name,
        'description': s.description,
        'mode': s.mode,
        'params': s.params or {},
        'is_system': s.is_system,
        'is_approved_live': s.is_approved_live,
    }


def upsert_strategy(payload: dict) -> dict:
    """Implements: S-01, S-05, API-16, API-17"""
    slug = payload.get('id') or payload.get('slug')
    if not slug:
        raise ValueError('Strategie-ID (slug) fehlt')

    s = Strategy.query.filter_by(slug=slug).first()
    if s:
        if s.is_system:
            raise PermissionError('System-Strategien können nicht überschrieben werden')  # S-05
        s.name = payload.get('name', s.name)
        s.description = payload.get('description', s.description)
        s.mode = payload.get('mode', s.mode)
        s.params = payload.get('params', s.params)
    else:
        s = Strategy(
            slug=slug,
            name=payload.get('name', slug),
            description=payload.get('description', ''),
            is_system=False,
            mode=payload.get('mode', 'score'),
            params=payload.get('params', {}),
        )
        db.session.add(s)

    db.session.commit()
    return get_strategy(slug)


def set_active_strategy(strategy_id: str):
    """Deprecated: active_strategy ist per Portfolio. Implements: API-15"""
    # Prüfen ob Strategie existiert (Validierung bleibt erhalten)
    s = Strategy.query.filter_by(slug=strategy_id).first()
    if not s:
        raise ValueError(f'Strategie {strategy_id} nicht gefunden')
    if not s.is_approved_live:
        raise ValueError(f'Strategie {strategy_id} ist nicht für Live freigegeben')
    # Rückgabe-Kompatibilität zur alten JSON-Struktur
    return list_strategies()


def approve_strategy_for_live(strategy_id: str) -> dict:
    """Implements: S-02, API-17"""
    s = Strategy.query.filter_by(slug=strategy_id).first()
    if not s:
        raise ValueError(f'Strategie {strategy_id} nicht gefunden')
    s.is_approved_live = True
    db.session.commit()
    return list_strategies()
