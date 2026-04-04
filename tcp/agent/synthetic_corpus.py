"""Synthetic tool corpus generator for scale stress testing.

Generates realistic MCP-style tools to supplement the 90-tool real corpus
for testing per-task filtering at 500+ tool scale.
"""

from __future__ import annotations

import random
from typing import Sequence

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CapabilityFlags,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    PerformanceMetrics,
    ProcessingMode,
)
from tcp.harness.corpus import CorpusEntry

# Realistic MCP server families with tool templates
_FAMILIES = [
    {
        "source": "mcp:slack",
        "tools": [
            ("slack-send-message", ["send_message"], "Send a Slack message to a channel", "network-write"),
            ("slack-list-channels", ["list_channels"], "List Slack channels", "network"),
            ("slack-search-messages", ["search_messages"], "Search Slack message history", "network"),
            ("slack-get-thread", ["get_thread"], "Get a Slack thread", "network"),
            ("slack-set-topic", ["set_topic"], "Set channel topic", "network-write"),
            ("slack-upload-file", ["upload_file"], "Upload file to Slack", "network-write"),
            ("slack-list-users", ["list_users"], "List workspace users", "network"),
            ("slack-get-profile", ["get_profile"], "Get user profile", "network"),
        ],
    },
    {
        "source": "mcp:jira",
        "tools": [
            ("jira-create-issue", ["create_issue"], "Create a Jira issue", "network-write"),
            ("jira-search-issues", ["search_issues"], "Search Jira issues with JQL", "network"),
            ("jira-get-issue", ["get_issue"], "Get Jira issue details", "network"),
            ("jira-update-issue", ["update_issue"], "Update Jira issue fields", "network-write"),
            ("jira-add-comment", ["add_comment"], "Add comment to Jira issue", "network-write"),
            ("jira-list-projects", ["list_projects"], "List Jira projects", "network"),
            ("jira-get-board", ["get_board"], "Get Jira board", "network"),
            ("jira-list-sprints", ["list_sprints"], "List sprints in board", "network"),
        ],
    },
    {
        "source": "mcp:github",
        "tools": [
            ("gh-create-issue", ["create_issue"], "Create a GitHub issue", "network-write"),
            ("gh-list-prs", ["list_pull_requests"], "List pull requests", "network"),
            ("gh-get-pr", ["get_pull_request"], "Get pull request details", "network"),
            ("gh-merge-pr", ["merge_pull_request"], "Merge a pull request", "network-write"),
            ("gh-create-pr", ["create_pull_request"], "Create a pull request", "network-write"),
            ("gh-list-repos", ["list_repos"], "List repositories", "network"),
            ("gh-search-code", ["search_code"], "Search code across repos", "network"),
            ("gh-get-file", ["get_file_contents"], "Get file from repo", "network"),
            ("gh-create-branch", ["create_branch"], "Create a branch", "network-write"),
            ("gh-list-workflows", ["list_workflows"], "List GitHub Actions workflows", "network"),
        ],
    },
    {
        "source": "mcp:postgres",
        "tools": [
            ("pg-execute-query", ["execute_query"], "Execute SQL query", "network-auth"),
            ("pg-list-tables", ["list_tables"], "List database tables", "network-auth"),
            ("pg-describe-table", ["describe_table"], "Show table schema", "network-auth"),
            ("pg-list-schemas", ["list_schemas"], "List database schemas", "network-auth"),
            ("pg-get-indexes", ["get_indexes"], "Get table indexes", "network-auth"),
            ("pg-explain-query", ["explain_query"], "Explain query plan", "network-auth"),
        ],
    },
    {
        "source": "mcp:redis",
        "tools": [
            ("redis-get", ["redis_get"], "Get value by key", "network"),
            ("redis-set", ["redis_set"], "Set key-value pair", "network-write"),
            ("redis-del", ["redis_del"], "Delete key", "network-write"),
            ("redis-keys", ["redis_keys"], "List keys by pattern", "network"),
            ("redis-info", ["redis_info"], "Get server info", "network"),
        ],
    },
    {
        "source": "mcp:aws",
        "tools": [
            ("aws-s3-list", ["s3_list_buckets"], "List S3 buckets", "network-auth"),
            ("aws-s3-get", ["s3_get_object"], "Download S3 object", "network-auth"),
            ("aws-s3-put", ["s3_put_object"], "Upload to S3", "network-auth-write"),
            ("aws-ec2-list", ["ec2_list_instances"], "List EC2 instances", "network-auth"),
            ("aws-ec2-start", ["ec2_start_instance"], "Start EC2 instance", "network-auth-write"),
            ("aws-ec2-stop", ["ec2_stop_instance"], "Stop EC2 instance", "network-auth-write"),
            ("aws-lambda-invoke", ["lambda_invoke"], "Invoke Lambda function", "network-auth"),
            ("aws-lambda-list", ["lambda_list_functions"], "List Lambda functions", "network-auth"),
            ("aws-sqs-send", ["sqs_send_message"], "Send SQS message", "network-auth-write"),
            ("aws-sqs-receive", ["sqs_receive_messages"], "Receive SQS messages", "network-auth"),
        ],
    },
    {
        "source": "mcp:docker",
        "tools": [
            ("docker-list-containers", ["list_containers"], "List Docker containers", "file-read"),
            ("docker-start-container", ["start_container"], "Start a container", "file-write"),
            ("docker-stop-container", ["stop_container"], "Stop a container", "file-write"),
            ("docker-logs", ["container_logs"], "Get container logs", "file-read"),
            ("docker-exec", ["container_exec"], "Execute command in container", "file-write"),
            ("docker-images", ["list_images"], "List Docker images", "file-read"),
            ("docker-build", ["build_image"], "Build Docker image", "file-write"),
            ("docker-pull", ["pull_image"], "Pull Docker image", "network"),
        ],
    },
    {
        "source": "mcp:kubernetes",
        "tools": [
            ("k8s-get-pods", ["get_pods"], "List pods in namespace", "network-auth"),
            ("k8s-get-services", ["get_services"], "List services", "network-auth"),
            ("k8s-get-deployments", ["get_deployments"], "List deployments", "network-auth"),
            ("k8s-apply", ["apply_manifest"], "Apply K8s manifest", "network-auth-write"),
            ("k8s-delete", ["delete_resource"], "Delete K8s resource", "network-auth-write"),
            ("k8s-logs", ["get_pod_logs"], "Get pod logs", "network-auth"),
            ("k8s-describe", ["describe_resource"], "Describe K8s resource", "network-auth"),
            ("k8s-scale", ["scale_deployment"], "Scale deployment", "network-auth-write"),
        ],
    },
    {
        "source": "mcp:linear",
        "tools": [
            ("linear-create-issue", ["create_issue"], "Create Linear issue", "network-write"),
            ("linear-search", ["search_issues"], "Search Linear issues", "network"),
            ("linear-get-issue", ["get_issue"], "Get issue details", "network"),
            ("linear-update-issue", ["update_issue"], "Update issue", "network-write"),
            ("linear-list-projects", ["list_projects"], "List projects", "network"),
            ("linear-list-teams", ["list_teams"], "List teams", "network"),
        ],
    },
    {
        "source": "mcp:confluence",
        "tools": [
            ("confluence-search", ["search_content"], "Search Confluence", "network"),
            ("confluence-get-page", ["get_page"], "Get page content", "network"),
            ("confluence-create-page", ["create_page"], "Create page", "network-write"),
            ("confluence-update-page", ["update_page"], "Update page", "network-write"),
            ("confluence-list-spaces", ["list_spaces"], "List spaces", "network"),
            ("confluence-get-comments", ["get_comments"], "Get page comments", "network"),
        ],
    },
    {
        "source": "mcp:mongodb",
        "tools": [
            ("mongo-find", ["find_documents"], "Find documents", "network-auth"),
            ("mongo-insert", ["insert_document"], "Insert document", "network-auth-write"),
            ("mongo-update", ["update_document"], "Update document", "network-auth-write"),
            ("mongo-delete", ["delete_document"], "Delete document", "network-auth-write"),
            ("mongo-aggregate", ["aggregate"], "Run aggregation pipeline", "network-auth"),
            ("mongo-list-collections", ["list_collections"], "List collections", "network-auth"),
        ],
    },
    {
        "source": "system",
        "tools": [
            ("top", ["top"], "Show system processes", "file-read"),
            ("df", ["df"], "Show disk usage", "file-read"),
            ("du", ["du"], "Show directory sizes", "file-read"),
            ("ps", ["ps"], "List processes", "file-read"),
            ("kill", ["kill"], "Kill a process", "auth-guarded"),
            ("crontab", ["crontab"], "Edit cron jobs", "auth-guarded"),
            ("apt-get", ["apt_get"], "Install packages", "auth-guarded"),
            ("pip", ["pip_install"], "Install Python packages", "file-write"),
            ("npm", ["npm_install"], "Install Node packages", "file-write"),
            ("make", ["make"], "Run Makefile targets", "file-write"),
            ("zip", ["zip"], "Create zip archive", "file-write"),
            ("unzip", ["unzip"], "Extract zip archive", "file-write"),
            ("wget", ["wget"], "Download file from URL", "network"),
            ("scp", ["scp"], "Secure copy files", "network-auth"),
            ("less", ["less"], "View file with pager", "file-read"),
            ("head", ["head"], "Show first lines", "file-read"),
            ("tail", ["tail"], "Show last lines", "file-read"),
            ("wc", ["wc"], "Count lines/words/bytes", "file-read"),
            ("sort", ["sort"], "Sort lines", "file-read"),
            ("uniq", ["uniq"], "Filter duplicate lines", "file-read"),
            ("cut", ["cut"], "Cut fields from lines", "file-read"),
            ("tee", ["tee"], "Read stdin, write to file and stdout", "file-write"),
            ("xargs", ["xargs"], "Build command from stdin", "file-read"),
            ("diff", ["diff"], "Compare files", "file-read"),
            ("patch", ["patch"], "Apply diff patch", "file-write"),
            ("mount", ["mount"], "Mount filesystem", "auth-guarded"),
            ("umount", ["umount"], "Unmount filesystem", "auth-guarded"),
            ("chown", ["chown"], "Change file ownership", "auth-guarded"),
            ("ln", ["ln"], "Create symbolic link", "file-write"),
            ("stat", ["stat"], "Display file status", "file-read"),
            ("file", ["file"], "Determine file type", "file-read"),
            ("env", ["env"], "Show environment variables", "file-read"),
            ("which", ["which"], "Show command path", "file-read"),
        ],
    },
    {
        "source": "mcp:datadog",
        "tools": [
            ("dd-list-monitors", ["list_monitors"], "List Datadog monitors", "network-auth"),
            ("dd-get-metrics", ["get_metrics"], "Query metrics", "network-auth"),
            ("dd-create-monitor", ["create_monitor"], "Create monitor", "network-auth-write"),
            ("dd-list-dashboards", ["list_dashboards"], "List dashboards", "network-auth"),
            ("dd-get-events", ["get_events"], "Get events", "network-auth"),
        ],
    },
    {
        "source": "mcp:stripe",
        "tools": [
            ("stripe-list-customers", ["list_customers"], "List Stripe customers", "network-auth"),
            ("stripe-create-charge", ["create_charge"], "Create a charge", "network-auth-write"),
            ("stripe-get-balance", ["get_balance"], "Get account balance", "network-auth"),
            ("stripe-list-invoices", ["list_invoices"], "List invoices", "network-auth"),
            ("stripe-create-subscription", ["create_subscription"], "Create subscription", "network-auth-write"),
        ],
    },
    {
        "source": "mcp:twilio",
        "tools": [
            ("twilio-send-sms", ["send_sms"], "Send SMS message", "network-auth-write"),
            ("twilio-list-messages", ["list_messages"], "List SMS messages", "network-auth"),
            ("twilio-make-call", ["make_call"], "Initiate phone call", "network-auth-write"),
            ("twilio-get-call", ["get_call"], "Get call details", "network-auth"),
        ],
    },
    {
        "source": "mcp:elasticsearch",
        "tools": [
            ("es-search", ["es_search"], "Search Elasticsearch index", "network"),
            ("es-index", ["es_index_document"], "Index a document", "network-write"),
            ("es-get", ["es_get_document"], "Get document by ID", "network"),
            ("es-list-indices", ["es_list_indices"], "List indices", "network"),
            ("es-create-index", ["es_create_index"], "Create index", "network-write"),
        ],
    },
    {
        "source": "mcp:grafana",
        "tools": [
            ("grafana-list-dashboards", ["list_dashboards"], "List Grafana dashboards", "network-auth"),
            ("grafana-get-dashboard", ["get_dashboard"], "Get dashboard", "network-auth"),
            ("grafana-query-datasource", ["query_datasource"], "Query data source", "network-auth"),
            ("grafana-list-alerts", ["list_alerts"], "List alerts", "network-auth"),
        ],
    },
    {
        "source": "mcp:sendgrid",
        "tools": [
            ("sendgrid-send-email", ["send_email"], "Send email via SendGrid", "network-auth-write"),
            ("sendgrid-list-templates", ["list_templates"], "List email templates", "network-auth"),
            ("sendgrid-get-stats", ["get_stats"], "Get email statistics", "network-auth"),
        ],
    },
]

