#!/bin/sh
# Stop Press on the Kindle 4.
#   Awake:  the page keys, 5-way, Home, Menu and Back drive the paper (the server keeps the state; we just
#           forward each press to /ui.png and draw what comes back).
#   Asleep: after the power button, or 3 minutes without a press, the Kindle shows the front page (or your
#           rotation), suspends, and wakes on REFRESH_SCHEDULE (hourly by default) to refresh. The power button wakes it straight into the paper.
#   Offline: each hour it also downloads the paper as a book of screens (/offline.tar). When the server can't be
#           reached (the Wi-Fi drops as soon as you leave the room) the keys flip through that copy instead.
#   Live:   plugged in (or above 20% battery) and with FBInk installed, it can instead stay awake and keep the
#           page live: prices and countdowns update in place, with no page flashes. Home pressed twice switches.
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
BOOK="$DIR/offline"
OFFLINE=0
# FBInk draws one box of the screen at a time; live mode and the smart flash need it.
FBINK=
for f in "$DIR/fbink" /mnt/us/usbnet/bin/fbink /usr/bin/fbink /mnt/us/libkh/bin/fbink; do
  [ -x "$f" ] && FBINK=$f && break
done
export TZ="${POSIX_TZ:-UTC0}" # the Kindle clock is UTC; this names editions by your local date

log() { echo "$(date '+%F %T') $*"; }

# Ask the server for the screen after key $1 into file $2 ($3: extra query, e.g. &peek=1). Sends the battery level.
fetch() {
  "$XH" -d -q --timeout 12 --verify "$CA" -o "$2.part" get "$UI&k=$1${BATT:+&b=$BATT}$3" 2>/dev/null && mv "$2.part" "$2"
}

# Page draws. Waking up and the hourly page flash the whole screen ($2 = clean). For page turns the server decides
# (smart flash): it measures the ink the turn changes and, when most of the page changes, marks the PNG with a
# "stoppress-flash" text chunk so the turn itself flashes once; with FBInk, smaller worn areas flash alone with
# the next /live.tar, and small changes never flash. Without FBInk, turns flash as they always did.
draw() {
  lock
  if [ "$2" = clean ] || grep -q stoppress-flash "$1" 2>/dev/null || { [ -z "$FBINK" ] && [ "$2" = full ]; }; then $EIPS -f -g "$1"; else $EIPS -g "$1"; fi
  unlock
}

# --- live mode -------------------------------------------------------------------------------------
# In live mode (the file mode says live; charging or above 20% battery) the screen stays awake and alive with
# no page flashes. It needs FBInk (see the README); without it the paper stays in paper mode.
# FBInk redraws one box of the screen at a time, so:
#   - countdowns (the markets' opening and closing bells) are ticked here, every second, by stamping two-digit
#     images (/atlas.tar) into the cells the server names, with no network at all;
#   - every 5 seconds /live.tar brings whatever else changed on the screen as small PNG patches. The server keeps
#     a ghost ledger and marks a patch to flash (just that box) when its area has had too many no-flash redraws;
#   - page turns don't flash. Instead, 45 seconds after the last one (and every 20 minutes while left alone) an
#     "ink roller" cleans the screen top to bottom, one 100-row strip a second, each blinking once in place.
# A press holds /tmp/busy until the server has its new screen, and draws hold /tmp/drawlock, so nothing stamps
# old content onto a new page.
LD=/tmp/live
LIVE=$(echo "$DASH_URL" | sed 's#/kindle\.png#/live.tar#')
ATLAS=$(echo "$DASH_URL" | sed 's#/kindle\.png#/atlas.tar#')
LIVEPID=

# A lock left by a killed loop would freeze the page keys, so a long wait (about 2 s) takes it over.
lock() {
  # (its own counter name: sh variables are global, and a caller's loop counter must survive a lock)
  lk=0
  until mkdir /tmp/drawlock 2>/dev/null; do
    lk=$((lk + 1))
    [ $lk -gt 2000 ] && rmdir /tmp/drawlock 2>/dev/null
  done
}
unlock() { rmdir /tmp/drawlock 2>/dev/null; }
gen() { cat /tmp/drawgen 2>/dev/null || echo 0; }

# The file mode is "live" or "paper" (hourly, the default). KUAL's menu sets it; Home pressed twice switches it.
live_wanted() {
  [ -n "$FBINK" ] || return 1
  [ "$(cat "$DIR/mode" 2>/dev/null)" = live ] || return 1
  # Meant for when it's plugged in; the battery floor stops it flattening the Kindle if it isn't charging.
  [ "$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)" = 1 ] ||
    [ "$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null || echo 0)" -gt 20 ]
}

