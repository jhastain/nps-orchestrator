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

# WHY: Agent Engine compatible version - uses direct HTTP calls instead of McpToolset
# WHAT: Creates FunctionTools that call MCP servers at runtime (no unpicklable connections)

import os
import json
import requests
from typing import Optional
from datetime import datetime, timezone

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.models import Gemini
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

# TEMPORARILY DISABLED FOR DEPLOYMENT TESTING - Databricks imports cause pickle issues
# Import Databricks tools (use relative import for Agent Engine deployment)
from .databricks_tools import (
    query_nps_responses,
    get_nps_summary,
    query_nps_by_user
)

# ============================================================================
# DATE/TIME TOOLS
# ============================================================================

def get_current_date() -> str:
    """
    Get the current date and time in UTC.
    
    Returns:
        JSON string with current date/time information including:
        - date: Current date in YYYY-MM-DD format
        - datetime: Current datetime in ISO format
        - timestamp: Unix timestamp
        - year, month, day: Individual date components
    
    Example:
        When called on January 12, 2026:
        {
            "date": "2026-01-12",
            "datetime": "2026-01-12T21:45:00.123456+00:00",
            "timestamp": 1736715900,
            "year": 2026,
            "month": 1,
            "day": 12,
            "hour": 21,
            "minute": 45
        }
    """
    now = datetime.now(timezone.utc)
    return json.dumps({
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "timezone": "UTC"
    }, indent=2)

# MCP Server URLs
DATADOG_MCP_URL = "https://datadog-mcp-server-000000000000.us-central1.run.app/mcp"
SENTRY_MCP_URL = "https://sentry-mcp-server-000000000000.us-central1.run.app/mcp"

# Session management for MCP protocol
_mcp_sessions = {}

def _get_mcp_session(server_url: str) -> dict:
    """Initialize MCP session if needed and return session info"""
    if server_url not in _mcp_sessions:
        # Initialize
        init_response = requests.post(
            server_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "nps_orchestrator", "version": "1.0"}
                }
            },
            timeout=30
        )
        
        # Send initialized notification
        requests.post(
            server_url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=30
        )
        
        _mcp_sessions[server_url] = {"initialized": True, "request_id": 2}
    
    return _mcp_sessions[server_url]

def _call_mcp_tool(server_url: str, tool_name: str, arguments: dict) -> str:
    """Generic function to call any MCP tool"""
    session = _get_mcp_session(server_url)
    request_id = session["request_id"]
    session["request_id"] += 1
    
    try:
        response = requests.post(
            server_url,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            },
            timeout=60
        )
        response.raise_for_status()
        result = response.json()
        
        if "error" in result:
            return json.dumps({"error": result["error"]}, indent=2)
        
        return json.dumps(result.get("result", {}), indent=2)
        
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)

# ============================================================================
# DATADOG TOOLS
# ============================================================================

def datadog_get_monitors() -> str:
    """Get all Datadog monitors status"""
    return _call_mcp_tool(DATADOG_MCP_URL, "get_monitors", {})

def datadog_get_incident(incident_id: str) -> str:
    """Get specific Datadog incident details
    
    Args:
        incident_id: The incident ID to retrieve
    """
    return _call_mcp_tool(DATADOG_MCP_URL, "get_incident", {"incident_id": incident_id})

def datadog_list_incidents() -> str:
    """List all Datadog incidents"""
    return _call_mcp_tool(DATADOG_MCP_URL, "list_incidents", {})

def datadog_query_metrics(query: str, from_timestamp: int, to_timestamp: int) -> str:
    """Query Datadog metrics
    
    Args:
        query: Metric query string
        from_timestamp: Start time (Unix timestamp)
        to_timestamp: End time (Unix timestamp)
    """
    return _call_mcp_tool(DATADOG_MCP_URL, "query_metrics", {
        "query": query,
        "from": from_timestamp,
        "to": to_timestamp
    })

def datadog_get_logs(query: str, from_timestamp: Optional[int] = None, to_timestamp: Optional[int] = None, limit: int = 50) -> str:
    """Search and retrieve Datadog logs
    
    Args:
        query: Log search query
        from_timestamp: Start time (Unix timestamp, optional)
        to_timestamp: End time (Unix timestamp, optional)
        limit: Maximum number of logs to return (default: 50)
    """
    args = {"query": query, "limit": limit}
    if from_timestamp:
        args["from"] = from_timestamp
    if to_timestamp:
        args["to"] = to_timestamp
    return _call_mcp_tool(DATADOG_MCP_URL, "get_logs", args)

