#!/bin/sh
# Stop Press on a Kindle. One script, two kinds of device (see "device profile" below):
#   keys   the Kindle 4: page keys, 5-way, Home, Menu and Back;
#   touch  the Kindle Touch, Paperwhite and later: tap a headline or market once to frame it and again to open
#          it, use the navigation bar along the foot (or swipe left/right) to turn, swipe down for the front
#          page and up for the article list, tap LIVE/PAPER at the top (or hold a finger down) to switch mode,
#          hold the top-left corner to go back to the Kindle library.
#   Awake:  each press or tap goes to the server (it keeps the state; we just ask /ui.png and draw what comes
#           back).
#   Asleep: after the power button, or 3 minutes without a press, the Kindle shows the front page (or your
#           rotation), suspends, and wakes on REFRESH_SCHEDULE (hourly by default) to refresh. The power button wakes it straight into the paper.
#   Offline: each hour it also downloads the paper as a book of screens (/offline.tar). When the server can't be
#           reached (the Wi-Fi drops as soon as you leave the room) the keys flip through that copy instead.
#   Live:   plugged in (or above 20% battery) and with FBInk installed, it can instead stay awake and keep the
#           page live: prices and countdowns update in place, with no page flashes. Home pressed twice switches.
#   Standalone (touch Kindles): instead of asking the server for every screen, fetch the whole paper once an
#           hour (/bundle.tar) and answer every tap from the Kindle's own storage, with Wi-Fi off in between.
#           The STANDALONE / SERVER switch at the top of the screen changes over; see "standalone mode" below.
#   The keyboard key hands the Kindle back to its normal library.
DIR=/mnt/us/stop-press
. "$DIR/env.sh"
XH="$DIR/xh"
CA="$DIR/cacert.pem"
# The full certificate list costs about 3.5 s of CPU per HTTPS request on a Paperwhite. cacert-fast.pem holds
# only the few roots Azure and Yahoo use, and takes 0.25 s. If a fetch fails with it, ca_full goes back to the
# full list for the rest of the run (your server may use another certificate authority).
[ -s "$DIR/cacert-fast.pem" ] && CA="$DIR/cacert-fast.pem"
server_up() { "$XH" -d -q --timeout 5 --verify "$CA" -o /tmp/ca.test get "$(url healthz)" >/dev/null 2>&1; }
ca_full() { # after a failed fetch: true (and switched) only if the full list works where the short one doesn't
  [ "$CA" != "$DIR/cacert.pem" ] || return 1
  server_up && return 1 # (just a blip)
  "$XH" -d -q --timeout 5 --verify "$DIR/cacert.pem" -o /tmp/ca.test get "$(url healthz)" >/dev/null 2>&1 || return 1 # (offline)
  server_up && return 1 # (and checked again: the network may simply have come back in between)
  CA="$DIR/cacert.pem"
  log "your server's certificate isn't in cacert-fast.pem: using the full cacert.pem from now on"
}
# xh ignores --verify for plain http:// addresses and loads the Kindle's own certificate bundle, which it
# can't read, so every fetch to a server on your home network would fail. Point it at ours instead.
export SSL_CERT_FILE="$DIR/cacert.pem"
EIPS=/usr/sbin/eips
RTC=/sys/devices/platform/mxc_rtc.0/wakeup_enable
FIFO=/tmp/paper.fifo
IDLE=${PAPER_IDLE:-180} # seconds awake after the last press (server mode)
BOOK="$DIR/offline"
OFFLINE=0
# FBInk draws one box of the screen at a time; live mode and the smart flash need it, and so do touch Kindles.
FBINK=
for f in "$DIR/fbink" /mnt/us/usbnet/bin/fbink /usr/bin/fbink /mnt/us/libkh/bin/fbink; do
  [ -x "$f" ] && FBINK=$f && break
done

# --- device profile --------------------------------------------------------------------------------
# The Kindle 4 (and older) has the legacy e-ink driver, keys and SysV init scripts. Later Kindles have the mxcfb
# driver, a touch screen and upstart. INPUT, SW x SH (screen pixels) and the helpers below are all that differs.
PATH=/sbin:/usr/sbin:$PATH
if [ -e /proc/eink_fb ] || [ ! -e /etc/upstart/lab126_gui.conf ]; then
  INPUT=keys
  SW=600
  SH=800
