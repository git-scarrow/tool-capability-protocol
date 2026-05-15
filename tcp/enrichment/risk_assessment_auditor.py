#!/usr/bin/env python3
"""
Risk Assessment Auditor for TCP Security Classification

Provides complete transparency and human auditability of all factors
going into security risk assessment and classification decisions.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .manpage_enricher import ManPageData, PrivilegeLevel, SecurityLevel
from .tcp_encoder import EnrichedTCPDescriptor, OperationFlags, SecurityFlags


class RiskFactor(Enum):
    """Types of risk factors considered in assessment."""

    COMMAND_NAME = "command_name"
    PRIVILEGE_REQUIREMENT = "privilege_requirement"
    DESTRUCTIVE_CAPABILITY = "destructive_capability"
    NETWORK_ACCESS = "network_access"
    FILE_OPERATIONS = "file_operations"
    SYSTEM_OPERATIONS = "system_operations"
    SECURITY_NOTES = "security_notes"
    MAN_PAGE_KEYWORDS = "man_page_keywords"
    OPTION_ANALYSIS = "option_analysis"
    EXAMPLE_ANALYSIS = "example_analysis"


@dataclass
class RiskEvidence:
    """Evidence supporting a risk assessment decision."""

    factor_type: RiskFactor
    evidence_text: str
    risk_contribution: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    source: str  # Where this evidence came from
    rationale: str  # Human-readable explanation


@dataclass
class SecurityClassificationAudit:
    """Complete audit trail for security classification decision."""

    command: str
    final_security_level: SecurityLevel
    final_privilege_level: PrivilegeLevel
    classification_timestamp: str
    classifier_version: str

    # All evidence considered
    risk_evidence: List[RiskEvidence]

    # Decision factors
    security_score: float
    privilege_score: float
    destructive_score: float

    # Final flag assignments
    security_flags: int
    operation_flags: int

    # Human-readable summary
    classification_summary: str
    security_rationale: str
    privilege_rationale: str

    # Verification data
    man_page_checksum: Optional[str]
    data_sources: List[str]


class TransparentRiskAssessor:
    """
    Transparent risk assessor that provides complete auditability
    of all factors going into security classification decisions.
    """

    def __init__(self, audit_dir: str = None):
        """Initialize transparent risk assessor."""
        self.audit_dir = Path(audit_dir or Path.cwd() / "risk_assessment_audits")
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        self.classifier_version = "1.0.0"

        # Initialize risk factor databases with detailed rationales
        self._init_risk_databases()

    def _init_risk_databases(self) -> None:
        """Initialize detailed risk assessment databases."""

        # Critical risk command patterns
        self.critical_risk_patterns = {
            "rm": {
                "base_risk": 0.9,
                "rationale": "Can permanently delete files and directories",
                "flags_when_recursive": "With -r/-R: can delete entire directory trees",
                "flags_when_force": "With -f: bypasses confirmations and permissions",
            },
            "dd": {
                "base_risk": 0.95,
                "rationale": "Direct disk operations can overwrite any data",
                "device_writes": "Writing to /dev/ devices can destroy filesystems",
                "block_operations": "Operates at block level, bypassing filesystem safety",
            },
            "mkfs": {
                "base_risk": 1.0,
                "rationale": "Creates new filesystem, destroying all existing data",
                "irreversible": "Data loss is permanent and complete",
                "system_impact": "Can render system unbootable if used on system partitions",
            },
            "fdisk": {
                "base_risk": 1.0,
                "rationale": "Disk partitioning tool can destroy partition tables",
                "data_loss": "Incorrect partitioning destroys all data on disk",
                "system_critical": "Can make system completely unbootable",
            },
        }

        # High risk command patterns
        self.high_risk_patterns = {
            "chmod": {
                "base_risk": 0.7,
                "rationale": "Changes file permissions affecting security",
                "permission_escalation": "Can make files world-writable or executable",
                "system_files": "Changing system file permissions breaks security",
            },
            "chown": {
                "base_risk": 0.8,
                "rationale": "Changes file ownership affecting access control",
                "privilege_requirement": "Often requires elevated privileges",
                "security_bypass": "Can transfer ownership of security-critical files",
            },
            "mount": {
                "base_risk": 0.85,
                "rationale": "Mounts filesystems with potential security implications",
                "privilege_escalation": "Can mount with dangerous options (suid, dev)",
                "system_modification": "Changes system filesystem layout",
            },
        }

        # Medium risk patterns
        self.medium_risk_patterns = {
            "cp": {
                "base_risk": 0.4,
                "rationale": "Copies files but can overwrite existing data",
                "overwrite_risk": "Can accidentally overwrite important files",
                "permission_preservation": "May copy permissions creating security issues",
            },
            "mv": {
                "base_risk": 0.5,
                "rationale": "Moves files with potential for data loss",
                "overwrite_risk": "Can overwrite destination files",
                "directory_operations": "Moving directories can break system dependencies",
            },
        }

        # Privilege requirement indicators
        self.privilege_indicators = {
            "root_required": [
                "must be run as root",
                "requires root privileges",
                "superuser only",
                "run as root",
                "root access required",
                "administrative privileges",
            ],
            "sudo_suggested": [
                "requires sudo",
                "may require sudo",
                "elevated privileges",
                "administrative rights",
                "superuser privileges",
            ],
            "system_level": [
                "system configuration",
                "system files",
                "kernel modules",
                "device access",
                "hardware control",
                "system services",
            ],
        }

        # Destructive operation keywords
        self.destructive_keywords = {
            "permanent_loss": [
                "destroy",
                "delete permanently",
                "irreversible",
                "cannot be undone",
            ],
            "data_modification": ["overwrite", "replace", "modify", "change"],
            "system_impact": ["format", "wipe", "erase", "clear", "purge"],
            "security_impact": [
                "remove permissions",
                "change ownership",
                "bypass security",
            ],
        }

        # Network operation risk factors
        self.network_risk_factors = {
            "data_transmission": ["upload", "download", "transfer", "send", "receive"],
            "connection_establishment": ["connect", "bind", "listen", "accept"],
            "protocol_specific": ["http", "https", "ftp", "ssh", "telnet", "smtp"],
            "security_implications": [
                "authentication",
                "encryption",
                "certificate",
                "key",
            ],
        }

    def assess_command_risk(
        self, command: str, man_data: ManPageData
    ) -> SecurityClassificationAudit:
        """
        Perform comprehensive, auditable risk assessment of a command.

        Returns complete audit trail of all factors considered.
        """
        audit = SecurityClassificationAudit(
            command=command,
            final_security_level=SecurityLevel.SAFE,  # Will be determined
            final_privilege_level=PrivilegeLevel.USER,  # Will be determined
            classification_timestamp=datetime.now().isoformat(),
            classifier_version=self.classifier_version,
            risk_evidence=[],
            security_score=0.0,
            privilege_score=0.0,
            destructive_score=0.0,
            security_flags=0,
            operation_flags=0,
            classification_summary="",
            security_rationale="",
            privilege_rationale="",
            man_page_checksum=self._calculate_manpage_checksum(man_data),
            data_sources=[],
        )

        # Analyze all risk factors with full transparency
        self._analyze_command_name_risk(command, audit)
        self._analyze_privilege_requirements(man_data, audit)
        self._analyze_destructive_capabilities(man_data, audit)
        self._analyze_network_operations(man_data, audit)
        self._analyze_file_operations(man_data, audit)
        self._analyze_system_operations(man_data, audit)
        self._analyze_security_notes(man_data, audit)
        self._analyze_manpage_keywords(man_data, audit)
        self._analyze_command_options(man_data, audit)
        self._analyze_usage_examples(man_data, audit)

        # Calculate final scores
        self._calculate_final_scores(audit)

        # Make classification decisions with rationale
        self._make_security_classification(audit)
        self._make_privilege_classification(audit)

        # Generate human-readable summary
        self._generate_classification_summary(audit)

        # Build flag values
        self._build_security_flags(audit)
        self._build_operation_flags(audit)

        return audit

    def _calculate_manpage_checksum(self, man_data: ManPageData) -> str:
        """Calculate checksum of man page data for verification."""
        data_str = f"{man_data.command}{man_data.description}{man_data.synopsis}"
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]

    def _analyze_command_name_risk(
        self, command: str, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze risk based on command name patterns."""

        # Check against critical risk patterns
        if command in self.critical_risk_patterns:
            pattern_data = self.critical_risk_patterns[command]
            evidence = RiskEvidence(
                factor_type=RiskFactor.COMMAND_NAME,
                evidence_text=f"Command '{command}' matches critical risk pattern",
                risk_contribution=pattern_data["base_risk"],
                confidence=0.95,
                source="critical_risk_patterns_db",
                rationale=pattern_data["rationale"],
            )
            audit.risk_evidence.append(evidence)
            audit.data_sources.append("Critical Risk Patterns Database")

        # Check against high risk patterns
        elif command in self.high_risk_patterns:
            pattern_data = self.high_risk_patterns[command]
            evidence = RiskEvidence(
                factor_type=RiskFactor.COMMAND_NAME,
                evidence_text=f"Command '{command}' matches high risk pattern",
                risk_contribution=pattern_data["base_risk"],
                confidence=0.9,
                source="high_risk_patterns_db",
                rationale=pattern_data["rationale"],
            )
            audit.risk_evidence.append(evidence)
            audit.data_sources.append("High Risk Patterns Database")

        # Check against medium risk patterns
        elif command in self.medium_risk_patterns:
            pattern_data = self.medium_risk_patterns[command]
            evidence = RiskEvidence(
                factor_type=RiskFactor.COMMAND_NAME,
                evidence_text=f"Command '{command}' matches medium risk pattern",
                risk_contribution=pattern_data["base_risk"],
                confidence=0.85,
                source="medium_risk_patterns_db",
                rationale=pattern_data["rationale"],
            )
            audit.risk_evidence.append(evidence)
            audit.data_sources.append("Medium Risk Patterns Database")

        else:
            # Default to low risk for unknown commands
            evidence = RiskEvidence(
                factor_type=RiskFactor.COMMAND_NAME,
                evidence_text=f"Command '{command}' not in known risk pattern databases",
                risk_contribution=0.1,
                confidence=0.5,
                source="default_classification",
                rationale="No specific risk patterns identified for this command name",
            )
            audit.risk_evidence.append(evidence)

    def _analyze_privilege_requirements(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze privilege requirements with detailed evidence."""

        content = f"{man_data.description} {man_data.synopsis}".lower()

        # Check for root requirement indicators
        for indicator in self.privilege_indicators["root_required"]:
            if indicator in content:
                evidence = RiskEvidence(
                    factor_type=RiskFactor.PRIVILEGE_REQUIREMENT,
                    evidence_text=f"Man page contains root requirement indicator: '{indicator}'",
                    risk_contribution=0.9,
                    confidence=0.95,
                    source="man_page_content_analysis",
                    rationale="Explicit statement that command requires root privileges",
                )
                audit.risk_evidence.append(evidence)
                audit.data_sources.append("Man Page Content Analysis")

        # Check for sudo requirement indicators
        for indicator in self.privilege_indicators["sudo_suggested"]:
            if indicator in content:
                evidence = RiskEvidence(
                    factor_type=RiskFactor.PRIVILEGE_REQUIREMENT,
                    evidence_text=f"Man page contains sudo requirement indicator: '{indicator}'",
                    risk_contribution=0.7,
                    confidence=0.85,
                    source="man_page_content_analysis",
                    rationale="Documentation suggests elevated privileges may be required",
                )
                audit.risk_evidence.append(evidence)

        # Check for system-level operation indicators
        for indicator in self.privilege_indicators["system_level"]:
            if indicator in content:
                evidence = RiskEvidence(
                    factor_type=RiskFactor.PRIVILEGE_REQUIREMENT,
                    evidence_text=f"Man page contains system-level indicator: '{indicator}'",
                    risk_contribution=0.6,
                    confidence=0.8,
                    source="man_page_content_analysis",
                    rationale="Command operates at system level, likely requiring privileges",
                )
                audit.risk_evidence.append(evidence)

    def _analyze_destructive_capabilities(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze destructive operation capabilities."""

        content = f"{man_data.description} {' '.join(man_data.destructive_operations)}".lower()

        # Check for permanent loss indicators
        for keyword in self.destructive_keywords["permanent_loss"]:
            if keyword in content:
                evidence = RiskEvidence(
                    factor_type=RiskFactor.DESTRUCTIVE_CAPABILITY,
                    evidence_text=f"Contains permanent loss indicator: '{keyword}'",
                    risk_contribution=0.95,
                    confidence=0.9,
                    source="destructive_capability_analysis",
                    rationale="Command can cause permanent, irreversible data loss",
                )
                audit.risk_evidence.append(evidence)

        # Check for data modification indicators
        for keyword in self.destructive_keywords["data_modification"]:
            if keyword in content:
                evidence = RiskEvidence(
                    factor_type=RiskFactor.DESTRUCTIVE_CAPABILITY,
                    evidence_text=f"Contains data modification indicator: '{keyword}'",
                    risk_contribution=0.6,
                    confidence=0.8,
                    source="destructive_capability_analysis",
                    rationale="Command can modify or overwrite existing data",
                )
                audit.risk_evidence.append(evidence)

        # Analyze specific destructive operations
        for operation in man_data.destructive_operations:
            evidence = RiskEvidence(
                factor_type=RiskFactor.DESTRUCTIVE_CAPABILITY,
                evidence_text=f"Documented destructive operation: {operation}",
                risk_contribution=0.8,
                confidence=0.85,
                source="man_page_destructive_operations",
                rationale=f"Man page explicitly documents destructive capability: {operation}",
            )
            audit.risk_evidence.append(evidence)
            audit.data_sources.append("Man Page Destructive Operations Analysis")

    def _analyze_network_operations(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze network operation capabilities."""

        for operation in man_data.network_operations:
            risk_score = 0.4  # Base network risk

            # Increase risk for specific network operations
            if any(
                keyword in operation.lower()
                for keyword in ["upload", "send", "transmit"]
            ):
                risk_score = 0.6

            evidence = RiskEvidence(
                factor_type=RiskFactor.NETWORK_ACCESS,
                evidence_text=f"Network operation capability: {operation}",
                risk_contribution=risk_score,
                confidence=0.8,
                source="man_page_network_analysis",
                rationale=f"Command can perform network operations: {operation}",
            )
            audit.risk_evidence.append(evidence)

        if man_data.network_operations:
            audit.data_sources.append("Man Page Network Operations Analysis")

    def _analyze_file_operations(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze file operation capabilities."""

        file_risk_scores = {
            "read": 0.1,
            "write": 0.5,
            "modify": 0.6,
            "delete": 0.8,
            "create": 0.3,
        }

        for operation in man_data.file_operations:
            operation_lower = operation.lower()

            for op_type, risk_score in file_risk_scores.items():
                if op_type in operation_lower:
                    evidence = RiskEvidence(
                        factor_type=RiskFactor.FILE_OPERATIONS,
                        evidence_text=f"File operation capability: {operation}",
                        risk_contribution=risk_score,
                        confidence=0.85,
                        source="man_page_file_analysis",
                        rationale=f"Command can {op_type} files with risk level {risk_score}",
                    )
                    audit.risk_evidence.append(evidence)

        if man_data.file_operations:
            audit.data_sources.append("Man Page File Operations Analysis")

    def _analyze_system_operations(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze system-level operations."""

        system_risk_keywords = {
            "process": 0.7,
            "service": 0.8,
            "kernel": 0.9,
            "device": 0.8,
            "mount": 0.8,
            "module": 0.9,
        }

        for operation in man_data.system_operations:
            operation_lower = operation.lower()

            for keyword, risk_score in system_risk_keywords.items():
                if keyword in operation_lower:
                    evidence = RiskEvidence(
                        factor_type=RiskFactor.SYSTEM_OPERATIONS,
                        evidence_text=f"System operation: {operation}",
                        risk_contribution=risk_score,
                        confidence=0.9,
                        source="man_page_system_analysis",
                        rationale=f"Command performs system-level operation involving {keyword}",
                    )
                    audit.risk_evidence.append(evidence)

        if man_data.system_operations:
            audit.data_sources.append("Man Page System Operations Analysis")

    def _analyze_security_notes(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze explicit security notes in man page."""

        for note in man_data.security_notes:
            note_lower = note.lower()

            # Determine risk contribution based on note content
            risk_score = 0.3  # Base risk for any security note

            if any(
                keyword in note_lower for keyword in ["dangerous", "caution", "warning"]
            ):
                risk_score = 0.8
            elif any(
                keyword in note_lower for keyword in ["careful", "security", "risk"]
            ):
                risk_score = 0.6

            evidence = RiskEvidence(
                factor_type=RiskFactor.SECURITY_NOTES,
                evidence_text=f"Security note: {note[:100]}...",
                risk_contribution=risk_score,
                confidence=0.95,
                source="man_page_security_notes",
                rationale="Explicit security warning in man page documentation",
            )
            audit.risk_evidence.append(evidence)

        if man_data.security_notes:
            audit.data_sources.append("Man Page Security Notes")

    def _analyze_manpage_keywords(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze risk-indicating keywords in man page content."""

        high_risk_keywords = [
            "destroy",
            "delete",
            "remove",
            "erase",
            "wipe",
            "format",
            "overwrite",
            "replace",
            "modify",
            "change",
            "alter",
        ]

        content = f"{man_data.description} {man_data.synopsis}".lower()

        for keyword in high_risk_keywords:
            if keyword in content:
                evidence = RiskEvidence(
                    factor_type=RiskFactor.MAN_PAGE_KEYWORDS,
                    evidence_text=f"High-risk keyword found: '{keyword}'",
                    risk_contribution=0.5,
                    confidence=0.7,
                    source="keyword_analysis",
                    rationale=f"Presence of high-risk keyword '{keyword}' indicates potential for data modification",
                )
                audit.risk_evidence.append(evidence)

        audit.data_sources.append("Man Page Keyword Analysis")

    def _analyze_command_options(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze command options for risk indicators."""

        dangerous_option_patterns = {
            "-f": "Force operation, bypassing safety checks",
            "--force": "Force operation, bypassing safety checks",
            "-r": "Recursive operation affecting multiple files/directories",
            "-R": "Recursive operation affecting multiple files/directories",
            "--recursive": "Recursive operation affecting multiple files/directories",
            "--no-preserve-root": "Disables protection of root directory",
        }

        for option in man_data.options:
            flag = option.get("flag", "")
            description = option.get("description", "").lower()

            # Check for dangerous option patterns
            for pattern, rationale in dangerous_option_patterns.items():
                if pattern in flag:
                    evidence = RiskEvidence(
                        factor_type=RiskFactor.OPTION_ANALYSIS,
                        evidence_text=f"Dangerous option: {flag} - {description[:50]}...",
                        risk_contribution=0.7,
                        confidence=0.8,
                        source="option_analysis",
                        rationale=rationale,
                    )
                    audit.risk_evidence.append(evidence)

            # Check for privilege-related options
            if any(keyword in description for keyword in ["root", "sudo", "privilege"]):
                evidence = RiskEvidence(
                    factor_type=RiskFactor.OPTION_ANALYSIS,
                    evidence_text=f"Privilege-related option: {flag}",
                    risk_contribution=0.6,
                    confidence=0.8,
                    source="option_analysis",
                    rationale="Option description mentions privilege requirements",
                )
                audit.risk_evidence.append(evidence)

        if man_data.options:
            audit.data_sources.append("Command Options Analysis")

    def _analyze_usage_examples(
        self, man_data: ManPageData, audit: SecurityClassificationAudit
    ) -> None:
        """Analyze usage examples for risk patterns."""

        for example in man_data.examples:
            example_lower = example.lower()

            # Check for dangerous usage patterns
            if any(
                pattern in example_lower
                for pattern in ["/dev/", "rm -rf", "dd if=", "format"]
            ):
                evidence = RiskEvidence(
                    factor_type=RiskFactor.EXAMPLE_ANALYSIS,
                    evidence_text=f"Dangerous usage example: {example[:50]}...",
                    risk_contribution=0.8,
                    confidence=0.9,
                    source="example_analysis",
                    rationale="Usage example demonstrates potentially dangerous operation",
                )
                audit.risk_evidence.append(evidence)

            # Check for system file operations
            elif any(
                pattern in example_lower
                for pattern in ["/etc/", "/var/", "/usr/", "/sys/"]
            ):
                evidence = RiskEvidence(
                    factor_type=RiskFactor.EXAMPLE_ANALYSIS,
                    evidence_text=f"System file operation example: {example[:50]}...",
                    risk_contribution=0.6,
                    confidence=0.8,
                    source="example_analysis",
                    rationale="Usage example shows operation on system directories",
                )
                audit.risk_evidence.append(evidence)

        if man_data.examples:
            audit.data_sources.append("Usage Examples Analysis")

    def _calculate_final_scores(self, audit: SecurityClassificationAudit) -> None:
        """Calculate weighted final risk scores."""

        # Calculate security score (weighted average of all evidence)
        total_weighted_risk = 0.0
        total_confidence = 0.0

        for evidence in audit.risk_evidence:
            weight = evidence.confidence * evidence.risk_contribution
            total_weighted_risk += weight
            total_confidence += evidence.confidence

        audit.security_score = total_weighted_risk / max(total_confidence, 1.0)

        # Calculate privilege score (focus on privilege-related evidence)
        privilege_evidence = [
            e
            for e in audit.risk_evidence
            if e.factor_type == RiskFactor.PRIVILEGE_REQUIREMENT
        ]

        if privilege_evidence:
            audit.privilege_score = max(e.risk_contribution for e in privilege_evidence)
        else:
            audit.privilege_score = 0.1  # Default low privilege requirement

        # Calculate destructive score (focus on destructive capabilities)
        destructive_evidence = [
            e
            for e in audit.risk_evidence
            if e.factor_type == RiskFactor.DESTRUCTIVE_CAPABILITY
        ]

        if destructive_evidence:
            audit.destructive_score = max(
                e.risk_contribution for e in destructive_evidence
            )
        else:
            audit.destructive_score = 0.0

    def _make_security_classification(self, audit: SecurityClassificationAudit) -> None:
        """Make final security level classification with rationale."""

        score = audit.security_score

        if score >= 0.9 or audit.destructive_score >= 0.9:
            audit.final_security_level = SecurityLevel.CRITICAL
            audit.security_rationale = f"Critical risk (score: {score:.2f}) due to high potential for irreversible system damage"
        elif score >= 0.7:
            audit.final_security_level = SecurityLevel.HIGH_RISK
            audit.security_rationale = f"High risk (score: {score:.2f}) due to potential for significant system impact"
        elif score >= 0.4:
            audit.final_security_level = SecurityLevel.MEDIUM_RISK
            audit.security_rationale = f"Medium risk (score: {score:.2f}) due to potential for data modification"
        elif score >= 0.2:
            audit.final_security_level = SecurityLevel.LOW_RISK
            audit.security_rationale = (
                f"Low risk (score: {score:.2f}) with minimal security implications"
            )
        else:
            audit.final_security_level = SecurityLevel.SAFE
            audit.security_rationale = f"Safe (score: {score:.2f}) with no significant security risks identified"

    def _make_privilege_classification(
        self, audit: SecurityClassificationAudit
    ) -> None:
        """Make privilege requirement classification with rationale."""

        score = audit.privilege_score

        if score >= 0.8:
            audit.final_privilege_level = PrivilegeLevel.ROOT
            audit.privilege_rationale = f"Requires root privileges (score: {score:.2f}) based on documentation and operation type"
        elif score >= 0.6:
            audit.final_privilege_level = PrivilegeLevel.SUDO
            audit.privilege_rationale = (
                f"Requires elevated privileges (score: {score:.2f}) for safe operation"
            )
        else:
            audit.final_privilege_level = PrivilegeLevel.USER
            audit.privilege_rationale = (
                f"User-level privileges sufficient (score: {score:.2f})"
            )

    def _generate_classification_summary(
        self, audit: SecurityClassificationAudit
    ) -> None:
        """Generate human-readable classification summary."""

        evidence_count = len(audit.risk_evidence)
        high_risk_evidence = len(
            [e for e in audit.risk_evidence if e.risk_contribution >= 0.7]
        )

        audit.classification_summary = (
            f"Command '{audit.command}' classified as {audit.final_security_level.value} "
            f"requiring {audit.final_privilege_level.value} privileges. "
            f"Assessment based on {evidence_count} pieces of evidence, "
            f"including {high_risk_evidence} high-risk indicators. "
            f"Final security score: {audit.security_score:.2f}"
        )

    def _build_security_flags(self, audit: SecurityClassificationAudit) -> None:
        """Build security flags for TCP descriptor."""
        flags = 0

        # Security level flags
        if audit.final_security_level == SecurityLevel.CRITICAL:
            flags |= 1 << SecurityFlags.CRITICAL
        elif audit.final_security_level == SecurityLevel.HIGH_RISK:
            flags |= 1 << SecurityFlags.HIGH_RISK
        elif audit.final_security_level == SecurityLevel.MEDIUM_RISK:
            flags |= 1 << SecurityFlags.MEDIUM_RISK
        elif audit.final_security_level == SecurityLevel.LOW_RISK:
            flags |= 1 << SecurityFlags.LOW_RISK
        else:
            flags |= 1 << SecurityFlags.SAFE

        # Privilege flags
        if audit.final_privilege_level == PrivilegeLevel.ROOT:
            flags |= 1 << SecurityFlags.REQUIRES_ROOT
        elif audit.final_privilege_level == PrivilegeLevel.SUDO:
            flags |= 1 << SecurityFlags.REQUIRES_SUDO
        else:
            flags |= 1 << SecurityFlags.REQUIRES_USER

        # Additional flags based on evidence
        if audit.destructive_score >= 0.7:
            flags |= 1 << SecurityFlags.DESTRUCTIVE

        if audit.destructive_score >= 0.9:
            flags |= 1 << SecurityFlags.IRREVERSIBLE

        # Check for specific capabilities
        for evidence in audit.risk_evidence:
            if evidence.factor_type == RiskFactor.NETWORK_ACCESS:
                flags |= 1 << SecurityFlags.NETWORK_ACCESS
            elif evidence.factor_type == RiskFactor.FILE_OPERATIONS:
                if "write" in evidence.evidence_text.lower():
                    flags |= 1 << SecurityFlags.FILE_WRITE
                if "delete" in evidence.evidence_text.lower():
                    flags |= 1 << SecurityFlags.FILE_DELETE
            elif evidence.factor_type == RiskFactor.SYSTEM_OPERATIONS:
                flags |= 1 << SecurityFlags.SYSTEM_MODIFY

        audit.security_flags = flags

    def _build_operation_flags(self, audit: SecurityClassificationAudit) -> None:
        """Build operation flags for TCP descriptor."""
        # This would be implemented based on capability analysis
        audit.operation_flags = 0  # Simplified for this example

    def save_audit_report(self, audit: SecurityClassificationAudit) -> str:
        """Save detailed audit report to file."""

        audit_filename = (
            f"{audit.command}_risk_audit_{audit.classification_timestamp[:10]}.json"
        )
        audit_path = self.audit_dir / audit_filename

        # Convert to serializable format
        audit_data = asdict(audit)

        # Add metadata
        audit_data["audit_metadata"] = {
            "total_evidence_pieces": len(audit.risk_evidence),
            "high_risk_evidence_count": len(
                [e for e in audit.risk_evidence if e.risk_contribution >= 0.7]
            ),
            "evidence_sources": list(set(audit.data_sources)),
            "classifier_confidence": sum(e.confidence for e in audit.risk_evidence)
            / len(audit.risk_evidence),
        }

        with open(audit_path, "w") as f:
            json.dump(audit_data, f, indent=2, default=str)

        return str(audit_path)

    def generate_human_readable_report(self, audit: SecurityClassificationAudit) -> str:
        """Generate human-readable audit report."""

        report_lines = [
            f"SECURITY RISK ASSESSMENT AUDIT REPORT",
            f"=" * 60,
            f"Command: {audit.command}",
            f"Assessment Date: {audit.classification_timestamp}",
            f"Classifier Version: {audit.classifier_version}",
            f"",
            f"FINAL CLASSIFICATION:",
            f"-" * 30,
            f"Security Level: {audit.final_security_level.value}",
            f"Privilege Level: {audit.final_privilege_level.value}",
            f"Security Score: {audit.security_score:.3f}",
            f"Destructive Score: {audit.destructive_score:.3f}",
            f"",
            f"CLASSIFICATION RATIONALE:",
            f"-" * 30,
            f"Security: {audit.security_rationale}",
            f"Privilege: {audit.privilege_rationale}",
            f"",
            f"EVIDENCE ANALYSIS ({len(audit.risk_evidence)} pieces):",
            f"-" * 30,
        ]

        # Group evidence by type
        evidence_by_type = {}
        for evidence in audit.risk_evidence:
            factor_type = evidence.factor_type.value
            if factor_type not in evidence_by_type:
                evidence_by_type[factor_type] = []
            evidence_by_type[factor_type].append(evidence)

        for factor_type, evidence_list in evidence_by_type.items():
            report_lines.append(f"\n{factor_type.replace('_', ' ').title()}:")

            for evidence in sorted(
                evidence_list, key=lambda x: x.risk_contribution, reverse=True
            ):
                risk_level = (
                    "HIGH"
                    if evidence.risk_contribution >= 0.7
                    else "MED"
                    if evidence.risk_contribution >= 0.4
                    else "LOW"
                )
                report_lines.append(f"  [{risk_level}] {evidence.evidence_text}")
                report_lines.append(
                    f"       Risk: {evidence.risk_contribution:.2f}, Confidence: {evidence.confidence:.2f}"
                )
                report_lines.append(f"       Rationale: {evidence.rationale}")

        report_lines.extend([f"", f"DATA SOURCES:", f"-" * 30])

        for source in sorted(set(audit.data_sources)):
            report_lines.append(f"• {source}")

        report_lines.extend(
            [
                f"",
                f"SECURITY FLAGS: 0x{audit.security_flags:08x}",
                f"MAN PAGE CHECKSUM: {audit.man_page_checksum}",
                f"",
                f"This assessment provides complete transparency into all factors",
                f"considered in the security classification decision.",
            ]
        )

        return "\n".join(report_lines)


def main():
    """Demonstrate transparent risk assessment with full auditability."""
    print("🔍 TRANSPARENT RISK ASSESSMENT WITH FULL AUDITABILITY")
    print("=" * 70)
    print("Demonstrating complete transparency in security classification...")
    print()

    # This would integrate with the man page enricher
    from .manpage_enricher import ManPageEnricher

    enricher = ManPageEnricher()
    assessor = TransparentRiskAssessor()

    # Test with a critical risk command
    print("📋 Analyzing 'rm' command for demonstration...")

    # Get man page data (would be real data in practice)
    man_data = enricher.enrich_command("rm")

    if man_data:
        # Perform transparent risk assessment
        audit = assessor.assess_command_risk("rm", man_data)

        # Save audit report
        audit_path = assessor.save_audit_report(audit)
        print(f"✅ Audit report saved: {audit_path}")

        # Generate human-readable report
        readable_report = assessor.generate_human_readable_report(audit)

        print("\n📄 HUMAN-READABLE AUDIT REPORT:")
        print("-" * 50)
        print(
            readable_report[:1500] + "..."
            if len(readable_report) > 1500
            else readable_report
        )

        print(f"\n🎯 TRANSPARENCY BENEFITS:")
        print("-" * 50)
        print("✅ Every factor in classification decision is documented")
        print("✅ Risk contributions are quantified and sourced")
        print("✅ Human administrators can verify and audit decisions")
        print("✅ Evidence trails enable regulatory compliance")
        print("✅ Bias detection and correction possible")
        print("✅ Continuous improvement of classification accuracy")

    else:
        print("❌ Could not retrieve man page data for demonstration")


if __name__ == "__main__":
    main()