# Fetch /live.tar and apply its patches; keep its manifest for the ticker.
live_poll() {
  g=$(gen)
  rm -rf "$LD/new"
  mkdir -p "$LD/new"
  "$XH" -d -q --timeout 8 --verify "$CA" -o "$LD/live.tar" get "$LIVE" 2>/dev/null &&
    tar -xf "$LD/live.tar" -C "$LD/new" 2>/dev/null && [ -s "$LD/new/manifest.txt" ] || return 1
  lock
  if [ ! -e /tmp/busy ] && [ "$g" = "$(gen)" ]; then
    while read -r kind a b c d; do
      [ "$kind" = patch ] || continue
      if [ "$d" = 1 ]; then $FBINK -q -f -g "file=$LD/new/$a,x=$b,y=$c"; else $FBINK -q -g "file=$LD/new/$a,x=$b,y=$c"; fi
    done <"$LD/new/manifest.txt"
    MODE=none
    while read -r kind a b c; do
      case "$kind" in
        mode) MODE=$a; TARGET=$b ;;
        cell) eval "CELL_$a=\"$b $c\"" ;;
      esac
    done <"$LD/new/manifest.txt"
    LAST_h=; LAST_m=; LAST_s= # a patch may have covered the cells: stamp them all again
    VIEWGEN=$g
  fi
  unlock
}

# Stamp cell $1 with two digits $2 if they changed. Every 60th stamp of a cell flashes just that cell.
stamp() {
  eval "last=\$LAST_$1; n=\${CNT_$1:-0}; xy=\$CELL_$1"
  [ "$2" = "$last" ] && return
  set -- "$1" "$2" $xy
  flash=
  [ $((n % 60)) = 59 ] && flash=-f
  $FBINK -q $flash -g "file=$LD/atlas/d$2.png,x=$3,y=$4"
  eval "LAST_$1=$2; CNT_$1=$((n + 1))"
}

# Flash strip $1 (0-7) of what's on screen now, unless a page is going up.
roll() {
  lock
  [ ! -e /tmp/busy ] && [ "$gseen" = "$(gen)" ] && $FBINK -q -f -s "top=$(($1 * 100)),left=0,width=600,height=100"
  unlock
}

tick() {
  [ "$MODE" = countdown ] || [ "$MODE" = clock ] || return
  case "$MODE" in
    countdown)
      s=$((TARGET - $(date +%s)))
      [ "$s" -lt 0 ] && s=0
      set -- $(printf '%02d %02d %02d' $((s / 3600 % 100)) $((s / 60 % 60)) $((s % 60))) ;;
    clock) set -- $(date '+%H %M %S') ;;
  esac
  lock
  if [ ! -e /tmp/busy ] && [ "$VIEWGEN" = "$(gen)" ]; then
    stamp h "$1"
    stamp m "$2"
    stamp s "$3"
  fi
  unlock
}

live_loop() {
  mkdir -p "$LD/atlas"
  until [ -s "$LD/atlas/d99.png" ]; do
    "$XH" -d -q --timeout 20 --verify "$CA" -o "$LD/atlas.tar" get "$ATLAS" 2>/dev/null &&
      tar -xf "$LD/atlas.tar" -C "$LD/atlas" 2>/dev/null || sleep 5
  done
  MODE=none
  VIEWGEN=
  next=0
  n=0
  gseen=
  band=8
  while true; do
    now=$(date +%s)
    g=$(gen)
    if [ "$g" != "$gseen" ]; then
      gseen=$g
      changed=$now
      band=-1
    fi
    # After a press, give it and its prefetch the CPU for 2 s before asking for the new page's patches.
    if [ ! -e /tmp/busy ] && { [ "$now" -ge "$next" ] || { [ "$VIEWGEN" != "$g" ] && [ $((now - changed)) -ge 2 ]; }; }; then
      if live_poll; then next=$((now + 5)); else MODE=none; next=$((now + 3)); fi
    fi
    tick
    [ $((now - changed)) -ge 1245 ] && changed=$((now - 45)) && band=-1 # left alone: roll again every 20 minutes
    if [ "$band" -lt 8 ] && [ $((now - changed)) -ge 45 ]; then
      band=$((band + 1))
      [ "$band" -lt 8 ] && roll "$band"
    fi
    n=$((n + 1))
    if [ $((n % 300)) = 0 ] && ! live_wanted; then
      log "live mode off (battery low or time up)"
      echo "liveoff 0 $(date +%s)" >$FIFO
      exit 0
    fi
    sleep 1
  done
}

start_live() {
  live_wanted || return 1
  live_loop &
  LIVEPID=$!
  log "live mode"
}

