#!/bin/bash

echo "🚀 Matrix Play - Multi-Compilation Performance Setup"
echo "=================================================="

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
echo "Python version: $PYTHON_VERSION"

# 1. Enable Python's built-in optimizations
echo ""
echo "1️⃣  Enabling Python optimizations..."
export PYTHONOPTIMIZE=2  # Remove assert statements and __debug__ code
export PYTHONDONTWRITEBYTECODE=0  # Ensure .pyc files are created

# 2. Install/upgrade pip and build tools
echo ""
echo "2️⃣  Installing build dependencies..."
python3 -m pip install --upgrade pip setuptools wheel

# 3. Install Numba for JIT compilation
echo ""
echo "3️⃣  Installing Numba for JIT compilation..."
python3 -m pip install numba>=0.56.0

# 4. Install Cython for C-level compilation
echo ""
echo "4️⃣  Installing Cython for C-level compilation..."
python3 -m pip install cython>=0.29.0

# 5. Compile Cython extensions
echo ""
echo "5️⃣  Compiling Cython extensions..."
python3 setup.py build_ext --inplace

# 6. Create optimized Python bytecode
echo ""
echo "6️⃣  Creating optimized Python bytecode..."
python3 -m py_compile sinwave.py
python3 -m py_compile performance_test.py

# 7. Test compilation results
echo ""
echo "7️⃣  Testing compilation results..."
if python3 -c "import numba; print('✅ Numba:', numba.__version__)" 2>/dev/null; then
    echo "   Numba JIT compilation: READY"
else
    echo "   Numba JIT compilation: FAILED"
fi

if python3 -c "import cython_optimized; print('✅ Cython extensions: READY')" 2>/dev/null; then
    echo "   Cython extensions: READY"
else
    echo "   Cython extensions: FAILED"
fi

# 8. Performance comparison
echo ""
echo "8️⃣  Running performance comparison..."
python3 performance_test.py

echo ""
echo "🎯 Compilation complete! Your matrix animations should now run much faster."
echo ""
echo "Performance tiers (from fastest to slowest):"
echo "   🥇 Cython extensions (C-level compiled)"
echo "   🥈 Numba JIT (machine code compiled)"
echo "   🥉 Python .pyc (bytecode compiled)"
echo "   🐌 Pure Python (interpreted)"
echo ""
echo "To run with maximum performance:"
echo "   python3 -O sinwave.py"
