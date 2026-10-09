"""VRE AC Stock — PRIVATE license key generator. NEVER ships, NEVER commits
license_private.pem. Whoever holds that file can mint keys for every
install, so keep it backed up somewhere safe (losing it means you can no
longer issue keys your existing installs accept).

Usage:
    python license_tool.py init
        Generate the signing keypair ONCE. Prints the public key — paste it
        into PUBLIC_KEY_B64 in license_check.py and rebuild.

    python license_tool.py sign --customer "Shop Name" --machine XXXX-XXXX-...
            [--expires 2027-12-31 | --perpetual]
        Issue a key locked to one PC. --expires is optional per key.
        --machine * issues a master key that runs on ANY computer
        (emergency use only — treat it like the private key itself).

The customer sees their Machine ID on the app's activation screen and
sends it to you; you sign a key for it and send the VRE1.… text back.
"""

import argparse
import base64
import datetime
import json
import sys
from pathlib import Path

APP_ID = "VRE-STOCK"
PRIVATE_KEY_PATH = Path(__file__).resolve().parent / "license_private.pem"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def cmd_init() -> int:
    if PRIVATE_KEY_PATH.exists():
        print(f"Keypair already exists: {PRIVATE_KEY_PATH}")
        print("Delete it first ONLY if you want to invalidate every key")
        print("ever issued (existing installs would reject new keys).")
        return 1
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    from cryptography.hazmat.primitives import serialization
    sk = Ed25519PrivateKey.generate()
    PRIVATE_KEY_PATH.write_bytes(sk.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub = _b64e(sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    print(f"Private key saved to: {PRIVATE_KEY_PATH}")
    print(">>> Keep this file SECRET and BACKED UP. Never commit it. <<<\n")
    print("Paste this into license_check.py as PUBLIC_KEY_B64:\n")
    print(f'PUBLIC_KEY_B64 = "{pub}"')
    return 0


def cmd_sign(args) -> int:
    if not PRIVATE_KEY_PATH.exists():
        print("No private key found — run: python license_tool.py init")
        return 1
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key)
    sk = load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)

    expires = "" if args.perpetual else (args.expires or "")
    if expires:
        try:
            datetime.date.fromisoformat(expires)
        except ValueError:
            print(f"Bad --expires '{expires}' — use YYYY-MM-DD.")
            return 1

    machine = args.machine.strip()
    if machine != "*":
        norm = "".join(c for c in machine.upper() if c.isalnum())
        machine = "-".join(norm[i:i + 4] for i in range(0, len(norm), 4))
    payload = {
        "app": APP_ID,
        "customer": args.customer.strip(),
        "machine": machine,
        "expires": expires,
        "issued": datetime.date.today().isoformat(),
    }
    msg = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    key = f"VRE1.{_b64e(msg)}.{_b64e(sk.sign(msg))}"

    print("License issued")
    print("--------------")
    print(f"Customer : {payload['customer'] or '(unnamed)'}")
    print(f"Machine  : {machine}")
    print(f"Expires  : {expires or 'never (perpetual)'}")
    print(f"Issued   : {payload['issued']}")
    print("\nSend this key to the customer:\n")
    print(key)
    if machine == "*":
        print("\n!! MASTER KEY — works on ANY computer. Do not hand it out !!")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="VRE AC Stock private license key tool (keep private!)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="generate the signing keypair once")
    s = sub.add_parser("sign", help="issue a machine-locked license key")
    s.add_argument("--customer", default="", help="customer / shop name")
    s.add_argument("--machine", required=True,
                   help="Machine ID from the app's activation screen, or '*'")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--expires", metavar="YYYY-MM-DD",
                   help="expiry date for this key")
    g.add_argument("--perpetual", action="store_true",
                   help="key never expires")
    args = ap.parse_args()
    return cmd_init() if args.cmd == "init" else cmd_sign(args)


if __name__ == "__main__":
    sys.exit(main())
