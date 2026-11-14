#!/usr/bin/env bash
#
# enable_carplay.sh — enable wireless CarPlay / Android Auto on an Ultraviolette
# ZKSWE FlyThings dash by setting "uv_app_use": true in /data/zkapp.cfg.
#
# Runtime verification lives in a separate script: verify_carplay.sh
#
# What it does:
#   1. Ensures prerequisites: an adb that works with this dash (platform-tools
#      r28; newer adb breaks file transfer on this old adbd), and python3.
#   2. Connects to the dash over adb (auto-detects on the LAN, or use --ip).
#   3. Backs up /data/zkapp.cfg (on the dash and locally).
#   4. Adds "uv_app_use": true, preserving every other key.
#   5. Stops the dash UI, installs the file, verifies byte-for-byte, restarts UI.
#   6. Confirms the key persisted.
#
# SAFETY:
#   - Run with the bike PARKED, ignition on, stand down. The dash screen goes
#     blank for a few seconds while the UI restarts.
#   - This edits a dash settings file. It is reversible: see --restore.
#   - Do NOT run an OTA update on this dash while the "not genuine" warning is
#     present; that is a separate, unresolved issue.
#   - adb over Wi-Fi drops when the dash switches to its CarPlay hotspot. Expected.
#
# Usage:
#   ./enable_carplay.sh                 # auto-detect dash, apply fix
#   ./enable_carplay.sh --ip 192.168.1.16
#   ./enable_carplay.sh --restore       # restore the most recent local backup
#   ./enable_carplay.sh --dry-run       # show what would change, write nothing
#   ./enable_carplay.sh --self-destruct # delete THIS script after a successful apply
#                                       #   (not on dry-run/restore/failure). Undo
#                                       #   stays available via verify_carplay.sh --restore.
#
set -euo pipefail

# absolute path to this script, resolved before anything changes directory
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# ----- config ---------------------------------------------------------------
ADB_PORT=5555
DASH_MODEL="Zkswe_V85X_SPINOR"
CFG=/data/zkapp.cfg
WORKDIR="${WORKDIR:-$HOME/UVA}"
TOOLDIR="$WORKDIR/tools/platform-tools"
BACKUPDIR="$WORKDIR/backup"
PT_VER="r28.0.1"          # platform-tools version known to work with this adbd
IP=""
MODE="apply"             # apply | restore | dry-run
SELF_DESTRUCT=0

# ----- args -----------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --ip) IP="$2"; shift 2 ;;
    --restore) MODE="restore"; shift ;;
    --dry-run) MODE="dry-run"; shift ;;
    --self-destruct) SELF_DESTRUCT=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

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
  log "adb ready: $("$ADB" version | head -1)"
}

# a single adb server only: kill any other adb so versions don't clash
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
  # confirm it really is the dash
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

# ----- apply ----------------------------------------------------------------
do_apply(){
  mkdir -p "$BACKUPDIR"
  local ts orig new staged written cur
  ts="$(date +%Y%m%d_%H%M%S)"
  orig="$BACKUPDIR/zkapp.cfg.$ts.bak"
  new="$BACKUPDIR/zkapp.cfg.$ts.new"

  # 1. pull + back up current config
  "$ADB" -s "$S" shell "cat $CFG" 2>/dev/null | tr -d '\r' > "$orig"
  [ -s "$orig" ] || die "could not read $CFG from dash"
  python3 -c "import json,sys; json.load(open('$orig'))" || die "current $CFG is not valid JSON; aborting"
  log "backed up current config -> $orig"
  "$ADB" -s "$S" shell "cp $CFG ${CFG}.bak" >/dev/null 2>&1 || true

  # 2. already enabled?
  if python3 -c "import json;exit(0 if json.load(open('$orig')).get('uv_app_use') is True else 1)"; then
    log "uv_app_use is already true. Nothing to do."
    return 0
  fi

  # 3. build new config, preserving all keys, matching the dash's formatting
  python3 - "$orig" "$new" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src))
