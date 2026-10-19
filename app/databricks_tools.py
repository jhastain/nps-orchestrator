"""
Databricks NPS Query Tools

WHY: Provide direct access to NPS data from Databricks SQL Warehouse
WHAT: FunctionTools that wrap the DatabricksClient for use with Google ADK agents
"""

import json
from typing import Optional

from app.databricks_client import DatabricksClient, clamp_row_limit

# Initialize Databricks client (singleton pattern)
_databricks_client: Optional[DatabricksClient] = None

def get_databricks_client() -> DatabricksClient:
    """Get or create Databricks client singleton"""
    global _databricks_client
    if _databricks_client is None:
        _databricks_client = DatabricksClient(
            workspace_url="https://0000000000000000.1.gcp.databricks.com",
            warehouse_id="<warehouse-id>",
            project_id="genai-dev-project"
        )
    return _databricks_client


def query_nps_responses(
    start_date: str = None,
    end_date: str = None,
    score_filter: int = None,
    limit: int = 50
) -> str:
    """Query NPS survey responses with optional filters.
    
    Retrieves Net Promoter Score (NPS) survey responses from Databricks. 
    NPS scores range from 0-10 where:
    - 9-10 = Promoters (satisfied customers)
    - 7-8 = Passives (neutral customers) 
    - 0-6 = Detractors (unsatisfied customers)
    
    Use this to:
    - Find recent NPS responses to identify unhappy users
    - Filter by score to focus on detractors (0-6) or promoters (9-10)
    - Get responses within a specific date range
    - Analyze user feedback and comments
    
    Args:
        start_date: Start date filter in YYYY-MM-DD format (e.g., '2025-01-01')
            For relative dates like "last 30 days":
            1. Get today's date (e.g., 2026-01-12)
            2. Subtract the number of days (e.g., 30 days ago = 2025-12-13)
            3. Format as YYYY-MM-DD
            Example: "last 30 days" -> start_date='2025-12-13', end_date='2026-01-12'
        
        end_date: End date filter in YYYY-MM-DD format (e.g., '2026-01-12')
            Usually set to today's date for "last N days" queries
        
        score_filter: Filter by specific NPS score (0-10). 
            NOTE: This filters ONE score at a time. For detractors (scores 0-6),
            you need to call this function multiple times or filter the results.
            Common individual scores:
            - 0-6: Individual detractor scores (call once per score or filter results)
            - 7-8: Individual passive scores
            - 9-10: Individual promoter scores
        
        limit: Maximum number of responses to return (default: 50, max: 1000)
    
    Returns:
        JSON string containing NPS response records with fields like:
        - response_id: Unique identifier
        - nps: Original score (0-10) ← This is the score column
        - nps_value: Transformed score (typically -100, 0, or 100)
        - response_start_at: When the survey was submitted
        - login_id: User ID who submitted the response (can be None)
        - account_id: Associated account/organization
        - comment: User's feedback text
        - company: Company name (typically 'example-org')
        - user_role: User's role (can be None)
        - etl_updated_at: Last ETL update timestamp
    
    Examples:
        # Get responses from last 30 days (calculate dates first!)
        # If today is 2026-01-12, then 30 days ago is 2025-12-13
        query_nps_responses(start_date='2025-12-13', end_date='2026-01-12', limit=100)
        
        # Get detractor responses (score 0-6) from last week
        # Call multiple times for different scores, or get all and filter
        query_nps_responses(start_date='2026-01-05', end_date='2026-01-12', limit=100)
        # Then filter results where nps is between 0-6
        
        # Get specific score (e.g., score of 3)
        query_nps_responses(start_date='2025-01-01', score_filter=3, limit=10)
        
        # Get promoters only (scores 9-10)
        # Get score 9 and score 10 separately, then combine
        query_nps_responses(score_filter=9, limit=20)
        query_nps_responses(score_filter=10, limit=20)
    """
    try:
        client = get_databricks_client()
        results = client.query_nps_responses(
            start_date=start_date,
            end_date=end_date,
            score_filter=score_filter,
            limit=min(limit, 1000)  # Cap at 1000 for safety
        )
        
        return json.dumps({
            "success": True,
            "filters_applied": {
                "start_date": start_date,
                "end_date": end_date,
                "score_filter": score_filter,
                "limit": limit
            },
            "records_returned": len(results),
            "data": results
        }, indent=2, default=str)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }, indent=2)


