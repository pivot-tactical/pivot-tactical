from fastapi.testclient import TestClient

from pivot.api.app import app

client = TestClient(app)
try:
    print(
        client.post(
            "/api/admin/updates/apply",
            json={
                "tag": "v1.0",
                "asset_url": "file:///etc/passwd",
                "sha256_url": "http://evil.com/hash",
                "sig_url": "http://evil.com/sig",
                "asset_name": "update.bin",
            },
        ).json()
    )
except Exception as e:
    print("Error:", e)
