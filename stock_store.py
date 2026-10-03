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

import calendar
import datetime
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from openpyxl import Workbook, load_workbook

if getattr(sys, "frozen", False):
    DB_PATH = Path(sys.executable).parent / "daikin_stock.xlsx"
else:
    DB_PATH = Path(__file__).with_name("daikin_stock.xlsx")
BACKUP_DIR = DB_PATH.parent / "backups"
DO_DIR = DB_PATH.parent / "delivery_orders"

RECORD_HEADER = ["Brand", "Model", "Serial",
                 "Date In", "Status", "Customer", "Date Out", "Batch"]
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
        day = min(d.day, calendar.monthrange(year, month)[1])
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
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DB_PATH)
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
        self._model_canon: dict[str, str] = {}   # UPPER(model) -> display case
        for idx, row in enumerate(
                self.recs.iter_rows(min_row=2, values_only=True), start=2):
            if not row:
                continue
            rec = dict(zip(RECORD_HEADER,
                           ("" if v is None else v for v in row)))
            if not rec.get("Serial"):
                continue
            rec["Batch"] = str(rec.get("Batch") or "")
            rec["_row"] = idx  # Excel row, so updates hit the right cells
            self.records.append(rec)
            self._serial_set.add(str(rec["Serial"]).strip().lower())
            self._model_canon.setdefault(str(rec["Model"]).strip().upper(),
                                         str(rec["Model"]).strip())
        for row in self.brands_sheet.iter_rows(min_row=2, values_only=True):
            if row and row[0] and row[0] not in self.brands:
                self.brands.append(str(row[0]))
        for row in self.map_sheet.iter_rows(min_row=2, values_only=True):
            if row and row[0] and row[1]:
                self.model_to_brand[str(row[0]).upper()] = str(row[1])
                self._model_canon.setdefault(str(row[0]).strip().upper(),
                                             str(row[0]).strip())
        for idx, row in enumerate(
                self.ret_sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not row or row[0] in (None, ""):
                continue
            ret = dict(zip(RETURN_HEADER,
                           ("" if v is None else v for v in row)))
            ret["_row"] = idx  # Excel row — survives blank/deleted rows
            self.returns.append(ret)
        for row in self.act_sheet.iter_rows(min_row=2, values_only=True):
            if not row or row[0] in (None, ""):
                continue
            self.activities.append(dict(
                zip(ACTIVITY_HEADER, ("" if v is None else v for v in row))))
        if self._schema_dirty:
            # Migrated a legacy/older-schema file: keep the original on
            # disk as a premigration snapshot before we overwrite it.
            try:
                self._backup("premigration")
            except Exception:
                pass
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
        # Fix leftover "Type" header cells inside the renamed sheets
        if "Brands" in self.wb.sheetnames:
            c = self.wb["Brands"].cell(row=1, column=1)
            if str(c.value or "").strip() == "Type":
                c.value = "Brand"
                self._schema_dirty = True
        if "ModelBrands" in self.wb.sheetnames:
            c = self.wb["ModelBrands"].cell(row=1, column=2)
            if str(c.value or "").strip() == "Type":
                c.value = "Brand"
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
        # Accident guard: protected in Excel (one click to unprotect,
        # no password). openpyxl ignores this flag, so the app writes freely.
        ws.protection.sheet = True
        return ws

    def _backup(self, suffix: str = ""):
        """Snapshot daikin_stock.xlsx as it exists on disk right now.

        Called BEFORE a change (suffix='before' -> undo point) and again
        AFTER wb.save() (suffix='after' -> state mirror). Same-second
        collisions get a numeric suffix.
        """
        if not self.path.exists():
            return None
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"daikin_stock_{now_str}" + (f"_{suffix}" if suffix else "")
        dest = BACKUP_DIR / f"{stem}.xlsx"
        i = 2
        while dest.exists():
            dest = BACKUP_DIR / f"{stem}_{i}.xlsx"
            i += 1
        shutil.copy2(self.path, dest)
        self._prune_backups(keep=60)  # 60 files = 30 before+after pairs
        return dest

    def _prune_backups(self, keep: int = 60):
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

    @staticmethod
    def _as_date(v):
        """Cell/record value -> date for sorting (handles str + datetime)."""
        if isinstance(v, datetime.datetime):
            return v.date()
        if isinstance(v, datetime.date):
            return v
        return parse_date_safe(str(v or ""))

    def _sort_sheet_rows(self):
        """Newest activity floats to the TOP of MasterRecord + Returns, so
        opening the workbook in Excel reads like the app's Sell Record.
        Memory and sheet are rewritten together — every _row is renumbered
        after the rewrite, so incremental edits stay aligned."""
        epoch = datetime.date(1970, 1, 1)

        # A unit's "activity date" = Date Out when sold/returned, else Date In
        self.records.sort(
            key=lambda r: self._as_date(r.get("Date Out"))
                          or self._as_date(r.get("Date In")) or epoch,
            reverse=True)
        if self.recs.max_row > 1:
            self.recs.delete_rows(2, self.recs.max_row - 1)
        for i, rec in enumerate(self.records, start=2):
            rec["_row"] = i
            self.recs.append([rec.get("Brand", ""), rec.get("Model", ""),
                              rec.get("Serial", ""), rec.get("Date In", ""),
                              rec.get("Status", ""), rec.get("Customer", ""),
                              rec.get("Date Out", ""), rec.get("Batch", "")])
        # Excel convenience: frozen header + filter dropdowns on the columns
        self.recs.freeze_panes = "A2"
        self.recs.auto_filter.ref = f"A1:H{len(self.records) + 1}"

        self.returns.sort(
            key=lambda r: self._as_date(r.get("Date")) or epoch,
            reverse=True)
        if self.ret_sheet.max_row > 1:
            self.ret_sheet.delete_rows(2, self.ret_sheet.max_row - 1)
        for i, ret in enumerate(self.returns, start=2):
            ret["_row"] = i
            self.ret_sheet.append([ret.get("Serial", ""), ret.get("Model", ""),
                                   ret.get("Customer", ""), ret.get("Reason", ""),
                                   ret.get("Condition", ""), ret.get("Notes", ""),
                                   ret.get("Action", ""), ret.get("Date", ""),
                                   ret.get("Date Out", "")])
        self.ret_sheet.freeze_panes = "A2"

    def save(self, backup: bool = False):
        if backup:
            try:
                self._backup("before")   # undo point: state before the change
            except Exception as e:
                # No undo point -> do NOT persist. Re-sync memory from disk
                # so in-memory state matches the workbook again.
                try:
                    self.load()
                except Exception:
                    pass
                raise RuntimeError(
                    f"Pre-change backup failed — change not saved: {e}") from e
        # Newest activity first in MasterRecord + Returns; memory _row
        # values are renumbered to match the rewritten sheet.
        self._sort_sheet_rows()
        # Keep user-entered strings as text — a leading =,+,-,@ must never
        # be written as a spreadsheet formula.
        for ws in self.wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and c.value[:1] in ("=", "+", "-", "@"):
                        c.data_type = "s"
        # Atomic write: build the file under a temp name, then swap it in so
        # no reader ever observes a half-written workbook.
        tmp = self.path.with_name(self.path.stem + ".tmp.xlsx")
        try:
            self.wb.save(tmp)
            os.replace(tmp, self.path)
        except Exception:
            # Save failed (e.g. workbook open in Excel). Memory now differs
            # from disk — reload so a later successful save can't persist
            # the changes this failed write was supposed to contain.
            try:
                os.remove(tmp)
            except OSError:
                pass
            try:
                self.load()
            except Exception:
                pass
            raise
        if backup:
            try:
                self._backup("after")    # mirror: state including the change
            except Exception as e:
                try:
                    self.log_activity("Backup Failed",
                                      details=f"after-snapshot: {e}",
                                      status="FAIL")
                    self.wb.save(tmp)
                    os.replace(tmp, self.path)
                except Exception:
                    pass

    def log_activity(self, action: str, model: str = "", count: int = 0,
                     details: str = "", status: str = "OK"):
        """Record an action in the ActivityLog sheet and memory."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Excel cells cap at 32,767 chars — a huge serial list would break
        # the file, so keep a margin and note what was cut.
        if details and len(details) > 30000:
            details = details[:30000] + " … (truncated)"
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
                       key=lambda p: p.stat().st_ctime, reverse=True)
        out = []
        for f in files:
            st = f.stat()
            kind = ""
            m = re.search(r"_(before|after|manual)(_\d+)?$", f.stem)
            if m:
                kind = m.group(1)
            out.append({
                "filename": f.name,
                "size": st.st_size,
                "kind": kind,
                "modified": datetime.datetime.fromtimestamp(
                    st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            })
        return out

    def manual_backup(self) -> dict:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_DIR / f"daikin_stock_{now_str}_manual.xlsx"
        i = 2
        while dest.exists():
            dest = BACKUP_DIR / f"daikin_stock_{now_str}_manual_{i}.xlsx"
            i += 1
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
        # Only files this app wrote (or a premigration snapshot) are
        # restorable — never copy an arbitrary extension into place.
        if not re.fullmatch(r"daikin_stock_\d{8}_\d{6}(_(before|after|manual|premigration)(_\d+)?)?\.xlsx", safe_name):
            return False, f"'{safe_name}' is not a backup snapshot"
        src = BACKUP_DIR / safe_name
        if not src.exists():
            return False, f"Backup file '{safe_name}' not found"
        # Validate the candidate BEFORE touching the live workbook — a
        # corrupt or non-workbook file must not overwrite good data.
        try:
            probe = load_workbook(src, read_only=True)
            ok = "MasterRecord" in probe.sheetnames
            probe.close()
            if not ok:
                return False, (f"'{safe_name}' has no MasterRecord sheet — "
                               "not a stock workbook")
        except Exception as e:
            return False, f"Backup is not a readable workbook — restore aborted: {e}"
        try:
            self._backup("before")  # safety snapshot of current state before restoring
        except Exception as e:
            return False, f"Safety snapshot failed — restore aborted: {e}"
        # The audit log should not be rewound — keep the events that happened
        # between the snapshot and now so the history stays complete.
        prev_activities = list(self.activities)
        try:
            shutil.copy2(src, self.path)
            self.load()
            # Restored log is normally a prefix of the live log; append whatever
            # came after it back onto the sheet and into memory.
            common = 0
            restored = list(self.activities)
            for a, b in zip(restored, prev_activities):
                if a != b:
                    break
                common += 1
            extras = prev_activities[common:]
            for e in extras:
                self.activities.append(e)
                if self.act_sheet is not None:
                    self.act_sheet.append([e["Timestamp"], e["Action"], e["Model"],
                                           e["Serials Count"], e["Details"], e["Status"]])
            self.log_activity("Restore", details=f"Restored from {safe_name}")
            self.save(backup=False)
            try:
                self._backup("after")   # pair mirror: post-restore state
            except Exception as e:
                self.log_activity("Backup Failed",
                                  details=f"after-snapshot: {e}",
                                  status="FAIL")
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
                                       "models": {}})
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
                                   "models": {}})
        for b in inv.values():
            for i, m in enumerate(sorted(b["models"]), start=1):
                b["models"][m]["idx"] = f"{i:02d}"
        return dict(sorted(inv.items()))

    def track_list(self) -> list[dict]:
        """Sold units, newest first, with warranty information."""
        out = [{"model": str(r["Model"]), "serial": str(r["Serial"]),
                "dateIn": str(r["Date In"]), "dateOut": str(r["Date Out"]),
                "customer": str(r["Customer"]), "status": str(r["Status"]),
                "batch": str(r.get("Batch") or ""),
                "warranty": compute_warranty(str(r["Date Out"]))}
               for r in self.records if str(r["Status"]).strip() == SOLD]
        return out

    def returns_list(self) -> list[dict]:
        # Date In lives on the master record, not the returns row — join it in
        date_in_map = {str(rec["Serial"]).strip().lower():
                       str(rec.get("Date In", "")).strip()
                       for rec in self.records}
        return [{"serial": str(r["Serial"]), "model": str(r["Model"]),
                 "customer": str(r["Customer"]), "reason": str(r["Reason"]),
                 "condition": str(r["Condition"]), "notes": str(r["Notes"]),
                 "action": str(r["Action"]), "date": str(r["Date"]),
                 "dateIn": date_in_map.get(str(r["Serial"]).strip().lower(), ""),
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
            cur = parse_date_safe(stats[cust]["lastDate"])
            new = parse_date_safe(date_out)
            if date_out and (cur is None or (new is not None and new > cur)):
                stats[cust]["lastDate"] = date_out

        return sorted(stats.values(),
                      key=lambda x: (x["count"], parse_date_safe(x["lastDate"]) or datetime.date.min),
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
                 date_in: str, auto_ids: bool = False):
        """Shared save path for manual entry and scanned rows."""
        serials = [s.strip() for s in serials if s.strip()]
        if not model.strip() or not serials:
            return [], serials, True
        dupes, new_serials = self._split_dupes(serials, auto_ids)
        brand = self.brand_for(model)
        if not brand:
            return [], dupes, False
        self._write_rows(self._canon_model(model), brand,
                         new_serials, date_in)
        return new_serials, dupes, True

    def stock_in_with_brand(self, model: str, serials: list[str],
                            date_in: str, brand: str,
                            auto_ids: bool = False):
        """Completes stock_in for a Model the user just assigned a Brand."""
        brand = self._canon_brand(brand)
        if not brand:
            return [], serials
        self.assign_brand(model, brand)
        dupes, new_serials = self._split_dupes(
            [s.strip() for s in serials if s.strip()], auto_ids)
        self._write_rows(self._canon_model(model), brand,
                         new_serials, date_in)
        return new_serials, dupes

    def _canon_brand(self, brand: str) -> str:
        """Match an existing brand case-insensitively so 'daikin' and
        'Daikin' can't fork into two groups; new names keep typed casing."""
        name = brand.strip()
        for b in self.brands:
            if b.strip().lower() == name.lower():
                return b
        return name

    def _canon_model(self, model: str) -> str:
        """Reuse the stored display casing for a model ('ftkm35' and
        'FTKM35' are the same SKU), else keep what was typed."""
        name = model.strip()
        return self._model_canon.get(name.upper(), name)

    def reinit_empty(self):
        """Reset to a fresh empty workbook in memory WITHOUT touching disk.
        Used when the xlsx vanished — honors the deletion instead of
        resurrecting stale rows on the next save."""
        self.wb = Workbook()
        self.wb.active.title = "MasterRecord"
        self.recs = self._sheet("MasterRecord", RECORD_HEADER)
        self.brands_sheet = self._sheet("Brands", ["Brand"])
        self.map_sheet = self._sheet("ModelBrands", ["Model", "Brand"])
        self.ret_sheet = self._sheet("Returns", RETURN_HEADER)
        self.act_sheet = self._sheet("ActivityLog", ACTIVITY_HEADER)
        self.records, self.brands, self.model_to_brand = [], [], {}
        self.returns, self.activities, self._serial_set = [], [], set()
        self._model_canon = {}
        self._schema_dirty = False

    def _split_dupes(self, serials, auto_ids: bool = False):
        """Known serials AND repeated serials inside the same batch are dupes.
        ' #' synthetic unit IDs auto-increment to the next free sequence
        number — but ONLY for non-serial rows the caller flagged as
        generated, so a real serial like 'SN #5001' can't be silently
        renamed."""
        seen, dupes, new_serials = set(), [], []
        for raw_s in serials:
            s = str(raw_s).strip()
            if auto_ids and " #" in s:
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

    def _write_rows(self, model, brand, serials, date_in, save=True):
        self._model_canon.setdefault(model.strip().upper(), model.strip())
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
                              details=f"Brand: {brand} | {len(serials)} units added | Serials: {', '.join(serials)}")
            if save:
                self.save(backup=True)

    def stock_in_batch(self, rows: list[dict], date_in: str) -> dict:
        """Commit many rows in ONE save — a 40-row scan produces one
        before/after backup pair instead of flooding the rotation.

        Two-phase: if any model has no mapped/provided brand, report
        needs_brands WITHOUT mutating, so the caller can ask once and retry.
        """
        needs, plan = [], []
        for pos, row in enumerate(rows):
            model = str(row.get("model", "")).strip()
            serials = [str(s).strip().upper()
                       for s in row.get("serials", []) if str(s).strip()]
            if not model or not serials:
                continue
            brand = str(row.get("brand", "")).strip() or self.brand_for(model)
            idx = row.get("idx")
            if idx is None or not isinstance(idx, int):
                idx = pos
            if not brand:
                needs.append(model)
            else:
                plan.append((idx, model, brand, serials,
                             bool(row.get("auto_ids"))))
        if needs:
            return {"needs_brands": sorted(set(needs)), "results": []}
        results = []
        for idx, model, brand, serials, auto_ids in plan:
            brand = self._canon_brand(brand)
            key = model.upper()
            # Register the mapping inline — assign_brand() would save()
            # per row, defeating the single-save point of this method.
            if self.model_to_brand.get(key) != brand:
                self.model_to_brand[key] = brand
                self.map_sheet.append([model, brand])
            if brand not in self.brands:
                self.brands.append(brand)
                self.brands_sheet.append([brand])
            dupes, new_serials = self._split_dupes(serials, auto_ids)
            if new_serials:
                self._write_rows(self._canon_model(model), brand,
                                 new_serials, date_in, save=False)
            results.append({"idx": idx, "model": model,
                            "added": new_serials, "dupes": dupes,
                            "brand": brand})
        if results:
            self.save(backup=True)
        return {"needs_brands": [], "results": results}

    def stock_out(self, serials: list[str], customer: str, date_out: str):
        """Mark units Sold with one Customer + Date Out for the whole cart.
        Every checkout stamps the same Batch id on all its units so a
        mistaken dispatch can be reverted as one transaction."""
        batch = ("SALE-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                 + "-" + uuid.uuid4().hex[:5].upper())
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
            rec["Batch"] = batch
            models_sold.add(str(rec["Model"]))
            row = rec["_row"]
            self.recs.cell(row=row, column=5, value=SOLD)
            self.recs.cell(row=row, column=6, value=customer)
            self.recs.cell(row=row, column=7, value=date_out)
            self.recs.cell(row=row, column=8, value=batch)
            done.append(str(rec["Serial"]))
        if done:
            model_summary = ", ".join(sorted(models_sold))
            self.log_activity("Stock Out", model=model_summary, count=len(done),
                              details=f"Customer: {customer} | Batch: {batch} | {len(done)} units sold | Serials: {', '.join(done)}")
            self.save(backup=True)
        return done

    def revert_sale(self, batch: str = "", customer: str = "",
                    date_out: str = "") -> tuple[list[str], str]:
        """Undo a whole dispatch: flip its Sold units back to In Stock and
        clear Customer/Date Out/Batch. Matches by Batch id; legacy rows
        without one match on Customer + Date Out. Returns (serials, error)."""
        batch = (batch or "").strip()
        cust = (customer or "").strip().lower()
        dout = (date_out or "").strip()
        if not batch and not (cust and dout):
            return [], "Nothing identifies this dispatch"
        targets = [r for r in self.records
                   if str(r["Status"]).strip() == SOLD
                   and ((batch and str(r.get("Batch") or "").strip() == batch)
                        or (not batch and not str(r.get("Batch") or "").strip()
                            and str(r.get("Customer") or "").strip().lower() == cust
                            and str(r.get("Date Out") or "").strip() == dout))]
        if not targets:
            return [], "No sold units found for this dispatch"
        done, models = [], set()
        for rec in targets:
            row = rec["_row"]
            rec["Status"] = IN_STOCK
            rec["Customer"] = ""
            rec["Date Out"] = ""
            rec["Batch"] = ""
            self.recs.cell(row=row, column=5, value=IN_STOCK)
            self.recs.cell(row=row, column=6, value="")
            self.recs.cell(row=row, column=7, value="")
            self.recs.cell(row=row, column=8, value="")
            done.append(str(rec["Serial"]))
            models.add(str(rec["Model"]))
            self._ensure_brand(rec.get("Brand", ""))
        self.log_activity("Sale Reverted", model=", ".join(sorted(models)),
                          count=len(done),
                          details=f"Customer: {customer} | Batch: {batch or 'legacy'} | {len(done)} units restored to stock | Serials: {', '.join(done)}")
        self.save(backup=True)
        return done, ""

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
            "Action": action_label, "Date": date, "Date Out": date_out,
            "_row": self.ret_sheet.max_row})
        row = rec["_row"]
        rec["Batch"] = ""
        self.recs.cell(row=row, column=8, value="")
        prev_customer = str(rec["Customer"] or "")
        if action == "restock":
            rec["Status"] = IN_STOCK
            rec["Customer"] = ""
            rec["Date Out"] = ""
            self.recs.cell(row=row, column=5, value=IN_STOCK)
            self.recs.cell(row=row, column=6, value="")
            self.recs.cell(row=row, column=7, value="")
            self._ensure_brand(rec.get("Brand", ""))
        else:
            rec["Status"] = QUARANTINED
            self.recs.cell(row=row, column=5, value=QUARANTINED)
        self.log_activity("Return", model=str(rec["Model"]), count=1,
                          details=f"Serial: {rec['Serial']} | Customer: {prev_customer} | Reason: {reason} | Action: {action_label}")
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
            # Orphan registry row (e.g. legacy/migrated data): a Returns
            # entry marked Quarantined whose unit no longer exists. Closing
            # it keeps the registry resolvable instead of stuck forever.
            orphan = next((r for r in reversed(self.returns)
                           if str(r["Serial"]).strip().lower() == key
                           and str(r.get("Action", "")).strip() == "Quarantined"),
                          None)
            if orphan is None:
                return None, "No quarantined unit found for serial " + serial
            orphan["Action"] = "Closed"
            stamp = f"Registry closed {date} — no matching unit on record"
            orphan["Notes"] = (str(orphan.get("Notes", "")).strip()
                               + " | " + stamp).strip(" |")
            o_row = orphan.get("_row") or (self.returns.index(orphan) + 2)
            self.ret_sheet.cell(row=o_row, column=6, value=str(orphan["Notes"]))
            self.ret_sheet.cell(row=o_row, column=7, value="Closed")
            self.log_activity("Return Closed", model=str(orphan.get("Model", "")),
                              count=1,
                              details=f"Serial: {orphan.get('Serial')} | orphan quarantine entry closed")
            self.save(backup=True)
            return orphan, None
        row = rec["_row"]
        rec["Status"] = IN_STOCK
        rec["Customer"] = ""
        rec["Date Out"] = ""
        self.recs.cell(row=row, column=5, value=IN_STOCK)
        self.recs.cell(row=row, column=6, value="")
        self.recs.cell(row=row, column=7, value="")
        rec["Batch"] = ""
        self.recs.cell(row=row, column=8, value="")
        self._ensure_brand(rec.get("Brand", ""))
        # Resolve the latest open quarantine in the returns registry
        ret = next((r for r in reversed(self.returns)
                    if str(r["Serial"]).strip().lower() == key
                    and str(r.get("Action", "")).strip() == "Quarantined"), None)
        if ret is not None:
            ret["Action"] = "Restocked"
            stamp = f"Released from quarantine {date}"
            ret["Notes"] = (str(ret.get("Notes", "")).strip() + " | " + stamp).strip(" |")
            ret_row = ret.get("_row") or (self.returns.index(ret) + 2)
            self.ret_sheet.cell(row=ret_row, column=6, value=str(ret["Notes"]))
            self.ret_sheet.cell(row=ret_row, column=7, value="Restocked")
        else:
            # Unit quarantined before the Returns registry tracked it
            # (legacy/migrated rows) — append the entry so the release is
            # auditable and the unit keeps its second-hand tag.
            date_out = str(rec.get("Date Out", "")).strip()
            self.ret_sheet.append([str(rec["Serial"]), str(rec["Model"]),
                                   str(rec.get("Customer") or ""),
                                   "Quarantined (legacy)", "",
                                   f"Released from quarantine {date}",
                                   "Restocked", date, date_out])
            self.returns.append({
                "Serial": str(rec["Serial"]), "Model": str(rec["Model"]),
                "Customer": str(rec.get("Customer") or ""),
                "Reason": "Quarantined (legacy)", "Condition": "",
                "Notes": f"Released from quarantine {date}",
                "Action": "Restocked", "Date": date, "Date Out": date_out,
                "_row": self.ret_sheet.max_row})
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

    def delete_units(self, serials: list[str]) -> tuple[list[str], list[str]]:
        """Delete many In-Stock units in ONE save — marquee bulk-delete.
        Returns (deleted, missing): serials not In Stock are reported,
        never silently ignored. One backup pair + one activity entry."""
        wanted = {str(s).strip().lower() for s in serials if str(s).strip()}
        targets = [r for r in self.records
                   if str(r.get("Serial", "")).strip().lower() in wanted
                   and str(r.get("Status", "")).strip() == IN_STOCK]
        missing = [s for s in serials
                   if str(s).strip().lower() not in
                   {str(r["Serial"]).strip().lower() for r in targets}]
        if not targets:
            return [], missing
        # Sheet rows die highest-first so earlier _row values stay valid
        for r in sorted(targets, key=lambda x: x.get("_row", 0),
                        reverse=True):
            self.recs.delete_rows(r["_row"], 1)
            self._serial_set.discard(str(r["Serial"]).strip().lower())
        gone = {id(r) for r in targets}
        self.records = [r for r in self.records if id(r) not in gone]
        # records order mirrors sheet order — renumber rows wholesale
        for i, r in enumerate(self.records, start=2):
            r["_row"] = i
        models = sorted({str(r.get("Model", "")) for r in targets})
        deleted = [str(r["Serial"]) for r in targets]
        self.log_activity("Units Deleted", model=", ".join(models[:6]),
                          count=len(deleted),
                          details=f"{len(deleted)} units deleted | Serials: {', '.join(deleted)}")
        self.save(backup=True)
        return deleted, missing

    # ---------------------------------------------------------- brands
    def assign_brand(self, model: str, brand: str):
        """Remember Model -> Brand permanently (exact full string match)."""
        brand = self._canon_brand(brand)
        if brand not in self.brands:
            self.brands.append(brand)
            self.brands_sheet.append([brand])
        key = model.strip().upper()
        self._model_canon.setdefault(key, model.strip())
        if self.model_to_brand.get(key) != brand:
            self.model_to_brand[key] = brand
            self.map_sheet.append([model.strip(), brand])
        self.log_activity("Brand Assigned", model=model, count=0,
                          details=f"Assigned to {brand}")
        self.save(backup=True)

    def add_brand(self, brand: str) -> bool:
        brand = self._canon_brand(brand)
        if not brand or brand in self.brands:
            return False
        self.brands.append(brand)
        self.brands_sheet.append([brand])
        self.save(backup=False)
        return True

    def delete_brand(self, brand: str) -> tuple[bool, str]:
        """Remove an empty brand: its Brands row + all ModelBrands mappings.
        Refuses while In-Stock or Quarantined units still carry the name.
        Sold records keep it — a later return re-registers the brand."""
        name = brand.strip()
        if name not in self.brands:
            return False, "Brand not found: " + name
        held = sum(1 for r in self.records
                   if str(r.get("Brand", "")).strip().lower() == name.lower()
                   and str(r.get("Status", "")).strip() in (IN_STOCK, QUARANTINED))
        if held:
            return False, f"{name} still has {held} unit(s) in stock/quarantine"
        self.brands.remove(name)
        for i in range(self.brands_sheet.max_row, 1, -1):
            if str(self.brands_sheet.cell(row=i, column=1).value or "").strip() == name:
                self.brands_sheet.delete_rows(i)
        drop = [m for m, b in self.model_to_brand.items()
                if str(b).strip().lower() == name.lower()]
        for m in drop:
            del self.model_to_brand[m]
        for i in range(self.map_sheet.max_row, 1, -1):
            if str(self.map_sheet.cell(row=i, column=2).value or "").strip().lower() == name.lower():
                self.map_sheet.delete_rows(i)
        self.log_activity("Brand Deleted", model=name, count=0,
                          details=f"Removed empty brand | {len(drop)} model mapping(s) cleared")
        self.save(backup=True)
        return True, ""

    def _ensure_brand(self, brand: str):
        """Re-register a brand name after it was deleted while empty —
        e.g. a sold unit returning to stock under its old brand.
        Only the name is restored (not the model mapping) so a return
        never overwrites a deliberate re-assignment made meanwhile."""
        brand = str(brand).strip()
        if brand and brand not in self.brands:
            self.brands.append(brand)
            self.brands_sheet.append([brand])