def get_nps_summary(
    start_date: str = None,
    end_date: str = None
) -> str:
    """Get NPS summary statistics and calculated NPS score.
    
    Calculates Net Promoter Score (NPS) and provides summary statistics.
    NPS is calculated as: (% Promoters - % Detractors) * 100
    
    Score ranges:
    - NPS > 50: Excellent
    - NPS 30-50: Good
    - NPS 0-30: Needs improvement
    - NPS < 0: Critical - more detractors than promoters
    
    Use this to:
    - Get overall NPS health metrics
    - Compare NPS across time periods
    - Identify trends (improving vs declining)
    - Understand the distribution of responses
    
    Args:
        start_date: Start date filter in YYYY-MM-DD format (optional)
        end_date: End date filter in YYYY-MM-DD format (optional)
    
    Returns:
        JSON string containing:
        - nps_score: Calculated NPS (-100 to 100)
        - total_responses: Total number of survey responses
        - promoters: Count of scores 9-10
        - passives: Count of scores 7-8  
        - detractors: Count of scores 0-6
        - avg_score: Average score (0-10)
        - promoter_percentage: % of promoters
        - detractor_percentage: % of detractors
    
    Examples:
        # Get overall NPS for all time
        get_nps_summary()
        
        # Get NPS for January 2025
        get_nps_summary(start_date='2025-01-01', end_date='2025-01-31')
        
        # Get NPS for last 7 days
        get_nps_summary(start_date='2025-01-01')
    """
    try:
        client = get_databricks_client()
        summary = client.get_nps_summary(
            start_date=start_date,
            end_date=end_date
        )
        
        # Add percentage calculations
        total = summary["total_responses"]
        if total > 0:
            summary["promoter_percentage"] = round((summary["promoters"] / total) * 100, 2)
            summary["passive_percentage"] = round((summary["passives"] / total) * 100, 2)
            summary["detractor_percentage"] = round((summary["detractors"] / total) * 100, 2)
        else:
            summary["promoter_percentage"] = 0
            summary["passive_percentage"] = 0
            summary["detractor_percentage"] = 0
        
        # Add interpretation
        nps = summary["nps_score"]
        if nps > 50:
            interpretation = "Excellent - Strong customer satisfaction"
        elif nps >= 30:
            interpretation = "Good - Positive customer sentiment"
        elif nps >= 0:
            interpretation = "Needs Improvement - Neutral sentiment"
        else:
            interpretation = "Critical - More detractors than promoters"
        
        summary["interpretation"] = interpretation
        
        return json.dumps({
            "success": True,
            "date_range": {
                "start_date": start_date or "all time",
                "end_date": end_date or "present"
            },
            "nps_metrics": summary
        }, indent=2)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }, indent=2)


def query_nps_by_user(
    user_id: str = None,
    account_id: str = None,
    limit: int = 20
) -> str:
    """Query NPS responses for a specific user or account.
    
    Finds NPS survey responses associated with a specific user or account.
    This is essential for correlating user feedback with errors/performance issues.
    
    Use this to:
    - Find NPS feedback from users experiencing errors (from Sentry user.id)
    - Investigate account satisfaction issues
    - Correlate low NPS scores with technical problems
    - Understand specific user pain points
    
    Args:
        user_id: Sentry user.id or application user identifier
        account_id: Account/organization identifier
        limit: Maximum number of responses to return (default: 20)
    
    Returns:
        JSON string with NPS responses for the specified user/account
    
    Examples:
        # Find NPS responses from a user who experienced errors
        query_nps_by_user(user_id='12345')
        
        # Find responses from a specific account
        query_nps_by_user(account_id='67890')
        
        # Find both (returns matches for either)
        query_nps_by_user(user_id='12345', account_id='67890')
    """
    try:
        client = get_databricks_client()
        
        # Build SQL query with filters. Identifiers are bound as parameters, never
        # interpolated: these values originate from an LLM tool call, so they are
        # untrusted input.
        conditions = []
        params = {}
        if user_id:
            conditions.append("user_id = :user_id")
            params["user_id"] = user_id
        if account_id:
            conditions.append("account_id = :account_id")
            params["account_id"] = account_id

        if not conditions:
            return json.dumps({
                "success": False,
                "error": "Must provide user_id or account_id"
            }, indent=2)

        sql = f"""
        SELECT *
        FROM analytics.facts.fct_nps_responses
        WHERE {' OR '.join(conditions)}
        ORDER BY response_date DESC
        LIMIT {clamp_row_limit(limit)}
        """

        results = client.query(sql, parameters=params)
        
        return json.dumps({
            "success": True,
            "filters": {
                "user_id": user_id,
                "account_id": account_id
            },
            "records_returned": len(results),
            "data": results
        }, indent=2, default=str)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }, indent=2)
