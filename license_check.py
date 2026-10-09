"""Offline license verification for VRE AC Stock.

License keys are Ed25519-signed by license_tool.py, which stays private
and never ships. The public key embedded below can only VERIFY keys — a
customer who extracts it from the exe still cannot mint licenses.

Key format:  VRE1.<payload-b64url>.<signature-b64url>
Payload:     {"app": "VRE-STOCK", "customer": ..., "machine": ...,
              "expires": "YYYY-MM-DD" | "" (perpetual),
              "issued":  "YYYY-MM-DD"}
machine:     the PC's Machine ID this key is locked to, or "*" for a
             master key that runs on any machine.
"""

import base64
import binascii
import datetime
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

APP_ID = "VRE-STOCK"

# Public half of the signing keypair (license_tool.py init prints this).
# Public by design — it verifies signatures, it cannot create them.
PUBLIC_KEY_B64 = "-9uGx6d3Y9lc3_I0c6_LXmkMzUi1XR9vuwg_R87m3Jk"

_FROZEN = getattr(sys, "frozen", False)
DATA_DIR = (Path(sys.executable).parent if _FROZEN
            else Path(__file__).resolve().parent)
LICENSE_PATH = DATA_DIR / "license.lic"
STATE_PATH = DATA_DIR / "license_state.json"

# Escape hatch for local development ONLY — ignored inside the packaged
# exe (frozen), so setting the env var can't unlock a shipped build.
DEV_BYPASS = (not _FROZEN) and os.environ.get("VRE_DEV_BYPASS") == "1"

_STATUS_CACHE = {"at": 0.0, "value": None}
_CACHE_TTL = 10.0  # seconds — a license file read per API call is wasteful


def machine_id() -> str:
    """Stable per-PC ID: Windows MachineGuid, grouped for readability.
    Falls back to a MAC+hostname hash off-Windows (dev machines)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as k:
            raw = winreg.QueryValueEx(k, "MachineGuid")[0]
    except Exception:
        seed = f"{uuid.getnode()}|{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', '')}"
        raw = hashlib.sha256(seed.encode()).hexdigest()
    raw = "".join(c for c in raw.upper() if c in "0123456789ABCDEF")
    return "-".join(raw[i:i + 4] for i in range(0, min(len(raw), 32), 4))


def _norm_machine(s: str) -> str:
    return "".join(c for c in str(s or "").upper() if c.isalnum())


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    return Ed25519PublicKey.from_public_bytes(_b64d(PUBLIC_KEY_B64))


def _verify(payload: dict, sig: bytes) -> bool:
    if PUBLIC_KEY_B64.startswith("REPLACE_"):
        return False
    try:
        _public_key().verify(
            sig, json.dumps(payload, separators=(",", ":"),
                            sort_keys=True).encode())
        return True
    except Exception:
        return False


def _parse_key(text: str):
    """-> (payload dict, signature bytes) or (None, error-string)."""
    t = "".join(str(text or "").split())
    if not t:
        return None, "empty"
    if not t.startswith("VRE1."):
        return None, "malformed"
    parts = t.split(".")
    if len(parts) != 3:
        return None, "malformed"
    try:
        payload = json.loads(_b64d(parts[1]))
        sig = _b64d(parts[2])
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None, "malformed"
    if not isinstance(payload, dict) or payload.get("app") != APP_ID:
        return None, "wrong_app"
    return payload, sig


def _check_payload(payload: dict, sig: bytes) -> dict:
    """Shared verdict logic for status() and activate()."""
    if not _verify(payload, sig):
        return {"ok": False, "state": "bad_signature",
                "error": "This license key is not genuine."}
    is_master = str(payload.get("machine") or "").strip() == "*"
    if not is_master and _norm_machine(payload.get("machine")) != _norm_machine(machine_id()):
        return {"ok": False, "state": "wrong_machine",
                "error": "This key belongs to a different computer."}
    expires = str(payload.get("expires") or "").strip()
    days_left = None
    if expires:
        try:
            exp_d = datetime.date.fromisoformat(expires)
        except ValueError:
            return {"ok": False, "state": "malformed",
                    "error": "The expiry date in this key is unreadable."}
        # Clock-rollback guard: judge expiry against the latest date the
        # app has ever seen, so rewinding Windows' clock gains nothing.
        eff = _effective_today()
        days_left = (exp_d - eff).days
        if days_left < 0:
            return {"ok": False, "state": "expired", "expires": expires,
                    "days_left": days_left,
                    "customer": payload.get("customer") or "",
                    "error": f"License expired on {_dmy(expires)}."}
    return {"ok": True, "state": "active",
            "customer": payload.get("customer") or "",
            "expires": expires, "days_left": days_left,
            "issued": payload.get("issued") or "",
            "master": is_master}


def _dmy(iso: str) -> str:
    try:
        d = datetime.date.fromisoformat(iso)
        return d.strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _effective_today() -> datetime.date:
    """today, never earlier than the date this app last ran."""
    today = datetime.date.today()
    last = today
    try:
        last = datetime.date.fromisoformat(
            json.loads(STATE_PATH.read_text(encoding="utf-8"))
            .get("last_seen", "")) or today
    except Exception:
        pass
    eff = max(today, last)
    if eff > last or not STATE_PATH.exists():
        try:
            STATE_PATH.write_text(json.dumps({"last_seen": eff.isoformat()}),
                                  encoding="utf-8")
        except Exception:
            pass
    return eff


def status(force: bool = False) -> dict:
    """License verdict for this machine, briefly cached for the middleware."""
    import time
    now = time.time()
    if not force and _STATUS_CACHE["value"] is not None \
            and now - _STATUS_CACHE["at"] < _CACHE_TTL:
        return _STATUS_CACHE["value"]

    if DEV_BYPASS:
        v = {"ok": True, "state": "dev", "customer": "DEV MODE",
             "expires": "", "days_left": None, "machine": machine_id()}
    else:
        v = _status_uncached()
    v["machine"] = machine_id()
    _STATUS_CACHE.update(at=now, value=v)
    return v


def _status_uncached() -> dict:
    try:
        text = LICENSE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"ok": False, "state": "missing",
                "error": "No license key is installed on this computer."}
    except OSError as e:
        return {"ok": False, "state": "unreadable",
                "error": f"Cannot read the license file: {e}"}
    payload, sig = _parse_key(text)
    if payload is None:
        return {"ok": False, "state": sig or "malformed",
                "error": "The installed license key is corrupted."}
    return _check_payload(payload, sig)


def activate(key_text: str) -> dict:
    """Validate a pasted key; install it when it checks out."""
    payload, sig = _parse_key(key_text)
    if payload is None:
        return {"ok": False, "state": sig or "malformed",
                "machine": machine_id(),
                "error": "That key is not a valid VRE license key."}
    verdict = _check_payload(payload, sig)
    verdict["machine"] = machine_id()
    if not verdict["ok"]:
        return verdict
    try:
        LICENSE_PATH.write_text("".join(key_text.split()) + "\n",
                                encoding="utf-8")
    except OSError as e:
        return {"ok": False, "state": "unwritable", "machine": machine_id(),
                "error": f"Key is valid but could not be saved: {e}"}
    status(force=True)
    verdict["saved_to"] = str(LICENSE_PATH)
    return verdict


if __name__ == "__main__":
    s = status(force=True)
    print("Machine ID :", s.get("machine"))
    print("State      :", s.get("state"))
    for k in ("customer", "expires", "days_left", "error"):
        if s.get(k) not in (None, ""):
            print(f"{k.capitalize():11}:", s[k])
