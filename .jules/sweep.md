## 2024-07-07 - Python Tooling & Verification
**Learning:** Found an unused DSP function (`qrm_tones` in `server/pivot/dsp/noise.py`) via `vulture` static analysis, which was confirmed unused by a codebase-wide `grep`. Tests fully passed after removal without side effects.
**Action:** Use tools like `vulture` in the backend for hunting dead code, then rigorously verify with global regex search (`grep -rn "function_name" .`) before planning the deletion patch.

## Win32 handles through ctypes (`server/pivot/win_tray.py`)
**Learning:** ctypes defaults every function's `restype`/`argtypes` to C `int` (32 bit). On 64-bit Windows a handle passed or returned that way is truncated, which is what rendered the tray menu as an empty white rectangle — `AppendMenuW`/`TrackPopupMenu` received an HMENU with garbage high bits. Declaring explicit prototypes fixes it; the handle type only has to be *pointer-sized*. `ctypes.c_uint64` (what this codebase uses) and pointer types like `ctypes.c_void_p` / `wintypes.HMENU` are all pointer-sized on x64 and none of them truncate.
**Action:** Never let a handle-carrying call fall back to ctypes' default `int`. Declare the prototype.

**Consequence worth knowing:** the two families differ in what they accept for a NULL handle. An *integer* argtype such as `c_uint64` rejects `None` outright — `argument N: TypeError: wrong type` — so a NULL handle must be written as `0`. Pointer types accept `None`. Passing `None` for `CreateWindowExW`'s `hMenu` against the `c_uint64` argtype is exactly what killed startup in tray mode; the fix was to pass `0` (see `fix(win-tray): pass 0, not None, for CreateWindowExW's hMenu`). Match the literal to the argtype you declared.
