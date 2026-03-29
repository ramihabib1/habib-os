"""
BaseAgent — wraps the Claude Agent SDK query() loop.

Every agent (ops, finance, marketing) inherits from this class.
Provides:
  - System prompt construction (CLAUDE.md + memory + skill)
  - Supabase query tools
  - PreToolUse hook for financial action approval
  - agent_runs logging (tokens, cost, duration)
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agents.hooks import pre_tool_use_hook
from src.agents.memory import AgentMemory
from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_CLAUDE_MD = Path(__file__).parent.parent.parent / "CLAUDE.md"
_SKILLS_DIR = Path(__file__).parent / "skills"

# Approximate cost per token for claude-sonnet-4-5 (USD)
_COST_PER_INPUT_TOKEN = 3e-6
_COST_PER_OUTPUT_TOKEN = 15e-6


class BaseAgent:
    """
    Base class for all Habib OS agents.

    Subclasses must set:
      - role: str  — "ops" | "finance" | "marketing"
      - output_language: str — "en" | "ar"
    """

    role: str = "ops"
    output_language: str = "en"

    def __init__(self) -> None:
        self.memory = AgentMemory(self.role)
        self._run_id = str(uuid.uuid4())

    # ── Prompt construction ───────────────────────────────────────────────────

    def _load_system_prompt(self, skill_name: str, memory_context: str) -> str:
        """Build the full system prompt: CLAUDE.md + memory + skill instructions."""
        parts: list[str] = []

        # CLAUDE.md as grounding
        if _CLAUDE_MD.exists():
            parts.append(_CLAUDE_MD.read_text(encoding="utf-8"))

        # Language directive
        if self.output_language == "ar":
            parts.append(
                "\n\n**CRITICAL: ALL output, analysis, summaries, and Telegram messages "
                "MUST be written entirely in Arabic. Do not use English.**"
            )

        # Memory context
        if memory_context:
            parts.append(f"\n\n## Your Memory\n{memory_context}")

        # Skill instructions
        skill_file = _SKILLS_DIR / f"{skill_name}.md"
        if skill_file.exists():
            parts.append(f"\n\n## Current Task\n{skill_file.read_text(encoding='utf-8')}")

        return "\n\n".join(parts)

    # ── Supabase tools ────────────────────────────────────────────────────────

    async def _tool_query_table(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        columns: str = "*",
        limit: int = 100,
    ) -> list[dict]:
        """Query a Supabase table and return rows."""
        db = await get_supabase()
        query = db.table(table).select(columns)
        if filters:
            for col, val in filters.items():
                query = query.eq(col, val)
        result = await query.limit(limit).execute()
        return result.data or []

    async def _tool_query_view(self, view: str, limit: int = 100) -> list[dict]:
        """Query a Supabase view."""
        return await self._tool_query_table(view, limit=limit)

    async def _tool_call_rpc(self, function_name: str, params: dict | None = None) -> Any:
        """Call a Supabase RPC function."""
        db = await get_supabase()
        result = await db.rpc(function_name, params or {}).execute()
        return result.data

    # ── Agent run ─────────────────────────────────────────────────────────────

    async def run_skill(self, skill_name: str, extra_context: str = "") -> dict[str, Any]:
        """
        Execute a named skill using the Claude Agent SDK.

        Args:
            skill_name:    Name of the skill file in src/agents/skills/ (without .md)
            extra_context: Optional additional context to append to the user message

        Returns:
            dict with final_response, tokens_used, cost_usd, duration_seconds
        """
        start = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        # Load memory context
        memory_context = await self.memory.load_context(skill_name)
        system_prompt = self._load_system_prompt(skill_name, memory_context)

        user_message = f"Execute the {skill_name} skill for Habib Distribution."
        if extra_context:
            user_message += f"\n\nAdditional context: {extra_context}"

        logger.info("agent_skill_start", agent=self.role, skill=skill_name, run_id=self._run_id)

        final_response = ""
        error: str | None = None

        try:
            # Use Claude Agent SDK
            import claude_agent_sdk as sdk

            messages = [{"role": "user", "content": user_message}]

            async for event in sdk.query(
                model="claude-sonnet-4-6",
                system=system_prompt,
                messages=messages,
                tools=self._get_tools(),
                api_key=settings.ANTHROPIC_API_KEY,
            ):
                if event.type == "text":
                    final_response += event.text
                elif event.type == "tool_use":
                    # Check if tool requires approval
                    allowed = await pre_tool_use_hook(
                        tool_name=event.name,
                        tool_input=event.input,
                        agent_name=self.role,
                    )
                    if not allowed:
                        logger.info("tool_blocked", tool=event.name)
                elif hasattr(event, "usage"):
                    input_tokens += getattr(event.usage, "input_tokens", 0)
                    output_tokens += getattr(event.usage, "output_tokens", 0)

        except Exception as exc:
            error = str(exc)
            logger.error("agent_skill_error", agent=self.role, skill=skill_name, exc=error)

        duration = time.monotonic() - start
        cost_usd = (
            input_tokens * _COST_PER_INPUT_TOKEN
            + output_tokens * _COST_PER_OUTPUT_TOKEN
        )

        # Log to agent_runs
        await self._log_run(
            skill_name=skill_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration=duration,
            success=error is None,
            error=error,
        )

        return {
            "final_response": final_response,
            "tokens_used": input_tokens + output_tokens,
            "cost_usd": round(cost_usd, 6),
            "duration_seconds": round(duration, 2),
            "error": error,
        }

    def _get_tools(self) -> list[dict]:
        """Override in subclasses to add agent-specific tools."""
        return []

    async def _log_run(
        self,
        skill_name: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        duration: float,
        success: bool,
        error: str | None,
    ) -> None:
        try:
            db = await get_supabase()
            await db.table("agent_runs").insert({
                "id": self._run_id,
                "agent": self.role,
                "skill": skill_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost_usd, 6),
                "duration_seconds": round(duration, 2),
                "success": success,
                "error": error,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            logger.warning("agent_run_log_failed", exc=str(exc))