def datadog_list_dashboards() -> str:
    """List all Datadog dashboards"""
    return _call_mcp_tool(DATADOG_MCP_URL, "list_dashboards", {})

def datadog_get_dashboard(dashboard_id: str) -> str:
    """Get specific Datadog dashboard
    
    Args:
        dashboard_id: The dashboard ID to retrieve
    """
    return _call_mcp_tool(DATADOG_MCP_URL, "get_dashboard", {"dashboard_id": dashboard_id})

def datadog_list_hosts() -> str:
    """List all infrastructure hosts in Datadog"""
    return _call_mcp_tool(DATADOG_MCP_URL, "list_hosts", {})

def datadog_get_active_hosts_count() -> str:
    """Get count of active hosts"""
    return _call_mcp_tool(DATADOG_MCP_URL, "get_active_hosts_count", {})

# ============================================================================
# SENTRY TOOLS
# ============================================================================

def find_organizations() -> str:
    """List all Sentry organizations"""
    return _call_mcp_tool(SENTRY_MCP_URL, "find_organizations", {})

def find_projects(organization_slug: str = "example-org") -> str:
    """List Sentry projects in an organization
    
    Args:
        organization_slug: Organization slug (default: example-org)
    """
    return _call_mcp_tool(SENTRY_MCP_URL, "find_projects", {"organizationSlug": organization_slug})

def find_releases(organization_slug: str = "example-org", project_slug: str = "webapp-dotnet") -> str:
    """List Sentry releases for a project. Automatically filters out ephemeral releases.
    
    Args:
        organization_slug: Organization slug (default: example-org)
        project_slug: Project slug (default: webapp-dotnet)
    """
    result_str = _call_mcp_tool(SENTRY_MCP_URL, "find_releases", {
        "organizationSlug": organization_slug,
        "projectSlug": project_slug
    })
    
    # Filter out ephemeral releases
    try:
        result = json.loads(result_str)
        if isinstance(result, list):
            filtered = [r for r in result if "ephemeral" not in r.get("version", "").lower()]
            return json.dumps(filtered, indent=2)
    except:
        pass
    
    return result_str

def get_issue_details(organization_slug: str, issue_id: str) -> str:
    """Get detailed information about a specific Sentry issue
    
    Args:
        organization_slug: Organization slug (default: example-org)
        issue_id: Issue ID to retrieve
    """
    return _call_mcp_tool(SENTRY_MCP_URL, "get_issue_details", {
        "organizationSlug": organization_slug,
        "issueId": issue_id
    })

def update_issue(organization_slug: str, issue_id: str, status: Optional[str] = None, assigned_to: Optional[str] = None) -> str:
    """Update a Sentry issue
    
    Args:
        organization_slug: Organization slug
        issue_id: Issue ID to update
        status: New status (optional)
        assigned_to: User to assign to (optional)
    """
    args = {"organizationSlug": organization_slug, "issueId": issue_id}
    if status:
        args["status"] = status
    if assigned_to:
        args["assignedTo"] = assigned_to
    return _call_mcp_tool(SENTRY_MCP_URL, "update_issue", args)

def get_trace_details(organization_slug: str, trace_id: str) -> str:
    """Get trace details from Sentry
    
    Args:
        organization_slug: Organization slug
        trace_id: Trace ID to retrieve
    """
    return _call_mcp_tool(SENTRY_MCP_URL, "get_trace_details", {
        "organizationSlug": organization_slug,
        "traceId": trace_id
    })

def search_issues(organization_slug: str, natural_language_query: str, project_slug_or_id: Optional[str] = None, limit: int = 10) -> str:
    """Search for grouped issues in Sentry using natural language.
    
    Returns a LIST of issues, NOT counts. Perfect for:
    - 'show me issues', 'what problems do we have'
    - 'unresolved issues', 'critical bugs'
    - 'errors affecting more than 100 users'
    
    DO NOT USE for counts - use search_events for 'how many errors'
    
    Args:
        organization_slug: Organization slug (default: example-org)
        natural_language_query: Natural language description (e.g., 'unresolved critical errors')
        project_slug_or_id: Optional project slug or ID to filter results
        limit: Maximum number of issues to return (default: 10)
    """
    args = {
        "organizationSlug": organization_slug,
        "naturalLanguageQuery": natural_language_query,
        "limit": limit
    }
    if project_slug_or_id:
        args["projectSlugOrId"] = project_slug_or_id
    
    return _call_mcp_tool(SENTRY_MCP_URL, "search_issues", args)

