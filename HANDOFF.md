# HANDOFF — AC Stock Tracker (Daikin)

Everything a new AI/developer needs to continue this project. Read this
whole file before changing code — it encodes decisions made with the
owner over multiple sessions.

## 1. What this app is

Daikin AC parts-list photo → AI vision scan → Excel-backed stock system.
Flow: **Stock In (scan) → Available Stock → Stock Out → Track List →
Return (RMA)**. Single-file HTML frontend + FastAPI backend; one process
serves both at `http://localhost:8000`.

- Repo: `github.com/Ai-mali/Antigravity-Stock`, work directly on `main`.
- Owner runs: `D:\AI-Project\Stock New UI\Stock-App` on Windows, uses
  `git-pull.bat` + `run.bat` (both in repo).
- **Deliver commits to `main` — the owner git-pulls and tests himself.**
- Final packaging goal: a Python `.exe` (PyInstaller) — deferred to the
  very end on purpose.

## 2. File map

| File | Role |
|---|---|
| `ac-stock-tracker.html` | The ENTIRE frontend: ~3700 lines, vanilla HTML/CSS/JS, no frameworks. All screens, modals, and logic live here. |
| `backend.py` | FastAPI app: serves the HTML at `/` + `/ac-stock-tracker.html`, plus all `/api/*` endpoints. Auto-opens the browser. |
| `scanner.py` | Vision engine: `PROVIDERS` dict (gemini/alibaba/openai/anthropic/deepseek), `PROMPT` sent to the vision model, `parse_scan_json`, per-provider scanners with **multi-key failover**, `list_models` (live model fetch with static fallback). |
| `stock_store.py` | `StockStore` — openpyxl layer over `daikin_stock.xlsx`. Sheets: `MasterRecord` `Brand\|Model\|Serial\|Date In\|Status\|Customer\|Date Out` (one row per serial), `Brands`, `ModelBrands` (exact Model→Brand), `Returns` `Serial\|Model\|Customer\|Reason\|Condition\|Notes\|Action\|Date`, and `ActivityLog` `Timestamp\|Action\|Model\|Serials Count\|Details\|Status`. Automatic rotating backups in `backups/`, customer history ranking, and warranty tracking. |
| `requirements.txt` | `fastapi, uvicorn, python-multipart, google-genai, openpyxl` |
| `git-pull.bat`, `run.bat` | Owner's helpers (`git pull` / `python backend.py`). |
| `extractor_flowchart.html` | Old flowchart doc — informational only. |

## 3. NOT in git (must know)

- `daikin_stock.xlsx` — **gitignored on purpose** (real business data). A
  fresh clone starts with an empty workbook; auto-created on first run.
- `~/.model_serial_extractor.json` — scan engine config + ALL API keys,
  stored per-user, shared with the old desktop app. Shape:
  `{"providers": {pid: {"keys": [{"key","remark"}], "model"}},
   "active": {"provider","model"}}`. NEVER commit keys or this file.

## 4. Run / develop

```
pip install -r requirements.txt
python backend.py          # uvicorn on 127.0.0.1:8000 + opens browser
```

Windows note: the owner's Python is an embeddable distro — `backend.py`
starts with `sys.path.insert(0, script_dir)`; keep that line.

**JS check before committing** `ac-stock-tracker.html` edits (there is no
linter): extract `<script>` blocks to a temp file, run `node --check` on
it. Also `python -c "import ast; ast.parse(open('scanner.py').read())"`.

## 5. Business rules & conventions (decided with owner — do not regress)

### Scan rows
Row shape: `{no, model, qty, serial, serials[], desc, flag, sev, warn}`.
- Each `Serial No:` block belongs ONLY to its row — never merge serials
  across rows. Model-less blocks keep `model=""` (UI shows `—`).
- Dash ranges (`K361 - K368`) expand ONLY when the expanded count equals
  declared Qty; if splitting dashes as separators matches Qty instead,
  use that (flag `qty_fixed`); else expand + `qty_mismatch`.
