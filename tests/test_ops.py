"""Phase 0: backups, health monitor and admin alerts."""

import asyncio
import os
import shutil

import pytest

from cs2tracker.app.backup import BackupError, backup_database, backups, latest_age
from cs2tracker.app.db import Store
from cs2tracker.app.monitor import AdminAlerts, HealthMonitor
from cs2tracker.app.settings import AppSettings
from cs2tracker.app.telegram import TelegramBot


def make_db(tmp_path):
    store = Store(tmp_path / "live.db", "UAH")
    store.add_lot(1, "Prisma 2 Case", 11, 6521)
    store.touch_user({"id": 1, "first_name": "Owner", "language_code": "ru"}, "app")
    return store


# -- backups ----------------------------------------------------------------------

def test_backup_is_a_complete_checked_copy_and_restores(tmp_path):
    store = make_db(tmp_path)
    store.add_lot(1, "Kilowatt Case", 7, 3782)  # still open and in WAL mode while copying
    path = backup_database(tmp_path / "live.db", tmp_path / "backups", now=1_700_000_000)
    assert path.name == "cs2tracker-20231114-221320.db"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert not list((tmp_path / "backups").glob("*.partial"))

    # Restore drill: a copy of the backup opens as a normal database.
    restored = tmp_path / "restored.db"
    shutil.copy(path, restored)
    again = Store(restored, "UAH")
    assert [(h.hash_name, h.qty) for h in again.holdings(1)] == [("Prisma 2 Case", 11), ("Kilowatt Case", 7)]
    assert again.user_language(1) == "ru"


def test_backups_are_pruned_to_keep(tmp_path):
    make_db(tmp_path)
    for day in range(5):
        backup_database(tmp_path / "live.db", tmp_path / "b", keep=3, now=1_700_000_000 + day * 86400)
    names = [p.name for p in backups(tmp_path / "b")]
    assert len(names) == 3 and names[0].startswith("cs2tracker-20231116")


def test_backup_failure_leaves_nothing_behind(tmp_path):
    with pytest.raises(BackupError):
        backup_database(tmp_path / "missing.db", tmp_path / "b")
    assert backups(tmp_path / "b") == [] and not list((tmp_path / "b").iterdir())


def test_latest_age(tmp_path):
    make_db(tmp_path)
    assert latest_age(tmp_path / "b") is None
    backup_database(tmp_path / "live.db", tmp_path / "b", now=1_700_000_000)
    assert latest_age(tmp_path / "b", now=1_700_003_600) == 3600


# -- alerts -----------------------------------------------------------------------

class Recorder:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def __call__(self, key, values):
        self.sent.append((key, values))
        if self.fail:
            raise RuntimeError("telegram down")


def test_problem_is_sent_once_an_hour_and_recovery_once():
    now = [0.0]
    rec = Recorder()
    alerts = AdminAlerts(rec, clock=lambda: now[0])

    async def run():
        await alerts.problem("steam_failing", minutes=30)
        now[0] += 600
        await alerts.problem("steam_failing", minutes=40)  # repeat within the hour: dropped
        now[0] += 3000
        await alerts.problem("steam_failing", minutes=90)  # an hour later: reminded
        await alerts.resolved("steam_failing")
        await alerts.resolved("steam_failing")  # nothing active any more
        await alerts.resolved("backup_failed")  # never raised
    asyncio.run(run())
    assert [k for k, _ in rec.sent] == ["steam_failing", "steam_failing", "steam_failing_ok"]


def test_events_are_rate_limited_and_delivery_errors_swallowed():
    now = [0.0]
    rec = Recorder(fail=True)
    alerts = AdminAlerts(rec, clock=lambda: now[0])

    async def run():
        await alerts.event("job_crashed", job="Telegram bot", error="x")
        await alerts.event("job_crashed", job="Telegram bot", error="x")
        now[0] += 3601
        await alerts.event("job_crashed", job="Telegram bot", error="x")
    asyncio.run(run())
    assert len(rec.sent) == 2


