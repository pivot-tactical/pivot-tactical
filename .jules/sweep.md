## 2024-07-07 - Python Tooling & Verification
**Learning:** Found an unused DSP function (`qrm_tones` in `server/pivot/dsp/noise.py`) via `vulture` static analysis, which was confirmed unused by a codebase-wide `grep`. Tests fully passed after removal without side effects.
**Action:** Use tools like `vulture` in the backend for hunting dead code, then rigorously verify with global regex search (`grep -rn "function_name" .`) before planning the deletion patch.

## Investigated: Empty-Menu Bug (64-bit handle)
**Learning:** The explicit definition of `HMENU = ctypes.c_uint64` in `server/pivot/win_tray.py` is not dead code or a generic mistake. It is a critical codebase-specific pattern designed to prevent pointer truncation bugs on 64-bit Windows platforms, which would otherwise result in an empty menu being rendered when passed to functions like `user32.AppendMenuW` and `user32.TrackPopupMenu`. It must be preserved exactly as `ctypes.c_uint64`.
**Action:** Do not attempt to refactor `HMENU` to `wintypes.HMENU` or `ctypes.c_void_p`, as `ctypes` type coercion will corrupt the high bits of the handle.