else
  INPUT=touch
  SW=$(cut -d, -f1 /sys/class/graphics/fb0/virtual_size 2>/dev/null)
  SH=
  if [ -n "$FBINK" ]; then
    eval "$("$FBINK" -e 2>/dev/null | tr ';' '\n' | grep -e '^viewWidth=' -e '^viewHeight=')"
    SW=${viewWidth:-$SW}
    SH=$viewHeight
  fi
  SW=${SW:-758}
  SH=${SH:-1024}
  TOUCH_DEV=${TOUCH_DEV:-/dev/input/event0}
fi
# Tell the server the screen, so it draws pages at this size (nothing is sent for the Kindle 4's 600x800).
DEVQ=
[ "$SW" = 600 ] && [ "$SH" = 800 ] || DEVQ="&dev=${SW}x${SH}"
[ "$INPUT" = touch ] && DEVQ="$DEVQ&touch=1&epd=mxc"
url() { echo "$DASH_URL" | sed "s#/kindle\\.png#/$1#"; } # the server address for another endpoint
UI="$(url ui.png)$DEVQ"
# The screens fetched ahead while you read. A touch screen has no up/down; its navigation bar's arrows (the end
# cells of the bar along the foot, see ui.py) are the next and previous page, answered here from those screens.
if [ "$INPUT" = touch ]; then PREKEYS="next prev"; else PREKEYS="next down prev up"; fi
NAV_Y=$((SH - SH * 44 / 800))
NAV_ARROW=$((SW * 70 / 600))

# Show a tap was felt, at once: blink a small square under the finger (the page that follows redraws it).
ack() {
  ax=$(($1 - 28)); [ $ax -lt 0 ] && ax=0
  ay=$(($2 - 28)); [ $ay -lt 0 ] && ay=0
  "$FBINK" -q -h -W A2 -s "top=$ay,left=$ax,width=56,height=56" 2>/dev/null
}
# Put the square back as it should be (a tap that changed nothing would otherwise leave it inverted).
unack() {
  lock
  "$FBINK" -q -W GL16 -s "top=$ay,left=$ax,width=56,height=56" 2>/dev/null
  unlock
}

# Put image $1 on the screen; $2 = flash for a whole-screen flash, fast when only a highlight changed.
# On the newer driver each job gets the update mode made for it:
#   flash  GC16 with a flash, and wait for it, so nothing lands on top of a half-finished flash;
#   fast   DU, the quick black-and-white update (about a third of the time): enough for a selection frame;
#   else   GL16, the no-flash mode Kindles turn book pages with (it leaves white alone, so less ghosting).
put() {
  if [ "$INPUT" = touch ]; then
    case "$2" in
      flash) "$FBINK" -q -f -w -W GC16 -g "file=$1" ;;
      fast) "$FBINK" -q -W DU -g "file=$1" ;;
      *) "$FBINK" -q -W GL16 -g "file=$1" ;;
    esac
  elif [ "$2" = flash ]; then $EIPS -f -g "$1"; else $EIPS -g "$1"; fi
}

# A one-line note at the foot of the screen ($2 = up for the line above it).
note() {
  if [ "$INPUT" = touch ]; then
    if [ "$2" = up ]; then "$FBINK" -q -pm -y -3 "$1"; else "$FBINK" -q -pm -y -2 "$1"; fi
  elif [ "$2" = up ]; then $EIPS 1 38 "  $1  "; else $EIPS 1 39 "  $1  "; fi
}

# Stop and restart the Kindle's own reading software, which otherwise draws over us and takes the input.
ui_stop() {
  if [ "$INPUT" = touch ]; then
    stop lab126_gui >/dev/null 2>&1
  else
    /etc/init.d/framework stop
    # The web reader and browser outlive the framework and grab the page keys, so they go too.
    /etc/init.d/webreaderd stop >/dev/null 2>&1
    /etc/init.d/browserd stop >/dev/null 2>&1
  fi
}
ui_start() {
  if [ "$INPUT" = touch ]; then
    start lab126_gui >/dev/null 2>&1
  else
    /etc/init.d/browserd start
    /etc/init.d/webreaderd start
    /etc/init.d/framework start
  fi
}
export TZ="${POSIX_TZ:-UTC0}" # the Kindle clock is UTC; this names editions by your local date

log() { echo "$(date '+%F %T') $*"; }

# A touch screen shows a live/paper switch, so each request says which mode this is.
modeq() {
  [ "$INPUT" = touch ] || return 0
  if [ -n "$LIVEPID" ]; then echo "&mode=live"; else echo "&mode=paper"; fi
}

