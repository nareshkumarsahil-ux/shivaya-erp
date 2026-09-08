# Shivaya Circuit — Smart ERP (PCB Manufacturing)

Flask + SQLite based PCB manufacturing ERP — dashboard, live process tracking, production planning, billing, inventory and more.

## Run

```bash
cd shivaya_circuit
pip install flask itsdangerous
python3 app.py        # → http://0.0.0.0:8000
```

## 🚀 Vercel Deploy (mobile + PC par same data)

Ye project **Vercel-ready** hai — `api/index.py` + `vercel.json` + `requirements.txt` included.
Vercel par data persist karne ke liye **Turso (free cloud SQLite)** use hota hai — `TURSO_URL` + `TURSO_AUTH_TOKEN` env vars set karo.

👉 **Poori guide: [README_DEPLOY.md](README_DEPLOY.md)** (Hindi/English steps — Turso DB banao → Vercel env set karo → deploy).

## Login

| Role | Username | Password |
|---|---|---|
| Admin | `sahil` | `admin123` |
| Operator | `ramesh` | `op123` |

## Modules

- **Dashboard** — stat cards (Urgent, Present Today, Active Orders, Overdue, Pending Revenue, Low Stock Items, Job Orders, Machines Running), Live Process Tracking table, Production Overview with progress bars
- **Live Process Tracking** — order process move karein (Laminate Cutting → CNC Drilling → … → Completed), operator assign, per-order process history
- **Job Orders** — kanban board (6 process columns: 01 Cutting & Drilling … 06 Routing & Testing), filters (Active/Urgent/Overdue), search, card click → Job Card
- **Parties** — party master list (Customer/Supplier), credit days, phone/GST/address, balance auto-computed (due/payable/advance/settled), overdue status (On time / n days late / n days left), add/edit/delete, search, Print, CSV download
- **Party Ledger** — per-party ledger: invoices + receipts (customer) ya purchases + payments (supplier), running balance, due status per invoice (Paid / n days left / n days late), TOTAL BILLED / TOTAL RECEIVED / BALANCE DUE stats. Ledger har party row se khulta hai
- **Operator View** — "Logged in as" + Switch Operator (sirf admin), ⚠ Emergency (sab jobs dikhao) toggle (sirf admin), JOBS AWAITING ACTION cards (CURRENT STEP + state), workflow: ▶ Start → Job Card mein auto START date/time; ⏸ Pause (reason ke saath) → ▶ Resume; ✅ Finish → Job Card mein auto FINISH date/time + next process advance (kanban bhi move); 🤝 Handover (naya worker + reason + details); MY ACTIVITY LOG
- **🔒 Role Access (Operator Limitation)** — operator role (jaise ramesh/suresh) login karne par sidebar mein **screenshot wala compact menu** dikhta hai: 🏠 Dashboard, 📋 Job Orders, 🏭 Machines, ✅ Quality, 🛠️ Operator View. **Operator in pages mein kaam kar sakta hai**: Dashboard (view), Job Orders (kanban board view), Machines (**status update** — running/idle/maintenance + current job), Quality (**QC entry add + result update**), Operator View (Start/Pause/Finish/Handover + 📦 ITEM LO). **Admin-only cheezein operator se hide + block**: naya order banana, machine add, job card (prices), inventory, billing, parties, users, cut list — sab pages redirect with flash; Create dropdown mein sirf admin items. Sirf **admin** ko pura 15-module ERP + saare kaam milte hain.
- **Production Planning** — Active Orders, Bottleneck Step, Due This Week (pcs), Capacity Utilization stats; BOTTLENECK DETECTION (step-wise order count); CAPACITY PLANNING (daily 500 pcs, weekly 3500); PRODUCTION CALENDAR next 7 days (deliveries); MACHINE/SHIFT/OPERATOR ASSIGNMENT table (save karne par Operator View mein assigned jobs dikhte hain)
- **Job Card** — PROCESS LOG panel (operator start/pause/resume/finish/handover with reasons)
- **Material Issues (Stock Out Register)** — worker koi bhi item stock se leta hai (jaise drill bit) to entry worker ke naam + job order ke saath save hoti hai; inventory balance turant update; Job Card mein 📦 MATERIAL ISSUES panel — item, qty, worker, date/time, note + TOTAL CONSUMED (print mein bhi aata hai); Inventory page par issue form (order/item/qty/worker) + 🧾 Material Issue History (kaunsa item kisne kab liya) + ↩ Undo (stock wapas); **Operator View mein 📦 ITEM LO form** — operator khud item issue karta hai (worker auto = uska naam, order sirf apna assigned, auto date/time) + MY ISSUED ITEMS list
- **BOM (Bill of Materials)** — har finished product ke liye raw material usage **manually add karo** (qty + **per-basis dropdown: per 1000 / 10000 / 100 / 10 / 1 PCB** — har item ka apna basis); jaise 10 drills per 1000 PCB, 100 sheets per 10000 PCB, 0.5 kg ink per 100 PCB; inline edit (qty + basis change + save) + delete; formula: Required = (Order Qty ÷ basis) × Qty — Job Card mein auto-calculate + save par stock deduct, 🏭 Produce par bhi same; 10000 PCB par column har row ke basis se calculate hota hai; RAW MATERIALS textarea BOM se auto-compose hota hai (user text override nahi hota)
- **Sheet Material Stock** — Job Card mein SHEET MATERIAL dropdown stock list (Inventory) se; option mein current stock dikhta hai; Save karne par stock auto-maintain: use hue sheets deduct, material/sheets change par purana wapas + naya deduct, custom material par koi effect nahi
- **PCB Dispatch** — Job Card mein DISPATCH panel: mode (Courier/By Hand/Transport/Bus Parcel/Speed Post/Other) + auto date/time + dispatched by + details (LR No./AWB/Driver) + dispatch history; Operator View mein 🚚 Dispatch button (auto date/time); kanban/done list par dispatched badge; print mein dispatch line — entry date/time/mode ke saath save rehti hai
- **Party dropdowns har jagah** — Job Card, Job Orders, Billing, Payments, Purchase Orders — sab jagah party list se select hoti hai (+ New Party link)
- **Job Card** — per-order full card:
  - Edit sections: Order Details, Material & Finish, Sizing & Layout, Commercial
  - Process table (16 processes) with start/finish date-time-name, total time auto, qty, next
  - Customer Requirement & Raw Materials, total/short qty + handover signature
  - Actions: Save, Duplicate, Complete, Delete
  - **Print Preview** — exact A4 print layout (SHIVAYA CIRCUIT PVT. LTD. header), Portrait/Landscape toggle, 🖨️ Print (window.print), ⬇️ Download (standalone printable HTML)
  - Live sync: form edits instantly update the print preview
