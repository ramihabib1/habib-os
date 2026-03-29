"""Marketing agent — PPC watchdog, competitor snapshots, review intelligence."""

from __future__ import annotations

from src.agents.base import BaseAgent


class MarketingAgent(BaseAgent):
    role = "marketing"
    output_language = "en"
