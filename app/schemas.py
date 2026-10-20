"""Validated owner-facing API input for AI HUB."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task: str = Field(min_length=1, max_length=8000)
    language: Literal["ar", "en"] = "ar"

class RunRequest(TaskRequest):
    continue_from_report_id: str | None = Field(
        default=None, max_length=100, pattern=r"^rpt_[A-Za-z0-9_-]{1,96}$")

class ScheduleCreate(TaskRequest):
    agent_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    name: str = Field(default="", max_length=120)
    times: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = False

    @field_validator("times")
    @classmethod
    def validate_times(cls, values: list[str]) -> list[str]:
        import re
        if any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in values):
            raise ValueError("Use daily times such as 09:30 on the 24-hour clock, in Asia/Dubai.")
        if len(set(values)) != len(values):
            raise ValueError("Daily times must not repeat.")
        return sorted(values)

class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    name: str | None = Field(default=None, max_length=120)
    task: str | None = Field(default=None, min_length=1, max_length=8000)
    times: list[str] | None = Field(default=None, min_length=1, max_length=50)
    enabled: bool | None = None
    language: Literal["ar", "en"] | None = None

    @field_validator("times")
    @classmethod
    def validate_times(cls, values: list[str] | None) -> list[str] | None:
        return ScheduleCreate.validate_times(values) if values is not None else None

class AgentSummary(BaseModel):
    id: str
    number: str
    name: str
    description: str
    skill_ids: list[str]
    enabled: bool
    runtime_ready: bool = False
