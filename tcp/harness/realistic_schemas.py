"""Realistic Anthropic API tool schemas mirroring Claude Code runtime.

Built-in tool descriptions and input_schemas are extracted verbatim from
the Claude Code source (March 2026 npm sourcemap build). MCP tool schemas
are delegated to the existing tcp.harness.corpus module.

This module produces the A-arm (ungated) tool list for fair A/B comparison
against TCP binary pre-filtering.
"""

from __future__ import annotations

# Store each built-in tool as a dict literal -- no runtime code generation.
# This ensures the benchmark measures model behavior against the exact same
# schemas that production Claude Code sends to the API.

_BASH_DESCRIPTION = (
    "Executes a given bash command and returns its output.\n\n"
    "The working directory persists between commands, but shell state does not. "
    "The shell environment is initialized from the user's profile (bash or zsh).\n\n"
    "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, "
    "`tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or "
    "after you have verified that a dedicated tool cannot accomplish your task. "
    "Instead, use the appropriate dedicated tool as this will provide a much "
    "better experience for the user:\n\n"
    " - File search: Use Glob (NOT find or ls)\n"
    " - Content search: Use Grep (NOT grep or rg)\n"
    " - Read files: Use Read (NOT cat/head/tail)\n"
    " - Edit files: Use Edit (NOT sed/awk)\n"
    " - Write files: Use Write (NOT echo >/cat <<EOF)\n"
    " - Communication: Output text directly (NOT echo/printf)\n"
    "While the Bash tool can do similar things, it\u2019s better to use the "
    "built-in tools as they provide a better user experience and make it easier "
    "to review tool calls and give permission.\n\n"
    "# Instructions\n"
    " - If your command will create new directories or files, first use this "
    "tool to run `ls` to verify the parent directory exists and is the correct "
    "location.\n"
    " - Always quote file paths that contain spaces with double quotes in your "
    'command (e.g., cd "path with spaces/file.txt")\n'
    " - Try to maintain your current working directory throughout the session "
    "by using absolute paths and avoiding usage of `cd`. You may use `cd` if "
    "the User explicitly requests it.\n"
    " - You may specify an optional timeout in milliseconds (up to 600000ms / "
    "10 minutes). By default, your command will timeout after 120000ms (2 "
    "minutes).\n"
    " - You can use the `run_in_background` parameter to run the command in "
    "the background. Only use this if you don't need the result immediately "
    "and are OK being notified when the command completes later. You do not "
    "need to check the output right away - you'll be notified when it "
    "finishes. You do not need to use '&' at the end of the command when "
    "using this parameter.\n"
    " - When issuing multiple commands:\n"
    "  - If the commands are independent and can run in parallel, make "
    "multiple Bash tool calls in a single message. Example: if you need to "
    'run "git status" and "git diff", send a single message with two Bash '
    "tool calls in parallel.\n"
    "  - If the commands depend on each other and must run sequentially, use "
    "a single Bash call with '&&' to chain them together.\n"
    "  - Use ';' only when you need to run commands sequentially but don't "
    "care if earlier commands fail.\n"
    "  - DO NOT use newlines to separate commands (newlines are ok in quoted "
    "strings).\n"
    " - For git commands:\n"
    "  - Prefer to create a new commit rather than amending an existing "
    "commit.\n"
    "  - Before running destructive operations (e.g., git reset --hard, git "
    "push --force, git checkout --), consider whether there is a safer "
    "alternative that achieves the same goal. Only use destructive operations "
    "when they are truly the best approach.\n"
    "  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c "
    "commit.gpgsign=false) unless the user has explicitly asked for it. If a "
    "hook fails, investigate and fix the underlying issue.\n"
    " - Avoid unnecessary `sleep` commands:\n"
    "  - Do not sleep between commands that can run immediately \u2014 just run "
    "them.\n"
    "  - If your command is long running and you would like to be notified "
    "when it finishes \u2014 use `run_in_background`. No sleep needed.\n"
    "  - Do not retry failing commands in a sleep loop \u2014 diagnose the root "
    "cause.\n"
    "  - If waiting for a background task you started with "
    "`run_in_background`, you will be notified when it completes \u2014 do not "
    "poll.\n"
    "  - If you must poll an external process, use a check command (e.g. `gh "
    "run view`) rather than sleeping first.\n"
    "  - If you must sleep, keep the duration short (1-5 seconds) to avoid "
    "blocking the user."
)

