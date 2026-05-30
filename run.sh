#!/usr/bin/env bash
set -e

# Color codes for readable output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
MARKER_FILE="$VENV_DIR/.deps_installed"

# 1. Create python virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    info "Creating Python virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# 2. Activate virtual environment
source "$VENV_DIR/bin/activate"

# 3. Install/update dependencies only when needed
#    The marker file is touched after a successful install.
#    It is removed if requirements change (e.g. new packages added to this script).
REQUIRED_PACKAGES="mss pillow pyserial opencv-python PyTurboJPEG"
MARKER_CONTENT="$REQUIRED_PACKAGES"

needs_install=false
if [ ! -f "$MARKER_FILE" ]; then
    needs_install=true
elif [ "$(cat "$MARKER_FILE")" != "$MARKER_CONTENT" ]; then
    warn "Dependency list changed. Reinstalling..."
    needs_install=true
fi

if [ "$needs_install" = true ]; then
    info "Installing/updating dependencies: $REQUIRED_PACKAGES"
    pip install --upgrade pip --quiet
    pip install $REQUIRED_PACKAGES --quiet
    echo "$MARKER_CONTENT" > "$MARKER_FILE"
    success "Dependencies installed."
else
    success "Dependencies already installed — skipping pip install."
fi

# 4. Run the host monitor script, passing all command line arguments
info "Starting Host Monitor..."
python host_monitor.py "$@"
