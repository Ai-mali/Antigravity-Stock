"""VRE AC Stock backend — FastAPI + Excel store + vision scan engine.

Serves the single-file frontend (ac-stock-tracker.html) and the REST API
it calls. Run:

    pip install -r requirements.txt
    python backend.py        # opens http://localhost:8000
"""

import asyncio
import datetime
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # embeddable Python lacks script dir

from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Literal

import scanner
from stock_store import StockStore

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
HTML_PATH = BASE / "ac-stock-tracker.html"
if not HTML_PATH.exists():
    HTML_PATH = Path(__file__).parent / "ac-stock-tracker.html"

app = FastAPI(title="VRE AC Stock")
store = StockStore()
_mtime = store.path.stat().st_mtime if store.path.exists() else 0

# The Excel workbook + in-memory store are not concurrency-safe: two requests
# can interleave a load/mutate/save and one of them reads a half-written file.
# Serialize every /api/ request — calls take milliseconds and this app is
# single-operator, so a simple global lock is the correct model here.
_store_lock = asyncio.Lock()


@app.middleware("http")
async def _serialize_api(request, call_next):
    # Local-only app: refuse /api/ calls whose Host isn't this machine so a
    # web page on another origin (or DNS rebinding) can't drive the store.
    if request.url.path.startswith("/api/"):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            return JSONResponse({"ok": False, "error": "local access only"},
                                status_code=403)
        async with _store_lock:
            return await call_next(request)
    return await call_next(request)


def _fresh():
    """Reload the workbook if it changed on disk (e.g. edited in Excel)."""
    global _mtime
    try:
        m = store.path.stat().st_mtime
    except FileNotFoundError:
        # The file was deleted (or moved) while running — don't let the
        # next save resurrect the stale in-memory copy over a fresh sheet.
        if store.records or store.returns:
            store.reinit_empty()
        _mtime = 0
        return
    if m != _mtime:
        store.load()
        _mtime = store.path.stat().st_mtime


def _saved():
    global _mtime
    try:
        _mtime = store.path.stat().st_mtime
    except FileNotFoundError:
        pass


# The desktop shell is a thin wrapper around this file — never let WebView2
# serve a cached copy, or UI fixes don't show up until the cache expires.
_NOCACHE = {"Cache-Control": "no-store"}


@app.get("/")
def index():
    return FileResponse(HTML_PATH, headers=_NOCACHE)


@app.get("/ac-stock-tracker.html")
def index_alias():
    return FileResponse(HTML_PATH, headers=_NOCACHE)


@app.post("/api/reload")
def reload():
    store.load()
    _saved()
    return {"ok": True}


# ------------------------------------------------------------------ scan & delivery orders
@app.post("/api/scan")
def scan(file: UploadFile):
    logs: list[str] = []
    photo_name = ""
    # Reject before anything is archived: a scan upload that isn't a sane
    # image never touches delivery_orders/.
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    is_image = ((file.content_type or "").startswith("image/")
                or ext in ("jpg", "jpeg", "png", "webp", "bmp"))
    if not is_image:
        return JSONResponse({"ok": False, "log": logs,
                             "error": "Only image files can be scanned"},
                            status_code=200)
    try:
        img = file.file.read()
    except Exception as ex:
        return JSONResponse({"ok": False, "log": logs,
                             "error": f"Upload read failed: {ex}"},
                            status_code=200)
    if not img or len(img) > 20 * 1024 * 1024:
        return JSONResponse({"ok": False, "log": logs,
                             "error": "Image is empty or over 20 MB"},
                            status_code=200)
    try:
        providers = scanner.load_providers()
        active = scanner.load_active(providers)
        if not providers.get(active["provider"], {}).get("keys"):
            label = scanner.PROVIDERS[active["provider"]]["label"]
            return JSONResponse({"ok": False, "log": logs,
                                 "error": f"No API key saved for {label} — open Scan Model and add one."},
                                status_code=200)
    except Exception:
        pass  # config unreadable — let scan_image produce its own message
    try:
        try:
            from stock_store import save_do_photo
            photo_name = save_do_photo(img, file.filename or "photo.jpg")
            logs.append(f"DO Photo archived: {photo_name} (3-day rolling retention)")
        except Exception as p_ex:
            logs.append(f"Photo save note: {p_ex}")

        rows = scanner.scan_image(img, file.filename or "photo.jpg",
                                  logs.append)
        return {"ok": True, "rows": rows, "log": logs, "photo": photo_name}
    except Exception as ex:
        logs.append(f"Scan failed: {ex}")
        return JSONResponse({"ok": False, "log": logs, "error": str(ex), "photo": photo_name},
                            status_code=200)