_READ_DESCRIPTION = (
    "Reads a file from the local filesystem. You can access any file directly "
    "by using this tool.\n"
    "Assume this tool is able to read all files on the machine. If the User "
    "provides a path to a file assume that path is valid. It is okay to read a "
    "file that does not exist; an error will be returned.\n\n"
    "Usage:\n"
    "- The file_path parameter must be an absolute path, not a relative path\n"
    "- By default, it reads up to 2000 lines starting from the beginning of "
    "the file\n"
    "- When you already know which part of the file you need, only read that "
    "part. This can be important for larger files.\n"
    "- Results are returned using cat -n format, with line numbers starting at "
    "1\n"
    "- This tool allows Claude Code to read images (eg PNG, JPG, etc). When "
    "reading an image file the contents are presented visually as Claude Code "
    "is a multimodal LLM.\n"
    "- This tool can read PDF files (.pdf). For large PDFs (more than 10 "
    'pages), you MUST provide the pages parameter to read specific page ranges '
    '(e.g., pages: "1-5"). Reading a large PDF without the pages parameter '
    "will fail. Maximum 20 pages per request.\n"
    "- This tool can read Jupyter notebooks (.ipynb files) and returns all "
    "cells with their outputs, combining code, text, and visualizations.\n"
    "- This tool can only read files, not directories. To read a directory, "
    "use an ls command via the Bash tool.\n"
    "- You will regularly be asked to read screenshots. If the user provides a "
    "path to a screenshot, ALWAYS use this tool to view the file at the path. "
    "This tool will work with all temporary file paths.\n"
    "- If you read a file that exists but has empty contents you will receive a "
    "system reminder warning in place of file contents."
)

_EDIT_DESCRIPTION = (
    "Performs exact string replacements in files.\n\n"
    "Usage:\n"
    "- You must use your `Read` tool at least once in the conversation before "
    "editing. This tool will error if you attempt an edit without reading the "
    "file.\n"
    "- When editing text from Read tool output, ensure you preserve the exact "
    "indentation (tabs/spaces) as it appears AFTER the line number prefix. The "
    "line number prefix format is: line number + tab. Everything after that is "
    "the actual file content to match. Never include any part of the line "
    "number prefix in the old_string or new_string.\n"
    "- ALWAYS prefer editing existing files in the codebase. NEVER write new "
    "files unless explicitly required.\n"
    "- Only use emojis if the user explicitly requests it. Avoid adding emojis "
    "to files unless asked.\n"
    "- The edit will FAIL if `old_string` is not unique in the file. Either "
    "provide a larger string with more surrounding context to make it unique or "
    "use `replace_all` to change every instance of `old_string`.\n"
    "- Use `replace_all` for replacing and renaming strings across the file. "
    "This parameter is useful if you want to rename a variable for instance."
)

_WRITE_DESCRIPTION = (
    "Writes a file to the local filesystem.\n\n"
    "Usage:\n"
    "- This tool will overwrite the existing file if there is one at the "
    "provided path.\n"
    "- If this is an existing file, you MUST use the Read tool first to read "
    "the file's contents. This tool will fail if you did not read the file "
    "first.\n"
    "- Prefer the Edit tool for modifying existing files \u2014 it only sends the "
    "diff. Only use this tool to create new files or for complete rewrites.\n"
    "- NEVER create documentation files (*.md) or README files unless "
    "explicitly requested by the User.\n"
    "- Only use emojis if the user explicitly requests it. Avoid writing "
    "emojis to files unless asked."
)

_GLOB_DESCRIPTION = (
    "- Fast file pattern matching tool that works with any codebase size\n"
    '- Supports glob patterns like "**/*.js" or "src/**/*.ts"\n'
    "- Returns matching file paths sorted by modification time\n"
    "- Use this tool when you need to find files by name patterns\n"
    "- When you are doing an open ended search that may require multiple "
    "rounds of globbing and grepping, use the Agent tool instead"
)

