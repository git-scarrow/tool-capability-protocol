"""Tests for MCP Registry API."""

import pytest
import asyncio
from httpx import AsyncClient
from datetime import datetime
from unittest.mock import AsyncMock, patch

from mcp_registry.api.server import app
from mcp_registry.api.models import (
    ServerRegistration, ServerMetadata, Capability, ServerStatus
)


@pytest.fixture
async def client():
    """Create test client."""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


class TestAPI:
    """Test suite for API endpoints."""
    
    async def test_root_endpoint(self, client: AsyncClient):
        """Test root endpoint returns API info."""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "MCP Registry API"
        assert data["version"] == "1.0.0"
        assert "endpoints" in data
    
    async def test_health_endpoint(self, client: AsyncClient):
        """Test health endpoint returns status."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
    
    async def test_list_servers_empty(self, client: AsyncClient):
        """Test listing servers when registry is empty."""
        response = await client.get("/servers")
        assert response.status_code == 200
        data = response.json()
        assert data["servers"] == []
        assert data["total"] == 0
        assert data["page"] == 1
    
    async def test_register_server(self, client: AsyncClient):
        """Test server registration."""
        server_data = {
            "name": "test-server",
            "url": "https://github.com/user/test-server",
            "description": "A test MCP server",
            "version": "1.0.0",
            "capabilities": ["tool", "resource"],
            "tags": ["test", "example"],
            "metadata": {
                "name": "test-server",
                "description": "A test server",
                "version": "1.0.0",
                "author": "Test Author",
                "license": "MIT"
            }
        }
        
        response = await client.post("/servers", json=server_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == "test-server"
        assert data["status"] == "pending"
        assert "id" in data
        assert "created_at" in data
    
    async def test_register_duplicate_server(self, client: AsyncClient):
        """Test registering duplicate server fails."""
        server_data = {
            "name": "duplicate-server",
            "url": "https://github.com/user/duplicate-server",
            "description": "A duplicate server",
            "version": "1.0.0",
            "capabilities": ["tool"],
            "tags": []
        }
        
        # First registration should succeed
        response1 = await client.post("/servers", json=server_data)
        assert response1.status_code == 200
        
        # Second registration should fail
        response2 = await client.post("/servers", json=server_data)
        assert response2.status_code == 409
        assert "already exists" in response2.json()["detail"]
    
    async def test_get_server(self, client: AsyncClient):
        """Test getting specific server."""
        # First register a server
        server_data = {
            "name": "get-test-server",
            "url": "https://github.com/user/get-test-server",
            "description": "Server for get test",
            "version": "1.0.0",
            "capabilities": ["resource"],
            "tags": ["test"]
        }
        
        reg_response = await client.post("/servers", json=server_data)
        server_id = reg_response.json()["id"]
        
        # Get the server
        response = await client.get(f"/servers/{server_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert data["name"] == "get-test-server"
        assert data["id"] == server_id
    
    async def test_get_nonexistent_server(self, client: AsyncClient):
        """Test getting nonexistent server returns 404."""
        response = await client.get("/servers/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    
    async def test_update_server(self, client: AsyncClient):
        """Test updating server information."""
        # First register a server
        server_data = {
            "name": "update-test-server",
            "url": "https://github.com/user/update-test-server",
            "description": "Server for update test",
            "version": "1.0.0",
            "capabilities": ["tool"],
            "tags": ["test"]
        }
        
        reg_response = await client.post("/servers", json=server_data)
        server_id = reg_response.json()["id"]
        
        # Update the server
        updated_data = {
            "name": "update-test-server",
            "url": "https://github.com/user/update-test-server",
            "description": "Updated description",
            "version": "1.1.0",
            "capabilities": ["tool", "resource"],
            "tags": ["test", "updated"]
        }
        
        response = await client.put(f"/servers/{server_id}", json=updated_data)
        assert response.status_code == 200
        
        data = response.json()
        assert data["description"] == "Updated description"
        assert data["version"] == "1.1.0"
        assert len(data["capabilities"]) == 2
    
    async def test_delete_server(self, client: AsyncClient):
        """Test deleting server."""
        # First register a server
        server_data = {
            "name": "delete-test-server",
            "url": "https://github.com/user/delete-test-server",
            "description": "Server for delete test",
            "version": "1.0.0",
            "capabilities": ["prompt"],
            "tags": ["test"]
        }
        
        reg_response = await client.post("/servers", json=server_data)
        server_id = reg_response.json()["id"]
        
        # Delete the server
        response = await client.delete(f"/servers/{server_id}")
        assert response.status_code == 200
        assert "deleted successfully" in response.json()["message"]
        
        # Verify server is gone
        get_response = await client.get(f"/servers/{server_id}")
        assert get_response.status_code == 404
    
    async def test_search_servers(self, client: AsyncClient):
        """Test server search functionality."""
        # Register multiple servers
        servers = [
            {
                "name": "search-server-1",
                "url": "https://github.com/user/search-server-1",
                "description": "First search server",
                "version": "1.0.0",
                "capabilities": ["tool"],
                "tags": ["search", "first"]
            },
            {
                "name": "search-server-2",
                "url": "https://github.com/user/search-server-2", 
                "description": "Second search server",
                "version": "1.0.0",
                "capabilities": ["resource"],
                "tags": ["search", "second"]
            }
        ]
        
        for server in servers:
            await client.post("/servers", json=server)
        
        # Test query search
        response = await client.get("/servers?query=First")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert any("First" in server["description"] for server in data["servers"])
        
        # Test capability filter
        response = await client.get("/servers?capabilities=tool")
        assert response.status_code == 200
        data = response.json()
        assert all("tool" in server["capabilities"] for server in data["servers"])
        
        # Test tag filter
        response = await client.get("/servers?tags=first")
        assert response.status_code == 200
        data = response.json()
        assert all("first" in server["tags"] for server in data["servers"])
    
    async def test_pagination(self, client: AsyncClient):
        """Test pagination in server listing."""
        # Register multiple servers
        for i in range(25):  # More than default page size
            server_data = {
                "name": f"pagination-server-{i}",
                "url": f"https://github.com/user/pagination-server-{i}",
                "description": f"Server {i} for pagination test",
                "version": "1.0.0",
                "capabilities": ["tool"],
                "tags": ["pagination"]
            }
            await client.post("/servers", json=server_data)
        
        # Test first page
        response = await client.get("/servers?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data["servers"]) == 10
        assert data["page"] == 1
        assert data["has_next"] is True
        assert data["has_prev"] is False
        
        # Test second page
        response = await client.get("/servers?page=2&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert data["has_prev"] is True
    
    @patch('mcp_registry.verifier.validator.ServerValidator.verify')
    async def test_verify_server(self, mock_verify, client: AsyncClient):
        """Test server verification endpoint."""
        # Mock verification result
        mock_verify.return_value = AsyncMock()
        
        # Register a server
        server_data = {
            "name": "verify-test-server",
            "url": "https://github.com/user/verify-test-server",
            "description": "Server for verification test",
            "version": "1.0.0",
            "capabilities": ["tool"],
            "tags": ["test"]
        }
        
        reg_response = await client.post("/servers", json=server_data)
        server_id = reg_response.json()["id"]
        
        # Trigger verification
        response = await client.post(f"/servers/{server_id}/verify")
        assert response.status_code == 200
        
        data = response.json()
        assert "server_id" in data
        assert "status" in data
    
    async def test_stats_endpoint(self, client: AsyncClient):
        """Test registry statistics endpoint."""
        response = await client.get("/stats")
        assert response.status_code == 200
        
        data = response.json()
        assert "total_servers" in data
        assert "verified_servers" in data
        assert "healthy_servers" in data
        assert "categories" in data
        assert "timestamp" in data