@app.get("/api/delivery-orders")
def get_delivery_orders():
    from stock_store import list_do_photos
    return {"photos": list_do_photos()}


@app.get("/api/delivery-orders/{filename}")
def get_delivery_order_photo(filename: str):
    from stock_store import DO_DIR
    safe_name = Path(filename).name
    target = DO_DIR / safe_name
    if not target.exists() or not target.is_file():
        return JSONResponse({"ok": False, "error": "Photo not found"}, status_code=404)
    ext = target.suffix.lower()
    media_type = ("image/jpeg" if ext in (".jpg", ".jpeg")
                  else "image/png" if ext == ".png"
                  else "image/webp" if ext == ".webp"
                  else "application/octet-stream")
    return FileResponse(target, media_type=media_type)


# ------------------------------------------------------------------ reads
@app.get("/api/inventory")
def inventory():
    _fresh()
    return {"inventory": store.inventory(), "brands": store.brands}


@app.get("/api/inventory/units")
def get_in_stock_units():
    _fresh()
    return {"units": store.in_stock_units(), "brands": store.brands}


@app.get("/api/track")
def track():
    _fresh()
    return {"records": store.track_list()}


@app.get("/api/returns")
def returns():
    _fresh()
    return {"records": store.returns_list()}


@app.get("/api/brands")
def brands():
    _fresh()
    return {"brands": store.brands}


@app.get("/api/customers")
def customers():
    _fresh()
    return {"customers": store.customer_history()}


# ------------------------------------------------------------------ activity & backups
@app.get("/api/activity")
def get_activity(limit: int = 100):
    _fresh()
    return {"records": store.activity_log(limit=limit)}


@app.get("/api/backups")
def get_backups():
    return {"backups": store.list_backups()}


@app.post("/api/backup")
def trigger_backup():
    _fresh()
    bk = store.manual_backup()
    _saved()
    return {"ok": True, "backup": bk}


class RestoreBody(BaseModel):
    filename: str


@app.post("/api/restore")
def restore_backup(body: RestoreBody):
    ok, err = store.restore_backup(body.filename)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _saved()
    return {"ok": True}