_GREP_DESCRIPTION = (
    "A powerful search tool built on ripgrep\n\n"
    "  Usage:\n"
    "  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a "
    "Bash command. The Grep tool has been optimized for correct permissions "
    "and access.\n"
    '  - Supports full regex syntax (e.g., "log.*Error", '
    '"function\\s+\\w+")\n'
    '  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type '
    'parameter (e.g., "js", "py", "rust")\n'
    '  - Output modes: "content" shows matching lines, "files_with_matches" '
    'shows only file paths (default), "count" shows match counts\n'
    "  - Use Agent tool for open-ended searches requiring multiple rounds\n"
    "  - Pattern syntax: Uses ripgrep (not grep) - literal braces need "
    "escaping (use `interface\\{\\}` to find `interface{}` in Go code)\n"
    "  - Multiline matching: By default patterns match within single lines "
    "only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use "
    "`multiline: true`"
)

_AGENT_DESCRIPTION = (
    "Launch a new agent to handle complex, multi-step tasks autonomously.\n\n"
    "The Agent tool launches specialized agents (subprocesses) that "
    "autonomously handle complex tasks. Each agent type has specific "
    "capabilities and tools available to it.\n\n"
    "Available agent types and the tools they have access to:\n"
    "- general-purpose: General-purpose agent for researching complex "
    "questions, searching for code, and executing multi-step tasks. "
    "(Tools: *)\n\n"
    "When NOT to use the Agent tool:\n"
    "- If you want to read a specific file path, use the Read tool or the "
    "Glob tool instead of the Agent tool, to find the match more quickly\n"
    '- If you are searching for a specific class definition like "class Foo", '
    "use the Glob tool instead, to find the match more quickly\n"
    "- If you are searching for code within a specific file or set of 2-3 "
    "files, use the Read tool instead of the Agent tool, to find the match "
    "more quickly\n"
    "- Other tasks that are not related to the agent descriptions above\n\n"
    "Usage notes:\n"
    "- Always include a short description (3-5 words) summarizing what the "
    "agent will do\n"
    "- Launch multiple agents concurrently whenever possible, to maximize "
    "performance; to do that, use a single message with multiple tool uses\n"
    "- When the agent is done, it will return a single message back to you. "
    "The result returned by the agent is not visible to the user. To show the "
    "user the result, you should send a text message back to the user with a "
    "concise summary of the result.\n"
    "- Clearly tell the agent whether you expect it to write code or just to "
    "do research (search, file reads, web fetches, etc.), since it is not "
    "aware of the user's intent"
)

_SKILL_DESCRIPTION = (
    "Execute a skill within the main conversation\n\n"
    "When users ask you to perform tasks, check if any of the available skills "
    "match. Skills provide specialized capabilities and domain knowledge.\n\n"
    'When users reference a "slash command" or "/<something>" (e.g., '
    '"/commit", "/review-pr"), they are referring to a skill. Use this tool to '
    "invoke it.\n\n"
    "How to invoke:\n"
    "- Use this tool with the skill name and optional arguments\n"
    "- Examples:\n"
    '  - `skill: "pdf"` - invoke the pdf skill\n'
    '  - `skill: "commit", args: "-m \'Fix bug\'"` - invoke with arguments\n\n'
    "Important:\n"
    "- Available skills are listed in system-reminder messages in the "
    "conversation\n"
    "- When a skill matches the user's request, this is a BLOCKING "
    "REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other "
    "response about the task\n"
    "- NEVER mention a skill without actually calling this tool\n"
    "- Do not invoke a skill that is already running\n"
    "- Do not use this tool for built-in CLI commands (like /help, /clear, "
    "etc.)\n"
    "- If you see a <command-name> tag in the current conversation turn, the "
    "skill has ALREADY been loaded - follow the instructions directly instead "
    "of calling this tool again"
)

_TOOLSEARCH_DESCRIPTION = (
    "Fetches full schema definitions for deferred tools so they can be "
    "called.\n\n"
    "Deferred tools appear by name in <system-reminder> messages. Until "
    "fetched, only the name is known \u2014 there is no parameter schema, so the "
    "tool cannot be invoked. This tool takes a query, matches it against the "
    "deferred tool list, and returns the matched tools' complete JSONSchema "
    "definitions inside a <functions> block. Once a tool's schema appears in "
    "that result, it is callable exactly like any tool defined at the top of "
    "the prompt.\n\n"
    'Result format: each matched tool appears as one <function>{"description":'
    ' "...", "name": "...", "parameters": {...}}</function> line inside the '
    "<functions> block \u2014 the same encoding as the tool list at the top of "
    "this prompt.\n\n"
    "Query forms:\n"
    '- "select:Read,Edit,Grep" \u2014 fetch these exact tools by name\n'
    '- "notebook jupyter" \u2014 keyword search, up to max_results best matches\n'
    '- "+slack send" \u2014 require "slack" in the name, rank by remaining terms'
)

