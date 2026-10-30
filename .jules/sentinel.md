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
## 2024-05-24 - [CRITICAL] Fix Path Traversal in Audio Playback
**Vulnerability:** The API endpoint returning audio playback dynamically joined a user-controlled path string from the database with a base directory without path sanitization, allowing arbitrary file retrieval.
**Learning:** Always sanitize `Path` constructs generated from dynamic or database-stored values using `.is_absolute()` and verifying traversal (`..`) attempts before trusting the `Path.resolve()` boundary check.
**Prevention:** Use defensive input validation enforcing `.is_relative_to(base_dir)` alongside absolute path and traversal character rejections.

## 2025-03-09 - [Path Traversal in RollbackRequest Schema]
**Vulnerability:** The `RollbackRequest` schema accepted a `tag` field without validating for path traversal characters (`..`, `/`, `\`). While the backend validation inside `UpdateManager` might eventually fail to find the directory, relying solely on downstream checks is weak. In earlier versions of `delete_retained`, `tag` was directly concatenated in `shutil.rmtree(self.app_dir(tag))`.
**Learning:** Pydantic models must validate all file path components derived from user input to prevent path traversal, even for endpoints considered lower risk or admin-only.
**Prevention:** Always use `@field_validator` on API schemas to sanitize input strings that will be interpolated into file or directory paths. Reject payloads containing `/`, `\`, and `..`.

## 2026-06-05 - [ZIP Export Directory Traversal Test Flaw & Arcname Sanitization]
**Vulnerability:** Flawed test assertion in path traversal test for session zip export compared `zf.namelist()` against `{session_id}/...` instead of the human-readable `{session_dir_name}/...` top folder name, masking potential exfiltration false passes.
**Learning:** Archive top-level folder naming conventions must match exact production path rendering in security tests to prevent false-negative test passes during security audits.
**Prevention:** Compute top-level archive path names dynamically using production helpers in test assertions, and use `PureWindowsPath(wav_path.name).name` on `arcname` to strip cross-platform directory components when packaging files into ZIP archives.

## 2026-10-17 - [Configurable Default Instructor Credentials]
**Vulnerability:** Hardcoded default instructor password (`"instructor"`) could lead to static default credentials in production deployments if unchanged.
**Learning:** Hardcoded default passwords in authentication services pose security risks if operators cannot configure initial credentials via environment variables during deployment.
**Prevention:** Always support environment variable overrides (`PIVOT_DEFAULT_INSTRUCTOR_PASSWORD` / `PIVOT_INSTRUCTOR_PASSWORD`) for default credentials seeded on first run.