# Ask the server for the screen after key $1 into file $2 ($3: extra query, e.g. &peek=1). Sends the battery level.
fetch() {
  "$XH" -d -q --timeout 12 --verify "$CA" -o "$2.part" get "$UI$(modeq)&k=$1${BATT:+&b=$BATT}$3$TAPQ" 2>/dev/null && mv "$2.part" "$2" && return 0
  ca_full && "$XH" -d -q --timeout 12 --verify "$CA" -o "$2.part" get "$UI$(modeq)&k=$1${BATT:+&b=$BATT}$3$TAPQ" 2>/dev/null && mv "$2.part" "$2"
}

# Page draws. Waking up and the hourly page flash the whole screen ($2 = clean). For page turns the server decides
# (smart flash): it measures the ink the turn changes and, when most of the page changes, marks the PNG with a
# "stoppress-flash" text chunk so the turn itself flashes once; with FBInk, smaller worn areas flash alone with
# the next /live.tar, and small changes never flash. Without FBInk, turns flash as they always did.
draw() {
  lock
  if [ "$2" = clean ] || grep -q stoppress-flash "$1" 2>/dev/null || { [ -z "$FBINK" ] && [ "$2" = full ]; }; then
    put "$1" flash
  elif grep -q stoppress-fast "$1" 2>/dev/null; then
    put "$1" fast
  else
    put "$1"
  fi
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
LIVE="$(url live.tar)$DEVQ"
ATLAS="$(url atlas.tar)$DEVQ"
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
  "$XH" -d -q --timeout 8 --verify "$CA" -o "$LD/live.tar" get "$LIVE$(modeq)" 2>/dev/null &&
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
  # (the newer driver ticks with DU, its quick black-and-white update; the server draws those digits 1-bit)
  if [ "$INPUT" = touch ] && [ -z "$flash" ]; then
    $FBINK -q -W DU -g "file=$LD/atlas/d$2.png,x=$3,y=$4"
  else
    $FBINK -q $flash -g "file=$LD/atlas/d$2.png,x=$3,y=$4"
  fi
  eval "LAST_$1=$2; CNT_$1=$((n + 1))"
}

# Flash strip $1 (0-7) of what's on screen now, unless a page is going up.
STRIP=$(((SH + 7) / 8))
roll() {
  lock
  [ ! -e /tmp/busy ] && [ "$gseen" = "$(gen)" ] && $FBINK -q -f -s "top=$(($1 * STRIP)),left=0,width=$SW,height=$STRIP"
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
  LIVEPID=self # (we are the live loop: modeq, asked from in here, should say so)
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
    # (keys only: the newer driver doesn't need the roller, and strips blinking mid-read look like a fault)
    if [ "$INPUT" = keys ] && [ "$band" -lt 8 ] && [ $((now - changed)) -ge 45 ]; then
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
  if [ -n "$ARMQ" ]; then # something is framed: fetch what a second tap on it opens, first
    TAPQ=$ARMQ
    fetch tap /tmp/pre.arm.png "&peek=1"
    TAPQ=
  fi
  for k in $PREKEYS; do
    fetch "$k" "/tmp/pre.$k.png" "&peek=1" || return
  done
  # Where this screen is in the offline copy, so losing Wi-Fi carries on from here.
  "$XH" -d -q --timeout 12 --verify "$CA" -o /tmp/bookpos.part get "$(url bookpos)" 2>/dev/null &&
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
  bnow=$(date +%s) # the battery level is asked for at most every two minutes: it's slow to ask
  if [ $((bnow - ${BATT_AT:-0})) -ge 120 ]; then
    BATT=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
    BATT_AT=$bnow
  fi
  if [ -s "/tmp/pre.$1.png" ]; then
    draw "/tmp/pre.$1.png" "$2"
    # Now make the press real on the server; its screen is the one already drawn.
    fetch "$1" /tmp/ui.png || fetch "$1" /tmp/ui.png || log "commit failed for $1"
    # (a tap drawn from a guess: if the server's answer differs, that's the truth)
    if [ "$1" = tap ] && ! cmp -s /tmp/pre.tap.png /tmp/ui.png; then draw /tmp/ui.png "$2"; fi
  elif fetch "$1" /tmp/ui.png || { sleep 5; fetch "$1" /tmp/ui.png; }; then # (Wi-Fi is often slow just after a wake)
    draw /tmp/ui.png "$2"
  else
    log "fetch failed for $1"
    rm -f /tmp/pre.*.png
    if [ -s "$BOOK/p001.png" ]; then
      go_offline "$1"
    else
      note "No connection - will retry"
    fi
    return 1
  fi
  rm -f /tmp/pre.*.png
  if [ "$3" != noprefetch ]; then
    prefetch &
    PREFETCH=$!
  fi
}

