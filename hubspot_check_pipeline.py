"""
hubspot_check_pipeline.py  -  PourCastAI (Step 3, part 1)

Run this to confirm your HubSpot token works and to print out the pipeline +
stage IDs the seeding script will need. Uses the same .env-loading pattern
as risk_tools.py, so nothing new to set up.

Run:  python hubspot_check_pipeline.py
"""
import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")

if not TOKEN:
    print("HUBSPOT_ACCESS_TOKEN not found in .env")
    print("Open your .env file and make sure this line is in it:")
    print("HUBSPOT_ACCESS_TOKEN=your-token-here")
    raise SystemExit(1)

r = requests.get(
    "https://api.hubapi.com/crm/v3/pipelines/deals",
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=10,
)

if r.status_code != 200:
    print(f"HubSpot returned {r.status_code}: {r.text[:300]}")
    print("Most likely cause: token is wrong, or the private app is missing")
    print("the crm.schemas.deals / crm.objects.deals scopes.")
    raise SystemExit(1)

data = r.json()
print("Connected. Pipeline(s) found:\n")
for pipeline in data.get("results", []):
    print(f"Pipeline: {pipeline['label']}  (id: {pipeline['id']})")
    for stage in pipeline.get("stages", []):
        print(f"    stage '{stage['label']}'  ->  id: {stage['id']}")
    print()

print("Copy the pipeline id and the 4 stage ids above -- paste them back")
print("so the seeding script can be built against your exact stage IDs.")
