"""Pydantic schemas for TiqueTaque API requests and responses."""

from typing import Any
from pydantic import BaseModel, Field


class TimeEntry(BaseModel):
    time: str = Field(description="Time formatted as HH:mm (e.g. '08:00', '12:00')")
    source: str = Field(default="web", description="Registration source (e.g. 'web', 'app', 'tablet')")
    approved: bool = Field(default=False)
    justification: str | None = None
    time_id: str | None = None
    nsr: int | None = None
    original_date: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "TimeEntry":
        afd = data.get("afd") or {}
        return cls(
            time=data.get("time", ""),
            source=data.get("source", "web"),
            approved=bool(data.get("approved", False)),
            justification=data.get("justification"),
            time_id=data.get("time_id"),
            nsr=afd.get("nsr"),
            original_date=data.get("original_date"),
        )


class DayRecord(BaseModel):
    date: str
    time_entries: list[TimeEntry] = Field(default_factory=list)
    work_schedule: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "DayRecord":
        raw_entries = data.get("time_entries", [])
        return cls(
            date=data.get("date", ""),
            time_entries=[TimeEntry.from_api(e) for e in raw_entries],
            work_schedule=data.get("work_schedule"),
        )


class EmployeeProfile(BaseModel):
    id: str = ""
    full_name: str = ""
    email: str = ""
    records_start_date: str | None = None
    company_name: str | None = None


class WorkScheduleDay(BaseModel):
    start_work: str | None = None
    end_work: str | None = None
    start_interval: str | None = None
    end_interval: str | None = None
    day_break: str | None = None
    dsr_day: bool = False


class WorkSchedule(BaseModel):
    id: str
    description: str
    schedule_type: str = "regular"
    work_days: dict[str, Any] = Field(default_factory=dict)