# --- standalone mode ------------------------------------------------------------------------------
# The file source says "standalone" or "server" (the default). In standalone mode the Kindle holds the paper
# itself: $SA/s/<screen>.png and beside each a .map of what a tap on it does (the server's bundle.py makes them).
# A tap is looked up in the map of the screen that's showing and the next screen is drawn straight from storage:
# no Wi-Fi, no waiting. Wi-Fi is switched on only to fetch the next hourly bundle, and for the live board.
SA="$DIR/bundle"
SRC=server
[ "$INPUT" = touch ] && [ "$(cat "$DIR/source" 2>/dev/null)" = standalone ] && SRC=standalone
SA_IDLE=${STANDALONE_IDLE:-90} # seconds awake after the last tap (waking again costs nothing here)
INK_FLASH=6                    # flash a turn once the screens drawn since the last flash add up to this
SALIVE=
pkill -f "$DIR/live.lua" 2>/dev/null # (a board left over from before a restart)

# Wi-Fi on (and wait for it) or off. STANDALONE_WIFI=on in env.sh keeps it on, e.g. to reach the Kindle by SSH.
wifi() {
  if [ "$1" = on ]; then
    lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
    wn=0
    until ping -c 1 "$WIFI_TEST_IP" >/dev/null 2>&1 || [ $wn -ge 40 ]; do
      wn=$((wn + 1))
      sleep 1
    done
    [ $wn -lt 40 ]
  elif [ "$STANDALONE_WIFI" != on ]; then
    lipc-set-prop com.lab126.cmd wirelessEnable 0 2>/dev/null
  fi
}

# Fetch a fresh bundle into $SA (unpacked beside the old one and swapped in whole). The server answers 202 with
# no bundle while it is building one, so ask again for up to three minutes. Leaves Wi-Fi on.
sa_sync() {
  t0=$(date +%s)
  if ! wifi on; then
    log "bundle: no Wi-Fi"
    return 1
  fi
  BATT=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
  burl="$(url bundle.tar)$DEVQ${BATT:+&b=$BATT}"
  got=
  tries=0
  fails=0
  while [ $tries -lt 18 ] && [ $fails -lt 3 ]; do
    if "$XH" -d -q --timeout 120 --verify "$CA" -o "$DIR/bundle.tar.part" get "$burl" 2>/dev/null; then
      rm -rf "$SA.new"
      mkdir -p "$SA.new"
      if tar -xf "$DIR/bundle.tar.part" -C "$SA.new" 2>/dev/null && [ -s "$SA.new/meta.txt" ] && [ -s "$SA.new/s/front.png" ]; then
        got=1
        break
      fi
    else
      ca_full || fails=$((fails + 1))
    fi
    tries=$((tries + 1))
    sleep 10
  done
  rm -f "$DIR/bundle.tar.part"
  if [ -z "$got" ]; then
    rm -rf "$SA.new"
    log "bundle: fetch failed after $(($(date +%s) - t0))s"
    return 1
  fi
  rm -rf "$SA"
  mv "$SA.new" "$SA"
  date +%s >"$DIR/state/bundle-fetched"
  log "bundle saved in $(($(date +%s) - t0))s: $(sed -n 's/^screens //p' "$SA/meta.txt") screens, edition $(sed -n 's/^stamp //p' "$SA/meta.txt"), battery ${BATT:-?}%"
}

# Draw screen $1 from the bundle ($2 = flash). Turns don't flash until enough ink has changed since the last one.
sa_show() {
  [ -s "$SA/s/$1.png" ] || return 1
  inkw=$(sed -n 's/^ink //p' "$SA/s/$1.map" 2>/dev/null)
  INKSUM=$((${INKSUM:-0} + ${inkw:-3}))
  lock
  if [ "$2" = flash ] || [ "$INKSUM" -ge "$INK_FLASH" ]; then
    put "$SA/s/$1.png" flash
    INKSUM=0
  else
    put "$SA/s/$1.png"
  fi
  unlock
  SCR=$1
  ARMED=
}

