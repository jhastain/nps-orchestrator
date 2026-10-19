"""
Databricks SQL Warehouse REST API Client

This module provides a simple interface to query Databricks SQL Warehouse
using OAuth service principal authentication and GCP Secret Manager for credentials.

Usage:
    from databricks_client import DatabricksClient
    
    client = DatabricksClient(
        workspace_url="https://0000000000000000.1.gcp.databricks.com",
        warehouse_id="<warehouse-id>",
        project_id="genai-dev-project"
    )
    
    results = client.query("SELECT * FROM analytics.facts.fct_nps_responses LIMIT 10")
"""

import time
import requests
from typing import Dict, List, Any, Optional
from google.cloud import secretmanager


def clamp_row_limit(limit: Any, default: int = 100, ceiling: int = 1000) -> int:
    """Coerce a caller-supplied LIMIT to a bounded int.

    LIMIT cannot be a bound parameter in Databricks SQL, so it is the one value
    that has to be interpolated - which means it has to be validated here.
    """
    try:
        return max(1, min(int(limit), ceiling))
    except (TypeError, ValueError):
        return default


class DatabricksClient:
    """Client for querying Databricks SQL Warehouse via REST API"""
    
    def __init__(
        self,
        workspace_url: str,
        warehouse_id: str,
        project_id: str,
        client_id_secret_name: str = "databricks-client-id",
        client_secret_secret_name: str = "databricks-client-secret"
    ):
        """
        Initialize Databricks client
        
        Args:
            workspace_url: Databricks workspace URL (e.g., "https://xxx.gcp.databricks.com")
            warehouse_id: SQL Warehouse ID
            project_id: GCP project ID for Secret Manager
            client_id_secret_name: Name of the GCP secret containing OAuth client ID
            client_secret_secret_name: Name of the GCP secret containing OAuth client secret
        """
        self.workspace_url = workspace_url.rstrip('/')
        self.warehouse_id = warehouse_id
        self.project_id = project_id
        
        # Retrieve credentials from GCP Secret Manager
        self.client_id = self._get_secret(client_id_secret_name)
        self.client_secret = self._get_secret(client_secret_secret_name)
        
        # OAuth token (will be fetched on first use)
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[float] = None
        
    def _get_secret(self, secret_name: str) -> str:
        """
        Retrieve secret from GCP Secret Manager
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            Secret value as string (stripped of whitespace)
        """
        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{self.project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": secret_path})
        # Strip whitespace and newlines from secret value
        return response.payload.data.decode('UTF-8').strip()
    
    def _get_access_token(self) -> str:
        """
        Get OAuth access token (cached with refresh logic)
        
        Returns:
            Valid access token
        """
        # Check if we have a valid cached token
        if self._access_token and self._token_expiry and time.time() < self._token_expiry:
            return self._access_token
        
        # Request new token using OAuth 2.0 client credentials flow
        # Per Databricks documentation: https://docs.databricks.com/dev-tools/auth/oauth-m2m.html
        token_url = f"{self.workspace_url}/oidc/v1/token"
        
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "scope": "all-apis"
            },
            auth=(self.client_id, self.client_secret),
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=30
        )
        
        # If token request fails, provide detailed error
        if response.status_code != 200:
            error_detail = f"OAuth token request failed: {response.status_code}"
            try:
                error_json = response.json()
                error_detail += f"\nError: {error_json}"
            except:
                error_detail += f"\nResponse: {response.text[:500]}"
            raise RuntimeError(error_detail)
        
        response.raise_for_status()
        token_data = response.json()
        
        self._access_token = token_data["access_token"]
        # Set expiry to 5 minutes before actual expiry for safety
        expires_in = token_data.get("expires_in", 3600)
        self._token_expiry = time.time() + expires_in - 300
        
        return self._access_token
    
    def query(
        self,
        sql: str,
        timeout_seconds: int = 300,
        row_limit: Optional[int] = None,
        parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute SQL query on Databricks SQL Warehouse

        Args:
            sql: SQL query to execute. Use named markers (:name) for any value that
                 did not originate in this file - never f-string user input into SQL.
            timeout_seconds: Maximum time to wait for query completion (default: 300s)
            row_limit: Optional limit on number of rows to return
            parameters: Values for the named markers in `sql`, as {name: value}.
                        Sent via the statement API's `parameters` field so the
                        warehouse binds them instead of parsing them as SQL.
            
        Returns:
            List of dictionaries representing query results
            
        Raises:
            requests.HTTPError: If API request fails
            TimeoutError: If query exceeds timeout
        """
        token = self._get_access_token()
        
        # Step 1: Submit query
        submit_url = f"{self.workspace_url}/api/2.0/sql/statements"
        
        payload = {
            "warehouse_id": self.warehouse_id,
            "statement": sql,
            # Databricks API requires wait_timeout between 5s-50s or "0s"
            # We'll use 50s for initial wait, then poll
            "wait_timeout": "50s"
        }
        
        if row_limit:
            payload["row_limit"] = row_limit

        if parameters:
            payload["parameters"] = [
                {"name": name, "value": None if value is None else str(value)}
                for name, value in parameters.items()
            ]
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(submit_url, json=payload, headers=headers)
        
        # If request fails, provide detailed error
        if response.status_code != 200:
            error_detail = f"SQL statement submission failed: {response.status_code}"
            try:
                error_json = response.json()
                error_detail += f"\nError: {error_json}"
            except:
                error_detail += f"\nResponse: {response.text[:500]}"
            raise RuntimeError(error_detail)
        
        response.raise_for_status()
        
        result = response.json()
        statement_id = result["statement_id"]
        status = result["status"]["state"]
        
        # Step 2: Poll for completion if needed
        start_time = time.time()
        
        while status in ["PENDING", "RUNNING"]:
            if time.time() - start_time > timeout_seconds:
                raise TimeoutError(f"Query exceeded timeout of {timeout_seconds} seconds")
            
            time.sleep(1)  # Poll every second
            
            # Get statement status
            status_url = f"{self.workspace_url}/api/2.0/sql/statements/{statement_id}"
            response = requests.get(status_url, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            status = result["status"]["state"]
        
        # Step 3: Check for errors
        if status == "FAILED":
            error_message = result["status"].get("error", {}).get("message", "Unknown error")
            raise RuntimeError(f"Query failed: {error_message}")
        
        if status == "CANCELED":
            raise RuntimeError("Query was canceled")
        
        # Step 4: Extract results
        if status != "SUCCEEDED":
            raise RuntimeError(f"Unexpected query status: {status}")
        
        # Parse results
        manifest = result.get("manifest", {})
        schema = manifest.get("schema", {}).get("columns", [])
        column_names = [col["name"] for col in schema]
        
        # Get data chunks
        chunks = result.get("result", {}).get("data_array", [])
        
        # Convert to list of dictionaries
        rows = []
        for chunk in chunks:
            row_dict = dict(zip(column_names, chunk))
            rows.append(row_dict)
        
        return rows
    
    def query_nps_responses(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        score_filter: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query NPS responses with optional filters
        
        Args:
            start_date: Start date filter (YYYY-MM-DD format)
            end_date: End date filter (YYYY-MM-DD format)
            score_filter: Filter by specific NPS score (0-10)
            limit: Maximum number of results
            
        Returns:
            List of NPS response records
        """
        sql = "SELECT * FROM analytics.facts.fct_nps_responses"

        conditions = []
        params: Dict[str, Any] = {}
        if start_date:
            conditions.append("response_start_at >= :start_date")
            params["start_date"] = start_date
        if end_date:
            conditions.append("response_start_at <= :end_date")
            params["end_date"] = end_date
        if score_filter is not None:
            # Filter on 'nps' column (0-10 scores), not 'nps_value' (-100/0/100)
            conditions.append("nps = :score_filter")
            params["score_filter"] = score_filter

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        # LIMIT cannot be parameterized, so coerce it to a bounded int instead.
        sql += f" ORDER BY response_start_at DESC LIMIT {clamp_row_limit(limit)}"

        return self.query(sql, parameters=params)
    
    def get_nps_summary(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get NPS summary statistics
        
        Args:
            start_date: Start date filter (YYYY-MM-DD format)
            end_date: End date filter (YYYY-MM-DD format)
            
        Returns:
            Dictionary with NPS metrics (promoters, passives, detractors, score)
        """
        sql = """
        SELECT 
            COUNT(*) as total_responses,
            SUM(CASE WHEN nps_value >= 9 THEN 1 ELSE 0 END) as promoters,
            SUM(CASE WHEN nps_value >= 7 AND nps_value <= 8 THEN 1 ELSE 0 END) as passives,
            SUM(CASE WHEN nps_value <= 6 THEN 1 ELSE 0 END) as detractors,
            AVG(nps_value) as avg_score
        FROM analytics.facts.fct_nps_responses
        """
        
        conditions = []
        params: Dict[str, Any] = {}
        if start_date:
            conditions.append("response_start_at >= :start_date")
            params["start_date"] = start_date
        if end_date:
            conditions.append("response_start_at <= :end_date")
            params["end_date"] = end_date

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        results = self.query(sql, parameters=params)
        
        if not results:
            return {
                "total_responses": 0,
                "promoters": 0,
                "passives": 0,
                "detractors": 0,
                "nps_score": 0,
                "avg_score": 0
            }
        
        data = results[0]
        # Convert string values to integers/floats
        total = int(data["total_responses"])
        promoters = int(data["promoters"])
        detractors = int(data["detractors"])
        passives = int(data["passives"])
        avg_score = float(data["avg_score"]) if data["avg_score"] is not None else 0.0
        
        # Calculate NPS score: (% promoters - % detractors)
        nps_score = 0
        if total > 0:
            nps_score = ((promoters - detractors) / total) * 100
        
        return {
            "total_responses": total,
            "promoters": promoters,
            "passives": passives,
            "detractors": detractors,
            "nps_score": round(nps_score, 2),
            "avg_score": round(avg_score, 2)
        }


# Example usage
if __name__ == "__main__":
    # Initialize client
    client = DatabricksClient(
        workspace_url="https://0000000000000000.1.gcp.databricks.com",
        warehouse_id="<warehouse-id>",
        project_id="genai-dev-project"
    )
    
    # Test query
    print("Testing Databricks connection...")
    results = client.query("SELECT COUNT(*) as record_count FROM analytics.facts.fct_nps_responses")
    print(f"Total NPS records: {results[0]['record_count']}")
    
    # Get NPS summary
    print("\nNPS Summary:")
    summary = client.get_nps_summary()
    print(f"Total Responses: {summary['total_responses']}")
    print(f"NPS Score: {summary['nps_score']}")
    print(f"Promoters: {summary['promoters']}")
    print(f"Passives: {summary['passives']}")
    print(f"Detractors: {summary['detractors']}")
    print(f"Average Score: {summary['avg_score']}")
