==========================================================
  AC STOCK TRACKER - EMERGENCY DATA RECOVERY GUIDE
==========================================================

IF THE PROGRAM CRASHES OR WILL NOT OPEN
------------------------------------------
Your data is NOT inside the program. It is a normal Excel
file in this folder. You can always read it directly.

  THIS FOLDER:
    daikin_stock.xlsx   <- ALL your live data (stock, sales,
                           customers, returns, activity log)
    backups\            <- automatic snapshots of that file
    delivery_orders\    <- scanned delivery-order photos

STEP 1 - GET THE DATA RIGHT NOW
------------------------------------------
1. Double-click  daikin_stock.xlsx  -> opens in Excel.
   That IS your data. Nothing else is needed.

2. Sheets inside:
     MasterRecord  = every unit (Brand, Model, Serial,
                     Date In, Status, Customer, Date Out, Batch)
     Returns       = RMA / returned units
     ActivityLog   = audit trail of every action
     Brands        = brand colors / settings
     ModelBrands   = model -> brand mapping

3. If daikin_stock.xlsx is corrupt or missing:
   open the  backups\  folder, sort by Date Modified,
   and open the newest file ending in  _after.xlsx
   (or  _manual.xlsx  if that is newest).

   BEFORE files = state just BEFORE an action (undo point)
   AFTER files  = state including that action (latest data)
   MANUAL files = snapshots you made with Backup Now

IMPORTANT RULES
------------------------------------------
* Do NOT delete daikin_stock.xlsx or the backups folder.
* To see it inside Excel while the app is broken, just open
  it - reading a file is always safe.
* Do NOT keep the file open in Excel while the app IS running
  - Excel locks it and the app cannot save (app will error).

IF YOU EDIT THE EXCEL FILE BY HAND (e.g. mark a unit sold)
------------------------------------------
The app reads daikin_stock.xlsx fresh every time it starts.
So: edit while the app is CLOSED -> save in Excel -> next
time the app launches it sees your edits automatically.

NOTE: all sheets are PROTECTED against accidental edits.
To unlock before editing:
  Excel ribbon -> Review tab -> "Unprotect Sheet"
  (no password needed - it is one click)
The app re-locks the sheets automatically on its next save.

To manually "stock out" a serial in MasterRecord:
  1. Review tab -> Unprotect Sheet.
  2. Find the row with the matching Serial value.
  3. Set Status     = Sold
  4. Set Customer   = (customer name)
  5. Set Date Out   = YYYY-MM-DD   (e.g. 2026-09-30)
  6. Set Batch      = MANUAL-YYYYMMDD  (optional, recommended)
  7. Save and close the file BEFORE opening the app.

To manually "return" / restock a serial:
  1. Unprotect Sheet first (see above).
  2. Find the row.
  3. Set Status     = In Stock
  4. Clear Customer, Date Out and Batch.
  (You can also add a row to the Returns sheet, but it is
   optional - Status on MasterRecord is what the app checks.)

WARNING: only edit the file while the app is CLOSED.
If the app is running it holds a copy in memory and will
overwrite your manual edits on its next save.

QUICK RECAP
------------------------------------------
  Need latest data NOW?   -> open daikin_stock.xlsx
  Live file broken?       -> backups\ newest *_after.xlsx
  Edited Excel by hand?   -> edits load on next app start
  Photos of DOs?          -> delivery_orders\ folder
==========================================================
