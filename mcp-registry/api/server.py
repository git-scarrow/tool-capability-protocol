"""MCP Registry API Server."""

from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timedelta
import logging
import hashlib
import time
from collections import defaultdict

from .models import (
    Server, ServerRegistration, SearchFilters, SearchResult,
    ServerStatus, VerificationResult, HealthCheck, ServerMetrics,
    Capability
)
from .database import Database
from ..verifier.validator import ServerValidator
from ..monitor.health import HealthMonitor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="MCP Registry API",
    description="Centralized registry for Model Context Protocol servers",
    version="1.0.0"
)

# Configure CORS with security restrictions
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "https://modelcontextprotocol.io",
    "https://app.modelcontextprotocol.io",
    "https://registry.modelcontextprotocol.io"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Initialize components
db = Database()
validator = ServerValidator()
health_monitor = HealthMonitor()

# Rate limiting configuration
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 100  # requests per window
rate_limit_store: Dict[str, List[float]] = defaultdict(list)

# API Key configuration (in production, load from environment or secure storage)
API_KEYS = {
    "demo-api-key-12345": "demo-user",
    # Add more API keys as needed
}

# Security header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def check_rate_limit(client_id: str) -> bool:
    """Check if client has exceeded rate limit."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    
    # Clean old entries
    rate_limit_store[client_id] = [
        timestamp for timestamp in rate_limit_store[client_id]
        if timestamp > window_start
    ]
    
    # Check limit
    if len(rate_limit_store[client_id]) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    
    # Add current request
    rate_limit_store[client_id].append(now)
    return True


async def get_api_key(api_key: Optional[str] = Depends(api_key_header)) -> Optional[str]:
    """Validate API key for protected endpoints."""
    if api_key and api_key in API_KEYS:
        return API_KEYS[api_key]
    return None


async def require_api_key(api_key: Optional[str] = Depends(api_key_header)) -> str:
    """Require valid API key for protected endpoints."""
    if not api_key or api_key not in API_KEYS:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return API_KEYS[api_key]


@app.on_event("startup")
async def startup_event():
    """Initialize database and start background tasks."""
    await db.initialize()
    await health_monitor.start()
    logger.info("MCP Registry API started")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    await health_monitor.stop()
    await db.close()
    logger.info("MCP Registry API stopped")


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "MCP Registry API",
        "version": "1.0.0",
        "status": "operational",
        "endpoints": {
            "servers": "/servers",
            "health": "/health",
            "docs": "/docs"
        }
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """API health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "database": await db.health_check(),
        "monitor": health_monitor.status()
    }