# -- health monitor ---------------------------------------------------------------

class FakePrices:
    def __init__(self, store):
        self.store = store
        self.last_pass = None
        self.last_attempt = None
        self.last_success = None
        self.last_error = None


def monitor(tmp_path, clock):
    store = make_db(tmp_path)
    prices = FakePrices(store)
    rec = Recorder()
    mon = HealthMonitor(prices, AdminAlerts(rec, clock=clock), db_path=tmp_path / "live.db",
                        backup_dir=tmp_path / "b", clock=clock)
    return mon, prices, rec


def test_monitor_reports_a_stuck_refresh_and_its_recovery(tmp_path):
    now = [10_000.0]
    mon, prices, rec = monitor(tmp_path, lambda: now[0])
    prices.last_pass = now[0]
    asyncio.run(mon.check())
    now[0] += 3700
    asyncio.run(mon.check())
    prices.last_pass = now[0]
    asyncio.run(mon.check())
    assert [k for k, _ in rec.sent] == ["refresh_stuck", "refresh_stuck_ok"]
    assert rec.sent[0][1] == {"minutes": 61}


def test_monitor_reports_steam_failing_for_half_an_hour(tmp_path):
    now = [10_000.0]
    mon, prices, rec = monitor(tmp_path, lambda: now[0])
    prices.last_pass = prices.last_success = now[0]
    for _ in range(7):  # 35 minutes of failures, passes still finishing
        now[0] += 300
        prices.last_pass = prices.last_attempt = now[0]
        prices.last_error = "order book: rate limited (429)"
        asyncio.run(mon.check())
    assert [k for k, _ in rec.sent] == ["steam_failing"]
    assert "429" in rec.sent[0][1]["error"]
    prices.last_success = now[0]
    asyncio.run(mon.check())
    assert rec.sent[-1][0] == "steam_failing_ok"


def test_short_steam_hiccups_are_not_reported(tmp_path):
    now = [10_000.0]
    mon, prices, rec = monitor(tmp_path, lambda: now[0])
    prices.last_pass = prices.last_success = now[0]
    now[0] += 600
    prices.last_pass = prices.last_attempt = now[0]
    asyncio.run(mon.check())
    assert rec.sent == []


def test_monitor_backs_up_daily_and_alerts_on_failure(tmp_path):
    now = [1_700_000_000.0]
    mon, prices, rec = monitor(tmp_path, lambda: now[0])
    prices.last_pass = now[0]
    asyncio.run(mon.check())
    assert len(backups(tmp_path / "b")) == 1
    now[0] += 3600
    prices.last_pass = now[0]
    asyncio.run(mon.check())
    assert len(backups(tmp_path / "b")) == 1  # not due yet

    mon.db_path = tmp_path / "gone.db"
    now[0] += 86400
    prices.last_pass = now[0]
    asyncio.run(mon.check())
    assert rec.sent[-1][0] == "backup_failed"


# -- delivery -----------------------------------------------------------------------

class RecordingBot(TelegramBot):
    def __init__(self, settings, store):
        super().__init__(settings, session=None, store=store)
        self.calls = []

    async def call(self, method, **params):
        self.calls.append((method, params))


def test_admin_alerts_use_the_admins_language(tmp_path):
    store = make_db(tmp_path)  # user 1 speaks ru
    settings = AppSettings(bot_token="1:x", public_url="https://example.com/", admins=frozenset({1, 2}))
    bot = RecordingBot(settings, store)
    asyncio.run(bot.notify_admins("steam_failing", {"minutes": 31, "error": "429"}))
    texts = {p["chat_id"]: p["text"] for _, p in bot.calls}
    assert texts[1].startswith("⚠️ Steam отвечает ошибками уже 31 мин")
    assert texts[2].startswith("⚠️ Steam has been failing for 31 min")  # unknown language -> en
    asyncio.run(bot.notify_admins("no_such_key", {}))
    assert len(bot.calls) == 2