# Go to a target from a map: a screen, @back, !live or !server. $2: the line's flags (b = remember where we were).
sa_go() {
  case "$1" in
    "!live") sa_live; return ;;
    "!server") to_server; return ;;
    "@back") sa_show "${BACK:-front}" || sa_show front; return ;;
  esac
  case "$2" in *b*) BACK=$SCR ;; esac
  sa_show "$1" || sa_show front
}

# Frame the box "$1 $2 $3 $4" (x0 y0 x1 y1): four bars drawn inverted, the quick way. sa_unframe puts it back.
sa_frame() {
  set -- $1
  fw=$(($3 - $1)); fh=$(($4 - $2))
  lock
  "$FBINK" -q -h -W DU -s "top=$2,left=$1,width=$fw,height=6" 2>/dev/null
  "$FBINK" -q -h -W DU -s "top=$(($4 - 6)),left=$1,width=$fw,height=6" 2>/dev/null
  "$FBINK" -q -h -W DU -s "top=$2,left=$1,width=6,height=$fh" 2>/dev/null
  "$FBINK" -q -h -W DU -s "top=$2,left=$(($3 - 6)),width=6,height=$fh" 2>/dev/null
  unlock
}
sa_unframe() {
  [ -n "$ARMED" ] || return 0
  set -- $ARMED
  lock
  "$FBINK" -q -W GL16 -s "top=$2,left=$1,width=$(($3 - $1)),height=$(($4 - $2))" 2>/dev/null
  unlock
  ARMED=
}

# A tap at $1 $2: the first box in this screen's map that holds it. Boxes flagged f are framed by the first tap
# and opened by the second, as in server mode.
sa_tap() {
  hit=
  while read -r kind a b c d tgt fl; do
    [ "$kind" = tap ] || continue
    if [ "$1" -ge "$a" ] && [ "$1" -lt "$c" ] && [ "$2" -ge "$b" ] && [ "$2" -lt "$d" ]; then
      hit=1
      break
    fi
  done <"$SA/s/$SCR.map"
  if [ -z "$hit" ]; then
    sa_unframe
    unack
    return
  fi
  case "$fl" in
    *f*)
      if [ "$ARMED" = "$a $b $c $d" ]; then
        sa_go "$tgt" "$fl"
      else
        sa_unframe
        unack
        ARMED="$a $b $c $d"
        sa_frame "$ARMED"
      fi ;;
    *) sa_go "$tgt" "$fl" ;;
  esac
}

sa_key() {
  t=$(sed -n "s/^key $1 //p" "$SA/s/$SCR.map" 2>/dev/null)
  [ -n "$t" ] && sa_go "$t"
}

# Fetch a fresh edition now (swipe down on the front page, or KUAL).
sa_refresh() {
  lock
  note "Fetching a fresh edition - about a minute..."
  unlock
  if sa_sync; then
    wifi off
    BACK=
    sa_show front flash
  else
    wifi off
    lock
    note "Couldn't reach the server - still the $(sed -n 's/^stamp //p' "$SA/meta.txt" 2>/dev/null) edition"
    unlock
  fi
}

# The live board: live.lua fetches each price itself and prints the board, with no server at all. A test bed:
# it logs how long every fetch and draw takes. Any tap ends it.
sa_live() {
  if [ "$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)" != 1 ] &&
    [ "$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null || echo 0)" -le 20 ]; then
    lock
    note "LIVE MODE needs charger or 20% battery" up
    unlock
    return
  fi
  echo live >"$DIR/mode"
  disarm_idle
  lock
  note "LIVE MODE - connecting..." up
  unlock
  wifi on
  pkill -f "$DIR/live.lua" 2>/dev/null # (one board at a time: two would share /tmp/salive and mix up prices)
  lua "$DIR/live.lua" "$SA/live/symbols.txt" "$FBINK" "$XH" "$CA" /tmp/salive &
  SALIVE=$! # (its own name: LIVEPID belongs to server live mode, and other code clears it)
  log "standalone live board"
}
sa_live_stop() {
  [ -n "$SALIVE" ] || return 0
  kill "$SALIVE" 2>/dev/null
  pkill -f "$DIR/live.lua" 2>/dev/null # (started from a subshell, e.g. on waking, its pid isn't ours to know)
  SALIVE=
  echo paper >"$DIR/mode"
  wifi off
  log "standalone live board off"
  sa_show "${SCR:-front}" flash
}

