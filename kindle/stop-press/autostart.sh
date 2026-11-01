#!/bin/sh
# Boot straight into the paper: "autostart.sh on" adds a tiny init script to the Kindle's system partition that
# runs boot.sh; "autostart.sh off" removes it. Run from KUAL (Stop Press > Start at boot) or over SSH.
# The hook only calls boot.sh, which lives on the USB drive, so everything else can change without touching it.
INIT=/etc/init.d/stop-press
LINK=/etc/rc5.d/S99stop-press
mount -o remount,rw / || exit 1
case "$1" in
  on)
    cat >"$INIT" <<'HOOK'
#!/bin/sh
# Starts Stop Press after boot (installed by /mnt/us/stop-press/autostart.sh).
case "$1" in
  start) [ -f /mnt/us/stop-press/boot.sh ] && (sh /mnt/us/stop-press/boot.sh </dev/null >/dev/null 2>&1 &) ;;
esac
exit 0
HOOK
    chmod 755 "$INIT"
    ln -sf ../init.d/stop-press "$LINK"
    msg="Stop Press will start at boot" ;;
  off)
    rm -f "$LINK" "$INIT"
    msg="Stop Press won't start at boot" ;;
  *)
    msg="usage: autostart.sh on|off" ;;
esac
sync
mount -o remount,ro /
echo "$msg"