stop_live() {
  [ -n "$LIVEPID" ] && kill "$LIVEPID" 2>/dev/null
  LIVEPID=
  rmdir /tmp/drawlock 2>/dev/null
}

# While you read, fetch (without applying) the screens the likely next presses give, so a flip only has to draw.
prefetch() {
  for k in next down prev up; do
    fetch "$k" "/tmp/pre.$k.png" "&peek=1" || return
  done
  # Where this screen is in the offline copy, so losing Wi-Fi carries on from here.
  "$XH" -d -q --timeout 12 --verify "$CA" -o /tmp/bookpos.part get "$(echo "$DASH_URL" | sed 's#/kindle\.png#/bookpos#')" 2>/dev/null &&
    mv /tmp/bookpos.part /tmp/bookpos
}

stop_prefetch() {
  [ -n "$PREFETCH" ] && kill "$PREFETCH" 2>/dev/null
  PREFETCH=
  pkill -f "peek=1" 2>/dev/null # its xh, if one is mid-fetch
}

# Show the screen for one key. $2 = clean (whole-screen flash), full (a flash only without FBInk) or partial;
# $3 = noprefetch. /tmp/busy and the draw count tell the live loop a new page is going up (see live mode).
show() {
  touch /tmp/busy
  _show "$@"
  r=$?
  echo $(($(gen) + 1)) >/tmp/drawgen
  rm -f /tmp/busy
  return $r
}

_show() {
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
    if [ -s "$BOOK/p001.png" ]; then
      go_offline "$1"
    else
      $EIPS 1 39 "  No connection - will retry  "
    fi
    return 1
  fi
  rm -f /tmp/pre.*.png
  if [ "$3" != noprefetch ]; then
    prefetch &
    PREFETCH=$!
  fi
}

# --- the offline copy -----------------------------------------------------------------------------

# Download a fresh copy, at most hourly, into $BOOK. It's unpacked beside the old one and swapped in whole.
fetch_offline() {
  stamp="$DIR/state/offline-fetched"
  now=$(date +%s)
  last=$(cat "$stamp" 2>/dev/null || echo 0)
  [ $((now - last)) -ge 3000 ] || return 0
  url=$(echo "$DASH_URL" | sed 's#/kindle\.png#/offline.tar#')
  tar="$DIR/offline.tar"
  if "$XH" -d -q --timeout 180 --verify "$CA" -o "$tar.part" get "$url" 2>/dev/null; then
    rm -rf "$BOOK.new"
    mkdir -p "$BOOK.new"
    if tar -xf "$tar.part" -C "$BOOK.new" && [ -s "$BOOK.new/pages.txt" ]; then
      rm -rf "$BOOK"
      mv "$BOOK.new" "$BOOK"
      echo "$now" >"$stamp"
      log "offline copy saved ($(cat "$BOOK/pages.txt") pages)"
    else
      log "offline copy didn't unpack"
    fi
  fi
  rm -rf "$tar.part" "$BOOK.new"
}

offline_draw() {
  $EIPS -f -g "$BOOK/$(printf 'p%03d.png' "$OPAGE")"
}

# Switch to the offline copy at the page matching where we were ($1: the key that failed; home and the sleep
# refresh go to the front page).
go_offline() {
  log "offline"
  OFFLINE=1
  OPAGE=$(cat /tmp/bookpos 2>/dev/null)
  case "$1" in home | auto | menu) OPAGE=1 ;; esac
  [ -s "$BOOK/$(printf 'p%03d.png' "${OPAGE:-1}" 2>/dev/null)" ] || OPAGE=1
  offline_draw
}

# One key while offline. Page keys turn pages; up/down jump to the next or previous story.
offline_key() {
  last=$(cat "$BOOK/pages.txt")
  case "$1" in
    next | right | select) [ "$OPAGE" -lt "$last" ] && OPAGE=$((OPAGE + 1)) ;;
    prev | left) [ "$OPAGE" -gt 1 ] && OPAGE=$((OPAGE - 1)) ;;
    down)
      while read -r c; do
        if [ "$c" -gt "$OPAGE" ]; then OPAGE=$c; break; fi
      done <"$BOOK/chapters.txt" ;;
    up | back)
      to=1
      while read -r c; do
        [ "$c" -lt "$OPAGE" ] && to=$c
      done <"$BOOK/chapters.txt"
      OPAGE=$to ;;
    menu) OPAGE=1 ;;
  esac
  offline_draw
}

# A key press, online or off. Home always tries the server again first.
press() {
  if [ "$OFFLINE" = 1 ] && [ "$1" != home ]; then
    offline_key "$1"
    return
  fi
  OFFLINE=0
  show "$@"
  settle
}

