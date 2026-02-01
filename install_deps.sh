#!/bin/bash

echo "Installing dependencies for matrix-play with compilation support..."

# Check if pip is available
if command -v pip3 &> /dev/null; then
    PIP_CMD="pip3"
elif command -v pip &> /dev/null; then
    PIP_CMD="pip"
else
    echo "Error: pip not found. Please install pip first."
    exit 1
fi

# Install dependencies
echo "Installing numpy, numba, and rgbmatrix..."
$PIP_CMD install -r requirements.txt

# Check if numba was installed successfully
if python3 -c "import numba; print('Numba version:', numba.__version__)" 2>/dev/null; then
    echo "✅ Numba installed successfully!"
    echo "Your matrix animations will now run with compiled code for better performance!"
else
    echo "⚠️  Numba installation failed. Matrix animations will run in interpreted mode."
fi

echo "Installation complete!"
