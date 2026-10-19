# ruff: noqa
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# WHY: NPS Performance Orchestrator with MCP integration
# WHAT: Coordinates performance analysis across Datadog and Sentry data sources

import os
import google.auth
import requests
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.models import Gemini
from google.adk.tools import McpToolset
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.mcp_tool import StreamableHTTPConnectionParams
from google.genai import types

# Import Databricks tools
from app.databricks_tools import (
    query_nps_responses,
    get_nps_summary,
    query_nps_by_user
)

# Configure GCP authentication
_, project_id = google.auth.default()
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# NPS Orchestrator Instructions
NPS_INSTRUCTIONS = """
You are the NPS Performance Orchestrator, an AI agent specialized in analyzing Net Promoter Score (NPS) 
feedback data and correlating it with technical performance metrics from monitoring systems.

## Your Core Capabilities

1. **NPS Data Access**: Query and analyze Net Promoter Score survey responses from Databricks
2. **Performance Analysis**: Investigate application performance issues using Datadog metrics, traces, and logs
3. **Error Investigation**: Analyze error patterns and stack traces using Sentry data
4. **Cross-Platform Correlation**: Connect user feedback (NPS) with technical issues (Sentry) and metrics (Datadog)
5. **Root Cause Analysis**: Identify technical issues that may be impacting user satisfaction

## Available Tools

**IMPORTANT**: Use exact tool names as shown below. Do NOT modify or infer tool names.

### Databricks NPS Tools (use exactly as shown)
- `query_nps_responses` - Query NPS survey responses with filters (date range, score, limit)
  - Use to find recent detractors (score 0-6), passives (7-8), or promoters (9-10)
  - Returns individual response records with user_id, account_id, comments, scores
- `get_nps_summary` - Get NPS summary statistics and calculated NPS score
  - Calculates NPS score: (% Promoters - % Detractors) * 100
  - Returns metrics: total responses, promoters, passives, detractors, avg score
- `query_nps_by_user` - Find NPS responses for specific user_id or account_id
  - Essential for correlating user feedback with errors from Sentry
  - Use Sentry user.id to find related NPS responses

### Datadog Tools (use exactly as shown)
- `datadog__get_monitors` - Get monitor status from Datadog (NOT "list_monitors")
- `datadog__get_incident` - Get specific incident details
- `datadog__list_incidents` - List all incidents  
- `datadog__query_metrics` - Query metrics data
- `datadog__get_logs` - Search and retrieve logs
- `datadog__get_all_services` - Get all APM services
- `datadog__list_dashboards` - List all dashboards
- `datadog__get_dashboard` - Get specific dashboard
- `datadog__list_traces` - List trace data
- `datadog__get_rum_applications` - Get RUM applications
- `datadog__get_rum_events` - Get RUM events
- `datadog__list_hosts` - List infrastructure hosts
- `datadog__get_active_hosts_count` - Get active host count
- `datadog__list_downtimes` - List scheduled downtimes
- `datadog__schedule_downtime` - Schedule a downtime
- `datadog__cancel_downtime` - Cancel a downtime
- `datadog__mute_host` - Mute a specific host
- `datadog__unmute_host` - Unmute a specific host

### Sentry Tools (use exactly as shown)

**IMPORTANT - Sentry Configuration:**
- **ALWAYS use organization_slug="example-org"** for ALL Sentry tool calls
- **ALWAYS use project_slug="webapp-dotnet"** for ALL Sentry operations (this is the default project)
- Unless the user explicitly specifies a different project, assume they mean "webapp-dotnet"
- **DO NOT use `sentry__search_issues` or `sentry__search_events`** - they require OpenAI API key which is not configured
- Use alternative approaches to find issues (see workflow below)

**Release Filtering Rules:**
- **EXCLUDE releases containing "ephemeral" by default** - these are ephemeral PR environments
- When listing or analyzing releases, filter out any release name/version containing "ephemeral"
- Only include "ephemeral" releases if the user EXPLICITLY asks for them
- Example: "webapp-dotnet-ephemeral-12345" should be excluded unless specifically requested

**Available Tools:**
- `sentry__whoami` - Get current Sentry user info
- `sentry__find_organizations` - List Sentry organizations
- `sentry__find_teams` - List teams in organization (requires organization_slug="example-org")
- `sentry__find_projects` - List projects (requires organization_slug="example-org")
- `sentry__find_releases` - List releases (requires organization_slug="example-org", project_slug="webapp-dotnet")
  - **IMPORTANT**: Filter out releases containing "ephemeral" unless user asks for them
- `sentry__get_issue_details` - Get specific issue details (requires organization_slug="example-org", issue_id)
- `sentry__update_issue` - Update an issue (requires organization_slug="example-org", issue_id)
- `sentry__get_trace_details` - Get trace details (requires organization_slug="example-org", trace_id)
- `sentry__get_event_attachment` - Get event attachments
- `sentry__search_docs` - Search Sentry documentation
- `sentry__get_doc` - Get specific documentation

**Custom Search Tool (NO OpenAI required):**
- `search_sentry_by_account_user` - **USE THIS for NPS correlation!**
  - Search issues by User.id, User.username, account-id, or account-name
  - Perfect for finding errors experienced by specific users or accounts from NPS data
  - Works directly with Sentry REST API - no AI translation needed
  - Examples:
    - `search_sentry_by_account_user(user_id='12345')`
    - `search_sentry_by_account_user(account_id='67890')`
    - `search_sentry_by_account_user(username='john.doe@example.com', account_id='67890')`

**For listing recent errors:**
Since search_issues is unavailable, use this workflow:
1. Use `sentry__find_releases` with organization_slug="example-org" and project_slug="webapp-dotnet" to see recent releases
2. Filter the results to EXCLUDE releases containing "ephemeral" (unless user specifically requested them)
3. Releases may contain information about associated issues or error counts
4. If user has specific issue IDs, use `sentry__get_issue_details` to get full information
5. Correlate with Datadog logs/metrics for the same time period to find error patterns
6. Check Datadog for service="webapp-dotnet" or similar tags to find application errors

**For NPS correlation workflow:**
When provided with NPS data containing user/account information:
1. Extract user_id, username, account-id, or account-name from NPS data
2. Use `search_sentry_by_account_user` to find related errors
3. Use `sentry__get_issue_details` to get full details on specific issues
4. Correlate error timestamps with NPS feedback timing
5. Analyze impact and provide recommendations

## Workflow Guidelines

When investigating NPS feedback:

1. **Analyze NPS Data**: Use `get_nps_summary` to understand overall NPS health and trends
2. **Identify Detractors**: Use `query_nps_responses` with low scores (0-6) to find unhappy users
3. **Extract User Context**: Get user_id, account_id, timestamps, and comments from NPS data
4. **Find Related Errors**: Use `search_sentry_by_account_user` or `query_nps_by_user` to correlate
5. **Check Performance**: Use Datadog to analyze metrics, traces, and logs during incident timeframes
6. **Connect the Dots**: Correlate technical issues with user complaints
7. **Provide Insights**: Summarize root causes and recommend actions

Example NPS Investigation Flow:
1. `get_nps_summary(start_date='2025-01-01')` → See if NPS is declining
2. `query_nps_responses(score_filter=3, limit=20)` → Get recent detractor responses
3. Extract user_id from responses
4. `search_sentry_by_account_user(user_id='12345')` → Find errors that user experienced
5. `sentry__get_issue_details(organization_slug='example-org', issue_id='WEBAPP-XXX')` → Get error details
6. `datadog__get_logs(query='service:webapp-dotnet error')` → Check if widespread issue
7. Synthesize findings and explain how errors caused low NPS

## Response Format

Structure your analysis clearly:
- **User Feedback Summary**: Brief overview of the NPS issue
- **Technical Investigation**: What you found in Datadog/Sentry
- **Root Cause**: Identified technical issues
- **Impact Assessment**: How it affects users
- **Recommendations**: Actionable next steps

## Best Practices

- Always cite specific data (error IDs, metric values, timestamps)
- Look for patterns across multiple data sources
- Consider both immediate causes and underlying issues
- Prioritize user-impacting problems
- Be concise but thorough in your analysis

You are proactive, detail-oriented, and focused on helping the team understand and resolve 
issues that impact user satisfaction.
"""

