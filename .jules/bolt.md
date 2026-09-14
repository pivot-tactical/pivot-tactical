## 2026-06-08 - [Filter Coefficient Caching]
**Learning:** `scipy.signal.butter` is called repeatedly per audio render to recalculate the same Butterworth filter coefficients. Since filter characteristic parameters rarely change given identical conditions, calculating them is an unnecessary bottleneck taking roughly 30% of audio DSP rendering time.
**Action:** Always wrap computationally expensive coefficient design functions like `signal.butter` with `functools.lru_cache` and ensure mutable arguments (like lists of frequencies) are transformed into hashable tuples beforehand.
## 2024-07-25 - React Component Re-render Performance
**Learning:** Instantiating `Intl.DateTimeFormat` inside a component render loop is extremely expensive (~0.14ms). In components that re-render frequently (like `SevenSegmentClock` which updates 4x per second via `setInterval`), this unnecessary instantiation burns main thread cycles continuously.
**Action:** Always memoize expensive API instantiations like `Intl.DateTimeFormat` or `Intl.NumberFormat` with `useMemo` when they depend on state that rarely changes (like a timezone string). This reduces the cost to virtually zero (~0.002ms) across re-renders.
## 2026-07-13 - [Intl.DateTimeFormat in loops]
**Learning:** Calling `new Date().toLocaleString` inside loops (like rendering a long list of session history items) is a hidden performance killer because it implicitly creates a new `Intl.DateTimeFormat` instance each time. Profiling shows it takes ~1.9s for 10k iterations vs ~38ms for 10k formats using a cached instance.
**Action:** Always extract and cache `Intl.DateTimeFormat` instances outside of mapping functions or loops when dealing with lists of data to be formatted, e.g. using a simple Map cache keyed by timezone.

## 2024-08-03 - [Deferred Numpy Processing in High Frequency Accumulation]
**Learning:** When accumulating numpy arrays from high-frequency calls like `push_tx_audio`, intermediate casting, shaping, or `np.asarray` operations cause noticeable overhead due to extra allocations.
**Action:** Defer `astype` and `reshape(-1)` to the final `_collect_audio` concatenation step.

## 2024-05-24 - V8 Math Functions vs Inlining
**Learning:** When optimizing tight inner loops in JavaScript/V8 (e.g., PCM audio conversion running 16,000 times a second), calling standard Math functions like `Math.max` and `Math.min` per sample incurs noticeable overhead compared to manually inlining the min/max checks with conditionals or ternaries. Caching the array length in a local variable also provides a minor speedup over accessing `.length` on every iteration.
**Action:** Avoid nested math function calls in hot loops where simple manual clamping logic suffices.
## 2026-03-30 - Optimize file checksum hashing using hashlib.file_digest
**Learning:** Python 3.11+ `hashlib.file_digest(f, "sha256")` performs file reading and hash calculation directly in C-level code, eliminating Python interpreter loop overhead (`iter(lambda: f.read(...), b"")`) and intermediate buffer allocations.
**Action:** Use `hashlib.file_digest` for computing cryptographic digests directly from binary file streams.
## 2026-10-27 - [Division vs Multiplication in V8]
**Learning:** In JavaScript/V8 (e.g., tight audio processing loops), replacing division by a constant with multiplication by its reciprocal (e.g. `s / 0x8000` to `s * 0.000030517578125`) does not consistently improve performance and may actually degrade it slightly, as V8 is highly optimized for standard math operations.
**Action:** Focus micro-optimizations in JavaScript tight loops on avoiding nested function calls (like `Math.min`/`Math.max` or object creations) rather than attempting to outsmart V8's basic arithmetic handling, unless verified thoroughly by benchmarks.