# One touch event in standalone mode.
sa_event() {
  if [ -n "$SALIVE" ]; then
    if [ "$1" = hold ] && [ "$3" -lt $((SW / 5)) ] && [ "$4" -lt $((SH / 6)) ]; then exit_to_library; fi
    sa_live_stop
    return
  fi
  case "$1" in
    tap)
      ack "$3" "$4"
      sa_tap "$3" "$4" ;;
    swipe)
      sa_unframe
      case "$2" in
        left) sa_key next ;;
        right) sa_key prev ;;
        down) if [ "$SCR" = front ]; then sa_refresh; else sa_key home; fi ;;
        up) sa_key menu ;;
      esac ;;
    hold)
      if [ "$3" -lt $((SW / 5)) ] && [ "$4" -lt $((SH / 6)) ]; then exit_to_library; else sa_live; fi ;;
  esac
}

# Change over. To the server: Wi-Fi on, and every tap is asked of the server again. To standalone: fetch the
# paper, then Wi-Fi off.
to_server() {
  sa_live_stop >/dev/null 2>&1
  echo server >"$DIR/source"
  SRC=server
  log "server mode"
  lock
  note "SERVER MODE - connecting..." up
  unlock
  wifi on
  show none clean
  start_live
}
to_standalone() {
  stop_live
  stop_prefetch
  [ -n "$SETTLE" ] && kill "$SETTLE" 2>/dev/null
  lock
  note "STANDALONE - fetching the paper (about a minute)..." up
  unlock
  if sa_sync || [ -s "$SA/s/front.png" ]; then
    echo standalone >"$DIR/source"
    echo paper >"$DIR/mode"
    SRC=standalone
    OFFLINE=0
    wifi off
    BACK=
    log "standalone mode"
    sa_show front flash
  else
    lock
    note "Couldn't fetch the paper - staying in server mode" up
    unlock
  fi
}

# --- the offline copy -----------------------------------------------------------------------------

# Download a fresh copy, at most hourly, into $BOOK. It's unpacked beside the old one and swapped in whole.
fetch_offline() {
  stamp="$DIR/state/offline-fetched"
  now=$(date +%s)
  last=$(cat "$stamp" 2>/dev/null || echo 0)
  [ $((now - last)) -ge 3000 ] || return 0
  url="$(url offline.tar)$DEVQ"
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
  put "$BOOK/$(printf 'p%03d.png' "$OPAGE")" flash
}

# Switch to the offline copy at the page matching where we were ($1: the key that failed; home and the sleep
# refresh go to the front page).
go_offline() {
  log "offline"
  OFFLINE=1
  OFF_AT=$(date +%s)
  OPAGE=$(cat /tmp/bookpos 2>/dev/null)
  case "$1" in home | auto | menu) OPAGE=1 ;; esac
  case "$OPAGE" in "" | 0 | *[!0-9]*) OPAGE=1 ;; esac # (/tmp/bookpos can be empty or junk)
  [ -s "$BOOK/$(printf 'p%03d.png' "$OPAGE")" ] || OPAGE=1
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
  if [ "$INPUT" = touch ] && [ "$OFFLINE" != 1 ]; then # one clean flash of what's there; nobody is looking
    lock
    "$FBINK" -q -f -s
    unlock
    return
  fi
  [ -n "$FBINK" ] && [ "$OFFLINE" != 1 ] || return 0
  strip=0
  while [ $strip -lt 8 ]; do
    lock
    $FBINK -q -f -s "top=$((strip * STRIP)),left=0,width=$SW,height=$STRIP"
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

# Touch Kindles: touch.lua (the Kindle's own Lua) turns the screen's events into "tap x y time",
# "swipe <direction> x y time" and "hold x y tenths" lines, which go to the same queue as key presses.
touch_reader() {
  lua "$DIR/touch.lua" "$TOUCH_DEV" 2>/dev/null | while read -r line; do
    echo "$line" >$FIFO
  done
}

start_readers() {
  rm -f $FIFO
  mkfifo $FIFO
  exec 3<>$FIFO # held open both ways: reads block instead of hitting end-of-file
  if [ "$INPUT" = touch ]; then
    touch_reader &
  else
    key_reader /dev/input/event0 &
    key_reader /dev/input/event1 &
  fi
  power_reader &
}

disarm_idle() {
  [ -n "$TIMER" ] && kill "$TIMER" 2>/dev/null
  TIMER=
}