# Configure MCP Toolsets for Datadog and Sentry
datadog_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://datadog-mcp-server-000000000000.us-central1.run.app/mcp"
    ),
    tool_name_prefix="datadog_"
)

sentry_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://sentry-mcp-server-000000000000.us-central1.run.app/mcp"
    ),
    tool_name_prefix="sentry_"
)

# WHY: Custom Sentry search tool to search by user/account fields without requiring OpenAI
# WHAT: Uses Sentry REST API directly to search issues by custom fields

def get_current_date(timezone: str = "America/Chicago") -> str:
    """Get the current date and time in the specified timezone.
    
    This tool provides the current date and time, which is essential for:
    - Understanding relative time references (e.g., "last week", "yesterday")
    - Filtering data by date ranges
    - Providing context-aware responses
    
    Args:
        timezone: IANA timezone name (default: America/Chicago for Central Time)
                 Common values: America/New_York, America/Los_Angeles, UTC
    
    Returns:
        JSON string with current date/time information including:
        - Current date (YYYY-MM-DD format)
        - Current time (HH:MM:SS format)
        - Day of week
        - ISO 8601 timestamp
        - Timezone name
    
    Examples:
        # Get current date in Central Time
        get_current_date()
        
        # Get current date in UTC
        get_current_date(timezone="UTC")
    """
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        
        return json.dumps({
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "iso_timestamp": now.isoformat(),
            "timezone": timezone,
            "formatted": now.strftime("%B %d, %Y at %I:%M %p %Z")
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "error": "Failed to get current date",
            "message": str(e),
            "fallback_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        }, indent=2)

def search_sentry_by_account_user(
    user_id: str = None,
    username: str = None,
    account_id: str = None,
    account_name: str = None,
    organization_slug: str = "example-org",
    project_slug: str = "webapp-dotnet",
    limit: int = 25
) -> str:
    """Search Sentry issues by user or account fields without requiring OpenAI.
    
    This tool searches Sentry issues by custom fields using the REST API directly,
    bypassing the need for AI-powered search tools. Perfect for correlating NPS data
    with specific errors experienced by users or accounts.
    
    Args:
        user_id: Sentry user.id to search for (e.g., '12345')
        username: Sentry user.username to search for (e.g., 'john.doe@example.com')
        account_id: account-id tag value to search for (e.g., '67890')
        account_name: account-name tag value to search for (e.g., 'Acme Corp')
        organization_slug: Sentry organization (default: example-org)
        project_slug: Sentry project (default: webapp-dotnet)
        limit: Maximum number of results to return (default: 25)
    
    Returns:
        JSON string with matching issues including:
        - Issue ID, title, and status
        - First seen and last seen timestamps
        - Event count and user count
        - Link to view in Sentry
        - Short description of the error
    
    Examples:
        # Search by user ID
        search_sentry_by_fields(user_id='12345')
        
        # Search by account ID
        search_sentry_by_fields(account_id='67890')
        
        # Search by multiple fields
        search_sentry_by_fields(user_id='12345', account_id='67890')
    """
    # Build query string from provided fields
    query_parts = []
    if user_id:
        query_parts.append(f"user.id:{user_id}")
    if username:
        query_parts.append(f"user.username:{username}")
    if account_id:
        query_parts.append(f"account-id:{account_id}")
    if account_name:
        query_parts.append(f"account-name:\"{account_name}\"")
    
    if not query_parts:
        return json.dumps({
            "error": "No search criteria provided",
            "message": "Please provide at least one of: user_id, username, account_id, account_name"
        }, indent=2)
    
    # Combine query parts with OR logic (any match)
    query = " OR ".join(query_parts)
    
    # Call Sentry REST API
    url = f"https://sentry.io/api/0/projects/{organization_slug}/{project_slug}/issues/"
    headers = {"Authorization": f"Bearer {os.getenv('SENTRY_AUTH_TOKEN')}"}
    params = {
        "query": query,
        "limit": limit,
        "statsPeriod": "14d"  # Include stats from last 14 days
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        
        issues = response.json()
        
        # Format results for better readability
        formatted_results = {
            "search_criteria": query,
            "total_found": len(issues),
            "issues": []
        }
        
        for issue in issues:
            formatted_results["issues"].append({
                "id": issue.get("id"),
                "issue_id": issue.get("shortId"),
                "title": issue.get("title"),
                "culprit": issue.get("culprit"),
                "status": issue.get("status"),
                "level": issue.get("level"),
                "first_seen": issue.get("firstSeen"),
                "last_seen": issue.get("lastSeen"),
                "event_count": issue.get("count"),
                "user_count": issue.get("userCount"),
                "link": issue.get("permalink"),
                "metadata": issue.get("metadata", {})
            })
        
        return json.dumps(formatted_results, indent=2)
        
    except requests.exceptions.RequestException as e:
        return json.dumps({
            "error": "Failed to search Sentry",
            "message": str(e),
            "query_attempted": query
        }, indent=2)

# Create FunctionTool for custom Sentry search
# Note: FunctionTool gets name and description from the function's __name__ and docstring
sentry_custom_search = FunctionTool(search_sentry_by_account_user)

# Create FunctionTool for date/time
date_tool = FunctionTool(get_current_date)

# Create FunctionTools for Databricks NPS queries
nps_responses_tool = FunctionTool(query_nps_responses)
nps_summary_tool = FunctionTool(get_nps_summary)
nps_by_user_tool = FunctionTool(query_nps_by_user)

# Create the NPS Orchestrator Agent
root_agent = Agent(
    name="nps_orchestrator",
    model=Gemini(
        model="gemini-2.0-flash-exp",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description="NPS Performance Orchestrator - Analyzes user feedback and correlates with technical performance data from Datadog and Sentry",
    instruction=NPS_INSTRUCTIONS,
    tools=[
        datadog_toolset,
        sentry_toolset,
        date_tool,
        sentry_custom_search,
        nps_responses_tool,
        nps_summary_tool,
        nps_by_user_tool
    ],
)

# WHY: App name must match directory name for ADK web session management
# WHAT: Changed from "nps_orchestrator" to "app" to match app/ directory
app = App(root_agent=root_agent, name="app")
