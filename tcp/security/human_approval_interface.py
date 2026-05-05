#!/usr/bin/env python3
"""
Human Approval Interface for TCP Sandbox

Provides a secure interface for humans to review and approve TCP tools
with comprehensive security validation and audit trails.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .sandbox_manager import SandboxedTool, SandboxPermission, TCPSandboxManager


class HumanApprovalInterface:
    """
    Interface for human administrators to securely manage TCP tool approvals.

    Key Security Features:
    - Multi-step approval process
    - Security risk assessment
    - Comprehensive audit trails
    - Tool capability analysis
    - Permission level recommendations
    """

    def __init__(self, sandbox_manager: TCPSandboxManager):
        self.sandbox = sandbox_manager
        self.approval_session = hashlib.md5(
            os.urandom(16), usedforsecurity=False
        ).hexdigest()[:8]

    def list_pending_approvals(self) -> List[Dict]:
        """List all tools pending human approval."""
        request_dir = self.sandbox.sandbox_dir / "approval_requests"
        if not request_dir.exists():
            return []

        pending = []
        for request_file in request_dir.glob("*.json"):
            try:
                with open(request_file, "r") as f:
                    request_data = json.load(f)

                # Add security analysis
                request_data["security_analysis"] = self._analyze_tool_security(
                    request_data
                )
                request_data["risk_level"] = self._assess_risk_level(request_data)
                pending.append(request_data)

            except Exception as e:
                print(f"⚠️  Failed to load request {request_file}: {e}")

        return pending

    def _analyze_tool_security(self, request_data: Dict) -> Dict:
        """Analyze security implications of a tool request."""
        binary_path = request_data.get("binary_path", "")
        tool_name = request_data.get("tool_name", "")

        analysis = {
            "binary_exists": Path(binary_path).exists() if binary_path else False,
            "is_system_tool": (
                binary_path.startswith(("/bin/", "/usr/bin/")) if binary_path else False
            ),
            "has_network_capability": False,  # Would analyze TCP descriptor
            "has_file_write_capability": False,  # Would analyze TCP descriptor
            "requires_elevated_privileges": False,
            "known_security_tool": tool_name.lower()
            in ["ssh", "sudo", "su", "passwd", "mount"],
            "potential_risks": [],
        }

        # Risk assessment based on tool characteristics
        if tool_name.lower() in ["rm", "dd", "mkfs", "fdisk"]:
            analysis["potential_risks"].append("Destructive file operations")

        if tool_name.lower() in ["curl", "wget", "ssh", "nc", "telnet"]:
            analysis["potential_risks"].append("Network operations")
            analysis["has_network_capability"] = True

        if tool_name.lower() in ["sudo", "su", "mount", "umount"]:
            analysis["potential_risks"].append("Requires elevated privileges")
            analysis["requires_elevated_privileges"] = True

        if tool_name.lower() in ["python", "perl", "ruby", "sh", "bash"]:
            analysis["potential_risks"].append("Script execution capability")

        return analysis

    def _assess_risk_level(self, request_data: Dict) -> str:
        """Assess overall risk level of tool request."""
        security_analysis = request_data.get("security_analysis", {})
        risks = security_analysis.get("potential_risks", [])

        if security_analysis.get("requires_elevated_privileges") or len(risks) >= 3:
            return "HIGH"
        elif len(risks) >= 1:
            return "MEDIUM"
        else:
            return "LOW"

    def _recommend_permission_level(self, request_data: Dict) -> SandboxPermission:
        """Recommend appropriate permission level based on security analysis."""
        security_analysis = request_data.get("security_analysis", {})
        tool_name = request_data.get("tool_name", "").lower()

        # High-risk tools
        if security_analysis.get("requires_elevated_privileges"):
            return SandboxPermission.DENIED

        if "Destructive file operations" in security_analysis.get(
            "potential_risks", []
        ):
            return SandboxPermission.DENIED

        # Read-only tools
        if tool_name in ["cat", "head", "tail", "less", "more", "grep", "find"]:
            return SandboxPermission.READ_ONLY

        # Network tools - require careful consideration
        if security_analysis.get("has_network_capability"):
            return SandboxPermission.EXECUTE_SAFE

        # Default for most tools
        return SandboxPermission.EXECUTE_SAFE

    def review_tool_request(self, tool_name: str) -> Dict:
        """Provide detailed review of a tool approval request."""
        request_file = (
            self.sandbox.sandbox_dir
            / "approval_requests"
            / f"{tool_name}_approval.json"
        )

        if not request_file.exists():
            return {"error": f"No approval request found for {tool_name}"}

        with open(request_file, "r") as f:
            request_data = json.load(f)

        # Enhanced analysis for human review
        review = {
            "tool_name": request_data["tool_name"],
            "binary_path": request_data["binary_path"],
            "requested_permission": request_data["requested_permission"],
            "security_analysis": self._analyze_tool_security(request_data),
            "risk_level": self._assess_risk_level(request_data),
            "recommended_permission": self._recommend_permission_level(
                request_data
            ).value,
            "tcp_descriptor_analysis": self._analyze_tcp_descriptor(
                request_data.get("tcp_descriptor")
            ),
            "approval_recommendation": self._generate_approval_recommendation(
                request_data
            ),
        }

        return review

    def _analyze_tcp_descriptor(self, tcp_hex: str) -> Dict:
        """Analyze TCP binary descriptor for capabilities."""
        if not tcp_hex:
            return {"error": "No TCP descriptor provided"}

        try:
            tcp_bytes = bytes.fromhex(tcp_hex)
            if len(tcp_bytes) != 20:
                return {"error": f"Invalid TCP descriptor length: {len(tcp_bytes)}"}

            # Parse TCP descriptor (simplified)
            import struct

            magic = tcp_bytes[:4]
            version_bytes = tcp_bytes[4:6]
            cap_bytes = tcp_bytes[6:10]

            version = struct.unpack(">H", version_bytes)[0]
            cap_flags = struct.unpack(">I", cap_bytes)[0]

            analysis = {
                "magic_signature": magic.hex(),
                "version": (
                    f"{version // 100}.{version % 100}" if version > 0 else "unknown"
                ),
                "capability_flags": f"0x{cap_flags:08x}",
                "active_capabilities": [],
            }

            # Decode capability flags
            capability_names = {
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

            for bit_pos, cap_name in capability_names.items():
                if cap_flags & (1 << bit_pos):
                    analysis["active_capabilities"].append(cap_name)

            return analysis

        except Exception as e:
            return {"error": f"Failed to analyze TCP descriptor: {e}"}

    def _generate_approval_recommendation(self, request_data: Dict) -> Dict:
        """Generate human-readable approval recommendation."""
        security_analysis = request_data.get("security_analysis", {})
        risk_level = self._assess_risk_level(request_data)
        recommended_permission = self._recommend_permission_level(request_data)

        recommendation = {
            "approve": risk_level != "HIGH",
            "permission_level": recommended_permission.value,
            "reasoning": [],
            "security_considerations": [],
        }

        # Generate reasoning
        if risk_level == "LOW":
            recommendation["reasoning"].append(
                "Low security risk - standard approval recommended"
            )
        elif risk_level == "MEDIUM":
            recommendation["reasoning"].append(
                "Medium security risk - approve with restrictions"
            )
        else:
            recommendation["reasoning"].append(
                "High security risk - denial recommended"
            )

        # Security considerations
        risks = security_analysis.get("potential_risks", [])
        for risk in risks:
            recommendation["security_considerations"].append(f"⚠️  {risk}")

        if security_analysis.get("is_system_tool"):
            recommendation["security_considerations"].append("✅ Standard system tool")

        return recommendation

    def interactive_approval_session(self) -> None:
        """Run interactive approval session for human administrator."""
        print("👤 HUMAN APPROVAL INTERFACE")
        print("=" * 60)
        print(f"Approval Session ID: {self.approval_session}")
        print()

        pending = self.list_pending_approvals()

        if not pending:
            print("✅ No tools pending approval.")
            return

        print(f"📋 {len(pending)} tool(s) pending approval:")
        print()

        for i, request in enumerate(pending, 1):
            tool_name = request["tool_name"]
            risk_level = request["risk_level"]

            risk_emoji = (
                "🟢"
                if risk_level == "LOW"
                else "🟡" if risk_level == "MEDIUM" else "🔴"
            )

            print(f"{i}. {tool_name} {risk_emoji} {risk_level} RISK")
            print(f"   Path: {request['binary_path']}")
            print(f"   Requested: {request['requested_permission']}")
            print()

        # Interactive review
        while True:
            try:
                choice = input("Enter tool number to review (or 'q' to quit): ").strip()

                if choice.lower() == "q":
                    break

                tool_idx = int(choice) - 1
                if 0 <= tool_idx < len(pending):
                    self._review_single_tool(pending[tool_idx])
                else:
                    print("Invalid tool number.")

            except ValueError:
                print("Please enter a valid number or 'q' to quit.")
            except KeyboardInterrupt:
                print("\n👋 Approval session ended.")
                break

    def _review_single_tool(self, request_data: Dict) -> None:
        """Review a single tool in detail."""
        tool_name = request_data["tool_name"]

        print(f"\n🔍 DETAILED REVIEW: {tool_name}")
        print("=" * 50)

        review = self.review_tool_request(tool_name)

        print(f"Tool: {review['tool_name']}")
        print(f"Binary: {review['binary_path']}")
        print(f"Risk Level: {review['risk_level']}")
        print(f"Requested Permission: {review['requested_permission']}")
        print(f"Recommended Permission: {review['recommended_permission']}")
        print()

        # Security analysis
        security = review["security_analysis"]
        print("🔒 Security Analysis:")
        for risk in security.get("potential_risks", []):
            print(f"   ⚠️  {risk}")

        if security.get("is_system_tool"):
            print("   ✅ Standard system tool")

        print()

        # TCP capabilities
        tcp_analysis = review["tcp_descriptor_analysis"]
        if "active_capabilities" in tcp_analysis:
            print("🚀 TCP Capabilities:")
            for cap in tcp_analysis["active_capabilities"]:
                print(f"   • {cap.replace('_', ' ').title()}")
        print()

        # Recommendation
        rec = review["approval_recommendation"]
        approval_text = "APPROVE" if rec["approve"] else "DENY"
        approval_emoji = "✅" if rec["approve"] else "❌"

        print(f"🤖 Recommendation: {approval_emoji} {approval_text}")
        for reason in rec["reasoning"]:
            print(f"   • {reason}")
        print()

        # Human decision
        self._prompt_human_decision(tool_name, review)

    def _prompt_human_decision(self, tool_name: str, review: Dict) -> None:
        """Prompt human for final approval decision."""
        print("👤 HUMAN DECISION REQUIRED:")
        print("Options:")
        print("  a) Approve with recommended settings")
        print("  c) Approve with custom settings")
        print("  d) Deny approval")
        print("  s) Skip for now")

        while True:
            try:
                decision = input("Your decision [a/c/d/s]: ").strip().lower()

                if decision == "a":
                    # Approve with recommended settings
                    recommended_perm = SandboxPermission(
                        review["recommended_permission"]
                    )
                    approved_by = input("Enter your name/ID: ").strip()

                    if approved_by:
                        self.sandbox.approve_tool(
                            tool_name, approved_by, recommended_perm
                        )
                        print(
                            f"✅ {tool_name} approved with {recommended_perm.value} permission"
                        )
                    break

                elif decision == "c":
                    # Custom approval
                    self._custom_approval(tool_name, review)
                    break

                elif decision == "d":
                    # Deny
                    denied_by = input("Enter your name/ID: ").strip()
                    reason = input("Denial reason: ").strip()

                    if denied_by:
                        # Move request to denied folder
                        self._deny_tool_request(tool_name, denied_by, reason)
                        print(f"❌ {tool_name} denied: {reason}")
                    break

                elif decision == "s":
                    print("⏸️  Skipped for later review")
                    break

                else:
                    print("Please enter 'a', 'c', 'd', or 's'")

            except KeyboardInterrupt:
                print("\n⏸️  Decision cancelled")
                break

    def _custom_approval(self, tool_name: str, review: Dict) -> None:
        """Handle custom approval with user-specified settings."""
        print("\n⚙️  CUSTOM APPROVAL SETTINGS:")

        # Permission level
        print("Permission levels:")
        print("  1) read_only - Tool can only read data")
        print("  2) execute_safe - Tool can execute with restrictions")
        print("  3) execute_full - Tool has full execution rights")

        while True:
            try:
                perm_choice = input("Select permission level [1-3]: ").strip()

                if perm_choice == "1":
                    permission = SandboxPermission.READ_ONLY
                    break
                elif perm_choice == "2":
                    permission = SandboxPermission.EXECUTE_SAFE
                    break
                elif perm_choice == "3":
                    permission = SandboxPermission.EXECUTE_FULL
                    break
                else:
                    print("Please enter 1, 2, or 3")
            except KeyboardInterrupt:
                print("\n⏸️  Custom approval cancelled")
                return

        # Custom arguments
        allowed_args = input("Allowed arguments (comma-separated, or blank): ").strip()
        forbidden_args = input(
            "Forbidden arguments (comma-separated, or blank): "
        ).strip()

        custom_args = {}
        if allowed_args:
            custom_args["allowed_args"] = [
                arg.strip() for arg in allowed_args.split(",")
            ]
        if forbidden_args:
            custom_args["forbidden_args"] = [
                arg.strip() for arg in forbidden_args.split(",")
            ]

        approved_by = input("Enter your name/ID: ").strip()

        if approved_by:
            self.sandbox.approve_tool(tool_name, approved_by, permission, custom_args)
            print(f"✅ {tool_name} approved with custom settings")

    def _deny_tool_request(self, tool_name: str, denied_by: str, reason: str) -> None:
        """Record tool denial for audit purposes."""
        request_file = (
            self.sandbox.sandbox_dir
            / "approval_requests"
            / f"{tool_name}_approval.json"
        )

        if request_file.exists():
            # Move to denied folder
            denied_dir = self.sandbox.sandbox_dir / "denied_requests"
            denied_dir.mkdir(exist_ok=True)

            denial_record = {
                "denied_by": denied_by,
                "denial_reason": reason,
                "denial_timestamp": datetime.now().isoformat(),
                "approval_session": self.approval_session,
            }

            # Load original request and add denial info
            with open(request_file, "r") as f:
                request_data = json.load(f)

            request_data["denial_record"] = denial_record
            request_data["status"] = "denied"

            denied_file = denied_dir / f"{tool_name}_denied.json"
            with open(denied_file, "w") as f:
                json.dump(request_data, f, indent=2)

            # Remove from pending
            request_file.unlink()

            # Log denial
            self.sandbox.logger.warning(
                f"Tool request denied: {tool_name} by {denied_by} - {reason}"
            )


def main():
    """Demonstrate human approval interface."""
    print("👤 HUMAN APPROVAL INTERFACE DEMONSTRATION")
    print("=" * 60)

    # Initialize sandbox and interface
    from pathlib import Path

    sandbox_dir = Path.cwd() / "demo_tcp_sandbox"
    sandbox = TCPSandboxManager(str(sandbox_dir))

    approval_interface = HumanApprovalInterface(sandbox)

    print("This interface provides:")
    print("✅ Comprehensive security analysis of tool requests")
    print("✅ Risk assessment and permission recommendations")
    print("✅ TCP descriptor capability analysis")
    print("✅ Interactive approval workflow")
    print("✅ Custom security settings")
    print("✅ Complete audit trails")
    print()

    print("🔐 HUMAN CONTROL MAINTAINED:")
    print("• Every tool requires explicit human approval")
    print("• Detailed security analysis provided for decisions")
    print("• Custom restrictions can be applied")
    print("• All decisions are logged and auditable")
    print("• Tools can be revoked at any time")
    print()

    print("💡 Usage:")
    print("1. Tools request approval via sandbox.request_tool_approval()")
    print("2. Human reviews via approval_interface.interactive_approval_session()")
    print("3. Approved tools become available for sandboxed execution")
    print("4. All usage is monitored and logged")


if __name__ == "__main__":
    main()