@app.get("/api/backup/download")
def download_backup(filename: str = ""):
    if filename:
        from stock_store import BACKUP_DIR
        safe_name = Path(filename).name
        target = BACKUP_DIR / safe_name
        if not target.exists():
            return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)
        return FileResponse(target, filename=safe_name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    _fresh()
    if not store.path.exists():
        return JSONResponse({"ok": False, "error": "Workbook does not exist yet"}, status_code=404)
    return FileResponse(store.path, filename="daikin_stock.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ stock
class StockInBody(BaseModel):
    model: str = Field(min_length=1)
    serials: list[str]
    date_in: str
    brand: str = ""
    auto_ids: bool = False  # ' #' unit IDs may auto-increment (non-serial rows)


@app.post("/api/stock-in")
def stock_in(body: StockInBody):
    _fresh()
    if not body.model.strip():
        raise HTTPException(status_code=400, detail="model is required")
    if not body.date_in.strip():
        raise HTTPException(status_code=400, detail="date_in is required")
    serials = [s.strip().upper() for s in body.serials if s.strip()]
    if body.brand.strip():
        added, dupes = store.stock_in_with_brand(
            body.model, serials, body.date_in, body.brand,
            auto_ids=body.auto_ids)
        needs_brand = False
    else:
        added, dupes, ok = store.stock_in(
            body.model, serials, body.date_in, auto_ids=body.auto_ids)
        needs_brand = not ok
    _saved()
    return {"added": added, "dupes": dupes, "needs_brand": needs_brand}


class StockInBatchRow(BaseModel):
    model: str
    serials: list[str]
    brand: str = ""
    auto_ids: bool = False
    # Scan-table index the frontend uses to match per-row results back to
    # its rows. Undeclared fields are stripped at validation, so without
    # this the store fell back to list positions and the UI could never
    # reconcile committed rows (units were written, rows never cleared).
    idx: int | None = None


class StockInBatchBody(BaseModel):
    rows: list[StockInBatchRow] = Field(min_length=1, max_length=500)
    date_in: str


@app.post("/api/stock-in-batch")
def stock_in_batch(body: StockInBatchBody):
    """Commit a whole scan sheet in one write — one backup pair total."""
    _fresh()
    if not body.date_in.strip():
        raise HTTPException(status_code=400, detail="date_in is required")
    res = store.stock_in_batch(
        [r.model_dump() for r in body.rows], body.date_in)
    _saved()
    return res


class AssignBrandBody(BaseModel):
    model: str = Field(min_length=1)
    brand: str = Field(min_length=1)


@app.post("/api/models/brand")
def assign_brand(body: AssignBrandBody):
    store.assign_brand(body.model, body.brand.strip())
    _saved()
    return {"ok": True, "brands": store.brands}


class DeleteBrandBody(BaseModel):
    brand: str = Field(min_length=1)


@app.post("/api/brands/delete")
def delete_brand(body: DeleteBrandBody):
    _fresh()
    ok, err = store.delete_brand(body.brand)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _saved()
    return {"ok": True, "brands": store.brands}


class RevertSaleBody(BaseModel):
    batch: str = ""
    customer: str = ""
    date_out: str = ""


@app.post("/api/sales/revert")
def revert_sale(body: RevertSaleBody):
    _fresh()
    done, err = store.revert_sale(body.batch, body.customer, body.date_out)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _saved()
    return {"ok": True, "serials": done}


class RescheduleBody(BaseModel):
    batch: str = ""
    customer: str = ""
    date_out: str = ""
    new_date: str = Field(min_length=1)


@app.post("/api/sales/reschedule")
def reschedule_sale(body: RescheduleBody):
    _fresh()
    done, err = store.reschedule_sale(body.batch, body.customer, body.date_out, body.new_date)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _saved()
    return {"ok": True, "serials": done}


class StockOutBody(BaseModel):
    serials: list[str] = Field(min_length=1)
    customer: str
    date_out: str


@app.post("/api/stock-out")
def stock_out(body: StockOutBody):
    _fresh()
    done = store.stock_out(body.serials, body.customer, body.date_out)
    _saved()
    return {"sold": done}


class ReturnBody(BaseModel):
    serial: str = Field(min_length=1)
    reason: str = ""
    condition: str = ""
    notes: str = ""
    action: Literal["restock", "quarantine"] = "quarantine"


@app.post("/api/returns")
def create_return(body: ReturnBody):
    _fresh()
    today = datetime.date.today().strftime("%m/%d/%Y")
    rec, err = store.create_return(body.serial, body.reason, body.condition,
                                   body.notes, body.action, today)
    _saved()
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=404)
    return {"ok": True}


class ReleaseQuarantineBody(BaseModel):
    serial: str


@app.post("/api/returns/release")
def release_quarantine(body: ReleaseQuarantineBody):
    _fresh()
    today = datetime.date.today().strftime("%m/%d/%Y")
    rec, err = store.release_quarantine(body.serial, today)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=404)
    _saved()
    return {"ok": True}


class UpdateUnitBody(BaseModel):
    old_serial: str
    new_serial: str
    model: str
    brand: str = ""
    date_in: str = ""


class DeleteUnitBody(BaseModel):
    serial: str = ""
    serials: list[str] = []  # bulk path: one save for the whole selection


@app.post("/api/inventory/update")
def update_inventory_unit(body: UpdateUnitBody):
    _fresh()
    rec, err = store.update_unit(
        old_serial=body.old_serial,
        new_serial=body.new_serial,
        model=body.model,
        brand=body.brand,
        date_in=body.date_in
    )
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _saved()
    return {"ok": True, "unit": {
        "brand": rec["Brand"],
        "model": rec["Model"],
        "serial": rec["Serial"],
        "dateIn": rec["Date In"]
    }}