# ---------------------------------------------------------------------------
# Built-in tool list -- dict literals only, no code generation.
# ---------------------------------------------------------------------------

_BUILTIN_TOOLS: list[dict] = [
    # --- Core tools (always loaded, never deferred) ---
    {
        "name": "Bash",
        "description": _BASH_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "description": "The command to execute",
                    "type": "string",
                },
                "description": {
                    "description": (
                        "Clear, concise description of what this command does "
                        "in active voice."
                    ),
                    "type": "string",
                },
                "timeout": {
                    "description": "Optional timeout in milliseconds (max 600000)",
                    "type": "number",
                },
                "run_in_background": {
                    "description": (
                        "Set to true to run this command in the background."
                    ),
                    "type": "boolean",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "Read",
        "description": _READ_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "description": "The absolute path to the file to read",
                    "type": "string",
                },
                "offset": {
                    "description": (
                        "The line number to start reading from. Only provide "
                        "if the file is too large to read at once"
                    ),
                    "type": "integer",
                    "minimum": 0,
                },
                "limit": {
                    "description": (
                        "The number of lines to read. Only provide if the "
                        "file is too large to read at once."
                    ),
                    "type": "integer",
                    "exclusiveMinimum": 0,
                },
                "pages": {
                    "description": (
                        'Page range for PDF files (e.g., "1-5", "3", '
                        '"10-20"). Only applicable to PDF files. Maximum 20 '
                        "pages per request."
                    ),
                    "type": "string",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Edit",
        "description": _EDIT_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "description": "The absolute path to the file to modify",
                    "type": "string",
                },
                "old_string": {
                    "description": "The text to replace",
                    "type": "string",
                },
                "new_string": {
                    "description": (
                        "The text to replace it with (must be different from "
                        "old_string)"
                    ),
                    "type": "string",
                },
                "replace_all": {
                    "description": (
                        "Replace all occurrences of old_string (default false)"
                    ),
                    "type": "boolean",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Write",
        "description": _WRITE_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "description": (
                        "The absolute path to the file to write (must be "
                        "absolute, not relative)"
                    ),
                    "type": "string",
                },
                "content": {
                    "description": "The content to write to the file",
                    "type": "string",
                },
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Glob",
        "description": _GLOB_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "description": "The glob pattern to match files against",
                    "type": "string",
                },
                "path": {
                    "description": (
                        "The directory to search in. If not specified, the "
                        "current working directory will be used. IMPORTANT: "
                        "Omit this field to use the default directory. DO NOT "
                        'enter "undefined" or "null" - simply omit it for the '
                        "default behavior. Must be a valid directory path if "
                        "provided."
                    ),
                    "type": "string",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": _GREP_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "description": (
                        "The regular expression pattern to search for in file "
                        "contents"
                    ),
                    "type": "string",
                },
                "path": {
                    "description": (
                        "File or directory to search in (rg PATH). Defaults "
                        "to current working directory."
                    ),
                    "type": "string",
                },
                "glob": {
                    "description": (
                        'Glob pattern to filter files (e.g. "*.js", '
                        '"*.{ts,tsx}") - maps to rg --glob'
                    ),
                    "type": "string",
                },
                "output_mode": {
                    "description": (
                        'Output mode: "content" shows matching lines, '
                        '"files_with_matches" shows file paths (default), '
                        '"count" shows match counts.'
                    ),
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                },
                "-B": {
                    "description": (
                        "Number of lines to show before each match (rg -B)."
                    ),
                    "type": "number",
                },
                "-A": {
                    "description": (
                        "Number of lines to show after each match (rg -A)."
                    ),
                    "type": "number",
                },
                "-C": {
                    "description": "Alias for context.",
                    "type": "number",
                },
                "context": {
                    "description": (
                        "Number of lines to show before and after each match "
                        "(rg -C)."
                    ),
                    "type": "number",
                },
                "-n": {
                    "description": (
                        "Show line numbers in output (rg -n). Defaults to true."
                    ),
                    "type": "boolean",
                },
                "-i": {
                    "description": "Case insensitive search (rg -i)",
                    "type": "boolean",
                },
                "type": {
                    "description": (
                        "File type to search (rg --type). Common types: js, "
                        "py, rust, go, java, etc."
                    ),
                    "type": "string",
                },
                "head_limit": {
                    "description": (
                        "Limit output to first N lines/entries. Defaults to "
                        "250 when unspecified. Pass 0 for unlimited."
                    ),
                    "type": "number",
                },
                "offset": {
                    "description": (
                        "Skip first N lines/entries before applying "
                        "head_limit."
                    ),
                    "type": "number",
                },
                "multiline": {
                    "description": (
                        "Enable multiline mode where . matches newlines. "
                        "Default: false."
                    ),
                    "type": "boolean",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Agent",
        "description": _AGENT_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "description": (
                        "A short (3-5 word) description of the task"
                    ),
                    "type": "string",
                },
                "prompt": {
                    "description": "The task for the agent to perform",
                    "type": "string",
                },
                "subagent_type": {
                    "description": (
                        "The type of specialized agent to use for this task"
                    ),
                    "type": "string",
                },
                "model": {
                    "description": "Optional model override for this agent.",
                    "type": "string",
                    "enum": ["sonnet", "opus", "haiku"],
                },
                "run_in_background": {
                    "description": (
                        "Set to true to run this agent in the background."
                    ),
                    "type": "boolean",
                },
                "isolation": {
                    "description": (
                        "Isolation mode. 'worktree' creates a temporary git "
                        "worktree."
                    ),
                    "type": "string",
                    "enum": ["worktree"],
                },
            },
            "required": ["description", "prompt"],
        },
    },
    {
        "name": "Skill",
        "description": _SKILL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {
                    "description": (
                        'The skill name. E.g., "commit", "review-pr", or "pdf"'
                    ),
                    "type": "string",
                },
                "args": {
                    "description": "Optional arguments for the skill",
                    "type": "string",
                },
            },
            "required": ["skill"],
        },
    },
    # --- Deferred tools (shouldDefer=true) ---
    {
        "name": "WebFetch",
        "description": (
            "Fetches full content from a URL and extracts information based "
            "on a prompt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "description": "The URL to fetch content from",
                    "type": "string",
                },
                "prompt": {
                    "description": (
                        "The prompt to run on the fetched content"
                    ),
                    "type": "string",
                },
            },
            "required": ["url", "prompt"],
        },
    },
    {
        "name": "WebSearch",
        "description": (
            "Searches the web using a search engine and returns relevant "
            "results including titles, URLs, and descriptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "description": "The search query to use",
                    "type": "string",
                },
                "allowed_domains": {
                    "description": (
                        "Only include search results from these domains"
                    ),
                    "type": "array",
                    "items": {"type": "string"},
                },
                "blocked_domains": {
                    "description": (
                        "Never include search results from these domains"
                    ),
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "NotebookEdit",
        "description": (
            "Edit Jupyter notebook (.ipynb) cells. Supports replace, insert, "
            "and delete operations on code and markdown cells."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "description": (
                        "The absolute path to the Jupyter notebook file to edit"
                    ),
                    "type": "string",
                },
                "cell_id": {
                    "description": "The ID of the cell to edit",
                    "type": "string",
                },
                "new_source": {
                    "description": "The new source for the cell",
                    "type": "string",
                },
                "cell_type": {
                    "description": "The type of the cell",
                    "type": "string",
                    "enum": ["code", "markdown"],
                },
                "edit_mode": {
                    "description": "The type of edit to make",
                    "type": "string",
                    "enum": ["replace", "insert", "delete"],
                },
            },
            "required": ["notebook_path", "new_source"],
        },
    },
    {
        "name": "TodoWrite",
        "description": (
            "Create and manage a structured task checklist for tracking "
            "progress."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "description": "The updated todo list",
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                ],
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["id", "content", "status", "priority"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
    {
        "name": "AskUserQuestion",
        "description": "Prompt the user with a multiple-choice question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Questions to ask the user (1-4 questions)"
                    ),
                },
            },
            "required": ["questions"],
        },
    },
    {
        "name": "EnterPlanMode",
        "description": "Enter plan mode to design an approach before coding.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ExitPlanMode",
        "description": "Exit plan mode.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Task management tools ---
    {
        "name": "TaskCreate",
        "description": "Create a new background task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "description": "Short description of the task",
                    "type": "string",
                },
                "prompt": {
                    "description": "The task prompt",
                    "type": "string",
                },
            },
            "required": ["description", "prompt"],
        },
    },
    {
        "name": "TaskGet",
        "description": "Get the status and result of a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "description": "The task ID to retrieve",
                    "type": "string",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "TaskUpdate",
        "description": "Update a running task with new instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "description": "The task ID to update",
                    "type": "string",
                },
                "message": {
                    "description": "The update message",
                    "type": "string",
                },
            },
            "required": ["task_id", "message"],
        },
    },
    {
        "name": "TaskList",
        "description": "List all tasks and their statuses.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "TaskStop",
        "description": "Stop a running task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "description": "The task ID to stop",
                    "type": "string",
                },
            },
            "required": ["task_id"],
        },
    },
    # --- Meta / utility tools ---
    {
        "name": "ToolSearch",
        "description": _TOOLSEARCH_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "description": (
                        'Query to find deferred tools. Use '
                        '"select:<tool_name>" for direct selection, or '
                        "keywords to search."
                    ),
                    "type": "string",
                },
                "max_results": {
                    "description": (
                        "Maximum number of results to return (default: 5)"
                    ),
                    "type": "number",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "SendMessage",
        "description": "Send a message to the user in the conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "description": "The message to send",
                    "type": "string",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "EnterWorktree",
        "description": (
            "Create and enter a temporary git worktree for isolated work."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "branch": {
                    "description": "Branch name for the worktree",
                    "type": "string",
                },
            },
            "required": ["branch"],
        },
    },
    {
        "name": "ExitWorktree",
        "description": "Exit and clean up the current git worktree.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ListMcpResources",
        "description": "List available MCP server resources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {
                    "description": "MCP server name to query",
                    "type": "string",
                },
            },
            "required": ["server"],
        },
    },
    {
        "name": "ReadMcpResource",
        "description": "Read a specific MCP resource by URI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "server": {
                    "description": "MCP server name",
                    "type": "string",
                },
                "uri": {
                    "description": "Resource URI to read",
                    "type": "string",
                },
            },
            "required": ["server", "uri"],
        },
    },
    {
        "name": "Brief",
        "description": "Toggle brief response mode for shorter outputs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "description": "Whether to enable brief mode",
                    "type": "boolean",
                },
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "Sleep",
        "description": "Wait for a specified duration before continuing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_ms": {
                    "description": "Duration to sleep in milliseconds",
                    "type": "number",
                },
            },
            "required": ["duration_ms"],
        },
    },
    {
        "name": "RemoteTrigger",
        "description": "Trigger a remote action or webhook.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "description": "The remote action to trigger",
                    "type": "string",
                },
                "payload": {
                    "description": "Optional JSON payload for the action",
                    "type": "object",
                },
            },
            "required": ["action"],
        },
    },
]


def build_builtin_schemas() -> list[dict]:
    """Return built-in tool schemas matching Claude Code's toolToAPISchema() output."""
    return list(_BUILTIN_TOOLS)


def build_realistic_corpus() -> list[dict]:
    """Return full realistic tool list: built-in + MCP tools.

    This mirrors what toolUseContext.options.tools produces after
    assembleToolPool() in tools.ts -- the exact tool list sent to
    every messages.create() call in query.ts:663.
    """
    from tcp.harness.corpus import build_mcp_corpus
    from tcp.harness.schema_bridge import corpus_to_anthropic_schemas

    mcp_schemas = corpus_to_anthropic_schemas(build_mcp_corpus())
    # Built-in first (sorted), then MCP (sorted) -- matches assembleToolPool()
    builtins = sorted(build_builtin_schemas(), key=lambda t: t["name"])
    mcp_sorted = sorted(mcp_schemas, key=lambda t: t["name"])
    return builtins + mcp_sorted
