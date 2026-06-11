## 2025-03-09 - [ApplyUpdateRequest Path Traversal]
**Vulnerability:** The `ApplyUpdateRequest` schema in `server/pivot/api/schemas.py` accepted `tag` and `asset_name` fields without validating for path traversal characters. These values are used to construct file paths for staging update downloads.
**Learning:** Pydantic models must validate all file path components derived from user input to prevent SSRF or Path Traversal, even if the user is considered an admin (defense in depth).
**Prevention:** Always use `@field_validator` on API schemas to sanitize input strings that will be interpolated into file or directory paths. Reject payloads containing `/`, `\`, and `..`.
