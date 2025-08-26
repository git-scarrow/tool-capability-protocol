"""Batch health monitoring system for efficient server checks."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
import aiohttp
from collections import defaultdict

from ..api.models import Server, HealthCheck, HealthStatus

logger = logging.getLogger(__name__)


class BatchHealthMonitor:
    """Efficient batch health monitoring for large numbers of servers."""
    
    def __init__(
        self,
        batch_size: int = 10,
        check_interval: int = 300,
        max_concurrent: int = 5
    ):
        """
        Initialize batch health monitor.
        
        Args:
            batch_size: Number of servers to check in each batch
            check_interval: Seconds between health check cycles
            max_concurrent: Maximum concurrent health checks
        """
        self.batch_size = batch_size
        self.check_interval = check_interval
        self.max_concurrent = max_concurrent
        self.session: Optional[aiohttp.ClientSession] = None
        self.servers: Dict[str, Server] = {}
        self.health_results: Dict[str, HealthCheck] = {}
        self.running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
    async def start(self):
        """Start the batch health monitoring system."""
        logger.info(f"Starting batch health monitor (batch_size={self.batch_size})")
        self.running = True
        self.session = aiohttp.ClientSession()
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
    async def stop(self):
        """Stop the batch health monitoring system."""
        logger.info("Stopping batch health monitor")
        self.running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        if self.session:
            await self.session.close()
    
    def add_server(self, server: Server):
        """Add a server to batch monitoring."""
        self.servers[server.id] = server
        logger.debug(f"Added server {server.name} to batch monitoring")
    
    def remove_server(self, server_id: str):
        """Remove a server from batch monitoring."""
        if server_id in self.servers:
            del self.servers[server_id]
            if server_id in self.health_results:
                del self.health_results[server_id]
            logger.debug(f"Removed server {server_id} from batch monitoring")
    
    def get_health(self, server_id: str) -> Optional[HealthCheck]:
        """Get latest health check result for a server."""
        return self.health_results.get(server_id)
    
    def get_all_health(self) -> Dict[str, HealthCheck]:
        """Get all health check results."""
        return self.health_results.copy()
    
    async def _monitor_loop(self):
        """Main monitoring loop that processes servers in batches."""
        while self.running:
            try:
                await self._check_all_servers()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in batch monitoring loop: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def _check_all_servers(self):
        """Check all servers in batches."""
        servers_list = list(self.servers.values())
        
        if not servers_list:
            return
        
        logger.info(f"Starting batch health checks for {len(servers_list)} servers")
        
        # Create batches
        batches = [
            servers_list[i:i + self.batch_size]
            for i in range(0, len(servers_list), self.batch_size)
        ]
        
        # Process batches
        for batch_num, batch in enumerate(batches, 1):
            logger.debug(f"Processing batch {batch_num}/{len(batches)} with {len(batch)} servers")
            
            # Check servers in batch concurrently with semaphore
            tasks = [
                self._check_server_with_semaphore(server)
                for server in batch
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Store results
            for server, result in zip(batch, results):
                if isinstance(result, HealthCheck):
                    self.health_results[server.id] = result
                elif isinstance(result, Exception):
                    logger.error(f"Health check failed for {server.name}: {result}")
                    # Store error result
                    self.health_results[server.id] = HealthCheck(
                        server_id=server.id,
                        status=HealthStatus.UNKNOWN,
                        timestamp=datetime.utcnow(),
                        last_check=datetime.utcnow(),
                        next_check=datetime.utcnow() + timedelta(seconds=self.check_interval),
                        error_count=1,
                        details={"error": str(result)}
                    )
    
    async def _check_server_with_semaphore(self, server: Server) -> HealthCheck:
        """Check a single server with concurrency control."""
        async with self._semaphore:
            return await self._check_server(server)
    
    async def _check_server(self, server: Server) -> HealthCheck:
        """Perform health check on a single server."""
        start_time = datetime.utcnow()
        status = HealthStatus.UNKNOWN
        response_time = None
        error_count = 0
        details = {}
        
        try:
            if "github.com" in str(server.url):
                status, response_time = await self._check_github_health(server)
            else:
                status, response_time = await self._check_http_health(server)
            
            details["last_successful_check"] = datetime.utcnow().isoformat()
            
        except Exception as e:
            logger.debug(f"Health check error for {server.name}: {e}")
            status = HealthStatus.UNHEALTHY
            error_count = 1
            details["error"] = str(e)[:100]  # Limit error message size
        
        # Calculate simple uptime (would be more sophisticated in production)
        uptime = 95.0 if status == HealthStatus.HEALTHY else 75.0
        
        return HealthCheck(
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
    
    async def _check_github_health(self, server: Server) -> tuple[HealthStatus, Optional[float]]:
        """Check health of a GitHub-hosted server."""
        if not self.session:
            return HealthStatus.UNKNOWN, None
        
        try:
            url = str(server.url)
            parts = url.replace("https://github.com/", "").split("/")
            
            if len(parts) >= 2:
                api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
                
                start = datetime.utcnow()
                async with self.session.get(api_url, timeout=5) as response:
                    response_time = (datetime.utcnow() - start).total_seconds() * 1000
                    
                    if response.status == 200:
                        return HealthStatus.HEALTHY, response_time
                    elif response.status == 404:
                        return HealthStatus.UNHEALTHY, response_time
                    else:
                        return HealthStatus.DEGRADED, response_time
                        
        except asyncio.TimeoutError:
            return HealthStatus.UNHEALTHY, None
        except Exception:
            return HealthStatus.UNKNOWN, None
    
    async def _check_http_health(self, server: Server) -> tuple[HealthStatus, Optional[float]]:
        """Check health of an HTTP-accessible server."""
        if not self.session:
            return HealthStatus.UNKNOWN, None
        
        try:
            url = str(server.url)
            
            start = datetime.utcnow()
            async with self.session.get(url, timeout=5) as response:
                response_time = (datetime.utcnow() - start).total_seconds() * 1000
                
                if response.status < 400:
                    return HealthStatus.HEALTHY, response_time
                elif response.status < 500:
                    return HealthStatus.DEGRADED, response_time
                else:
                    return HealthStatus.UNHEALTHY, response_time
                    
        except asyncio.TimeoutError:
            return HealthStatus.UNHEALTHY, None
        except Exception:
            return HealthStatus.UNKNOWN, None
    
    def get_statistics(self) -> Dict:
        """Get monitoring statistics."""
        total_servers = len(self.servers)
        healthy = sum(1 for h in self.health_results.values() if h.status == HealthStatus.HEALTHY)
        degraded = sum(1 for h in self.health_results.values() if h.status == HealthStatus.DEGRADED)
        unhealthy = sum(1 for h in self.health_results.values() if h.status == HealthStatus.UNHEALTHY)
        unknown = sum(1 for h in self.health_results.values() if h.status == HealthStatus.UNKNOWN)
        
        avg_response_time = None
        response_times = [
            h.response_time_ms for h in self.health_results.values()
            if h.response_time_ms is not None
        ]
        if response_times:
            avg_response_time = sum(response_times) / len(response_times)
        
        return {
            "total_servers": total_servers,
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "unknown": unknown,
            "health_percentage": (healthy / total_servers * 100) if total_servers > 0 else 0,
            "average_response_time_ms": avg_response_time,
            "batch_size": self.batch_size,
            "check_interval": self.check_interval,
            "max_concurrent": self.max_concurrent
        }