- Tokens without digits are description text, not serials — parse moves
  them to `desc` (catches models reading "DAIKIN REFNET JOINT" as serials).

### Warnings (`flag` code → `sev` + `warn` human message)
- `no_model` — **red** (`sev:"error"`): model is REQUIRED to commit.
- `qty_mismatch` — **red**: "N serials captured but Qty declares M…".
- `qty_fixed` — **yellow** (`sev:"warn"`): verify dash interpretation.
- `no_serial` — **yellow**: description shown instead; still usable.
- UI: warning ⚠ icon sits in the actions column BEFORE the zoom icon;
  whole row tinted red/yellow. Editing + saving a row CLEARS warnings
  and re-flags only if the problem still exists.

### Desc-only rows are committable
`commitSerialsOf(row)` in the frontend: real serials if present, else the
description as the unit ID — expanded `desc #1..#N` when Qty > 1 so each
unit is counted and dedup still works. Rows with neither serials nor
desc, or no model, are skipped/blocked.

### Commit flow
- Per-row ▶ commits individually; "Commit All" bulk button exists too.
- `/api/stock-in` returns `needs_brand` when the model is unmapped →
  frontend `askBrand()` modal → `POST /api/models/brand` → retry.
- Duplicate serials are skipped server-side (case-insensitive `_serial_set`).

### API keys
- Order in the config table IS priority: index 0 = PRIMARY, rest FAILOVER;
  scanner tries top→down on quota/5xx errors. UI rows are drag-to-reorder
  (`POST /api/config/keys/reorder` accepts a permutation of indices).

### UI specifics
- Serial cell scrolls internally (`max-height ~82px`) so 100+ serial rows
  stay compact; buttons pinned in a fixed-width `.actions-cell`.
- Zoom 🔍 button = read-only modal showing No|Model|Qty + all serial chips.
- Pencil ✏️ = Edit Scanned Item modal: No/Model/Qty/Serials, live serial
  count, auto-format, delete row. Esc + backdrop close for all modals.
- Stitch-designed CSS — match existing classes/variables (`--green-bright`,
  `--surface2`, `btn-action-icon`, `scan-serial-badge`, …), dark + light
  themes (`body.theme-light` overrides).

## 6. Roadmap status & features implemented

1. Serial-less parts policy — partly done (desc commits); confirm desired
   stock-out UX for desc-identified units.
2. [DONE] **Backup + activity log for daikin_stock.xlsx**:
   - Automatic rotating backups saved to `backups/daikin_stock_YYYYMMDD_HHMMSS.xlsx` before every mutation (keeps latest 30 snapshots, gitignored).
   - Dedicated `ActivityLog` sheet inside `daikin_stock.xlsx` documenting Timestamp, Action, Model, Count, Details, and Status.
   - UI Activity Log & Backups modal with live history search, instant manual backup trigger, active file download, and one-click restore.
3. [DONE] **Customer autocomplete in Stock Out**:
   - History query ranking customers by order frequency and recency.
   - Stitch-styled autocomplete dropdown supporting keyboard navigation (↑/↓/Enter/Esc).
   - Quick-select "Recent Customers" chip bar for 1-click selection.
   - Dynamic tag indicating "Known Customer" vs "✦ New Customer".
4. [DONE] **Low-stock alert (≤2 units per model)**:
   - Amber alert banner at the top of Available Stock highlighting low-stock models across all brands, with 1-click filter toggle (`⚡ View Low Stock Only`).
   - Warning badge on model rows: `⚠️ Low Stock (N)` when ≤2 units, `Out of Stock (0)` when 0 units.
   - Warning badges also displayed in Stock Out view.
5. [DONE] **Warranty tracking**:
   - Auto-computed from `Date Out` + duration (standard 12 months default).
   - Track List displays `Active (Xd left)`, `Expiring Soon (Xd left)`, or `Expired` badges.
   - Warranty filter pills in Track List (`All Warranty`, `🛡️ Active`, `⚠️ Expiring Soon`, `❌ Expired`) and CSV export inclusion.
   - Return (RMA) screen shows live warranty status banner for selected serial.
6. [DONE] **Accidental Dispatch / Cancellation restock**:
   - Added `"Order Cancelled / Wrong Entry"` to Return (RMA) reasons.
   - Allows operators to cleanly restock mistakenly dispatched serials back into Available Stock with a recorded audit trail.
7. [DONE] **Direct In-Stock Unit Editing & Deletion (Chips UX)**:
   - Replaced the separate "Manage Inventory Table" modal with direct, contextual actions on every serial chip in Available Stock.
   - Each serial card features a subtle Pencil ✏️ icon to edit (Serial, Model, Brand, Date In) and a Trash 🗑️ icon to delete accidental/duplicate units via an in-app `#confirm-delete-modal` (no browser alerts).
   - Clicking the card body still copies the serial to clipboard; clicking action buttons is isolated with `event.stopPropagation()`.
   - Dedicated sleek `#edit-unit-modal` for modifying unit details with automatic brand synchronization and staged cart updating.
   - Fixed Image Preview action buttons layout & scan controller: Set `.preview-actions-row` to `flex-wrap: nowrap` with fixed button dimensions (`width: 138px` on `#btn-scan`) so buttons stay completely still and never wrap to a second line.
   - Added interactive multi-state scan button: displays animated spinner (`Scanning...`) while active, smoothly switches to danger red (`Stop`) on mouse hover, and aborts/cancels scanning immediately on click via `AbortController`.
   - Powered by `StockStore.update_unit()` and `StockStore.delete_unit()` with pre-save backup snapshots and `ActivityLog` records.
8. [DONE] **Delivery Order (DO) Photos (3-Day Rolling Auto-Cleanup)**:
   - Scanned invoice/DO photos saved locally to `delivery_orders/` (gitignored).
   - Rolling auto-cleanup prunes photos older than 3 days from oldest to newest.
   - "Recent DOs" button & modal in Stock In with badge count, thumbnails, and full-resolution lightbox viewer with download support.
9. [DONE] **Scan Results Drag-to-Select & Batch Brand Assignment**:
   - Holding left-click and dragging across table rows enables fast multi-row selection with smooth emerald/green highlighting (`scan-row-selected`).
   - Initial click detects target row state: dragging from an unselected row selects all hovered rows; dragging from a selected row deselects them.
   - Removed checkbox column and header text `(Hold & drag...)` for a cleaner UI; selection operates purely by clicking and dragging directly on rows.
   - Real-time selection badge and action buttons: "Commit Selected (X) to Available Stock →", "Delete Selected (X)", or "Commit All to Available Stock →".
   - **Batch Brand Assignment**: When multiple rows are committed, selecting a brand in the modal applies to the entire selected batch in one go, eliminating repetitive one-by-one popups. Individual row commit arrow still assigns single models.
   - **Live Brand Badge & High Contrast**: In the Assign Brand modal, replaced static phrase with `"Currently Selected Brand: [Brand Name]"` rendered in a dedicated high-contrast `.brand-display-badge` (electric cyan on dark, deep ocean blue on light) for WCAG AAA readability in both Black and White modes.
10. [DONE] **Duplicate Serial Retention, Location Popup & Centered Alert**:
   - Duplicate serials are **NOT** cleared immediately on commit; they stay in the Scan Results table for review.
   - Row background does **NOT** blink (keeps the table clean and steady); only the duplicate serial numbers and the duplicate badge blink.
   - The Model code and the animated `[⚠️ DUPLICATE]` badge are **center-aligned** in the Model column.
   - Clicking either the `[⚠️ DUPLICATE]` badge or any red duplicate serial chip opens the **Duplicate Serial Location** modal (`#duplicate-location-modal`).
   - The modal details exactly where the duplicate unit is located:
     - If **In Stock**: Shows Brand, Model, Date In, and provides a `"View in Available Stock →"` button that jumps directly to the unit and filters by brand.
     - If **Sold**: Shows Brand, Model, Customer Name, and Date Out with a `"View in Track List →"` button that searches and highlights the sold record.
     - If **Returned**: Shows RMA status, reason, customer, and return date with a `"View in Returns →"` button.
   - Added a direct Trash button (🗑️) in the row's Actions column right next to the Zoom button (🔍), enabling 1-click removal of duplicate or unwanted scan rows.
   - Automatic pre-check `markDuplicateScans()` flags known serials on scan extraction, manual addition, and data refresh.
11. [DONE] **Manual Single Assign Fix & Centered "Action" Header**:
   - Fixed the brand assignment bug where `subEl.innerHTML` previously removed `#brand-modal-model` from DOM, causing single-row assign (`commitScanRow`) to fail silently or get stuck when prompting for a new brand.
   - Preserved `_currentBrandModel` across modal lifecycle and directly passed the assigned brand to `/api/stock-in` on retry.
   - Centered the table header word **"Action"** (singular) and horizontally centered the 4 buttons (🗑️ Trash, 🔍 Zoom, ✏️ Edit, ▶️ Assign) directly underneath it with balanced spacing.
   - Protected row selection state by shifting remaining selected row indices when a single row is committed from the middle of the table.
12. [DONE] **Duplicate Row Selection Lock, Disabled Assign & Traffic Light System (Option 2)**:
   - **Selection Lock for Duplicates**: Duplicate rows are completely locked from selection (click & drag selection ignores them, and "Select All" excludes them).
   - **Disabled Manual Assign on Duplicates**: The manual assign arrow (▶️) is disabled (`opacity: 0.22`, `cursor: not-allowed`, `pointer-events: none`) on duplicate rows with tooltip explaining it cannot commit duplicates.
   - **Independent Batch Commit**: Because duplicate rows cannot be selected, the bottom commit button automatically shows `"Commit Valid (X) to Available Stock →"` (or `"Commit Selected (X)..."`) and safely skips duplicate rows so the operator is never blocked from stocking in good items.
   - **Traffic Light Indicators**:
     - 🟢 **Ready / Good Rows**: Get a vibrant green left border (`border-left: 3.5px solid var(--green-bright)` / `#10b981`), and a clean `[🟢 READY]` status pill under the model name.
     - 🔴 **Duplicate Rows**: Retain their red left border (`border-left: 3.5px solid #ef4444`), blinking duplicate serial chips, and `[⚠️ DUPLICATE]` badge with cursor lock.
   - **Editable Recovery**: When an operator edits a duplicate row via ✏️ and fixes the serials, `saveEditScanModal` re-evaluates the serials and automatically restores the row to 🟢 READY status and re-enables its assign arrow.
13. [DONE] **Partial Duplicate Auto-Split & Ready Serial Assignment Modal**:
   - **Per-Serial Duplicate & Ready Distinction**:
     - Duplicate serial chips blink red (`scan-serial-badge-dupe`) and open the Duplicate Location modal on click.
     - Valid/ready serial chips glow green (`scan-serial-badge-ready`) with a soft, non-strobe breathing pulse and open the **Ready Serial Modal** on click.
   - **Partial Duplicate Badge**: Rows containing both new and duplicate serials display a split status badge `[🟢 X READY | 🔴 Y DUP]` and an amber left border (`border-left: 3.5px solid #f59e0b`).
   - **Dual Action Commit Paths**:
     - **Path 1 (Green Chip Modal)**: Clicking any green serial chip opens `#ready-serial-modal` (`Ready to Stock In`), displaying Model, Serial, target Brand selector, and an `"Assign & Stock In"` button to commit that single verified unit immediately.
     - **Path 2 (Row Assign Arrow ▶️)**: The row's assign button remains enabled on partial duplicate rows. Clicking it automatically commits all valid serials in the row to Available Stock and leaves only the duplicate serial(s) behind in the scan results (dropping row quantity and turning into full red `[⚠️ DUPLICATE]`).
14. [DONE] **Smart Auto-Expand, Focus Glow, & Search Query Preservation (Available Stock & Stock Out)**:
   - **Auto-Expand on Search**:
     - When searching for a serial (e.g. `K000581`) or model, matching brand and model accordions automatically expand (`display: block` / `display: grid`), even if they were previously collapsed.
     - Non-matching models and serials are cleanly filtered out; if a brand has no matches, it is hidden.
   - **Focus Glow & Match Highlight**:
     - Found serial cards receive `.avail-unit-card-highlighted` with an emerald focus border, ambient breathing glow (`avail-card-focus-pulse`), and matched character text highlight (`<mark class="search-match-text">`).
   - **Accordion State Preservation & Restore**:
     - Toggling accordions (e.g. clicking brand header) during search retains the active search text instead of clearing it.
     - When the search box is cleared (or user clicks the new quick `✕` clear button), previous brand and model collapsed/expanded states are automatically restored.
   - **Seamless Duplicate Jump Navigation**:
     - Clicking "View in Available Stock" from `#duplicate-location-modal` auto-expands the brand and model, focuses the matching card, and smoothly scrolls it into view.
15. [DONE] **Dispatch Staging Split Cards (Dedicated Checkout Card + Staging Cart)**:
   - **Split into 2 Separate Cards on Right Sidebar**:
     - **Card 1 (Dedicated Customer & Checkout Card)**: Placed above the staging cart with its own 4-side closed border (`border: 1px solid var(--border); border-radius: 10px; background: var(--surface)`). Houses Customer Name (autocomplete + recent customer chips), Date Out, and "Complete Checkout →" button. Pinned frozen (`flex-shrink: 0`) at the top of the sidebar.
     - **Card 2 (Dispatch Staging Cart Card)**: Sits directly below Card 1 with its own closed border, header (Title, ID, Pill Count, Brand Breakdown Pills), and scrollable staged units list (`#cart-items-container` with `flex: 1 1 auto; overflow-y: auto`).
   - **Zero Bottom Cut-Off on Any Display**:
     - Both cards are held inside a sticky column (`position: sticky; top: 76px; max-height: calc(100vh - 96px);`). Card 1 never scrolls out of view and never gets pushed offscreen on small laptops or high display scaling.
   - **Top Brand Breakdown Row**:
     - Right below the "Dispatch Staging Cart" title, `#cart-brand-breakdown-row` displays colorful brand summary pills showing live units per brand staged (e.g. `[● CARRIER 8] [● DAIKIN 2]`).
   - **Brand Badges on Staged Cards**:
     - Each card in the staging cart displays a distinct colored brand tag (`.cart-item-brand-badge`) alongside the serial and model description.
16. [DONE] **Ultra-Fast 50ms Theme Transition & Pure Natural Scrolling**:
   - **Ultra-Fast 50ms Day/Dark Mode Transition**:
     - Configured a crisp 50ms (0.05s) micro-fade transition on main surfaces and cards (`body, .app-container, header, main, .card, .avail-unit-card, .so-serial-card, .search-filter-bar, .pill-filter-btn { transition: background-color 0.05s ease, border-color 0.05s ease, color 0.05s ease, box-shadow 0.05s ease; }`).
     - Provides instant, snappy theme toggling within 3 frames (~50ms) without any rendering lag or dropped frames on low-power devices.
     - Snappy card hover transforms (`transform: translateY(-2px)`) remain fast and responsive.
   - **Pure Natural Scrolling (Lock Screen Removed)**:
     - Removed experimental `#screen-mode-btn` (Screen Fit vs Free Scroll) and removed all locked viewport / trapped inner scrollbar CSS.
     - Clean, comfortable whole-page scrolling where header sticks at `top: 0` and Stock Out cards stick at `top: 76px`.
