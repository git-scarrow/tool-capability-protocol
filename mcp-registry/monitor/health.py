"""Health monitoring system for MCP servers."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, OrderedDict
import aiohttp
from asyncio import Task
from collections import OrderedDict

from ..api.models import Server, HealthCheck, HealthStatus

logger = logging.getLogger(__name__)


class LRUCache:
    """Simple LRU cache implementation."""
    
    def __init__(self, max_size: int = 1000):
        self.cache: OrderedDict[str, tuple[HealthCheck, datetime]] = OrderedDict()
        self.max_size = max_size
        self.ttl_seconds = 3600  # 1 hour TTL
    
    def get(self, key: str) -> Optional[HealthCheck]:
        """Get item from cache if not expired."""
        if key not in self.cache:
            return None
        
        value, timestamp = self.cache[key]
        # Check if expired
        if (datetime.utcnow() - timestamp).total_seconds() > self.ttl_seconds:
            del self.cache[key]
            return None
        
        # Move to end (most recently used)
        self.cache.move_to_end(key)
        return value
    
    def put(self, key: str, value: HealthCheck):
        """Add item to cache."""
        # Remove if already exists
        if key in self.cache:
            del self.cache[key]
        
        # Add to end
        self.cache[key] = (value, datetime.utcnow())
        
        # Evict oldest if over limit
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
    
    def clear(self):
        """Clear the cache."""
        self.cache.clear()


class HealthMonitor:
    """Monitors health of registered MCP servers."""
    
    def __init__(self, check_interval: int = 300, max_cache_size: int = 1000):  # 5 minutes default
        self.check_interval = check_interval
        self.session: Optional[aiohttp.ClientSession] = None
        self.monitoring_tasks: Dict[str, Task] = {}
        self.health_cache = LRUCache(max_cache_size)
        self.running = False
        
    async def start(self):
        """Start the health monitoring system."""
        logger.info("Starting health monitor")
        self.running = True
        self.session = aiohttp.ClientSession()
        
    async def stop(self):
        """Stop the health monitoring system."""
        logger.info("Stopping health monitor")
        self.running = False
        
        # Cancel all monitoring tasks
        for task in self.monitoring_tasks.values():
            task.cancel()
        
        # Wait for tasks to complete
        if self.monitoring_tasks:
            await asyncio.gather(*self.monitoring_tasks.values(), return_exceptions=True)
        
        # Close session
        if self.session:
            await self.session.close()
            
    def status(self) -> Dict:
        """Get monitor status."""
        return {
            "running": self.running,
            "monitored_servers": len(self.monitoring_tasks),
            "check_interval": self.check_interval
        }
    
    async def add_server(self, server: Server):
        """Add a server to health monitoring."""
        if server.id in self.monitoring_tasks:
            logger.debug(f"Server {server.name} already being monitored")
            return
            
        task = asyncio.create_task(self._monitor_server(server))
        self.monitoring_tasks[server.id] = task
        logger.info(f"Added server {server.name} to health monitoring")
        
    async def remove_server(self, server_id: str):
        """Remove a server from health monitoring."""
        if server_id in self.monitoring_tasks:
            self.monitoring_tasks[server_id].cancel()
            del self.monitoring_tasks[server_id]
            logger.info(f"Removed server {server_id} from health monitoring")
            
    async def check_server(self, server: Server) -> HealthCheck:
        """Perform a health check on a server."""
        logger.debug(f"Checking health of server: {server.name}")
        
        # Check cache first
        cached = self.health_cache.get(server.id)
        if cached:
            # Return cached result if less than 1 minute old
            if (datetime.utcnow() - cached.timestamp).total_seconds() < 60:
                return cached
        
        status = HealthStatus.UNKNOWN
        response_time = None
        error_count = 0
        details = {}
        
        try:
            # Perform health check based on server type
            if "github.com" in str(server.url):
                # For GitHub repos, check repository status
                status, response_time = await self._check_github_health(server)
            else:
                # For other servers, try to ping endpoint
                status, response_time = await self._check_http_health(server)
            
            details["last_successful_check"] = datetime.utcnow().isoformat()
            
        except Exception as e:
            logger.error(f"Health check failed for {server.name}: {e}")
            status = HealthStatus.UNHEALTHY
            error_count = 1
            details["error"] = str(e)
        
        # Calculate uptime percentage (simplified)
        uptime = 95.0 if status == HealthStatus.HEALTHY else 75.0
        
        health_check = HealthCheck(
            server_id=server.id,
            status=status,
            timestamp=datetime.utcnow(),
            response_time_ms=response_time,
            uptime_percentage=uptime,
            last_check=datetime.utcnow(),
            next_check=datetime.utcnow() + timedelta(seconds=self.check_interval),
            error_count=error_count,
            details=details
        )
        
        # Update cache
        self.health_cache.put(server.id, health_check)
        
        return health_check
    
    async def _monitor_server(self, server: Server):
        """Background task to monitor a server's health."""
        while self.running:
            try:
                health_check = await self.check_server(server)
                logger.debug(f"Health check for {server.name}: {health_check.status}")
                
                # Store result (in production, would update database)
                self.health_cache.put(server.id, health_check)
                
                # Wait for next check
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error monitoring server {server.name}: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def _check_github_health(self, server: Server) -> tuple[HealthStatus, float]:
        """Check health of a GitHub-hosted server."""
        if not self.session:
            self.session = aiohttp.ClientSession()
            
        try:
            url = str(server.url)
            parts = url.replace("https://github.com/", "").split("/")
            
            if len(parts) >= 2:
                api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
                
                start_time = datetime.utcnow()
                async with self.session.get(api_url) as response:
                    response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        # Check repository activity
                        last_push = data.get("pushed_at")
                        if last_push:
                            last_push_date = datetime.fromisoformat(last_push.replace("Z", "+00:00"))
                            days_since_push = (datetime.utcnow() - last_push_date.replace(tzinfo=None)).days
                            
                            if days_since_push < 30:
                                return HealthStatus.HEALTHY, response_time
                            elif days_since_push < 90:
                                return HealthStatus.DEGRADED, response_time
                            else:
                                return HealthStatus.UNHEALTHY, response_time
                        
                        return HealthStatus.HEALTHY, response_time
                    elif response.status == 404:
                        return HealthStatus.UNHEALTHY, response_time
                    else:
                        return HealthStatus.DEGRADED, response_time
                        
        except Exception as e:
            logger.error(f"GitHub health check error: {e}")
            return HealthStatus.UNKNOWN, None
    
    async def _check_http_health(self, server: Server) -> tuple[HealthStatus, float]:
        """Check health of an HTTP-accessible server."""
        if not self.session:
            self.session = aiohttp.ClientSession()
            
        try:
            url = str(server.url)
            
            # Try common health endpoints
            health_endpoints = ["/health", "/api/health", "/status", "/.well-known/mcp"]
            
            for endpoint in health_endpoints:
                check_url = url.rstrip("/") + endpoint
                
                try:
                    start_time = datetime.utcnow()
                    async with self.session.get(check_url, timeout=5) as response:
                        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                        
                        if response.status == 200:
                            return HealthStatus.HEALTHY, response_time
                        elif response.status < 500:
                            continue  # Try next endpoint
                        else:
                            return HealthStatus.DEGRADED, response_time
                except:
                    continue
            
            # If no health endpoint found, just check base URL
            start_time = datetime.utcnow()
            async with self.session.get(url, timeout=5) as response:
                response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                if response.status < 400:
                    return HealthStatus.HEALTHY, response_time
                elif response.status < 500:
                    return HealthStatus.DEGRADED, response_time
                else:
                    return HealthStatus.UNHEALTHY, response_time
                    
        except asyncio.TimeoutError:
            return HealthStatus.UNHEALTHY, None
        except Exception as e:
            logger.error(f"HTTP health check error: {e}")
            return HealthStatus.UNKNOWN, None