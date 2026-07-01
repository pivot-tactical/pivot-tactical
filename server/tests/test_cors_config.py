from fastapi.testclient import TestClient

from pivot.api.app import create_app


def test_cors_methods_and_headers():
    app = create_app()
    client = TestClient(app)

    # 1. Test allowed preflight request
    headers = {
        "Origin": "http://192.168.1.100:8080",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Authorization",
    }
    response = client.options("/api/status", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://192.168.1.100:8080"
    assert "POST" in response.headers.get("access-control-allow-methods", "")
    assert "Authorization" in response.headers.get("access-control-allow-headers", "")

    # 2. Test disallowed method (PUT)
    headers["Access-Control-Request-Method"] = "PUT"
    response = client.options("/api/status", headers=headers)
    assert response.status_code == 400

    # 3. Test disallowed header (X-Custom-Admin)
    headers["Access-Control-Request-Method"] = "POST"
    headers["Access-Control-Request-Headers"] = "X-Custom-Admin"
    response = client.options("/api/status", headers=headers)
    assert response.status_code == 400
