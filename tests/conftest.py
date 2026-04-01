"""
Shared pytest fixtures for Habib OS test suite.
"""

import asyncio
import pytest
import pytest_asyncio

from src.config.supabase_client import get_supabase, close_supabase


@pytest.fixture(scope="session")
def event_loop():
    """
    Session-scoped event loop.

    Required so that async module-level state (Supabase HTTP/2 connections) is
    created and reused within a single loop for the full test session.
    pytest-asyncio 0.23 marks this as deprecated but it still works; the
    replacement API (loop_scope= marker) requires pytest-asyncio >=0.24.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db():
    """Session-scoped Supabase client — shared across all tests, closed at end."""
    client = await get_supabase()
    yield client
    await close_supabase()
