"""Database layer for MCP Registry."""

import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import aiofiles
import logging

from .models import Server, SearchFilters, SearchResult, ServerStatus, HealthStatus

logger = logging.getLogger(__name__)


class Database:
    """Simple file-based database for MCP Registry."""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.servers: Dict[str, Server] = {}
        self.lock = asyncio.Lock()
        
    async def initialize(self):
        """Initialize the database."""
        # Create data directory if it doesn't exist
        self.data_dir.mkdir(exist_ok=True)
        
        # Load existing data
        await self.load_data()
        logger.info(f"Database initialized with {len(self.servers)} servers")
        
    async def close(self):
        """Close database connections."""
        # Save any pending changes
        await self.save_data()
        
    async def health_check(self) -> Dict:
        """Check database health."""
        return {
            "status": "healthy",
            "servers_count": len(self.servers),
            "data_dir": str(self.data_dir)
        }
        
    async def load_data(self):
        """Load data from disk."""
        data_file = self.data_dir / "servers.json"
        
        if data_file.exists():
            try:
                async with aiofiles.open(data_file, "r") as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    for server_data in data:
                        server = Server(**server_data)
                        self.servers[server.id] = server
                        
            except Exception as e:
                logger.error(f"Failed to load data: {e}")
                
    async def save_data(self):
        """Save data to disk."""
        async with self.lock:
            data_file = self.data_dir / "servers.json"
            
            try:
                # Convert servers to JSON
                data = [server.dict() for server in self.servers.values()]
                
                # Write to file
                async with aiofiles.open(data_file, "w") as f:
                    await f.write(json.dumps(data, indent=2, default=str))
                    
            except Exception as e:
                logger.error(f"Failed to save data: {e}")
                
    async def get_server(self, server_id: str) -> Optional[Server]:
        """Get a server by ID."""
        return self.servers.get(server_id)
        
    async def find_server_by_name(self, name: str) -> Optional[Server]:
        """Find a server by name."""
        for server in self.servers.values():
            if server.name == name:
                return server
        return None
        
    async def create_server(self, server: Server) -> Server:
        """Create a new server."""
        async with self.lock:
            self.servers[server.id] = server
            await self.save_data()
        return server
        
    async def update_server(self, server: Server) -> Server:
        """Update an existing server."""
        async with self.lock:
            self.servers[server.id] = server
            await self.save_data()
        return server
        
    async def delete_server(self, server_id: str) -> bool:
        """Delete a server."""
        async with self.lock:
            if server_id in self.servers:
                del self.servers[server_id]
                await self.save_data()
                return True
        return False
        
    async def search_servers(
        self,
        filters: SearchFilters,
        page: int,
        page_size: int
    ) -> SearchResult:
        """Search servers with filters."""
        # Filter servers
        filtered_servers = []
        
        for server in self.servers.values():
            # Apply filters
            if filters.query:
                query_lower = filters.query.lower()
                if not (
                    query_lower in server.name.lower() or
                    query_lower in server.description.lower() or
                    any(query_lower in tag for tag in server.tags)
                ):
                    continue
                    
            if filters.capabilities:
                if not any(cap in server.capabilities for cap in filters.capabilities):
                    continue
                    
            if filters.tags:
                if not any(tag in server.tags for tag in filters.tags):
                    continue
                    
            if filters.status:
                if server.status != filters.status:
                    continue
                    
            if filters.min_score:
                if not server.verification or server.verification.score < filters.min_score:
                    continue
                    
            filtered_servers.append(server)
        
        # Sort by updated_at (newest first)
        filtered_servers.sort(key=lambda s: s.updated_at, reverse=True)
        
        # Paginate
        total = len(filtered_servers)
        start = (page - 1) * page_size
        end = start + page_size
        page_servers = filtered_servers[start:end]
        
        return SearchResult(
            servers=page_servers,
            total=total,
            page=page,
            page_size=page_size,
            has_next=end < total,
            has_prev=page > 1
        )
        
    async def get_stats(self) -> Dict:
        """Get database statistics."""
        total = len(self.servers)
        verified = sum(1 for s in self.servers.values() if s.status == ServerStatus.VERIFIED)
        healthy = sum(
            1 for s in self.servers.values()
            if s.health and s.health.status == HealthStatus.HEALTHY
        )
        
        # Count categories (capabilities)
        categories = {}
        for server in self.servers.values():
            for cap in server.capabilities:
                categories[cap] = categories.get(cap, 0) + 1
        
        # Count tags
        tag_counts = {}
        for server in self.servers.values():
            for tag in server.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # Get top tags
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            "total": total,
            "verified": verified,
            "healthy": healthy,
            "categories": categories,
            "top_tags": dict(top_tags)
        }