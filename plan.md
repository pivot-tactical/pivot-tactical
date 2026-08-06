1. **Remove token from query parameters in API dependencies**
   - In `server/pivot/api/deps.py`, edit `_extract_token` to only check the `Authorization` header and the `pivot_token` cookie, removing `request.query_params.get("token")`.
2. **Remove token from query parameters in WebSocket handler**
   - In `server/pivot/api/ws.py`, remove `or ws.query_params.get("token")` when resolving the token.
3. **Update tests to use cookies instead of query parameters for tokens**
   - In `server/tests/test_api.py`, change instances of `client.websocket_connect(f"/ws?token={token}")` to `client.websocket_connect("/ws", cookies={"pivot_token": token})`.
4. Complete pre commit steps
   - Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done.
5. Submit the change
   - Submit the change with branch name, commit message, and description using the `submit` tool.
