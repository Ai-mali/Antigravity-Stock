# VRE AC Stock — Flow Charts

## 1. Business workflow (what the app is for)

```mermaid
flowchart LR
    A[📷 Delivery-order photo<br/>drop / paste / browse] --> B[AI vision scan<br/>/api/scan]
    B --> C[Scan Results rows<br/>model + serials + qty]
    C -->|Commit All| D[Stock In<br/>MasterRecord += in_stock]
    D --> E[Available Stock<br/>brand accordions]
    E -->|Dispatch / Stock Out| F[Checkout: customer,<br/>date out, 24h delivery time]
    F --> G[Sell Record / Track List<br/>status = sold, warranty starts]
    G -->|Return / RMA| H{Condition}
    H -->|Restock| E
    H -->|Quarantine| I[Returns registry<br/>held units]
    G --> J[Warranty expiry<br/>tracked per unit]
```

## 2. Runtime architecture (one exe, three layers)

```mermaid
flowchart TD
    subgraph EXE["VRE AC Stock.exe"]
        S[Splash - GDI+ comet] --> W[desktop_app.py<br/>pywebview frameless window]
        W --> T[thread: uvicorn 127.0.0.1:8000]
        W --> UI[WebView2 loads ac-stock-tracker.html]
        T --> BE[backend.py - FastAPI]
        UI -->|fetch /api/*| BE
        BE --> MW{license middleware<br/>except /api/license/*}
        MW -->|403 license_required| GATE[gate overlay]
        MW -->|lock: _store_lock| SS[stock_store.py]
        SS --> XLSX[(daikin_stock.xlsx<br/>+ backups/ + delivery_orders/)]
        UI <-->|window.pywebview.api| BR[DesktopApi:<br/>min/max/close, save_file, open_url]
    end
```

## 3. Boot sequence

```mermaid
flowchart TD
    A[exe launch] --> B[native splash shows instantly]
    B --> C{port 8000 healthy?}
    C -->|yes, reuse| D[skip backend spawn]
    C -->|no| E[start uvicorn thread]
    E --> F[poll /api/license/status<br/>always 200, even unlicensed]
    F --> G[backend alive → load app URL]
    D --> G
    G --> H[first painted frame → reveal window,<br/>splash fades]
    H --> I{licenseGateCheck}
    I -->|valid| J[main UI + warn banner if ≤30d]
    I -->|invalid/missing| K[license gate: Machine ID,<br/>key field, reason text]
```

## 4. License lifecycle

```mermaid
flowchart LR
    subgraph YOU["Your machine"]
        G[License Generator exe<br/>+ license_private.pem] -->|sign VRE1.key| K
    end
    subgraph CLIENT["Client PC"]
        A[App boot] --> B{license.lic<br/>beside exe?}
        B -->|no/invalid| C[Gate: shows<br/>Machine ID]
        C --> D[Purchase Renewal →<br/>copies ID + opens<br/>t.me/chh_ck]
        D -->|customer sends ID| E[you sign key<br/>locked to that ID]
        E --> F[customer pastes key]
        F --> H[/api/license/activate<br/>verifies sig + machine<br/>writes license.lic]
        B -->|valid| I{expiry +<br/>clock checks}
        H --> I
        I -->|ok| J[unlocked]
        I -->|≤30d| L[warning banner]
        I -->|expired/rollback| C
    end
```

## 5. Request lifecycle

```mermaid
flowchart LR
    A[fetch /api/x] --> B{path == license/status<br/>or license/activate?}
    B -->|yes| E[handle]
    B -->|no| C{license_check.status()}
    C -->|invalid| F[403 license_required<br/>+ machine + detail]
    C -->|valid| D[await _store_lock<br/>single-file workbook safety]
    D --> E
    E --> G[StockStore mutate]
    G --> H[save → auto-backup<br/>→ ready_exports]
```
