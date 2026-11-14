#!/usr/bin/env python3
"""
enable_carplay.py — enable wireless CarPlay / Android Auto on an Ultraviolette
ZKSWE FlyThings dash by setting "uv_app_use": true in /data/zkapp.cfg.

Cross-platform (Windows, macOS, Linux). Runtime verification is in verify_carplay.py.

What it does:
  1. Ensures an adb that works with this dash (platform-tools r28; newer adb
     breaks file transfer on this old adbd). Downloads it if missing.
  2. Connects to the dash over adb (auto-detects on the LAN, or use --ip).
  3. Backs up /data/zkapp.cfg (on the dash and locally).
  4. Adds "uv_app_use": true, preserving every other key.
  5. Stops the dash UI, installs the file, verifies byte-for-byte, restarts UI.
  6. Confirms the key persisted.

SAFETY:
  - Run with the bike PARKED, ignition on, stand down. The dash screen goes
    blank for a few seconds while the UI restarts.
  - Reversible: --restore (or verify_carplay.py --restore).
  - Do NOT run an OTA update while the "not genuine" warning is present.
  - adb over Wi-Fi drops when the dash switches to its CarPlay hotspot. Expected.

Usage:
  python3 enable_carplay.py                 # auto-detect dash, apply fix
  python3 enable_carplay.py --ip 192.168.1.16
  python3 enable_carplay.py --restore       # restore the most recent local backup
  python3 enable_carplay.py --dry-run       # show what would change, write nothing
  python3 enable_carplay.py --self-destruct # delete THIS script after a successful apply
"""
import argparse, concurrent.futures, io, json, os, platform, socket, subprocess, sys, time, zipfile
from pathlib import Path
from urllib.request import urlopen

# ----- config ---------------------------------------------------------------
ADB_PORT   = 5555
DASH_MODEL = "Zkswe_V85X_SPINOR"
CFG        = "/data/zkapp.cfg"
WORKDIR    = Path(os.environ.get("WORKDIR", Path.home() / "UVA"))
TOOLDIR    = WORKDIR / "tools" / "platform-tools"
BACKUPDIR  = WORKDIR / "backup"
PT_VER     = "r28.0.1"
SELF       = Path(__file__).resolve()

# Windows: don't open a console window for each adb call (matters in the GUI .exe)
NO_WINDOW  = {"creationflags": 0x08000000} if platform.system() == "Windows" else {}

ADB = None   # resolved adb path
S   = None   # "ip:port" of the connected dash

def log(m): print(f">> {m}", flush=True)
def die(m): print(f"ERROR: {m}", file=sys.stderr); sys.exit(1)

# ----- prerequisites --------------------------------------------------------
def adb_name():
    return "adb.exe" if platform.system() == "Windows" else "adb"

def ensure_adb():
    global ADB
    ADB = TOOLDIR / adb_name()
    if ADB.exists():
        return
    sysname = platform.system()
    osslug = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(sysname)
    if not osslug:
        die(f"unsupported OS {sysname}; install platform-tools {PT_VER} manually into {TOOLDIR}")
    url = f"https://dl.google.com/android/repository/platform-tools_{PT_VER}-{osslug}.zip"
    log(f"platform-tools {PT_VER} not found; downloading from {url}")
    (WORKDIR / "tools").mkdir(parents=True, exist_ok=True)
    try:
        data = urlopen(url, timeout=120).read()
    except Exception as e:
        die(f"download failed: {e}")
    zipfile.ZipFile(io.BytesIO(data)).extractall(WORKDIR / "tools")
    if not ADB.exists():
        die("adb still missing after download")
    if platform.system() != "Windows":
        os.chmod(ADB, 0o755)

def run(args, timeout=120, check=False):
    """Run an adb command. args is a list AFTER the adb binary."""
    cmd = [str(ADB)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, stdin=subprocess.DEVNULL, **NO_WINDOW)
    if check and r.returncode != 0:
        die(f"adb {' '.join(args)} failed: {r.stderr.strip()}")
    return r

def sh(cmd_str, timeout=120):
    """adb -s S shell <cmd_str as one string>; returns stdout with CRs stripped."""
    r = run(["-s", S, "shell", cmd_str], timeout=timeout)
    return r.stdout.replace("\r", "")

# ----- connection -----------------------------------------------------------
def reset_adb_server():
    run(["kill-server"]); time.sleep(1); run(["start-server"])

def adb_ok(target):
    r = run(["-s", target, "shell", "echo", "ok"], timeout=20)
    return "ok" in r.stdout

def local_subnet():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip.rsplit(".", 1)[0]
    except Exception:
        return None

def scan_lan(base):
    def probe(i):
        a = f"{base}.{i}"
        s = socket.socket(); s.settimeout(0.3)
        ok = s.connect_ex((a, ADB_PORT)) == 0
        s.close()
        return a if ok else None
    hits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        for r in ex.map(probe, range(2, 255)):
            if r: hits.append(r)
    return hits

def model_of(target):
    return run(["-s", target, "shell", "getprop", "ro.product.model"], timeout=20).stdout.replace("\r", "").strip()

def connect_dash(ip):
    global S
    reset_adb_server()
    if ip:
        S = f"{ip}:{ADB_PORT}"
        for _ in range(5):
            run(["connect", S], timeout=20)
            if adb_ok(S): break
            run(["disconnect", S]); time.sleep(2)
        if not adb_ok(S): die(f"could not connect to {S}")
    else:
        base = local_subnet()
        if not base: die("could not determine LAN subnet; pass --ip")
        log(f"scanning {base}.0/24 for the dash (adb port {ADB_PORT} open)...")
        found = None
        for host in scan_lan(base):
            t = f"{host}:{ADB_PORT}"
            run(["connect", t], timeout=20)
            if adb_ok(t) and model_of(t) == DASH_MODEL:
                found = t; break
        if not found: die("dash not found on LAN; pass --ip")
        S = found
    m = model_of(S)
    if m != DASH_MODEL: die(f"device at {S} is '{m}', not {DASH_MODEL}; refusing")
    log(f"connected to dash at {S} ({m})")

