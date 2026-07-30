"""System/health/configuration response and request models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    workers: int


class ResolveStemRoutingRequest(BaseModel):
    stems: list[str] = Field(min_length=1)
    channel_layout: str
    preset: str = "balanced"
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
