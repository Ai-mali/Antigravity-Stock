"""Excel-backed store for VRE AC Stock.

One workbook (daikin_stock.xlsx, next to the app) holds:
  MasterRecord — one row per physical unit:
      Brand | Model | Serial | Date In | Status | Customer | Date Out
  Brands       — the brand list (Daikin, LG, Panasonic, ...)
  ModelBrands  — exact full Model string -> Brand (never prefix matching)
  Returns      — Serial | Model | Customer | Reason | Condition | Notes |
                 Action | Date
  ActivityLog  — Timestamp | Action | Model | Serials Count | Details | Status

Statuses: "In Stock" | "Sold" | "Quarantined".
A restocked unit goes back to "In Stock"; a quarantined unit stays out of
both Available Stock and the Track List but keeps its record.

Existing files from the Type-era schema are migrated automatically:
sheet Types -> Brands, ModelTypes -> ModelBrands, header Type -> Brand.
Supplier column is automatically migrated and removed if present.
"""

import datetime
from pathlib import Path
import shutil
import sys

from openpyxl import Workbook, load_workbook

if getattr(sys, "frozen", False):
    DB_PATH = Path(sys.executable).parent / "daikin_stock.xlsx"
else:
    DB_PATH = Path(__file__).with_name("daikin_stock.xlsx")
BACKUP_DIR = DB_PATH.parent / "backups"
DO_DIR = DB_PATH.parent / "delivery_orders"

RECORD_HEADER = ["Brand", "Model", "Serial",
                 "Date In", "Status", "Customer", "Date Out"]
RETURN_HEADER = ["Serial", "Model", "Customer", "Reason",
                 "Condition", "Notes", "Action", "Date", "Date Out"]
ACTIVITY_HEADER = ["Timestamp", "Action", "Model",
                   "Serials Count", "Details", "Status"]

BRAND_COLORS = ["#26d07c", "#3b82f6", "#f59e0b", "#a78bfa",
                "#f87171", "#38bdf8", "#fb923c", "#4ade80",
                "#e879f9", "#facc15", "#2dd4bf", "#94a3b8"]

IN_STOCK = "In Stock"
SOLD = "Sold"
QUARANTINED = "Quarantined"


def parse_date_safe(d_str: str) -> datetime.date | None:
    """Parse various date formats (MM/DD/YYYY, YYYY-MM-DD, DD/MM/YYYY)."""
    if not d_str or not isinstance(d_str, str):
        return None
    s = d_str.strip().split(" ")[0].replace(".", "/").replace("-", "/")
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        if len(parts[0]) == 4:  # YYYY/MM/DD
            return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
        p0, p1, p2 = int(parts[0]), int(parts[1]), int(parts[2])
        if p2 < 100:
            p2 += 2000
        if 1 <= p0 <= 12 and 1 <= p1 <= 31:  # MM/DD/YYYY (Daikin default)
            return datetime.date(p2, p0, p1)
        elif 1 <= p1 <= 12 and 1 <= p0 <= 31:  # DD/MM/YYYY
            return datetime.date(p2, p1, p0)
    except Exception:
        pass
    return None


def _add_months(d: datetime.date, months: int) -> datetime.date:
    try:
        year = d.year + (d.month + months - 1) // 12
        month = (d.month + months - 1) % 12 + 1
        day = min(d.day, 28) if month == 2 else min(d.day, 30) if month in (4, 6, 9, 11) else d.day
        return datetime.date(year, month, day)
    except Exception:
        return d + datetime.timedelta(days=int(months * 30.4375))


def compute_warranty(date_out_str: str, months: int = 12) -> dict:
    """Calculate warranty status, expiry date, and days remaining from Date Out."""
    d = parse_date_safe(date_out_str)
    if not d:
        return {"status": "none", "expiry": "", "daysRemaining": None, "months": months}
    expiry = _add_months(d, months)
    warn_start = _add_months(d, months - 1)  # expiring window: last calendar month

    today = datetime.date.today()
    days_left = (expiry - today).days
    if today > expiry:
        status = "expired"
    elif today >= warn_start:
        status = "expiring"
    else:
        status = "active"

    return {
        "status": status,
        "expiry": expiry.strftime("%m/%d/%Y"),
        "daysRemaining": days_left,
        "months": months
    }

