#!/usr/bin/env python3
"""
Secure TCP Agent - Sandboxed Agent with Human-Controlled Tools

This agent can only use tools that have been explicitly approved by humans
through the secure sandbox system. No tool access without human oversight.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .human_approval_interface import HumanApprovalInterface
from .sandbox_manager import SandboxPermission, SandboxViolation, TCPSandboxManager


class SecureTCPAgent:
    """
    Security-first TCP agent that operates within human-controlled sandbox.

    Key Security Principles:
    1. Can only use human-approved tools
    2. All tool usage is monitored and logged
    3. Cannot bypass sandbox restrictions
    4. Fails safely when security violations occur
    5. Provides transparency about available capabilities
    """

    def __init__(
        self,
        agent_id: str,
        sandbox_manager: TCPSandboxManager,
        security_level: str = "strict",
    ):
        """Initialize secure agent with sandbox controls."""
        self.agent_id = agent_id
        self.sandbox = sandbox_manager
        self.security_level = security_level
        self.session_id = f"agent_{agent_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Set up agent logging
        self.logger = logging.getLogger(f"secure_tcp_agent_{agent_id}")
        self._setup_logging()

        # Initialize capability cache
        self.capability_cache = {}
        self._refresh_capability_cache()

        self.logger.info(f"Secure TCP Agent initialized: {agent_id}")

    def _setup_logging(self) -> None:
        """Set up secure agent logging."""
        # Ensure audit logs directory exists
        audit_dir = self.sandbox.sandbox_dir / "audit_logs"
        audit_dir.mkdir(parents=True, exist_ok=True)

        log_file = audit_dir / f"agent_{self.agent_id}.log"

        handler = logging.FileHandler(log_file)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] AGENT-%(name)s: %(message)s"
        )
        handler.setFormatter(formatter)

        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def _refresh_capability_cache(self) -> None:
        """Refresh cache of available tool capabilities."""
        approved_tools = self.sandbox.get_available_tools()

        self.capability_cache = {}
        for tool_name, tool in approved_tools.items():
            # Parse TCP descriptor for capabilities
            try:
                capabilities = self._parse_tcp_capabilities(tool.tcp_descriptor)
                self.capability_cache[tool_name] = {
                    "capabilities": capabilities,
                    "permission_level": tool.permission_level.value,
                    "allowed_args": tool.allowed_args,
                    "forbidden_args": tool.forbidden_args,
                    "human_approved": tool.human_approved,
                }
            except Exception as e:
                self.logger.warning(
                    f"Failed to parse capabilities for {tool_name}: {e}"
                )

        self.logger.info(
            f"Capability cache refreshed: {len(self.capability_cache)} tools available"
        )

    def _parse_tcp_capabilities(self, tcp_descriptor: bytes) -> List[str]:
        """Parse TCP binary descriptor to extract capabilities."""
        if len(tcp_descriptor) != 20:
            return []

        import struct

        # Extract capability flags
        cap_bytes = tcp_descriptor[6:10]
        cap_flags = struct.unpack(">I", cap_bytes)[0]

        # Map bit positions to capability names
        capability_map = {
            0: "text_processing",
            1: "json_handling",
            2: "file_operations",
            3: "stdin_support",
            4: "recursive_operations",
            5: "parallel_processing",
            6: "streaming_support",
            7: "pattern_matching",
            8: "case_handling",
            9: "word_boundaries",
            10: "line_numbering",
            11: "context_aware",
            12: "binary_support",
            13: "compression",
            14: "network_operations",
            15: "real_time_processing",
        }

        capabilities = []
        for bit_pos, capability in capability_map.items():
            if cap_flags & (1 << bit_pos):
                capabilities.append(capability)

        return capabilities

    def get_available_capabilities(self) -> Dict[str, List[str]]:
        """Get all capabilities available to this agent."""
        self._refresh_capability_cache()

        all_capabilities = {}
        for tool_name, tool_info in self.capability_cache.items():
            all_capabilities[tool_name] = tool_info["capabilities"]

        return all_capabilities

    def find_tools_with_capability(self, capability: str) -> List[str]:
        """Find all approved tools that have a specific capability."""
        matching_tools = []

        for tool_name, tool_info in self.capability_cache.items():
            if capability in tool_info["capabilities"]:
                matching_tools.append(tool_name)

        return matching_tools

    def check_tool_availability(self, tool_name: str) -> Dict[str, Any]:
        """Check if a tool is available and what permissions it has."""
        if tool_name not in self.capability_cache:
            return {
                "available": False,
                "reason": "Tool not approved for use",
                "suggestion": "Request human approval for this tool",
            }

        tool_info = self.capability_cache[tool_name]
        permission = self.sandbox.check_tool_permission(tool_name)

        return {
            "available": True,
            "permission_level": permission.value if permission else "none",
            "capabilities": tool_info["capabilities"],
            "allowed_args": tool_info["allowed_args"],
            "forbidden_args": tool_info["forbidden_args"],
            "human_approved": tool_info["human_approved"],
        }

    def request_tool_execution(
        self,
        tool_name: str,
        args: List[str],
        input_data: str = None,
        justification: str = None,
    ) -> Dict[str, Any]:
        """
        Request execution of a tool with security validation.

        Args:
            tool_name: Name of tool to execute
            args: Arguments to pass to tool
            input_data: Optional input data
            justification: Human-readable justification for tool use

        Returns:
            Execution result or security violation details
        """
        self.logger.info(f"Tool execution requested: {tool_name} {' '.join(args)}")

        if justification:
            self.logger.info(f"Justification: {justification}")

        try:
            # Verify tool availability
            availability = self.check_tool_availability(tool_name)
            if not availability["available"]:
                return {
                    "success": False,
                    "error": "security_violation",
                    "reason": availability["reason"],
                    "suggestion": availability["suggestion"],
                }

            # Validate execution permissions
            allowed, reason = self.sandbox.validate_tool_execution(tool_name, args)
            if not allowed:
                self.logger.warning(f"Tool execution blocked: {tool_name} - {reason}")
                return {
                    "success": False,
                    "error": "permission_denied",
                    "reason": reason,
                    "tool_info": availability,
                }

            # Execute in sandbox
            result = self.sandbox.execute_sandboxed_tool(
                tool_name=tool_name, args=args, input_data=input_data, timeout=30
            )

            # Add agent context to result
            result["agent_id"] = self.agent_id
            result["session_id"] = self.session_id
            result["justification"] = justification
            result["success"] = True

            self.logger.info(f"Tool execution successful: {tool_name}")
            return result

        except SandboxViolation as e:
            self.logger.error(f"Sandbox violation: {tool_name} - {e}")
            return {
                "success": False,
                "error": "sandbox_violation",
                "reason": str(e),
                "agent_id": self.agent_id,
            }
        except Exception as e:
            self.logger.error(f"Tool execution failed: {tool_name} - {e}")
            return {
                "success": False,
                "error": "execution_failed",
                "reason": str(e),
                "agent_id": self.agent_id,
            }

    def suggest_tool_for_task(self, task_description: str) -> Dict[str, Any]:
        """
        Suggest best available tool for a given task.

        Only suggests tools that are human-approved and available.
        """
        task_lower = task_description.lower()

        # Map task keywords to required capabilities
        capability_requirements = []

        if any(word in task_lower for word in ["search", "find", "grep", "pattern"]):
            capability_requirements.append("pattern_matching")

        if any(word in task_lower for word in ["file", "read", "write"]):
            capability_requirements.append("file_operations")

        if any(word in task_lower for word in ["text", "string", "line"]):
            capability_requirements.append("text_processing")

        if any(word in task_lower for word in ["json", "data"]):
            capability_requirements.append("json_handling")

        if any(word in task_lower for word in ["network", "download", "http"]):
            capability_requirements.append("network_operations")

        # Find tools that meet requirements
        candidate_tools = []
        for tool_name in self.capability_cache.keys():
            tool_caps = self.capability_cache[tool_name]["capabilities"]

            # Check if tool has required capabilities
            matches = sum(1 for req in capability_requirements if req in tool_caps)
            if matches > 0:
                candidate_tools.append((tool_name, matches, tool_caps))

        if not candidate_tools:
            return {
                "suggestion": None,
                "reason": "No approved tools match task requirements",
                "required_capabilities": capability_requirements,
                "available_tools": list(self.capability_cache.keys()),
            }

        # Sort by capability matches
        candidate_tools.sort(key=lambda x: x[1], reverse=True)
        best_tool, matches, capabilities = candidate_tools[0]

        return {
            "suggestion": best_tool,
            "confidence": (
                matches / len(capability_requirements)
                if capability_requirements
                else 0.5
            ),
            "capabilities": capabilities,
            "permission_level": self.capability_cache[best_tool]["permission_level"],
            "reasoning": f"Matches {matches} of {len(capability_requirements)} required capabilities",
        }

    def generate_capability_report(self) -> str:
        """Generate human-readable report of agent capabilities."""
        self._refresh_capability_cache()

        report_lines = [
            f"🤖 SECURE TCP AGENT CAPABILITY REPORT",
            f"=" * 60,
            f"Agent ID: {self.agent_id}",
            f"Session: {self.session_id}",
            f"Security Level: {self.security_level}",
            f"Sandbox Session: {self.sandbox.session_id}",
            f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"APPROVED TOOLS: {len(self.capability_cache)}",
            "-" * 40,
        ]

        # List approved tools
        for tool_name, tool_info in self.capability_cache.items():
            permission = tool_info["permission_level"]
            cap_count = len(tool_info["capabilities"])

            report_lines.append(f"🔧 {tool_name}:")
            report_lines.append(f"   Permission: {permission}")
            report_lines.append(f"   Capabilities: {cap_count}")

            if tool_info["capabilities"]:
                for cap in tool_info["capabilities"]:
                    report_lines.append(f"     • {cap.replace('_', ' ').title()}")

            if tool_info["forbidden_args"]:
                report_lines.append(
                    f"   Forbidden args: {', '.join(tool_info['forbidden_args'])}"
                )

            report_lines.append("")

        # Capability summary
        all_capabilities = set()
        for tool_info in self.capability_cache.values():
            all_capabilities.update(tool_info["capabilities"])

        report_lines.extend(
            [
                "CAPABILITY SUMMARY:",
                "-" * 40,
                f"Unique capabilities: {len(all_capabilities)}",
            ]
        )

        for capability in sorted(all_capabilities):
            tools_with_cap = self.find_tools_with_capability(capability)
            cap_name = capability.replace("_", " ").title()
            report_lines.append(f"• {cap_name}: {len(tools_with_cap)} tools")

        report_lines.extend(
            [
                "",
                "SECURITY STATUS:",
                "-" * 40,
                "✅ All tools require explicit human approval",
                "✅ Execution monitored and logged",
                "✅ Sandbox restrictions enforced",
                "✅ No tool access without oversight",
                "",
                "🔐 HUMAN CONTROL MAINTAINED",
                "   Agent capabilities limited to approved tools only.",
            ]
        )

        return "\n".join(report_lines)

    def interactive_mode(self) -> None:
        """Run agent in interactive mode with security controls."""
        print(f"🤖 SECURE TCP AGENT: {self.agent_id}")
        print("=" * 60)
        print("Security-controlled agent with human-approved tools only.")
        print("Type 'help' for commands or 'quit' to exit.")
        print()

        while True:
            try:
                command = input(f"secure-agent[{self.agent_id}]> ").strip()

                if command.lower() in ["quit", "exit", "q"]:
                    print("👋 Secure agent shutting down...")
                    break

                elif command.lower() == "help":
                    self._show_help()

                elif command.lower() == "status":
                    self._show_status()

                elif command.lower() == "tools":
                    self._list_available_tools()

                elif command.lower() == "capabilities":
                    print(self.generate_capability_report())

                elif command.startswith("check "):
                    tool_name = command[6:].strip()
                    availability = self.check_tool_availability(tool_name)
                    self._show_tool_availability(tool_name, availability)

                elif command.startswith("suggest "):
                    task = command[8:].strip()
                    suggestion = self.suggest_tool_for_task(task)
                    self._show_task_suggestion(task, suggestion)

                elif command.startswith("exec "):
                    self._handle_exec_command(command[5:].strip())

                else:
                    print("Unknown command. Type 'help' for available commands.")

                print()

            except KeyboardInterrupt:
                print("\n👋 Secure agent interrupted. Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

    def _show_help(self) -> None:
        """Show available commands."""
        print("Available commands:")
        print("  help           - Show this help")
        print("  status         - Show agent security status")
        print("  tools          - List approved tools")
        print("  capabilities   - Show detailed capability report")
        print("  check <tool>   - Check if tool is available")
        print("  suggest <task> - Suggest tool for task")
        print("  exec <cmd>     - Execute command (if approved)")
        print("  quit           - Exit agent")

    def _show_status(self) -> None:
        """Show agent security status."""
        status = self.sandbox.get_security_status()

        print("🔐 SECURITY STATUS:")
        print("-" * 30)
        print(f"Agent ID: {self.agent_id}")
        print(f"Security Level: {self.security_level}")
        print(f"Approved Tools: {len(self.capability_cache)}")
        print(f"Sandbox Session: {status['sandbox_session']}")
        print(f"Human Control: Active")
        print(f"Audit Logging: Enabled")

    def _list_available_tools(self) -> None:
        """List all approved tools."""
        if not self.capability_cache:
            print("❌ No tools approved for use.")
            print("💡 Request human approval for tools via sandbox manager.")
            return

        print(f"🔧 APPROVED TOOLS ({len(self.capability_cache)}):")
        print("-" * 40)

        for tool_name, tool_info in self.capability_cache.items():
            permission = tool_info["permission_level"]
            cap_count = len(tool_info["capabilities"])
            print(f"{tool_name:15} | {permission:12} | {cap_count} capabilities")

    def _show_tool_availability(self, tool_name: str, availability: Dict) -> None:
        """Show tool availability details."""
        if availability["available"]:
            print(f"✅ Tool '{tool_name}' is available:")
            print(f"   Permission: {availability['permission_level']}")
            print(f"   Capabilities: {', '.join(availability['capabilities'])}")
            if availability["forbidden_args"]:
                print(f"   Forbidden args: {', '.join(availability['forbidden_args'])}")
        else:
            print(f"❌ Tool '{tool_name}' not available:")
            print(f"   Reason: {availability['reason']}")
            print(f"   Suggestion: {availability['suggestion']}")

    def _show_task_suggestion(self, task: str, suggestion: Dict) -> None:
        """Show task suggestion details."""
        if suggestion["suggestion"]:
            print(f"💡 Task: '{task}'")
            print(f"   Suggested tool: {suggestion['suggestion']}")
            print(f"   Confidence: {suggestion['confidence']:.1%}")
            print(f"   Permission: {suggestion['permission_level']}")
            print(f"   Reasoning: {suggestion['reasoning']}")
        else:
            print(f"❌ No approved tools found for task: '{task}'")
            print(
                f"   Required capabilities: {', '.join(suggestion.get('required_capabilities', []))}"
            )
            print(
                f"   Available tools: {', '.join(suggestion.get('available_tools', []))}"
            )

    def _handle_exec_command(self, command: str) -> None:
        """Handle execution command with security validation."""
        if not command:
            print("Usage: exec <tool> [args...]")
            return

        parts = command.split()
        tool_name = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        # Get justification
        justification = input(f"Justification for using '{tool_name}': ").strip()

        result = self.request_tool_execution(
            tool_name, args, justification=justification
        )

        if result["success"]:
            print(f"✅ Execution successful:")
            if result.get("stdout"):
                print("STDOUT:")
                print(result["stdout"])
            if result.get("stderr"):
                print("STDERR:")
                print(result["stderr"])
            print(f"Exit code: {result['exit_code']}")
        else:
            print(f"❌ Execution failed:")
            print(f"Error: {result['error']}")
            print(f"Reason: {result['reason']}")


def main():
    """Demonstrate secure TCP agent."""
    print("🔐 SECURE TCP AGENT DEMONSTRATION")
    print("=" * 60)
    print("This agent operates within strict security controls:")
    print("✅ Only human-approved tools available")
    print("✅ All usage monitored and logged")
    print("✅ Sandbox restrictions enforced")
    print("✅ Fails safely on security violations")
    print()

    # Initialize secure components
    from pathlib import Path

    sandbox_dir = Path.cwd() / "secure_tcp_sandbox"
    sandbox = TCPSandboxManager(str(sandbox_dir), security_level="strict")

    # Create secure agent
    agent = SecureTCPAgent("demo_agent", sandbox)

    print(f"Agent initialized: {agent.agent_id}")
    print(f"Approved tools: {len(agent.capability_cache)}")
    print()

    if agent.capability_cache:
        print("📋 AGENT CAPABILITY SUMMARY:")
        print(agent.generate_capability_report())
    else:
        print("⚠️  No tools approved yet. Use approval interface to add tools.")

    print("\n💡 Usage:")
    print("1. Request tool approval via sandbox.request_tool_approval()")
    print("2. Human approves via HumanApprovalInterface")
    print("3. Agent can use approved tools via request_tool_execution()")
    print("4. All usage is monitored and secure")


if __name__ == "__main__":
    main()