d['uv_app_use'] = True
s = json.dumps(d, indent=3).replace('": ', '" : ') + '\n'
json.loads(s)                       # sanity check
open(dst, 'w').write(s)
PY
  log "new config written -> $new"

  if [ "$MODE" = "dry-run" ]; then
    log "DRY RUN — diff (current vs new):"
    diff "$orig" "$new" || true
    log "dry run complete; nothing written to the dash."
    return 0
  fi

  # 4. stage to /tmp and verify the upload is intact
  "$ADB" -s "$S" push "$new" /tmp/zkapp.cfg.new >/dev/null
  staged="$BACKUPDIR/.staged.$$"
  "$ADB" -s "$S" pull /tmp/zkapp.cfg.new "$staged" >/dev/null 2>&1
  cmp -s "$staged" "$new" || { rm -f "$staged"; die "staged file mismatch; nothing installed"; }
  rm -f "$staged"
  log "staged file verified on dash"

  # 5. stop UI, install, verify, restart UI
  log "stopping dash UI, installing config, restarting UI..."
  "$ADB" -s "$S" shell "setprop ctl.stop zkswe; sleep 2 2>/dev/null; cp /tmp/zkapp.cfg.new $CFG; sync" >/dev/null 2>&1 || true
  written="$BACKUPDIR/.written.$$"
  "$ADB" -s "$S" pull "$CFG" "$written" >/dev/null 2>&1
  if ! cmp -s "$written" "$new"; then
    rm -f "$written"
    log "WARNING: written file differs from intended. Restoring backup."
    "$ADB" -s "$S" push "$orig" /tmp/zkapp.restore >/dev/null
    "$ADB" -s "$S" shell "cp /tmp/zkapp.restore $CFG; sync; setprop ctl.start zkswe"
    die "install verification failed; config restored"
  fi
  rm -f "$written"
  "$ADB" -s "$S" shell "setprop ctl.start zkswe" >/dev/null 2>&1 || true

  # 6. confirm it survived the UI restart (UI rewrites this file)
  sleep 10
  "$ADB" connect "$S" >/dev/null 2>&1 || true
  cur="$("$ADB" -s "$S" shell "cat $CFG" 2>/dev/null | tr -d '\r')"
  if printf '%s' "$cur" | python3 -c "import json,sys;exit(0 if json.load(sys.stdin).get('uv_app_use') is True else 1)" 2>/dev/null; then
    log "SUCCESS: uv_app_use is true and persisted."
    APPLIED_OK=1
  else
    log "WARNING: uv_app_use not confirmed after UI restart. Re-check with: $ADB -s $S shell cat $CFG"
  fi

  cat <<EOF

Done. Next, on the parked bike:
  1. Open the dash Link / phone-projection screen.
     The dash switches to its CarPlay hotspot; adb over Wi-Fi will drop (expected).
  2. On the iPhone, a dash Wi-Fi network (e.g. UVA-xxxx_xxxx) should appear.
  3. Connect CarPlay from the iPhone.

To check it actually connected:  ./verify_carplay.sh --ip <dash-ip> --start   (then --check)
To undo:                         ./enable_carplay.sh --restore
Backup:                          $orig
EOF
}

# ----- main -----------------------------------------------------------------
APPLIED_OK=0
ensure_python
ensure_adb
connect_dash
case "$MODE" in
  restore) do_restore ;;
  *)       do_apply ;;
esac

# self-destruct only after a confirmed-successful apply (never dry-run/restore/fail)
if [ "$SELF_DESTRUCT" = 1 ]; then
  if [ "$MODE" = "apply" ] && [ "$APPLIED_OK" = 1 ]; then
    log "self-destruct: removing $SELF (backups remain; undo via verify_carplay.sh --restore)"
    rm -f -- "$SELF"
  else
    log "self-destruct skipped (apply did not confirm success, or mode was $MODE)"
  fi
fi
