#!/usr/bin/env python3
"""
make_license.py — owner-only tool that makes license keys.

  python3 make_license.py genkey            # once: create the signing key
  python3 make_license.py sign <dash-id>    # per sale: print the buyer's key

The private signing key lives outside this repo, in ~/.carplay-license/
(override with CARPLAY_LICENSE_DIR). Back that folder up somewhere safe and
never commit it: anyone with it can make keys for any dash.
"""
import os, sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import license_key as lk

KEYDIR = Path(os.environ.get("CARPLAY_LICENSE_DIR", Path.home() / ".carplay-license"))
KEYFILE = KEYDIR / "signing-key.pem"


def genkey():
    if KEYFILE.exists():
        sys.exit(f"{KEYFILE} already exists. Delete it yourself first if you really want a new one: "
                 "every key made with the old one stops working in new app builds.")
    KEYDIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(f"Signing key saved to {KEYFILE}. Back it up; never commit it.")
    print("Put this line in license_key.py:")
    print(f'PUBLIC_KEY_HEX = "{pub.hex()}"')


def sign(did):
    did = did.strip()
    if not did:
        sys.exit("dash ID is empty")
    priv = serialization.load_pem_private_key(KEYFILE.read_bytes(), password=None)
    key = lk.format_key(priv.sign(lk._message(did)))
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    if pub != lk.PUBLIC_KEY_HEX:
        print("WARNING: license_key.py PUBLIC_KEY_HEX does not match this signing key; "
              "the app will reject this key.", file=sys.stderr)
    print(key)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "genkey":
        genkey()
    elif len(sys.argv) == 3 and sys.argv[1] == "sign":
        sign(sys.argv[2])
    else:
        sys.exit(__doc__)
