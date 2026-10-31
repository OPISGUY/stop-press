#!/bin/sh
# KUAL "Get today's edition now": download the day's edition into the library straight away.
DIR=/mnt/us/stop-press
. "$DIR/env.sh"
export TZ="${POSIX_TZ:-UTC0}"
url=$(echo "$DASH_URL" | sed 's#/kindle\.png#/edition.mobi#')
out="/mnt/us/documents/${EDITION_NAME:-Stop Press} $(date +%Y-%m-%d).mobi"
"$DIR/xh" -d -q --timeout 120 --verify "$DIR/cacert.pem" -o "$out.part" get "$url" && mv "$out.part" "$out"