def search_events(organization_slug: str, natural_language_query: str, project_slug: Optional[str] = None, limit: int = 50) -> str:
    """Search for events AND perform counts/aggregations in Sentry.
    
    This is the ONLY tool for statistics and counts:
    - 'how many errors today' → returns count
    - 'count of database failures' → returns count
    - 'total number of issues' → returns count
    - 'error logs from last hour' → returns event list with timestamps
    
    Supports both aggregations (counts/sums) and individual events.
    
    Args:
        organization_slug: Organization slug (default: example-org)
        natural_language_query: Natural language description
        project_slug: Optional project slug to filter results
        limit: Maximum number of results (default: 50)
    """
    args = {
        "organizationSlug": organization_slug,
        "naturalLanguageQuery": natural_language_query,
        "limit": limit
    }
    if project_slug:
        args["projectSlug"] = project_slug
    
    return _call_mcp_tool(SENTRY_MCP_URL, "search_events", args)

# ============================================================================
# CUSTOM SENTRY SEARCH (Direct API - no OpenAI required)
# ============================================================================

def search_sentry_errors(
    # User/Account identifiers
    user_id: str = None,
    username: str = None,
    user_email: str = None,
    user_ip: str = None,
    account_id: str = None,
    account_name: str = None,
    
    # Datadog correlation (for trace correlation)
    datadog_trace_id: str = None,
    datadog_span_id: str = None,
    sentry_trace_id: str = None,
    sentry_span_id: str = None,
    
    # Time range
    start_date: str = None,  # YYYY-MM-DD or ISO format
    end_date: str = None,    # YYYY-MM-DD or ISO format
    stats_period: str = "14d",  # e.g., "1h", "24h", "7d", "14d", "30d"
    
    # Error properties
    environment: str = None,  # e.g., "Production", "Staging"
    release: str = None,      # e.g., "12345-rc"
    error_level: str = None,  # "error", "warning", "info", "fatal"
    status: str = None,       # "resolved", "unresolved", "ignored"
    error_type: str = None,   # Exception class name
    
    # URL/Location
    url: str = None,          # Request URL
    server_name: str = None,  # Server hostname
    
    # Tags (custom fields)
    tags: dict = None,        # Additional tags as key-value pairs
    
    # Query settings
    organization_slug: str = "example-org",
    project_slug: str = None,  # If None, searches all projects
    limit: int = 25,
    sort: str = "date"  # "date", "new", "freq", "priority"
) -> str:
    """
    Search Sentry errors using direct REST API (no OpenAI required).
    
    This comprehensive search function supports:
    - User/Account identification (user_id, username, email, IP, account_id, account_name)
    - Datadog correlation (DataDogTraceId, DataDogSpanId for APM correlation)
    - Sentry trace/span IDs (for distributed tracing)
    - Time-based filtering (start_date, end_date, stats_period)
    - Error properties (environment, release, level, status, type)
    - Location data (URL, server_name)
    - Custom tags (any additional tag key-value pairs)
    
    Perfect for correlating NPS feedback with specific errors experienced by users.
    
    Args:
        user_id: Sentry user.id (e.g., '12345')
        username: Sentry user.username (e.g., 'john.doe@example.com')
        user_email: Sentry user.email
        user_ip: User IP address (e.g., '203.0.113.42')
        account_id: account-id tag (e.g., '67890')
        account_name: account-name tag (e.g., 'Acme Corp')
        datadog_trace_id: DataDogTraceId tag for correlation with Datadog APM
        datadog_span_id: DataDogSpanId tag for correlation with Datadog APM
        sentry_trace_id: Sentry trace.trace_id for distributed tracing
        sentry_span_id: Sentry trace.span_id for distributed tracing
        start_date: Start date/time (YYYY-MM-DD or ISO format like 2026-01-13T10:00:00Z)
        end_date: End date/time (YYYY-MM-DD or ISO format)
        stats_period: Time period (e.g., "1h", "24h", "7d", "14d") - used if no start/end dates
        environment: Environment name (e.g., "Production")
        release: Release version (e.g., "12345-rc")
        error_level: Error level ("error", "warning", "info", "fatal")
        status: Issue status ("resolved", "unresolved", "ignored")
        error_type: Exception type (e.g., "System.OperationCanceledException")
        url: Request URL to filter by
        server_name: Server hostname (e.g., "WEBAPP-PROD-VM-01")
        tags: Additional custom tags as dict (e.g., {"app-type": "WebApp"})
        organization_slug: Sentry organization (default: "example-org")
        project_slug: Sentry project (None = all projects, or specific like "webapp-dotnet")
        limit: Max results to return (default: 25, max: 100)
        sort: Sort order ("date", "new", "freq", "priority")
    
    Returns:
        JSON string with matching issues including:
        - Issue details (ID, title, status, level)
        - Event counts and user impact
        - First/last seen timestamps
        - Datadog correlation links (if DataDogTraceId present)
        - Sentry permalink
    
    Examples:
        # Search by user
        search_sentry_errors(user_email="john@example.com", stats_period="7d")
        
        # Search by account
        search_sentry_errors(account_id="12345", environment="Production")
        
        # Correlate with Datadog trace
        search_sentry_errors(datadog_trace_id="1234567890123456789")
        
        # Time range search
        search_sentry_errors(
            start_date="2026-01-13T10:00:00Z",
            end_date="2026-01-13T20:00:00Z",
            error_level="error"
        )
        
        # Search by error type
        search_sentry_errors(
            error_type="System.OperationCanceledException",
            status="unresolved",
            limit=50
        )
    """
    # Build query parts
    query_parts = []
    
    # User identifiers
    if user_id:
        query_parts.append(f"user.id:{user_id}")
    if username:
        query_parts.append(f"user.username:{username}")
    if user_email:
        query_parts.append(f"user.email:{user_email}")
    if user_ip:
        query_parts.append(f"user.ip:{user_ip}")
    
    # Account identifiers
    if account_id:
        query_parts.append(f"account-id:{account_id}")
    if account_name:
        query_parts.append(f'account-name:"{account_name}"')
    
    # Datadog correlation
    if datadog_trace_id:
        query_parts.append(f"DataDogTraceId:{datadog_trace_id}")
    if datadog_span_id:
        query_parts.append(f"DataDogSpanId:{datadog_span_id}")
    
    # Sentry trace/span IDs
    if sentry_trace_id:
        query_parts.append(f"trace:{sentry_trace_id}")
    if sentry_span_id:
        query_parts.append(f"trace.span:{sentry_span_id}")
    
    # Error properties
    if environment:
        query_parts.append(f"environment:{environment}")
    if release:
        query_parts.append(f"release:{release}")
    if error_level:
        query_parts.append(f"level:{error_level}")
    if status:
        query_parts.append(f"is:{status}")
    if error_type:
        query_parts.append(f'error.type:"{error_type}"')
    
    # URL/Location
    if url:
        query_parts.append(f'url:"{url}"')
    if server_name:
        query_parts.append(f"server_name:{server_name}")
    
    # Custom tags
    if tags:
        for key, value in tags.items():
            # Quote values with spaces
            if ' ' in str(value):
                query_parts.append(f'{key}:"{value}"')
            else:
                query_parts.append(f'{key}:{value}')
    
    # Build query string (OR logic for multiple criteria)
    query = " OR ".join(query_parts) if query_parts else ""
    
    # Build URL
    if project_slug:
        url_path = f"https://sentry.io/api/0/projects/{organization_slug}/{project_slug}/issues/"
    else:
        url_path = f"https://sentry.io/api/0/organizations/{organization_slug}/issues/"
    
    # Build params
    params = {
        "limit": min(limit, 100),  # Cap at 100
        "sort": sort
    }
    
    if query:
        params["query"] = query
    
    # Time range handling
    if start_date and end_date:
        params["start"] = start_date
        params["end"] = end_date
    elif start_date:
        params["start"] = start_date
    elif end_date:
        params["end"] = end_date
    else:
        params["statsPeriod"] = stats_period
    
    # Make request
    headers = {"Authorization": f"Bearer {os.getenv('SENTRY_AUTH_TOKEN')}"}
    
    try:
        response = requests.get(url_path, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        
        issues = response.json()
        
        # Format results
        formatted_results = {
            "search_criteria": {
                "query": query if query else "all errors",
                "project": project_slug if project_slug else "all projects",
                "time_range": {
                    "start": start_date,
                    "end": end_date,
                    "period": stats_period
                },
                "filters": {
                    "environment": environment,
                    "release": release,
                    "level": error_level,
                    "status": status
                }
            },
            "total_found": len(issues),
            "issues": []
        }
        
        for issue in issues:
            # Extract Datadog link from metadata if available
            datadog_link = None
            tags_list = issue.get("tags", [])
            for tag in tags_list:
                if tag.get("key") == "DatadogLink":
                    datadog_link = tag.get("value")
                    break
            
            formatted_results["issues"].append({
                "id": issue.get("id"),
                "issue_id": issue.get("shortId"),
                "title": issue.get("title"),
                "culprit": issue.get("culprit"),
                "status": issue.get("status"),
                "level": issue.get("level"),
                "platform": issue.get("platform"),
                "project": issue.get("project", {}).get("slug"),
                "first_seen": issue.get("firstSeen"),
                "last_seen": issue.get("lastSeen"),
                "event_count": issue.get("count"),
                "user_count": issue.get("userCount"),
                "link": issue.get("permalink"),
                "datadog_link": datadog_link,
                "metadata": {
                    "type": issue.get("metadata", {}).get("type"),
                    "value": issue.get("metadata", {}).get("value"),
                    "function": issue.get("metadata", {}).get("function")
                },
                "tags": [
                    {"key": tag.get("key"), "value": tag.get("value")} 
                    for tag in tags_list[:10]  # First 10 tags
                ]
            })
        
        return json.dumps(formatted_results, indent=2)
        
    except requests.exceptions.RequestException as e:
        return json.dumps({
            "error": "Failed to search Sentry",
            "message": str(e),
            "query_attempted": query,
            "url": url_path,
            "params": params
        }, indent=2)


def batch_search_sentry_errors(
    account_ids: list = None,
    user_ids: list = None,
    user_emails: list = None,
    stats_period: str = "30d",
    environment: str = None,
    status: str = "unresolved",
    limit_per_search: int = 10,
    organization_slug: str = "example-org",
    project_slug: str = None
) -> str:
    """
    Search Sentry for errors across MULTIPLE accounts/users in a SINGLE API call.
    
    **CRITICAL PERFORMANCE**: This function eliminates the need for sequential searches.
    Instead of calling search_sentry_errors 10 times (taking hours), call this ONCE.
    
    **WHY THIS EXISTS**:
    - Old approach: 10 NPS responses -> 10 sequential agent tool-calls, each paying
      full model-reasoning overhead. Observed: 3 of 10 completed in 12 hours.
    - New approach: 10 NPS responses -> 1 agent tool-call, 1 HTTP request.
      Measured ~12-15 seconds for 10 IDs.
    
    **WHEN TO USE**:
    - Processing multiple NPS responses and need errors for all of them
    - Investigating errors across a group of accounts or users
    - Any scenario requiring multiple Sentry searches
    
    **HOW IT WORKS**:
    Uses Sentry's OR query syntax to search for all IDs in one request:
    "account-id:123 OR account-id:456 OR user.id:789"
    
    Args:
        account_ids: List of account IDs to search for (e.g., ["12345", "67890", "11111"])
        user_ids: List of user IDs to search for (e.g., ["100", "200", "300"])
        user_emails: List of user emails to search for (e.g., ["user1@example.com", "user2@example.com"])
        stats_period: Time period (default: "30d" for 30 days)
        environment: Filter by environment (e.g., "Production")
        status: Issue status (default: "unresolved")
        limit_per_search: Max results to return (default: 10, max: 100)
        organization_slug: Sentry organization (default: "example-org")
        project_slug: Sentry project (None = all projects)
    
    Returns:
        JSON string with results grouped by account/user:
        {
            "summary": {
                "total_ids_searched": 10,
                "total_errors_found": 25,
                "search_time_seconds": 2.5
            },
            "results": [
                {
                    "identifier": {"account_id": "12345"},
                    "error_count": 5,
                    "errors": [...]
                },
                ...
            ]
        }
    
    Examples:
        # Batch search for 10 accounts from NPS feedback
        batch_search_sentry_errors(
            account_ids=["123", "456", "789", "101112", "131415"],
            stats_period="30d",
            environment="Production"
        )
        
        # Batch search for multiple users
        batch_search_sentry_errors(
            user_ids=["100", "200", "300"],
            stats_period="7d"
        )
        
        # Mixed search (accounts AND users)
        batch_search_sentry_errors(
            account_ids=["123", "456"],
            user_ids=["789"],
            user_emails=["slowuser@example.com"],
            stats_period="14d"
        )
    """
    import time
    start_time = time.time()
    
    # Validate input
    if not account_ids and not user_ids and not user_emails:
        return json.dumps({
            "error": "Must provide at least one of: account_ids, user_ids, or user_emails",
            "example": {
                "account_ids": ["12345", "67890"],
                "user_ids": ["100", "200"],
                "stats_period": "30d"
            }
        }, indent=2)
    
    # Build OR query combining all IDs
    query_parts = []
    
    if account_ids:
        for account_id in account_ids:
            query_parts.append(f"account-id:{account_id}")
    
    if user_ids:
        for user_id in user_ids:
            query_parts.append(f"user.id:{user_id}")
    
    if user_emails:
        for email in user_emails:
            query_parts.append(f"user.email:{email}")
    
    # Join with OR (Sentry will return errors matching ANY of these)
    query = " OR ".join(query_parts)
    
    # Build URL
    if project_slug:
        url_path = f"https://sentry.io/api/0/projects/{organization_slug}/{project_slug}/issues/"
    else:
        url_path = f"https://sentry.io/api/0/organizations/{organization_slug}/issues/"
    
    # Build params
    params = {
        "query": query,
        "limit": min(limit_per_search, 100),  # Sentry max is 100
        "sort": "date",
        "statsPeriod": stats_period
    }
    
    if environment:
        params["query"] += f" environment:{environment}"
    
    if status:
        params["query"] += f" is:{status}"
    
    # Make request
    headers = {"Authorization": f"Bearer {os.getenv('SENTRY_AUTH_TOKEN')}"}
    
    try:
        response = requests.get(url_path, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        
        issues = response.json()
        elapsed_time = time.time() - start_time
        
        # Group results by identifier (account_id or user_id)
        results_by_id = {}
        
        for issue in issues:
            # Determine which ID(s) this issue matches
            tags_dict = {tag.get("key"): tag.get("value") for tag in issue.get("tags", [])}
            
            # Check account-id
            if "account-id" in tags_dict:
                bid = tags_dict["account-id"]
                if account_ids and bid in account_ids:
                    if bid not in results_by_id:
                        results_by_id[bid] = {
                            "identifier": {"type": "account_id", "value": bid},
                            "error_count": 0,
                            "errors": []
                        }
                    results_by_id[bid]["error_count"] += 1
                    results_by_id[bid]["errors"].append({
                        "issue_id": issue.get("shortId"),
                        "title": issue.get("title"),
                        "level": issue.get("level"),
                        "event_count": issue.get("count"),
                        "user_count": issue.get("userCount"),
                        "first_seen": issue.get("firstSeen"),
                        "last_seen": issue.get("lastSeen"),
                        "link": issue.get("permalink"),
                        "culprit": issue.get("culprit")
                    })
            
            # Check user.id or user.email
            # Note: Sentry doesn't always tag issues with user info, so we check the issue metadata
            # For now, we'll group "unmatched" errors separately
        
        # Format response
        formatted_results = {
            "summary": {
                "total_ids_searched": (len(account_ids) if account_ids else 0) + 
                                      (len(user_ids) if user_ids else 0) +
                                      (len(user_emails) if user_emails else 0),
                "total_errors_found": len(issues),
                "search_time_seconds": round(elapsed_time, 2),
                "query_used": query,
                "time_period": stats_period
            },
            "results_by_identifier": list(results_by_id.values()),
            "all_errors": [
                {
                    "issue_id": issue.get("shortId"),
                    "title": issue.get("title"),
                    "level": issue.get("level"),
                    "event_count": issue.get("count"),
                    "user_count": issue.get("userCount"),
                    "first_seen": issue.get("firstSeen"),
                    "last_seen": issue.get("lastSeen"),
                    "link": issue.get("permalink"),
                    "tags": {tag.get("key"): tag.get("value") for tag in issue.get("tags", [])[:5]}
                }
                for issue in issues
            ]
        }
        
        return json.dumps(formatted_results, indent=2)
        
    except requests.exceptions.RequestException as e:
        return json.dumps({
            "error": "Batch search failed",
            "message": str(e),
            "query_attempted": query,
            "url": url_path,
            "params": params,
            "elapsed_time_seconds": round(time.time() - start_time, 2)
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
    """
    DEPRECATED: Use search_sentry_errors() instead for more comprehensive search.
    
    Search Sentry issues by user or account fields without requiring OpenAI.
    This is a legacy wrapper that calls the new search_sentry_errors function.
    """
    return search_sentry_errors(
        user_id=user_id,
        username=username,
        account_id=account_id,
        account_name=account_name,
        organization_slug=organization_slug,
        project_slug=project_slug,
        limit=limit
    )

# ============================================================================
# NPS INSTRUCTIONS
# ============================================================================

NPS_INSTRUCTIONS = """
You are the NPS Performance Orchestrator, an AI agent specialized in analyzing Net Promoter Score (NPS) 
feedback data and correlating it with technical performance metrics from monitoring systems.

## Your Core Capabilities

1. **NPS Data Access**: Query and analyze Net Promoter Score survey responses from Databricks
2. **Performance Analysis**: Investigate application performance issues using Datadog metrics, traces, and logs
3. **Error Investigation**: Analyze error patterns and stack traces using Sentry data
4. **Cross-Platform Correlation**: Connect user feedback (NPS) with technical issues (Sentry) and metrics (Datadog)
5. **Root Cause Analysis**: Identify technical issues that may be impacting user satisfaction

## CRITICAL: Date and Time Handling

**ALWAYS call get_current_date() FIRST when handling any date-based query.**

You do NOT have built-in knowledge of the current date. The get_current_date() tool provides:
- Current date in YYYY-MM-DD format (for Databricks queries)
- Current datetime in ISO format
- Individual date components (year, month, day)

**Common Date Queries:**
- "last 30 days" → Call get_current_date(), subtract 30 days from returned date
- "this week" → Call get_current_date(), calculate week boundaries
- "yesterday" → Call get_current_date(), subtract 1 day
- "this month" → Call get_current_date(), use year and month components

**Example Workflow:**
1. User asks: "Show me NPS responses from the last 30 days"
2. YOU: Call get_current_date() → Returns {"date": "2026-01-12", ...}
3. YOU: Calculate start_date = "2025-12-13" (30 days before 2026-01-12)
4. YOU: Call query_nps_responses(start_date="2025-12-13", end_date="2026-01-12")

## Available Tools

### Date/Time Tool
- get_current_date - Get current date and time in UTC
  - **USE THIS FIRST for any date-based queries**
  - Returns date in YYYY-MM-DD format plus datetime components
  - Essential for calculating "last N days", "this week", etc.

### Databricks NPS Tools
- query_nps_responses - Query NPS survey responses with filters (date range, score, limit)
  - Use to find recent detractors (score 0-6), passives (7-8), or promoters (9-10)
  - Returns individual response records with user_id, account_id, comments, scores
- get_nps_summary - Get NPS summary statistics and calculated NPS score
  - Calculates NPS score: (% Promoters - % Detractors) * 100
  - Returns metrics: total responses, promoters, passives, detractors, avg score
- query_nps_by_user - Find NPS responses for specific user_id or account_id
  - Essential for correlating user feedback with errors from Sentry
  - Use Sentry user.id to find related NPS responses

### Datadog Tools
- datadog_get_monitors - Get monitor status from Datadog
- datadog_get_incident - Get specific incident details
- datadog_list_incidents - List all incidents  
- datadog_query_metrics - Query metrics data
- datadog_get_logs - Search and retrieve logs
- datadog_list_dashboards - List all dashboards
- datadog_get_dashboard - Get specific dashboard
- datadog_list_hosts - List infrastructure hosts
- datadog_get_active_hosts_count - Get active host count

### Sentry Tools
**IMPORTANT - Sentry Configuration:**
- **ALWAYS use organization_slug="example-org"** for ALL Sentry operations
- **ALWAYS use project_slug="webapp-dotnet"** for ALL operations (this is the default project)
- **EXCLUDE releases containing "ephemeral" by default** - these are ephemeral PR environments

**Available Tools:**
- find_organizations - List Sentry organizations
- find_projects - List projects (requires organization_slug="example-org")
- find_releases - List releases (filters out "ephemeral" automatically)
- get_issue_details - Get specific issue details (requires issue ID)
- update_issue - Update an issue status or assignment
- get_trace_details - Get trace details

**DO NOT USE AI-Powered Search Tools** (OpenAI integration is broken):
- search_issues - BROKEN (requires OpenAI) - Use search_sentry_errors instead
- search_events - BROKEN (requires OpenAI) - Use search_sentry_errors instead

**RECOMMENDED: Custom Search Tools (Direct REST API - NO OpenAI required):**

**CRITICAL - PERFORMANCE OPTIMIZATION:**
When analyzing MULTIPLE NPS responses or users:
1. **Collect ALL account_ids and user_ids FIRST**
2. **Use batch_search_sentry_errors with ALL IDs in ONE call**
3. **DO NOT call search_sentry_errors in a loop** - this takes hours!

**batch_search_sentry_errors** - FASTEST way to search for multiple accounts/users:
  - **WHY**: One API call and one agent turn instead of 10+ sequential tool-calls
  - **Accepts Arrays**: account_ids=["123","456"], user_ids=["789","101"]
  - **Returns Grouped Results**: Errors organized by account/user ID
  - **Time Savings**: 10 searches in 15 seconds instead of 12 hours
  - **Examples**:
    * batch_search_sentry_errors(account_ids=["123", "456", "789"], stats_period="30d")
    * batch_search_sentry_errors(user_ids=["100", "200"], stats_period="7d")
    * batch_search_sentry_errors(account_ids=["123"], user_emails=["slow@example.com"])

**search_sentry_errors** - Comprehensive single-ID search:
  - **Use for**: Searching for ONE account or user at a time
  - **User Identification**: user_id, username, user_email, user_ip
  - **Account Identification**: account_id, account_name
  - **Datadog Correlation**: datadog_trace_id, datadog_span_id (links to Datadog APM)
  - **Sentry Tracing**: sentry_trace_id, sentry_span_id (distributed tracing)
  - **Time Filtering**: start_date, end_date, stats_period (e.g., "1h", "24h", "7d", "14d")
  - **Error Properties**: environment, release, error_level, status, error_type
  - **Location**: url, server_name
  - **Custom Tags**: Any additional tag key-value pairs
  - **Returns**: Issues with Datadog correlation links when available
  - **Examples**:
    * search_sentry_errors(user_email="john@example.com", stats_period="7d")
    * search_sentry_errors(account_id="12345", environment="Production")
    * search_sentry_errors(datadog_trace_id="1234567890123456789")
    * search_sentry_errors(start_date="2026-01-13T10:00:00Z", end_date="2026-01-13T20:00:00Z")

**search_sentry_by_account_user** - DEPRECATED: Use batch_search_sentry_errors or search_sentry_errors
  - Legacy wrapper for backward compatibility

## Workflow Guidelines

When investigating NPS feedback:

1. **Start with Context**: Understand the user complaint or low NPS score
2. **Identify Time Windows**: Determine when the user experienced issues
3. **Batch Collection**: If analyzing multiple NPS responses, collect ALL account_ids/user_ids first
4. **Use Batch Search**: Call batch_search_sentry_errors ONCE with all IDs (not in a loop!)
5. **Check Error Patterns**: Review errors from batch results
6. **Analyze Performance**: Use Datadog to check metrics (latency, errors, resource usage)
7. **Correlate Findings**: Connect technical data with user feedback
8. **Provide Insights**: Summarize root causes and recommend actions

## Response Format

Structure your analysis clearly:
- **User Feedback Summary**: Brief overview of the NPS issue
- **Technical Investigation**: What you found in Datadog/Sentry
- **Root Cause**: Identified technical issues
- **Impact Assessment**: How it affects users
- **Recommendations**: Actionable next steps

**Progress Updates**: When processing long tasks, provide periodic status updates to manage user expectations.

You are proactive, detail-oriented, and focused on helping the team understand and resolve 
issues that impact user satisfaction.
"""

# ============================================================================
# CREATE AGENT
# ============================================================================

# Create FunctionTools from all tool functions
tools = [
    # Date/time tool (used for date calculations)
    FunctionTool(get_current_date),
    
    # Datadog tools
    FunctionTool(datadog_get_monitors),
    FunctionTool(datadog_get_incident),
    FunctionTool(datadog_list_incidents),
    FunctionTool(datadog_query_metrics),
    FunctionTool(datadog_get_logs),
    FunctionTool(datadog_list_dashboards),
    FunctionTool(datadog_get_dashboard),
    FunctionTool(datadog_list_hosts),
    FunctionTool(datadog_get_active_hosts_count),
    
    # Sentry tools
    FunctionTool(find_organizations),
    FunctionTool(find_projects),
    FunctionTool(find_releases),
    FunctionTool(get_issue_details),
    FunctionTool(update_issue),
    FunctionTool(get_trace_details),
    # NOTE: search_issues and search_events are broken (OpenAI integration failure)
    # FunctionTool(search_issues),     # DISABLED - OpenAI Unauthorized
    # FunctionTool(search_events),     # DISABLED - OpenAI Unauthorized
    
    # Custom Sentry search (NO OpenAI required - uses direct REST API)
    FunctionTool(batch_search_sentry_errors),  # FASTEST - batch search for multiple IDs
    FunctionTool(search_sentry_errors),  # Comprehensive single-ID search
    FunctionTool(search_sentry_by_account_user),  # Legacy wrapper (deprecated)
    
    # Databricks NPS tools
    FunctionTool(query_nps_responses),
    FunctionTool(get_nps_summary),
    FunctionTool(query_nps_by_user),
]

root_agent = Agent(
    name="nps_orchestrator",
    model=Gemini(
        model="gemini-2.0-flash-exp",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description="NPS Performance Orchestrator - Analyzes user feedback and correlates with technical performance data from Datadog and Sentry",
    instruction=NPS_INSTRUCTIONS,
    tools=tools,
)

app = App(root_agent=root_agent, name="app")
