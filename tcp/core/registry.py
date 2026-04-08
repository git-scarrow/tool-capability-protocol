"""Capability registry for managing tool descriptors."""

import logging
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, List, Optional, Set, Tuple

from .descriptors import CapabilityDescriptor


UTC = timezone.utc

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class CapabilityRegistry:
    """Thread-safe registry for tool capability descriptors."""

    def __init__(self, max_entries: int = 1000):
        """Initialize the registry."""
        self._tools: Dict[str, Dict[str, CapabilityDescriptor]] = {}
        self._lock = RLock()
        self._max_entries = max_entries
        self._access_times: Dict[Tuple[str, str], datetime] = {}

    def register(self, descriptor: CapabilityDescriptor) -> bool:
        """Register a tool capability descriptor."""
        with self._lock:
            try:
                # Check if we need to make space
                if len(self._get_all_tools()) >= self._max_entries:
                    self._evict_oldest()

                # Register the tool
                if descriptor.name not in self._tools:
                    self._tools[descriptor.name] = {}

                self._tools[descriptor.name][descriptor.version] = descriptor
                self._access_times[
                    (descriptor.name, descriptor.version)
                ] = _utc_now()

                logger.info(f"Registered tool: {descriptor.name} v{descriptor.version}")
                return True

            except Exception as e:
                logger.error(f"Failed to register tool {descriptor.name}: {e}")
                return False

    def unregister(self, name: str, version: Optional[str] = None) -> bool:
        """Unregister a tool or specific version."""
        with self._lock:
            try:
                if name not in self._tools:
                    return False

                if version is None:
                    # Remove all versions
                    for v in list(self._tools[name].keys()):
                        self._access_times.pop((name, v), None)
                    del self._tools[name]
                    logger.info(f"Unregistered all versions of tool: {name}")
                else:
                    # Remove specific version
                    if version in self._tools[name]:
                        del self._tools[name][version]
                        self._access_times.pop((name, version), None)

                        # Clean up empty tool entry
                        if not self._tools[name]:
                            del self._tools[name]

                        logger.info(f"Unregistered tool: {name} v{version}")
                    else:
                        return False

                return True

            except Exception as e:
                logger.error(f"Failed to unregister tool {name}: {e}")
                return False

    def get(
        self, name: str, version: Optional[str] = None
    ) -> Optional[CapabilityDescriptor]:
        """Get a tool descriptor by name and version."""
        with self._lock:
            if name not in self._tools:
                return None

            if version is None:
                # Get latest version
                versions = self._tools[name]
                if not versions:
                    return None

                # Sort versions and get the latest
                latest_version = max(versions.keys())
                descriptor = versions[latest_version]
            else:
                # Get specific version
                if version not in self._tools[name]:
                    return None
                descriptor = self._tools[name][version]

            # Update access time
            self._access_times[
                (descriptor.name, descriptor.version)
            ] = _utc_now()

            return descriptor

    def list_tools(self, name_pattern: Optional[str] = None) -> List[str]:
        """List all registered tool names."""
        with self._lock:
            tools = list(self._tools.keys())

            if name_pattern:
                import fnmatch

                tools = [t for t in tools if fnmatch.fnmatch(t, name_pattern)]

            return sorted(tools)

    def list_versions(self, name: str) -> List[str]:
        """List all versions of a specific tool."""
        with self._lock:
            if name not in self._tools:
                return []

            return sorted(self._tools[name].keys())

    def list_all(self) -> List[CapabilityDescriptor]:
        """List all registered descriptors."""
        with self._lock:
            descriptors = []
            for tool_versions in self._tools.values():
                descriptors.extend(tool_versions.values())
            return descriptors

    def find_by_capability(
        self, capability: str, value: Optional[str] = None
    ) -> List[CapabilityDescriptor]:
        """Find tools by capability."""
        with self._lock:
            results = []

            for descriptor in self._get_all_tools():
                if self._has_capability(descriptor, capability, value):
                    results.append(descriptor)

            return results

    def find_by_format(
        self, format_name: str, is_input: bool = True
    ) -> List[CapabilityDescriptor]:
        """Find tools that support a specific format."""
        with self._lock:
            results = []

            for descriptor in self._get_all_tools():
                formats = (
                    descriptor.input_formats if is_input else descriptor.output_formats
                )

                if any(
                    format_name.lower() == f.name.lower()
                    or format_name.lower() in [ext.lower() for ext in f.extensions]
                    for f in formats
                ):
                    results.append(descriptor)

            return results

    def find_by_command(self, command_name: str) -> List[CapabilityDescriptor]:
        """Find tools that have a specific command."""
        with self._lock:
            results = []

            for descriptor in self._get_all_tools():
                if descriptor.get_command(command_name):
                    results.append(descriptor)

            return results

    def get_statistics(self) -> Dict[str, int]:
        """Get registry statistics."""
        with self._lock:
            all_tools = self._get_all_tools()

            return {
                "total_tools": len(self._tools),
                "total_descriptors": len(all_tools),
                "total_commands": sum(len(d.commands) for d in all_tools),
                "total_formats": sum(
                    len(d.input_formats) + len(d.output_formats) for d in all_tools
                ),
                "memory_usage_bytes": self._estimate_memory_usage(),
            }

    def clear(self) -> None:
        """Clear all registered tools."""
        with self._lock:
            self._tools.clear()
            self._access_times.clear()
            logger.info("Cleared all registered tools")

    def cleanup_expired(self, max_age_hours: int = 24) -> int:
        """Remove tools that haven't been accessed recently."""
        with self._lock:
            cutoff_time = _utc_now() - timedelta(hours=max_age_hours)
            expired = []

            for (name, version), access_time in self._access_times.items():
                if access_time < cutoff_time:
                    expired.append((name, version))

            count = 0
            for name, version in expired:
                if self.unregister(name, version):
                    count += 1

            logger.info(f"Cleaned up {count} expired tool descriptors")
            return count

    def _get_all_tools(self) -> List[CapabilityDescriptor]:
        """Get all tool descriptors (internal use)."""
        descriptors = []
        for tool_versions in self._tools.values():
            descriptors.extend(tool_versions.values())
        return descriptors

    def _has_capability(
        self, descriptor: CapabilityDescriptor, capability: str, value: Optional[str]
    ) -> bool:
        """Check if descriptor has a specific capability."""
        # This is a simplified implementation
        # In practice, this would be more sophisticated

        if capability == "format":
            return value is None or descriptor.supports_format(value)
        elif capability == "command":
            return value is None or descriptor.get_command(value) is not None
        elif capability == "processing_mode":
            if value:
                try:
                    from .descriptors import ProcessingMode

                    mode = ProcessingMode[value.upper()]
                    return mode in descriptor.processing_modes
                except (KeyError, AttributeError):
                    return False
            return len(descriptor.processing_modes) > 0
        else:
            # Generic attribute check
            return hasattr(descriptor, capability)

    def _evict_oldest(self) -> None:
        """Evict the oldest accessed tool."""
        if not self._access_times:
            return

        # Find oldest access time
        oldest_key = min(self._access_times.keys(), key=lambda k: self._access_times[k])
        name, version = oldest_key

        # Remove the tool
        self.unregister(name, version)
        logger.debug(f"Evicted oldest tool: {name} v{version}")

    def _estimate_memory_usage(self) -> int:
        """Estimate memory usage in bytes."""
        import sys

        total_size = 0
        total_size += sys.getsizeof(self._tools)
        total_size += sys.getsizeof(self._access_times)

        for tool_versions in self._tools.values():
            total_size += sys.getsizeof(tool_versions)
            for descriptor in tool_versions.values():
                total_size += sys.getsizeof(descriptor)

        return total_size