# ----- config helpers -------------------------------------------------------
def read_cfg():
    return sh(f"cat {CFG}")

def render(d):
    """json with the dash's formatting: 3-space indent, '\"key\" : value'."""
    return json.dumps(d, indent=3).replace('": ', '" : ') + "\n"

# ----- operations -----------------------------------------------------------
def do_restore():
    baks = sorted(BACKUPDIR.glob("zkapp.cfg.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not baks: die(f"no local backup found in {BACKUPDIR}")
    bak = baks[0]
    log(f"restoring {bak} -> dash {CFG}")
    run(["-s", S, "push", str(bak), "/tmp/zkapp.restore"], check=True)
    sh(f"setprop ctl.stop zkswe; cp /tmp/zkapp.restore {CFG}; sync; setprop ctl.start zkswe")
    log("restored. UI restarting.")

def do_apply(mode, self_destruct):
    BACKUPDIR.mkdir(parents=True, exist_ok=True)
    ts   = time.strftime("%Y%m%d_%H%M%S")
    orig = BACKUPDIR / f"zkapp.cfg.{ts}.bak"
    new  = BACKUPDIR / f"zkapp.cfg.{ts}.new"

    cur = read_cfg()
    if not cur.strip(): die(f"could not read {CFG} from dash")
    try:
        d = json.loads(cur)
    except Exception:
        die(f"current {CFG} is not valid JSON; aborting")
    orig.write_text(cur)
    log(f"backed up current config -> {orig}")
    sh(f"cp {CFG} {CFG}.bak")

    if d.get("uv_app_use") is True:
        log("uv_app_use is already true. Nothing to do.")
        return False

    d["uv_app_use"] = True
    text = render(d)
    json.loads(text)                       # sanity
    new.write_text(text)
    log(f"new config written -> {new}")

    if mode == "dry-run":
        log("DRY RUN — current vs new:")
        for a, b in zip(cur.splitlines(), text.splitlines()):
            if a != b: print(f"  - {a}\n  + {b}")
        extra = text.splitlines()[len(cur.splitlines()):]
        for b in extra: print(f"  + {b}")
        log("dry run complete; nothing written to the dash.")
        return False

    # stage + verify upload
    run(["-s", S, "push", str(new), "/tmp/zkapp.cfg.new"], check=True)
    staged = BACKUPDIR / f".staged.{os.getpid()}"
    run(["-s", S, "pull", "/tmp/zkapp.cfg.new", str(staged)])
    if staged.read_bytes() != new.read_bytes():
        staged.unlink(missing_ok=True); die("staged file mismatch; nothing installed")
    staged.unlink(missing_ok=True)
    log("staged file verified on dash")

    # stop UI, install, verify, restart UI
    log("stopping dash UI, installing config, restarting UI...")
    sh(f"setprop ctl.stop zkswe; sleep 2 2>/dev/null; cp /tmp/zkapp.cfg.new {CFG}; sync")
    written = BACKUPDIR / f".written.{os.getpid()}"
    run(["-s", S, "pull", CFG, str(written)])
    if written.read_bytes() != new.read_bytes():
        written.unlink(missing_ok=True)
        log("WARNING: written file differs from intended. Restoring backup.")
        run(["-s", S, "push", str(orig), "/tmp/zkapp.restore"], check=True)
        sh(f"cp /tmp/zkapp.restore {CFG}; sync; setprop ctl.start zkswe")
        die("install verification failed; config restored")
    written.unlink(missing_ok=True)
    sh("setprop ctl.start zkswe")

    # confirm it survived the UI restart
    time.sleep(10)
    run(["connect", S], timeout=20)
    try:
        ok = json.loads(read_cfg()).get("uv_app_use") is True
    except Exception:
        ok = False
    if ok:
        log("SUCCESS: uv_app_use is true and persisted.")
    else:
        log(f"WARNING: uv_app_use not confirmed after UI restart. Re-check: {ADB} -s {S} shell cat {CFG}")

    print(f"""
Done. Next, on the parked bike:
  1. Open the dash Link / phone-projection screen.
     The dash switches to its CarPlay hotspot; adb over Wi-Fi will drop (expected).
  2. On the iPhone, a dash Wi-Fi network (e.g. UVA-xxxx_xxxx) should appear.
  3. Connect CarPlay from the iPhone.

To check it connected:  python3 verify_carplay.py --ip {S.split(':')[0]} --start   (then --check)
To undo:                python3 enable_carplay.py --restore   (or verify_carplay.py --restore)
Backup:                 {orig}
""")
    return ok

# ----- main -----------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(add_help=True, description="enable wireless CarPlay on the UV dash")
    p.add_argument("--ip")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--restore", action="store_true")
    g.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--self-destruct", action="store_true")
    a = p.parse_args()

    ensure_adb()
    connect_dash(a.ip)

    if a.restore:
        do_restore()
        return
    mode = "dry-run" if a.dry_run else "apply"
    applied_ok = do_apply(mode, a.self_destruct)

    if a.self_destruct:
        if mode == "apply" and applied_ok:
            log(f"self-destruct: removing {SELF} (backups remain; undo via verify_carplay.py --restore)")
            try: SELF.unlink()
            except Exception as e: log(f"self-destruct failed: {e}")
        else:
            log(f"self-destruct skipped (apply did not confirm success, or mode was {mode})")

if __name__ == "__main__":
    main()
