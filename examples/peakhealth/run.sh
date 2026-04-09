#!/bin/bash
# =============================================================================
# peakHealth — Multi-workflow runner
# =============================================================================
#
# Runs multiple Stokowski instances in parallel, each handling a different
# workflow based on issue type:
#
#   - workflow-implement.yaml: Todo/Backlog issues (bugs, improvements, tasks)
#   - workflow-feature.yaml: Feature definition issues
#
# Each instance watches the same Linear project but different states/labels.
#
# Usage:
#   cd /path/to/stokowski
#   ./examples/peakhealth/run.sh
#
# Stop:
#   Ctrl+C (kills all instances)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STOKOWSKI_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$STOKOWSKI_DIR"

# Check for required env vars
if [ -z "$LINEAR_API_KEY" ]; then
    echo "Error: LINEAR_API_KEY not set"
    echo "Set it in your environment or in a .env file"
    exit 1
fi

if [ -z "$LINEAR_PROJECT_SLUG" ]; then
    echo "Error: LINEAR_PROJECT_SLUG not set"
    echo "Find it in your Linear project URL (the hex slugId)"
    exit 1
fi

echo "Starting peakHealth workflows..."
echo "  Implementation: port 4200"
echo "  Feature Definition: port 4201"
echo ""
echo "Dashboards:"
echo "  http://localhost:4200 — Implementation pipeline"
echo "  http://localhost:4201 — Feature definition pipeline"
echo ""

# Trap Ctrl+C to kill all background processes
cleanup() {
    echo ""
    echo "Shutting down all workflows..."
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    echo "Done."
}
trap cleanup INT TERM

# Start implementation workflow
stokowski examples/peakhealth/workflow-implement.yaml &
PID_IMPL=$!

# Start feature workflow
stokowski examples/peakhealth/workflow-feature.yaml &
PID_FEAT=$!

echo "Running. Press Ctrl+C to stop."
wait
