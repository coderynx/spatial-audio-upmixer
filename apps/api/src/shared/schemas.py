"""Base Pydantic model shared by every feature slice's response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