@app.get("/servers", response_model=SearchResult, tags=["Servers"])
async def list_servers(
    query: Optional[str] = Query(None, description="Search query"),
    capabilities: Optional[List[Capability]] = Query(None, description="Filter by capabilities"),
    tags: Optional[List[str]] = Query(None, description="Filter by tags"),
    status: Optional[ServerStatus] = Query(None, description="Filter by status"),
    min_score: Optional[float] = Query(None, ge=0.0, le=100.0, description="Minimum verification score"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page")
):
    """List all servers with optional filtering."""
    filters = SearchFilters(
        query=query,
        capabilities=capabilities,
        tags=tags,
        status=status,
        min_score=min_score
    )
    
    results = await db.search_servers(filters, page, page_size)
    return results


@app.get("/servers/{server_id}", response_model=Server, tags=["Servers"])
async def get_server(server_id: str):
    """Get detailed information about a specific server."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@app.post("/servers", response_model=Server, tags=["Servers"])
async def register_server(
    registration: ServerRegistration,
    background_tasks: BackgroundTasks
):
    """Register a new MCP server."""
    # Check if server already exists
    existing = await db.find_server_by_name(registration.name)
    if existing:
        raise HTTPException(status_code=409, detail="Server with this name already exists")
    
    # Create server record
    server = Server(
        id=str(uuid.uuid4()),
        name=registration.name,
        url=registration.url,
        description=registration.description,
        version=registration.version,
        capabilities=registration.capabilities,
        tags=registration.tags,
        metadata=registration.metadata,
        status=ServerStatus.PENDING,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    # Save to database
    await db.create_server(server)
    
    # Schedule verification in background
    background_tasks.add_task(verify_server_background, server.id)
    
    logger.info(f"Registered new server: {server.name}")
    return server


@app.put("/servers/{server_id}", response_model=Server, tags=["Servers"])
async def update_server(
    server_id: str,
    registration: ServerRegistration,
    background_tasks: BackgroundTasks
):
    """Update server information."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Update server fields
    server.url = registration.url
    server.description = registration.description
    server.version = registration.version
    server.capabilities = registration.capabilities
    server.tags = registration.tags
    server.metadata = registration.metadata
    server.updated_at = datetime.utcnow()
    
    # Save updates
    await db.update_server(server)
    
    # Re-verify in background
    background_tasks.add_task(verify_server_background, server.id)
    
    logger.info(f"Updated server: {server.name}")
    return server


@app.delete("/servers/{server_id}", tags=["Servers"])
async def delete_server(server_id: str):
    """Remove a server from the registry."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    await db.delete_server(server_id)
    logger.info(f"Deleted server: {server.name}")
    
    return {"message": "Server deleted successfully"}


@app.post("/servers/{server_id}/verify", response_model=VerificationResult, tags=["Verification"])
async def verify_server(
    server_id: str,
    background_tasks: BackgroundTasks,
    user: str = Depends(require_api_key)
):
    """Trigger server verification. Requires API key."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    logger.info(f"Verification requested by {user} for server {server.name}")
    
    # Run verification in background
    background_tasks.add_task(verify_server_background, server_id, user)
    
    # Return current verification status
    if server.verification:
        return server.verification
    else:
        return VerificationResult(
            server_id=server_id,
            status=ServerStatus.PENDING,
            timestamp=datetime.utcnow(),
            checks={},
            issues=[],
            score=0.0
        )


@app.get("/servers/{server_id}/verification", response_model=VerificationResult, tags=["Verification"])
async def get_verification(server_id: str):
    """Get server verification status."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    if not server.verification:
        raise HTTPException(status_code=404, detail="No verification results available")
    
    return server.verification


@app.get("/servers/{server_id}/health", response_model=HealthCheck, tags=["Health"])
async def get_server_health(server_id: str):
    """Get server health status."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # Get latest health check
    health = await health_monitor.check_server(server)
    return health


@app.get("/servers/{server_id}/metrics", response_model=ServerMetrics, tags=["Metrics"])
async def get_server_metrics(server_id: str):
    """Get server metrics and statistics."""
    server = await db.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    if not server.metrics:
        raise HTTPException(status_code=404, detail="No metrics available")
    
    return server.metrics


@app.get("/stats", tags=["Statistics"])
async def get_registry_stats(
    user: Optional[str] = Depends(get_api_key),
    x_forwarded_for: Optional[str] = Header(None)
):
    """Get registry statistics. Requires API key for detailed stats."""
    # Rate limiting based on IP or API key
    client_id = user or x_forwarded_for or "anonymous"
    
    if not check_rate_limit(client_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later."
        )
    
    stats = await db.get_stats()
    
    # Public stats (no auth required)
    basic_stats = {
        "total_servers": stats["total"],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Detailed stats (require authentication)
    if user:
        basic_stats.update({
            "verified_servers": stats["verified"],
            "healthy_servers": stats["healthy"],
            "categories": stats["categories"],
            "top_tags": stats["top_tags"]
        })
    else:
        basic_stats["message"] = "Authenticate with API key for detailed statistics"
    
    return basic_stats


async def verify_server_background(server_id: str, requested_by: str = "system"):
    """Background task to verify a server."""
    try:
        server = await db.get_server(server_id)
        if not server:
            return
        
        # Run verification
        result = await validator.verify(server)
        
        # Update server status
        server.verification = result
        server.status = result.status
        server.updated_at = datetime.utcnow()
        
        await db.update_server(server)
        logger.info(f"Verification completed for server: {server.name} (requested by: {requested_by})")
        
    except Exception as e:
        logger.error(f"Verification failed for server {server_id}: {str(e)[:100]}")  # Limit error detail exposure


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)