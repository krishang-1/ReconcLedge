"""Lists models actually accessible on this GROQ_API_KEY's account. Run this
if a model 404s as inaccessible - not every model listed in Groq's public
docs is available to every account/tier, so this checks reality directly
instead of guessing from documentation.

    python agent\\list_models.py
"""

import json
import os

import requests

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    raise SystemExit("GROQ_API_KEY not set in environment")

response = requests.get(
    "https://api.groq.com/openai/v1/models",
    headers={"Authorization": f"Bearer {api_key}"},
)
response.raise_for_status()

models = response.json()["data"]
for m in sorted(models, key=lambda x: x["id"]):
    print(m["id"])
print(f"\n{len(models)} models accessible on this account.")
