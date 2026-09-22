"""Unit tests for WorkdayEngine, Database, and Security Hash."""

from datetime import datetime
from pathlib import Path
import pytz

from tests import _bootstrap  # noqa: F401  (isolates config/data before imports)

from tiquetaque_sync.tiquetaque.security_hash import generate_clock_in_hash
from tiquetaque_sync.engine.workday import WorkdayEngine, WorkdayStage
from tiquetaque_sync.engine.database import Database


def test_generate_clock_in_hash():
    # Test with generic values
    emp_id = "600000000000000000000000"
    full_name = "Test User"
    date_str = "21-09-2026"
    time_str = "14:51"

    h = generate_clock_in_hash(emp_id, full_name, date_str, time_str)
    assert len(h) == 64
    assert isinstance(h, str)
    # Hash must be deterministic
    assert h == generate_clock_in_hash(emp_id, full_name, date_str, time_str)


def test_workday_engine_not_started():
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")
    now = tz.localize(datetime(2026, 9, 21, 7, 30))

    status = engine.calculate_status([], current_dt=now)
    assert status.stage == WorkdayStage.NOT_STARTED
    assert status.worked_seconds == 0
    assert status.progress_percentage == 0.0


def test_workday_engine_morning():
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")
    now = tz.localize(datetime(2026, 9, 21, 10, 0))

    status = engine.calculate_status(["08:00"], current_dt=now)
    assert status.stage == WorkdayStage.WORKING_MORNING
    assert status.worked_seconds == 2 * 3600  # 08:00 to 10:00 = 2 hours
    assert status.estimated_departure == "17:00"  # 08:00 + 8h + 1h lunch
    assert status.progress_percentage == 25.0


def test_workday_engine_lunch():
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")
    now = tz.localize(datetime(2026, 9, 21, 12, 30))

    status = engine.calculate_status(["08:00", "12:00"], current_dt=now)
    assert status.stage == WorkdayStage.LUNCH_BREAK
    assert status.worked_seconds == 4 * 3600  # 08:00 to 12:00 = 4 hours
    assert status.lunch_duration_seconds == 30 * 60  # 30 min into lunch
    assert status.next_alert_seconds == 30 * 60  # 30 min left of 1h lunch


def test_workday_engine_afternoon_recalculation():
    # If employee took only 45 min lunch (12:00 to 12:45)
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")
    now = tz.localize(datetime(2026, 9, 21, 13, 45))

    status = engine.calculate_status(["08:00", "12:00", "12:45"], current_dt=now)
    assert status.stage == WorkdayStage.WORKING_AFTERNOON
    # 4h morning + 1h afternoon = 5h
    assert status.worked_seconds == 5 * 3600
    # Remaining 3h after 12:45 -> 15:45? Wait: 4h done, 4h needed. 12:45 + 4h = 16:45!
    assert status.estimated_departure == "16:45"


def test_workday_engine_completed():
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")
    now = tz.localize(datetime(2026, 9, 21, 17, 10))

    status = engine.calculate_status(["08:00", "12:00", "13:00", "17:05"], current_dt=now)
    assert status.stage == WorkdayStage.COMPLETED
    # 4h morning + 4h05min afternoon = 8h05min
    assert status.worked_seconds == (8 * 3600) + (5 * 60)
    assert status.balance_seconds == 5 * 60  # +5 min
    assert status.balance_formatted == "+00h05min"


