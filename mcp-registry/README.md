# MCP Verified Registry

A centralized registry for discovering, verifying, and monitoring MCP (Model Context Protocol) servers.

## Problem Statement

Currently, there's no central discovery mechanism for MCP servers. Users must:
- Manually find servers through various channels
- Trust servers without verification
- Lack visibility into server health and compatibility

## Solution

This registry provides:
- **Discovery**: Central searchable catalog of MCP servers
- **Verification**: Automated verification of server functionality
- **Health Monitoring**: Real-time health checks and status
- **Metadata**: Rich metadata including capabilities, version compatibility, and usage statistics

## Features

### Core Registry Features
- Server registration with metadata
- Search and filtering capabilities
- Version compatibility tracking
- Category/tag organization

### Verification System
- Automated capability testing
- Security scanning
- Code quality checks
- License verification

### Health Monitoring
- Periodic health checks
- Uptime tracking
- Performance metrics
- Error rate monitoring

## Architecture

```
mcp-registry/
├── api/                 # Registry API server
│   ├── server.py       # FastAPI application
│   ├── models.py       # Data models
│   ├── database.py     # Database connections
│   └── routes/         # API endpoints
├── verifier/           # Verification system
│   ├── scanner.py      # Security scanner
│   ├── tester.py       # Capability tester
│   └── validator.py    # Metadata validator
├── monitor/            # Health monitoring
│   ├── health.py       # Health check system
│   ├── metrics.py      # Metrics collection
│   └── alerts.py       # Alert system
├── web/                # Web interface
│   └── index.html      # Registry UI
└── tests/              # Test suite
```

## API Endpoints

### Registry Operations
- `GET /servers` - List all servers with filtering
- `GET /servers/{id}` - Get server details
- `POST /servers` - Register new server
- `PUT /servers/{id}` - Update server metadata
- `DELETE /servers/{id}` - Remove server

### Verification
- `POST /servers/{id}/verify` - Trigger verification
- `GET /servers/{id}/verification` - Get verification status

### Health
- `GET /servers/{id}/health` - Get health status
- `GET /servers/{id}/metrics` - Get performance metrics

## Getting Started

### Installation

```bash
pip install mcp-registry
```

### Running the Registry

```bash
# Start the API server
python -m mcp_registry.api

# Start the health monitor
python -m mcp_registry.monitor

# Start the verifier
python -m mcp_registry.verifier
```

### Registering a Server

```python
import requests

server_data = {
    "name": "my-mcp-server",
    "url": "https://github.com/user/my-mcp-server",
    "description": "Description of capabilities",
    "version": "1.0.0",
    "capabilities": ["tool", "resource", "prompt"],
    "tags": ["productivity", "development"]
}

response = requests.post("http://localhost:8000/servers", json=server_data)
```

## Evidence

Based on strong market signals:
- GitHub Issue #142: Server discovery problem
- Forum discussions requesting central registry
- VS Code telemetry showing manual configuration pain
- Enterprise requirements for verified servers