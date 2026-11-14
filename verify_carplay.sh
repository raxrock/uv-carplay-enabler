#!/usr/bin/env bash
#
# verify_carplay.sh — runtime check that wireless CarPlay / Android Auto actually
# connects on an Ultraviolette ZKSWE FlyThings dash after enable_carplay.sh.
#
# Two steps, because adb over Wi-Fi drops the moment the dash starts its CarPlay
# hotspot:
#   1) --start : start a detached logcat recorder on the dash (writes to internal
#                storage so it survives the adb drop).
#   2) --check : read that log, print a PASS/PARTIAL/FAIL verdict, stop recorder.
#
# Between the two, on the PARKED bike: open the Link screen, connect CarPlay from
# the iPhone, let it sit ~15 s, then turn the dash Wi-Fi back ON so adb reconnects.
#
# Usage:
#   ./verify_carplay.sh --start            # auto-detect dash
#   ./verify_carplay.sh --ip 192.168.1.16 --start
#   ./verify_carplay.sh --ip 192.168.1.16 --check
#   ./verify_carplay.sh --ip 192.168.1.16 --restore   # undo: restore latest backup
#
set -euo pipefail

# ----- config ---------------------------------------------------------------
ADB_PORT=5555
DASH_MODEL="Zkswe_V85X_SPINOR"
CFG=/data/zkapp.cfg
WORKDIR="${WORKDIR:-$HOME/UVA}"
TOOLDIR="$WORKDIR/tools/platform-tools"
BACKUPDIR="$WORKDIR/backup"
PT_VER="r28.0.1"
IP=""
MODE=""                  # start | check | restore
LOGDIR_DASH=""
LOGFILE_DASH=""

# ----- args -----------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --ip) IP="$2"; shift 2 ;;
    --start) MODE="start"; shift ;;
    --check) MODE="check"; shift ;;
    --restore) MODE="restore"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$MODE" ] || { echo "pass --start, --check or --restore (see --help)" >&2; exit 2; }

log(){ printf '>> %s\n' "$*"; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ----- prerequisites --------------------------------------------------------
ensure_python(){
  command -v python3 >/dev/null 2>&1 && return 0
  die "python3 not found. Install it (macOS: 'brew install python3')."
}

ensure_adb(){
  ADB="$TOOLDIR/adb"
  if [ -x "$ADB" ]; then return 0; fi
  log "platform-tools $PT_VER not found; downloading to $TOOLDIR"
  mkdir -p "$WORKDIR/tools"
  local os zip url
  case "$(uname -s)" in
    Darwin) os="darwin" ;;
    Linux)  os="linux" ;;
    *) die "unsupported OS $(uname -s); install platform-tools $PT_VER manually and set TOOLDIR" ;;
  esac
  url="https://dl.google.com/android/repository/platform-tools_${PT_VER}-${os}.zip"
  zip="$WORKDIR/tools/pt.zip"
  command -v curl >/dev/null 2>&1 || die "curl not found"
  curl -fsSL -o "$zip" "$url" || die "download failed: $url"
  command -v unzip >/dev/null 2>&1 || die "unzip not found"
  unzip -q -o "$zip" -d "$WORKDIR/tools" || die "unzip failed"
  rm -f "$zip"
  [ -x "$ADB" ] || die "adb still missing after download"
}

reset_adb_server(){
  pkill -9 adb 2>/dev/null || true
  sleep 1
  "$ADB" start-server >/dev/null 2>&1 || true
}

# ----- connection -----------------------------------------------------------
adb_ok(){ "$ADB" -s "$1" shell echo ok 2>/dev/null | tr -d '\r' | grep -q ok; }

connect_dash(){
  reset_adb_server
  if [ -n "$IP" ]; then
    S="$IP:$ADB_PORT"
    for i in 1 2 3 4 5; do "$ADB" connect "$S" >/dev/null 2>&1; adb_ok "$S" && break; "$ADB" disconnect "$S" >/dev/null 2>&1 || true; sleep 2; done
    adb_ok "$S" || die "could not connect to $S"
  else
    log "scanning LAN for the dash (adb port $ADB_PORT open)..."
    local base host found=""
    base="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
    base="${base%.*}"
    [ -n "$base" ] || die "could not determine LAN subnet; pass --ip"
    for i in $(seq 2 254); do
      ( nc -z -G 1 "$base.$i" "$ADB_PORT" 2>/dev/null && echo "$base.$i" ) &
    done > "$WORKDIR/.scan.$$" ; wait
    while read -r host; do
      [ -n "$host" ] || continue
      "$ADB" connect "$host:$ADB_PORT" >/dev/null 2>&1
      if adb_ok "$host:$ADB_PORT" && \
         [ "$("$ADB" -s "$host:$ADB_PORT" shell getprop ro.product.model 2>/dev/null | tr -d '\r')" = "$DASH_MODEL" ]; then
        found="$host:$ADB_PORT"; break
      fi
    done < "$WORKDIR/.scan.$$"
    rm -f "$WORKDIR/.scan.$$"
    [ -n "$found" ] || die "dash not found on LAN; pass --ip"
    S="$found"
  fi
  local model
  model="$("$ADB" -s "$S" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
  [ "$model" = "$DASH_MODEL" ] || die "device at $S is '$model', not $DASH_MODEL; refusing"
  log "connected to dash at $S ($model)"
}

# ----- restore --------------------------------------------------------------
do_restore(){
  local bak
  bak="$(ls -t "$BACKUPDIR"/zkapp.cfg.*.bak 2>/dev/null | head -1 || true)"
  [ -n "$bak" ] || die "no local backup found in $BACKUPDIR"
  log "restoring $bak -> dash $CFG"
  "$ADB" -s "$S" push "$bak" /tmp/zkapp.restore >/dev/null
  "$ADB" -s "$S" shell "setprop ctl.stop zkswe; cp /tmp/zkapp.restore $CFG; sync; setprop ctl.start zkswe"
  log "restored. UI restarting."
}

