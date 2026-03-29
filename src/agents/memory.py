"""
Agent memory system — two-layer persistent memory.

Layer 1: Markdown files at .claude/memory/{agent}.md
  - Human-readable dated entries
  - Read at agent startup, appended after each run

Layer 2: pgvector embeddings in agent_memory table
  - Semantic search for relevant past memories
  - Embedded via Anthropic text-embedding-3-small (via OpenAI compat) or
    direct Supabase pgvector upsert using pre-computed embeddings

Usage:
  memory = AgentMemory("ops")
  context = await memory.load_context("inventory check for Almond Fingers")
  # ... run agent ...
  await memory.save_learning(date="2026-03-28", task="daily_briefing", content="...")
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.utils.logging import get_logger

logger = get_logger(__name__)

_MEMORY_DIR = Path(__file__).parent.parent.parent / ".claude" / "memory"
_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIM = 1536


class AgentMemory:
    """Manages memory for a specific agent."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self.memory_file = _MEMORY_DIR / f"{agent_name}.md"
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    # ── Layer 1: Markdown memory ──────────────────────────────────────────────

    def read_markdown(self) -> str:
        """Read the full markdown memory file for this agent."""
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8")

    def append_learning(
        self,
        task: str,
        observations: str,
        decisions: str,
        improvements: str | None = None,
    ) -> None:
        """Append a new learning entry to the markdown memory file."""
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"""
## {date} — {task}

**Observations:** {observations}

**Decisions:** {decisions}
"""
        if improvements:
            entry += f"\n**Improvements:** {improvements}\n"

        with self.memory_file.open("a", encoding="utf-8") as f:
            f.write(entry)

        logger.debug("memory_markdown_appended", agent=self.agent_name, task=task)

    # ── Layer 2: pgvector semantic memory ─────────────────────────────────────

    async def embed(self, text: str) -> list[float]:
        """Generate a 1536-dim embedding for the given text."""
        # Use Anthropic's API — they expose embeddings via the same client
        # In practice, use OpenAI's text-embedding-3-small or voyageai
        # For now, fall back to a zero vector if embedding unavailable
        try:
            import openai  # optional dependency
            client = openai.AsyncOpenAI(api_key=settings.ANTHROPIC_API_KEY)
            response = await client.embeddings.create(
                model=_EMBEDDING_MODEL,
                input=text,
            )
            return response.data[0].embedding
        except Exception as exc:
            logger.warning("embed_failed", exc=str(exc))
            return [0.0] * _EMBEDDING_DIM

    async def search_memories(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Search pgvector for semantically similar past memories."""
        try:
            query_embedding = await self.embed(query)
            db = await get_supabase()
            result = await db.rpc(
                "match_memories",
                {
                    "query_embedding": query_embedding,
                    "match_agent": self.agent_name,
                    "match_count": top_k,
                    "match_threshold": threshold,
                },
            ).execute()
            return result.data or []
        except Exception as exc:
            logger.warning("memory_search_failed", exc=str(exc))
            return []

    async def store_memory(
        self,
        content: str,
        task: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Embed and store a memory in pgvector."""
        try:
            embedding = await self.embed(content)
            db = await get_supabase()
            await db.table("agent_memory").insert({
                "agent": self.agent_name,
                "task": task,
                "content": content,
                "embedding": embedding,
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as exc:
            logger.warning("memory_store_failed", exc=str(exc))

    # ── Combined context loader ───────────────────────────────────────────────

    async def load_context(self, query: str) -> str:
        """
        Build memory context for injection into agent prompt.
        Combines last N lines of markdown + top semantic matches.
        """
        lines = []

        # Layer 1: last 100 lines of markdown memory
        md = self.read_markdown()
        if md:
            recent_lines = md.strip().split("\n")[-100:]
            lines.append("## Recent Memory (markdown)\n")
            lines.extend(recent_lines)

        # Layer 2: semantic matches
        matches = await self.search_memories(query)
        if matches:
            lines.append("\n## Relevant Past Memories (semantic search)\n")
            for m in matches:
                lines.append(f"- [{m.get('task', '?')}] {m.get('content', '')[:200]}")

        return "\n".join(lines)

    async def save_learning(
        self,
        task: str,
        observations: str,
        decisions: str,
        improvements: str | None = None,
    ) -> None:
        """Save learning to both markdown and pgvector."""
        self.append_learning(task, observations, decisions, improvements)

        combined = f"Task: {task}. Observations: {observations}. Decisions: {decisions}."
        await self.store_memory(combined, task)
