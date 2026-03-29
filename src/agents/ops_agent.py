"""Operations agent — daily briefing, inventory checks, replenishment suggestions."""

from __future__ import annotations

from src.agents.base import BaseAgent


class OpsAgent(BaseAgent):
    role = "ops"
    output_language = "en"