_CATEGORY_FLAGS: dict[str, int] = {
    "network": CapabilityFlags.SUPPORTS_NETWORK,
    "network-write": CapabilityFlags.SUPPORTS_NETWORK,
    "network-auth": CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.AUTH_REQUIRED,
    "network-auth-write": CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.AUTH_REQUIRED,
    "file-read": CapabilityFlags.SUPPORTS_FILES,
    "file-write": CapabilityFlags.SUPPORTS_FILES,
    "auth-guarded": CapabilityFlags.SUPPORTS_FILES | CapabilityFlags.AUTH_REQUIRED,
}

_FMT_MAP = {
    "json": FormatType.JSON,
    "text": FormatType.TEXT,
}


def build_synthetic_corpus(*, seed: int = 42) -> list[CorpusEntry]:
    """Generate 410+ synthetic tools supplementing the real corpus."""
    rng = random.Random(seed)
    entries: list[CorpusEntry] = []

    for family in _FAMILIES:
        source = family["source"]
        for name, commands, description, category in family["tools"]:
            flags = _CATEGORY_FLAGS.get(category, 0)
            # Add JSON_OUTPUT to ~60% of tools
            if rng.random() < 0.6:
                flags |= CapabilityFlags.JSON_OUTPUT

            out_fmt = "json" if flags & CapabilityFlags.JSON_OUTPUT else "text"
            latency = rng.randint(5, 5000)
            memory = rng.randint(8, 256)

            descriptor = CapabilityDescriptor(
                name=name,
                version="1.0",
                description=description,
                commands=[CommandDescriptor(name=c) for c in commands],
                input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
                output_formats=[FormatDescriptor(name=out_fmt, type=_FMT_MAP.get(out_fmt, FormatType.TEXT))],
                processing_modes=[ProcessingMode.SYNC],
                capability_flags=flags,
                performance=PerformanceMetrics(
                    avg_processing_time_ms=latency,
                    memory_usage_mb=memory,
                ),
            )
            entries.append(CorpusEntry(descriptor=descriptor, source=source, category=category))

    # Generate versioned variants to hit 500+ (e.g. slack-send-message-v2)
    base_entries = list(entries)
    for variant in ("v2", "v3", "beta"):
        for entry in base_entries:
            d = entry.descriptor
            variant_name = f"{d.name}-{variant}"
            variant_desc = f"{d.description} ({variant})"
            variant_cmds = [CommandDescriptor(name=f"{c.name}_{variant}") for c in d.commands]

            vd = CapabilityDescriptor(
                name=variant_name,
                version=f"1.0-{variant}",
                description=variant_desc,
                commands=variant_cmds,
                input_formats=d.input_formats,
                output_formats=d.output_formats,
                processing_modes=d.processing_modes,
                capability_flags=d.capability_flags,
                performance=d.performance,
            )
            entries.append(CorpusEntry(descriptor=vd, source=entry.source, category=entry.category))

    return entries


def build_scaled_corpus() -> list[CorpusEntry]:
    """Build a 500+ tool corpus: 90 real + synthetic."""
    from tcp.harness.corpus import build_mcp_corpus

    real = build_mcp_corpus()
    synthetic = build_synthetic_corpus()

    # Deduplicate by name (synthetic should not collide with real)
    real_names = {e.descriptor.name for e in real}
    synthetic_deduped = [e for e in synthetic if e.descriptor.name not in real_names]

    return real + synthetic_deduped