def test_supervise_reports_a_crashing_job(monkeypatch):
    from cs2tracker.app import __main__ as main
    rec = Recorder()
    alerts = AdminAlerts(rec)

    async def boom():
        raise ValueError("bad update")

    async def run():
        task = asyncio.ensure_future(main.supervise("Telegram bot", boom, alerts))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(run())
    assert rec.sent == [("job_crashed", {"job": "Telegram bot", "error": "ValueError: bad update"})]


def test_backup_settings(tmp_path):
    from cs2tracker.app.settings import load_app_settings
    base = {"CS2BOT_TOKEN": "1:x", "CS2BOT_URL": "https://x", "CS2BOT_DB": str(tmp_path / "a.db")}
    assert load_app_settings(base).backups == tmp_path / "backups"
    s = load_app_settings({**base, "CS2BOT_BACKUP_DIR": str(tmp_path / "bk"), "CS2BOT_BACKUP_KEEP": "30"})
    assert (s.backups, s.backup_keep) == (tmp_path / "bk", 30)


def test_not_found_everywhere_counts_as_steam_failing(tmp_path):
    """Steam answers "not found" under load: attempts without successes must alert."""
    import time as _time
    from cs2tracker.app.prices import PriceService
    from cs2tracker.steam import ItemNotFound

    class NotFoundMarket:
        def orderbook(self, name):
            raise ItemNotFound(name)

    now = [10_000.0]
    store = make_db(tmp_path)
    prices = PriceService(store, NotFoundMarket(), currency="UAH", kind="sell", refresh_minutes=10,
                          clock=lambda: now[0])
    rec = Recorder()
    mon = HealthMonitor(prices, AdminAlerts(rec, clock=lambda: now[0]), db_path=tmp_path / "live.db",
                        backup_dir=tmp_path / "b", clock=lambda: now[0])
    for _ in range(8):
        now[0] += 600
        asyncio.run(prices.refresh())
        asyncio.run(mon.check())
    assert [k for k, _ in rec.sent] == ["steam_failing"]
    assert "not found" in rec.sent[0][1]["error"]


def test_stray_and_future_files_do_not_confuse_backups(tmp_path):
    make_db(tmp_path)
    b = tmp_path / "b"
    b.mkdir()
    (b / "cs2tracker-before-v2.db").write_text("operator's copy")
    (b / "cs2tracker-20991231-000000.db").write_text("written while the clock was ahead")
    now = 1_700_000_000
    assert latest_age(b, now) is None  # neither counts
    for day in range(4):
        backup_database(tmp_path / "live.db", b, keep=2, now=now + day * 86400)
    names = sorted(p.name for p in b.iterdir())
    assert "cs2tracker-before-v2.db" in names and "cs2tracker-20991231-000000.db" in names  # never pruned
    assert len(backups(b, now + 3 * 86400)) == 2
    assert latest_age(b, now + 3 * 86400 + 60) == 60


def test_second_instance_keeps_the_crash_marker(tmp_path, monkeypatch):
    """A second instance that can't bind must not remove the running one's marker."""
    from cs2tracker.app import __main__ as main
    db = tmp_path / "app.db"
    marker = tmp_path / "app.db.running"
    marker.write_text("12345")  # the running instance
    monkeypatch.setenv("CS2BOT_TOKEN", "1:x")
    monkeypatch.setenv("CS2BOT_URL", "https://x")
    monkeypatch.setenv("CS2BOT_DB", str(db))

    class Busy:
        def __init__(self, *a, **k):
            pass

        async def start(self):
            raise OSError("address already in use")
    monkeypatch.setattr(main.web, "TCPSite", Busy)
    with pytest.raises(OSError):
        asyncio.run(main.serve())
    assert marker.read_text() == "12345"
