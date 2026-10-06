"""Ready-to-read Excel export builders shared by the HTTP endpoints and the
automatic snapshot sidecars in backups/ready_exports/ — those are written on
every backup so the key views (Available Stock, Sell Record) can be opened
straight in Excel even when the app itself won't start.
"""
import datetime
import io
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

_TITLE_FILL = PatternFill("solid", fgColor="00B0F0")
_THIN = Side(style="thin", color="9E9E9E")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def build_available_workbook(records, now=None) -> bytes:
    """Per-model summary in the user's manual 'Available Stock' layout:
    NO | Model | Brand | TOTAL | Booking | Balance.
    TOTAL = physical units (in stock + booked for future dispatch);
    Balance = TOTAL - Booking = what can still be dispatched today."""
    from stock_store import IN_STOCK, SOLD, parse_datetime_safe

    now = now or datetime.datetime.now()
    stock, booked = {}, {}
    for rec in records:
        status = str(rec.get("Status") or "").strip()
        key = (str(rec.get("Brand") or "").strip() or "UNBRANDED",
               str(rec.get("Model") or "").strip())
        if status == IN_STOCK:
            stock[key] = stock.get(key, 0) + 1
        elif status == SOLD:
            dt = parse_datetime_safe(str(rec.get("Date Out") or ""))
            if dt and dt > now:
                booked[key] = booked.get(key, 0) + 1

    keys = sorted(set(stock) | set(booked),
                  key=lambda k: (k[0].upper(), k[1].upper()))

    wb = Workbook()
    ws = wb.active
    ws.title = "Available"
    ws.sheet_view.zoomScale = 85

    ws.merge_cells("A1:F1")
    t = ws["A1"]
    t.value = "Available Stock"
    t.font = Font(bold=True, size=14)
    t.fill = _TITLE_FILL
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    headers = ["NO.", "Model", "Brand", "TOTAL", "Booking", "Balance"]
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=c, value=h)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.border = _BORDER
    ws.auto_filter.ref = "A2:F2"
    ws.freeze_panes = "A3"

    num_fmt = "0.00"
    for i, (brand, model) in enumerate(keys, 1):
        r = i + 2
        total = stock.get((brand, model), 0) + booked.get((brand, model), 0)
        bking = booked.get((brand, model), 0)
        balance = total - bking
        vals = [i, model, brand, total, bking]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = _BORDER
        for c in (4, 5):
            ws.cell(row=r, column=c).number_format = num_fmt
        bal = ws.cell(row=r, column=6, value=f"=D{r}-E{r}")
        bal.number_format = num_fmt
        bal.border = _BORDER
        if balance <= 0:
            bal.font = Font(color="FF0000")

    for col, w in zip("ABCDEF", (10.3, 29.0, 11.9, 13.4, 13.9, 13.6)):
        ws.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _warranty_label(w: dict) -> str:
    """Same wording the app shows: 'Active · 366 days' / Expiring / Expired."""
    st, days = w.get("status"), w.get("daysRemaining")
    if st == "active":
        return f"Active · {days} days"
    if st == "expiring":
        return f"Expiring · {days} days"
    if st == "expired":
        return "Expired"
    return "—"


def sell_rows(track: list[dict], now=None) -> list[dict]:
    """store.track_list() dicts -> the row shape build_sell_workbook writes."""
    from stock_store import parse_datetime_safe

    now = now or datetime.datetime.now()
    rows = []
    for r in track:
        dt = parse_datetime_safe(str(r.get("dateOut") or ""))
        rows.append({
            "model": r.get("model", ""), "serial": r.get("serial", ""),
            "dateIn": r.get("dateIn", ""), "dateOut": r.get("dateOut", ""),
            "customer": r.get("customer", ""),
            "wstatus": _warranty_label(r.get("warranty") or {}),
            "wexpiry": (r.get("warranty") or {}).get("expiry", ""),
            "status": "Booked" if (dt and dt > now) else "Dispatched",
        })
    return rows


def build_sell_workbook(rows: list[dict], title: str = "Sell Records") -> bytes:
    """One row per sold unit: NO | Model | Serial | Date In | Dispatched At |
    Customer | Warranty Status | Warranty Expiry | Status."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sell Record"
    ws.sheet_view.zoomScale = 85

    headers = ["NO.", "Model", "Serial Num/Desc", "Date In", "Dispatched At",
               "Customer Destination", "Warranty Status", "Warranty Expiry",
               "Status"]
    ws.merge_cells("A1:I1")
    t = ws["A1"]
    t.value = str(title or "Sell Records")[:80]
    t.font = Font(bold=True, size=14)
    t.fill = _TITLE_FILL
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=c, value=h)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.border = _BORDER
    ws.auto_filter.ref = "A2:I2"
    ws.freeze_panes = "A3"

    for i, row in enumerate(rows, 1):
        vals = [i,
                row.get("model", ""), row.get("serial", ""),
                row.get("dateIn", ""), row.get("dateOut", ""),
                row.get("customer", ""), row.get("wstatus", ""),
                row.get("wexpiry", ""), row.get("status", "Dispatched")]
        for c, v in enumerate(vals, 1):
            ws.cell(row=i + 2, column=c, value=v).border = _BORDER

    for col, w in zip("ABCDEFGHI", (7, 24, 22, 14, 18, 24, 16, 16, 12)):
        ws.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_ready_exports(store, keep: int = 10) -> list[Path]:
    """Drop Available-Stock + Sell-Record workbooks beside the snapshots so a
    crash never leaves the user without a readable copy. Keeps the newest
    `keep` of each kind, overwriting in a loop."""
    from stock_store import DB_PATH

    out_dir = DB_PATH.parent / "backups" / "ready_exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H%M%S")

    written = []
    for prefix, data in (
        ("Available-Stock", build_available_workbook(store.records, now)),
        ("Sell-Record", build_sell_workbook(
            sell_rows(store.track_list(), now),
            f"Sell Record — {now:%Y-%m-%d %H:%M}")),
    ):
        dest = out_dir / f"{prefix}_{stamp}.xlsx"
        i = 2
        while dest.exists():
            dest = out_dir / f"{prefix}_{stamp}_{i}.xlsx"
            i += 1
        dest.write_bytes(data)
        written.append(dest)

    for prefix in ("Available-Stock", "Sell-Record"):
        olds = sorted(out_dir.glob(f"{prefix}_*.xlsx"),
                      key=lambda p: p.stat().st_mtime)
        for f in olds[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass
    return written
