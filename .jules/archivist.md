## 2025-03-09 - [--headless flag is deprecated]
**Learning:** The server is always headless by design now, and the `--headless` flag is ignored and suppressed in `server/pivot/__main__.py`. The `README.md` previously instructed users to run the server with `--headless` on headless servers, which is redundant and outdated.
**Action:** Removed the `--headless` flag instructions from `README.md`. Ensure that future documentation does not re-introduce the `--headless` flag.