arm_idle() {
  disarm_idle
  [ -n "$LIVEPID$SALIVE" ] && return # live: stay awake
  GEN=$(($(date +%s) % 100000 * 10 + ${GEN:-0} % 10 + 1)) # tags this timer, so a stale one can't put us to sleep
  idle=$IDLE
  [ "$SRC" = standalone ] && idle=$SA_IDLE
  (sleep $idle && echo "idle $GEN $(date +%s)" >$FIFO) &
  TIMER=$!
}

# Switch live/paper mode from the keys, and say so for a few seconds at the foot of the screen.
toggle_mode() {
  [ -n "$SETTLE" ] && kill "$SETTLE" 2>/dev/null # its answer would be drawn for the old mode
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
  [ "$1" = quiet ] && return
  lock
  note "$note" up
  unlock
  (sleep 4; lock; put /tmp/ui.png; unlock) &
}

interactive() {
  log "awake"
  entered=$(date +%s)
  OFFLINE=0
  if [ "$SRC" = standalone ] && [ ! -s "$SA/s/front.png" ]; then # first time: there is no paper here yet
    note "Fetching the paper - about a minute..."
    sa_sync || { SRC=server; log "no bundle: server mode for now"; }
    [ "$SRC" = standalone ] && wifi off
  fi
  if [ "$SRC" = standalone ]; then
    [ -s "$SA/s/${SCR:-front}.png" ] || SCR=front
    sa_show "${SCR:-front}" flash
    ready=$(date +%s)
    [ "$(cat "$DIR/mode" 2>/dev/null)" = live ] && sa_live
    arm_idle
  else
  note "Waking up - one moment..."
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
  fi
  while true; do
    # BusyBox 1.7 ash's read fails when a signal lands (e.g. the idle timer we just killed); just read again.
    read -r ev code at a4 a5 <&3 || continue
    # Touch lines carry more fields: "tap x y time", "swipe dir x y time", "hold x y tenths".
    case "$ev" in
      tap) tx=$code; ty=$at; at=$a4 ;;
      swipe) tx=$at; ty=$a4; at=$a5 ;;
      hold) tx=$code; ty=$at; at=$(date +%s) ;;
    esac
    # Ignore anything queued before we woke (keys hit while asleep).
    [ "${at:-0}" -lt "$entered" ] && continue
    if [ "$SRC" = standalone ]; then
      case "$ev" in
        tap | swipe | hold)
          log "$ev $code ${tx:+at $tx $ty} on ${SCR:-?}"
          sa_event "$ev" "$code" "$tx" "$ty"
          arm_idle
          continue ;;
        liveoff)
          sa_live_stop
          arm_idle
          continue ;;
      esac
    fi
    case "$ev" in
      tap)
        log "tap $tx $ty"
        arm_idle
        if [ "$OFFLINE" = 1 ] && [ $(($(date +%s) - ${OFF_AT:-0})) -ge 20 ] && server_up; then
          log "server back"
          press home full # (Wi-Fi drops for a minute now and then: don't stay offline once it's back)
        elif [ "$OFFLINE" = 1 ]; then
          # No server to ask what was tapped: the left third goes back a page, the top strip retries the server.
          if [ "$ty" -lt $((SH / 13)) ]; then press home full
          elif [ "$tx" -lt $((SW / 3)) ]; then press prev full
          else press next full; fi
        elif [ "$ty" -ge "$NAV_Y" ] && [ "$tx" -lt "$NAV_ARROW" ]; then
          ack "$tx" "$ty"
          press prev full
        elif [ "$ty" -ge "$NAV_Y" ] && [ "$tx" -ge $((SW - NAV_ARROW)) ]; then
          ack "$tx" "$ty"
          press next full
        else
          ack "$tx" "$ty"
          # A second tap near the first one, with its screen already fetched: draw it now, then tell the server.
          if [ -n "$ARMQ" ] && [ -s /tmp/pre.arm.png ] &&
            [ $(((tx - ARMX) * (tx - ARMX) + (ty - ARMY) * (ty - ARMY))) -lt 3600 ]; then
            mv /tmp/pre.arm.png /tmp/pre.tap.png
          fi
          ARMQ=
          TAPQ="&x=$tx&y=$ty"
          press tap full
          TAPQ=
          if grep -q stoppress-fast /tmp/ui.png 2>/dev/null; then
            # The tap framed something. Fetch what tapping it again opens (the prefetch running now started
            # before we knew, so start it again).
            ARMQ="&x=$tx&y=$ty"; ARMX=$tx; ARMY=$ty
            stop_prefetch
            prefetch &
            PREFETCH=$!
          else
            unack
          fi
          if grep -q stoppress-mode /tmp/ui.png 2>/dev/null; then
            toggle_mode quiet
            show none full
          fi
          if grep -q stoppress-standalone /tmp/ui.png 2>/dev/null; then
            to_standalone
            arm_idle
          fi
        fi ;;
      swipe)
        log "swipe $code"
        ARMQ=
        arm_idle
        case "$code" in
          left) press next full ;;
          right) press prev full ;;
          down) press home full ;;
          up) press menu full ;;
        esac ;;
      hold)
        log "hold $tx $ty"
        arm_idle
        if [ "$tx" -lt $((SW / 5)) ] && [ "$ty" -lt $((SH / 6)) ]; then exit_to_library; else toggle_mode; fi ;;
      idle)
        [ "$code" = "$GEN" ] || continue
        [ -n "$SALIVE" ] && continue # (a timer from before the live board started: the board keeps us awake)
        log "idle, going to sleep"; disarm_idle; sweep; return ;;
      liveoff)
        sa_live_stop # (the standalone board stops itself at 20% battery)
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
  url=$(url edition.mobi)
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
  sa_live_stop >/dev/null 2>&1
  stop_live
  stop_prefetch
  rm -f /tmp/pre.*.png
  stale=3300
  while true; do
    secs=$("$DIR/next-wakeup" --schedule="$REFRESH_SCHEDULE" --timezone="$TIMEZONE")
    if [ "$secs" -gt 3600 ]; then
      put "$DIR/sleeping.png" flash # overnight
    elif [ "$SRC" = standalone ]; then
      # Woken on schedule: fetch the next bundle and show its front page. Just put down (the first time round):
      # only if the bundle is nearly an hour old; otherwise the page you were on stays, as a book's does.
      if [ $(($(date +%s) - $(cat "$DIR/state/bundle-fetched" 2>/dev/null || echo 0))) -ge "${stale:-3300}" ]; then
        if sa_sync; then
          fetch_edition
          BACK=
          sa_show front flash
        fi
        wifi off
      fi
    else
      "$DIR/wait-for-wifi.sh" "$WIFI_TEST_IP" >/dev/null 2>&1
      if show auto clean noprefetch; then
        fetch_edition
        fetch_offline
      fi
    fi
    stale=600
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
  if [ "$INPUT" = touch ]; then
    "$FBINK" -q -c -f
    "$FBINK" -q -pm -y 12 "Back to your Kindle library..."
    "$FBINK" -q -pm -y 14 "(about half a minute)"
    "$FBINK" -q -pm -y 17 "KUAL > Stop Press > Start the paper"
    "$FBINK" -q -pm -y 18 "brings the paper back."
  else
    $EIPS -c
    $EIPS 8 18 "Back to your Kindle library..."
    $EIPS 8 20 "(about half a minute)"
    $EIPS 4 23 "KUAL > Stop Press > Start the paper"
    $EIPS 4 24 "brings the paper back."
  fi
  echo 0 >$RTC
  disarm_idle
  stop_live
  stop_prefetch
  [ "$SRC" = standalone ] && lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
  pkill -f "$DIR/live.lua"
  pkill -f "tail -n 0 -f /var/log/messages"
  pkill -f "dd if=/dev/input/event"
  pkill -f "$DIR/touch.lua"
  lipc-set-prop com.lab126.powerd preventScreenSaver 0
  ui_start
  # Stop our own reader loops last (they share our command line).
  pkill -f "sh /mnt/us/stop-press/paper.sh"
  exit 0
}

log "starting"
mkdir -p "$DIR/state"
rm -f /tmp/busy
rmdir /tmp/drawlock 2>/dev/null
trap '' HUP
if [ "$INPUT" = touch ] && [ -z "$FBINK" ]; then
  log "no FBInk found: touch Kindles need it (it comes with NiLuJe's USBNetwork package)"
  exit 1
fi
log "device: $INPUT, ${SW}x${SH}, fbink ${FBINK:-none}, $SRC mode"
trap '' TERM # stopping the Kindle UI also signals whatever KUAL launched, which includes us
ui_stop
trap - TERM
lipc-set-prop com.lab126.powerd preventScreenSaver 1
start_readers
[ "$SRC" = standalone ] && [ -s "$SA/s/front.png" ] && wifi off
while true; do
  interactive
  asleep
done
