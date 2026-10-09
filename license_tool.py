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


def issue_key(machine: str, customer: str, expires: str):
    """Sign + return (key, payload). Shared by CLI, interactive and GUI.
    Raises FileNotFoundError (no private key) or ValueError (bad input)."""
    if not PRIVATE_KEY_PATH.exists():
        raise FileNotFoundError(
            f"No signing key at {PRIVATE_KEY_PATH} — run 'init' once")
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key)
    sk = load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)

    machine = machine.strip()
    if not machine:
        raise ValueError("No Machine ID given — a key needs a machine to lock to.")
    if machine != "*":
        norm = "".join(c for c in machine.upper() if c.isalnum())
        machine = "-".join(norm[i:i + 4] for i in range(0, len(norm), 4))
    payload = {
        "app": APP_ID,
        "customer": customer.strip(),
        "machine": machine,
        "expires": expires,
        "issued": datetime.date.today().isoformat(),
    }
    msg = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"VRE1.{_b64e(msg)}.{_b64e(sk.sign(msg))}", payload


def cmd_sign(args) -> int:
    expires = "" if args.perpetual else (args.expires or "")
    if getattr(args, "days", None):
        expires = (datetime.date.today()
                   + datetime.timedelta(days=args.days)).isoformat()
    if expires:
        try:
            datetime.date.fromisoformat(expires)
        except ValueError:
            print(f"Bad --expires '{expires}' — use YYYY-MM-DD.")
            return 1
    try:
        key, payload = issue_key(args.machine, args.customer, expires)
    except FileNotFoundError:
        print("No private key found — run: python license_tool.py init")
        return 1
    except ValueError as e:
        print(str(e))
        return 1

    print("License issued")
    print("--------------")
    print(f"Customer : {payload['customer'] or '(unnamed)'}")
    print(f"Machine  : {payload['machine']}")
    print(f"Expires  : {payload['expires'] or 'never (perpetual)'}")
    print(f"Issued   : {payload['issued']}")
    print("\nSend this key to the customer:\n")
    print(key)
    if payload["machine"] == "*":
        print("\n!! MASTER KEY — works on ANY computer. Do not hand it out !!")
    return 0


def _parse_expiry(text: str):
    """A bare number = that many days from now; DD/MM/YYYY or ISO also
    accepted; blank = perpetual."""
    t = text.strip()
    if not t:
        return ""
    if t.isdigit():
        return (datetime.date.today()
                + datetime.timedelta(days=int(t))).isoformat()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def cmd_interactive() -> int:
    """No-args mode: prompt for everything so the tool runs by
    double-clicking it or a bare `python license_tool.py`."""
    if not PRIVATE_KEY_PATH.exists():
        print("No signing key yet — generating your keypair once now.\n")
        if cmd_init():
            return 1
        print()
    print("=" * 46)
    print("  VRE License Tool")
    print("=" * 46)
    try:
        import license_check
        my_id = license_check.machine_id()
    except Exception:
        my_id = ""
    if my_id:
        print(f"  This PC's Machine ID: {my_id}\n")
    while True:
        try:
            machine = input("Machine ID to license (Enter = this PC): ").strip()
            customer = input("Customer / shop name: ").strip()
            while True:
                exp_in = input("License valid for how many days? "
                               "e.g. 365 (Enter = never): ").strip()
                expires = _parse_expiry(exp_in)
                if expires is not None:
                    if expires:
                        d = datetime.date.fromisoformat(expires)
                        print(f"  -> expires {d:%d/%m/%Y}")
                    break
                print("  Type a number of days, e.g. 365.")
            print()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 1
        rc = cmd_sign(argparse.Namespace(
            customer=customer, machine=machine or my_id or "",
            expires=expires, perpetual=not expires))
        if rc:
            return rc
        try:
            if input("\nIssue another key? [y/N]: ").strip().lower() != "y":
                break
        except (EOFError, KeyboardInterrupt):
            break
    return 0