def test_database_deduplication(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    date_str = "21/09/2026"

    # Test entry insertion
    assert db.record_entry(date_str, "08:00", source="web") is True
    assert db.record_entry(date_str, "08:00", source="web") is False  # Duplicate

    # Test alert deduplication
    assert db.is_alert_dispatched(date_str, "lunch_warning") is False
    db.mark_alert_dispatched(date_str, "lunch_warning", "Aviso de almoço")
    assert db.is_alert_dispatched(date_str, "lunch_warning") is True


def test_workday_engine_lunch_final_warning():
    engine = WorkdayEngine(target_hours=8.0, lunch_advance_warning=10, lunch_final_warning=1, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")

    # 12:00 lunch start. At 12:50 (10 min left) -> standard advance warning
    t_10min = tz.localize(datetime(2026, 9, 21, 12, 50))
    status_10 = engine.calculate_status(["08:00", "12:00"], current_dt=t_10min)
    triggers_10 = engine.evaluate_alert_triggers(status_10, current_dt=t_10min)
    keys_10 = [t["key"] for t in triggers_10]
    assert "lunch_warning" in keys_10
    assert "lunch_warning_final" not in keys_10

    # At 12:59 (1 min left) -> final warning
    t_1min = tz.localize(datetime(2026, 9, 21, 12, 59, 10))
    status_1 = engine.calculate_status(["08:00", "12:00"], current_dt=t_1min)
    triggers_1 = engine.evaluate_alert_triggers(status_1, current_dt=t_1min)
    keys_1 = [t["key"] for t in triggers_1]
    assert "lunch_warning_final" in keys_1


def test_workday_engine_afternoon_final_warning():
    # 08:00 to 12:00 (4h) + 1h lunch (12:00 to 13:00) -> ends at 17:00
    engine = WorkdayEngine(target_hours=8.0, end_work_advance_warning=15, end_work_final_warning=1, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")

    # At 16:45 (15 min left) -> advance warning
    t_15min = tz.localize(datetime(2026, 9, 21, 16, 45))
    status_15 = engine.calculate_status(["08:00", "12:00", "13:00"], current_dt=t_15min)
    triggers_15 = engine.evaluate_alert_triggers(status_15, current_dt=t_15min)
    keys_15 = [t["key"] for t in triggers_15]
    assert "work_end_warning" in keys_15
    assert "work_end_warning_final" not in keys_15

    # At 16:59 (1 min left) -> final warning
    t_1min = tz.localize(datetime(2026, 9, 21, 16, 59, 15))
    status_1 = engine.calculate_status(["08:00", "12:00", "13:00"], current_dt=t_1min)
    triggers_1 = engine.evaluate_alert_triggers(status_1, current_dt=t_1min)
    keys_1 = [t["key"] for t in triggers_1]
    assert "work_end_warning_final" in keys_1


def test_workday_engine_clt_6h_continuous():
    # 08:00 start, no pause. 6h continuous limit is at 14:00
    engine = WorkdayEngine(
        continuous_work_limit_hours=6.0,
        continuous_work_advance_warning=10,
        continuous_work_final_warning=1,
        timezone_name="America/Sao_Paulo"
    )
    tz = pytz.timezone("America/Sao_Paulo")

    # At 13:50 (5h50m worked continuous, 10 min left) -> CLT advance warning
    t_10min = tz.localize(datetime(2026, 9, 21, 13, 50, 5))
    status_10 = engine.calculate_status(["08:00"], current_dt=t_10min)
    triggers_10 = engine.evaluate_alert_triggers(status_10, current_dt=t_10min)
    keys_10 = [t["key"] for t in triggers_10]
    assert "clt_6h_warning_shift_1" in keys_10
    assert "clt_6h_final_shift_1" not in keys_10

    # At 13:59 (5h59m worked continuous, 1 min left) -> CLT final critical warning
    t_1min = tz.localize(datetime(2026, 9, 21, 13, 59, 10))
    status_1 = engine.calculate_status(["08:00"], current_dt=t_1min)
    triggers_1 = engine.evaluate_alert_triggers(status_1, current_dt=t_1min)
    keys_1 = [t["key"] for t in triggers_1]
    assert "clt_6h_final_shift_1" in keys_1

    # At 14:05 (6h05m worked continuous, exceeded) -> CLT exceeded warning
    t_exceeded = tz.localize(datetime(2026, 9, 21, 14, 5))
    status_exc = engine.calculate_status(["08:00"], current_dt=t_exceeded)
    triggers_exc = engine.evaluate_alert_triggers(status_exc, current_dt=t_exceeded)
    keys_exc = [t["key"] for t in triggers_exc]
    assert "clt_6h_exceeded_shift_1" in keys_exc



def test_workday_engine_lunch_2h_warning():
    engine = WorkdayEngine(target_hours=8.0, lunch_advance_warning=10, lunch_final_warning=1, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")

    # 12:00 lunch start.
    # At 13:50 (1h50m elapsed, exactly 10 min left to 2h) -> 2h advance warning
    t_10min = tz.localize(datetime(2026, 9, 21, 13, 50))
    status_10 = engine.calculate_status(["08:00", "12:00"], current_dt=t_10min)
    triggers_10 = engine.evaluate_alert_triggers(status_10, current_dt=t_10min)
    keys_10 = [t["key"] for t in triggers_10]
    assert "lunch_2h_warning" in keys_10
    assert "lunch_2h_warning_final" not in keys_10

    # At 13:59:15 (1h59m15s elapsed, 45s left to 2h) -> 2h final warning
    t_1min = tz.localize(datetime(2026, 9, 21, 13, 59, 15))
    status_1 = engine.calculate_status(["08:00", "12:00"], current_dt=t_1min)
    triggers_1 = engine.evaluate_alert_triggers(status_1, current_dt=t_1min)
    keys_1 = [t["key"] for t in triggers_1]
    assert "lunch_2h_warning_final" in keys_1

    # At 14:02:00 (2h02m elapsed, exceeded 2h limit) -> 2h exceeded warning
    t_exc = tz.localize(datetime(2026, 9, 21, 14, 2))
    status_exc = engine.calculate_status(["08:00", "12:00"], current_dt=t_exc)
    triggers_exc = engine.evaluate_alert_triggers(status_exc, current_dt=t_exc)
    keys_exc = [t["key"] for t in triggers_exc]
    assert "lunch_2h_warning_final_exceeded" in keys_exc


def test_workday_engine_multi_break_flexible():
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")

    # 4 punches: 08:00-10:00 (2h) and 10:30-12:00 (1.5h) -> 3.5h worked. Total < 8h -> active break 2
    now_break = tz.localize(datetime(2026, 9, 21, 12, 15))
    status_break = engine.calculate_status(["08:00", "10:00", "10:30", "12:00"], current_dt=now_break)
    assert status_break.stage == WorkdayStage.LUNCH_BREAK
    assert status_break.worked_seconds == int(3.5 * 3600)
    assert status_break.lunch_duration_seconds == 15 * 60
    assert status_break.remaining_work_seconds == int(4.5 * 3600)
    assert status_break.remaining_work_formatted == "04h30min"
    # Planned return 12:00 + 1h = 13:00 + 4.5h work -> 17:30
    assert status_break.estimated_departure == "17:30"

    # 5 punches: clock back in at 13:00, evaluating at 15:00
    now_shift = tz.localize(datetime(2026, 9, 21, 15, 0))
    status_shift = engine.calculate_status(["08:00", "10:00", "10:30", "12:00", "13:00"], current_dt=now_shift)
    assert status_shift.stage == WorkdayStage.WORKING_AFTERNOON
    # 3.5h + 2h = 5.5h
    assert status_shift.worked_seconds == int(5.5 * 3600)
    assert status_shift.estimated_departure == "17:30"

    # 6 punches: clock out at 17:30 reaching 8h
    now_done = tz.localize(datetime(2026, 9, 21, 17, 35))
    status_done = engine.calculate_status(["08:00", "10:00", "10:30", "12:00", "13:00", "17:30"], current_dt=now_done)
    assert status_done.stage == WorkdayStage.COMPLETED
    assert status_done.worked_seconds == 8 * 3600
    assert status_done.balance_seconds == 0
    assert status_done.balance_formatted == "+00h00min"


def test_workday_engine_additional_exit_punch_summary():
    engine = WorkdayEngine(target_hours=8.0, timezone_name="America/Sao_Paulo")
    tz = pytz.timezone("America/Sao_Paulo")

    # 4 punches: punch #4 at 12:00 (< 8h worked)
    now_4 = tz.localize(datetime(2026, 9, 21, 12, 0))
    status_4 = engine.calculate_status(["08:00", "10:00", "10:30", "12:00"], current_dt=now_4)
    triggers_4 = engine.evaluate_alert_triggers(status_4, current_dt=now_4)
    punch_4_trigger = next(t for t in triggers_4 if t["key"] == "entry_3_12:00")
    assert "Saída / Pausa" in punch_4_trigger["title"]
    assert "03h30min" in punch_4_trigger["message"]
    assert "04h30min" in punch_4_trigger["message"]
    assert "13:00" in punch_4_trigger["message"]

    # 6 punches: punch #6 at 17:30 (>= 8h worked)
    now_6 = tz.localize(datetime(2026, 9, 21, 17, 30))
    status_6 = engine.calculate_status(["08:00", "10:00", "10:30", "12:00", "13:00", "17:30"], current_dt=now_6)
    triggers_6 = engine.evaluate_alert_triggers(status_6, current_dt=now_6)
    punch_6_trigger = next(t for t in triggers_6 if t["key"] == "entry_5_17:30")
    assert "Saída" in punch_6_trigger["title"]
    assert "08h00min" in punch_6_trigger["message"]
    assert "Meta diária de 8h cumprida" in punch_6_trigger["message"]

    # 7 punches: returning at 18:00 after completing 8h (overtime / on-call)
    now_7 = tz.localize(datetime(2026, 9, 21, 18, 0))
    status_7 = engine.calculate_status(["08:00", "10:00", "10:30", "12:00", "13:00", "17:30", "18:00"], current_dt=now_7)
    assert status_7.estimated_departure is None
    triggers_7 = engine.evaluate_alert_triggers(status_7, current_dt=now_7)
    punch_7_trigger = next(t for t in triggers_7 if t["key"] == "entry_6_18:00")
    assert "Retorno" in punch_7_trigger["title"]
    assert "Horário previsto para encerramento da jornada" not in punch_7_trigger["message"]



# ------------------------------------------------------------------------------
# unittest wrappers so the plain-function tests above are picked up by discovery
# ------------------------------------------------------------------------------
import tempfile
import unittest


class TestTiqueTaqueEngine(unittest.TestCase):
    def test_hash(self):
        test_generate_clock_in_hash()

    def test_not_started(self):
        test_workday_engine_not_started()

    def test_morning(self):
        test_workday_engine_morning()

    def test_lunch(self):
        test_workday_engine_lunch()

    def test_lunch_final_warning(self):
        test_workday_engine_lunch_final_warning()

    def test_lunch_2h_warning(self):
        test_workday_engine_lunch_2h_warning()

    def test_afternoon(self):
        test_workday_engine_afternoon_recalculation()

    def test_afternoon_final_warning(self):
        test_workday_engine_afternoon_final_warning()

    def test_clt_6h_continuous(self):
        test_workday_engine_clt_6h_continuous()

    def test_multi_break_flexible(self):
        test_workday_engine_multi_break_flexible()

    def test_additional_exit_punch_summary(self):
        test_workday_engine_additional_exit_punch_summary()

    def test_completed(self):
        test_workday_engine_completed()

    def test_database(self):
        with tempfile.TemporaryDirectory() as td:
            test_database_deduplication(Path(td))


if __name__ == "__main__":
    unittest.main()

