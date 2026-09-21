"""Application runtime: builds the engine, client, notifiers and scheduler.

Everything is rebuilt from the current settings, so saving the settings screen
re-applies notification channels, alert moments and polling intervals without
restarting the process.
"""

import asyncio
import logging

from .config import Settings, reload_settings, settings
from .engine.database import Database
from .engine.scheduler import SyncScheduler
from .engine.workday import WorkdayEngine
from .notifiers.dispatcher import NotificationDispatcher, create_dispatcher_from_settings
from .tiquetaque.client import TiqueTaqueClient

logger = logging.getLogger(__name__)


class AppRuntime:
    """Owns the long-lived objects of the service."""

    def __init__(self, config: Settings | None = None):
        self.settings = config or settings
        self.db: Database = self._build_db()
        self.client: TiqueTaqueClient = self._build_client()
        self.dispatcher: NotificationDispatcher = self._build_dispatcher()
        self.engine: WorkdayEngine = self._build_engine()
        self.scheduler: SyncScheduler = self._build_scheduler()
        self._lock = asyncio.Lock()
        self._started = False

    # ---------------------------------------------------------------- builders
    def _build_db(self) -> Database:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        return Database(self.settings.db_path)

    def _build_client(self) -> TiqueTaqueClient:
        return TiqueTaqueClient(
            email=self.settings.tiquetaque_email,
            code=self.settings.tiquetaque_code,
            token=self.settings.tiquetaque_token,
            employee_id=self.settings.tiquetaque_employee_id,
            full_name=self.settings.tiquetaque_full_name,
            timezone=self.settings.timezone,
        )

    def _build_dispatcher(self) -> NotificationDispatcher:
        return create_dispatcher_from_settings(self.settings)

    def _build_engine(self) -> WorkdayEngine:
        s = self.settings
        return WorkdayEngine(
            target_hours=s.work_hours_per_day,
            lunch_minutes=s.lunch_duration_minutes,
            lunch_advance_warning=s.lunch_warning_advance_minutes,
            lunch_final_warning=s.lunch_warning_final_minutes,
            end_work_advance_warning=s.end_work_warning_advance_minutes,
            end_work_final_warning=s.end_work_warning_final_minutes,
            continuous_work_limit_hours=s.continuous_work_limit_hours,
            continuous_work_advance_warning=s.continuous_work_warning_advance_minutes,
            continuous_work_final_warning=s.continuous_work_warning_final_minutes,
            timezone_name=s.timezone,
        )

    def _build_scheduler(self) -> SyncScheduler:
        return SyncScheduler(
            client=self.client,
            engine=self.engine,
            database=self.db,
            dispatcher=self.dispatcher,
            poll_interval_seconds=self.settings.poll_interval_seconds,
            alert_ticker_interval_seconds=self.settings.alert_ticker_interval_seconds,
            timezone_name=self.settings.timezone,
        )

    # ----------------------------------------------------------------- control
    async def start(self) -> None:
        if not self.settings.is_configured:
            logger.warning(
                "TiqueTaque credentials are missing — open %s/settings (or run "
                "'tiquetaque-sync setup') to finish configuring.",
                self.settings.dashboard_url,
            )
        await self.scheduler.start()
        self._started = True

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.client.close()
        self._started = False

    async def reload(self) -> None:
        """Re-read the configuration and rebuild every settings-derived object."""
        async with self._lock:
            previous_status = self.scheduler.last_status
            previous_client = self.client

            await self.scheduler.stop()
            reload_settings()
            self.settings = settings

            self.db = self._build_db()
            self.client = self._build_client()
            self.dispatcher = self._build_dispatcher()
            self.engine = self._build_engine()
            self.scheduler = self._build_scheduler()
            # Keep the dashboard populated across the restart.
            self.scheduler.seed_status(previous_status)

            await previous_client.close()

            if self._started:
                await self.scheduler.start()
            logger.info("Runtime reloaded with the updated configuration.")


runtime = AppRuntime()
