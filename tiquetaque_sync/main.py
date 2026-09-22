"""Main FastAPI application entrypoint."""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
import pytz
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from . import autostart, paths, store
from .config import Settings, settings
from .engine.database import Database
from .engine.scheduler import SyncScheduler
from .engine.workday import WorkdayEngine
from .runtime import runtime
from .tiquetaque.client import TiqueTaqueClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tiquetaque_sync")

def _base_dir() -> Path:
    """Diretório que contém ``web/``.

    Sob PyInstaller os módulos vivem no arquivo compactado e os dados são
    extraídos para ``sys._MEIPASS`` — daí a necessidade do caso especial.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "tiquetaque_sync"
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR = BASE_DIR / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup and shutdown routines."""
    logger.info("Initializing TiqueTaque Sync service...")
    logger.info("Configuration file: %s", paths.config_file())
    logger.info("Data directory: %s", settings.data_dir)

    await runtime.start()
    yield

    logger.info("Shutting down TiqueTaque Sync...")
    await runtime.stop()


app = FastAPI(
    title="TiqueTaque Sync & Assistant",
    description="Workday tracker, real-time alerts (Slack & Telegram), and web dashboard for TiqueTaque",
    version="2.0.0",
    lifespan=lifespan,
)

# Static & Template files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ------------------------------------------------------------------------------
# Web Routes
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve the modern dark-theme dashboard."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/settings", response_class=HTMLResponse)
async def serve_settings_page(request: Request):
    """Serve the settings screen (credentials, channels and alert moments)."""
    return templates.TemplateResponse(request=request, name="settings.html")


# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------
@app.get("/api/status")
async def get_status():
    """Return the current workday status and countdown metrics."""
    scheduler: SyncScheduler = runtime.scheduler
    engine: WorkdayEngine = runtime.engine

    status = scheduler.last_status
    if not status:
        status = engine.calculate_status([])
    return status.to_dict()


@app.get("/api/entries")
async def get_entries(date: str | None = None):
    """Return entries stored in the database for the given date (default: today)."""
    db: Database = runtime.db
    tz = pytz.timezone(settings.timezone)
    now = datetime.now(tz)
    date_str = date or now.strftime("%d/%m/%Y")
    entries = db.get_entries_for_date(date_str)
    return {"date": date_str, "entries": entries}


@app.get("/api/config")
async def get_public_config():
    """Return non-sensitive public configuration."""
    client: TiqueTaqueClient = runtime.client
    schedule_desc = "Seg.-Sex. 08:00 - 17:00 (1h almoço)"

    if client and settings.is_configured:
        try:
            schedules = await client.get_work_schedules()
            if schedules:
                schedule_desc = schedules[0].description
        except Exception:
            pass

    return {
        "timezone": settings.timezone,
        "work_hours": settings.work_hours_per_day,
        "lunch_minutes": settings.lunch_duration_minutes,
        "lunch_warning_advance_minutes": settings.lunch_warning_advance_minutes,
        "lunch_warning_final_minutes": settings.lunch_warning_final_minutes,
        "end_work_warning_advance_minutes": settings.end_work_warning_advance_minutes,
        "end_work_warning_final_minutes": settings.end_work_warning_final_minutes,
        "continuous_work_limit_hours": settings.continuous_work_limit_hours,
        "continuous_work_warning_advance_minutes": settings.continuous_work_warning_advance_minutes,
        "continuous_work_warning_final_minutes": settings.continuous_work_warning_final_minutes,
        "telegram_enabled": settings.telegram_enabled,
        "slack_enabled": settings.slack_enabled,
        "schedule_description": schedule_desc,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "alert_ticker_interval_seconds": settings.alert_ticker_interval_seconds,
        "is_configured": settings.is_configured,
    }


# ------------------------------------------------------------------------------
# Settings API (used by the /settings screen)
# ------------------------------------------------------------------------------
class SettingsPayload(BaseModel):
    """Partial settings update. Omitted fields keep their current value."""

    model_config = {"extra": "forbid"}

    tiquetaque_email: str | None = None
    tiquetaque_code: str | None = None
    timezone: str | None = None

    work_hours_per_day: float | None = Field(default=None, gt=0, le=24)
    lunch_duration_minutes: int | None = Field(default=None, ge=0, le=480)
    lunch_warning_advance_minutes: int | None = Field(default=None, ge=0, le=480)
    lunch_warning_final_minutes: int | None = Field(default=None, ge=0, le=480)
    end_work_warning_advance_minutes: int | None = Field(default=None, ge=0, le=480)
    end_work_warning_final_minutes: int | None = Field(default=None, ge=0, le=480)
    continuous_work_limit_hours: float | None = Field(default=None, gt=0, le=24)
    continuous_work_warning_advance_minutes: int | None = Field(default=None, ge=0, le=480)
    continuous_work_warning_final_minutes: int | None = Field(default=None, ge=0, le=480)

    poll_interval_seconds: int | None = Field(default=None, ge=30, le=3600)
    alert_ticker_interval_seconds: int | None = Field(default=None, ge=5, le=300)

    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    slack_enabled: bool | None = None
    slack_webhook_url: str | None = None

    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    open_browser_on_start: bool | None = None


