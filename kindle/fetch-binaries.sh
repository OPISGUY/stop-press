#!/bin/sh
# Run on your computer (not the Kindle), from this folder. Puts the three files paper.sh needs that aren't ours
# into stop-press/:
#   xh, next-wakeup  ARM builds from pascalw/kindle-dash (MIT): an HTTP client, and a cron-schedule calculator
#   cacert.pem       Mozilla's CA bundle via curl.se (the Kindle 4's own is too old for today's HTTPS)
set -e
cd "$(dirname "$0")"
VER=v1.0.0-beta.4
tmp=$(mktemp -d)
curl -fsSL "https://github.com/pascalw/kindle-dash/releases/download/$VER/kindle-dash-$VER.tgz" -o "$tmp/kd.tgz"
tar -xzf "$tmp/kd.tgz" -C "$tmp"
cp "$(find "$tmp" -name xh -type f | head -1)" stop-press/xh
cp "$(find "$tmp" -name next-wakeup -type f | head -1)" stop-press/next-wakeup
curl -fsSL https://curl.se/ca/cacert.pem -o stop-press/cacert.pem
rm -rf "$tmp"
echo "Done: stop-press/xh, stop-press/next-wakeup, stop-press/cacert.pem"
