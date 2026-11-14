"""
license_key.py — offline license keys tied to one dash.

A license key is an Ed25519 signature over the dash ID, written in base32.
The app holds only the public key, so it can check keys but never make them.
Keys are made by make_license.py with the private key, which never goes in
this repo.

Dash ID: the dash's ro.serialno, or "mac-" plus its wlan0 MAC address when the
serial is missing or a known factory placeholder.
"""
import base64, binascii, json, re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Paste the line printed by `python3 make_license.py genkey` here.
PUBLIC_KEY_HEX = "31cfd6ef027d930843f76a9fb5626c01c34852cc3abfe1b60a8676003faf40b4"

CONTEXT = b"carplay-license-v1:"   # domain separation; bump to invalidate every key
JUNK_SERIALS = {"", "unknown", "0123456789abcdef", "0123456789", "000000000000"}


def dash_id(serial, mac):
    """Stable ID for the dash from `getprop ro.serialno` and wlan0's MAC."""
    s = (serial or "").strip()
    if s.lower() not in JUNK_SERIALS:
        return s
    m = (mac or "").strip().lower()
    if not m or m == "00:00:00:00:00:00":
        return None
    return "mac-" + m


def read_dash_id(sh):
    """Dash ID via a script's `sh` (adb shell) function; None if unreadable."""
    return dash_id(sh("getprop ro.serialno"), sh("cat /sys/class/net/wlan0/address 2>/dev/null"))


# ----- backups -----------------------------------------------------------------
# Undo needs no key, so it must never become a way to unlock a dash, and it must
# never put one dash's config on another:
#  - backups are kept per dash ID, in a folder that records the exact ID in
#    dash-id.txt; Undo restores only from the folder whose ID matches exactly;
#  - backups outside a dash folder (made by older versions) are never restored;
#  - Undo never restores a config that has CarPlay switched on.

ID_FILE = "dash-id.txt"


def backup_dir(base, did):
    """Folder for this dash's backups, e.g. backup/dash-SN42 (prefix: never "." or "..")."""
    return base / ("dash-" + re.sub(r"[^A-Za-z0-9._-]", "_", did))


def folder_id(folder):
    """Exact dash ID recorded in a backup folder, or None."""
    try:
        return (folder / ID_FILE).read_text().strip() or None
    except OSError:
        return None


def prepare_backup_dir(base, did):
    """Create this dash's backup folder and record its ID.

    Returns None if the folder already belongs to a different dash ID (two IDs
    that clean up to the same folder name), so the caller must stop.
    """
    d = backup_dir(base, did)
    d.mkdir(parents=True, exist_ok=True)
    have = folder_id(d)
    if have is None:
        (d / ID_FILE).write_text(did + "\n")
    elif have != did:
        return None
    return d


def enables_carplay(text):
    try:
        return json.loads(text).get("uv_app_use") is True
    except (ValueError, AttributeError):
        return True   # unreadable: treat as unsafe to restore


def pick_backup(base, did):
    """Newest restorable backup made from exactly this dash, or None."""
    if not did:
        return None
    d = backup_dir(base, did)
    if folder_id(d) != did:
        return None
    for b in sorted(d.glob("zkapp.cfg.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not enables_carplay(b.read_text()):
            return b
    return None


def normalize(key):
    """Accept keys with spaces, dashes or lower case, as people paste them."""
    return "".join(ch for ch in (key or "").upper() if ch.isalnum())


def _message(did):
    return CONTEXT + did.encode("utf-8")


def format_key(sig):
    """64-byte signature -> base32 in groups of 5, for email."""
    b = base64.b32encode(sig).decode().rstrip("=")
    return "-".join(b[i:i + 5] for i in range(0, len(b), 5))


def _decode(key):
    k = normalize(key)
    try:
        return base64.b32decode(k + "=" * (-len(k) % 8))
    except (binascii.Error, ValueError):
        return None


def is_valid(key, did, public_key_hex=None):
    """True only if `key` was signed for exactly this dash ID."""
    if not did:
        return False
    sig = _decode(key)
    if sig is None or len(sig) != 64:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex or PUBLIC_KEY_HEX))
    except ValueError:
        return False   # public key not set: no key is valid
    try:
        pub.verify(sig, _message(did))
        return True
    except InvalidSignature:
        return False
