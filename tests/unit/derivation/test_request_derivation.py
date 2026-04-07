"""Tests for TCP-DS-2 request derivation contract."""

import pytest
from tcp.core.descriptors import CapabilityFlags
from tcp.harness.models import ToolSelectionRequest
from tcp.derivation.request_derivation import (
    derive_request,
    classify_unscorable,
    get_equivalence_class,
    SessionStartEvent,
    PostToolUseEvent,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def default_session(permission_mode: str = "default", cwd: str = "/home/user/projects/myapp") -> SessionStartEvent:
    return SessionStartEvent(
        session_id="test-session-1",
        permission_mode=permission_mode,
        cwd=cwd,
    )


def tool_event(tool_name: str, tool_input: dict | None = None) -> PostToolUseEvent:
    return PostToolUseEvent(
        session_id="test-session-1",
        tool_name=tool_name,
        tool_input=tool_input or {},
        tool_use_id="use-1",
        tool_result_status="ok",
    )


# ── derive_request: capability flags ─────────────────────────────────────────

class TestCapabilityFlagDerivation:
    def test_file_read_prompt_sets_files_flag(self):
        req = derive_request("Read the contents of config.yaml", default_session())
        assert req.required_capability_flags & int(CapabilityFlags.SUPPORTS_FILES)

    def test_file_write_prompt_sets_files_flag(self):
        req = derive_request("Write the output to /tmp/result.json", default_session())
        assert req.required_capability_flags & int(CapabilityFlags.SUPPORTS_FILES)

    def test_network_fetch_prompt_sets_network_flag(self):
        req = derive_request("Fetch https://api.example.com/status and return JSON", default_session())
        assert req.required_capability_flags & int(CapabilityFlags.SUPPORTS_NETWORK)

    def test_sudo_prompt_sets_auth_required_flag(self):
        req = derive_request("Run sudo systemctl restart nginx", default_session())
        assert req.required_capability_flags & int(CapabilityFlags.AUTH_REQUIRED)

    def test_neutral_prompt_sets_no_flags(self):
        req = derive_request("What is 2 + 2?", default_session())
        assert req.required_capability_flags == 0

    def test_too_short_prompt_sets_no_flags(self):
        req = derive_request("ok", default_session())
        assert req.required_capability_flags == 0

    def test_url_in_prompt_sets_network_flag(self):
        req = derive_request("curl https://httpbin.org/get", default_session())
        assert req.required_capability_flags & int(CapabilityFlags.SUPPORTS_NETWORK)

    def test_multi_flag_prompt(self):
        req = derive_request(
            "Download the file from https://example.com and save it to /tmp/data.csv",
            default_session(),
        )
        flags = req.required_capability_flags
        assert flags & int(CapabilityFlags.SUPPORTS_FILES)
        assert flags & int(CapabilityFlags.SUPPORTS_NETWORK)


# ── derive_request: output formats ───────────────────────────────────────────

class TestOutputFormatDerivation:
    def test_default_output_format_is_text(self):
        req = derive_request("Show me the file contents", default_session())
        assert "text" in req.required_output_formats

    def test_json_keyword_adds_json_format(self):
        req = derive_request("Return the result as JSON", default_session())
        assert "json" in req.required_output_formats

    def test_structured_output_keyword_adds_json(self):
        req = derive_request("Give me structured output", default_session())
        assert "json" in req.required_output_formats

    def test_binary_file_extension_sets_binary(self):
        req = derive_request("Save the result to output.png", default_session())
        assert "binary" in req.required_output_formats

    def test_image_keyword_adds_binary(self):
        req = derive_request("Take a screenshot of the page", default_session())
        assert "binary" in req.required_output_formats


# ── derive_request: environment → deny_mask + approval_mode ──────────────────

class TestEnvironmentMapping:
    def test_default_permission_mode_sets_prompt_approval(self):
        req = derive_request("Do something", default_session("default"))
        assert req.require_auto_approval is False  # default = PROMPT = not auto

    def test_bypass_permissions_sets_auto_approval(self):
        req = derive_request("Do something", default_session("bypassPermissions"))
        assert req.require_auto_approval is True

    def test_plan_mode_disables_auto_approval(self):
        req = derive_request("Do something", default_session("plan"))
        assert req.require_auto_approval is False

    def test_system_cwd_adds_deny_flags(self):
        req = derive_request("Edit the config", default_session(cwd="/etc/nginx"))
        # System cwd should result in AUTH_REQUIRED being set even for benign prompts
        assert req.required_capability_flags & int(CapabilityFlags.AUTH_REQUIRED)


# ── get_equivalence_class ─────────────────────────────────────────────────────

class TestEquivalenceClass:
    def test_read_tool_maps_to_file_read(self):
        assert get_equivalence_class("Read", {}) == "FILE_READ"

    def test_mcp_filesystem_read_maps_to_file_read(self):
        assert get_equivalence_class("mcp__filesystem__read_file", {}) == "FILE_READ"

    def test_write_tool_maps_to_file_write(self):
        assert get_equivalence_class("Write", {}) == "FILE_WRITE"

    def test_edit_tool_maps_to_file_edit(self):
        assert get_equivalence_class("Edit", {}) == "FILE_EDIT"

    def test_grep_maps_to_search_text(self):
        assert get_equivalence_class("Grep", {}) == "SEARCH_TEXT"

    def test_glob_maps_to_search_files(self):
        assert get_equivalence_class("Glob", {}) == "SEARCH_FILES"

    def test_bash_default_maps_to_exec_command(self):
        assert get_equivalence_class("Bash", {"command": "ls -la"}) == "EXEC_COMMAND"

    def test_bash_git_log_maps_to_git_read(self):
        assert get_equivalence_class("Bash", {"command": "git log --oneline"}) == "GIT_READ"

    def test_bash_git_commit_maps_to_git_write(self):
        assert get_equivalence_class("Bash", {"command": "git commit -m 'fix'"}) == "GIT_WRITE"

    def test_bash_curl_maps_to_web_fetch(self):
        assert get_equivalence_class("Bash", {"command": "curl https://example.com"}) == "WEB_FETCH"

    def test_web_fetch_tool_maps_to_web_fetch(self):
        assert get_equivalence_class("WebFetch", {}) == "WEB_FETCH"

    def test_unknown_tool_maps_to_itself(self):
        assert get_equivalence_class("some_unknown_mcp_tool", {}) == "some_unknown_mcp_tool"

    def test_mcp_git_log_maps_to_git_read(self):
        assert get_equivalence_class("mcp__git__git_log", {}) == "GIT_READ"


# ── classify_unscorable ───────────────────────────────────────────────────────

class TestClassifyUnscorable:
    def test_system_tool_is_unscorable(self):
        assert classify_unscorable("do something", tool_event("TodoWrite"))

    def test_todo_read_is_unscorable(self):
        assert classify_unscorable("do something", tool_event("TodoRead"))

    def test_empty_prompt_is_unscorable(self):
        assert classify_unscorable("", tool_event("Read"))

    def test_continuation_prompt_is_unscorable(self):
        assert classify_unscorable("yes", tool_event("Read"))

    def test_ok_prompt_is_unscorable(self):
        assert classify_unscorable("ok", tool_event("Bash"))

    def test_failed_tool_result_is_unscorable(self):
        ev = PostToolUseEvent(
            session_id="s1",
            tool_name="Read",
            tool_input={},
            tool_use_id="u1",
            tool_result_status="error",
        )
        assert classify_unscorable("read the file", ev)

    def test_normal_prompt_and_tool_is_scorable(self):
        assert not classify_unscorable(
            "Read the contents of README.md and summarise it",
            tool_event("Read"),
        )

    def test_highly_complex_prompt_is_unscorable(self):
        # popcount >= 3 capability flags → unscorable
        complex_prompt = "Download the remote dataset, save it to /tmp/data.csv, and run sudo chmod on it"
        assert classify_unscorable(complex_prompt, tool_event("Bash"))
