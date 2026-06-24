## 2025-03-09 - [ApplyUpdateRequest Path Traversal]
**Vulnerability:** The `ApplyUpdateRequest` schema in `server/pivot/api/schemas.py` accepted `tag` and `asset_name` fields without validating for path traversal characters. These values are used to construct file paths for staging update downloads.
**Learning:** Pydantic models must validate all file path components derived from user input to prevent SSRF or Path Traversal, even if the user is considered an admin (defense in depth).
**Prevention:** Always use `@field_validator` on API schemas to sanitize input strings that will be interpolated into file or directory paths. Reject payloads containing `/`, `\`, and `..`.

## 2024-05-24 - [LocalStorage Token Persistence Vulnerability — resolved via HttpOnly cookies]
**Vulnerability:** The `instructorToken` was being stored in `localStorage` in `frontend/src/api.ts`.
**Learning:** Storing sensitive authentication tokens in `localStorage` or `sessionStorage` makes them vulnerable to XSS — JavaScript can read and exfiltrate the token at any time.
**Prevention:** Use HttpOnly cookies set by the server. The token never reaches JavaScript at all: the browser sends it automatically on every same-origin request (including audio/export GETs and the WebSocket handshake), and XSS cannot read it. Use `SameSite=Strict` to prevent CSRF. A non-HttpOnly `sessionStorage` flag can track login state locally without exposing the token value.
**Resolution:** Server sets `pivot_token; HttpOnly; SameSite=Strict` on login/refresh and clears it on logout. Frontend tracks a boolean login-state flag in sessionStorage only. The `Authorization: Bearer` header is kept as a fallback for non-browser API clients.
