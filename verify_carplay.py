#!/usr/bin/env python3
"""
verify_carplay.py — runtime check that wireless CarPlay / Android Auto actually
connects on an Ultraviolette ZKSWE FlyThings dash after enable_carplay.py.
Also holds the --restore (undo) so it survives enable_carplay.py --self-destruct.

Cross-platform (Windows, macOS, Linux).

Two steps, because adb over Wi-Fi drops the moment the dash starts its hotspot:
  1) --start : start a detached logcat recorder on the dash (internal storage,
               survives the adb drop).
  2) --check : read that log, print PASS/PARTIAL/FAIL, stop the recorder.
Between them, on the PARKED bike: open the Link screen, connect CarPlay from the
iPhone, let it sit ~15 s, then turn the dash Wi-Fi back ON so adb reconnects.

Usage:
  python3 verify_carplay.py --start
  python3 verify_carplay.py --ip 192.168.1.16 --start
  python3 verify_carplay.py --ip 192.168.1.16 --check
  python3 verify_carplay.py --ip 192.168.1.16 --restore
"""
import argparse, concurrent.futures, io, json, os, platform, re, socket, subprocess, sys, time, zipfile
from pathlib import Path
from urllib.request import urlopen

import license_key as lk

ADB_PORT   = 5555
DASH_MODEL = "Zkswe_V85X_SPINOR"
CFG        = "/data/zkapp.cfg"
WORKDIR    = Path(os.environ.get("WORKDIR", Path.home() / "UVA"))
TOOLDIR    = WORKDIR / "tools" / "platform-tools"
BACKUPDIR  = WORKDIR / "backup"
PT_VER     = "r28.0.1"

# Windows: don't open a console window for each adb call (matters in the GUI .exe)
NO_WINDOW  = {"creationflags": 0x08000000} if platform.system() == "Windows" else {}

DASH_ID = None   # ID of the connected dash, read in connect_dash()
ADB = None
S   = None

def log(m): print(f">> {m}", flush=True)
def die(m): print(f"ERROR: {m}", file=sys.stderr); sys.exit(1)

def adb_name():
    return "adb.exe" if platform.system() == "Windows" else "adb"

def ensure_adb():
    global ADB
    ADB = TOOLDIR / adb_name()
    if ADB.exists(): return
    sysname = platform.system()
    osslug = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(sysname)
    if not osslug: die(f"unsupported OS {sysname}; install platform-tools {PT_VER} into {TOOLDIR}")
    url = f"https://dl.google.com/android/repository/platform-tools_{PT_VER}-{osslug}.zip"
    log(f"platform-tools {PT_VER} not found; downloading from {url}")
    (WORKDIR / "tools").mkdir(parents=True, exist_ok=True)
    try:
        data = urlopen(url, timeout=120).read()
    except Exception as e:
        die(f"download failed: {e}")
    zipfile.ZipFile(io.BytesIO(data)).extractall(WORKDIR / "tools")
    if not ADB.exists(): die("adb still missing after download")
    if platform.system() != "Windows": os.chmod(ADB, 0o755)

def run(args, timeout=120, check=False):
    r = subprocess.run([str(ADB)] + args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **NO_WINDOW)
    if check and r.returncode != 0: die(f"adb {' '.join(args)} failed: {r.stderr.strip()}")
    return r

def sh(cmd_str, timeout=120):
    return run(["-s", S, "shell", cmd_str], timeout=timeout).stdout.replace("\r", "")

def reset_adb_server():
    run(["kill-server"]); time.sleep(1); run(["start-server"])

def adb_ok(t):
    return "ok" in run(["-s", t, "shell", "echo", "ok"], timeout=20).stdout

def local_subnet():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip.rsplit(".", 1)[0]
    except Exception:
        return None

def scan_lan(base):
    def probe(i):
        a = f"{base}.{i}"; s = socket.socket(); s.settimeout(0.3)
        ok = s.connect_ex((a, ADB_PORT)) == 0; s.close()
        return a if ok else None
    hits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        for r in ex.map(probe, range(2, 255)):
            if r: hits.append(r)
    return hits

def model_of(t):
    return run(["-s", t, "shell", "getprop", "ro.product.model"], timeout=20).stdout.replace("\r", "").strip()

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
    # Read the dash ID before any action, so every action knows which dash it is on.
    global DASH_ID
    DASH_ID = lk.read_dash_id(sh)
    if not DASH_ID: die("could not read a serial number or Wi-Fi MAC address from the dash; refusing")
    log(f"connected to dash at {S} ({m}), dash ID {DASH_ID}")

# ----- restore --------------------------------------------------------------
def do_restore():
    did = DASH_ID
    bak = lk.pick_backup(BACKUPDIR, did)
    if not bak: die(f"no backup of this dash (ID {did}) with CarPlay off found in {BACKUPDIR}")
    log(f"restoring {bak} -> dash {CFG}")
    if lk.read_dash_id(sh) != DASH_ID: die("dash ID changed since connecting; refusing to write")
    run(["-s", S, "push", str(bak), "/tmp/zkapp.restore"], check=True)
    sh(f"setprop ctl.stop zkswe; cp /tmp/zkapp.restore {CFG}; sync; setprop ctl.start zkswe")
    log("restored. UI restarting.")

