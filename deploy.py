#!/usr/bin/env python3
"""
Deploy NPS Orchestrator with bug fixes using client.agent_engines.create()

WHY: Original deployment method might handle module paths differently
WHAT: Uses vertexai.Client.agent_engines.create() instead of ReasoningEngine.create()
"""

import os
import sys
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent / "app"))

import vertexai
from vertexai.agent_engines import AdkApp
from dotenv import load_dotenv

# Load environment variables  
load_dotenv()

# Configuration
PROJECT_ID = "genai-dev-project"
LOCATION = "us-central1"

print("=" * 80)
print("DEPLOYING NPS ORCHESTRATOR (BUG FIXES) TO VERTEX AI AGENT ENGINE")
print("=" * 80)
print()
print("Bug Fixes Included:")
print("   - Fixed score filtering (nps vs nps_value column)")
print("   - Improved date handling documentation")
print()

# Set environment variables for deployment
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)

# Initialize Vertex AI Client
print("Initializing Vertex AI Client...")
print(f"   Project: {PROJECT_ID}")
print(f"   Location: {LOCATION}")
print()

client = vertexai.Client(project=PROJECT_ID, location=LOCATION)

# Import agent
print("Loading NPS Orchestrator agent...")
from app.agent_direct import root_agent, app

print(f"   Agent Name: {root_agent.name}")
print(f"   Tools: {len(root_agent.tools)} configured")
print()

# Wrap in AdkApp
print("Wrapping in AdkApp...")
wrapped_app = AdkApp(agent=root_agent)
print("   AdkApp wrapper created")
print()

# Requirements
requirements = [
    "google-cloud-aiplatform[agent_engines,adk]>=1.112",
    "google-adk>=1.18.0",
    "requests>=2.31.0",
    "python-dotenv>=1.0.0",
    "google-cloud-secret-manager>=2.16.0",
    "databricks-sql-connector",
    "cloudpickle>=2.0.0",  # Required for serialization
    "pydantic>=2.0.0",
]

# Environment variables
env_vars = {
    "GOOGLE_GENAI_USE_VERTEXAI": "1",
    # Never hardcode a token here - it is baked into the deployed engine's config.
    "SENTRY_AUTH_TOKEN": os.environ["SENTRY_AUTH_TOKEN"],
}

print("Starting deployment (this may take 2-5 minutes)...")
print()

try:
    remote_app = client.agent_engines.create(
        agent=wrapped_app,
        config={
            "requirements": requirements,
            "env_vars": env_vars,
            "display_name": "NPS Orchestrator",
            "staging_bucket": f"gs://{PROJECT_ID}-agent-staging",
            "extra_packages": ["./app"],  # Include app directory as a package
        },
    )
    
    print()
    print("=" * 80)
    print("DEPLOYMENT SUCCESSFUL!")
    print("=" * 80)
    
    # Extract resource name and ID from api_resource
    resource_name = None
    reasoning_engine_id = None
    
    if hasattr(remote_app, 'api_resource') and hasattr(remote_app.api_resource, 'name'):
        resource_name = remote_app.api_resource.name
        print(f"\nAgent Resource Name:")
        print(f"   {resource_name}")
        
        # Extract ID from resource name
        # Format: projects/000000000000/locations/us-central1/reasoningEngines/<reasoning-engine-id>
        parts = resource_name.split("/")
        if len(parts) > 0 and 'reasoningEngines' in resource_name:
            reasoning_engine_id = parts[-1]
    
    if reasoning_engine_id:
        print()
        print(f"✅ Reasoning Engine ID: {reasoning_engine_id}")
        print()
        print("Next Steps:")
        print("   1. Copy the Reasoning Engine ID above")
        print("   2. Attach to Gemini Enterprise app 'NPS_orchestrator'")
        print("   3. Test Sentry search with:")
        print("      - 'Find Sentry errors for account ID 12345'")
        print("      - 'Show errors for user john@example.com from last week'")
        print("      - 'Get production errors from the last 7 days'")
    else:
        print("\n⚠️  Could not extract Reasoning Engine ID")
        print("Checking api_resource...")
        if hasattr(remote_app, 'api_resource'):
            print(f"api_resource: {remote_app.api_resource}")
    
    print()
    print("=" * 80)

except Exception as e:
    print()
    print(f"❌ Deployment failed: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
