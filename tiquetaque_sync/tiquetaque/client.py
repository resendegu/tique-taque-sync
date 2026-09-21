"""Async HTTP Client for the TiqueTaque REST API."""

import logging
from datetime import datetime
import pytz
import httpx

from .models import DayRecord, EmployeeProfile, WorkSchedule
from .security_hash import generate_clock_in_hash

logger = logging.getLogger(__name__)


class TiqueTaqueClient:
    """Client for interacting with the official TiqueTaque API (api.tiquetaque.com)."""

    def __init__(
        self,
        email: str,
        code: str,
        token: str | None = None,
        employee_id: str | None = None,
        full_name: str | None = None,
        timezone: str = "America/Sao_Paulo",
        base_url: str = "https://api.tiquetaque.com",
    ):
        self.email = email
        self.code = code
        self.token = token
        self.employee_id = employee_id
        self.full_name = full_name
        self.timezone_name = timezone
        self.tz = pytz.timezone(timezone)
        self.base_url = base_url.rstrip("/")
        self._http_client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    def _get_auth_headers(self) -> dict[str, str]:
        if not self.token:
            raise ValueError("Client is not authenticated. Call authenticate() first.")
        return {"Authorization": f"Bearer {self.token}"}

    async def authenticate(self) -> str:
        """Authenticate with email and 4-digit code via /employees/code/verify."""
        url = "/employees/code/verify"
        payload = {
            "email": self.email,
            "sms_verification_code": self.code,
            "source": "web",
        }
        logger.info("Authenticating with TiqueTaque as %s...", self.email)
        resp = await self._http_client.post(url, json=payload)
        if resp.status_code != 200:
            logger.error("Authentication failed: HTTP %s - %s", resp.status_code, resp.text)
            resp.raise_for_status()

        data = resp.json()
        self.token = data.get("token")
        self.employee_id = data.get("_id")
        logger.info("Authenticated successfully! Employee ID: %s", self.employee_id)

        # Retrieve profile to cache full_name
        try:
            profile = await self.get_profile()
            self.full_name = profile.full_name
            logger.info("Employee full name: %s", self.full_name)
        except Exception as e:
            logger.warning("Could not fetch profile during login: %s", e)

        return self.token

    async def ensure_authenticated(self) -> None:
        """Ensure token and employee_id exist, otherwise authenticate."""
        if not self.token or not self.employee_id:
            await self.authenticate()

    async def get_profile(self) -> EmployeeProfile:
        """Fetch employee profile information."""
        await self.ensure_authenticated()
        resp = await self._http_client.get(
            "/employees/profile",
            headers=self._get_auth_headers(),
        )
        if resp.status_code == 401:
            # Token expired; reauthenticate and retry once
            logger.info("Token expired during profile query, reauthenticating...")
            await self.authenticate()
            resp = await self._http_client.get(
                "/employees/profile",
                headers=self._get_auth_headers(),
            )

        resp.raise_for_status()
        data = resp.json()
        return EmployeeProfile(
            id=self.employee_id or "",
            full_name=data.get("full_name", ""),
            email=data.get("email", self.email),
            records_start_date=data.get("records_start_date"),
            company_name=data.get("payment_source", {}).get("name"),
        )

    async def get_work_schedules(self) -> list[WorkSchedule]:
        """Fetch contractual work schedules."""
        await self.ensure_authenticated()
        resp = await self._http_client.get(
            "/work-schedules",
            headers=self._get_auth_headers(),
        )
        resp.raise_for_status()
        items = resp.json().get("_items", [])
        return [
            WorkSchedule(
                id=item.get("_id", ""),
                description=item.get("description", ""),
                schedule_type=item.get("schedule_type", "regular"),
                work_days=item.get("work_days", {}),
            )
            for item in items
        ]

    async def get_day_records(self, period: str = "current") -> list[DayRecord]:
        """Fetch timesheet and clock-in records for the specified period."""
        await self.ensure_authenticated()
        params = {
            "employee": self.employee_id,
            "period": period,
        }
        resp = await self._http_client.get(
            "/employees/day-records",
            params=params,
            headers=self._get_auth_headers(),
        )
        if resp.status_code == 401:
            logger.info("Token expired during day-records query, reauthenticating...")
            await self.authenticate()
            params["employee"] = self.employee_id
            resp = await self._http_client.get(
                "/employees/day-records",
                params=params,
                headers=self._get_auth_headers(),
            )

        resp.raise_for_status()
        data = resp.json()
        raw_items = data.get("_items", [])
        return [DayRecord.from_api(item) for item in raw_items]

    async def get_today_record(self) -> DayRecord | None:
        """Fetch today's DayRecord containing all registered points for today."""
        now = datetime.now(self.tz)
        today_date_str = now.strftime("%d/%m/%Y")
        records = await self.get_day_records(period="current")

        for r in records:
            # TiqueTaque dates look like "21/09/2026 12:00:00 +0000"
            if today_date_str in r.date:
                return r

        # If not found in the list, return empty DayRecord for today
        return DayRecord(date=f"{today_date_str} 12:00:00 +0000", time_entries=[])

    async def clock_in(self, time_str: str | None = None, date_str: str | None = None) -> dict:
        """Register a point (bater ponto) using the official API and security hash.

        Args:
            time_str: HH:mm (default: current local time)
            date_str: DD/MM/YYYY (default: current local date)
        """
        await self.ensure_authenticated()
        now = datetime.now(self.tz)

        if not time_str:
            time_str = now.strftime("%H:%M")
        if not date_str:
            date_str = now.strftime("%d/%m/%Y")

        date_for_hash = now.strftime("%d-%m-%Y")
        full_name = self.full_name or "Employee"

        x_check_hash = generate_clock_in_hash(
            employee_id=self.employee_id or "",
            full_name=full_name,
            date_str=date_for_hash,
            time_str=time_str,
        )

        headers = {
            **self._get_auth_headers(),
            "X-Check": x_check_hash,
        }

        payload = {
            "times": [
                {
                    "source": "web",
                    "date": date_str,
                    "time": time_str,
                }
            ]
        }

        logger.info("Registering clock-in at %s %s with X-Check %s...", date_str, time_str, x_check_hash[:8])
        resp = await self._http_client.post(
            "/employees/day-records/add-times",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
