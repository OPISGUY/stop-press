#!/bin/sh
# Over SSH: replace a running paper with the current paper.sh. (A separate file, so pkill can't match our own command line.)
pkill -f "sh /mnt/us/stop-press/paper.sh"
pkill -f "tail -n 0 -f /var/log/messages"
pkill -f "dd if=/dev/input/event"
sleep 1
sh /mnt/us/stop-press/paper-start.sh
