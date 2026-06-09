## 2025-06-09 - [SSRF Bypass in URL Validation]
**Vulnerability:** The GitHub URL validation in `server/pivot/api/schemas.py` checked if the parsed hostname ended with `.githubusercontent.com`. This is a classic SSRF bypass vector as it allows an attacker to use a malicious domain like `evil.githubusercontent.com`.
**Learning:** Never use a `.endswith` string check for domain validation in URL parsers. Subdomains of allowed top level domains can be registered by anyone (or bypasses using `@` might trick simple string parsers).
**Prevention:** Use a strict allowlist of exact domains (e.g., `hostname not in allowed_set`) rather than string suffix matching when validating URL safety boundaries.