# ----- verification ---------------------------------------------------------
# pick a log path on the dash that survives an adb drop (internal storage,
# else tmpfs). /mnt/extsd here is the dash's built-in eMMC (shared with dashcam).
resolve_logdir(){
  if "$ADB" -s "$S" shell 'ls -d /mnt/extsd' >/dev/null 2>&1; then
    LOGDIR_DASH=/mnt/extsd/cpverify
  else
    LOGDIR_DASH=/tmp/cpverify      # NOTE: tmpfs, wiped on dash reboot
  fi
  LOGFILE_DASH="$LOGDIR_DASH/cp.txt"
}

do_start(){
  resolve_logdir
  local cur
  cur="$("$ADB" -s "$S" shell "cat $CFG" 2>/dev/null | tr -d '\r')"
  if ! printf '%s' "$cur" | python3 -c "import json,sys;exit(0 if json.load(sys.stdin).get('uv_app_use') is True else 1)" 2>/dev/null; then
    log "WARNING: uv_app_use is not true yet. Run './enable_carplay.sh' first, or the test will FAIL."
  fi
  "$ADB" -s "$S" shell "for p in \$(ps | grep logcat | grep -v grep | awk '{print \$2}'); do kill \$p 2>/dev/null; done; mkdir -p $LOGDIR_DASH; rm -f $LOGFILE_DASH; (logcat -v time </dev/null >$LOGFILE_DASH 2>&1 &)" >/dev/null 2>&1
  sleep 2
  if "$ADB" -s "$S" shell "ps | grep -q '[l]ogcat'" 2>/dev/null; then
    log "log recorder running on dash -> $LOGFILE_DASH"
  else
    die "could not start the log recorder on the dash"
  fi
  cat <<EOF

Now, on the PARKED bike:
  1. Open the dash Link / phone-projection screen.
  2. The dash starts its CarPlay hotspot; adb over Wi-Fi WILL drop (expected).
  3. On the iPhone, connect CarPlay (it should appear / auto-connect).
  4. Let it sit connected for ~15 seconds.
  5. Turn the dash Wi-Fi back ON (Settings -> Wi-Fi) so adb reconnects.
  6. Run:  ./verify_carplay.sh${IP:+ --ip $IP} --check
EOF
}

do_check(){
  resolve_logdir
  local L="$BACKUPDIR/verify_$(date +%Y%m%d_%H%M%S).txt"
  mkdir -p "$BACKUPDIR"
  "$ADB" -s "$S" pull "$LOGFILE_DASH" "$L" >/dev/null 2>&1 || die "could not pull log $LOGFILE_DASH (was --start run?)"
  [ -s "$L" ] || die "log is empty; did the Link screen get opened?"
  log "pulled verification log -> $L"

  # Log signatures found during reverse-engineering:
  #   PASS   : LYLINK_LINK_ESTABLISH wifi carplay   (or android auto)
  #   PARTIAL: hotspot came up and stayed (E_NET_MODE_AP success, no ap disable)
  #   FAIL   : 'ap still nok' loops and/or 'ap disable' right after 'ap enable'
  local established ap_up ap_down nok iap rc
  established="$(grep -cE 'LYLINK_LINK_ESTABLISH +wifi +(carplay|android auto)' "$L" || true)"
  ap_up="$(grep -cE '\[net\] E_NET_MODE_AP success' "$L" || true)"
  ap_down="$(grep -cE '\[net\] ap disable' "$L" || true)"
  nok="$(grep -cE 'ap still nok' "$L" || true)"
  iap="$(grep -cE 'iap2_start.*ssid:.*UVA' "$L" || true)"

  echo
  echo "---- verification summary ----"
  echo "  LINK_ESTABLISH (carplay/AA)   : $established"
  echo "  hotspot up  (AP success)      : $ap_up"
  echo "  hotspot torn down (ap disable): $ap_down"
  echo "  iAP2 handshake w/ SSID        : $iap"
  echo "  'ap still nok' lines          : $nok"
  echo "------------------------------"

  if [ "$established" -ge 1 ]; then
    grep -nE 'LYLINK_LINK_ESTABLISH +wifi +(carplay|android auto)' "$L" | tail -2
    log "PASS: CarPlay/Android Auto link established."
    rc=0
  elif [ "$ap_up" -ge 1 ] && [ "$ap_up" -gt "$ap_down" ]; then
    log "PARTIAL: hotspot started and stayed up, but no LINK_ESTABLISH seen."
    log "  -> dash side works; check the iPhone (Bluetooth on, CarPlay allowed, forget+repair)."
    rc=0
  else
    log "FAIL: hotspot did not come up or was torn down (the pre-fix symptom)."
    log "  -> confirm uv_app_use is true: $ADB -s $S shell cat $CFG"
    log "  -> confirm Wi-Fi was OFF before opening the Link screen (STA blocks the AP)."
    rc=1
  fi

  "$ADB" -s "$S" shell "for p in \$(ps | grep logcat | grep -v grep | awk '{print \$2}'); do kill \$p 2>/dev/null; done" >/dev/null 2>&1 || true
  log "log recorder stopped. Dash log kept at $LOGFILE_DASH (delete with: $ADB -s $S shell rm $LOGFILE_DASH)"
  return "$rc"
}

# ----- main -----------------------------------------------------------------
ensure_python
ensure_adb
connect_dash
case "$MODE" in
  start)   do_start ;;
  check)   do_check ;;
  restore) do_restore ;;
esac
