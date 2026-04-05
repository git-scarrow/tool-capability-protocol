"""Canned tool responses for EXP-2 agent loop benchmarks.

Each tool in the MT-3 corpus has a canned JSON response.  The mock
returns valid-shaped JSON for the tool's declared output format,
isolating measurement to model-side behavior.
"""

from __future__ import annotations

from typing import Callable

MOCK_RESPONSES: dict[str, str] = {
    # --- MCP: Notion Agents ---
    "notion-chat-with-agent": '{"response": "Agent reply here"}',
    "notion-query-database": '{"results": [{"id": "page-1", "title": "Item"}]}',
    "notion-describe-database": '{"schema": {"Name": "title", "Status": "select"}}',
    "notion-create-agent": '{"id": "agent-1", "status": "created"}',
    "notion-update-agent": '{"id": "agent-1", "status": "updated"}',
    "notion-list-agents": '{"agents": [{"id": "agent-1", "name": "Bot"}]}',
    "notion-discover-agent": '{"agent": {"id": "agent-1", "capability": "chat"}}',
    "notion-dump-agent": '{"config": {"model": "claude", "tools": []}}',
    "notion-get-conversation": '{"messages": [{"role": "user", "content": "hi"}]}',
    "notion-handle-final-return": '{"status": "dispatched"}',
    # --- MCP: Playwright ---
    "browser-navigate": '{"url": "https://example.com", "status": 200}',
    "browser-click": '{"clicked": true, "selector": "#btn"}',
    "browser-fill-form": '{"filled": true, "fields": 2}',
    "browser-snapshot": '{"snapshot": "page title: Example"}',
    "browser-screenshot": '{"path": "/tmp/screenshot.png"}',
    "browser-evaluate": '{"result": "42"}',
    "browser-press-key": '{"key": "Enter", "sent": true}',
    "browser-tabs": '{"tabs": [{"id": 1, "url": "https://example.com"}]}',
    # --- MCP: Filesystem ---
    "fs-read-file": '{"content": "sample file content", "path": "/tmp/data.json"}',
    "fs-read-multiple": '{"files": [{"path": "/a.txt", "content": "a"}, {"path": "/b.txt", "content": "b"}]}',
    "fs-write-file": '{"status": "written", "bytes": 42}',
    "fs-edit-file": '{"status": "edited", "replacements": 1}',
    "fs-list-directory": '{"entries": ["file1.py", "file2.py", "dir1/"]}',
    "fs-directory-tree": '{"tree": {"name": "root", "children": [{"name": "src"}]}}',
    "fs-search-files": '{"matches": ["/src/main.py:10: TODO fix this"]}',
    "fs-get-file-info": '{"size": 1024, "modified": "2026-01-01T00:00:00Z"}',
    "fs-move-file": '{"status": "moved", "from": "/a", "to": "/b"}',
    "fs-create-directory": '{"status": "created", "path": "/new-dir"}',
    # --- MCP: Git ---
    "git-status": '{"branch": "main", "clean": true, "files": []}',
    "git-log": '{"commits": [{"sha": "abc123", "message": "initial"}]}',
    "git-diff": '{"diff": "--- a/file\\n+++ b/file\\n@@ -1 +1 @@"}',
    "git-diff-staged": '{"diff": ""}',
    "git-show": '{"sha": "abc123", "message": "initial", "diff": ""}',
    "git-commit": '{"sha": "def456", "message": "committed"}',
    "git-add": '{"staged": ["file.py"]}',
    "git-branch": '{"branches": ["main", "dev"], "current": "main"}',
    "git-checkout": '{"branch": "dev", "status": "switched"}',
    "git-reset": '{"status": "reset", "head": "abc123"}',
    # --- MCP: Fetch ---
    "web-fetch": '{"status": 200, "body": "<html>example</html>"}',
    # --- MCP: Exa ---
    "exa-web-search": '{"results": [{"title": "Result", "url": "https://example.com"}]}',
    "exa-company-research": '{"company": "Acme", "info": "Founded 2020"}',
    "exa-code-context": '{"context": "function main() {}"}',
    # --- MCP: Context7 ---
    "c7-resolve-library": '{"library_id": "/react/latest", "version": "19.0"}',
    "c7-query-docs": '{"content": "React.useState hook documentation..."}',
    # --- MCP: Oracle ---
    "oracle-execute-query": '{"rows": [{"id": 1, "name": "Alice"}], "count": 1}',
    "oracle-list-schemas": '{"schemas": ["HR", "SALES"]}',
    "oracle-list-tables": '{"tables": ["users", "orders"]}',
    "oracle-describe-table": '{"columns": [{"name": "id", "type": "NUMBER"}]}',
    "oracle-get-indexes": '{"indexes": [{"name": "pk_users"}]}',
    "oracle-get-constraints": '{"constraints": [{"name": "pk_users", "type": "PRIMARY KEY"}]}',
    # --- MCP: Gmail ---
    "gmail-search": '{"messages": [{"id": "msg-1", "subject": "Hello"}]}',
    "gmail-read-message": '{"id": "msg-1", "body": "Message body"}',
    "gmail-read-thread": '{"messages": [{"id": "msg-1"}]}',
    "gmail-create-draft": '{"id": "draft-1", "status": "created"}',
    "gmail-get-profile": '{"email": "user@example.com"}',
    "gmail-list-labels": '{"labels": ["INBOX", "SENT"]}',
    # --- MCP: Google Calendar ---
    "gcal-list-events": '{"events": [{"summary": "Meeting", "start": "10:00"}]}',
    "gcal-create-event": '{"id": "evt-1", "status": "created"}',
    "gcal-update-event": '{"id": "evt-1", "status": "updated"}',
    "gcal-delete-event": '{"status": "deleted"}',
    "gcal-find-free-time": '{"slots": [{"start": "14:00", "end": "15:00"}]}',
    # --- MCP: Vercel ---
    "vercel-deploy": '{"id": "dpl-1", "url": "https://my-app.vercel.app"}',
    "vercel-list-projects": '{"projects": [{"name": "my-app"}]}',
    "vercel-get-deployment": '{"id": "dpl-1", "state": "READY"}',
    "vercel-get-build-logs": '{"logs": ["Building...", "Done"]}',
    "vercel-get-runtime-logs": '{"logs": []}',
    # --- MCP: Tally ---
    "tally-create-form": '{"id": "form-1", "status": "created"}',
    "tally-list-forms": '{"forms": [{"id": "form-1", "title": "Survey"}]}',
    "tally-fetch-submissions": '{"submissions": [{"id": "sub-1"}]}',
    "tally-save-form": '{"status": "saved"}',
    # --- MCP: Writing RAG ---
    "rag-query-documents": '{"results": [{"text": "matching passage", "score": 0.2}]}',
    "rag-query-passages": '{"passages": [{"text": "passage", "score": 0.3}]}',
    "rag-ingest-file": '{"status": "ingested", "chunks": 12}',
    "rag-list-files": '{"files": ["essay.md", "notes.md"]}',
    # --- MCP: NixOS ---
    "nix-eval": '{"result": "hello-2.12"}',
    "nix-versions": '{"versions": ["24.05", "24.11"]}',
    # --- System commands ---
    "grep": '{"matches": ["file.py:10:pattern found"]}',
    "find": '{"files": ["/src/main.py", "/src/util.py"]}',
    "sed": '{"status": "replaced", "count": 3}',
    "awk": '{"output": "field1 field2"}',
    "curl": '{"status": 200, "body": "response"}',
    "ssh": '{"status": "connected", "output": "hostname"}',
    "rsync": '{"status": "synced", "files": 5}',
    "tar": '{"status": "archived", "file": "backup.tar.gz"}',
    "rm": '{"status": "removed", "files": ["temp.txt"]}',
    "chmod": '{"status": "permissions changed", "mode": "644"}',
    "systemctl": '{"status": "active", "service": "nginx"}',
    "docker": '{"containers": [{"id": "abc", "status": "running"}]}',
    "python": '{"output": "Hello, World!"}',
    "jq": '{"result": {"name": "extracted_value"}}',
    # --- Synthetic ambiguous corpus ---
    "ripgrep": '{"matches": ["src/main.py:15:TODO fix auth"]}',
    "http-fetch": '{"status": 200, "body": {"deploy_status": "healthy"}}',
    "wget": '{"status": 200, "saved": "/tmp/output"}',
    "python-exec": '{"output": "result"}',
    "node-exec": '{"output": "result"}',
    "tee": '{"status": "written", "bytes": 128}',
    "editor": '{"status": "opened", "path": "/tmp/file"}',
    "ps": '{"processes": [{"pid": 1234, "name": "postgres"}]}',
    "pgrep": '{"pids": [1234, 1235]}',
    "diff": '{"diff": "--- v1\\n+++ v2\\n@@ -1 +1 @@\\n-old\\n+new"}',
    "colordiff": '{"diff": "--- v1\\n+++ v2\\n@@ -1 +1 @@\\n-old\\n+new"}',
}

_DEFAULT_RESPONSE = '{"status": "ok"}'


def get_mock_executor() -> Callable[[str, dict], str]:
    """Return a mock executor that returns canned JSON responses.

    Unknown tools get a generic ``{"status": "ok"}`` response.
    """

    def _execute(tool_name: str, tool_input: dict) -> str:  # noqa: ARG001
        return MOCK_RESPONSES.get(tool_name, _DEFAULT_RESPONSE)

    return _execute
