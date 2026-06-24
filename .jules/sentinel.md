## 2025-03-09 - [ApplyUpdateRequest Path Traversal]
**Vulnerability:** The `ApplyUpdateRequest` schema in `server/pivot/api/schemas.py` accepted `tag` and `asset_name` fields without validating for path traversal characters. These values are used to construct file paths for staging update downloads.
**Learning:** Pydantic models must validate all file path components derived from user input to prevent SSRF or Path Traversal, even if the user is considered an admin (defense in depth).
**Prevention:** Always use `@field_validator` on API schemas to sanitize input strings that will be interpolated into file or directory paths. Reject payloads containing `/`, `\`, and `..`.

## 2024-05-24 - [LocalStorage Token Persistence Vulnerability]
**Vulnerability:** The `instructorToken` was being stored in `localStorage` in `frontend/src/api.ts`.
**Learning:** Storing sensitive authentication tokens in `localStorage` makes them vulnerable to cross-site scripting (XSS) attacks, as they persist indefinitely across browser sessions and tabs until explicitly cleared.
**Prevention:** Sensitive session tokens should be bound to the immediate session context using `sessionStorage` (or better yet, HttpOnly cookies), ensuring the token is cleared when the tab or window is closed.
