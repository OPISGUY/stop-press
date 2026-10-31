#!/bin/sh
# Stop Press on the Kindle 4.
#   Awake:  the page keys, 5-way, Home, Menu and Back drive the paper (the server keeps the state; we just
#           forward each press to /ui.png and draw what comes back).
#   Asleep: after the power button, or 3 minutes without a press, the Kindle shows the scheduled page,
#           suspends, and wakes every 15 minutes to refresh. The power button wakes it straight into the paper.
#   The keyboard key hands the Kindle back to its normal library.
DIR=/mnt/us/stop-press
. "$DIR/env.sh"
XH="$DIR/xh"
CA="$DIR/cacert.pem"
EIPS=/usr/sbin/eips
RTC=/sys/devices/platform/mxc_rtc.0/wakeup_enable
FIFO=/tmp/paper.fifo
IDLE=180
UI=$(echo "$DASH_URL" | sed 's#/kindle\.png#/ui.png#')
export TZ="${POSIX_TZ:-UTC0}" # the Kindle clock is UTC; this names editions by your local date

log() { echo "$(date '+%F %T') $*"; }

# Ask the server for the screen after key $1 into file $2 ($3: extra query, e.g. &peek=1). Sends the battery level.
fetch() {
  "$XH" -d -q --timeout 30 --verify "$CA" -o "$2.part" get "$UI&k=$1${BATT:+&b=$BATT}$3" 2>/dev/null && mv "$2.part" "$2"
}

draw() {
  if [ "$2" = full ]; then $EIPS -f -g "$1"; else $EIPS -g "$1"; fi
}

# While you read, fetch (without applying) the screens the likely next presses give, so a flip only has to draw.
prefetch() {
  for k in next down prev up; do
    fetch "$k" "/tmp/pre.$k.png" "&peek=1" || return
  done
}

stop_prefetch() {
  [ -n "$PREFETCH" ] && kill "$PREFETCH" 2>/dev/null
  PREFETCH=
  pkill -f "peek=1" 2>/dev/null # its xh, if one is mid-fetch
}

# Show the screen for one key. $2 = full (whole-screen flash, clears ghosting) or partial; $3 = noprefetch.
show() {
  stop_prefetch
  BATT=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
  if [ -s "/tmp/pre.$1.png" ]; then
    draw "/tmp/pre.$1.png" "$2"
    # Now make the press real on the server; its screen is the one already drawn.
    fetch "$1" /tmp/ui.png || fetch "$1" /tmp/ui.png || log "commit failed for $1"
  elif fetch "$1" /tmp/ui.png; then
    draw /tmp/ui.png "$2"
  else
    log "fetch failed for $1"
    rm -f /tmp/pre.*.png
    $EIPS 1 39 "  No connection - will retry  "
    return 1
  fi
  rm -f /tmp/pre.*.png
  if [ "$3" != noprefetch ]; then
    prefetch &
    PREFETCH=$!
  fi
}

# Every input event is 16 bytes: seconds, microseconds, type, code, value. Key presses are type 1, value 1.
# The device stays open on fd 4: the side keys send a scan-code event microseconds before the key event, and
# reopening it between reads would lose the second one.
key_reader() {
  exec 4<"$1"
  while true; do
    set -- $(dd bs=16 count=1 <&4 2>/dev/null | hexdump -v -e '2/4 "%u " 2/2 " %u" 1/4 " %d" "\n"')
    if [ -z "$3" ]; then sleep 1; continue; fi # read failed: don't spin
    [ "$3" = 1 ] && [ "$5" = 1 ] && echo "key $4 $(date +%s)" >$FIFO
  done
}

# powerd ignores the power button while we prevent the screensaver, but still logs each press.
power_reader() {
  tail -n 0 -f /var/log/messages 2>/dev/null | while read -r line; do
    case "$line" in *pbpress*) echo "power 0 $(date +%s)" >$FIFO ;; esac
  done
}

start_readers() {
  rm -f $FIFO
  mkfifo $FIFO
  exec 3<>$FIFO # held open both ways: reads block instead of hitting end-of-file
  key_reader /dev/input/event0 &
  key_reader /dev/input/event1 &
  power_reader &
}

disarm_idle() {
  [ -n "$TIMER" ] && kill "$TIMER" 2>/dev/null
  TIMER=
}

arm_idle() {
  disarm_idle
  GEN=$(($(date +%s) % 100000 * 10 + ${GEN:-0} % 10 + 1)) # tags this timer, so a stale one can't put us to sleep
  (sleep $IDLE && echo "idle $GEN $(date +%s)" >$FIFO) &
  TIMER=$!
}

