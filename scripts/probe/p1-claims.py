import base64
import json
import os
import urllib.parse
import urllib.request

CLAIMS = (
    "sha",
    "workflow_sha",
    "job_workflow_sha",
    "ref",
    "ref_type",
    "event_name",
    "workflow_ref",
    "job_workflow_ref",
    "head_ref",
    "base_ref",
    "runner_environment",
    "repository_visibility",
    "repository",
    "repository_owner",
)

url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
parsed = urllib.parse.urlsplit(url)
if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".actions.githubusercontent.com"):
    raise SystemExit("unexpected token endpoint")
query = urllib.parse.urlencode({"audience": "wacwini:hemi"})
request = urllib.request.Request(
    f"{url}&{query}",
    headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
)
with urllib.request.urlopen(request, timeout=15) as response:
    token = json.load(response)["value"]
payload = token.split(".")[1]
payload += "=" * (-len(payload) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload))
del token
for name in CLAIMS:
    print(f"claim {name} = {claims.get(name)!r}")
print(f"context github.sha = {os.environ.get('CTX_SHA')!r}")
print(f"context pull_request.head.sha = {os.environ.get('CTX_HEAD_SHA')!r}")
print(f"context job.workflow_sha = {os.environ.get('CTX_JOB_WORKFLOW_SHA')!r}")
