"""Data models for MCP Registry."""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, HttpUrl, validator
from urllib.parse import urlparse
import re


class ServerStatus(str, Enum):
    """Server status enumeration."""
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    DEPRECATED = "deprecated"


class HealthStatus(str, Enum):
    """Health check status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class Capability(str, Enum):
    """MCP server capabilities."""
    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"
    SAMPLING = "sampling"


class ServerMetadata(BaseModel):
    """Server metadata model."""
    name: str = Field(..., description="Server name")
    description: str = Field(..., description="Server description")
    version: str = Field(..., description="Server version")
    author: str = Field(..., description="Server author")
    license: str = Field(..., description="License type")
    homepage: Optional[HttpUrl] = Field(None, description="Project homepage")
    repository: Optional[HttpUrl] = Field(None, description="Source repository")
    documentation: Optional[HttpUrl] = Field(None, description="Documentation URL")
    
    @validator('homepage', 'repository', 'documentation', pre=True)
    def validate_urls(cls, v):
        """Validate and sanitize URLs."""
        if v is None:
            return v
        
        # Convert to string for processing
        url_str = str(v)
        
        # Block potentially malicious patterns
        blocked_patterns = [
            r'javascript:',
            r'data:',
            r'vbscript:',
            r'file:',
            r'about:',
            r'chrome:',
            r'<script',
            r'onclick=',
            r'onerror=',
        ]
        
        for pattern in blocked_patterns:
            if re.search(pattern, url_str, re.IGNORECASE):
                raise ValueError(f"URL contains blocked pattern: {pattern}")
        
        # Ensure URL is from allowed domains for certain fields
        parsed = urlparse(url_str)
        
        # Repository should be from known git providers
        if 'repository' in cls.__fields__ and v == url_str:
            allowed_repo_domains = [
                'github.com',
                'gitlab.com',
                'bitbucket.org',
                'codeberg.org',
                'sr.ht'
            ]
            if parsed.hostname and not any(domain in parsed.hostname for domain in allowed_repo_domains):
                raise ValueError(f"Repository must be hosted on a known git provider")
        
        return v
    

class ServerRegistration(BaseModel):
    """Server registration request model."""
    name: str = Field(..., description="Unique server identifier", min_length=3, max_length=50)
    url: HttpUrl = Field(..., description="Server URL or repository")
    description: str = Field(..., description="Server description", min_length=10, max_length=500)
    version: str = Field(..., description="Server version", pattern=r"^\d+\.\d+\.\d+")
    capabilities: List[Capability] = Field(..., description="Server capabilities", min_items=1)
    tags: List[str] = Field(default_factory=list, description="Search tags", max_items=10)
    metadata: Optional[ServerMetadata] = Field(None, description="Additional metadata")
    
    @validator('name')
    def validate_name(cls, v):
        """Validate server name."""
        # Only allow alphanumeric, dash, and underscore
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError("Server name can only contain letters, numbers, dash, and underscore")
        return v
    
    @validator('url', pre=True)
    def validate_url(cls, v):
        """Validate and sanitize server URL."""
        url_str = str(v)
        
        # Block potentially malicious URLs
        blocked_patterns = [
            r'javascript:',
            r'data:',
            r'vbscript:',
            r'file:',
        ]
        
        for pattern in blocked_patterns:
            if re.search(pattern, url_str, re.IGNORECASE):
                raise ValueError(f"URL contains blocked pattern")
        
        return v
    
    @validator('tags')
    def validate_tags(cls, v):
        """Validate tags."""
        # Ensure tags are alphanumeric with dashes
        for tag in v:
            if not re.match(r'^[a-zA-Z0-9-]+$', tag):
                raise ValueError(f"Tag '{tag}' contains invalid characters")
            if len(tag) > 30:
                raise ValueError(f"Tag '{tag}' is too long (max 30 characters)")
        return v
    

class VerificationResult(BaseModel):
    """Verification result model."""
    server_id: str
    status: ServerStatus
    timestamp: datetime
    checks: Dict[str, bool] = Field(default_factory=dict, description="Verification checks")
    issues: List[str] = Field(default_factory=list, description="Issues found")
    score: float = Field(0.0, ge=0.0, le=100.0, description="Verification score")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")


class HealthCheck(BaseModel):
    """Health check result model."""
    server_id: str
    status: HealthStatus
    timestamp: datetime
    response_time_ms: Optional[float] = Field(None, description="Response time in milliseconds")
    uptime_percentage: Optional[float] = Field(None, description="Uptime percentage")
    last_check: datetime
    next_check: datetime
    error_count: int = Field(0, description="Recent error count")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional health details")


class ServerMetrics(BaseModel):
    """Server metrics model."""
    server_id: str
    timestamp: datetime
    downloads: int = Field(0, description="Total downloads")
    stars: int = Field(0, description="GitHub stars")
    forks: int = Field(0, description="GitHub forks")
    issues_open: int = Field(0, description="Open issues")
    issues_closed: int = Field(0, description="Closed issues")
    last_commit: Optional[datetime] = Field(None, description="Last commit date")
    contributors: int = Field(0, description="Number of contributors")
    

class Server(BaseModel):
    """Complete server model."""
    id: str = Field(..., description="Unique server ID")
    name: str = Field(..., description="Server name")
    url: HttpUrl = Field(..., description="Server URL")
    description: str = Field(..., description="Server description")
    version: str = Field(..., description="Server version")
    capabilities: List[Capability] = Field(..., description="Server capabilities")
    tags: List[str] = Field(default_factory=list, description="Search tags")
    status: ServerStatus = Field(ServerStatus.PENDING, description="Verification status")
    metadata: Optional[ServerMetadata] = Field(None, description="Server metadata")
    verification: Optional[VerificationResult] = Field(None, description="Latest verification")
    health: Optional[HealthCheck] = Field(None, description="Latest health check")
    metrics: Optional[ServerMetrics] = Field(None, description="Server metrics")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SearchFilters(BaseModel):
    """Search filter parameters."""
    query: Optional[str] = Field(None, description="Search query")
    capabilities: Optional[List[Capability]] = Field(None, description="Filter by capabilities")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    status: Optional[ServerStatus] = Field(None, description="Filter by status")
    min_score: Optional[float] = Field(None, ge=0.0, le=100.0, description="Minimum verification score")
    

class SearchResult(BaseModel):
    """Search result model."""
    servers: List[Server]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool