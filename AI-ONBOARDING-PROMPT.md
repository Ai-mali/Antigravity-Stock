# Prompt to onboard another AI onto this project

Paste everything below into the other AI session.

---

You are taking over **VRE AC Stock**, a Windows desktop stock-management app for air-conditioner parts. Repo: `D:\AI-Project\Devin\Stock-App` (GitHub: Ai-mali/Antigravity-Stock). **Read `HANDOVER.md` and `FLOWCHART.md` first** — they are the project bible. Then do this analysis pass before touching anything:

## Analyze, in this order

1. **`stock_store.py`** — `StockStore` wraps `daikin_stock.xlsx` (the whole database: MasterRecord / Brands / ModelBrands / Returns / ActivityLog sheets). Understand `load/_migrate_schema/save`, `_pair_key` (duplicate = same model AND serial; same serial on different models is legal), `_resolve_db_path` (frozen exe adopts the newest `.xlsx` beside it), `_backup`/`restore_backup`, and `compute_warranty`.

2. **`backend.py`** — FastAPI on port 8000. Note the `_store_lock` global lock serializing every `/api/` request (workbook isn't thread-safe — do not add concurrency), and the license middleware returning `403 {error:"license_required"}` for all `/api/*` except `/api/license/status` and `/api/license/activate`.

3. **`license_check.py`** — Ed25519 `VRE1.<payload>.<sig>` keys, `machine_id()` from Windows MachineGuid, expiry + clock-rollback via `license_state.json`, `VRE_DEV_BYPASS` (source-only, never honored when frozen). The private generator lives in `D:\AI-Project\Devin\License key generator\` — it is NOT in this repo; never move `license_private.pem` anywhere public.

4. **`desktop_app.py`** — pywebview frameless shell. Key invariants: health check must poll `/api/license/status` (any HTTP response = alive; a gated 403 must not hang the splash), `os._exit(0)` for instant port release, `_webview2_present()` guard, `DesktopApi.open_url` bridge.

5. **`ac-stock-tracker.html`** — the ENTIRE frontend (~17k lines). Learn: `api()`/`apiGet`/`apiPost` helpers + `license_required` sentinel handling; `fmtDMY`/`fmtDMYTime`/`parseDateDMY` (display DD/MM/YYYY, pickers ISO, workbook MM/DD/YYYY — three zones, never mix); theme via `body.theme-light` + CSS vars (`--brand-ink` for day-mode text); license gate `#license-gate` and admin modal `#license-modal` (`.licm-card`, label spans inside icon buttons); custom date/time pickers (`initDatePicker`, `rs-time` 24h); local re-render patterns (delete ops update state without full `refreshData`).

## Critical rules — never break these

- Duplicate serial = same **model+serial** only.
- Date zones: workbook `MM/DD/YYYY` ↔ picker/API ISO ↔ display `DD/MM/YYYY`. Never `new Date("MM/DD/YYYY")`-style ambiguous parsing.
- All writes serialize through `_store_lock`.
- The HTML contains literal `\u2014` `\u00b7` escape text — verify exact-match patterns before scripted edits.
- License middleware blocks API independent of UI; keep `/api/license/*` exempt.
- `license_private.pem`, `*.lic`, `license_state.json` never committed.
- Frozen paths = exe folder (writable required); bundled assets via `sys._MEIPASS`.

## Verify before/after any change

```bash
node test_fulfillment_groups.js    # 73 tests
python -m py_compile backend.py stock_store.py desktop_app.py license_check.py
# embedded JS syntax — see HANDOVER.md §9 for the one-liner
```

Run the app: `python backend.py` (browser) or `pythonw desktop_app.py` (desktop shell). Build: `build-exe.bat` → `dist\VRE AC Stock.exe`.

Report back: your understanding of the request flow, the license flow, and any risks you noticed — before making changes.

---