@app.post("/api/inventory/delete")
def delete_inventory_unit(body: DeleteUnitBody):
    _fresh()
    if body.serials:
        deleted, missing = store.delete_units(body.serials)
        _saved()
        return {"ok": True, "deleted": deleted, "missing": missing}
    ok, err = store.delete_unit(body.serial)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    _saved()
    return {"ok": True}



# ------------------------------------------------------------------ config
def _config_payload() -> dict:
    providers = scanner.load_providers()
    active = scanner.load_active(providers)
    return {
        "active": active,
        "providers": {
            pid: {
                "label": scanner.PROVIDERS[pid]["label"],
                "models": scanner.PROVIDERS[pid]["models"],
                "model": cfg["model"],
                "keys": [{"masked": scanner.mask_key(k["key"]),
                          "remark": k.get("remark", ""),
                          "status": "ACTIVE"}
                         for k in cfg["keys"]],
            }
            for pid, cfg in providers.items()
        },
    }


@app.get("/api/config")
def get_config():
    return _config_payload()


@app.get("/api/config/models")
def list_models(provider: str, key: str = None):
    """Live model list for a provider (fetches via its API, else static)."""
    return scanner.list_models_detailed(provider, key=key)


class ConfigBody(BaseModel):
    provider: str
    model: str


@app.post("/api/config")
def set_config(body: ConfigBody):
    providers = scanner.load_providers()
    if body.provider not in providers:
        return JSONResponse({"ok": False, "error": "unknown provider"},
                            status_code=400)
    providers[body.provider]["model"] = body.model
    scanner.save_providers(providers,
                           {"provider": body.provider, "model": body.model})
    return {"ok": True}


class KeyBody(BaseModel):
    provider: str
    key: str
    remark: str = ""


@app.post("/api/config/keys")
def add_key(body: KeyBody):
    providers = scanner.load_providers()
    if body.provider not in providers:
        return JSONResponse({"ok": False, "error": "unknown provider"},
                            status_code=400)
    keys = providers[body.provider]["keys"]
    if not any(k["key"] == body.key for k in keys):
        keys.append({"key": body.key.strip(), "remark": body.remark.strip()})
        scanner.save_providers(providers)
    return {"ok": True, "provider": _provider_payload(body.provider)}


def _provider_payload(pid: str) -> dict:
    cfg = scanner.load_providers()[pid]
    return {"id": pid, "label": scanner.PROVIDERS[pid]["label"],
            "models": scanner.PROVIDERS[pid]["models"],
            "model": cfg["model"],
            "keys": [{"masked": scanner.mask_key(k["key"]),
                      "remark": k.get("remark", ""), "status": "ACTIVE"}
                     for k in cfg["keys"]]}


class KeyReorderBody(BaseModel):
    provider: str
    order: list[int]


@app.post("/api/config/keys/reorder")
def reorder_keys(body: KeyReorderBody):
    providers = scanner.load_providers()
    if body.provider not in providers:
        return JSONResponse({"ok": False, "error": "unknown provider"},
                            status_code=400)
    keys = providers[body.provider]["keys"]
    if sorted(body.order) != list(range(len(keys))):
        return JSONResponse({"ok": False, "error": "order must be a "
                             "permutation of all key indices"},
                            status_code=400)
    providers[body.provider]["keys"] = [keys[i] for i in body.order]
    scanner.save_providers(providers)
    return {"ok": True, "provider": _provider_payload(body.provider)}


@app.delete("/api/config/keys")
def remove_key(provider: str, index: int):
    providers = scanner.load_providers()
    if provider not in providers:
        return JSONResponse({"ok": False, "error": "unknown provider"},
                            status_code=400)
    keys = providers[provider]["keys"]
    if 0 <= index < len(keys):
        keys.pop(index)
        scanner.save_providers(providers)
    return {"ok": True, "provider": _provider_payload(provider)}


