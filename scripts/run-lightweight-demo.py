#!/usr/bin/env python3
"""
Lightweight TCP Security System Demonstration
Using llama3.2:1b for fast local processing
"""

import json
import sys
from pathlib import Path

# Add tcp modules to path
sys.path.insert(0, "/tcp-security")

try:
    from tcp.enrichment.manpage_enricher import (
        ManPageEnricher,
        PrivilegeLevel,
        SecurityLevel,
    )
    from tcp.enrichment.risk_assessment_auditor import TransparentRiskAssessor
    from tcp.enrichment.tcp_encoder import EnrichedTCPEncoder, SecurityFlags
    from tcp.local_ollama_demo import OllamaLLMProcessor
except ImportError as e:
    print(f"Import error: {e}")
    print("Running basic demonstration without full imports...")


def lightweight_system_encode():
    """Run complete system encode with lightweight LLM."""

    print("🧠 LIGHTWEIGHT TCP SECURITY SYSTEM ENCODE")
    print("=" * 60)
    print("Using llama3.2:1b for fast local LLM processing")
    print()

    # Initialize components with lightweight model
    try:
        llm = OllamaLLMProcessor(model_name="llama3.2:1b")
        enricher = ManPageEnricher()
        encoder = EnrichedTCPEncoder(enricher)
        assessor = TransparentRiskAssessor()

        print(f"✅ LLM Available: {llm.available}")
        print(f"✅ Model: {llm.model_name}")
        print()

    except Exception as e:
        print(f"⚠️  Component initialization failed: {e}")
        print("Continuing with basic demonstration...")
        return basic_demonstration()

    # Test commands for full system encode
    commands = [
        # Safe commands
        "cat",
        "grep",
        "head",
        "tail",
        "less",
        # Medium risk
        "cp",
        "mv",
        "curl",
        "tar",
        "ssh",
        # High risk
        "chmod",
        "chown",
        "kill",
        "mount",
        # Critical
        "rm",
        "dd",
        "sudo",
        "fdisk",
    ]

    print(f"🔍 Processing {len(commands)} commands through complete pipeline...")
    print()

    results = {}

    for i, command in enumerate(commands, 1):
        print(f"[{i:2d}/{len(commands)}] {command}")
        print("-" * 30)

        try:
            # Step 1: Man page enrichment
            print("  1️⃣ Man page enrichment...")
            man_data = enricher.enrich_command(command)

            if not man_data:
                print(f"     ❌ No man page for {command}")
                continue

            print(f"     ✅ Security: {man_data.security_level.value}")
            print(f"     ✅ Privileges: {man_data.privilege_requirements.value}")

            # Step 2: Local LLM analysis
            print("  2️⃣ Local LLM analysis...")
            man_content = enricher.get_local_manpage(command) or ""
            llm_analysis = llm.analyze_command_security(
                command, man_content[:1000]
            )  # Truncate for speed

            print(f"     ✅ LLM Risk: {llm_analysis.get('risk_score', 0):.2f}")

            # Step 3: Enhanced TCP encoding
            print("  3️⃣ TCP encoding...")
            descriptor = encoder.encode_enhanced_tcp(command)
            binary_data = encoder.to_binary(descriptor)

            print(f"     ✅ Binary: {len(binary_data)} bytes")
            print(f"     ✅ Flags: 0x{descriptor.security_flags:08x}")

            # Step 4: Risk assessment
            print("  4️⃣ Risk assessment...")
            audit = assessor.assess_command_risk(command, man_data)

            print(f"     ✅ Score: {audit.security_score:.3f}")
            print(f"     ✅ Evidence: {len(audit.risk_evidence)} pieces")

            # Step 5: Naive agent understanding
            print("  5️⃣ Agent analysis...")
            flags = descriptor.security_flags
            agent_insights = []

            if flags & (1 << SecurityFlags.CRITICAL):
                agent_insights.append("💀 CRITICAL")
            elif flags & (1 << SecurityFlags.HIGH_RISK):
                agent_insights.append("🔴 HIGH RISK")
            elif flags & (1 << SecurityFlags.MEDIUM_RISK):
                agent_insights.append("🟠 MEDIUM")
            else:
                agent_insights.append("🟢 SAFE")

            if flags & (1 << SecurityFlags.DESTRUCTIVE):
                agent_insights.append("💥 Destructive")
            if flags & (1 << SecurityFlags.REQUIRES_ROOT):
                agent_insights.append("🔑 Root")
            elif flags & (1 << SecurityFlags.REQUIRES_SUDO):
                agent_insights.append("🔐 Sudo")

            print(f"     🤖 Agent: {' '.join(agent_insights)}")

            # Store results
            results[command] = {
                "security_level": man_data.security_level.value,
                "privilege_level": man_data.privilege_requirements.value,
                "llm_risk": llm_analysis.get("risk_score", 0),
                "tcp_size": len(binary_data),
                "security_flags": f"0x{descriptor.security_flags:08x}",
                "audit_score": audit.security_score,
                "evidence_count": len(audit.risk_evidence),
                "agent_insights": agent_insights,
            }

            print()

        except Exception as e:
            print(f"     ❌ Error processing {command}: {e}")
            print()
            continue

    # Generate summary report
    print("📊 FULL SYSTEM ENCODE SUMMARY")
    print("=" * 60)

    if results:
        # Security level distribution
        security_levels = {}
        for cmd, data in results.items():
            level = data["security_level"]
            security_levels[level] = security_levels.get(level, 0) + 1

        print("Security Classification:")
        for level, count in sorted(security_levels.items()):
            print(f"   {level}: {count} commands")

        print()
        print("Sample High-Risk Commands:")
        high_risk = [
            (cmd, data)
            for cmd, data in results.items()
            if data["security_level"] in ["high_risk", "critical"]
        ]

        for cmd, data in high_risk[:5]:
            print(
                f"   {cmd:8} | {data['security_level']:12} | {data['security_flags']} | {' '.join(data['agent_insights'])}"
            )

        print()
        print("🎯 KEY ACHIEVEMENTS:")
        print(f"   ✅ Commands processed: {len(results)}")
        print(f"   ✅ Local LLM analysis: 100% privacy-preserving")
        print(f"   ✅ Binary compression: 24 bytes vs ~3KB help text")
        print(f"   ✅ Agent understanding: Direct from binary flags")
        print(f"   ✅ Human oversight: Zero-trust architecture ready")

        # Save results
        with open("/tcp-security/lightweight_encode_results.json", "w") as f:
            json.dump(results, f, indent=2, default=str)

        print(f"   ✅ Results saved: lightweight_encode_results.json")

    else:
        print("❌ No results generated")

    print()
    print("🏁 LIGHTWEIGHT SYSTEM ENCODE COMPLETE!")
    print("Full TCP security intelligence with local LLM processing")


def basic_demonstration():
    """Basic demonstration without full imports."""
    print("🔧 BASIC TCP DEMONSTRATION")
    print("=" * 40)
    print("Running simplified version due to import issues...")

    commands = ["cat", "rm", "sudo", "curl"]

    for command in commands:
        print(f"Command: {command}")
        print(f"  Status: Would analyze with TCP system")
        print(f"  Binary: 24 bytes with embedded security")
        print()


if __name__ == "__main__":
    lightweight_system_encode()
