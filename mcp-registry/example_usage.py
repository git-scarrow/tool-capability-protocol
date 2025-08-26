#!/usr/bin/env python3
"""Example usage of MCP Registry."""

import asyncio
import json
from datetime import datetime

from mcp_registry import (
    Server, ServerRegistration, ServerValidator, HealthMonitor,
    Capability, ServerStatus, HealthStatus
)
from mcp_registry.api.database import Database


async def main():
    """Demonstrate MCP Registry functionality."""
    print("🚀 MCP Registry Example Usage\n")
    
    # Initialize components
    print("Initializing registry components...")
    db = Database("example_data")
    await db.initialize()
    
    validator = ServerValidator()
    monitor = HealthMonitor(check_interval=60)  # Check every minute
    await monitor.start()
    
    # Example 1: Register a new server
    print("\n1️⃣ Registering a new MCP server...")
    
    server_reg = ServerRegistration(
        name="example-mcp-server",
        url="https://github.com/modelcontextprotocol/example-server",
        description="An example MCP server demonstrating tool capabilities",
        version="1.0.0",
        capabilities=[Capability.TOOL, Capability.RESOURCE],
        tags=["example", "tools", "demo"]
    )
    
    # Create server record
    server = Server(
        id="example-server-1",
        name=server_reg.name,
        url=server_reg.url,
        description=server_reg.description,
        version=server_reg.version,
        capabilities=server_reg.capabilities,
        tags=server_reg.tags,
        status=ServerStatus.PENDING,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    await db.create_server(server)
    print(f"✅ Registered server: {server.name}")
    
    # Example 2: Verify the server
    print("\n2️⃣ Verifying server...")
    
    async with validator as v:
        verification = await v.verify(server)
    
    print(f"✅ Verification completed:")
    print(f"   Status: {verification.status}")
    print(f"   Score: {verification.score:.1f}/100")
    print(f"   Checks passed: {sum(verification.checks.values())}/{len(verification.checks)}")
    
    if verification.issues:
        print(f"   Issues found: {len(verification.issues)}")
        for issue in verification.issues:
            print(f"     - {issue}")
    
    # Update server with verification results
    server.verification = verification
    server.status = verification.status
    await db.update_server(server)
    
    # Example 3: Health monitoring
    print("\n3️⃣ Performing health check...")
    
    health = await monitor.check_server(server)
    server.health = health
    await db.update_server(server)
    
    print(f"✅ Health check completed:")
    print(f"   Status: {health.status}")
    print(f"   Response time: {health.response_time_ms}ms" if health.response_time_ms else "   Response time: N/A")
    print(f"   Uptime: {health.uptime_percentage}%" if health.uptime_percentage else "   Uptime: N/A")
    
    # Example 4: Search and filtering
    print("\n4️⃣ Searching registry...")
    
    from mcp_registry.api.models import SearchFilters
    
    # Search by capability
    filters = SearchFilters(capabilities=[Capability.TOOL])
    results = await db.search_servers(filters, page=1, page_size=10)
    
    print(f"✅ Search results:")
    print(f"   Found {results.total} servers with 'tool' capability")
    
    for server_result in results.servers:
        print(f"   - {server_result.name} ({server_result.status})")
    
    # Example 5: Registry statistics
    print("\n5️⃣ Registry statistics...")
    
    stats = await db.get_stats()
    print(f"✅ Registry stats:")
    print(f"   Total servers: {stats['total']}")
    print(f"   Verified servers: {stats['verified']}")
    print(f"   Healthy servers: {stats['healthy']}")
    print(f"   Top capabilities: {dict(list(stats['categories'].items())[:3])}")
    
    # Example 6: Simulate multiple servers
    print("\n6️⃣ Adding more example servers...")
    
    example_servers = [
        {
            "name": "weather-mcp-server",
            "url": "https://github.com/user/weather-mcp",
            "description": "MCP server providing weather information and forecasts",
            "capabilities": [Capability.TOOL],
            "tags": ["weather", "api", "tools"]
        },
        {
            "name": "docs-mcp-server", 
            "url": "https://github.com/user/docs-mcp",
            "description": "MCP server for document indexing and search",
            "capabilities": [Capability.RESOURCE, Capability.TOOL],
            "tags": ["documents", "search", "indexing"]
        },
        {
            "name": "ai-prompts-server",
            "url": "https://github.com/user/ai-prompts-mcp",
            "description": "Collection of AI prompts and templates",
            "capabilities": [Capability.PROMPT],
            "tags": ["ai", "prompts", "templates"]
        }
    ]
    
    for i, server_data in enumerate(example_servers, 2):
        example_server = Server(
            id=f"example-server-{i}",
            name=server_data["name"],
            url=server_data["url"],
            description=server_data["description"],
            version="1.0.0",
            capabilities=server_data["capabilities"],
            tags=server_data["tags"],
            status=ServerStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        await db.create_server(example_server)
        print(f"   ✅ Added {example_server.name}")
    
    # Final stats
    print("\n📊 Final registry state:")
    final_stats = await db.get_stats()
    print(f"   Total servers: {final_stats['total']}")
    print(f"   By capability: {final_stats['categories']}")
    print(f"   Popular tags: {dict(list(final_stats['top_tags'].items())[:5])}")
    
    # Cleanup
    await monitor.stop()
    await db.close()
    
    print("\n🎉 Example completed successfully!")
    print("\n💡 Next steps:")
    print("   - Start the API server: python -m mcp_registry.api.server")
    print("   - Open web interface: http://localhost:8000/")
    print("   - API documentation: http://localhost:8000/docs")


if __name__ == "__main__":
    asyncio.run(main())