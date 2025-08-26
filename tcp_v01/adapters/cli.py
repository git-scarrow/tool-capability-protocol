"""CLI adapter for TCP with safety features and override support."""

import os
import shlex
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..core.descriptor import TCPDescriptor, TLVBlock
from ..core.types import (
    ProtocolType, SecurityFlags, SecurityLevel, TLVType,
    compute_risk_level
)


class CLIAdapter:
    """Adapter for analyzing CLI tools and generating TCP descriptors."""
    
    def __init__(self, override_dir: Optional[Path] = None):
        """
        Initialize CLI adapter.
        
        Args:
            override_dir: Directory containing override YAML files
        """
        if override_dir is None:
            override_dir = Path.home() / ".tcp" / "overrides"
        self.override_dir = override_dir
        self.override_dir.mkdir(parents=True, exist_ok=True)
        
        # Coverage tracking
        self.total_analyzed = 0
        self.fully_overridden = 0
        self.partially_overridden = 0
        self.fully_inferred = 0
        self.field_coverage = {
            "security_flags": {"overridden": 0, "inferred": 0},
            "capabilities": {"overridden": 0, "inferred": 0},
            "risk_level": {"overridden": 0, "inferred": 0}
        }
    
    def analyze(self, command_line: str, override_file: Optional[Path] = None) -> TCPDescriptor:
        """
        Analyze command line and generate TCP descriptor.
        
        Args:
            command_line: Full command line to analyze
            override_file: Optional path to override YAML file
            
        Returns:
            TCP descriptor for the command
        """
        self.total_analyzed += 1
        
        # Parse command line
        base_command, subcommand, args = self._parse_command_line(command_line)
        
        # Load overrides
        overrides = self._load_overrides(base_command, subcommand, override_file)
        
        # Try to get help text
        help_text = self._get_help_text(base_command, subcommand, args)
        
        # Build descriptor
        descriptor = self._build_descriptor(
            base_command=base_command,
            subcommand=subcommand,
            args=args,
            help_text=help_text,
            overrides=overrides
        )
        
        # Track coverage
        self._update_coverage_stats(overrides, help_text)
        
        return descriptor
    
    def _parse_command_line(self, command_line: str) -> Tuple[str, Optional[str], List[str]]:
        """Parse command line into components."""
        try:
            tokens = shlex.split(command_line)
        except ValueError as e:
            # Handle malformed command lines
            warnings.warn(f"Failed to parse command line: {e}")
            tokens = command_line.split()
        
        if not tokens:
            raise ValueError("Empty command line")
        
        base_command = tokens[0]
        args = tokens[1:] if len(tokens) > 1 else []
        
        # Detect subcommand for known multi-command tools
        subcommand = None
        multi_command_tools = {
            'git', 'docker', 'kubectl', 'aws', 'az', 'gcloud',
            'npm', 'cargo', 'poetry', 'pip', 'apt', 'yum', 'brew'
        }
        
        if base_command in multi_command_tools and args and not args[0].startswith('-'):
            subcommand = args[0]
        
        return base_command, subcommand, args
    
    def _load_overrides(self, base_command: str, subcommand: Optional[str],
                       override_file: Optional[Path]) -> Dict[str, Any]:
        """Load override configuration."""
        overrides = {}
        
        # Try explicit override file first
        if override_file and override_file.exists():
            try:
                overrides = yaml.safe_load(override_file.read_text())
            except Exception as e:
                warnings.warn(f"Failed to load override file {override_file}: {e}")
        
        # Try default override location
        if not overrides:
            if subcommand:
                default_file = self.override_dir / f"{base_command}-{subcommand}.yaml"
            else:
                default_file = self.override_dir / f"{base_command}.yaml"
            
            if default_file.exists():
                try:
                    overrides = yaml.safe_load(default_file.read_text())
                except Exception as e:
                    warnings.warn(f"Failed to load default override {default_file}: {e}")
        
        return overrides
    
    def _get_safe_environment(self) -> Dict[str, str]:
        """Get safe environment for command execution."""
        safe_env = {
            "PATH": "/usr/bin:/bin",
            "HOME": tempfile.gettempdir(),
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "AWS_PAGER": "",
            "MANPAGER": "cat",
            "LESS": "",
            "MORE": "",
            "TERM": "dumb"
        }
        
        # Windows adjustments
        if sys.platform == "win32":
            safe_env["PATH"] = r"C:\Windows\System32;C:\Windows"
            safe_env["TEMP"] = tempfile.gettempdir()
            safe_env["TMP"] = tempfile.gettempdir()
        
        return safe_env
    
    def _safe_help_invocation(self, command: str, args: List[str] = None) -> Optional[str]:
        """Safely invoke command to get help text."""
        if args is None:
            args = []
        
        # Get safe environment
        safe_env = self._get_safe_environment()
        
        # Determine help flags to try
        if sys.platform == "win32":
            help_flags = ["/?", "-?", "--help", "-h", "help"]
        else:
            help_flags = ["--help", "-h", "--version", "help"]
        
        # Find command executable
        command_path = self._find_executable(command)
        if not command_path:
            return None
        
        # Try each help flag
        for flag in help_flags:
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    cmd = [command_path] + args + [flag]
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        env=safe_env,
                        cwd=tmpdir,
                        stdin=subprocess.DEVNULL
                    )
                    
                    # Check for successful help output
                    if result.returncode == 0 and len(result.stdout) > 10:
                        return result.stdout
                    elif result.stderr and "usage:" in result.stderr.lower():
                        return result.stderr
                        
            except (subprocess.TimeoutExpired, OSError) as e:
                warnings.warn(f"Failed to get help for {command}: {e}")
                continue
        
        return None
    
    def _find_executable(self, command: str) -> Optional[str]:
        """Find full path to executable."""
        # Windows built-ins that need special handling
        if sys.platform == "win32":
            builtins = {'dir', 'copy', 'del', 'type', 'echo', 'cd', 'mkdir', 'rmdir'}
            if command.lower() in builtins:
                return "cmd.exe"
        
        # Try to find in PATH
        import shutil
        return shutil.which(command)
    
    def _get_help_text(self, base_command: str, subcommand: Optional[str], 
                      args: List[str]) -> Optional[str]:
        """Get help text for command."""
        # Try base command help
        base_help = self._safe_help_invocation(base_command)
        
        # Try subcommand help if present
        subcommand_help = None
        if subcommand:
            subcommand_args = [subcommand]
            subcommand_help = self._safe_help_invocation(base_command, subcommand_args)
        
        # Combine help texts
        if subcommand_help:
            return subcommand_help
        return base_help
    
    def _infer_security_flags(self, command: str, subcommand: Optional[str], 
                            help_text: Optional[str]) -> int:
        """Infer security flags from command and help text."""
        flags = 0
        
        # Command-specific patterns
        dangerous_commands = {
            'rm': SecurityFlags.FS_DELETE,
            'del': SecurityFlags.FS_DELETE,
            'rmdir': SecurityFlags.FS_DELETE,
            'dd': SecurityFlags.FS_WRITE | SecurityFlags.FS_DELETE,
            'format': SecurityFlags.FS_DELETE | SecurityFlags.WILDCARD_RESOURCE,
            'mkfs': SecurityFlags.FS_DELETE | SecurityFlags.WILDCARD_RESOURCE
        }
        
        if command in dangerous_commands:
            flags |= dangerous_commands[command]
        
        # Network tools
        network_commands = {'curl', 'wget', 'nc', 'netcat', 'ssh', 'scp', 'rsync', 'ftp'}
        if command in network_commands:
            flags |= SecurityFlags.NET_EGRESS
        
        # Write operations
        write_commands = {'cp', 'copy', 'mv', 'move', 'echo', 'sed', 'awk', 'tee'}
        if command in write_commands:
            flags |= SecurityFlags.FS_WRITE
        
        # Code execution
        exec_commands = {'python', 'node', 'ruby', 'perl', 'sh', 'bash', 'cmd', 'powershell'}
        if command in exec_commands:
            flags |= SecurityFlags.CODE_EXEC
        
        # Privilege escalation
        priv_commands = {'sudo', 'su', 'doas', 'runas'}
        if command in priv_commands:
            flags |= SecurityFlags.PRIV_ESC
        
        # Check help text for clues
        if help_text:
            help_lower = help_text.lower()
            if 'delete' in help_lower or 'remove' in help_lower:
                flags |= SecurityFlags.FS_DELETE
            if 'write' in help_lower or 'modify' in help_lower or 'create' in help_lower:
                flags |= SecurityFlags.FS_WRITE
            if 'network' in help_lower or 'download' in help_lower or 'upload' in help_lower:
                flags |= SecurityFlags.NET_EGRESS
            if 'sudo' in help_lower or 'root' in help_lower or 'administrator' in help_lower:
                flags |= SecurityFlags.PRIV_ESC
        
        # Safe commands get no flags
        safe_commands = {'ls', 'dir', 'cat', 'type', 'grep', 'find', 'which', 'echo', 'pwd'}
        if command in safe_commands and not (subcommand or flags):
            flags = SecurityFlags.FS_READ
        
        return flags
    
    def _build_descriptor(self, base_command: str, subcommand: Optional[str],
                        args: List[str], help_text: Optional[str],
                        overrides: Dict[str, Any]) -> TCPDescriptor:
        """Build TCP descriptor from analyzed data."""
        descriptor = TCPDescriptor()
        
        # Set protocol type
        descriptor.header.protocol_type = ProtocolType.CLI
        
        # Tool name for ID hint
        tool_name = f"{base_command}-{subcommand}" if subcommand else base_command
        descriptor.header.tool_id_hint = descriptor.compute_tool_id_hint(tool_name)
        
        # Security flags (override or infer)
        if "security" in overrides and "flags" in overrides["security"]:
            # Parse flag names to bitmask
            flags = 0
            for flag_name in overrides["security"]["flags"]:
                if hasattr(SecurityFlags, flag_name):
                    flags |= getattr(SecurityFlags, flag_name)
            descriptor.header.security_flags = flags
        else:
            descriptor.header.security_flags = self._infer_security_flags(
                base_command, subcommand, help_text
            )
        
        # Security level (compute from flags or use override)
        if "security" in overrides and "level" in overrides["security"]:
            descriptor.header.security_level = overrides["security"]["level"]
        else:
            descriptor.header.security_level = compute_risk_level(descriptor.header.security_flags)
        
        # Add IDENTITY TLV (required)
        identity = {
            "name": tool_name,
            "version": overrides.get("version", "unknown"),
            "adapter": "cli/1.0.0",
            "source": f"help:{base_command}" if help_text else "override",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        descriptor.add_tlv(TLVType.IDENTITY, identity, required=True)
        
        # Add CAPABILITIES TLV
        capabilities = {
            "verbs": overrides.get("capabilities", {}).get("verbs", []),
            "resources": overrides.get("capabilities", {}).get("resources", ["filesystem"]),
            "command": {
                "base": base_command,
                "subcommand": subcommand,
                "args": args
            }
        }
        descriptor.add_tlv(TLVType.CAPABILITIES, capabilities, required=True)
        
        # Add SECURITY_EXT if needed
        if descriptor.header.security_flags & (
            SecurityFlags.FS_READ | SecurityFlags.FS_WRITE | SecurityFlags.FS_DELETE |
            SecurityFlags.NET_EGRESS | SecurityFlags.NET_INGRESS
        ):
            security_ext = {
                "resource_domain": overrides.get("security", {}).get(
                    "resource_domain", "LOCAL_HOST"
                ),
                "rationale": overrides.get("security", {}).get("rationale", ""),
                "help_available": help_text is not None
            }
            descriptor.add_tlv(TLVType.SECURITY_EXT, security_ext)
        
        # Add OVERRIDES TLV if overrides were used
        if overrides:
            descriptor.add_tlv(TLVType.OVERRIDES, {
                "source": str(self.override_dir / f"{tool_name}.yaml"),
                "fields_overridden": list(overrides.keys())
            })
        
        # Validate before returning
        descriptor.validate()
        
        return descriptor
    
    def _update_coverage_stats(self, overrides: Dict, help_text: Optional[str]) -> None:
        """Update coverage statistics."""
        if overrides and help_text:
            self.partially_overridden += 1
        elif overrides and not help_text:
            self.fully_overridden += 1
        elif not overrides and help_text:
            self.fully_inferred += 1
        
        # Track field-level coverage
        if "security" in overrides:
            self.field_coverage["security_flags"]["overridden"] += 1
        else:
            self.field_coverage["security_flags"]["inferred"] += 1
        
        if "capabilities" in overrides:
            self.field_coverage["capabilities"]["overridden"] += 1
        else:
            self.field_coverage["capabilities"]["inferred"] += 1
    
    def coverage_report(self) -> Dict[str, Any]:
        """Generate coverage report."""
        if self.total_analyzed == 0:
            return {"error": "No commands analyzed"}
        
        return {
            "total_tools": self.total_analyzed,
            "fully_overridden": self.fully_overridden,
            "partially_overridden": self.partially_overridden,
            "fully_inferred": self.fully_inferred,
            "fields": {
                field: {
                    "overridden": stats["overridden"] / self.total_analyzed,
                    "inferred": stats["inferred"] / self.total_analyzed
                }
                for field, stats in self.field_coverage.items()
            }
        }