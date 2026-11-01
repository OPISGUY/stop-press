#!/bin/sh
# Run at boot by /etc/init.d/stop-press (see autostart.sh): once the Kindle UI has come up, start the paper.
# To boot into the normal Kindle instead, put a file called NO_AUTOSTART in this folder (over USB).
DIR=/mnt/us/stop-press
[ -e "$DIR/NO_AUTOSTART" ] && exit 0
sleep 60 # let the framework finish starting first; paper.sh stops it again
[ -e "$DIR/NO_AUTOSTART" ] && exit 0
mkdir -p "$DIR/logs"
echo "$(date '+%F %T') boot: starting the paper" >>"$DIR/logs/paper.log"
exec sh "$DIR/paper-start.sh"
