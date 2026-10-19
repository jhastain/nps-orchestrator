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

"""
Agent Engine App Wrapper for NPS Orchestrator

WHY: Wraps the ADK app for deployment to Vertex AI Agent Engine
WHAT: Provides AdkApp wrapper that Agent Engine requires
"""

import logging
import os
import vertexai
from typing import Any
from google.cloud import logging as google_cloud_logging
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from vertexai.agent_engines.templates.adk import AdkApp

# Import the ADK app from agent_direct.py
from app.agent_direct import app as adk_app


class AgentEngineApp(AdkApp):
    """Agent Engine application wrapper for NPS Orchestrator"""
    
    def set_up(self) -> None:
        """Initialize the agent engine app with logging."""
        vertexai.init()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        logging_client = google_cloud_logging.Client()
        self.logger = logging_client.logger(__name__)
    
    def register_feedback(self, feedback: dict[str, Any]) -> None:
        """Collect and log feedback."""
        self.logger.log_struct(feedback, severity="INFO")
    
    def register_operations(self) -> dict[str, list[str]]:
        """Registers the operations of the Agent."""
        operations = super().register_operations()
        operations[""] = operations.get("", []) + ["register_feedback"]
        return operations


# Get logs bucket from environment (optional)
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

# Create the agent engine instance
agent_engine = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: GcsArtifactService(bucket_name=logs_bucket_name)
    if logs_bucket_name
    else InMemoryArtifactService(),
)
