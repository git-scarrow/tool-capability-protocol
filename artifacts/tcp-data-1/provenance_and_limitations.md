# TCP-DATA-1 Provenance and Limitations

## Source

- Source corpus: `~/.tcp-shadow/sessions/*.jsonl`
- Selection rule: turn-level prompts with enough evidence to score
- Total eligible turns observed in corpus: 235
- Selected turns in package: 50
- Coverage-audit-suitable turns in package: 22
- Unique sessions in selected package: 27
- Unique sessions in full eligible corpus: 43

## Observed class mix in selected package

- Agent: 2
- CronCreate: 1
- EXEC_COMMAND: 24
- EnterPlanMode: 1
- ExitPlanMode: 1
- FILE_EDIT: 9
- FILE_READ: 17
- FILE_WRITE: 5
- ListMcpResourcesTool: 1
- MCP_NOTION_AGENT_RUN: 2
- SEARCH_FILES: 4
- SEARCH_TEXT: 8
- Skill: 3
- TaskCreate: 1
- TaskOutput: 2
- TaskUpdate: 2
- ToolSearch: 1
- WEB_FETCH: 2
- mcp__agents__chatsearch__chatsearch_token_cost_report: 1
- mcp__bay-view-graph__get_email: 3
- mcp__bay-view-graph__get_email_attachments: 1
- mcp__bay-view-graph__list_drive_items: 1
- mcp__bay-view-graph__list_emails: 2
- mcp__bay-view-graph__list_site_drives: 1
- mcp__bay-view-graph__reply_email: 1
- mcp__bay-view-graph__search_emails: 2
- mcp__bay-view-graph__search_files: 2
- mcp__chatsearch__chatsearch_find: 1
- mcp__chatsearch__chatsearch_watch_cycles: 6
- mcp__claude-projects__claude_get_instructions: 1
- mcp__exa__web_search_exa: 1
- mcp__notion-agents__chat_with_agent: 4
- mcp__notion-agents__check_agent_response: 3
- mcp__notion-agents__describe_database: 2
- mcp__notion-agents__list_workspace_agents: 1
- mcp__notion-agents__query_database: 2
- mcp__notion-agents__set_agent_model: 1
- mcp__oracle-remote__execute_query: 3
- mcp__playwright__browser_navigate: 1
- mcp__plugin_Notion_notion__notion-search: 1

## Limitations

- This package contains label-ready turns, not final hand labels.
- Proxy-derived fields are included for scoring context, but they are not ground truth.
- Some projects and workflows may still be over-represented.
- The package excludes low-information prompts and turns classified as unscorable by TCP-DS-2 rules.