interactive() {
  log "awake"
  entered=$(date +%s)
  $EIPS 1 39 "  Waking up - one moment...  "
  "$DIR/wait-for-wifi.sh" "$WIFI_TEST_IP" >/dev/null 2>&1 # after a suspend Wi-Fi takes a few seconds
  show none full
  ready=$(date +%s) # power presses before the page appears are impatience, not "go to sleep"
  arm_idle
  while true; do
    # BusyBox 1.7 ash's read fails when a signal lands (e.g. the idle timer we just killed); just read again.
    read -r ev code at <&3 || continue
    # Ignore anything queued before we woke (keys hit while asleep).
    [ "${at:-0}" -lt "$entered" ] && continue
    case "$ev" in
      idle)
        [ "$code" = "$GEN" ] || continue
        log "idle, going to sleep"; disarm_idle; return ;;
      power)
        [ "$at" -lt $((ready + 2)) ] && continue # the press that woke us, or a repeat while waking
        log "power button, going to sleep"; disarm_idle; return ;;
      key)
        log "key $code"
        arm_idle
        case "$code" in
          104 | 191) show next full ;;
          109 | 193) show prev full ;;
          103) show up partial ;;
          108) show down partial ;;
          105) show left full ;;
          106) show right full ;;
          194) show select full ;;
          158) show back full ;;
          102) show home full ;;
          139) show menu full ;;
          29) exit_to_library ;;
        esac ;;
    esac
  done
}

fetch_edition() {
  stamp="$DIR/state/edition-fetched"
  now=$(date +%s)
  last=$(cat "$stamp" 2>/dev/null || echo 0)
  [ $((now - last)) -ge 3600 ] || [ "$(date +%H%M)" -ge 2345 ] || return 0
  url=$(echo "$DASH_URL" | sed 's#/kindle\.png#/edition.mobi#')
  out="/mnt/us/documents/${EDITION_NAME:-Stop Press} $(date +%Y-%m-%d).mobi"
  if "$XH" -d -q --timeout 120 --verify "$CA" -o "$out.part" get "$url" 2>/dev/null; then
    mv "$out.part" "$out"
    echo "$now" >"$stamp"
    log "edition saved"
  else
    rm -f "$out.part"
  fi
  find /mnt/us/documents -name "${EDITION_NAME:-Stop Press} *" -mtime +14 -exec rm -f {} \;
}

# Show the scheduled page, then suspend until the next refresh. Returns when the power button wakes us.
asleep() {
  log "asleep"
  stop_prefetch
  rm -f /tmp/pre.*.png
  while true; do
    secs=$("$DIR/next-wakeup" --schedule="$REFRESH_SCHEDULE" --timezone="$TIMEZONE")
    if [ "$secs" -gt 3600 ]; then
      $EIPS -f -g "$DIR/sleeping.png" # overnight
    else
      "$DIR/wait-for-wifi.sh" "$WIFI_TEST_IP" >/dev/null 2>&1
      show auto full noprefetch
      fetch_edition
    fi
    sleep 3 # let the screen settle
    echo 0 >$RTC
    echo -n "$secs" >$RTC
    before=$(date +%s)
    echo mem >/sys/power/state
    slept=$(($(date +%s) - before))
    if [ "$slept" -lt 3 ]; then
      sleep 20 # suspend refused; try again shortly rather than spin
    elif [ "$slept" -lt $((secs - 20)) ]; then
      log "woken by the power button after ${slept}s"
      return
    fi
  done
}

exit_to_library() {
  log "keyboard key: back to the Kindle library"
  $EIPS -c
  $EIPS 8 18 "Back to your Kindle library..."
  $EIPS 8 20 "(about half a minute)"
  $EIPS 4 23 "KUAL > Stop Press > Start the paper"
  $EIPS 4 24 "brings the paper back."
  echo 0 >$RTC
  disarm_idle
  stop_prefetch
  pkill -f "tail -n 0 -f /var/log/messages"
  pkill -f "dd if=/dev/input/event"
  lipc-set-prop com.lab126.powerd preventScreenSaver 0
  /etc/init.d/browserd start
  /etc/init.d/webreaderd start
  /etc/init.d/framework start
  # Stop our own reader loops last (they share our command line).
  pkill -f "sh /mnt/us/stop-press/paper.sh"
  exit 0
}

log "starting"
mkdir -p "$DIR/state"
trap '' HUP
trap '' TERM # stopping the Kindle UI also signals whatever KUAL launched, which includes us
/etc/init.d/framework stop
trap - TERM
# The web reader and browser outlive the framework and grab the page keys, so they go too.
/etc/init.d/webreaderd stop >/dev/null 2>&1
/etc/init.d/browserd stop >/dev/null 2>&1
lipc-set-prop com.lab126.powerd preventScreenSaver 1
start_readers
while true; do
  interactive
  asleep
done
