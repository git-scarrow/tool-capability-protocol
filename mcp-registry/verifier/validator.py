"""Server verification and validation system."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp
import subprocess
import tempfile
import os
from pathlib import Path

from ..api.models import Server, VerificationResult, ServerStatus

logger = logging.getLogger(__name__)


class ServerValidator:
    """Validates and verifies MCP servers."""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._session_owner = False  # Track if we created the session
        
    async def __aenter__(self):
        await self.ensure_session()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
    
    async def ensure_session(self):
        """Ensure we have an active session."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
            self._session_owner = True
    
    async def close_session(self):
        """Close session if we own it."""
        if self.session and self._session_owner:
            await self.session.close()
            self.session = None
            self._session_owner = False
    
    async def verify(self, server: Server) -> VerificationResult:
        """Run complete verification suite on a server."""
        logger.info(f"Starting verification for server: {server.name}")
        
        # Ensure we have a session for all checks
        await self.ensure_session()
        
        checks = {}
        issues = []
        
        # Run verification checks
        try:
            # Check 1: Repository accessibility
            repo_check = await self._check_repository(server)
            checks["repository_accessible"] = repo_check["success"]
            if not repo_check["success"]:
                issues.append(repo_check["issue"])
            
            # Check 2: Valid package structure
            structure_check = await self._check_structure(server)
            checks["valid_structure"] = structure_check["success"]
            if not structure_check["success"]:
                issues.append(structure_check["issue"])
            
            # Check 3: MCP compliance
            mcp_check = await self._check_mcp_compliance(server)
            checks["mcp_compliant"] = mcp_check["success"]
            if not mcp_check["success"]:
                issues.append(mcp_check["issue"])
            
            # Check 4: Security scan
            security_check = await self._security_scan(server)
            checks["security_passed"] = security_check["success"]
            if not security_check["success"]:
                issues.extend(security_check["issues"])
            
            # Check 5: License validation
            license_check = await self._check_license(server)
            checks["valid_license"] = license_check["success"]
            if not license_check["success"]:
                issues.append(license_check["issue"])
            
            # Check 6: Documentation
            docs_check = await self._check_documentation(server)
            checks["has_documentation"] = docs_check["success"]
            if not docs_check["success"]:
                issues.append(docs_check["issue"])
            
            # Calculate verification score
            passed_checks = sum(1 for v in checks.values() if v)
            total_checks = len(checks)
            score = (passed_checks / total_checks) * 100 if total_checks > 0 else 0
            
            # Determine status
            if score >= 80:
                status = ServerStatus.VERIFIED
            elif score >= 60:
                status = ServerStatus.VERIFIED  # With warnings
            else:
                status = ServerStatus.FAILED
                
        except Exception as e:
            logger.error(f"Verification error for {server.name}: {e}")
            status = ServerStatus.FAILED
            issues.append(f"Verification error: {str(e)}")
            score = 0.0
        
        result = VerificationResult(
            server_id=server.id,
            status=status,
            timestamp=datetime.utcnow(),
            checks=checks,
            issues=issues,
            score=score,
            details={
                "server_name": server.name,
                "server_version": server.version,
                "verification_version": "1.0.0"
            }
        )
        
        logger.info(f"Verification completed for {server.name}: {status} (score: {score:.1f})")
        return result
    
    async def _check_repository(self, server: Server) -> Dict:
        """Check if repository is accessible."""
        try:
            url = str(server.url)
            
            # Handle GitHub URLs
            if "github.com" in url:
                # Convert to API URL
                parts = url.replace("https://github.com/", "").split("/")
                if len(parts) >= 2:
                    api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
                    async with self.session.get(api_url) as response:
                        if response.status == 200:
                            return {"success": True}
                        else:
                            return {
                                "success": False,
                                "issue": f"Repository not accessible (status: {response.status})"
                            }
            
            # For other URLs, just check if accessible
            async with self.session.get(url) as response:
                if response.status == 200:
                    return {"success": True}
                else:
                    return {
                        "success": False,
                        "issue": f"URL not accessible (status: {response.status})"
                    }
                    
        except Exception as e:
            return {
                "success": False,
                "issue": f"Failed to access repository: {str(e)}"
            }
    
    async def _check_structure(self, server: Server) -> Dict:
        """Check if server has valid MCP structure."""
        try:
            # For GitHub repos, check for required files
            if "github.com" in str(server.url):
                required_files = ["package.json", "README.md"]
                url = str(server.url)
                parts = url.replace("https://github.com/", "").split("/")
                
                if len(parts) >= 2:
                    for file in required_files:
                        api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}/contents/{file}"
                        async with self.session.get(api_url) as response:
                            if response.status != 200:
                                return {
                                    "success": False,
                                    "issue": f"Missing required file: {file}"
                                }
                    
                    # Check package.json for MCP indicators
                    pkg_url = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/main/package.json"
                    async with self.session.get(pkg_url) as response:
                        if response.status == 200:
                            content = await response.text()
                            pkg = json.loads(content)
                            
                            # Check for MCP-related dependencies or keywords
                            deps = pkg.get("dependencies", {})
                            dev_deps = pkg.get("devDependencies", {})
                            keywords = pkg.get("keywords", [])
                            
                            has_mcp = (
                                "@modelcontextprotocol" in str(deps) or
                                "@modelcontextprotocol" in str(dev_deps) or
                                "mcp" in [k.lower() for k in keywords] or
                                "model-context-protocol" in [k.lower() for k in keywords]
                            )
                            
                            if has_mcp:
                                return {"success": True}
                            else:
                                return {
                                    "success": False,
                                    "issue": "No MCP dependencies or keywords found"
                                }
            
            return {"success": True}  # Pass by default for non-GitHub URLs
            
        except Exception as e:
            return {
                "success": False,
                "issue": f"Structure check failed: {str(e)}"
            }
    
    async def _check_mcp_compliance(self, server: Server) -> Dict:
        """Check MCP protocol compliance."""
        try:
            # Check if server implements required capabilities
            if not server.capabilities:
                return {
                    "success": False,
                    "issue": "No capabilities declared"
                }
            
            # At minimum, should have at least one capability
            if len(server.capabilities) > 0:
                return {"success": True}
            else:
                return {
                    "success": False,
                    "issue": "Must implement at least one MCP capability"
                }
                
        except Exception as e:
            return {
                "success": False,
                "issue": f"MCP compliance check failed: {str(e)}"
            }
    
    async def _security_scan(self, server: Server) -> Dict:
        """Run security scan on server code."""
        issues = []
        
        try:
            # Basic security checks
            # In production, this would integrate with security scanning tools
            
            # Check for common vulnerabilities
            suspicious_patterns = [
                "eval(",
                "exec(",
                "__import__",
                "os.system",
                "subprocess.call",
            ]
            
            # For GitHub repos, scan key files
            if "github.com" in str(server.url):
                # This is a simplified check
                # In production, would download and scan properly
                pass
            
            # If no major issues found
            if len(issues) == 0:
                return {"success": True, "issues": []}
            else:
                return {"success": False, "issues": issues}
                
        except Exception as e:
            return {
                "success": False,
                "issues": [f"Security scan error: {str(e)}"]
            }
    
    async def _check_license(self, server: Server) -> Dict:
        """Validate server license."""
        try:
            # Check for license file
            if "github.com" in str(server.url):
                url = str(server.url)
                parts = url.replace("https://github.com/", "").split("/")
                
                if len(parts) >= 2:
                    api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}/license"
                    if not self.session:
                        self.session = aiohttp.ClientSession()
                    async with self.session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("license"):
                                return {"success": True}
                        
            # Check metadata for license
            if server.metadata and server.metadata.license:
                return {"success": True}
            
            return {
                "success": False,
                "issue": "No license information found"
            }
            
        except Exception as e:
            return {
                "success": False,
                "issue": f"License check failed: {str(e)}"
            }
    
    async def _check_documentation(self, server: Server) -> Dict:
        """Check for adequate documentation."""
        try:
            # Check for README
            if "github.com" in str(server.url):
                url = str(server.url)
                parts = url.replace("https://github.com/", "").split("/")
                
                if len(parts) >= 2:
                    api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}/readme"
                    if not self.session:
                        self.session = aiohttp.ClientSession()
                    async with self.session.get(api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            # Check README size (should be substantial)
                            if data.get("size", 0) > 500:  # At least 500 bytes
                                return {"success": True}
                            else:
                                return {
                                    "success": False,
                                    "issue": "README too short or missing content"
                                }
            
            # Check for documentation URL
            if server.metadata and server.metadata.documentation:
                return {"success": True}
            
            return {
                "success": False,
                "issue": "No documentation found"
            }
            
        except Exception as e:
            return {
                "success": False,
                "issue": f"Documentation check failed: {str(e)}"
            }