- **Finished Products** — saved layout models ki list, load/delete karein; **➕ Add Manually** — Cut List Optimizer ke bina direct form se finished product add karo (name, model code, PCB size, PCBs X/Y, PC-to-PC gaps, panel size, PCS/panel, panels/sheet, sheets, orientation) — additional feature, existing Cut List flow unchanged; duplicate name block; **✏️ Edit** — har row par edit button, wahi form details ke saath pre-filled khulta hai aur save par update ho jata hai (BOM/FG stock/production history untouched); **🏭 Produce (FG Production)**: jab finished product ready ho jaye → qty daalo → BOM ke hisaab se raw materials inventory se auto-consume (jaise 1000 PCB SCPL-10 = 10 sheets + 2kg ink + 1 Ltr lacquer + 10 drills) aur Finished Goods stock badhta hai; Production History with item-wise consumption + ↩ Undo (stock wapas)
- **Purchase Orders** — vendor POs, status track (Pending → In Transit → Received)
- **Cut List Optimizer (PCB Panel Builder)** — screenshot-matched functions:
  - PCB-PANELBUILDER: single PCB size, PCBs across X/Y, PC-to-PC Gap X/Gap Y (N PCB jodne par N−1 gaps: 40+2.4+40+2.4+40 = 124.8mm), borders → panel size auto-compute + formula
  - Load from Finished Product
  - Panel Multiplier (Gang Panel) X/Y
  - Panel → Sheet Layout: sheet presets (1244×1044, 1240×1040, 1230×1030, 1200×1100, 1200×1000, 1100×1100, 1050×1050, Custom), inches display, panel gap/kerf, no. of sheets
  - Orientation optimization: Normal vs Rotated 90° (best auto-pick), wastage %
  - Visual previews: Panel outline + Individual PCB (multiplier/gang copies bhi dikhte hain — TOTAL WITH MULTIPLIER size green label ke saath), Best Layout sheet preview (SVG)
  - Apply Layout to Job Order · Save Layout to Model (Finished Product)
- **Inventory** — stock, min-stock reorder level, low stock alerts, quick stock update
- **Billing** — invoices, paid/overdue tracking, outstanding totals
- **Payments & Receipts** — receipts/payments register with modes
- **Employees** — staff records + attendance marking (Present / Half Day / Absent)
- **Machines** — machine status (Running / Idle / Maintenance) aur current job
- **Quality** — QC inspection records (Passed / Failed / Pending)
- **Operator View** — operators ke liye simple job + machine view
- **Production Planning** — order-wise production plans
- **Material Planning** — required vs available vs shortfall + order actions
- **Reports** — order stats, revenue, attendance, machine status, low stock, orders by party (printable)
- **Users & Access** — user management (admin/operator roles)

## Notes

- `circuit.db` auto-create hota hai demo data ke saath (delete karke reset).
- Login JS-based + token-auth hai — preview/iframe environments mein bhi kaam karta hai.
- Topbar: Search (Ctrl+K), Create quick-menu, Print, Download (CSV export of live tracking).