# ------------------------------------------------------------------ ui prefs
if getattr(sys, "frozen", False):
    UI_PREFS_PATH = Path(sys.executable).parent / "ui_prefs.json"
else:
    UI_PREFS_PATH = Path(__file__).parent / "ui_prefs.json"


def _read_ui_prefs() -> dict:
    try:
        prefs = json.loads(UI_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Drop/clamp bad width values left by an earlier unvalidated write.
    for k in list(prefs):
        if k.endswith("W"):
            try:
                prefs[k] = max(60, min(900, int(prefs[k])))
            except (TypeError, ValueError):
                del prefs[k]
    return prefs


@app.get("/api/ui-prefs")
def get_ui_prefs():
    return _read_ui_prefs()


class UiPrefsBody(BaseModel):
    prefs: dict


@app.post("/api/ui-prefs")
def save_ui_prefs(body: UiPrefsBody):
    existing = _read_ui_prefs()
    for k, v in body.prefs.items():
        # *W keys are sidebar pixel widths — clamp to a sane range so a
        # bad value can't stretch a sidebar across the whole window.
        if k.endswith("W"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            v = max(60, min(900, v))
        existing[k] = v
    tmp = UI_PREFS_PATH.with_name("ui_prefs.tmp.json")
    tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    os.replace(tmp, UI_PREFS_PATH)
    return {"ok": True}


# ------------------------------------------------------- user background image
BG_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
BG_MAX_BYTES = 15 * 1024 * 1024
BG_BASENAME = "user_bg"


def _find_bg():
    for p in UI_PREFS_PATH.parent.glob(BG_BASENAME + ".*"):
        if p.suffix.lower() in BG_ALLOWED_EXT:
            return p
    return None


def _is_real_image(data: bytes) -> bool:
    return (data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"\xff\xd8\xff")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


@app.get("/api/background")
def get_background():
    p = _find_bg()
    if not p:
        raise HTTPException(404, "No background image set")
    media = {".png": "image/png", ".webp": "image/webp"}.get(
        p.suffix.lower(), "image/jpeg")
    return FileResponse(p, media_type=media)


@app.post("/api/background")
def upload_background(file: UploadFile):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in BG_ALLOWED_EXT:
        return JSONResponse({"ok": False,
                             "error": "Use a PNG, JPG or WebP image"},
                            status_code=400)
    try:
        data = file.file.read()
    except Exception as ex:
        return JSONResponse({"ok": False, "error": f"Upload read failed: {ex}"},
                            status_code=400)
    if not data or len(data) > BG_MAX_BYTES:
        return JSONResponse({"ok": False,
                             "error": "Image is empty or over 15 MB"},
                            status_code=400)
    if not _is_real_image(data):
        return JSONResponse({"ok": False,
                             "error": "File is not a valid PNG/JPG/WebP image"},
                            status_code=400)
    target = UI_PREFS_PATH.parent / (BG_BASENAME + ext)
    tmp = UI_PREFS_PATH.parent / (BG_BASENAME + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    for old in UI_PREFS_PATH.parent.glob(BG_BASENAME + ".*"):
        if old != target:
            try:
                old.unlink()
            except OSError:
                pass
    return {"ok": True}


@app.delete("/api/background")
def delete_background():
    for old in UI_PREFS_PATH.parent.glob(BG_BASENAME + ".*"):
        try:
            old.unlink()
        except OSError:
            pass
    return {"ok": True}


def _launch_window(desktop_mode: bool = False):
    url = "http://localhost:8000"
    if desktop_mode:
        import subprocess
        import shutil
        candidates = [
            shutil.which("chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for c in candidates:
            if c and Path(c).exists():
                try:
                    subprocess.Popen([str(c), f"--app={url}", "--window-size=1440,900"])
                    return
                except Exception:
                    pass
    webbrowser.open(url)


# ------------------------------------------------------------------ run
if __name__ == "__main__":
    import multiprocessing
    import sys
    multiprocessing.freeze_support()
    import uvicorn

    desktop = "--desktop" in sys.argv or "--app" in sys.argv
    threading.Timer(1.0, lambda: _launch_window(desktop_mode=desktop)).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
