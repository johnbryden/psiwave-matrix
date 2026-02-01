#!/usr/bin/env python3

import time
import numpy as np

# Test the compiled vs interpreted performance
def test_pixel_blend_performance():
    """Test the performance difference between compiled and interpreted pixel blending"""
    
    # Create test data
    current_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
    new_colors = np.random.randint(0, 256, (1000, 3), dtype=np.uint8)
    
    # Test interpreted version (fallback)
    def interpreted_blend(current_color, c1, c2, c3):
        blended_r = max(current_color[0], c1)
        blended_g = max(current_color[1], c2)
        blended_b = max(current_color[2], c3)
        return blended_r, blended_g, blended_b
    
    # Test compiled version if available
    try:
        from numba import njit
        @njit(cache=True)
        def compiled_blend(current_color, c1, c2, c3):
            blended_r = max(current_color[0], c1)
            blended_g = max(current_color[1], c2)
            blended_b = max(current_color[2], c3)
            return blended_r, blended_g, blended_b
        
        # Warm up JIT
        for _ in range(100):
            compiled_blend(current_colors[0], new_colors[0, 0], new_colors[0, 1], new_colors[0, 2])
        
        # Test compiled performance
        start_time = time.time()
        for i in range(1000):
            compiled_blend(current_colors[i], new_colors[i, 0], new_colors[i, 1], new_colors[i, 2])
        compiled_time = time.time() - start_time
        
        print(f"Compiled version: {compiled_time:.6f} seconds")
        
    except ImportError:
        print("Numba not available - skipping compiled test")
        compiled_time = None
    
    # Test interpreted performance
    start_time = time.time()
    for i in range(1000):
        interpreted_blend(current_colors[i], new_colors[i, 0], new_colors[i, 1], new_colors[i, 2])
    interpreted_time = time.time() - start_time
    
    print(f"Interpreted version: {interpreted_time:.6f} seconds")
    
    if compiled_time:
        speedup = interpreted_time / compiled_time
        print(f"Speedup: {speedup:.2f}x faster with compilation!")

if __name__ == "__main__":
    print("Testing pixel blending performance...")
    test_pixel_blend_performance()
