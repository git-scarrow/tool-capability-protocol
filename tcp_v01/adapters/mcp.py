"""MCP adapter for TCP with lossless manifest preservation."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.descriptor import TCPDescriptor
from ..core.types import (
    ProtocolType, SecurityFlags, SecurityLevel, TLVType,
    compute_risk_level
)


class MCPAdapter:
    """Adapter for analyzing MCP servers and generating TCP descriptors."""
    
    def analyze(self, manifest: Dict[str, Any], source_uri: str) -> TCPDescriptor:
        """
        Analyze MCP manifest and generate lossless TCP descriptor.
        
        Args:
            manifest: MCP server manifest dictionary
            source_uri: Source URI of the manifest
            
        Returns:
            TCP descriptor with complete MCP data preserved
        """
        descriptor = TCPDescriptor()
        
        # Set protocol type
        descriptor.header.protocol_type = ProtocolType.MCP
        
        # Tool ID from server name
        server_name = manifest.get("name", "unknown-mcp")
        descriptor.header.tool_id_hint = descriptor.compute_tool_id_hint(server_name)
        
        # Map security flags from MCP capabilities
        descriptor.header.security_flags = self._map_security_flags(manifest)
        descriptor.header.security_level = compute_risk_level(descriptor.header.security_flags)
        
        # Add IDENTITY TLV (required)
        identity = {
            "name": server_name,
            "version": manifest.get("version", "unknown"),
            "adapter": "mcp/1.0.0",
            "source": source_uri,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        descriptor.add_tlv(TLVType.IDENTITY, identity, required=True)
        
        # Add PROTOCOL_EXT TLV with complete manifest (lossless)
        descriptor.add_tlv(TLVType.PROTOCOL_EXT, manifest, required=True)
        
        # Add CAPABILITIES TLV (extracted from manifest)
        capabilities = {
            "tools": manifest.get("tools", []),
            "resources": manifest.get("resources", []),
            "prompts": manifest.get("prompts", []),
            "server_type": manifest.get("type", "generic")
        }
        descriptor.add_tlv(TLVType.CAPABILITIES, capabilities)
        
        # Add SECURITY_EXT if security-relevant
        if descriptor.header.security_flags != 0:
            security_ext = {
                "resource_domain": self._determine_resource_domain(manifest),
                "rationale": self._generate_security_rationale(manifest, descriptor.header.security_flags),
                "mcp_specific": {
                    "prompt_injection_risk": self._has_prompt_injection_risk(manifest),
                    "model_access": self._has_model_access(manifest)
                }
            }
            descriptor.add_tlv(TLVType.SECURITY_EXT, security_ext)
        
        # Add EFFECTS if present
        effects = self._extract_effects(manifest)
        if effects:
            descriptor.add_tlv(TLVType.EFFECTS, effects)
        
        # Validate before returning
        descriptor.validate()
        
        return descriptor
    
    def _map_security_flags(self, manifest: Dict[str, Any]) -> int:
        """Map MCP capabilities to security flags."""
        flags = 0
        
        # Check tools for security implications
        tools = manifest.get("tools", [])
        for tool in tools:
            tool_lower = tool.lower() if isinstance(tool, str) else str(tool).lower()
            
            # File operations
            if any(op in tool_lower for op in ['write', 'create', 'save', 'put']):
                flags |= SecurityFlags.FS_WRITE
            if any(op in tool_lower for op in ['delete', 'remove', 'rm', 'unlink']):
                flags |= SecurityFlags.FS_DELETE
            if any(op in tool_lower for op in ['read', 'get', 'list', 'cat']):
                flags |= SecurityFlags.FS_READ
            
            # Network operations
            if any(op in tool_lower for op in ['http', 'request', 'fetch', 'download', 'upload']):
                flags |= SecurityFlags.NET_EGRESS
            if any(op in tool_lower for op in ['listen', 'serve', 'webhook']):
                flags |= SecurityFlags.NET_INGRESS
            
            # Execution
            if any(op in tool_lower for op in ['exec', 'run', 'eval', 'compile']):
                flags |= SecurityFlags.CODE_EXEC
            
            # Credentials
            if any(op in tool_lower for op in ['auth', 'login', 'credential', 'token', 'key']):
                flags |= SecurityFlags.CRED_ACCESS
        
        # Check resources
        resources = manifest.get("resources", [])
        for resource in resources:
            resource_lower = resource.lower() if isinstance(resource, str) else str(resource).lower()
            
            # Wildcard resources
            if '*' in resource_lower or 'all' in resource_lower:
                flags |= SecurityFlags.WILDCARD_RESOURCE
            
            # File resources
            if resource_lower.startswith('file://'):
                if not (flags & SecurityFlags.FS_READ):
                    flags |= SecurityFlags.FS_READ
            
            # Network resources
            if resource_lower.startswith(('http://', 'https://')):
                flags |= SecurityFlags.NET_EGRESS
        
        # Check prompts
        prompts = manifest.get("prompts", [])
        if prompts:
            flags |= SecurityFlags.PROMPT_SURFACE
        
        # Model access
        if "model" in manifest or "llm" in str(manifest).lower():
            flags |= SecurityFlags.MODEL_ACCESS
        
        # Persistence (if server maintains state)
        if manifest.get("persistent_state", False) or manifest.get("stateful", False):
            flags |= SecurityFlags.PERSISTENCE
        
        return flags
    
    def _determine_resource_domain(self, manifest: Dict[str, Any]) -> str:
        """Determine resource domain from manifest."""
        resources = manifest.get("resources", [])
        
        # Check if all resources are remote
        if resources and all(
            r.startswith(('http://', 'https://')) 
            for r in resources 
            if isinstance(r, str)
        ):
            return "REMOTE_SERVICE"
        
        # Check if sandboxed
        if manifest.get("sandbox", False) or manifest.get("containerized", False):
            return "SANDBOX"
        
        # Default to local host
        return "LOCAL_HOST"
    
    def _generate_security_rationale(self, manifest: Dict[str, Any], flags: int) -> str:
        """Generate security rationale based on flags."""
        rationales = []
        
        if flags & SecurityFlags.FS_DELETE:
            rationales.append("Can delete files")
        if flags & SecurityFlags.FS_WRITE:
            rationales.append("Can modify files")
        if flags & SecurityFlags.CODE_EXEC:
            rationales.append("Can execute code")
        if flags & SecurityFlags.CRED_ACCESS:
            rationales.append("Accesses credentials")
        if flags & SecurityFlags.WILDCARD_RESOURCE:
            rationales.append("Has wildcard resource access")
        if flags & SecurityFlags.PROMPT_SURFACE:
            rationales.append("Accepts prompt input")
        if flags & SecurityFlags.MODEL_ACCESS:
            rationales.append("Has model access")
        
        if not rationales:
            return "No significant security risks identified"
        
        return "; ".join(rationales)
    
    def _has_prompt_injection_risk(self, manifest: Dict[str, Any]) -> bool:
        """Check if server has prompt injection risk."""
        # Has prompts and tools that could be dangerous
        has_prompts = bool(manifest.get("prompts"))
        has_dangerous_tools = any(
            tool for tool in manifest.get("tools", [])
            if any(danger in str(tool).lower() for danger in [
                'exec', 'eval', 'write', 'delete', 'http', 'request'
            ])
        )
        return has_prompts and has_dangerous_tools
    
    def _has_model_access(self, manifest: Dict[str, Any]) -> bool:
        """Check if server has direct model access."""
        # Look for model-related fields
        model_indicators = ['model', 'llm', 'ai', 'completion', 'embedding']
        manifest_str = str(manifest).lower()
        return any(indicator in manifest_str for indicator in model_indicators)
    
    def _extract_effects(self, manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract effects from manifest if present."""
        effects = {}
        
        # Direct effects field
        if "effects" in manifest:
            effects.update(manifest["effects"])
        
        # Side effects field
        if "side_effects" in manifest:
            effects["side_effects"] = manifest["side_effects"]
        
        # Guards/preconditions
        if "guards" in manifest:
            effects["guards"] = manifest["guards"]
        
        # Examples
        if "examples" in manifest:
            effects["examples"] = manifest["examples"]
        
        return effects if effects else None