# Paper mode has no live loop, so after a press settles (2 s) ask /live.tar once for its smart-flash patches.
# A newer press kills the old one; live_poll also checks no page went up meanwhile.
settle() {
  [ -n "$FBINK" ] && [ -z "$LIVEPID" ] || return 0
  [ -n "$SETTLE" ] && kill "$SETTLE" 2>/dev/null
  (sleep 2; live_poll) &
  SETTLE=$!
}

# Before sleeping, clean the page gently: one 100-row strip at a time, each blinking once in place.
sweep() {
  [ -n "$FBINK" ] && [ "$OFFLINE" != 1 ] || return 0
  strip=0
  while [ $strip -lt 8 ]; do
    lock
    $FBINK -q -f -s "top=$((strip * 100)),left=0,width=600,height=100"
    unlock
    strip=$((strip + 1))
    sleep 1
  done
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
  [ -n "$LIVEPID" ] && return # live: stay awake
  GEN=$(($(date +%s) % 100000 * 10 + ${GEN:-0} % 10 + 1)) # tags this timer, so a stale one can't put us to sleep
  (sleep $IDLE && echo "idle $GEN $(date +%s)" >$FIFO) &
  TIMER=$!
}

# Switch live/paper mode from the keys, and say so for a few seconds at the foot of the screen.
toggle_mode() {
  if [ -n "$LIVEPID" ]; then
    echo paper >"$DIR/mode"
    stop_live
    arm_idle
    note="PAPER MODE - hourly, sleeps when idle"
  else
    echo live >"$DIR/mode"
    if start_live; then
      note="LIVE MODE - stays awake, updates live"
    elif [ -z "$FBINK" ]; then
      note="LIVE MODE needs FBInk - see the README"
    else
      note="LIVE MODE needs charger or 20% battery"
    fi
    arm_idle
  fi
  log "$note"
  lock
  $EIPS 1 38 "  $note  "
  unlock
  (sleep 4; lock; $EIPS -g /tmp/ui.png; unlock) &
}

interactive() {
  log "awake"
  entered=$(date +%s)
  $EIPS 1 39 "  Waking up - one moment...  "
  OFFLINE=0
  # After a suspend Wi-Fi takes a few seconds. Out of range, give up sooner and use the offline copy.
  n=0
  until ping -c 1 "$WIFI_TEST_IP" >/dev/null 2>&1 || [ $n -ge 12 ]; do
    n=$((n + 1))
    sleep 1
  done
  show none clean
  ready=$(date +%s) # power presses before the page appears are impatience, not "go to sleep"
  start_live
  arm_idle
  while true; do
    # BusyBox 1.7 ash's read fails when a signal lands (e.g. the idle timer we just killed); just read again.
    read -r ev code at <&3 || continue
    # Ignore anything queued before we woke (keys hit while asleep).
    [ "${at:-0}" -lt "$entered" ] && continue
    case "$ev" in
      idle)
        [ "$code" = "$GEN" ] || continue
        log "idle, going to sleep"; disarm_idle; sweep; return ;;
      liveoff)
        LIVEPID=; arm_idle ;; # back to sleeping after 3 idle minutes
      power)
        [ "$at" -lt $((ready + 2)) ] && continue # the press that woke us, or a repeat while waking
        log "power button, going to sleep"; disarm_idle; return ;;
      key)
        log "key $code"
        arm_idle
        case "$code" in
          104 | 191) press next full ;;
          109 | 193) press prev full ;;
          103) press up partial ;;
          108) press down partial ;;
          105) press left full ;;
          106) press right full ;;
          194) press select full ;;
          158) press back full ;;
          102)
            # Home twice within a second switches between live and paper mode.
            if [ $((at - ${LASTHOME:-0})) -le 1 ]; then
              LASTHOME=0
              toggle_mode
            else
              LASTHOME=$at
              press home full
            fi ;;
          139) press menu full ;;
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
  stop_live
  stop_prefetch
  rm -f /tmp/pre.*.png
  while true; do
    secs=$("$DIR/next-wakeup" --schedule="$REFRESH_SCHEDULE" --timezone="$TIMEZONE")
    if [ "$secs" -gt 3600 ]; then
      $EIPS -f -g "$DIR/sleeping.png" # overnight
    else
      "$DIR/wait-for-wifi.sh" "$WIFI_TEST_IP" >/dev/null 2>&1
      if show auto clean noprefetch; then
        fetch_edition
        fetch_offline
      fi
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
  stop_live
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
rm -f /tmp/busy
rmdir /tmp/drawlock 2>/dev/null
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
