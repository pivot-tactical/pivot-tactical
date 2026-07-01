## 2025-03-09 - [ApplyUpdateRequest Path Traversal]
**Vulnerability:** The `ApplyUpdateRequest` schema in `server/pivot/api/schemas.py` accepted `tag` and `asset_name` fields without validating for path traversal characters. These values are used to construct file paths for staging update downloads.
**Learning:** Pydantic models must validate all file path components derived from user input to prevent SSRF or Path Traversal, even if the user is considered an admin (defense in depth).
**Prevention:** Always use `@field_validator` on API schemas to sanitize input strings that will be interpolated into file or directory paths. Reject payloads containing `/`, `\`, and `..`.

## 2024-05-24 - [LocalStorage Token Persistence Vulnerability]
**Vulnerability:** The `instructorToken` was being stored in `localStorage` in `frontend/src/api.ts`.
**Learning:** Storing sensitive authentication tokens in `localStorage` makes them vulnerable to cross-site scripting (XSS) attacks, as they persist indefinitely across browser sessions and tabs until explicitly cleared.
**Prevention:** Sensitive session tokens should be bound to the immediate session context using `sessionStorage` (or better yet, HttpOnly cookies), ensuring the token is cleared when the tab or window is closed.

## 2024-05-27 - [Overly Permissive CORS Configuration]
**Vulnerability:** The FastAPI CORS configuration in `server/pivot/api/app.py` set `allow_methods=["*"]` and `allow_headers=["*"]`, allowing any HTTP method and any header from any matched origin.
**Learning:** Even if `allow_origins` or `allow_origin_regex` restricts requests to intended domains or LAN IPs, using wildcard (`*`) for methods or headers unnecessarily increases the attack surface. An attacker exploiting XSS on a permitted origin could leverage unexpected methods (like `PUT` or `PATCH`) or inject dangerous headers (like `X-HTTP-Method-Override`) if they were processed downstream.
**Prevention:** Always follow the principle of least privilege in CORS configurations. Explicitly list only the exact HTTP methods (e.g., `["GET", "POST", "DELETE", "OPTIONS"]`) and headers (e.g., `["Content-Type", "Authorization"]`) that the API actually requires and is designed to handle safely.
