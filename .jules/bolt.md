## 2026-06-08 - [Filter Coefficient Caching]
**Learning:** `scipy.signal.butter` is called repeatedly per audio render to recalculate the same Butterworth filter coefficients. Since filter characteristic parameters rarely change given identical conditions, calculating them is an unnecessary bottleneck taking roughly 30% of audio DSP rendering time.
**Action:** Always wrap computationally expensive coefficient design functions like `signal.butter` with `functools.lru_cache` and ensure mutable arguments (like lists of frequencies) are transformed into hashable tuples beforehand.
