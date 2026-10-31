#!/bin/sh
# Started from KUAL. Runs the paper detached, so it survives the Kindle UI (and KUAL) being stopped.
DIR=/mnt/us/stop-press
mkdir -p "$DIR/logs"
# Already running? (the Kindle's ps only shows arguments with -o args)
if ps -eo args | grep -q '^sh /mnt/us/stop-press/[p]aper.sh'; then exit 0; fi
# No setsid or nohup on the K4: ignore hangups instead (paper.sh rides out KUAL closing itself).
(trap '' HUP; exec sh "$DIR/paper.sh" >>"$DIR/logs/paper.log" 2>&1 </dev/null) &