def cmd_gui() -> int:
    """Default no-args mode: a small dark window for issuing keys.
    Falls back to the console prompts if Tkinter is unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return cmd_interactive()
    if not PRIVATE_KEY_PATH.exists():
        # Keypair generation is a deliberate one-time act — keep it in the
        # console flow where the warnings are unmissable.
        print("No signing key yet — run once in a console:\n"
              "    python license_tool.py init")
        return cmd_interactive()

    BG, CARD, BORDER = "#0c1218", "#141d27", "#26313d"
    FG, SUB, ACC, ACC_HOVER, ERR = "#e8eef4", "#8b98a5", "#26d07c", "#3ce18d", "#fb7185"
    MONO = ("Consolas", 10)

    try:
        import license_check
        my_id = license_check.machine_id()
    except Exception:
        my_id = ""

    root = tk.Tk()
    root.title("VRE License Generator")
    root.configure(bg=BG)
    root.resizable(False, False)

    def L(parent, text, size=10, fg=SUB, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kw)

    tk.Label(root, text="VRE LICENSE GENERATOR", bg=BG, fg=FG,
             font=("Segoe UI", 15, "bold")).pack(pady=(18, 2))
    L(root, "PRIVATE — never share this tool or license_private.pem",
      size=9, fg=ERR).pack(pady=(0, 12))

    card = tk.Frame(root, bg=CARD, highlightbackground=BORDER,
                    highlightthickness=1, bd=0)
    card.pack(padx=18, pady=(0, 14), fill="x")
    inner = tk.Frame(card, bg=CARD)
    inner.pack(padx=16, pady=16, fill="x")

    def field(row, label):
        L(inner, label, size=9, bold=True).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8 if row else 0, 3))
        e = tk.Entry(inner, bg="#0e1620", fg=FG, insertbackground=FG,
                     relief="flat", font=MONO, width=44,
                     highlightthickness=1, highlightcolor=ACC,
                     highlightbackground=BORDER)
        e.grid(row=row + 1, column=0, sticky="we", ipady=6)
        return e

    machine_e = field(0, "MACHINE ID")
    this_pc = tk.Button(inner, text="This PC", bg="#1c2836", fg=SUB,
                        relief="flat", font=("Segoe UI", 8, "bold"),
                        activebackground=BORDER, activeforeground=FG,
                        cursor="hand2", padx=10,
                        command=lambda: (machine_e.delete(0, "end"),
                                         machine_e.insert(0, my_id)))
    this_pc.grid(row=1, column=1, sticky="w", padx=(6, 0), ipady=4)
    customer_e = field(2, "CUSTOMER / SHOP NAME")
    days_e = field(4, "VALID FOR (DAYS) — blank = never expires")
    days_e.insert(0, "365")

    expiry_lbl = L(inner, "", size=9, fg=ACC)
    expiry_lbl.grid(row=6, column=0, columnspan=2, sticky="w", pady=(3, 0))

    def _echo_expiry(*_):
        iso = _parse_expiry(days_e.get())
        if iso is None:
            expiry_lbl.config(text="type a number of days, e.g. 365", fg=ERR)
        elif iso:
            d = datetime.date.fromisoformat(iso)
            expiry_lbl.config(text=f"expires on {d:%d/%m/%Y}", fg=ACC)
        else:
            expiry_lbl.config(text="never expires (perpetual)", fg=SUB)
    days_e.bind("<KeyRelease>", _echo_expiry)
    _echo_expiry()

    out = tk.Text(inner, height=4, bg="#0e1620", fg=ACC, relief="flat",
                  font=("Consolas", 9), wrap="char", state="disabled",
                  highlightthickness=1, highlightbackground=BORDER)
    out.grid(row=8, column=0, columnspan=2, sticky="we", pady=(12, 6))

    status = L(inner, "", size=9)
    status.grid(row=9, column=0, columnspan=2, sticky="w")

    btns = tk.Frame(inner, bg=CARD)
    btns.grid(row=10, column=0, columnspan=2, sticky="we", pady=(8, 0))

    gen = tk.Button(btns, text="GENERATE KEY", bg=ACC, fg="#06251a",
                    relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2",
                    activebackground=ACC_HOVER, pady=8)
    gen.pack(side="left", fill="x", expand=True)
    copy_b = tk.Button(btns, text="Copy", bg="#1c2836", fg=FG, relief="flat",
                       font=("Segoe UI", 9, "bold"), cursor="hand2",
                       activebackground=BORDER, padx=14)
    copy_b.pack(side="left", padx=(6, 0))
    save_b = tk.Button(btns, text="Save .lic", bg="#1c2836", fg=FG, relief="flat",
                       font=("Segoe UI", 9, "bold"), cursor="hand2",
                       activebackground=BORDER, padx=14)
    save_b.pack(side="left", padx=(6, 0))

    def set_out(text):
        out.config(state="normal")
        out.delete("1.0", "end")
        out.insert("1.0", text)
        out.config(state="disabled")

    def generate(*_):
        iso = _parse_expiry(days_e.get())
        if iso is None:
            status.config(text="Days must be a number, e.g. 365", fg=ERR)
            return
        try:
            key, payload = issue_key(machine_e.get(), customer_e.get(), iso)
        except (ValueError, FileNotFoundError) as e:
            status.config(text=str(e), fg=ERR)
            return
        set_out(key)
        cust = payload["customer"] or "(unnamed)"
        exp = payload["expires"]
        exp_txt = (datetime.date.fromisoformat(exp).strftime("%d/%m/%Y")
                   if exp else "never")
        if payload["machine"] == "*":
            status.config(text="!! MASTER KEY — works on ANY computer !!", fg=ERR)
        else:
            status.config(
                text=f"{cust} — expires {exp_txt}", fg=ACC)
    gen.config(command=generate)
    root.bind("<Return>", generate)

    def copy_key():
        key = out.get("1.0", "end").strip()
        if not key:
            return
        root.clipboard_clear()
        root.clipboard_append(key)
        copy_b.config(text="Copied")
        root.after(1400, lambda: copy_b.config(text="Copy"))
    copy_b.config(command=copy_key)

    def save_key():
        key = out.get("1.0", "end").strip()
        if not key:
            status.config(text="Generate a key first.", fg=ERR)
            return
        name = (customer_e.get().strip() or "license").replace(" ", "_")
        p = filedialog.asksaveasfilename(
            defaultextension=".lic", initialfile=f"{name}.lic",
            filetypes=[("License file", "*.lic"), ("Text", "*.txt")])
        if p:
            Path(p).write_text(key)
            status.config(text=f"saved: {p}", fg=ACC)
    save_b.config(command=save_key)

    L(inner, "The customer finds their Machine ID on the app's activation "
             "screen and sends it to you.", size=8).grid(
        row=11, column=0, columnspan=2, sticky="w", pady=(10, 0))

    customer_e.focus_set()
    root.mainloop()
    return 0


def main() -> int:
    if len(sys.argv) == 1:
        return cmd_gui()
    ap = argparse.ArgumentParser(
        description="VRE AC Stock private license key tool (keep private!)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="generate the signing keypair once")
    sub.add_parser("menu", help="interactive mode (same as no args)")
    s = sub.add_parser("sign", help="issue a machine-locked license key")
    s.add_argument("--customer", default="", help="customer / shop name")
    s.add_argument("--machine", required=True,
                   help="Machine ID from the app's activation screen, or '*'")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--expires", metavar="YYYY-MM-DD",
                   help="expiry date for this key")
    g.add_argument("--days", type=int, metavar="N",
                   help="expire N days from today (e.g. --days 365)")
    g.add_argument("--perpetual", action="store_true",
                   help="key never expires")
    args = ap.parse_args()
    if args.cmd == "init":
        return cmd_init()
    if args.cmd == "menu":
        return cmd_interactive()
    return cmd_sign(args)


if __name__ == "__main__":
    sys.exit(main())
