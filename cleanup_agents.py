"""
Cleanup old NPS Orchestrator agents, keeping only the most recent one.
"""
import os
from google.cloud import aiplatform
from datetime import datetime

# Initialize
project = "genai-dev-project"
location = "us-central1"

print("=" * 80)
print("CLEANUP OLD NPS ORCHESTRATOR AGENTS")
print("=" * 80)

# Initialize AI Platform
aiplatform.init(project=project, location=location)

print(f"\nProject: {project}")
print(f"Location: {location}")

# List all reasoning engines
print("\n" + "=" * 80)
print("LISTING ALL REASONING ENGINES")
print("=" * 80)

from vertexai.preview import reasoning_engines

all_engines = reasoning_engines.ReasoningEngine.list()

# Filter for NPS Orchestrator agents
nps_agents = []
for engine in all_engines:
    display_name = engine.display_name if hasattr(engine, 'display_name') else "Unknown"
    # Check if it's an NPS agent (by name or by checking resource name)
    if "nps" in display_name.lower() or "orchestrator" in display_name.lower():
        nps_agents.append(engine)
        
print(f"\nFound {len(nps_agents)} NPS Orchestrator agents")

if len(nps_agents) == 0:
    print("\nNo NPS agents found. Nothing to clean up.")
    exit(0)

# Sort by creation time (most recent first)
# The resource name contains the ID which is timestamp-based
nps_agents_sorted = sorted(nps_agents, key=lambda x: x.resource_name, reverse=True)

print("\n" + "=" * 80)
print("NPS ORCHESTRATOR AGENTS (sorted by age, newest first)")
print("=" * 80)

for i, engine in enumerate(nps_agents_sorted, 1):
    resource_name = engine.resource_name
    display_name = engine.display_name if hasattr(engine, 'display_name') else "Unknown"
    # Extract ID from resource name
    engine_id = resource_name.split("/")[-1]
    
    status = "✅ KEEP (Latest)" if i == 1 else f"❌ DELETE (Old #{i-1})"
    print(f"\n{status}")
    print(f"   Display Name: {display_name}")
    print(f"   Resource ID: {engine_id}")
    print(f"   Full Path: {resource_name}")

if len(nps_agents_sorted) == 1:
    print("\n" + "=" * 80)
    print("✅ Only one NPS agent exists. Nothing to delete.")
    print("=" * 80)
    exit(0)

# Ask for confirmation
print("\n" + "=" * 80)
print(f"READY TO DELETE {len(nps_agents_sorted) - 1} OLD AGENTS")
print("=" * 80)
print(f"\nKeeping: {nps_agents_sorted[0].resource_name.split('/')[-1]} (latest)")
print(f"Deleting: {len(nps_agents_sorted) - 1} older agents")

response = input("\nProceed with deletion? (yes/no): ").strip().lower()

if response != "yes":
    print("\n❌ Deletion cancelled.")
    exit(0)

# Delete old agents
print("\n" + "=" * 80)
print("DELETING OLD AGENTS")
print("=" * 80)

deleted_count = 0
for engine in nps_agents_sorted[1:]:  # Skip the first (newest) one
    engine_id = engine.resource_name.split("/")[-1]
    print(f"\n🗑️  Deleting agent {engine_id}...")
    try:
        engine.delete()
        print(f"   ✅ Deleted successfully")
        deleted_count += 1
    except Exception as e:
        print(f"   ❌ Error: {e}")

print("\n" + "=" * 80)
print("CLEANUP COMPLETE")
print("=" * 80)
print(f"\n✅ Kept: 1 agent (latest)")
print(f"🗑️  Deleted: {deleted_count} old agents")
print(f"\nRemaining agent ID: {nps_agents_sorted[0].resource_name.split('/')[-1]}")
