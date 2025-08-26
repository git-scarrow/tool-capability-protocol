"""Shared test configuration and fixtures."""

import pytest
import asyncio
from datetime import datetime

from mcp_registry.api.models import Server, ServerStatus, Capability


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_server():
    """Create a sample server for testing."""
    return Server(
        id="sample-server-id",
        name="sample-server",
        url="https://github.com/user/sample-server",
        description="A sample MCP server for testing",
        version="1.0.0",
        capabilities=[Capability.TOOL, Capability.RESOURCE],
        tags=["sample", "test"],
        status=ServerStatus.PENDING,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


@pytest.fixture
def sample_servers():
    """Create multiple sample servers for testing."""
    servers = []
    for i in range(5):
        server = Server(
            id=f"server-{i}",
            name=f"test-server-{i}",
            url=f"https://github.com/user/test-server-{i}",
            description=f"Test server {i}",
            version="1.0.0",
            capabilities=[Capability.TOOL],
            tags=["test", f"server-{i}"],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        servers.append(server)
    return servers