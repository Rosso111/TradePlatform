"""
Simulation Repository — zentralisiert SimulationRun-Abfragen und Bulk-Delete.
Der Bulk-Delete muss FK-Reihenfolge einhalten (simulation_trades → decision_logs).
"""
from models import (
    db, SimulationRun, SimulationPosition, SimulationTrade,
    DecisionLog, SimulationDailySnapshot,
)


def get_simulation(run_id: int) -> SimulationRun | None:
    return SimulationRun.query.get(run_id)


def get_simulations_for_user(user_id: int) -> list[SimulationRun]:
    return (SimulationRun.query
            .filter_by(user_id=user_id)
            .order_by(SimulationRun.created_at.desc())
            .all())


def get_all_simulations() -> list[SimulationRun]:
    return SimulationRun.query.order_by(SimulationRun.created_at.desc()).all()


def delete_simulation_runs(run_ids: list[int]):
    """Löscht SimulationRuns in korrekter FK-Reihenfolge.

    simulation_trades.decision_log_id referenziert decision_logs — ohne explizite
    Reihenfolge würde PostgreSQL einen FK-Fehler werfen wenn decision_logs vor
    simulation_trades gelöscht werden.
    """
    if not run_ids:
        return
    SimulationTrade.query.filter(SimulationTrade.run_id.in_(run_ids)).update(
        {'decision_log_id': None}, synchronize_session=False
    )
    SimulationPosition.query.filter(SimulationPosition.run_id.in_(run_ids)).delete(synchronize_session=False)
    SimulationTrade.query.filter(SimulationTrade.run_id.in_(run_ids)).delete(synchronize_session=False)
    DecisionLog.query.filter(DecisionLog.run_id.in_(run_ids)).delete(synchronize_session=False)
    SimulationDailySnapshot.query.filter(SimulationDailySnapshot.run_id.in_(run_ids)).delete(synchronize_session=False)
    SimulationRun.query.filter(SimulationRun.id.in_(run_ids)).delete(synchronize_session=False)
