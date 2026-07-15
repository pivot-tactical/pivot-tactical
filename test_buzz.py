import numpy as np
import timeit

def slow(n, t, phases):
    buzz = np.zeros(n, dtype=np.float64)
    for k in range(3, 29, 2):
        buzz += (1.0 / k) * np.sin(2.0 * np.pi * 100.0 * k * t + phases[k])
    return buzz

def fast(n, t, phases):
    k = np.arange(3, 29, 2)[:, np.newaxis]
    buzz = np.sum((1.0 / k) * np.sin(2.0 * np.pi * 100.0 * k * t + phases[3:29:2, np.newaxis]), axis=0)
    return buzz

n = 960
t = np.arange(n) / 48000.0
phases = np.random.uniform(0, 2*np.pi, size=32)

slow_time = timeit.timeit(lambda: slow(n, t, phases), number=10000)
fast_time = timeit.timeit(lambda: fast(n, t, phases), number=10000)

print(f"Slow: {slow_time:.4f}s")
print(f"Fast: {fast_time:.4f}s")
print(f"Improvement: {slow_time / fast_time:.2f}x")
print("Equal:", np.allclose(slow(n, t, phases), fast(n, t, phases)))
