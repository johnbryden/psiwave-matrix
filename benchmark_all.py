#!/usr/bin/env python3

import time
import math
import numpy as np
from statistics import mean, median

def benchmark_pure_python():
    """Benchmark pure Python operations"""
    print("🐌 Pure Python (Baseline)")
    
    # Test data
    current_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
    new_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
    
    def pure_pixel_blend(current_color, c1, c2, c3):
        blended_r = max(current_color[0], c1)
        blended_g = max(current_color[1], c2)
        blended_b = max(current_color[2], c3)
        return blended_r, blended_g, blended_b
    
    def pure_color_dim(color, dim_factor):
        return int(color[0] * dim_factor), int(color[1] * dim_factor), int(color[2] * dim_factor)
    
    def pure_sine_calc(x, amplitude, frequency, phase, vertical_offset):
        return amplitude * math.sin(frequency * x + phase) + vertical_offset
    
    # Benchmark pixel blending
    times = []
    for _ in range(10):
        start = time.time()
        for i in range(1000):
            pure_pixel_blend(current_colors[i], new_colors[i, 0], new_colors[i, 1], new_colors[i, 2])
        times.append(time.time() - start)
    
    blend_time = mean(times)
    print(f"   Pixel blending: {blend_time:.6f}s (avg of 10 runs)")
    
    # Benchmark color dimming
    times = []
    for _ in range(10):
        start = time.time()
        for i in range(1000):
            pure_color_dim(current_colors[i], 0.6)
        times.append(time.time() - start)
    
    dim_time = mean(times)
    print(f"   Color dimming:  {dim_time:.6f}s (avg of 10 runs)")
    
    # Benchmark sine calculation
    times = []
    for _ in range(10):
        start = time.time()
        for i in range(1000):
            pure_sine_calc(i * 0.1, 9, 0.2, i * 0.01, 20)
        times.append(time.time() - start)
    
    sine_time = mean(times)
    print(f"   Sine calculation: {sine_time:.6f}s (avg of 10 runs)")
    
    total_time = blend_time + dim_time + sine_time
    print(f"   Total time: {total_time:.6f}s")
    
    return total_time

def benchmark_numba():
    """Benchmark Numba JIT compilation"""
    try:
        from numba import njit
        print("🚀 Numba JIT Compilation")
        
        # Test data
        current_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
        new_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
        
        @njit(cache=True)
        def numba_pixel_blend(current_color, c1, c2, c3):
            blended_r = max(current_color[0], c1)
            blended_g = max(current_color[1], c2)
            blended_b = max(current_color[2], c3)
            return blended_r, blended_g, blended_b
        
        @njit(cache=True)
        def numba_color_dim(color, dim_factor):
            return int(color[0] * dim_factor), int(color[1] * dim_factor), int(color[2] * dim_factor)
        
        @njit(cache=True)
        def numba_sine_calc(x, amplitude, frequency, phase, vertical_offset):
            return amplitude * math.sin(frequency * x + phase) + vertical_offset
        
        # Warm up JIT
        for _ in range(100):
            numba_pixel_blend(current_colors[0], new_colors[0, 0], new_colors[0, 1], new_colors[0, 2])
            numba_color_dim(current_colors[0], 0.6)
            numba_sine_calc(0, 9, 0.2, 0, 20)
        
        # Benchmark pixel blending
        times = []
        for _ in range(10):
            start = time.time()
            for i in range(1000):
                numba_pixel_blend(current_colors[i], new_colors[i, 0], new_colors[i, 1], new_colors[i, 2])
            times.append(time.time() - start)
        
        blend_time = mean(times)
        print(f"   Pixel blending: {blend_time:.6f}s (avg of 10 runs)")
        
        # Benchmark color dimming
        times = []
        for _ in range(10):
            start = time.time()
            for i in range(1000):
                numba_color_dim(current_colors[i], 0.6)
            times.append(time.time() - start)
        
        dim_time = mean(times)
        print(f"   Color dimming:  {dim_time:.6f}s (avg of 10 runs)")
        
        # Benchmark sine calculation
        times = []
        for _ in range(10):
            start = time.time()
            for i in range(1000):
                numba_sine_calc(i * 0.1, 9, 0.2, i * 0.01, 20)
            times.append(time.time() - start)
        
        sine_time = mean(times)
        print(f"   Sine calculation: {sine_time:.6f}s (avg of 10 runs)")
        
        total_time = blend_time + dim_time + sine_time
        print(f"   Total time: {total_time:.6f}s")
        
        return total_time
        
    except ImportError:
        print("⚠️  Numba not available")
        return None

def benchmark_cython():
    """Benchmark Cython extensions"""
    try:
        import cython_optimized
        print("🚀 Cython Extensions")
        
        # Test data
        current_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
        new_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
        
        # Benchmark pixel blending
        times = []
        for _ in range(10):
            start = time.time()
            for i in range(1000):
                cython_optimized.fast_pixel_blend_cython(current_colors[i], new_colors[i, 0], new_colors[i, 1], new_colors[i, 2])
            times.append(time.time() - start)
        
        blend_time = mean(times)
        print(f"   Pixel blending: {blend_time:.6f}s (avg of 10 runs)")
        
        # Benchmark color dimming
        times = []
        for _ in range(10):
            start = time.time()
            for i in range(1000):
                cython_optimized.fast_color_dim_cython(current_colors[i], 0.6)
            times.append(time.time() - start)
        
        dim_time = mean(times)
        print(f"   Color dimming:  {dim_time:.6f}s (avg of 10 runs)")
        
        # Benchmark sine calculation
        times = []
        for _ in range(10):
            start = time.time()
            for i in range(1000):
                cython_optimized.fast_sine_calc_cython(i * 0.1, 9, 0.2, i * 0.01, 20)
            times.append(time.time() - start)
        
        sine_time = mean(times)
        print(f"   Sine calculation: {sine_time:.6f}s (avg of 10 runs)")
        
        total_time = blend_time + dim_time + sine_time
        print(f"   Total time: {total_time:.6f}s")
        
        return total_time
        
    except ImportError:
        print("⚠️  Cython extensions not available")
        return None

def main():
    print("🎯 Matrix Play - Performance Benchmark")
    print("=" * 50)
    
    results = {}
    
    # Run benchmarks
    results['Pure Python'] = benchmark_pure_python()
    print()
    
    results['Numba'] = benchmark_numba()
    print()
    
    results['Cython'] = benchmark_cython()
    print()
    
    # Calculate speedups
    print("🏆 Performance Results")
    print("=" * 50)
    
    baseline = results['Pure Python']
    
    for method, time_taken in results.items():
        if time_taken is not None:
            speedup = baseline / time_taken
            print(f"{method:15} {time_taken:.6f}s  ({speedup:.2f}x faster)")
        else:
            print(f"{method:15} Not available")
    
    print()
    print("💡 Recommendations:")
    print("   • Use Cython extensions for maximum performance")
    print("   • Use Numba JIT for good performance with minimal setup")
    print("   • Pure Python is always available as fallback")
    print()
    print("🚀 To get maximum performance, run:")
    print("   ./compile_all.sh")

if __name__ == "__main__":
    main()
