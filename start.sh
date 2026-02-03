#!/bin/bash

# ComfyUI Batch Client - Start Script

cd "$(dirname "$0")"

# Check if venv exists, create if not
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Check and install dependencies
echo "Checking dependencies..."
pip install -q -r requirements.txt

# Create data directories if needed
mkdir -p data/workflows data/templates data/outputs

# Start server
echo ""
echo "=========================================="
echo "  ComfyUI Batch Client"
echo "  Open: http://127.0.0.1:8000"
echo "=========================================="
echo ""
python scripts/server.py
