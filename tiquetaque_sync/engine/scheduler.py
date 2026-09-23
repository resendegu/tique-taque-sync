"""Background scheduler and polling coordinator."""

import logging
import asyncio
from datetime import datetime, timedelta
import pytz

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    HAS_APSCHEDULER = True
except ImportError:
    AsyncIOScheduler = None
    HAS_APSCHEDULER = False

from ..tiquetaque.client import TiqueTaqueClient
from ..notifiers.dispatcher import NotificationDispatcher
from .database import Database
from .workday import WorkdayEngine, WorkdayStatus

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Orchestrates periodic synchronization with TiqueTaque and triggers notifications."""

    def __init__(
        self,
        client: TiqueTaqueClient,
        engine: WorkdayEngine,
        database: Database,
        dispatcher: NotificationDispatcher,
        poll_interval_seconds: int = 180,
        alert_ticker_interval_seconds: int = 15,
        stale_alert_minutes: int = 15,
        timezone_name: str = "America/Sao_Paulo",
    ):
        self.client = client
        self.engine = engine
        self.db = database
        self.dispatcher = dispatcher
        self.poll_interval = poll_interval_seconds
        self.alert_ticker_interval = alert_ticker_interval_seconds
        # Alerta que se refere a mais de X minutos atrás é registrado, não enviado.
        self.stale_alert_minutes = stale_alert_minutes
        self.tz = pytz.timezone(timezone_name)
        self._last_status: WorkdayStatus | None = None
        self._is_syncing = False
        self._running = False
        self._scheduler = AsyncIOScheduler() if HAS_APSCHEDULER else None
        self._async_task: asyncio.Task | None = None
        self._ticker_task: asyncio.Task | None = None

    async def _dispatch_triggers(self, triggers: list, status, now: datetime) -> None:
        """Anuncia o que é atual e apenas registra o que já passou.

        Dois cenários faziam o app despejar notificações de uma vez:

        * **Reinício** — o `dispatched_alerts` vive no SQLite; num contêiner com
          disco efêmero ele some, e todas as batidas do dia eram anunciadas de
          novo.
        * **Meia-noite** — a chave de deduplicação inclui a data, então à 00:00
          tudo virava "inédito".

        Nos dois casos o alerta se refere a um instante que já passou. Aqui ele é
        gravado como despachado (para nunca mais disparar) mas não é enviado.
        Alertas de janela curta ("faltam 5 minutos") trazem `moment = agora` e
        passam normalmente.
        """
        tolerance = timedelta(minutes=self.stale_alert_minutes)

        for trigger in triggers:
            key = trigger["key"]
            if self.db.is_alert_dispatched(status.date_str, key):
                continue

            atraso = now - (trigger.get("moment") or now)
            if atraso > tolerance:
                logger.info(
                    "Silenciando '%s': refere-se a %d min atrás.",
                    key, int(atraso.total_seconds() // 60),
                )
                self.db.mark_alert_dispatched(status.date_str, key, trigger["title"])
                continue

            logger.info("Firing alert '%s': %s", key, trigger["title"])
            await self.dispatcher.dispatch(
                title=trigger["title"],
                message=trigger["message"],
                level=trigger.get("level", "info"),
            )
            self.db.mark_alert_dispatched(status.date_str, key, trigger["title"])

    @property
    def last_status(self) -> WorkdayStatus | None:
        return self._last_status

    def seed_status(self, status: WorkdayStatus | None) -> None:
        """Carry a previously computed status over into a freshly built scheduler."""
        if status is not None:
            self._last_status = status

    async def start(self) -> None:
        """Start the background scheduler."""
        self._running = True
        logger.info("Starting SyncScheduler (poll: %ds, ticker: %ds, backend: %s)...",
                    self.poll_interval, self.alert_ticker_interval, "APScheduler" if HAS_APSCHEDULER else "asyncio.Task")

        if HAS_APSCHEDULER and self._scheduler:
            self._scheduler.add_job(
                self.sync_now,
                trigger="interval",
                seconds=self.poll_interval,
                id="tiquetaque_poller",
                replace_existing=True,
            )
            self._scheduler.add_job(
                self._evaluate_active_alerts,
                trigger="interval",
                seconds=self.alert_ticker_interval,
                id="tiquetaque_alert_ticker",
                replace_existing=True,
            )
            self._scheduler.start()
        else:
            self._async_task = asyncio.create_task(self._poll_loop())
            self._ticker_task = asyncio.create_task(self._ticker_loop())

        # Perform initial sync in background
        asyncio.create_task(self.sync_now())

    async def _poll_loop(self) -> None:
        """Native asyncio fallback loop for remote API polling."""
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                if self._running:
                    await self.sync_now()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in scheduler poll loop: %s", e)

    async def _ticker_loop(self) -> None:
        """Native asyncio fast ticker loop for precise local alert evaluation."""
        while self._running:
            try:
                await asyncio.sleep(self.alert_ticker_interval)
                if self._running:
                    await self._evaluate_active_alerts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Error in scheduler alert ticker: %s", e)

    async def _evaluate_active_alerts(self) -> None:
        """Fast local evaluation of alerts against current local time."""
        if not self._last_status or not self._last_status.entries:
            return

        now = datetime.now(self.tz)

        # As batidas em memória são do dia em que foram buscadas. Passada a
        # meia-noite elas viram história: reavaliá-las com a data de hoje faz
        # cada uma parecer inédita e dispara o dia inteiro de novo. Descarta e
        # espera o poller trazer o dia corrente.
        if self._last_status.date_str != now.strftime("%d/%m/%Y"):
            logger.info(
                "Virada de dia detectada (cache de %s): limpando o estado até a próxima sincronização.",
                self._last_status.date_str,
            )
            self._last_status = None
            return

        try:
            status = self.engine.calculate_status(self._last_status.entries, current_dt=now)
            self._last_status = status

            triggers = self.engine.evaluate_alert_triggers(status, current_dt=now)
            await self._dispatch_triggers(triggers, status, now)
        except Exception as e:
            logger.debug("Error during fast alert evaluation: %s", e)

    async def stop(self) -> None:
        """Stop background scheduler."""
        self._running = False
        if HAS_APSCHEDULER and self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._async_task and not self._async_task.done():
            self._async_task.cancel()
        if self._ticker_task and not self._ticker_task.done():
            self._ticker_task.cancel()
        logger.info("SyncScheduler stopped.")

    async def sync_now(self) -> WorkdayStatus:
        """Execute synchronization cycle immediately."""
        if self._is_syncing:
            logger.debug("Sync already in progress, skipping duplicate run.")
            return self._last_status or self.engine.calculate_status([])

        self._is_syncing = True
        try:
            now = datetime.now(self.tz)
            logger.info("Running sync cycle at %s...", now.strftime("%Y-%m-%d %H:%M:%S"))

            # Fetch today's record from TiqueTaque
            day_record = await self.client.get_today_record()
            times = []
            if day_record:
                for entry in day_record.time_entries:
                    times.append(entry.time)
                    # Persist to database
                    date_str = now.strftime("%d/%m/%Y")
                    self.db.record_entry(
                        date_str=date_str,
                        time_str=entry.time,
                        source=entry.source,
                        approved=entry.approved,
                        nsr=entry.nsr,
                    )

            # Compute current status
            status = self.engine.calculate_status(times, current_dt=now)
            self._last_status = status

            # Persist snapshot
            self.db.save_daily_snapshot(
                date_str=status.date_str,
                stage=status.stage.value,
                worked_seconds=status.worked_seconds,
                entries=[{"time": t} for t in times],
            )

            # Evaluate alert triggers
            triggers = self.engine.evaluate_alert_triggers(status, current_dt=now)
            await self._dispatch_triggers(triggers, status, now)

            return status
        except Exception as e:
            logger.exception("Error during sync cycle: %s", e)
            if self._last_status:
                return self._last_status
            return self.engine.calculate_status([])
        finally:
            self._is_syncing = False
