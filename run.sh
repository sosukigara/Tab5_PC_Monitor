#!/usr/bin/env bash
set -e

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"

# 1. Create python virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# 2. Activate virtual environment and install/update dependencies
echo "Activating virtual environment and installing/updating dependencies..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install mss pillow pyserial opencv-python PyTurboJPEG

# 3. Run the host monitor script, passing all command line arguments
echo "Starting Host Monitor..."
python host_monitor.py "$@"
