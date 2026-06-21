## 2025-03-09 - [--headless flag is deprecated]
**Learning:** The server is always headless by design now, and the `--headless` flag is ignored and suppressed in `server/pivot/__main__.py`. The `README.md` previously instructed users to run the server with `--headless` on headless servers, which is redundant and outdated.
**Action:** Removed the `--headless` flag instructions from `README.md`. Ensure that future documentation does not re-introduce the `--headless` flag.
## 2025-03-09 - [NPM vs PNPM]
**Learning:** The user prompt states: "The user explicitly requires the use of `pnpm` for frontend package management and forbids the use of `npm` or `yarn`." However, github workflows, REBUILD-LGPL.md, and code comments still contain `npm` commands.
**Action:** Replace `npm` commands with `pnpm` equivalents where applicable in CI workflows and documentation.
