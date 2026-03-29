"""
Finance agent — weekly financial summary in Arabic.
ALL output must be in Arabic per the Golden Rules.
"""

from __future__ import annotations

from src.agents.base import BaseAgent


class FinanceAgent(BaseAgent):
    role = "finance"
    output_language = "ar"