def cleanup_do_photos(days: int = 3) -> int:
    """Delete delivery order photos older than `days` days, oldest first."""
    if not DO_DIR.exists():
        return 0
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    deleted = 0
    photos = sorted(DO_DIR.glob("*.*"), key=lambda p: p.stat().st_mtime)
    for p in photos:
        if p.is_file():
            try:
                mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
                if mtime < cutoff:
                    p.unlink()
                    deleted += 1
            except Exception:
                pass
    return deleted


def list_do_photos() -> list[dict]:
    """List DO photos from the last 3 days, newest first."""
    cleanup_do_photos(days=3)
    if not DO_DIR.exists():
        return []
    photos = sorted(DO_DIR.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in photos:
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            st = p.stat()
            dt = datetime.datetime.fromtimestamp(st.st_mtime)
            out.append({
                "filename": p.name,
                "size": st.st_size,
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": dt.strftime("%m/%d/%Y")
            })
    return out


def save_do_photo(img_bytes: bytes, original_name: str = "do_scan.jpg") -> str:
    """Save scanned Delivery Order photo locally, prune files older than 3 days, return filename."""
    DO_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_do_photos(days=3)
    ext = Path(original_name).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_stem = "".join(c for c in Path(original_name).stem if c.isalnum() or c in ("-", "_"))[:30] or "scan"
    dest_name = f"DO_{ts}_{clean_stem}{ext}"
    dest_path = DO_DIR / dest_name
    dest_path.write_bytes(img_bytes)
    return dest_name


class StockStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.wb = None
        self.recs = self.brands_sheet = self.map_sheet = self.ret_sheet = None
        self.act_sheet = None
        self.records: list[dict] = []            # parsed MasterRecord rows
        self.brands: list[str] = []              # ordered Brand names
        self.model_to_brand: dict[str, str] = {}  # UPPER(model) -> Brand
        self.returns: list[dict] = []            # parsed Returns rows
        self.activities: list[dict] = []         # parsed ActivityLog rows
        self._serial_set: set[str] = set()       # lowercase serials
        self.load()

    # ---------------------------------------------------------- load/save
    def load(self):
        self._schema_dirty = not self.path.exists()
        if self.path.exists():
            self.wb = load_workbook(self.path)
        else:
            self.wb = Workbook()
            self.wb.active.title = "MasterRecord"
        self._migrate_schema()
        self.recs = self._sheet("MasterRecord", RECORD_HEADER)
        self.brands_sheet = self._sheet("Brands", ["Brand"])
        self.map_sheet = self._sheet("ModelBrands", ["Model", "Brand"])
        self.ret_sheet = self._sheet("Returns", RETURN_HEADER)
        self.act_sheet = self._sheet("ActivityLog", ACTIVITY_HEADER)

        self.records, self.brands, self.model_to_brand = [], [], {}
        self.returns, self.activities, self._serial_set = [], [], set()
        for idx, row in enumerate(
                self.recs.iter_rows(min_row=2, values_only=True), start=2):
            if not row:
                continue
            rec = dict(zip(RECORD_HEADER,
                           ("" if v is None else v for v in row)))
            if not rec.get("Serial"):
                continue
            rec["_row"] = idx  # Excel row, so updates hit the right cells
            self.records.append(rec)
            self._serial_set.add(str(rec["Serial"]).strip().lower())
        for row in self.brands_sheet.iter_rows(min_row=2, values_only=True):
            if row and row[0] and row[0] not in self.brands:
                self.brands.append(str(row[0]))
        for row in self.map_sheet.iter_rows(min_row=2, values_only=True):
            if row and row[0] and row[1]:
                self.model_to_brand[str(row[0]).upper()] = str(row[1])
        for row in self.ret_sheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] in (None, ""):
                continue
            self.returns.append(dict(
                zip(RETURN_HEADER, ("" if v is None else v for v in row))))
        for row in self.act_sheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] in (None, ""):
                continue
            self.activities.append(dict(
                zip(ACTIVITY_HEADER, ("" if v is None else v for v in row))))
        if self._schema_dirty:
            self.save(backup=False)

    def _migrate_schema(self):
        """Rename the Type-era sheets/column to Brand, and remove Supplier column if present."""
        names = self.wb.sheetnames
        if "Types" in names and "Brands" not in names:
            self.wb["Types"].title = "Brands"
            self._schema_dirty = True
        if "ModelTypes" in names and "ModelBrands" not in names:
            self.wb["ModelTypes"].title = "ModelBrands"
            self._schema_dirty = True
        if "MasterRecord" in names:
            ws = self.wb["MasterRecord"]
            first_val = str(ws.cell(row=1, column=1).value or "").strip().lower()
            if first_val == "supplier":
                ws.delete_cols(1, 1)
                self._schema_dirty = True
            hdr = ws.cell(row=1, column=1).value
            if str(hdr or "").strip() == "Type":
                ws.cell(row=1, column=1, value="Brand")
                self._schema_dirty = True

    def _sheet(self, name: str, header: list[str]):
        if name in self.wb.sheetnames:
            ws = self.wb[name]
        else:
            ws = self.wb.create_sheet(name)
            self._schema_dirty = True
        first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True),
                     None)
        if first is None or all(v is None for v in first):
            # written cell-by-cell: append() would leave an empty leading row
            for col, title in enumerate(header, start=1):
                ws.cell(row=1, column=col, value=title)
            self._schema_dirty = True
        else:
            # Backfill any newly-added trailing columns on existing sheets
            for col, title in enumerate(header, start=1):
                if ws.cell(row=1, column=col).value in (None, ""):
                    ws.cell(row=1, column=col, value=title)
                    self._schema_dirty = True
        return ws

    def _backup(self):
        """Make an automatic snapshot of daikin_stock.xlsx before overwriting."""
        if not self.path.exists():
            return
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = BACKUP_DIR / f"daikin_stock_{now_str}.xlsx"
            shutil.copy2(self.path, dest)
            self._prune_backups(keep=30)
        except Exception:
            pass

    def _prune_backups(self, keep: int = 30):
        if not BACKUP_DIR.exists():
            return
        files = sorted(BACKUP_DIR.glob("daikin_stock_*.xlsx"),
                       key=lambda p: p.stat().st_mtime)
        if len(files) > keep:
            for f in files[:-keep]:
                try:
                    f.unlink()
                except Exception:
                    pass

    def save(self, backup: bool = False):
        if backup:
            self._backup()
        self.wb.save(self.path)

    def log_activity(self, action: str, model: str = "", count: int = 0,
                     details: str = "", status: str = "OK"):
        """Record an action in the ActivityLog sheet and memory."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {"Timestamp": ts, "Action": action, "Model": model,
                 "Serials Count": count, "Details": details, "Status": status}
        self.activities.append(entry)
        if self.act_sheet is not None:
            self.act_sheet.append([ts, action, model, count, details, status])

    # ---------------------------------------------------------- backups
    def list_backups(self) -> list[dict]:
        if not BACKUP_DIR.exists():
            return []
        files = sorted(BACKUP_DIR.glob("daikin_stock_*.xlsx"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for f in files:
            st = f.stat()
            out.append({
                "filename": f.name,
                "size": st.st_size,
                "modified": datetime.datetime.fromtimestamp(
                    st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            })
        return out

    def manual_backup(self) -> dict:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"daikin_stock_{now_str}.xlsx"
        if self.path.exists():
            shutil.copy2(self.path, dest)
        else:
            self.save(backup=False)
            shutil.copy2(self.path, dest)
        self.log_activity("Manual Backup", details=f"Created backup {dest.name}")
        self.save(backup=False)
        st = dest.stat()
        return {
            "filename": dest.name,
            "size": st.st_size,
            "modified": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def restore_backup(self, filename: str) -> tuple[bool, str]:
        safe_name = Path(filename).name
        src = BACKUP_DIR / safe_name
        if not src.exists():
            return False, f"Backup file '{safe_name}' not found"
        self._backup()  # snapshot current before restoring
        try:
            shutil.copy2(src, self.path)
            self.load()
            self.log_activity("Restore", details=f"Restored from {safe_name}")
            self.save(backup=False)
            return True, ""
        except Exception as ex:
            return False, str(ex)

    def activity_log(self, limit: int = 100) -> list[dict]:
        """Return newest activities first."""
        return list(reversed(self.activities[-limit:]))

    # ---------------------------------------------------------- queries
    def brand_for(self, model: str) -> str:
        return self.model_to_brand.get(model.strip().upper(), "")

    def find_dupes(self, serials: list[str]) -> list[str]:
        return [s for s in serials
                if s.strip().lower() in self._serial_set]

    def _brand_color(self, name: str) -> str:
        try:
            return BRAND_COLORS[self.brands.index(name) % len(BRAND_COLORS)]
        except ValueError:
            # Brand present on records but missing from the Brands sheet:
            # auto-assign the first palette color no registered brand uses,
            # falling back to a name hash when the palette is exhausted.
            used = {BRAND_COLORS[i % len(BRAND_COLORS)]
                    for i in range(len(self.brands))}
            for c in BRAND_COLORS:
                if c not in used:
                    return c
            return BRAND_COLORS[sum(map(ord, name)) % len(BRAND_COLORS)]

    def inventory(self) -> dict:
        """In-stock units grouped Brand -> Model -> serials (UI shape)."""
        inv: dict[str, dict] = {}
        # Only units that sat in Quarantine then were released are tagged 2nd.
        # A plain restock (e.g. cancelled order, like-new) is not second-hand.
        restocked = {str(r["Serial"]).strip().lower() for r in self.returns
                     if str(r.get("Action", "")).strip() == "Restocked"
                     and "released from quarantine" in str(r.get("Notes", "")).lower()}
        for rec in self.records:
            if str(rec["Status"]).strip() != IN_STOCK:
                continue
            model = str(rec["Model"]).strip()
            brand = str(rec["Brand"]).strip() or "UNBRANDED"
            b = inv.setdefault(brand, {"color": self._brand_color(brand),
                                       "open": True, "models": {}})
            m = b["models"].setdefault(
                model, {"dateIn": str(rec["Date In"]), "serials": [], "units": {}})
            s = str(rec["Serial"]).strip()
            m["serials"].append(s)
            m["units"][s] = {
                "dateIn": str(rec.get("Date In", "")).strip(),
                "secondHand": s.lower() in restocked
            }
        # keep zero-stock brands visible too
        for brand in self.brands:
            inv.setdefault(brand, {"color": self._brand_color(brand),
                                   "open": True, "models": {}})
        for b in inv.values():
            for i, m in enumerate(sorted(b["models"]), start=1):
                b["models"][m]["idx"] = f"{i:02d}"
        return dict(sorted(inv.items()))

    def track_list(self) -> list[dict]:
        """Sold units, newest first, with warranty information."""
        out = [{"model": str(r["Model"]), "serial": str(r["Serial"]),
                "dateIn": str(r["Date In"]), "dateOut": str(r["Date Out"]),
                "customer": str(r["Customer"]), "status": str(r["Status"]),
                "warranty": compute_warranty(str(r["Date Out"]))}
               for r in self.records if str(r["Status"]).strip() == SOLD]
        return out

    def returns_list(self) -> list[dict]:
        return [{"serial": str(r["Serial"]), "model": str(r["Model"]),
                 "customer": str(r["Customer"]), "reason": str(r["Reason"]),
                 "condition": str(r["Condition"]), "notes": str(r["Notes"]),
                 "action": str(r["Action"]), "date": str(r["Date"]),
                 "dateOut": str(r.get("Date Out", "")),
                 "warranty": compute_warranty(str(r.get("Date Out", "")))}
                for r in self.returns]

    def customer_history(self) -> list[dict]:
        """Unique customers ranked by order frequency and recency."""
        stats = {}
        for r in self.records:
            if str(r.get("Status", "")).strip() != SOLD:
                continue
            cust = str(r.get("Customer", "")).strip()
            if not cust:
                continue
            date_out = str(r.get("Date Out", "")).strip()
            if cust not in stats:
                stats[cust] = {"name": cust, "count": 0, "lastDate": date_out}
            stats[cust]["count"] += 1
            if date_out and date_out > stats[cust]["lastDate"]:
                stats[cust]["lastDate"] = date_out

        return sorted(stats.values(),
                      key=lambda x: (x["count"], x["lastDate"]),
                      reverse=True)

    def in_stock_units(self) -> list[dict]:
        """Return all physical units currently In Stock."""
        out = []
        for r in self.records:
            if str(r.get("Status", "")).strip() == IN_STOCK:
                out.append({
                    "brand": str(r.get("Brand", "")),
                    "model": str(r.get("Model", "")),
                    "serial": str(r.get("Serial", "")),
                    "dateIn": str(r.get("Date In", ""))
                })
        return out

    # ---------------------------------------------------------- mutations
    def stock_in(self, model: str, serials: list[str],
                 date_in: str):
        """Shared save path for manual entry and scanned rows."""
        serials = [s.strip() for s in serials if s.strip()]
        if not model.strip() or not serials:
            return [], serials, True
        dupes, new_serials = self._split_dupes(serials)
        brand = self.brand_for(model)
        if not brand:
            return [], dupes, False
        self._write_rows(model.strip(), brand,
                         new_serials, date_in)
        return new_serials, dupes, True

    def stock_in_with_brand(self, model: str, serials: list[str],
                            date_in: str, brand: str):
        """Completes stock_in for a Model the user just assigned a Brand."""
        brand = brand.strip()
        if not brand:
            return [], serials
        self.assign_brand(model, brand)
        dupes, new_serials = self._split_dupes(
            [s.strip() for s in serials if s.strip()])
        self._write_rows(model.strip(), brand,
                         new_serials, date_in)
        return new_serials, dupes

    def _split_dupes(self, serials):
        """Known serials AND repeated serials inside the same batch are dupes.
        However, non-serial synthetic unit IDs (containing ' #') automatically
        increment to the next unique sequence number rather than being rejected as duplicates.
        """
        seen, dupes, new_serials = set(), [], []
        for raw_s in serials:
            s = str(raw_s).strip()
            if " #" in s:
                # Auto-increment synthetic non-serial part IDs until unique
                prefix, num_str = s.rsplit(" #", 1)
                try:
                    num = int(num_str)
                except ValueError:
                    num = 1
                cand = f"{prefix} #{num}"
                while cand.lower() in seen or cand.lower() in self._serial_set:
                    num += 1
                    cand = f"{prefix} #{num}"
                new_serials.append(cand)
                seen.add(cand.lower())
                continue

            if s.lower() in seen or s.lower() in self._serial_set:
                dupes.append(s)
            else:
                new_serials.append(s)
            seen.add(s.lower())
        return dupes, new_serials

    def _write_rows(self, model, brand, serials, date_in):
        for s in serials:
            self.recs.append([brand, model, s, date_in,
                              IN_STOCK, "", ""])
            self.records.append({"Brand": brand,
                                 "Model": model, "Serial": s,
                                 "Date In": date_in, "Status": IN_STOCK,
                                 "Customer": "", "Date Out": "",
                                 "_row": self.recs.max_row})
            self._serial_set.add(s.lower())
        if serials:
            self.log_activity("Stock In", model=model, count=len(serials),
                              details=f"Brand: {brand} | {len(serials)} units added")
            self.save(backup=True)

    def stock_out(self, serials: list[str], customer: str, date_out: str):
        """Mark units Sold with one Customer + Date Out for the whole cart."""
        wanted = {s.strip().lower() for s in serials if s.strip()}
        done = []
        models_sold = set()
        for rec in self.records:
            if (str(rec["Serial"]).strip().lower() not in wanted
                    or str(rec["Status"]).strip() != IN_STOCK):
                continue
            rec["Status"] = SOLD
            rec["Customer"] = customer
            rec["Date Out"] = date_out
            models_sold.add(str(rec["Model"]))
            row = rec["_row"]
            self.recs.cell(row=row, column=5, value=SOLD)
            self.recs.cell(row=row, column=6, value=customer)
            self.recs.cell(row=row, column=7, value=date_out)
            done.append(str(rec["Serial"]))
        if done:
            model_summary = ", ".join(sorted(models_sold))
            self.log_activity("Stock Out", model=model_summary, count=len(done),
                              details=f"Customer: {customer} | {len(done)} units sold")
            self.save(backup=True)
        return done

    def create_return(self, serial: str, reason: str, condition: str,
                      notes: str, action: str, date: str):
        """Return a sold unit: action 'restock' puts it back In Stock,
        'quarantine' pulls it aside. Returns (record, error)."""
        key = serial.strip().lower()
        rec = next((r for r in self.records
                    if str(r["Serial"]).strip().lower() == key
                    and str(r["Status"]).strip() == SOLD), None)
        if rec is None:
            return None, "No sold unit found for serial " + serial
        action_label = "Restocked" if action == "restock" else "Quarantined"
        date_out = str(rec.get("Date Out", "")).strip()
        self.ret_sheet.append([str(rec["Serial"]), str(rec["Model"]),
                               str(rec["Customer"]), reason, condition,
                               notes, action_label, date, date_out])
        self.returns.append({
            "Serial": rec["Serial"], "Model": rec["Model"],
            "Customer": rec["Customer"], "Reason": reason,
            "Condition": condition, "Notes": notes,
            "Action": action_label, "Date": date, "Date Out": date_out})
        row = rec["_row"]
        if action == "restock":
            rec["Status"] = IN_STOCK
            rec["Customer"] = ""
            rec["Date Out"] = ""
            self.recs.cell(row=row, column=5, value=IN_STOCK)
            self.recs.cell(row=row, column=6, value="")
            self.recs.cell(row=row, column=7, value="")
        else:
            rec["Status"] = QUARANTINED
            self.recs.cell(row=row, column=5, value=QUARANTINED)
        self.log_activity("Return", model=str(rec["Model"]), count=1,
                          details=f"Serial: {rec['Serial']} | Customer: {rec['Customer']} | Reason: {reason} | Action: {action_label}")
        self.save(backup=True)
        return rec, None

    def release_quarantine(self, serial: str, date: str) -> tuple:
        """Release a quarantined unit back to In Stock after inspection.
        Flips the latest 'Quarantined' returns entry to 'Restocked' so the
        unit keeps its second-hand flag. Returns (record, error)."""
        key = serial.strip().lower()
        rec = next((r for r in self.records
                    if str(r["Serial"]).strip().lower() == key
                    and str(r["Status"]).strip() == QUARANTINED), None)
        if rec is None:
            return None, "No quarantined unit found for serial " + serial
        row = rec["_row"]
        rec["Status"] = IN_STOCK
        rec["Customer"] = ""
        rec["Date Out"] = ""
        self.recs.cell(row=row, column=5, value=IN_STOCK)
        self.recs.cell(row=row, column=6, value="")
        self.recs.cell(row=row, column=7, value="")
        # Resolve the latest open quarantine in the returns registry
        ret = next((r for r in reversed(self.returns)
                    if str(r["Serial"]).strip().lower() == key
                    and str(r.get("Action", "")).strip() == "Quarantined"), None)
        if ret is not None:
            ret["Action"] = "Restocked"
            stamp = f"Released from quarantine {date}"
            ret["Notes"] = (str(ret.get("Notes", "")).strip() + " | " + stamp).strip(" |")
            ret_row = self.returns.index(ret) + 2
            self.ret_sheet.cell(row=ret_row, column=6, value=str(ret["Notes"]))
            self.ret_sheet.cell(row=ret_row, column=7, value="Restocked")
        self.log_activity("Quarantine Released", model=str(rec["Model"]), count=1,
                          details=f"Serial: {rec['Serial']} | back to stock {date}")
        self.save(backup=True)
        return rec, None

    def update_unit(self, old_serial: str, new_serial: str, model: str,
                    brand: str = "", date_in: str = "") -> tuple[dict | None, str]:
        """Edit an In-Stock unit's serial, model, brand, or date_in.
        Creates backup snapshot and logs activity."""
        old_key = old_serial.strip().lower()
        new_s = new_serial.strip()
        new_key = new_s.lower()

        if not new_s:
            return None, "Serial number cannot be empty"
        if not model.strip():
            return None, "Model cannot be empty"

        rec = next((r for r in self.records
                    if str(r.get("Serial", "")).strip().lower() == old_key
                    and str(r.get("Status", "")).strip() == IN_STOCK), None)
        if not rec:
            return None, f"Unit with serial '{old_serial}' not found in stock"

        # Check duplicate if serial changed
        if new_key != old_key:
            if new_key in self._serial_set:
                return None, f"Serial '{new_s}' already exists in records"
            self._serial_set.discard(old_key)
            self._serial_set.add(new_key)

        m_str = model.strip()
        b_str = brand.strip() or self.brand_for(m_str) or str(rec.get("Brand", ""))
        d_str = date_in.strip() or str(rec.get("Date In", ""))

        rec["Serial"] = new_s
        rec["Model"] = m_str
        rec["Brand"] = b_str
        rec["Date In"] = d_str

        # Update cell in openpyxl worksheet (Column 1: Brand, 2: Model, 3: Serial, 4: Date In)
        row = rec["_row"]
        self.recs.cell(row=row, column=1, value=b_str)
        self.recs.cell(row=row, column=2, value=m_str)
        self.recs.cell(row=row, column=3, value=new_s)
        self.recs.cell(row=row, column=4, value=d_str)

        self.log_activity("Unit Edited", model=m_str, count=1,
                          details=f"Serial: {old_serial} -> {new_s} | Model: {m_str} | Brand: {b_str}")
        self.save(backup=True)
        return rec, ""

    def delete_unit(self, serial: str) -> tuple[bool, str]:
        """Delete an In-Stock unit from MasterRecord.
        Creates backup snapshot and logs activity."""
        key = serial.strip().lower()
        idx = next((i for i, r in enumerate(self.records)
                    if str(r.get("Serial", "")).strip().lower() == key
                    and str(r.get("Status", "")).strip() == IN_STOCK), None)
        if idx is None:
            return False, f"Unit with serial '{serial}' not found in stock"

        rec = self.records.pop(idx)
        self._serial_set.discard(key)
        row = rec["_row"]

        # Delete from openpyxl sheet
        self.recs.delete_rows(row, 1)

        # Shift _row for all subsequent records
        for r in self.records:
            if r.get("_row", 0) > row:
                r["_row"] -= 1

        self.log_activity("Unit Deleted", model=str(rec.get("Model", "")), count=1,
                          details=f"Deleted Serial: {rec.get('Serial', '')} | Model: {rec.get('Model', '')} | Brand: {rec.get('Brand', '')}")
        self.save(backup=True)
        return True, ""

    # ---------------------------------------------------------- brands
    def assign_brand(self, model: str, brand: str):
        """Remember Model -> Brand permanently (exact full string match)."""
        if brand not in self.brands:
            self.brands.append(brand)
            self.brands_sheet.append([brand])
        key = model.strip().upper()
        if self.model_to_brand.get(key) != brand:
            self.model_to_brand[key] = brand
            self.map_sheet.append([model.strip(), brand])
        self.log_activity("Brand Assigned", model=model, count=0,
                          details=f"Assigned to {brand}")
        self.save(backup=True)

    def add_brand(self, brand: str) -> bool:
        brand = brand.strip()
        if not brand or brand in self.brands:
            return False
        self.brands.append(brand)
        self.brands_sheet.append([brand])
        self.save(backup=False)
        return True
