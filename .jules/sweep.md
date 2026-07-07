## 2024-07-07 - Python Tooling & Verification
**Learning:** Found an unused DSP function (`qrm_tones` in `server/pivot/dsp/noise.py`) via `vulture` static analysis, which was confirmed unused by a codebase-wide `grep`. Tests fully passed after removal without side effects.
**Action:** Use tools like `vulture` in the backend for hunting dead code, then rigorously verify with global regex search (`grep -rn "function_name" .`) before planning the deletion patch.