def _settings_response() -> dict:
    """Current effective settings, with secrets masked, plus environment context."""
    values = {key: getattr(settings, key) for key in store.EDITABLE_KEYS}
    env_managed = sorted(k for k in store.EDITABLE_KEYS if _is_env_managed(k))
    return {
        "settings": store.mask(values),
        "is_configured": settings.is_configured,
        "config_file": str(paths.config_file()),
        "data_dir": str(settings.data_dir),
        "dashboard_url": settings.dashboard_url,
        "env_managed_keys": env_managed,
        "autostart": autostart.status().to_dict(),
    }


def _is_env_managed(key: str) -> bool:
    """True when an environment variable overrides whatever the UI saves."""
    return key.upper() in os.environ


@app.get("/api/settings")
async def read_settings():
    """Return the effective configuration (secrets masked) for the settings screen."""
    return _settings_response()


@app.put("/api/settings")
async def update_settings(payload: SettingsPayload):
    """Persist configuration changes and hot-reload the running service.

    Secret fields (`tiquetaque_code`, `telegram_bot_token`, `slack_webhook_url`)
    keep their stored value when sent empty, and are cleared when sent as `null`
    together with the key present in the request body.
    """
    provided = payload.model_dump(exclude_unset=True)
    updates: dict = {}

    for key, value in provided.items():
        if key in store.SECRET_KEYS and isinstance(value, str) and not value.strip():
            # Blank password field means "keep what is already saved".
            continue
        updates[key] = value.strip() if isinstance(value, str) else value

    if not updates:
        return _settings_response()

    # Validate the merged result before writing anything to disk.
    merged = {key: getattr(settings, key) for key in store.EDITABLE_KEYS}
    merged.update(updates)
    try:
        Settings(**{k: v for k, v in merged.items() if v is not None})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    try:
        store.save(updates)
    except OSError as exc:
        logger.exception("Could not write the configuration file")
        raise HTTPException(status_code=500, detail=f"Falha ao salvar configuração: {exc}")

    await runtime.reload()

    response = _settings_response()
    response["restart_required"] = any(key in updates for key in ("host", "port"))
    return response


@app.get("/api/autostart")
async def read_autostart():
    """Report whether the app is registered to start with the operating system."""
    return autostart.status().to_dict()


class AutostartPayload(BaseModel):
    enabled: bool


@app.post("/api/autostart")
async def write_autostart(payload: AutostartPayload):
    """Enable or disable launching the app at user login."""
    try:
        return autostart.set_enabled(payload.enabled).to_dict()
    except autostart.AutostartError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sync")
async def trigger_manual_sync():
    """Manually trigger immediate sync with TiqueTaque."""
    if not settings.is_configured:
        raise HTTPException(status_code=400, detail="Configure suas credenciais do TiqueTaque em /settings")
    try:
        status = await runtime.scheduler.sync_now()
        return {"success": True, "status": status.to_dict()}
    except Exception as e:
        logger.exception("Manual sync failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/test-notification")
async def test_notification(channel: str = Query("telegram", enum=["telegram", "slack", "all"])):
    """Send a test notification to verify channel credentials."""
    dispatcher = runtime.dispatcher
    now_str = datetime.now(pytz.timezone(settings.timezone)).strftime("%H:%M:%S")

    title = "Teste de Notificação — TiqueTaque Sync"
    message = (
        f"Esta é uma mensagem de verificação enviada às <b>{now_str}</b>.\n"
        f"Seus alertas de início/fim de almoço e jornada de trabalho serão recebidos por este canal! 🚀"
    )

    if channel in ("telegram", "slack"):
        notifier = next((n for n in dispatcher.notifiers if n.name == channel), None)
        if not notifier or not notifier.is_enabled:
            raise HTTPException(
                status_code=400,
                detail=f"{channel.capitalize()} não está habilitado ou configurado",
            )
        res = await notifier.send_message(title, message, level="success")
        return {"channel": channel, "success": res}

    results = await dispatcher.dispatch(title, message, level="success")
    return {"results": results, "success": any(results.values())}


class ClockInRequest(BaseModel):
    time_str: str | None = None
    date_str: str | None = None


@app.post("/api/clock-in")
async def execute_clock_in(payload: ClockInRequest):
    """Execute point registration directly via official API."""
    if not settings.is_configured:
        raise HTTPException(status_code=400, detail="Configure suas credenciais do TiqueTaque em /settings")

    try:
        result = await runtime.client.clock_in(time_str=payload.time_str, date_str=payload.date_str)
        # Immediately refresh local state
        await runtime.scheduler.sync_now()
        return {"success": True, "result": result}
    except Exception as e:
        logger.exception("Failed to execute clock-in: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class UserscriptHookPayload(BaseModel):
    time_str: str
    source: str = "userscript"


@app.post("/api/webhook/clock-in")
async def handle_userscript_instant_hook(payload: UserscriptHookPayload):
    """Webhook triggered instantly when the user clicks 'Registrar ponto' in the browser."""
    logger.info("Received instant clock-in hook from userscript: %s", payload.time_str)
    # Trigger an immediate background sync to reflect the newly recorded point
    status = await runtime.scheduler.sync_now()
    return {"status": "received", "current_stage": status.stage.value}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url="/static/img/favicon.svg")


@app.get("/healthz")
async def health_check():
    """Liveness probe for Kubernetes pod."""
    return {"status": "ok", "timestamp": datetime.now(pytz.utc).isoformat()}
