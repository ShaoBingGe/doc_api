"""End-to-end mock test using FastAPI TestClient (no server needed)."""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from starlette.testclient import TestClient
from app.main import app

client = TestClient(app)

print("=" * 60)
print("E2E MOCK TEST")
print("=" * 60)

# 1. Health check
print("\n--- Step 1: Health Check ---")
r = client.get("/health")
print(f"Status: {r.status_code}")
print(f"Body: {r.json()}")

# 2. Upload a test file with mock processor
print("\n--- Step 2: Upload Document (mock processor) ---")
test_file = None
test_dir = "/Users/kingdee/Documents/ApiAnything/testing/test1_honor/"
for f in os.listdir(test_dir):
    if f.endswith(".pdf"):
        test_file = os.path.join(test_dir, f)
        break
if not test_file:
    print("ERROR: No PDF found in test directory")
    sys.exit(1)

print(f"Using file: {test_file}")
with open(test_file, "rb") as f:
    r = client.post("/api/v1/documents/upload",
                     files={"file": (os.path.basename(test_file), f, "application/pdf")},
                     data={"processor_key": "mock"})
print(f"Status: {r.status_code}")
resp = r.json()
print(f"Document ID: {resp.get('id', 'N/A')}")
print(f"Status: {resp.get('status', 'N/A')}")
doc_id = resp.get("id")

if not doc_id:
    print("ERROR: No document ID returned")
    print(f"Full response: {resp}")
    sys.exit(1)

# 3. Get document detail
print("\n--- Step 3: Get Document Detail ---")
r = client.get(f"/api/v1/documents/{doc_id}")
print(f"Status: {r.status_code}")
detail = r.json()
print(f"Filename: {detail.get('filename', 'N/A')}")
print(f"Processing results count: {len(detail.get('processing_results', []))}")
if detail.get('processing_results'):
    pr = detail['processing_results'][0]
    print(f"  Version: {pr.get('version')}, Processor: {pr.get('processor_type')}")
    sd = pr.get('structured_data', [])
    print(f"  Structured data fields: {len(sd) if isinstance(sd, list) else 'dict'}")

# 4. List annotations
print("\n--- Step 4: List Annotations ---")
r = client.get(f"/api/v1/documents/{doc_id}/annotations")
print(f"Status: {r.status_code}")
ann_resp = r.json()
annotations = ann_resp.get("annotations", ann_resp.get("items", []))
if isinstance(ann_resp, list):
    annotations = ann_resp
print(f"Annotations count: {len(annotations)}")
if annotations and len(annotations) > 0:
    first = annotations[0] if isinstance(annotations, list) else annotations
    print(f"  First annotation: {first.get('field_name', 'N/A')} = {str(first.get('field_value', 'N/A'))[:50]}")

# 5. Create API Definition
print("\n--- Step 5: Create API Definition ---")
api_code = f"invoice-e2e-{int(time.time())}"
r = client.post("/api/v1/api-definitions",
                json={"document_id": doc_id, "name": "Invoice Extractor Test", "api_code": api_code, "processor_type": "mock"})
print(f"Status: {r.status_code}")
api_def = r.json()
print(f"API Definition ID: {api_def.get('id', 'N/A')}")
print(f"API Code: {api_def.get('api_code', 'N/A')}")
api_def_id = api_def.get("id")

if not api_def_id:
    print(f"Full response: {api_def}")
    sys.exit(1)

# 5b. Activate the API Definition
print("\n--- Step 5b: Activate API Definition ---")
r = client.patch(f"/api/v1/api-definitions/{api_def_id}/status", json={"action": "activate"})
print(f"Status: {r.status_code}")
print(f"New status: {r.json().get('status', r.json())}")

# 6. Create API Key
print("\n--- Step 6: Create API Key ---")
key_payload = {"name": "test-e2e-key"}
if api_def_id:
    key_payload["api_definition_id"] = api_def_id
r = client.post("/api/v1/api-keys", json=key_payload)
print(f"Status: {r.status_code}")
key_resp = r.json()
raw_key = key_resp.get("raw_key", key_resp.get("key", "N/A"))
print(f"Key prefix: {str(raw_key)[:10]}...")
print(f"Key name: {key_resp.get('name', 'N/A')}")

# 7. Call Extract endpoint
print("\n--- Step 7: Call Extract Endpoint ---")
with open(test_file, "rb") as f:
    r = client.post(f"/api/v1/extract/{api_code}",
                     headers={"X-API-Key": raw_key},
                     files={"file": (os.path.basename(test_file), f, "application/pdf")})
print(f"Status: {r.status_code}")
extract_resp = r.json()
if r.status_code == 200:
    print(f"Request ID: {extract_resp.get('request_id', 'N/A')}")
    data = extract_resp.get('data', extract_resp.get('structured_data', {}))
    print(f"Extracted fields: {len(data) if isinstance(data, (dict, list)) else 'N/A'}")
    print("SUCCESS: Full E2E flow completed!")
else:
    print(f"Response: {extract_resp}")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
