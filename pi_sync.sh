#!/bin/bash
# Usage:
#   ./pi-sync.sh push                    # copy local -> Pi
#   ./pi-sync.sh pull                    # copy Pi -> local
#   ./pi-sync.sh run [filename]          # push then run specific file on Pi
#   ./pi-sync.sh run                     # push then run sinwave.py (default)

LOCAL="/home/john/projects/matrix-play/"
REMOTE="pi:/home/john/projects/matrix-play/"

case "$1" in
  push)
    rsync -avz "$LOCAL" "$REMOTE"
    ;;
  pull)
    rsync -avz "$REMOTE" "$LOCAL"
    ;;
  run)
    # Default to sinwave.py if no filename specified
    FILENAME="${2:-sinwave.py}"
    rsync -avz "$LOCAL" "$REMOTE" && \
    ssh -t john@pi "cd /home/john/projects/matrix-play && sudo python3 $FILENAME"
    ;;
  *)
    echo "Usage: $0 {push|pull|run [filename]}"
    echo "Examples:"
    echo "  $0 run                    # run sinwave.py"
    echo "  $0 run simple_starfield.py # run simple_starfield.py"
    echo "  $0 run starfield_effect.py # run starfield_effect.py"
    exit 1
    ;;
esac