17. [DONE] **Frozen Sticky Search Dialog & Anti-Cutoff Sidebar Geometry**:
   - **Sticky Search Bar with Separated Border**:
     - Made `.search-filter-bar` sticky at `top: 56px` with `z-index: 35`, full-bleed background (`background: var(--bg-app)`), separated bottom border (`border-bottom: 1px solid var(--border)`), and soft ambient elevation shadow (`box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18)` in dark, `0 2px 10px rgba(0,0,0,0.05)` in light).
     - In both Available Stock and Stock Out, scrolling down keeps the search input and brand filter pills frozen directly below the header. The scrolling inventory passes underneath with zero bleed-through.
   - **Zero Bottom Cut-Off on Scroll Down**:
     - Adjusted `#stockout-right-sidebar` sticky anchor to `top: 122px` (docked cleanly below the frozen search toolbar with 9px breathing space).
     - Constrained sidebar to `max-height: calc(100vh - 150px)`, guaranteeing at least 28px of visible space between Card 2's bottom rounded border and the bottom of the viewport on any screen size.
   - **2-Tier Stacked Sticky Headers (Brand + Model)**:
     - Made `.brand-accordion-header` sticky at `top: 113px` (`z-index: 25`) directly below the frozen search toolbar, with `overflow: visible` on `.brand-accordion`.
     - Made `.model-accordion-header` sticky at `top: 156px` (`z-index: 20`) directly below the brand header, with `overflow: visible` on `.model-accordion`.
     - While scrolling serial chips of a model, both Brand and Model remain locked in view (`DAIKIN › FCFC60AV1F`).
     - Reaching the next model smoothly pushes and replaces the model header; reaching the next brand smoothly pushes and replaces both tiers with zero scroll jitter.
     - Collapsed accordions receive `.is-closed` with all 4 corners neatly rounded (`border-radius: 7px/9px`).
   - **Header & Card Title Refinements**:
     - Renamed Card 1 title from `Dispatch Destination & Checkout` to `Dispatch & Checkout` with `white-space: nowrap` and `flex-wrap: nowrap`.
     - Ensured customer status tag (`+ New Customer` / `Known Customer`) sits strictly on the same single line without wrapping down.
     - Cleaned up Stock Out view header by removing the subtitle and duplicate top "Clear Staging Cart" button (preserving the dedicated "Clear Cart" button inside Card 2).
18. **Indoor + Outdoor Sets & Non-Serial Parts**:
   - Decided with owner: Keep indoor and outdoor units treated as independent boxes (operator selects serials separately).
   - Non-serial accessories remain description-only (`#1..#N`) for now; to be revisited when the owner requests it.
19. **Final step: PyInstaller `.exe` packaging** — deferred until all upgrades and new features are finished as requested by the owner. Keep repo and folder clean until that final step.


Owner instruction that still applies: *"the most important is you have
to make the improvement on my idea — do not agree with me everytime,
and suggest me good ideas."* Push back constructively.

**MANDATORY PROTOCOL — ASK & PROPOSE OPTIONS FIRST BEFORE IMPLEMENTING**:
- If a requirement or request is underspecified, ambiguous, or if you can see a superior/pro-level approach or alternative options: **DO NOT assume or rush into code changes**.
- **Ask the owner first** using the interactive question tool.
- Present clear choices with your recommended option first, but always provide an option for the owner to write down their own custom thoughts or decision.
- Wait for the owner's response and approval before implementing code changes.

## 7. Workflow with the owner

- Always consult and confirm design/workflow options before modifying code when there are choices to make.
- Commit to `main`, tell him to `git pull` (or run `git-pull.bat`).
- He tests on his own machine and reports screenshots — diagnose from
  those; root-cause fixes, not workarounds.
- He sometimes sends `CHANGES.md` / HTML snippets from Google Stitch —
  apply them while keeping the backend wiring intact.
- Khmer phrases appear occasionally; e.g. "ថោរ​មេ" = "for me".
