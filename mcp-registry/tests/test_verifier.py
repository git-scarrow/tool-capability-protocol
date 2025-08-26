"""Tests for server verification system."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from mcp_registry.verifier.validator import ServerValidator
from mcp_registry.api.models import Server, ServerStatus, Capability


@pytest.fixture
def mock_server():
    """Create a mock server for testing."""
    return Server(
        id="test-server-id",
        name="test-server",
        url="https://github.com/user/test-server",
        description="A test MCP server",
        version="1.0.0",
        capabilities=[Capability.TOOL, Capability.RESOURCE],
        tags=["test"],
        status=ServerStatus.PENDING,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )


class TestServerValidator:
    """Test suite for server validation."""
    
    @pytest.fixture
    async def validator(self):
        """Create validator instance."""
        validator = ServerValidator()
        validator.session = AsyncMock()
        return validator
    
    async def test_verify_server_success(self, validator: ServerValidator, mock_server: Server):
        """Test successful server verification."""
        # Mock all verification checks to pass
        validator._check_repository = AsyncMock(return_value={"success": True})
        validator._check_structure = AsyncMock(return_value={"success": True})
        validator._check_mcp_compliance = AsyncMock(return_value={"success": True})
        validator._security_scan = AsyncMock(return_value={"success": True, "issues": []})
        validator._check_license = AsyncMock(return_value={"success": True})
        validator._check_documentation = AsyncMock(return_value={"success": True})
        
        result = await validator.verify(mock_server)
        
        assert result.server_id == mock_server.id
        assert result.status == ServerStatus.VERIFIED
        assert result.score == 100.0
        assert len(result.issues) == 0
        assert all(result.checks.values())
    
    async def test_verify_server_partial_failure(self, validator: ServerValidator, mock_server: Server):
        """Test server verification with some failures."""
        # Mock some checks to fail
        validator._check_repository = AsyncMock(return_value={"success": True})
        validator._check_structure = AsyncMock(return_value={
            "success": False, 
            "issue": "Missing package.json"
        })
        validator._check_mcp_compliance = AsyncMock(return_value={"success": True})
        validator._security_scan = AsyncMock(return_value={
            "success": False, 
            "issues": ["Security issue found"]
        })
        validator._check_license = AsyncMock(return_value={"success": True})
        validator._check_documentation = AsyncMock(return_value={"success": True})
        
        result = await validator.verify(mock_server)
        
        assert result.server_id == mock_server.id
        assert result.status == ServerStatus.VERIFIED  # Still passes with 66% score
        assert 60 <= result.score < 80
        assert len(result.issues) == 2
        assert not result.checks["valid_structure"]
        assert not result.checks["security_passed"]
    
    async def test_verify_server_major_failure(self, validator: ServerValidator, mock_server: Server):
        """Test server verification with major failures."""
        # Mock most checks to fail
        validator._check_repository = AsyncMock(return_value={
            "success": False,
            "issue": "Repository not accessible"
        })
        validator._check_structure = AsyncMock(return_value={
            "success": False,
            "issue": "Invalid structure"
        })
        validator._check_mcp_compliance = AsyncMock(return_value={
            "success": False,
            "issue": "Not MCP compliant"
        })
        validator._security_scan = AsyncMock(return_value={
            "success": False,
            "issues": ["Multiple security issues"]
        })
        validator._check_license = AsyncMock(return_value={
            "success": False,
            "issue": "No license"
        })
        validator._check_documentation = AsyncMock(return_value={
            "success": False,
            "issue": "No documentation"
        })
        
        result = await validator.verify(mock_server)
        
        assert result.server_id == mock_server.id
        assert result.status == ServerStatus.FAILED
        assert result.score == 0.0
        assert len(result.issues) >= 6
        assert not any(result.checks.values())
    
    async def test_check_repository_github_success(self, validator: ServerValidator):
        """Test GitHub repository check success."""
        # Mock successful GitHub API response
        mock_response = AsyncMock()
        mock_response.status = 200
        validator.session.get.return_value.__aenter__.return_value = mock_response
        
        server = Server(
            id="test",
            name="test",
            url="https://github.com/user/repo",
            description="test",
            version="1.0.0",
            capabilities=[Capability.TOOL],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._check_repository(server)
        assert result["success"] is True
    
    async def test_check_repository_github_failure(self, validator: ServerValidator):
        """Test GitHub repository check failure."""
        # Mock failed GitHub API response
        mock_response = AsyncMock()
        mock_response.status = 404
        validator.session.get.return_value.__aenter__.return_value = mock_response
        
        server = Server(
            id="test",
            name="test",
            url="https://github.com/user/nonexistent",
            description="test",
            version="1.0.0",
            capabilities=[Capability.TOOL],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._check_repository(server)
        assert result["success"] is False
        assert "not accessible" in result["issue"].lower()
    
    async def test_check_mcp_compliance(self, validator: ServerValidator):
        """Test MCP compliance check."""
        # Test with capabilities
        server_with_caps = Server(
            id="test",
            name="test",
            url="https://example.com",
            description="test",
            version="1.0.0",
            capabilities=[Capability.TOOL, Capability.RESOURCE],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._check_mcp_compliance(server_with_caps)
        assert result["success"] is True
        
        # Test without capabilities
        server_no_caps = Server(
            id="test",
            name="test",
            url="https://example.com",
            description="test",
            version="1.0.0",
            capabilities=[],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._check_mcp_compliance(server_no_caps)
        assert result["success"] is False
        assert "capabilities" in result["issue"].lower()
    
    async def test_check_structure_github_valid(self, validator: ServerValidator):
        """Test structure check for valid GitHub repository."""
        # Mock successful responses for required files
        mock_response_200 = AsyncMock()
        mock_response_200.status = 200
        mock_response_200.text.return_value = asyncio.coroutine(
            lambda: '{"dependencies": {"@modelcontextprotocol/sdk": "1.0.0"}}'
        )()
        
        validator.session.get.return_value.__aenter__.return_value = mock_response_200
        
        server = Server(
            id="test",
            name="test",
            url="https://github.com/user/mcp-server",
            description="test",
            version="1.0.0",
            capabilities=[Capability.TOOL],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._check_structure(server)
        assert result["success"] is True
    
    async def test_security_scan_basic(self, validator: ServerValidator):
        """Test basic security scan."""
        server = Server(
            id="test",
            name="test",
            url="https://github.com/user/secure-server",
            description="test",
            version="1.0.0",
            capabilities=[Capability.TOOL],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._security_scan(server)
        # Basic scan should pass for now
        assert result["success"] is True
        assert len(result["issues"]) == 0
    
    async def test_check_license_with_metadata(self, validator: ServerValidator, mock_server: Server):
        """Test license check with metadata."""
        from mcp_registry.api.models import ServerMetadata
        
        mock_server.metadata = ServerMetadata(
            name="test",
            description="test",
            version="1.0.0",
            author="Test Author",
            license="MIT"
        )
        
        result = await validator._check_license(mock_server)
        assert result["success"] is True
    
    async def test_check_documentation_github(self, validator: ServerValidator):
        """Test documentation check for GitHub repository."""
        # Mock README API response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = asyncio.coroutine(
            lambda: {"size": 1000}  # Substantial README
        )()
        
        validator.session.get.return_value.__aenter__.return_value = mock_response
        
        server = Server(
            id="test",
            name="test",
            url="https://github.com/user/documented-server",
            description="test",
            version="1.0.0",
            capabilities=[Capability.TOOL],
            tags=[],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        result = await validator._check_documentation(server)
        assert result["success"] is True