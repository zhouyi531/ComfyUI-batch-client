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
# Port 8931 chosen to avoid colliding with other local dev services.
export PORT="${PORT:-8931}"
echo ""
echo "=========================================="
echo "  ComfyUI Batch Client"
echo "  Open: http://127.0.0.1:${PORT}"
echo "=========================================="
echo ""

# Foreground mode (used by services-hub so it can manage the process)
if [ "$1" = "-f" ] || [ "$1" = "--foreground" ]; then
    exec python scripts/server.py
fi

LOG_FILE="$(dirname "$0")/server.log"
nohup python scripts/server.py > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "Server started in background (PID: $SERVER_PID)"
echo "Logs: $LOG_FILE"
echo "To stop: kill $SERVER_PID"