# ----- verification ---------------------------------------------------------
def logpaths():
    # /mnt/extsd is the dash's built-in eMMC (shared with dashcam); tmpfs fallback
    if run(["-s", S, "shell", "ls", "-d", "/mnt/extsd"], timeout=20).returncode == 0 \
       and "No such" not in sh("ls -d /mnt/extsd"):
        d = "/mnt/extsd/cpverify"
    else:
        d = "/tmp/cpverify"   # NOTE: tmpfs, wiped on dash reboot
    return d, f"{d}/cp.txt"

def do_start(ip):
    d, f = logpaths()
    try:
        ok = json.loads(read_cfg_for_start()).get("uv_app_use") is True
    except Exception:
        ok = False
    if not ok:
        log("WARNING: uv_app_use is not true yet. Run enable_carplay.py first, or the test will FAIL.")
    sh("for p in $(ps | grep logcat | grep -v grep | awk '{print $2}'); do kill $p 2>/dev/null; done; "
       f"mkdir -p {d}; rm -f {f}; (logcat -v time </dev/null >{f} 2>&1 &)")
    time.sleep(2)
    if "logcat" not in sh("ps"):
        die("could not start the log recorder on the dash")
    log(f"log recorder running on dash -> {f}")
    ipshow = f" --ip {ip}" if ip else ""
    print(f"""
Now, on the PARKED bike:
  1. Open the dash Link / phone-projection screen.
  2. The dash starts its CarPlay hotspot; adb over Wi-Fi WILL drop (expected).
  3. On the iPhone, connect CarPlay (it should appear / auto-connect).
  4. Let it sit connected for ~15 seconds.
  5. Turn the dash Wi-Fi back ON (Settings -> Wi-Fi) so adb reconnects.
  6. Run:  python3 verify_carplay.py{ipshow} --check
""")

def read_cfg_for_start():
    return sh(f"cat {CFG}")

def do_check():
    d, f = logpaths()
    BACKUPDIR.mkdir(parents=True, exist_ok=True)
    L = BACKUPDIR / f"verify_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    r = run(["-s", S, "pull", f, str(L)])
    if r.returncode != 0 or not L.exists() or L.stat().st_size == 0:
        die(f"could not pull log {f} (was --start run? did the Link screen get opened?)")
    log(f"pulled verification log -> {L}")
    txt = L.read_text(errors="replace")

    def count(pat): return len(re.findall(pat, txt))
    established = count(r"LYLINK_LINK_ESTABLISH +wifi +(?:carplay|android auto)")
    ap_up   = count(r"\[net\] E_NET_MODE_AP success")
    ap_down = count(r"\[net\] ap disable")
    nok     = count(r"ap still nok")
    iap     = count(r"iap2_start.*ssid:.*UVA")

    print("\n---- verification summary ----")
    print(f"  LINK_ESTABLISH (carplay/AA)   : {established}")
    print(f"  hotspot up  (AP success)      : {ap_up}")
    print(f"  hotspot torn down (ap disable): {ap_down}")
    print(f"  iAP2 handshake w/ SSID        : {iap}")
    print(f"  'ap still nok' lines          : {nok}")
    print("------------------------------")

    rc = 1
    if established >= 1:
        for m in re.findall(r".*LYLINK_LINK_ESTABLISH +wifi +(?:carplay|android auto).*", txt)[-2:]:
            print(m)
        log("PASS: CarPlay/Android Auto link established."); rc = 0
    elif ap_up >= 1 and ap_up > ap_down:
        log("PARTIAL: hotspot started and stayed up, but no LINK_ESTABLISH seen.")
        log("  -> dash side works; check the iPhone (Bluetooth on, CarPlay allowed, forget+repair).")
        rc = 0
    else:
        log("FAIL: hotspot did not come up or was torn down (the pre-fix symptom).")
        log(f"  -> confirm uv_app_use is true: {ADB} -s {S} shell cat {CFG}")
        log("  -> confirm Wi-Fi was OFF before opening the Link screen (STA blocks the AP).")
        rc = 1

    sh("for p in $(ps | grep logcat | grep -v grep | awk '{print $2}'); do kill $p 2>/dev/null; done")
    log(f"log recorder stopped. Dash log kept at {f} (delete with: {ADB} -s {S} shell rm {f})")
    sys.exit(rc)

# ----- main -----------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="verify / restore wireless CarPlay on the UV dash")
    p.add_argument("--ip")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--restore", action="store_true")
    a = p.parse_args()

    ensure_adb()
    connect_dash(a.ip)

    if a.start:   do_start(a.ip)
    elif a.check: do_check()
    elif a.restore: do_restore()

if __name__ == "__main__":
    main()
