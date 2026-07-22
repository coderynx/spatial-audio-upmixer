"""Web API and job orchestration for upmixer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from upmixer_web.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the web application without import-time filesystem changes."""
    from upmixer_web.api import create_app as factory
    return factory(settings)


__all__ = ["create_app"]
