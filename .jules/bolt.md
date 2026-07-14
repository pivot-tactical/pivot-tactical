## 2026-06-08 - [Filter Coefficient Caching]
**Learning:** `scipy.signal.butter` is called repeatedly per audio render to recalculate the same Butterworth filter coefficients. Since filter characteristic parameters rarely change given identical conditions, calculating them is an unnecessary bottleneck taking roughly 30% of audio DSP rendering time.
**Action:** Always wrap computationally expensive coefficient design functions like `signal.butter` with `functools.lru_cache` and ensure mutable arguments (like lists of frequencies) are transformed into hashable tuples beforehand.
## 2024-07-25 - React Component Re-render Performance
**Learning:** Instantiating `Intl.DateTimeFormat` inside a component render loop is extremely expensive (~0.14ms). In components that re-render frequently (like `SevenSegmentClock` which updates 4x per second via `setInterval`), this unnecessary instantiation burns main thread cycles continuously.
**Action:** Always memoize expensive API instantiations like `Intl.DateTimeFormat` or `Intl.NumberFormat` with `useMemo` when they depend on state that rarely changes (like a timezone string). This reduces the cost to virtually zero (~0.002ms) across re-renders.
## 2026-07-13 - [Intl.DateTimeFormat in loops]
**Learning:** Calling `new Date().toLocaleString` inside loops (like rendering a long list of session history items) is a hidden performance killer because it implicitly creates a new `Intl.DateTimeFormat` instance each time. Profiling shows it takes ~1.9s for 10k iterations vs ~38ms for 10k formats using a cached instance.
**Action:** Always extract and cache `Intl.DateTimeFormat` instances outside of mapping functions or loops when dealing with lists of data to be formatted, e.g. using a simple Map cache keyed by timezone.
