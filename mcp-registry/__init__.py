"""MCP Registry - Centralized registry for Model Context Protocol servers."""

__version__ = "1.0.0"
__author__ = "MCP Registry Team"
__email__ = "registry@modelcontextprotocol.io"
__description__ = "Centralized registry for discovering, verifying, and monitoring MCP servers"

from .api.models import (
    Server,
    ServerRegistration,
    VerificationResult,
    HealthCheck,
    ServerMetrics,
    ServerStatus,
    HealthStatus,
    Capability
)

from .api.server import app as api_app
from .verifier.validator import ServerValidator
from .monitor.health import HealthMonitor

__all__ = [
    # Models
    "Server",
    "ServerRegistration", 
    "VerificationResult",
    "HealthCheck",
    "ServerMetrics",
    "ServerStatus",
    "HealthStatus",
    "Capability",
    
    # Components
    "api_app",
    "ServerValidator",
    "HealthMonitor"
]