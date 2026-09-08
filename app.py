"""
Shivaya Circuit — Smart ERP (PCB Manufacturing)
Run: python3 app.py  → http://0.0.0.0:8000
Login: sahil / admin123
"""
import os
import re
import datetime
import calendar
import io
import csv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from functools import wraps
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "shivaya-circuit-secret-2026"

PROCESS_STEPS = ["Laminate Cutting", "CNC Drilling", "Plating", "Etching", "Solder Mask",
                 "Silkscreen", "Routing", "Testing", "Completed"]

# ---------------- token auth (works even when cookies are blocked in preview) ----------------
_tk = URLSafeTimedSerializer(app.secret_key, salt="shivaya-auth")

def make_token(user_id, op=None):
    payload = {"uid": user_id}
    if op:
        payload["op"] = op
    return _tk.dumps(payload)

def check_token(tok):
    try:
        data = _tk.loads(tok, max_age=7 * 86400)
        return {"uid": data.get("uid"), "op": data.get("op") or None}
    except (BadSignature, SignatureExpired):
        return None

def redirect_with_token(target, code=302):
    tok = request.values.get("token") or request.args.get("token")
    if tok:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}token={tok}"
    return redirect(target, code=code)

@app.before_request
def token_login():
    if request.path == "/ping":
        return jsonify({"ok": True})  # keep-warm health check — DB touch nahi karta
    try:
        db.ensure_db()
    except Exception as _dbe:
        import traceback
        print("ENSURE_DB_ERROR:")
        traceback.print_exc()
        return ("<html><head><meta charset='utf-8'><title>Database Error</title></head>"
                "<body style='font-family:monospace;padding:24px'>"
                "<h2>⚠️ Database Connection Error</h2>"
                "<p>Neeche wali error copy karke support ko bhejo. (Vercel → Project → Logs mein bhi pura detail milega)</p>"
                "<pre style='background:#f6f6f6;padding:12px;border:1px solid #ddd'>%s: %s</pre>"
                "<p><small>Check: Vercel → Settings → Environment Variables → TURSO_URL + TURSO_AUTH_TOKEN sahi hain?</small></p>"
                "</body></html>") % (type(_dbe).__name__, _dbe), 500
    tok = request.args.get("token") or request.values.get("token")
    data = check_token(tok) if tok else None
    if not session.get("user_id"):
        if data:
            user = db.query("SELECT * FROM users WHERE id=?", (data["uid"],), one=True)
            if user:
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_role"] = user["role"]
        elif session.get("user_id"):
            pass
        else:
            return
    # operator identity: token (cookie-free) wins; session fallback
    if data and data.get("op"):
        session["op_name"] = data["op"]

# Operator ka limited access (screenshot jaisa): Dashboard, Job Orders, Machines,
# Quality, Operator View — in pages mein wo dekh + kaam kar sakta hai. Baaki sab admin-only.
OPERATOR_READ = {"dashboard", "orders", "machines", "quality", "operator_view"}
OPERATOR_WRITE = {"operator_action", "operator_issue", "machine_status", "quality", "quality_result"}


@app.before_request
def role_gate():
    """Operator sirf apne allowed pages par kaam kar sakta hai — baaki sab sirf admin."""
    if request.endpoint in ("login", "logout", "static", None):
        return
    if session.get("user_role") == "operator":
        allowed = (request.endpoint in OPERATOR_WRITE
                   or (request.endpoint in OPERATOR_READ and request.method == "GET"))
        if not allowed:
            flash("Operator access limited hai — sirf Dashboard, Job Orders, Machines, Quality aur apna Operator View.", "error")
            return redirect_with_token(url_for("operator_view"))


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("user_role") != "admin":
            flash("Only admin can access this page.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_globals():
    today = datetime.date.today()
    try:
        parties = db.query("SELECT id, name, ptype, credit_days FROM parties ORDER BY name")
    except Exception:
        parties = []
    return {"today": today.strftime("%A, %d %B %Y"),
            "today_iso": today.isoformat(),
            "current_user_name": session.get("user_name", ""),
            "current_user_role": session.get("user_role", ""),
            "all_parties": parties,
            "party_names": [p["name"] for p in parties]}


@app.template_filter("inr")
def inr_filter(value):
    try:
        v = round(float(value), 2)
        neg = v < 0
        v = abs(v)
        s = f"{v:.2f}"
        int_part, dec = s.split(".")
        dec = dec.rstrip("0")
        if len(int_part) > 3:  # Indian grouping: 1,54,060.8
            last3 = int_part[-3:]
            rest = int_part[:-3]
            groups = [last3]
            while rest:
                groups.insert(0, rest[-2:])
                rest = rest[:-2]
            int_part = ",".join(groups)
        out = int_part + (("." + dec) if dec else "")
        return ("-₹" if neg else "₹") + out
    except (TypeError, ValueError):
        return "₹0"


@app.template_filter("dash")
def dash_filter(value):
    return value or "—"


@app.template_filter("gfmt")
def gfmt_filter(value):
    try:
        return f"{value:g}"
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------- login
@app.route("/ping")
def ping():
    return jsonify({"ok": True})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.query("SELECT * FROM users WHERE username=? AND password=?", (username, password), one=True)
        if user:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user["role"]
            token = make_token(user["id"])
            home = "operator_view" if user["role"] == "operator" else "dashboard"
            target = url_for(home) + f"?token={token}"
            if request.form.get("ajax") == "1":
                return jsonify(ok=True, token=token, redirect=target, name=user["name"])
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(target)
        if request.form.get("ajax") == "1":
            return jsonify(ok=False, error="Invalid username or password."), 401
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- dashboard
@app.route("/")
@login_required
def dashboard():
    today = datetime.date.today().isoformat()
    q = request.args.get("q", "").strip()

    # 13 queries -> 1 hi round trip (turso batch) — page 10x fast
    sqls = [
        ("SELECT COUNT(*) c FROM employees WHERE status='active'", ()),
        ("SELECT COUNT(*) c FROM attendance WHERE date=? AND status='present'", (today,)),
        ("SELECT COUNT(*) c FROM orders WHERE status!='done'", ()),
        ("SELECT COUNT(*) c FROM orders", ()),
        ("SELECT COUNT(*) c FROM orders WHERE status='pending' AND priority='urgent'", ()),
        ("SELECT COALESCE(SUM(amount),0) s FROM billing WHERE status='overdue'", ()),
        ("SELECT COALESCE(SUM(amount),0) s FROM billing WHERE status!='paid'", ()),
        ("SELECT COUNT(*) c FROM inventory WHERE stock <= min_stock", ()),
        ("SELECT COUNT(*) c FROM machines WHERE status='running'", ()),
        ("SELECT COUNT(*) c FROM machines", ()),
    ]
    if q:
        sqls.append(("SELECT * FROM orders WHERE status IN ('pending','done') AND (order_no LIKE ? OR party LIKE ? OR product LIKE ?) "
                     "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 4",
                     (f"%{q}%", f"%{q}%", f"%{q}%")))
    else:
        sqls.append(("SELECT * FROM orders WHERE status IN ('pending','done') "
                     "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 4", ()))
    sqls.append(("SELECT * FROM orders WHERE status IN ('pending','done') "
                 "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 4", ()))
    sqls.append(("SELECT * FROM purchase_orders WHERE status!='received' ORDER BY id DESC LIMIT 3", ()))

    rows = db.multi(sqls)
    (total_emp, present, active_orders, total_orders, urgent,
     overdue_rev, pending_rev, low_stock, machines_running, total_machines,
     tracking, overview, notices) = rows
    total_emp = total_emp[0]["c"]
    present = present[0]["c"]
    active_orders = active_orders[0]["c"]
    total_orders = total_orders[0]["c"]
    urgent = urgent[0]["c"]
    overdue_rev = overdue_rev[0]["s"]
    pending_rev = pending_rev[0]["s"]
    low_stock = low_stock[0]["c"]
    machines_running = machines_running[0]["c"]
    total_machines = total_machines[0]["c"]

    return render_template("dashboard.html", active="dashboard",
                           total_emp=total_emp, present=present,
                           active_orders=active_orders, total_orders=total_orders, urgent=urgent,
                           overdue_rev=overdue_rev, pending_rev=pending_rev, low_stock=low_stock,
                           machines_running=machines_running, total_machines=total_machines,
                           tracking=tracking, overview=overview, notices=notices, q=q)


# ---------------------------------------------------------------- live process tracking
@app.route("/tracking")
@login_required
def tracking():
    q = request.args.get("q", "").strip()
    if q:
        rows = db.query(
            "SELECT * FROM orders WHERE order_no LIKE ? OR party LIKE ? OR product LIKE ? ORDER BY "
            "CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC", (f"%{q}%", f"%{q}%", f"%{q}%"))
    else:
        rows = db.query("SELECT * FROM orders ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC")
    operators = db.query("SELECT DISTINCT name FROM employees WHERE department='Production' ORDER BY name")
    rows = [dict(r) for r in rows]
    for r in rows:
        r["step_idx"] = PROCESS_STEPS.index(r["current_process"]) if r["current_process"] in PROCESS_STEPS else 0
    return render_template("tracking.html", active="tracking", orders=rows, q=q,
                           PROCESS_STEPS=PROCESS_STEPS, operators=operators)


@app.route("/tracking/<int:order_id>/move", methods=["POST"])
@login_required
def tracking_move(order_id):
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        flash("Order not found.", "error")
        return redirect_with_token(url_for("tracking"))
    next_step = request.form.get("next_process", "")
    operator = request.form.get("operator", "").strip() or session.get("user_name", "")
    progress = int(request.form.get("progress", 0) or 0)
    if next_step:
        db.execute("UPDATE orders SET current_process=?, operator=?, progress=?, status=? WHERE id=?",
                   (next_step, operator, max(order["progress"], progress), "done" if next_step == "Completed" else "pending", order_id))
        db.execute("UPDATE process_log SET status='done' WHERE order_id=? AND process=?", (order_id, order["current_process"]))
        db.execute("INSERT INTO process_log (order_id, process, status, operator, updated_on) VALUES (?,?,?,?,?)",
                   (order_id, next_step, "done" if next_step == "Completed" else "active", operator,
                    datetime.date.today().isoformat()))
        flash(f"{order['order_no']} moved to {next_step}.", "success")
    return redirect_with_token(url_for("tracking"))


@app.route("/tracking/<int:order_id>/progress", methods=["POST"])
@login_required
def tracking_progress(order_id):
    try:
        progress = max(0, min(100, int(request.form.get("progress", 0) or 0)))
    except ValueError:
        progress = 0
    db.execute("UPDATE orders SET progress=? WHERE id=?", (progress, order_id))
    return redirect_with_token(url_for("tracking"))


@app.route("/tracking/<int:order_id>/history")
@login_required
def tracking_history(order_id):
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    log = db.query("SELECT * FROM process_log WHERE order_id=? ORDER BY id", (order_id,))
    return render_template("history.html", active="tracking", order=order, log=log)


# ---------------------------------------------------------------- job orders
KANBAN_COLS = [
    ("01", "Cutting & Drilling"),
    ("02", "PTH & Lamination"),
    ("03", "Circuit & Plating"),
    ("04", "Masking & Solder Mask"),
    ("05", "Printing & Finish"),
    ("06", "Routing & Testing"),
]


def kanban_col(process):
    p = (process or "").lower()
    if any(x in p for x in ["laminate", "cutting", "drilling"]):
        return 0
    if any(x in p for x in ["pth", "laminat"]):
        return 1
    if any(x in p for x in ["circuit", "plating", "etch", "center"]):
        return 2
    if any(x in p for x in ["mask", "solder"]):
        return 3
    if any(x in p for x in ["print", "silkscreen", "finish", "hal", "lacker", "osp"]):
        return 4
    return 5


@app.route("/orders", methods=["GET", "POST"])
@login_required
def orders():
    if request.method == "POST":
        f = request.form
        if f.get("party", "").strip():
            # product dropdown (Finished Products) -> name + model code
            prod_val = f.get("product", "").strip()
            pmodel = None
            if (f.get("product_id") or "").isdigit() and int(f.get("product_id") or 0) > 0:
                pmodel = db.query("SELECT * FROM product_models WHERE id=?",
                                  (int(f.get("product_id")),), one=True)
            if pmodel:
                party_model = pmodel["name"]
                model_code = pmodel["model_code"] or ""
                prod_display = f"{party_model} - {model_code}".strip(" -")
            elif prod_val:
                party_model, model_code = prod_val, ""
                prod_display = prod_val
            else:
                party_model, model_code, prod_display = "", "", ""
            qty_pcs = int(f.get("qty", 0) or 0)
            product_str = f"{prod_display} · {qty_pcs} pcs" if prod_display else ""
            count = db.query("SELECT COUNT(*) c FROM orders", one=True)["c"]
            new_id = db.execute(
                "INSERT INTO orders (order_no, party, board, product, qty, value, current_process, status, progress, priority, delivery_date, operator, started_qty, finished_qty, created_on) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"#{count + 1}", f.get("party").strip(), f.get("board", "Single Side"),
                 product_str, qty_pcs, float(f.get("value", 0) or 0),
                 PROCESS_STEPS[0], "pending", 0, f.get("priority", "normal"),
                 f.get("delivery_date", ""), "", 0, 0, datetime.date.today().isoformat()))
            if pmodel:
                # job card ko finished product ki poori sizing se pre-fill karo
                db.execute(
                    "INSERT OR IGNORE INTO jobcard (order_id, party_model, model, odate, exp_delivery, price, total_qty, "
                    "actual_pcb_x, actual_pcb_y, panel_x, panel_y, panels_per_sheet, sheets, pcs_panel, sheet_len, sheet_w) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id, party_model, model_code, datetime.date.today().isoformat(),
                     f.get("delivery_date", ""), float(f.get("value", 0) or 0), qty_pcs,
                     pmodel["pcb_len"], pmodel["pcb_w"], pmodel["panel_len"], pmodel["panel_w"],
                     pmodel["panels_sheet"], pmodel["sheets"], pmodel["pcs_panel"],
                     pmodel["sheet_len"], pmodel["sheet_w"]))
            else:
                db.execute("INSERT OR IGNORE INTO jobcard (order_id, party_model, odate, exp_delivery, price, total_qty) "
                           "VALUES (?,?,?,?,?,?)",
                           (new_id, party_model, datetime.date.today().isoformat(),
                            f.get("delivery_date", ""), float(f.get("value", 0) or 0), qty_pcs))
            flash("Job order created.", "success")
        return redirect_with_token(url_for("orders"))
    q = request.args.get("q", "").strip()
    filt = request.args.get("filter", "active")
    today = datetime.date.today().isoformat()
    where = ""
    params = ()
    if q:
        where = "WHERE party LIKE ? OR product LIKE ? OR order_no LIKE ?"
        params = (f"%{q}%", f"%{q}%", f"%{q}%")
    rows = db.query(f"SELECT * FROM orders {where} ORDER BY id DESC", params)
    if filt == "urgent":
        rows = [r for r in rows if r["priority"] == "urgent" and r["status"] == "pending"]
    elif filt == "overdue":
        rows = [r for r in rows if r["status"] == "pending" and r["delivery_date"] and r["delivery_date"] < today]
    else:
        rows = [r for r in rows if r["status"] != "done"]
    # 4 queries -> 1 round trip (turso batch)
    jc_rows, dispatch_rows, models, done_rows = db.multi([
        ("SELECT * FROM jobcard_process", ()),
        ("SELECT * FROM dispatch_log", ()),
        ("SELECT * FROM product_models ORDER BY name", ()),
        ("SELECT * FROM orders WHERE status='done' ORDER BY id DESC LIMIT 4", ()),
    ])
    jc_map = {}
    for pr in jc_rows:
        jc_map.setdefault(pr["order_id"], []).append(pr)

    def _jc_status(r):
        """current process ka naam + status (done/running/pending) — work status se."""
        cur = (r["current_process"] or "").lower()
        plist = jc_map.get(r["id"], [])
        prow = None
        for pr in plist:
            if cur and (pr["process"] or "").lower() == cur:
                prow = pr
                break
        if prow is None:
            prow = next((pr for pr in plist if not pr["end_dt"]), None)
        if prow is None and plist:
            prow = plist[0]
        if prow:
            return prow["process"], ("done" if prow["end_dt"] else "running" if prow["start_dt"] else "pending")
        return (r["current_process"] or "—"), "pending"

    dmap = {}
    for dr in dispatch_rows:
        if dr["order_id"] not in dmap or (dmap[dr["order_id"]]["id"] or 0) < dr["id"]:
            dmap[dr["order_id"]] = dr
    cols = [[] for _ in KANBAN_COLS]
    for r in rows:
        r = dict(r)
        r["disp"] = dmap.get(r["id"])
        r["cur_proc"], r["cur_status"] = _jc_status(r)
        cols[kanban_col(r["current_process"])].append(r)
    done = [dict(r) for r in done_rows]
    for r in done:
        r["disp"] = dmap.get(r["id"])
    return render_template("orders.html", active="orders", cols=cols, done=done, q=q, filt=filt,
                           KANBAN_COLS=KANBAN_COLS, PROCESS_STEPS=PROCESS_STEPS,
                           models=models, show_add=request.args.get("add"))


# ---------------------------------------------------------------- purchase orders
@app.route("/purchase-orders", methods=["GET", "POST"])
@login_required
def purchase_orders():
    if request.method == "POST":
        f = request.form
        if f.get("vendor", "").strip():
            count = db.query("SELECT COUNT(*) c FROM purchase_orders", one=True)["c"]
            db.execute(
                "INSERT INTO purchase_orders (po_no, vendor, item, qty, amount, status, date) VALUES (?,?,?,?,?,?,?)",
                (f"PO-{304 + count}", f.get("vendor").strip(), f.get("item", "").strip(), f.get("qty", "").strip(),
                 float(f.get("amount", 0) or 0), f.get("status", "pending"), datetime.date.today().isoformat()))
            flash("Purchase order created.", "success")
        return redirect_with_token(url_for("purchase_orders"))
    rows = db.query("SELECT * FROM purchase_orders ORDER BY id DESC")
    edit_po = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_po = db.query("SELECT * FROM purchase_orders WHERE id=?", (int(eid),), one=True)
    return render_template("purchase_orders.html", active="purchase", rows=rows,
                           show_add=request.args.get("add"), edit_po=edit_po)


@app.route("/purchase-orders/<int:po_id>/edit", methods=["POST"])
@login_required
def po_edit(po_id):
    f = request.form
    po_no = (f.get("po_no") or "").strip()
    vendor = (f.get("vendor") or "").strip()
    if not vendor:
        flash("Vendor zaroori hai.", "error")
        return redirect_with_token(url_for("purchase_orders", edit=po_id))
    try:
        db.execute(
            "UPDATE purchase_orders SET po_no=?, vendor=?, item=?, qty=?, amount=?, status=?, date=? WHERE id=?",
            (po_no, vendor, (f.get("item") or "").strip(), (f.get("qty") or "").strip(),
             float(f.get("amount", 0) or 0), f.get("status", "pending"),
             f.get("date") or datetime.date.today().isoformat(), po_id))
        flash(f"PO {po_no} update ho gaya ✅", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("purchase_orders"))


@app.route("/purchase-orders/<int:po_id>/delete", methods=["POST"])
@login_required
def po_delete(po_id):
    po = db.query("SELECT * FROM purchase_orders WHERE id=?", (po_id,), one=True)
    if not po:
        flash("Purchase order nahi mila.", "error")
    else:
        db.execute("DELETE FROM purchase_orders WHERE id=?", (po_id,))
        flash(f"PO {po['po_no']} delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("purchase_orders"))


@app.route("/purchase-orders/<int:po_id>/status", methods=["POST"])
@login_required
def po_status(po_id):
    st = request.form.get("status")
    if st in ("pending", "in_transit", "received", "cancelled"):
        db.execute("UPDATE purchase_orders SET status=? WHERE id=?", (st, po_id))
    return redirect_with_token(url_for("purchase_orders"))


# ---------------------------------------------------------------- cut list optimizer (PCB Panel Builder)
FIELD_KEYS = ["pcb_len", "pcb_w", "pcbs_x", "pcbs_y", "gap_x", "gap_y",
              "border_l", "border_r", "border_t", "border_b",
              "gang_x", "gang_y", "sheet_len", "sheet_w", "kerf_x", "kerf_y", "sheets", "use",
              "panel_len", "panel_w"]
DEFAULTS = {"pcb_len": "40", "pcb_w": "50", "pcbs_x": "10", "pcbs_y": "5",
            "gap_x": "0", "gap_y": "0",
            "border_l": "0", "border_r": "0", "border_t": "5", "border_b": "5",
            "gang_x": "1", "gang_y": "1", "sheet_len": "1200", "sheet_w": "1000",
            "kerf_x": "2", "kerf_y": "2", "sheets": "1", "use": "1",
            "panel_len": "400", "panel_w": "260"}
SHEET_PRESETS = ["1244x1044", "1240x1040", "1230x1030", "1200x1100", "1200x1000", "1100x1100", "1050x1050"]


def _f(d, k, default=0.0):
    try:
        return float(d.get(k) or 0)
    except (ValueError, TypeError):
        return default


def _i(d, k, default=0):
    try:
        return int(float(d.get(k) or 0))
    except (ValueError, TypeError):
        return default


def compute_layout(p):
    """PCB panel builder - full computation. Returns dict or None."""
    pcb_len, pcb_w = _f(p, "pcb_len"), _f(p, "pcb_w")
    pcbs_x, pcbs_y = _i(p, "pcbs_x"), _i(p, "pcbs_y")
    gap_x, gap_y = _f(p, "gap_x"), _f(p, "gap_y")
    border_l, border_r = _f(p, "border_l"), _f(p, "border_r")
    border_t, border_b = _f(p, "border_t"), _f(p, "border_b")
    gang_x, gang_y = max(1, _i(p, "gang_x", 1)), max(1, _i(p, "gang_y", 1))
    sheet_len, sheet_w = _f(p, "sheet_len"), _f(p, "sheet_w")
    kerf_x, kerf_y = _f(p, "kerf_x"), _f(p, "kerf_y")
    sheets = max(1, _i(p, "sheets", 1))
    panel_len_in, panel_w_in = _f(p, "panel_len"), _f(p, "panel_w")
    use_locked = p.get("use") == "1"

    if min(pcb_len, pcb_w, pcbs_x, pcbs_y, sheet_len, sheet_w) <= 0:
        return None

    # --- panel size: locked (computed from PCB builder) or manual ---
    # PC to PC gap: X=40, 3 jodne par beech me 2 gaps -> 40+2.4+40+2.4+40 = 124.8
    if use_locked or panel_len_in <= 0 or panel_w_in <= 0:
        panel_len = pcb_len * pcbs_x + gap_x * (pcbs_x - 1) + border_l + border_r
        panel_w = pcb_w * pcbs_y + gap_y * (pcbs_y - 1) + border_t + border_b
        locked = True
    else:
        panel_len, panel_w = panel_len_in, panel_w_in
        locked = False

    pcs_panel = pcbs_x * pcbs_y
    formula = (f"({pcb_len:g}x{pcbs_x} +{gap_x:g}x{pcbs_x - 1} gap +{border_l:g}+{border_r:g} border={panel_len:g}mm, "
               f"{pcb_w:g}x{pcbs_y} +{gap_y:g}x{pcbs_y - 1} gap +{border_t:g}+{border_b:g} border={panel_w:g}mm)")

    # --- gang panel unit ---
    gang_len = panel_len * gang_x + kerf_x * (gang_x - 1)
    gang_w = panel_w * gang_y + kerf_y * (gang_y - 1)

    def fits(unit_l, unit_w):
        if unit_l <= 0 or unit_w <= 0:
            return 0, 0
        nx = int((sheet_len + kerf_x) // (unit_l + kerf_x))
        ny = int((sheet_w + kerf_y) // (unit_w + kerf_y))
        return nx, ny

    nx, ny = fits(gang_len, gang_w)
    normal_panels = nx * ny * gang_x * gang_y
    rx, ry = fits(gang_w, gang_len)
    rotated_panels = rx * ry * gang_x * gang_y

    # --- mixed rows: normal rows + rotated rows on one sheet ---
    per_normal = int((sheet_len + kerf_x) // (gang_len + kerf_x))
    per_rot = int((sheet_len + kerf_x) // (gang_w + kerf_x))
    h_n, h_r = gang_w + kerf_y, gang_len + kerf_y
    max_n = int((sheet_w + kerf_y) // h_n) if h_n > 0 else 0
    max_m = int((sheet_w + kerf_y) // h_r) if h_r > 0 else 0
    mixed_n = mixed_m = 0
    mixed_panels = 0
    for n in range(max_n + 1):
        for m in range(max_m + 1):
            if n == 0 and m == 0:
                continue
            height = n * h_n + m * h_r - kerf_y
            if height <= sheet_w + 1e-9:
                panels = (n * per_normal + m * per_rot) * gang_x * gang_y
                if panels > mixed_panels:
                    mixed_panels, mixed_n, mixed_m = panels, n, m

    if mixed_panels > normal_panels and mixed_panels > rotated_panels:
        best = "mixed"
        panels_per_sheet = mixed_panels
    elif rotated_panels > normal_panels:
        best, panels_per_sheet = "rotated", rotated_panels
    else:
        best, panels_per_sheet = "normal", normal_panels

    grid_x, grid_y, cell_len, cell_w = nx, ny, gang_len, gang_w
    if best == "rotated":
        grid_x, grid_y, cell_len, cell_w = rx, ry, gang_w, gang_len
    pcs_per_sheet = panels_per_sheet * pcs_panel
    total_panels = panels_per_sheet * sheets
    total_pcs = pcs_per_sheet * sheets

    used = total_panels * panel_len * panel_w
    sheet_area = sheets * sheet_len * sheet_w
    wastage = round(100 * (sheet_area - used) / sheet_area, 1) if sheet_area else 0
    inches = f"{sheet_len / 25.4:.1f}\u2033 \u00d7 {sheet_w / 25.4:.1f}\u2033"

    return {
        "pcb_len": pcb_len, "pcb_w": pcb_w, "pcbs_x": pcbs_x, "pcbs_y": pcbs_y,
        "gap_x": gap_x, "gap_y": gap_y,
        "border_l": border_l, "border_r": border_r, "border_t": border_t, "border_b": border_b,
        "gang_x": gang_x, "gang_y": gang_y,
        "sheet_len": sheet_len, "sheet_w": sheet_w, "kerf_x": kerf_x, "kerf_y": kerf_y, "sheets": sheets,
        "panel_len": panel_len, "panel_w": panel_w, "locked": locked,
        "pcs_panel": pcs_panel, "formula": formula,
        "nx": nx, "ny": ny, "normal_panels": normal_panels,
        "rx": rx, "ry": ry, "rotated_panels": rotated_panels,
        "per_normal": per_normal, "per_rot": per_rot,
        "mixed_n": mixed_n, "mixed_m": mixed_m, "mixed_panels": mixed_panels,
        "best": best, "grid_x": grid_x, "grid_y": grid_y,
        "cell_len": cell_len, "cell_w": cell_w,
        "gang_len": gang_len, "gang_w": gang_w,
        "panels_per_sheet": panels_per_sheet, "pcs_per_sheet": pcs_per_sheet,
        "total_panels": total_panels, "total_pcs": total_pcs,
        "wastage": wastage, "inches": inches,
    }


def svg_panel_preview(r):
    """Panel outline + individual PCB grid, with multiplier (gang) copies + total size."""
    W, H, pad = 430, 300, 26
    pl, pw = r["panel_len"], r["panel_w"]
    gx, gy = r["gang_x"], r["gang_y"]
    kx, ky = r["kerf_x"], r["kerf_y"]
    gl = pl * gx + kx * (gx - 1)   # total panel size with multiplier
    gw = pw * gy + ky * (gy - 1)
    scale = min((W - 2 * pad) / gl, (H - 2 * pad) / gw)
    P, Q = pl * scale, pw * scale
    Kx, Ky = kx * scale, ky * scale
    GL, GW = gl * scale, gw * scale
    x0, y0 = pad + (W - 2 * pad - GL) / 2, pad + (H - 2 * pad - GW) / 2
    cw, ch = r["pcb_len"] * scale, r["pcb_w"] * scale
    gxs, gys = r["gap_x"] * scale, r["gap_y"] * scale
    bx, by = r["border_l"] * scale, r["border_t"] * scale
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']
    for a in range(gx):
        for b in range(gy):
            xp, yp = x0 + a * (P + Kx), y0 + b * (Q + Ky)
            s.append(f'<rect x="{xp:.1f}" y="{yp:.1f}" width="{P:.1f}" height="{Q:.1f}" fill="#fdf3df" stroke="#d97706" stroke-width="2" rx="3"/>')
            if cw > 2.4 and ch > 2.4:
                for i in range(r["pcbs_x"]):
                    for j in range(r["pcbs_y"]):
                        s.append(f'<rect x="{xp + bx + i * (cw + gxs):.1f}" y="{yp + by + j * (ch + gys):.1f}" width="{cw:.1f}" height="{ch:.1f}" fill="#fbbf24" fill-opacity="0.5" stroke="#f59e0b" stroke-width="0.8"/>')
                if r["border_l"] or r["border_r"] or r["border_t"] or r["border_b"]:
                    cw2 = r["pcbs_x"] * cw + (r["pcbs_x"] - 1) * gxs
                    ch2 = r["pcbs_y"] * ch + (r["pcbs_y"] - 1) * gys
                    s.append(f'<rect x="{xp + bx:.1f}" y="{yp + by:.1f}" width="{cw2:.1f}" height="{ch2:.1f}" fill="none" stroke="#9a8a5a" stroke-width="1" stroke-dasharray="4 3"/>')
    if gx > 1 or gy > 1:
        s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{GL:.1f}" height="{GW:.1f}" fill="none" stroke="#16a34a" stroke-width="1.8" stroke-dasharray="6 4" rx="5"/>')
        s.append(f'<text x="{x0 + GL/2:.0f}" y="{y0 - 6:.1f}" text-anchor="middle" font-size="11" fill="#16a34a" font-family="Segoe UI,Arial">TOTAL WITH MULTIPLIER: {gl:g}\u00d7{gw:g} mm ({gx}\u00d7{gy} + kerf)</text>')
    if (gxs > 0.1 or gys > 0.1) and Q > 24:
        s.append(f'<text x="{x0 + P/2:.0f}" y="{y0 + Q - 10:.1f}" text-anchor="middle" font-size="10" fill="#b45309" font-family="Segoe UI,Arial">gap {r["gap_x"]:g}\u00d7{r["gap_y"]:g} mm</text>')
    lbl = f'Panel {pl:g}\u00d7{pw:g} mm \u00b7 {r["pcs_panel"]} PCBs ({r["pcbs_x"]}\u00d7{r["pcbs_y"]})'
    if gx > 1 or gy > 1:
        lbl += f' \u00b7 {gx}\u00d7{gy} gang \u2192 <tspan fill="#16a34a" font-weight="700">Total {gl:g}\u00d7{gw:g} mm</tspan>'
    s.append(f'<text x="{W/2:.0f}" y="{H - 6:.0f}" text-anchor="middle" font-size="12.5" fill="#8a8f98" font-family="Segoe UI,Arial">{lbl}</text>')
    s.append('</svg>')
    return "".join(s)


def _svg_gang(s, cx, cy, cl, cw, gx, gy, kx, ky, fill, stroke, sw_):
    s.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cl:.1f}" height="{cw:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw_}" rx="2"/>')
    if gx > 1 or gy > 1:
        p = (cl - kx * (gx - 1)) / gx
        q = (cw - ky * (gy - 1)) / gy
        for a in range(gx):
            for b in range(gy):
                s.append(f'<rect x="{cx + a * (p + kx):.1f}" y="{cy + b * (q + ky):.1f}" width="{p:.1f}" height="{q:.1f}" fill="#fde68a" stroke="#f59e0b" stroke-width="0.7"/>')


def svg_sheet_preview(r):
    """Best layout preview on the sheet (uniform grid or mixed rows)."""
    W, H, pad = 430, 380, 26
    sl, sw = r["sheet_len"], r["sheet_w"]
    scale = min((W - 2 * pad) / sl, (H - 2 * pad) / sw)
    S, T = sl * scale, sw * scale
    x0, y0 = pad + (W - 2 * pad - S) / 2, pad + (H - 2 * pad - T) / 2
    gx, gy = r["gang_x"], r["gang_y"]
    kx, ky = r["kerf_x"] * scale, r["kerf_y"] * scale
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']
    s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{S:.1f}" height="{T:.1f}" fill="#fffdf5" stroke="#b3ac99" stroke-width="2"/>')
    if r["best"] == "mixed":
        # normal rows (amber) then rotated rows (blue)
        y = y0
        for _row in range(r["mixed_n"]):
            cl, cw = r["gang_len"] * scale, r["gang_w"] * scale
            for i in range(r["per_normal"]):
                _svg_gang(s, x0 + i * (cl + kx), y, cl, cw, gx, gy, kx, ky, "#f7c948", "#b45309", 1.4)
            y += cw + ky
        for _row in range(r["mixed_m"]):
            cl, cw = r["gang_w"] * scale, r["gang_len"] * scale
            for i in range(r["per_rot"]):
                _svg_gang(s, x0 + i * (cl + kx), y, cl, cw, gx, gy, kx, ky, "#93c5fd", "#1d4ed8", 1.4)
            y += cw + ky
        cap = (f"Normal rows \u00b7 Rotated rows \u2014 {r['mixed_n']}\u00d7 row of {r['per_normal']} + "
               f"{r['mixed_m']}\u00d7 row of {r['per_rot']} = {r['panels_per_sheet']} panels \u00b7 {r['wastage']}% waste")
    else:
        cl, cw = r["cell_len"] * scale, r["cell_w"] * scale
        for i in range(r["grid_x"]):
            for j in range(r["grid_y"]):
                cx, cy = x0 + i * (cl + kx), y0 + j * (cw + ky)
                _svg_gang(s, cx, cy, cl, cw, gx, gy, kx, ky, "#f7c948", "#b45309", 1.4)
        cap = f"Sheet {sl:g}\u00d7{sw:g} mm \u00b7 {r['grid_x']}\u00d7{r['grid_y']} = {r['panels_per_sheet']} panels \u00b7 {r['wastage']}% waste"
    s.append(f'<text x="{W/2:.0f}" y="{H - 6:.0f}" text-anchor="middle" font-size="12.5" fill="#8a8f98" font-family="Segoe UI,Arial">{cap}</text>')
    s.append('</svg>')
    return "".join(s)


@app.route("/cutlist", methods=["GET", "POST"])
@login_required
def cutlist():
    fields = {}
    load_model_id = request.args.get("model") or (request.values.get("model_id") if request.method == "POST" else None)

    if request.method == "POST":
        f = request.form
        action = f.get("action", "calculate")
        if action == "load" and f.get("model_id"):
            return redirect_with_token(url_for("cutlist", model=f.get("model_id")))
        fields = {k: f.get(k, "") for k in FIELD_KEYS}
        result = compute_layout(fields)

        if action == "save_model" and result:
            name = f.get("save_name", "").strip()
            sel = f.get("save_select", "").strip()
            if sel:
                try:
                    model = db.query("SELECT * FROM product_models WHERE id=?", (int(sel),), one=True)
                except ValueError:
                    model = None
                if model:
                    name = name or model["name"]
                    db.execute(
                        "UPDATE product_models SET name=?, pcb_len=?, pcb_w=?, pcbs_x=?, pcbs_y=?, gap_x=?, gap_y=?, "
                        "border_l=?, border_r=?, border_t=?, border_b=?, gang_x=?, gang_y=?, sheet_len=?, sheet_w=?, "
                        "panel_len=?, panel_w=?, kerf_x=?, kerf_y=?, orientation=?, pcs_panel=?, panels_sheet=?, sheets=? WHERE id=?",
                        (name, result["pcb_len"], result["pcb_w"], result["pcbs_x"], result["pcbs_y"],
                         result["gap_x"], result["gap_y"],
                         result["border_l"], result["border_r"], result["border_t"], result["border_b"],
                         result["gang_x"], result["gang_y"], result["sheet_len"], result["sheet_w"],
                         result["panel_len"], result["panel_w"], result["kerf_x"], result["kerf_y"],
                         result["best"], result["pcs_panel"], result["panels_per_sheet"], result["sheets"], model["id"]))
                    flash(f"Model '{name}' updated.", "success")
                else:
                    flash("Select a valid finished product.", "error")
            elif name:
                db.execute(
                    "INSERT INTO product_models (name, pcb_len, pcb_w, pcbs_x, pcbs_y, gap_x, gap_y, border_l, "
                    "border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, panel_len, panel_w, kerf_x, "
                    "kerf_y, orientation, pcs_panel, panels_sheet, sheets, created_on) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, result["pcb_len"], result["pcb_w"], result["pcbs_x"], result["pcbs_y"],
                     result["gap_x"], result["gap_y"],
                     result["border_l"], result["border_r"], result["border_t"], result["border_b"],
                     result["gang_x"], result["gang_y"], result["sheet_len"], result["sheet_w"],
                     result["panel_len"], result["panel_w"], result["kerf_x"], result["kerf_y"],
                     result["best"], result["pcs_panel"], result["panels_per_sheet"], result["sheets"],
                     datetime.date.today().isoformat()))
                flash(f"Model '{name}' saved to Finished Products.", "success")
            else:
                flash("Enter a name to save as finished product.", "error")

        if action == "apply_order" and result:
            try:
                order_id = int(f.get("apply_order") or 0)
                order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
                if order:
                    info = (f"{result['pcs_panel']} PCS/panel \u00b7 {result['panels_per_sheet']} panels/sheet "
                            f"({result['sheet_len']:g}\u00d7{result['sheet_w']:g}) \u00b7 {result['best']}")
                    db.execute("UPDATE orders SET cutlist_info=?, qty=?, product=? WHERE id=?",
                               (info, result["total_pcs"],
                                f"{order['product']} \u00b7 Panel {result['panel_len']:g}\u00d7{result['panel_w']:g}mm", order_id))
                    flash(f"Layout applied to {order['order_no']} ({order['party']}). Qty set to {result['total_pcs']} pcs.", "success")
                else:
                    flash("Select a valid job order.", "error")
            except (ValueError, TypeError):
                flash("Select a valid job order.", "error")

        from urllib.parse import urlencode
        params = urlencode({k: fields.get(k, "") for k in FIELD_KEYS})
        return redirect_with_token(url_for("cutlist") + "?" + params)

    # GET
    q = request.args
    if load_model_id:
        try:
            model = db.query("SELECT * FROM product_models WHERE id=?", (int(load_model_id),), one=True)
            if model:
                for k in FIELD_KEYS:
                    val = model[k] if k in model.keys() and model[k] is not None else ""
                    fields[k] = str(val)
                fields["use"] = "1"
                flash(f"Loaded model '{model['name']}'.", "success")
        except (ValueError, TypeError):
            pass
    elif q.get("pcb_len"):
        fields = {k: q.get(k, "") for k in FIELD_KEYS}

    result = compute_layout(fields) if fields else compute_layout(DEFAULTS)
    if not fields:
        fields = dict(DEFAULTS)
    if result is None:
        result = compute_layout(DEFAULTS)

    models = db.query("SELECT * FROM product_models ORDER BY id DESC")
    orders = db.query("SELECT id, order_no, party, product FROM orders WHERE status!='done' ORDER BY id DESC")
    svg_panel = svg_panel_preview(result) if result else ""
    svg_sheet = svg_sheet_preview(result) if result else ""
    return render_template("cutlist.html", active="cutlist", fields=fields, result=result,
                           models=models, orders=orders, SHEET_PRESETS=SHEET_PRESETS,
                           svg_panel=svg_panel, svg_sheet=svg_sheet)


# ---------------------------------------------------------------- finished products
@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        f = request.form
        name = (f.get("name") or "").strip()
        if not name:
            flash("Product name zaroori hai.", "error")
            return redirect_with_token(url_for("products"))
        vals = (name, (f.get("model_code") or "").strip(),
                float(f.get("pcb_len", 0) or 0), float(f.get("pcb_w", 0) or 0),
                int(f.get("pcbs_x", 0) or 0), int(f.get("pcbs_y", 0) or 0),
                float(f.get("gap_x", 0) or 0), float(f.get("gap_y", 0) or 0),
                float(f.get("border_l", 0) or 0), float(f.get("border_r", 0) or 0),
                float(f.get("border_t", 0) or 0), float(f.get("border_b", 0) or 0),
                max(1, int(f.get("gang_x", 1) or 1)), max(1, int(f.get("gang_y", 1) or 1)),
                float(f.get("sheet_len", 0) or 0), float(f.get("sheet_w", 0) or 0),
                float(f.get("panel_len", 0) or 0), float(f.get("panel_w", 0) or 0),
                float(f.get("kerf_x", 2) or 2), float(f.get("kerf_y", 2) or 2),
                (f.get("orientation") or "normal").strip(),
                int(f.get("pcs_panel", 0) or 0), int(f.get("panels_sheet", 0) or 0),
                int(f.get("sheets", 1) or 1))
        # ---- EDIT: existing product update ----
        try:
            edit_id = int(f.get("edit_id", 0) or 0)
        except ValueError:
            edit_id = 0
        try:
            if edit_id:
                db.execute(
                    "UPDATE product_models SET name=?, model_code=?, pcb_len=?, pcb_w=?, pcbs_x=?, pcbs_y=?, "
                    "gap_x=?, gap_y=?, border_l=?, border_r=?, border_t=?, border_b=?, gang_x=?, gang_y=?, "
                    "sheet_len=?, sheet_w=?, panel_len=?, panel_w=?, kerf_x=?, kerf_y=?, orientation=?, "
                    "pcs_panel=?, panels_sheet=?, sheets=? WHERE id=?",
                    vals + (edit_id,))
                flash(f"Finished product '{name}' update ho gaya ✅", "success")
            else:
                dup = db.query("SELECT COUNT(*) c FROM product_models WHERE name=?", (name,), one=True)["c"]
                if dup:
                    flash(f"'{name}' pehle se hai — duplicate nahi ban sakta. Us row ke ✏️ Edit button se update karo.", "error")
                    return redirect_with_token(url_for("products"))
                db.execute(
                    "INSERT INTO product_models (name, model_code, pcb_len, pcb_w, pcbs_x, pcbs_y, gap_x, gap_y, "
                    "border_l, border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, panel_len, panel_w, "
                    "kerf_x, kerf_y, orientation, pcs_panel, panels_sheet, sheets, order_id, created_on) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    vals + (None, datetime.date.today().isoformat()))
                flash(f"Finished product '{name}' manually add ho gaya ✅ — BOM set karne ke liye 🧪 BOM button dabao.", "success")
        except Exception as e:
            flash(f"Save failed: {e}", "error")
        return redirect_with_token(url_for("products"))
    rows = db.query("SELECT m.*, o.order_no FROM product_models m LEFT JOIN orders o ON o.id=m.order_id ORDER BY m.id DESC")
    # 3 queries -> 1 round trip (turso batch) — 12 alag calls ki jagah 2
    bom_lines, prods, cons_lines = db.multi([
        ("SELECT b.model_id, b.qty_per, b.unit_pcs, i.name, i.unit FROM bom b "
         "JOIN inventory i ON i.id=b.item_id", ()),
        ("SELECT * FROM fg_production ORDER BY id DESC LIMIT 8", ()),
        ("SELECT * FROM fg_consumption WHERE prod_id IN "
         "(SELECT id FROM fg_production ORDER BY id DESC LIMIT 8) ORDER BY id", ()),
    ])
    bom_map = {}
    for b in bom_lines:
        bom_map.setdefault(b["model_id"], []).append(
            f"{b['qty_per']:g} {b['unit']} {b['name']} / {b['unit_pcs'] or 1000} PCB")
    cons_by_prod = {}
    for c in cons_lines:
        cons_by_prod.setdefault(c["prod_id"], []).append(c)
    history = []
    for p in prods:
        d = dict(p)
        d["consumed"] = cons_by_prod.get(p["id"], [])
        history.append(d)
    edit_model = None
    if request.args.get("edit"):
        try:
            edit_model = db.query("SELECT * FROM product_models WHERE id=?",
                                  (int(request.args.get("edit")),), one=True)
        except ValueError:
            edit_model = None
    return render_template("products.html", active="products", models=rows,
                           bom_map=bom_map, history=history,
                           show_add=request.args.get("add"),
                           edit_model=edit_model)


@app.route("/products/<int:pid>/produce", methods=["POST"])
@login_required
def product_produce(pid):
    """Finished product ready -> BOM ke hisaab se raw material inventory se consume karo
    aur finished goods stock badhao. Jaise: 1000 PCB SCPL-10 = 10 sheets + 2kg ink + 1 Ltr
    lacquer + 10 drills (jo BOM mein set hai)."""
    pmodel = db.query("SELECT * FROM product_models WHERE id=?", (pid,), one=True)
    if not pmodel:
        flash("Finished product not found.", "error")
        return redirect_with_token(url_for("products"))
    f = request.form
    try:
        qty = max(0, int(float(f.get("qty", 0) or 0)))
    except ValueError:
        qty = 0
    if qty <= 0:
        flash("Qty (PCS) 1 ya zyada honi chahiye.", "error")
        return redirect_with_token(url_for("products"))

    bom_rows = db.bom_rows_for(pid)
    made_by = session.get("user_name") or "admin"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    consumed, shorts = [], []
    for br in bom_rows:
        up = br["unit_pcs"] or 1000
        required = round(qty / up * (br["qty_per"] or 0), 4) if up else 0
        if required <= 0:
            continue
        inv = db.query("SELECT * FROM inventory WHERE id=?", (br["item_id"],), one=True)
        if not inv:
            continue
        new_stock = round((inv["stock"] or 0) - required, 4)
        db.execute("UPDATE inventory SET stock=? WHERE id=?", (new_stock, br["item_id"]))
        if new_stock < 0:
            shorts.append(f"{br['item_name']} ab {new_stock:g} {br['unit']}")
        consumed.append((br["item_id"], br["item_name"], required, br["unit"] or ""))

    db.execute("UPDATE product_models SET fg_stock=fg_stock+? WHERE id=?", (qty, pid))
    prod_id = db.execute(
        "INSERT INTO fg_production (model_id, product_name, qty, made_by, created_on) VALUES (?,?,?,?,?)",
        (pid, pmodel["name"], qty, made_by, ts))
    for item_id, item_name, required, unit in consumed:
        db.execute("INSERT INTO fg_consumption (prod_id, item_id, item_name, qty_used, unit) VALUES (?,?,?,?,?)",
                   (prod_id, item_id, item_name, required, unit))

    if not bom_rows:
        flash(f"'{pmodel['name']}' +{qty} PCS stock mein add. ⚠️ Is product ka BOM khali hai — "
              f"koi raw material consume nahi hua. BOM set karne ke liye 🧪 BOM button dabao.", "success")
    elif shorts:
        flash(f"'{pmodel['name']}' +{qty} PCS ready. {len(consumed)} raw materials consume hue. "
              f"⚠️ STOCK SHORT: {'; '.join(shorts)} — naya stock lena hoga.", "error")
    else:
        flash(f"'{pmodel['name']}' +{qty} PCS ready ✅ — {len(consumed)} raw materials inventory se consume hue.",
              "success")
    return redirect_with_token(url_for("products"))


@app.route("/fg-production/<int:prod_id>/undo", methods=["POST"])
@login_required
def fg_production_undo(prod_id):
    """Galti se entry ho gayi to undo: inventory stock wapas + FG stock minus."""
    p = db.query("SELECT * FROM fg_production WHERE id=?", (prod_id,), one=True)
    if p:
        for c in db.query("SELECT * FROM fg_consumption WHERE prod_id=?", (prod_id,)):
            db.execute("UPDATE inventory SET stock=stock+? WHERE id=?", (c["qty_used"], c["item_id"]))
        db.execute("UPDATE product_models SET fg_stock=fg_stock-? WHERE id=?", (p["qty"], p["model_id"]))
        db.execute("DELETE FROM fg_consumption WHERE prod_id=?", (prod_id,))
        db.execute("DELETE FROM fg_production WHERE id=?", (prod_id,))
        flash(f"Production entry undo ho gayi — inventory stock wapas add, FG stock minus.", "success")
    return redirect_with_token(url_for("products"))


@app.route("/products/<int:model_id>/delete", methods=["POST"])
@login_required
def product_delete(model_id):
    db.execute("DELETE FROM product_models WHERE id=?", (model_id,))
    flash("Finished product removed.", "success")
    return redirect_with_token(url_for("products"))


# ---------------------------------------------------------------- material issues (worker stock se item leta hai)
@app.route("/material-issue/add", methods=["POST"])
@login_required
def material_issue_add():
    f = request.form
    try:
        order_id = int(f.get("order_id", 0) or 0)
        item_id = int(f.get("item_id", 0) or 0)
        qty = float(f.get("qty", 0) or 0)
    except ValueError:
        order_id, item_id, qty = 0, 0, 0
    item = db.query("SELECT * FROM inventory WHERE id=?", (item_id,), one=True) if item_id else None
    back = f.get("back", "jobcard").strip() or "jobcard"
    if not item or qty <= 0:
        flash("Item aur qty (0 se zyada) dono zaroori hain.", "error")
        return redirect_with_token(url_for("inventory") if back == "inventory" else
                                   url_for("jobcard", order_id=order_id) if order_id else
                                   url_for("orders"))
    worker = f.get("worker", "").strip() or (session.get("user_name") or session.get("op_name") or "admin")
    notes = f.get("notes", "").strip()
    taken_on = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    new_stock = round((item["stock"] or 0) - qty, 4)
    db.execute("UPDATE inventory SET stock=? WHERE id=?", (new_stock, item_id))
    db.execute("INSERT INTO material_issues (order_id, item_id, item_name, qty, unit, worker, taken_on, notes) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (order_id or None, item_id, item["name"], qty, item["unit"] or "", worker, taken_on, notes))
    order = db.query("SELECT order_no FROM orders WHERE id=?", (order_id,), one=True) if order_id else None
    where = f"order {order['order_no']}" if order else "General Store"
    msg = f"📦 {item['name']} {qty:g} {item['unit']} stock se issue — {worker} · {where}."
    if new_stock < 0:
        msg += f" ⚠️ STOCK SHORT: ab {new_stock:g} {item['unit']} bacha."
    flash(msg, "error" if new_stock < 0 else "success")
    if back == "inventory":
        return redirect_with_token(url_for("inventory"))
    return redirect_with_token(url_for("jobcard", order_id=order_id) if order_id else url_for("orders"))


@app.route("/material-issue/<int:issue_id>/undo", methods=["POST"])
@login_required
def material_issue_undo(issue_id):
    mi = db.query("SELECT * FROM material_issues WHERE id=?", (issue_id,), one=True)
    if mi:
        db.execute("UPDATE inventory SET stock=stock+? WHERE id=?", (mi["qty"], mi["item_id"]))
        db.execute("DELETE FROM material_issues WHERE id=?", (issue_id,))
        flash(f"Issue wapas — {mi['item_name']} {mi['qty']:g} {mi['unit']} stock mein add ho gaya.", "success")
    return redirect_with_token(request.referrer or url_for("inventory"))


# ---------------------------------------------------------------- inventory
@app.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory():
    if request.method == "POST":
        f = request.form
        if f.get("name", "").strip():
            count = db.query("SELECT COUNT(*) c FROM inventory", one=True)["c"]
            db.execute(
                "INSERT INTO inventory (code, name, category, stock, min_stock, unit) VALUES (?,?,?,?,?,?)",
                (f"INV-{7 + count:02d}", f.get("name").strip(), f.get("category", "").strip(),
                 float(f.get("stock", 0) or 0), float(f.get("min_stock", 0) or 0), f.get("unit", "pcs")))
            flash("Item added.", "success")
        return redirect_with_token(url_for("inventory"))
    rows = db.query("SELECT * FROM inventory ORDER BY CASE WHEN stock<=min_stock THEN 0 ELSE 1 END, name")
    issue_history = db.query("SELECT mi.*, o.order_no FROM material_issues mi "
                             "LEFT JOIN orders o ON o.id=mi.order_id "
                             "ORDER BY mi.id DESC LIMIT 30")
    orders_for_issue = db.query("SELECT id, order_no, party, product FROM orders ORDER BY id DESC")
    worker_names = [r["name"] for r in db.query("SELECT name FROM employees ORDER BY name")]
    edit_item = None
    if request.args.get("edit"):
        try:
            edit_item = db.query("SELECT * FROM inventory WHERE id=?",
                                 (int(request.args.get("edit")),), one=True)
        except ValueError:
            edit_item = None
    return render_template("inventory.html", active="inventory", rows=rows, show_add=request.args.get("add"),
                           issue_history=issue_history, orders_for_issue=orders_for_issue,
                           worker_names=worker_names, edit_item=edit_item)


@app.route("/inventory/<int:item_id>/update", methods=["POST"])
@login_required
def inventory_update(item_id):
    try:
        stock = float(request.form.get("stock", 0) or 0)
    except ValueError:
        stock = 0
    db.execute("UPDATE inventory SET stock=?, min_stock=? WHERE id=?",
               (stock, float(request.form.get("min_stock", 0) or 0), item_id))
    return redirect_with_token(url_for("inventory"))


@app.route("/inventory/<int:item_id>/edit", methods=["POST"])
@login_required
def inventory_edit(item_id):
    f = request.form
    name = (f.get("name") or "").strip()
    if not name:
        flash("Item name zaroori hai.", "error")
        return redirect_with_token(url_for("inventory", edit=item_id))
    try:
        db.execute("UPDATE inventory SET code=?, name=?, category=?, stock=?, min_stock=?, unit=? WHERE id=?",
                   ((f.get("code") or "").strip(), name, (f.get("category") or "").strip(),
                    float(f.get("stock", 0) or 0), float(f.get("min_stock", 0) or 0),
                    (f.get("unit") or "pcs").strip(), item_id))
        flash(f"'{name}' update ho gaya ✅", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("inventory"))


@app.route("/inventory/<int:item_id>/delete", methods=["POST"])
@login_required
def inventory_delete(item_id):
    item = db.query("SELECT * FROM inventory WHERE id=?", (item_id,), one=True)
    if not item:
        flash("Item nahi mila.", "error")
        return redirect_with_token(url_for("inventory"))
    refs = db.query("SELECT COUNT(*) c FROM bom WHERE item_id=?", (item_id,), one=True)["c"]
    if refs:
        flash(f"'{item['name']}' kisi Finished Product ke BOM mein hai ({refs} jagah) — "
              f"pehle wahan se BOM entry hatayein, tabhi delete hoga.", "error")
        return redirect_with_token(url_for("inventory"))
    db.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    flash(f"'{item['name']}' stock list se delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("inventory"))


# ---------------------------------------------------------------- billing
@app.route("/billing", methods=["GET", "POST"])
@login_required
def billing():
    if request.method == "POST":
        f = request.form
        if f.get("party", "").strip():
            count = db.query("SELECT COUNT(*) c FROM billing", one=True)["c"]
            db.execute("INSERT INTO billing (invoice_no, party, amount, status, date) VALUES (?,?,?,?,?)",
                       (f"INV-{105 + count}", f.get("party").strip(), float(f.get("amount", 0) or 0),
                        f.get("status", "pending"), datetime.date.today().isoformat()))
            flash("Invoice created.", "success")
        return redirect_with_token(url_for("billing"))
    rows = db.query("SELECT * FROM billing ORDER BY id DESC")
    totals = db.query("SELECT COALESCE(SUM(amount),0) total, "
                      "COALESCE(SUM(CASE WHEN status='paid' THEN amount END),0) paid, "
                      "COALESCE(SUM(CASE WHEN status!='paid' THEN amount END),0) pending FROM billing", one=True)
    edit_inv = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_inv = db.query("SELECT * FROM billing WHERE id=?", (int(eid),), one=True)
    return render_template("billing.html", active="billing", rows=rows, totals=totals,
                           show_add=request.args.get("add"), edit_inv=edit_inv)


@app.route("/billing/<int:inv_id>/status", methods=["POST"])
@login_required
def billing_status(inv_id):
    st = request.form.get("status")
    if st in ("paid", "pending", "overdue"):
        db.execute("UPDATE billing SET status=? WHERE id=?", (st, inv_id))
    return redirect_with_token(url_for("billing"))


@app.route("/billing/<int:inv_id>/edit", methods=["POST"])
@login_required
def billing_edit(inv_id):
    f = request.form
    invoice_no = (f.get("invoice_no") or "").strip()
    party = (f.get("party") or "").strip()
    if not party:
        flash("Party zaroori hai.", "error")
        return redirect_with_token(url_for("billing", edit=inv_id))
    try:
        db.execute("UPDATE billing SET invoice_no=?, party=?, amount=?, status=?, date=? WHERE id=?",
                   (invoice_no, party, float(f.get("amount", 0) or 0), f.get("status", "pending"),
                    f.get("date") or datetime.date.today().isoformat(), inv_id))
        flash(f"Invoice {invoice_no} update ho gaya ✅", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("billing"))


@app.route("/billing/<int:inv_id>/delete", methods=["POST"])
@login_required
def billing_delete(inv_id):
    inv = db.query("SELECT * FROM billing WHERE id=?", (inv_id,), one=True)
    if not inv:
        flash("Invoice nahi mila.", "error")
    else:
        db.execute("DELETE FROM billing WHERE id=?", (inv_id,))
        flash(f"Invoice {inv['invoice_no']} delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("billing"))


# ---------------------------------------------------------------- payments & receipts
@app.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    if request.method == "POST":
        f = request.form
        if f.get("party", "").strip():
            count = db.query("SELECT COUNT(*) c FROM payments", one=True)["c"]
            db.execute("INSERT INTO payments (ref_no, party, ptype, amount, mode, date) VALUES (?,?,?,?,?,?)",
                       (f"PMT-{204 + count}", f.get("party").strip(), f.get("ptype", "receipt"),
                        float(f.get("amount", 0) or 0), f.get("mode", "Bank"), datetime.date.today().isoformat()))
            flash("Entry saved.", "success")
        return redirect_with_token(url_for("payments"))
    rows = db.query("SELECT * FROM payments ORDER BY id DESC")
    totals = db.query("SELECT COALESCE(SUM(CASE WHEN ptype='receipt' THEN amount END),0) received, "
                      "COALESCE(SUM(CASE WHEN ptype='payment' THEN amount END),0) paid FROM payments", one=True)
    edit_pmt = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_pmt = db.query("SELECT * FROM payments WHERE id=?", (int(eid),), one=True)
    return render_template("payments.html", active="payments", rows=rows, totals=totals,
                           show_add=request.args.get("add"), edit_pmt=edit_pmt)


@app.route("/payments/<int:pmt_id>/edit", methods=["POST"])
@login_required
def payment_edit(pmt_id):
    f = request.form
    ref_no = (f.get("ref_no") or "").strip()
    party = (f.get("party") or "").strip()
    if not party:
        flash("Party zaroori hai.", "error")
        return redirect_with_token(url_for("payments", edit=pmt_id))
    try:
        db.execute("UPDATE payments SET ref_no=?, party=?, ptype=?, amount=?, mode=?, date=? WHERE id=?",
                   (ref_no, party, f.get("ptype", "receipt"), float(f.get("amount", 0) or 0),
                    f.get("mode", "Bank"), f.get("date") or datetime.date.today().isoformat(), pmt_id))
        flash(f"Entry {ref_no} update ho gaya ✅", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("payments"))


@app.route("/payments/<int:pmt_id>/delete", methods=["POST"])
@login_required
def payment_delete(pmt_id):
    pmt = db.query("SELECT * FROM payments WHERE id=?", (pmt_id,), one=True)
    if not pmt:
        flash("Entry nahi mila.", "error")
    else:
        db.execute("DELETE FROM payments WHERE id=?", (pmt_id,))
        flash(f"Entry {pmt['ref_no']} delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("payments"))


# ---------------------------------------------------------------- employees
@app.route("/employees", methods=["GET", "POST"])
@login_required
def employees():
    if request.method == "POST":
        f = request.form
        try:
            db.execute(
                "INSERT INTO employees (emp_code, name, email, phone, department, designation, joining_date, status, salary) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f.get("emp_code", "").strip(), f.get("name", "").strip(), f.get("email", "").strip(),
                 f.get("phone", "").strip(), f.get("department", "").strip(), f.get("designation", "").strip(),
                 f.get("joining_date", "") or datetime.date.today().isoformat(), "active",
                 float(f.get("salary", 0) or 0)))
            flash("Employee added.", "success")
        except Exception as e:
            flash(f"Could not add employee: {e}", "error")
        return redirect_with_token(url_for("employees"))
    cur_month = datetime.date.today().strftime("%Y-%m") + "%"
    rows = db.query("SELECT e.*, (SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='present' "
                    "AND a.date LIKE ?) AS present_days FROM employees e ORDER BY e.id", (cur_month,))
    today = datetime.date.today().isoformat()
    att = {r["emp_id"]: r["status"] for r in db.query("SELECT emp_id, status FROM attendance WHERE date=?", (today,))}
    edit_emp = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_emp = db.query("SELECT * FROM employees WHERE id=?", (int(eid),), one=True)
    return render_template("employees.html", active="employees", employees=rows, att=att,
                           show_add=request.args.get("add"), edit_emp=edit_emp)


@app.route("/employees/<int:emp_id>/edit", methods=["POST"])
@login_required
def employee_edit(emp_id):
    f = request.form
    name = (f.get("name") or "").strip()
    if not name:
        flash("Name zaroori hai.", "error")
        return redirect_with_token(url_for("employees", edit=emp_id))
    try:
        db.execute(
            "UPDATE employees SET emp_code=?, name=?, email=?, phone=?, department=?, designation=?, "
            "joining_date=?, status=?, salary=? WHERE id=?",
            ((f.get("emp_code") or "").strip(), name, (f.get("email") or "").strip(),
             (f.get("phone") or "").strip(), (f.get("department") or "").strip(),
             (f.get("designation") or "").strip(), (f.get("joining_date") or "").strip(),
             f.get("status", "active"), float(f.get("salary", 0) or 0), emp_id))
        flash(f"'{name}' update ho gaya ✅", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("employees"))


@app.route("/employees/<int:emp_id>/delete", methods=["POST"])
@login_required
@admin_required
def employee_delete(emp_id):
    db.execute("DELETE FROM attendance WHERE emp_id=?", (emp_id,))
    db.execute("DELETE FROM emp_salary WHERE emp_id=?", (emp_id,))
    db.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    flash("Employee removed.", "success")
    return redirect_with_token(url_for("employees"))


ATT_STATUSES = ["present", "halfday", "leave", "absent"]
ATT_LABELS = {"present": "P", "halfday": "HD", "leave": "L", "absent": "A"}


def _att_month_rows(month):
    """Per employee: P/A/L/HD counts + advance + paid + salary calc (is month ka)."""
    rows = db.query(
        "SELECT e.id, e.name, e.emp_code, e.department, e.designation, e.salary, "
        "COALESCE(SUM(CASE WHEN a.status='present' THEN 1 END),0) AS p, "
        "COALESCE(SUM(CASE WHEN a.status='absent' THEN 1 END),0) AS a, "
        "COALESCE(SUM(CASE WHEN a.status='leave' THEN 1 END),0) AS l, "
        "COALESCE(SUM(CASE WHEN a.status='halfday' THEN 1 END),0) AS hd, "
        "COALESCE(es.advance,0) AS advance, COALESCE(es.paid,0) AS paid "
        "FROM employees e "
        "LEFT JOIN attendance a ON a.emp_id=e.id AND a.date LIKE ? "
        "LEFT JOIN emp_salary es ON es.emp_id=e.id AND es.month=? "
        "WHERE e.status='active' GROUP BY e.id ORDER BY e.id", (month + "%", month))
    try:
        y, m = int(month[:4]), int(month[5:7])
        dim = calendar.monthrange(y, m)[1]
    except (ValueError, IndexError):
        dim = 30
    for r in rows:
        sal = float(r["salary"] or 0)
        per_day = round(sal / dim, 2) if sal and dim else 0.0
        ded = round(per_day * (r["a"] or 0) + per_day * 0.5 * (r["hd"] or 0), 2)
        r["per_day"] = per_day
        r["ded"] = ded
        r["earned"] = round(sal - ded, 2)
        r["net"] = round(sal - ded - float(r["advance"] or 0), 2)
    return rows, dim


def _valid_month(m):
    return bool(re.fullmatch(r"\d{4}-\d{2}", m or ""))


@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    date_str = request.form.get("date") if request.method == "POST" else request.args.get("date")
    if not date_str:
        date_str = datetime.date.today().isoformat()
    if request.method == "POST":
        try:
            d = datetime.date.fromisoformat(date_str)
            if d > datetime.date.today():
                flash("Cannot mark attendance for a future date.", "error")
            else:
                employees = db.query("SELECT id FROM employees WHERE status='active'")
                for e in employees:
                    st = request.form.get(f"status_{e['id']}", "absent")
                    if st not in ATT_STATUSES:
                        st = "absent"
                    db.execute("INSERT OR REPLACE INTO attendance (emp_id, date, status) VALUES (?,?,?)",
                               (e["id"], date_str, st))
                flash(f"Attendance saved for {date_str}.", "success")
        except ValueError:
            flash("Invalid date.", "error")
        return redirect_with_token(url_for("attendance", date=date_str))
    month = request.args.get("month") or datetime.date.today().strftime("%Y-%m")
    if not _valid_month(month):
        month = datetime.date.today().strftime("%Y-%m")
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        date_str = datetime.date.today().isoformat()
        d = datetime.date.today()
    rows = db.query(
        "SELECT e.id, e.name, e.department, a.status FROM employees e "
        "LEFT JOIN attendance a ON a.emp_id=e.id AND a.date=? WHERE e.status='active' ORDER BY e.id", (date_str,))
    summary = {"present": 0, "absent": 0, "halfday": 0, "leave": 0}
    for r in rows:
        if r["status"] in summary:
            summary[r["status"]] += 1
    sal_rows, dim = _att_month_rows(month)
    tot = {"salary": round(sum(r["salary"] or 0 for r in sal_rows), 2),
           "ded": round(sum(r["ded"] for r in sal_rows), 2),
           "advance": round(sum(r["advance"] or 0 for r in sal_rows), 2),
           "net": round(sum(r["net"] for r in sal_rows), 2)}
    return render_template("attendance.html", active="employees", employees=rows, date=date_str,
                           future=d > datetime.date.today(), summary=summary, month=month,
                           sal_rows=sal_rows, days_in_month=dim, tot=tot)


@app.route("/attendance/advance", methods=["POST"])
@login_required
def attendance_advance():
    emp_id = int(request.form.get("emp_id") or 0)
    month = request.form.get("month") or datetime.date.today().strftime("%Y-%m")
    if not _valid_month(month):
        month = datetime.date.today().strftime("%Y-%m")
    try:
        adv = float(request.form.get("advance") or 0)
    except ValueError:
        adv = 0.0
    cur = db.query("SELECT paid FROM emp_salary WHERE emp_id=? AND month=?", (emp_id, month), one=True)
    paid = cur["paid"] if cur else 0
    db.execute("INSERT OR REPLACE INTO emp_salary (emp_id, month, advance, paid) VALUES (?,?,?,?)",
               (emp_id, month, max(0.0, adv), paid))
    flash("Advance update ho gaya ✅", "success")
    return redirect_with_token(url_for("attendance", month=month))


@app.route("/attendance/paid", methods=["POST"])
@login_required
def attendance_paid():
    emp_id = int(request.form.get("emp_id") or 0)
    month = request.form.get("month") or datetime.date.today().strftime("%Y-%m")
    if not _valid_month(month):
        month = datetime.date.today().strftime("%Y-%m")
    cur = db.query("SELECT advance, paid FROM emp_salary WHERE emp_id=? AND month=?", (emp_id, month), one=True)
    advance = cur["advance"] if cur else 0.0
    new_paid = 0 if (cur and cur["paid"]) else 1
    db.execute("INSERT OR REPLACE INTO emp_salary (emp_id, month, advance, paid) VALUES (?,?,?,?)",
               (emp_id, month, advance, new_paid))
    flash("Salary paid status update ho gaya ✅", "success")
    return redirect_with_token(url_for("attendance", month=month))


# ---------------------------------------------------------------- machines
@app.route("/machines", methods=["GET", "POST"])
@login_required
def machines():
    if request.method == "POST":
        f = request.form
        if f.get("name", "").strip():
            count = db.query("SELECT COUNT(*) c FROM machines", one=True)["c"]
            db.execute("INSERT INTO machines (code, name, status, current_job) VALUES (?,?,?,?)",
                       (f"M{7 + count}", f.get("name").strip(), f.get("status", "idle"), f.get("current_job", "").strip()))
            flash("Machine added.", "success")
        return redirect_with_token(url_for("machines"))
    rows = db.query("SELECT * FROM machines ORDER BY id")
    edit_machine = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_machine = db.query("SELECT * FROM machines WHERE id=?", (int(eid),), one=True)
    return render_template("machines.html", active="machines", rows=rows,
                           show_add=request.args.get("add"), edit_machine=edit_machine)


@app.route("/machines/<int:m_id>/status", methods=["POST"])
@login_required
def machine_status(m_id):
    st = request.form.get("status")
    if st in ("running", "idle", "maintenance"):
        job = request.form.get("current_job", "").strip()
        db.execute("UPDATE machines SET status=?, current_job=? WHERE id=?", (st, job, m_id))
    return redirect_with_token(url_for("machines"))


@app.route("/machines/<int:m_id>/edit", methods=["POST"])
@login_required
def machine_edit(m_id):
    f = request.form
    name = (f.get("name") or "").strip()
    if not name:
        flash("Machine name zaroori hai.", "error")
        return redirect_with_token(url_for("machines", edit=m_id))
    try:
        db.execute("UPDATE machines SET code=?, name=?, status=?, current_job=? WHERE id=?",
                   ((f.get("code") or "").strip(), name, f.get("status", "idle"),
                    (f.get("current_job") or "").strip(), m_id))
        flash(f"Machine '{name}' update ho gaya ✅", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("machines"))


@app.route("/machines/<int:m_id>/delete", methods=["POST"])
@login_required
def machine_delete(m_id):
    m = db.query("SELECT * FROM machines WHERE id=?", (m_id,), one=True)
    if not m:
        flash("Machine nahi mila.", "error")
    else:
        db.execute("DELETE FROM machines WHERE id=?", (m_id,))
        flash(f"Machine '{m['name']}' delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("machines"))


# ---------------------------------------------------------------- quality
@app.route("/quality", methods=["GET", "POST"])
@login_required
def quality():
    if request.method == "POST":
        f = request.form
        count = db.query("SELECT COUNT(*) c FROM quality", one=True)["c"]
        db.execute("INSERT INTO quality (qc_no, order_id, remarks, result, date) VALUES (?,?,?,?,?)",
                   (f"QC-{404 + count}", int(f.get("order_id") or 0) or None, f.get("remarks", "").strip(),
                    f.get("result", "pending"), datetime.date.today().isoformat()))
        flash("QC entry saved.", "success")
        return redirect_with_token(url_for("quality"))
    rows = db.query("SELECT q.*, o.order_no, o.party FROM quality q LEFT JOIN orders o ON o.id=q.order_id ORDER BY q.id DESC")
    orders = db.query("SELECT id, order_no, party FROM orders ORDER BY id DESC")
    edit_qc = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_qc = db.query("SELECT * FROM quality WHERE id=?", (int(eid),), one=True)
    return render_template("quality.html", active="quality", rows=rows, orders=orders,
                           show_add=request.args.get("add"), edit_qc=edit_qc)


@app.route("/quality/<int:qc_id>/result", methods=["POST"])
@login_required
def quality_result(qc_id):
    r = request.form.get("result")
    if r in ("passed", "failed", "pending"):
        db.execute("UPDATE quality SET result=? WHERE id=?", (r, qc_id))
    return redirect_with_token(url_for("quality"))


@app.route("/quality/<int:qc_id>/edit", methods=["POST"])
@login_required
def quality_edit(qc_id):
    f = request.form
    qc_no = (f.get("qc_no") or "").strip()
    try:
        oid = int(f.get("order_id") or 0) or None
    except ValueError:
        oid = None
    db.execute("UPDATE quality SET qc_no=?, order_id=?, remarks=?, result=?, date=? WHERE id=?",
               (qc_no, oid, (f.get("remarks") or "").strip(), f.get("result", "pending"),
                f.get("date") or datetime.date.today().isoformat(), qc_id))
    flash(f"QC {qc_no} update ho gaya ✅", "success")
    return redirect_with_token(url_for("quality"))


@app.route("/quality/<int:qc_id>/delete", methods=["POST"])
@login_required
def quality_delete(qc_id):
    qc = db.query("SELECT * FROM quality WHERE id=?", (qc_id,), one=True)
    if not qc:
        flash("QC entry nahi mila.", "error")
    else:
        db.execute("DELETE FROM quality WHERE id=?", (qc_id,))
        flash(f"QC {qc['qc_no']} delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("quality"))


# ---------------------------------------------------------------- operator view
def op_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def current_proc_row(order_id):
    """order + current process row (fallback: pehla unfinished)."""
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        return None, None
    rows = db.get_jc_processes(order_id)
    cur = (order["current_process"] or "").lower()
    for r in rows:
        if (r["process"] or "").lower() == cur:
            return order, r
    for r in rows:
        if not r["end_dt"]:
            return order, r
    return order, (rows[0] if rows else None)


def op_state(order_id, process):
    """new / working / paused / finished — log-driven operator workflow state."""
    row = db.query("SELECT * FROM jobcard_process WHERE order_id=? AND process=?",
                   (order_id, process), one=True)
    if row and row["end_dt"]:
        return "finished"
    last = db.query("SELECT * FROM jobcard_log WHERE order_id=? AND process=? ORDER BY id DESC LIMIT 1",
                    (order_id, process), one=True)
    if last and last["action"] == "pause":
        return "paused"
    if last and last["action"] in ("start", "resume", "handover"):
        return "working"
    return "new"


def advance_current(order_id):
    """finish ke baad: latest finished row ka NEXT; NONE = agla process hoga hi nahi (skip);
    warna pehla unfinished; sab done -> Completed."""
    rows = db.get_jc_processes(order_id)
    finished = [r for r in rows if r["end_dt"]]
    if not finished:
        current = rows[0]["process"] if rows else ""
    elif all(r["end_dt"] for r in rows):
        current = "Completed"
    else:
        latest = max(finished, key=lambda r: r["end_dt"])
        nxt = (latest["next_process"] or "").strip()
        if nxt.upper() == "NONE":
            # next process nahi hoga — skip karke uske BAAD wala unfinished process
            after = [r for r in rows if r["idx"] > latest["idx"] + 1 and not r["end_dt"]]
            current = after[0]["process"] if after else "Completed"
        elif nxt:
            current = nxt
        else:
            unfinished = [r for r in rows if not r["end_dt"]]
            current = unfinished[0]["process"] if unfinished else "Completed"
    if current in db.JC_TOOL_OPTIONS:
        ddrow = next((r for r in rows if r["is_dropdown"]), None)
        if ddrow and ddrow["process"] != current:
            db.execute("UPDATE jobcard_process SET process=? WHERE id=?", (current, ddrow["id"]))
    db.execute("UPDATE orders SET current_process=? WHERE id=?", (current, order_id))
    return current


def proc_skipped(order_id, process):
    """Kya ye process skip hai (iska predecessor NEXT = NONE)?"""
    rows = db.get_jc_processes(order_id)
    for i, r in enumerate(rows):
        if (r["process"] or "").lower() == (process or "").lower() and i > 0:
            return (rows[i - 1]["next_process"] or "").upper() == "NONE"
    return False


@app.route("/operator")
@login_required
def operator_view():
    op = session.get("op_name") or session.get("user_name") or "Operator"
    emp = db.query("SELECT * FROM employees WHERE name=?", (op,), one=True)
    designation = emp["designation"] if emp and emp["designation"] else "Production Operator"
    is_admin = session.get("user_role") == "admin"
    jobs = []
    for o in db.query("SELECT * FROM orders WHERE status='pending' ORDER BY "
                      "CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, delivery_date, id"):
        d = dict(o)
        _order, prow = current_proc_row(o["id"])
        if not prow:
            continue
        d["proc"] = prow["process"]
        d["state"] = op_state(o["id"], prow["process"])
        nxt = (prow["next_process"] or "").strip()
        d["next_proc"] = nxt
        d["next_none"] = (nxt.upper() == "NONE")
        d["pause_reason"] = ""
        if d["state"] == "paused":
            last = db.query("SELECT * FROM jobcard_log WHERE order_id=? AND process=? AND action='pause' "
                            "ORDER BY id DESC LIMIT 1", (o["id"], prow["process"]), one=True)
            d["pause_reason"] = last["reason"] if last else ""
        asg = db.query("SELECT * FROM order_assignments WHERE order_id=?", (o["id"],), one=True)
        d["assigned_op"] = asg["operator"] if asg else "Unassigned"
        d["assigned_machine"] = asg["machine"] if asg else "Unassigned"
        d["mine"] = ((asg and asg["operator"] == op)
                     or (prow["start_name"] or "") == op
                     or (o["operator"] or "") == op)
        if not is_admin and not d["mine"] and (d["priority"] or "") != "urgent":
            continue  # operator ko sirf apna kaam dikhe; 🚨 URGENT/emergency orders SABKO dikhte hain
        d["skipped"] = proc_skipped(o["id"], prow["process"])
        dl = db.query("SELECT * FROM dispatch_log WHERE order_id=? ORDER BY id DESC LIMIT 1",
                      (o["id"],), one=True)
        d["dispatched"] = dl
        d["is_dispatch_step"] = (prow["process"] or "").lower() in ("dispatch", "packing", "completed")
        jobs.append(d)
    logs = (db.query("SELECT * FROM jobcard_log ORDER BY id DESC LIMIT 20") if is_admin else
            db.query("SELECT * FROM jobcard_log WHERE operator=? ORDER BY id DESC LIMIT 20", (op,)))
    employees = db.query("SELECT * FROM employees WHERE department='Production' ORDER BY name")
    inv_items = db.query("SELECT * FROM inventory ORDER BY CASE WHEN stock<=min_stock THEN 0 ELSE 1 END, name")
    my_issues = db.query("SELECT * FROM material_issues WHERE worker=? ORDER BY id DESC LIMIT 10", (op,))
    return render_template("operator.html", active="operator", jobs=jobs, logs=logs,
                           employees=employees, op=op, designation=designation,
                           inv_items=inv_items, my_issues=my_issues,
                           admin_show=session.get("user_role") == "admin")


@app.route("/operator/switch", methods=["POST"])
@login_required
def operator_switch():
    if session.get("user_role") != "admin":
        flash("Sirf admin operator switch kar sakta hai.", "error")
        return redirect_with_token(url_for("operator_view"))
    name = request.form.get("op_name", "").strip()
    if name:
        session["op_name"] = name
        uid = session.get("user_id")
        new_tok = make_token(uid, op=name)
        flash(f"Operator switched to {name}.", "success")
        return redirect(url_for("operator_view") + f"?token={new_tok}")
    return redirect_with_token(url_for("operator_view"))


@app.route("/operator/issue", methods=["POST"])
@login_required
def operator_issue():
    """Operator khud stock se item le — entry auto uske naam + uske job order ke saath."""
    f = request.form
    try:
        order_id = int(f.get("order_id", 0) or 0)
        item_id = int(f.get("item_id", 0) or 0)
        qty = float(f.get("qty", 0) or 0)
    except ValueError:
        order_id, item_id, qty = 0, 0, 0
    op = session.get("op_name") or session.get("user_name") or "Operator"
    item = db.query("SELECT * FROM inventory WHERE id=?", (item_id,), one=True) if item_id else None
    if not item or qty <= 0:
        flash("Item aur qty (0 se zyada) dono zaroori hain.", "error")
        return redirect_with_token(url_for("operator_view"))
    # operator sirf apne assigned job order mein issue kar sakta hai
    if session.get("user_role") != "admin":
        order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True) if order_id else None
        _order, prow = current_proc_row(order_id) if order_id else (None, None)
        asg = db.query("SELECT * FROM order_assignments WHERE order_id=?", (order_id,), one=True) if order_id else None
        allowed = (bool(asg and asg["operator"] == op)
                   or (prow is not None and (prow["start_name"] or "") == op)
                   or (order is not None and (order["operator"] or "") == op))
        if not allowed:
            flash("Aap sirf apne assigned job order mein item issue kar sakte hain.", "error")
            return redirect_with_token(url_for("operator_view"))
    notes = f.get("notes", "").strip()
    taken_on = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    new_stock = round((item["stock"] or 0) - qty, 4)
    db.execute("UPDATE inventory SET stock=? WHERE id=?", (new_stock, item_id))
    db.execute("INSERT INTO material_issues (order_id, item_id, item_name, qty, unit, worker, taken_on, notes) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (order_id or None, item_id, item["name"], qty, item["unit"] or "", op, taken_on, notes))
    msg = f"📦 {item['name']} {qty:g} {item['unit']} stock se issue ho gaya — {op}"
    if order_id:
        oo = db.query("SELECT order_no FROM orders WHERE id=?", (order_id,), one=True)
        if oo:
            msg += f" · order {oo['order_no']}"
    msg += ". Job Card ke MATERIAL ISSUES aur Inventory History mein entry save ho gayi."
    if new_stock < 0:
        msg += f" ⚠️ STOCK SHORT: ab {new_stock:g} {item['unit']} bacha."
    flash(msg, "error" if new_stock < 0 else "success")
    return redirect_with_token(url_for("operator_view"))


@app.route("/operator/action", methods=["POST"])
@login_required
def operator_action():
    f = request.form
    action = f.get("action", "")
    try:
        order_id = int(f.get("order_id") or 0)
    except ValueError:
        order_id = 0
    process = f.get("process", "").strip()
    op = (session.get("op_name") or session.get("user_name") or "Operator")
    reason = f.get("reason", "").strip()
    details = f.get("details", "").strip()
    now = op_now()
    if not order_id or not process:
        flash("Missing order/process.", "error")
        return redirect_with_token(url_for("operator_view"))
    row = db.query("SELECT * FROM jobcard_process WHERE order_id=? AND process=?",
                   (order_id, process), one=True)
    if not row:
        flash("Process row not found.", "error")
        return redirect_with_token(url_for("operator_view"))

    if action == "start":
        # job card mein automatic START date & time (operator ka asli start time)
        db.execute("UPDATE jobcard_process SET start_dt=?, start_name=? WHERE id=?",
                   (now, op, row["id"]))
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, ts) VALUES (?,?,?,?,?)",
                   (order_id, process, "start", op, now))
        flash(f"Started: {process} — START {now} job card mein save ho gaya.", "success")
    elif action == "pause":
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, reason, details, ts) VALUES (?,?,?,?,?,?,?)",
                   (order_id, process, "pause", op, reason, details, now))
        flash(f"Paused: {process}" + (f" — reason: {reason}" if reason else ""), "success")
    elif action == "resume":
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, ts) VALUES (?,?,?,?,?)",
                   (order_id, process, "resume", op, now))
        flash(f"Resumed: {process}", "success")
    elif action == "finish":
        if not row["start_dt"]:
            db.execute("UPDATE jobcard_process SET start_dt=?, start_name=? WHERE id=?", (now, op, row["id"]))
        db.execute("UPDATE jobcard_process SET end_dt=?, end_name=? WHERE id=?", (now, op, row["id"]))
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, ts) VALUES (?,?,?,?,?)",
                   (order_id, process, "finish", op, now))
        nxt = advance_current(order_id)
        flash(f"Finished: {process} — FINISH {now}. Next: {nxt}.", "success")
    elif action == "dispatch":
        mode = f.get("mode", "").strip()
        if mode:
            db.execute("INSERT INTO dispatch_log (order_id, ddate, dtime, mode, details, dispatched_by, ts) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (order_id, datetime.date.today().isoformat(),
                        datetime.datetime.now().strftime("%H:%M"), mode,
                        f.get("details", "").strip(), op, now))
            flash(f"PCB dispatched: {mode} — date/time auto save ho gaya.", "success")
        else:
            flash("Dispatch mode select karein.", "error")
    elif action == "handover":
        new_op = f.get("new_op", "").strip()
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, reason, details, ts) VALUES (?,?,?,?,?,?,?)",
                   (order_id, process, "handover", op, reason,
                    (f"→ {new_op}: " if new_op else "") + details, now))
        if new_op:
            session["op_name"] = new_op
        flash(f"Handover recorded: {process} → {new_op or op}" + (f" ({reason})" if reason else ""), "success")
    return redirect_with_token(url_for("operator_view"))


# ---------------------------------------------------------------- production planning
@app.route("/production-planning", methods=["GET", "POST"])
@login_required
def production_planning():
    if request.method == "POST":
        f = request.form
        if f.get("action") == "assign":
            for o in db.query("SELECT id FROM orders WHERE status='pending'"):
                oid = o["id"]
                if f.get(f"machine_{oid}") is None:
                    continue
                db.execute(
                    "INSERT INTO order_assignments (order_id, machine, shift, operator, planned_start) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(order_id) DO UPDATE SET machine=excluded.machine, shift=excluded.shift, "
                    "operator=excluded.operator, planned_start=excluded.planned_start",
                    (oid, f.get(f"machine_{oid}", "Unassigned").strip() or "Unassigned",
                     f.get(f"shift_{oid}", "").strip(), f.get(f"op_{oid}", "Unassigned").strip() or "Unassigned",
                     f.get(f"start_{oid}", "").strip()))
            flash("Assignments saved.", "success")
        else:
            count = db.query("SELECT COUNT(*) c FROM plans", one=True)["c"]
            db.execute("INSERT INTO plans (plan_no, order_id, desc, start_date, end_date, status) VALUES (?,?,?,?,?,?)",
                       (f"PL-{503 + count}", int(f.get("order_id") or 0) or None, f.get("desc", "").strip(),
                        f.get("start_date", ""), f.get("end_date", ""), f.get("status", "planned")))
            flash("Plan created.", "success")
        return redirect_with_token(url_for("production_planning"))

    today = datetime.date.today()
    pending = db.query("SELECT * FROM orders WHERE status='pending' ORDER BY id")
    active_orders = len(pending)
    # bottleneck detection
    bottleneck_rows = db.query(
        "SELECT current_process AS step, COUNT(*) c, SUM(qty) q FROM orders WHERE status='pending' "
        "GROUP BY current_process ORDER BY c DESC, q DESC")
    bottleneck = bottleneck_rows[0]["step"] if bottleneck_rows else "—"
    # due this week (pcs)
    week_end = (today + datetime.timedelta(days=7)).isoformat()
    due_pcs = 0
    for o in pending:
        dd = o["delivery_date"] or ""
        if dd and today.isoformat() <= dd <= week_end:
            due_pcs += o["qty"] or 0
    DAILY_CAP, WEEKLY_CAP = 500, 3500
    utilization = min(100, round(due_pcs * 100 / WEEKLY_CAP)) if due_pcs else 0
    # calendar next 7 days
    cal = []
    for i in range(7):
        d = today + datetime.timedelta(days=i)
        cnt = db.query("SELECT COUNT(*) c FROM orders WHERE delivery_date=?",
                       (d.isoformat(),), one=True)["c"]
        cal.append({"date": d.isoformat(), "iso": d.strftime("%d-%m"), "count": cnt})
    # assignment rows
    assigns = db.query("SELECT * FROM order_assignments")
    amap = {a["order_id"]: a for a in assigns}
    pending = [dict(o) for o in pending]
    for o in pending:
        o["asm"] = amap.get(o["id"])
    machines = db.query("SELECT * FROM machines ORDER BY id")
    employees = db.query("SELECT * FROM employees WHERE department='Production' ORDER BY name")
    rows = db.query("SELECT p.*, o.order_no, o.party FROM plans p LEFT JOIN orders o ON o.id=p.order_id ORDER BY p.id DESC")
    return render_template("production_planning.html", active="planning", rows=rows, orders=pending,
                           show_add=request.args.get("add"), active_orders=active_orders,
                           bottleneck=bottleneck, bottleneck_rows=bottleneck_rows, due_pcs=due_pcs,
                           utilization=utilization, cal=cal, machines=machines, employees=employees,
                           DAILY_CAP=DAILY_CAP, WEEKLY_CAP=WEEKLY_CAP)


# ---------------------------------------------------------------- material planning
@app.route("/material-planning", methods=["GET", "POST"])
@login_required
def material_planning():
    if request.method == "POST":
        f = request.form
        if f.get("item", "").strip():
            count = db.query("SELECT COUNT(*) c FROM materials", one=True)["c"]
            required = float(f.get("required", 0) or 0)
            available = float(f.get("available", 0) or 0)
            shortfall = max(0, required - available)
            db.execute(
                "INSERT INTO materials (mp_no, item, required, available, shortfall, action, unit) VALUES (?,?,?,?,?,?,?)",
                (f"MP-{604 + count}", f.get("item").strip(), required, available, shortfall,
                 f"Order {shortfall:g} {f.get('unit','pcs')}" if shortfall > 0 else "OK", f.get("unit", "pcs")))
            flash("Material plan saved.", "success")
        return redirect_with_token(url_for("material_planning"))
    rows = db.query("SELECT * FROM materials ORDER BY CASE WHEN shortfall>0 THEN 0 ELSE 1 END, id DESC")
    return render_template("material_planning.html", active="material", rows=rows, show_add=request.args.get("add"))


# ---------------------------------------------------------------- job card
JC_FIELDS = ["party", "party_model", "model", "odate", "board_type", "created_by", "checked_by",
             "order_via", "exp_delivery", "price", "rs_pcb", "payment_status", "sheet_material",
             "copper_finish", "masking", "finish", "legend_printing", "pcb_type", "actual_pcb_x",
             "actual_pcb_y", "x_size", "x_qty", "y_size", "y_qty", "cnc_margin_x", "cnc_margin_y",
             "panel_x", "panel_y", "panels_per_sheet", "sheets", "qty_panel", "pcs_panel",
             "v_grooving", "customer_req", "raw_materials", "total_qty", "short_qty", "short_reason",
             "handover_sign", "priority"]


def _jc_num(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0


def ensure_jobcard(order_id):
    jc = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
    if not jc:
        db.execute("INSERT INTO jobcard (order_id) VALUES (?)", (order_id,))
        jc = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
    return jc


def total_time_str(start_dt, end_dt):
    if not start_dt or not end_dt:
        return ""
    try:
        s = datetime.datetime.strptime(start_dt, "%Y-%m-%d %H:%M")
        e = datetime.datetime.strptime(end_dt, "%Y-%m-%d %H:%M")
        mins = max(0, int((e - s).total_seconds() // 60))
        if mins >= 60:
            return f"{mins // 60}h {mins % 60}m"
        return f"{mins}m"
    except ValueError:
        return ""


def prep_jobcard(order_id):
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        return None, None, None
    jc = ensure_jobcard(order_id)
    flat_next = db.all_process_options()
    procs = []
    raw_rows = db.get_jc_processes(order_id)
    for i, r in enumerate(raw_rows):
        d = dict(r)
        d["start_date"] = d["start_dt"][:10] if d["start_dt"] else ""
        d["start_time"] = d["start_dt"][11:16] if d["start_dt"] and len(d["start_dt"]) >= 16 else ""
        d["end_date"] = d["end_dt"][:10] if d["end_dt"] else ""
        d["end_time"] = d["end_dt"][11:16] if d["end_dt"] and len(d["end_dt"]) >= 16 else ""
        d["total"] = total_time_str(d["start_dt"], d["end_dt"])
        d["next_options"] = flat_next
        d["skipped"] = bool(i > 0 and (raw_rows[i - 1]["next_process"] or "").upper() == "NONE")
        # work status: end_dt -> done, sirf start_dt -> running, kuch nahi -> pending
        d["status"] = "done" if d["end_dt"] else ("running" if d["start_dt"] else "pending")
        procs.append(d)
    # current process row ka status (headbar ke liye)
    cur_name = (order["current_process"] or "").lower()
    cur_row = None
    for d in procs:
        if cur_name and (cur_name == (d["process"] or "").lower()
                         or (d.get("is_dropdown") and cur_name in [o.lower() for o in (d.get("options") or [])])):
            cur_row = d
            break
    cur_status = cur_row["status"] if cur_row else ("done" if cur_name == "completed" else "pending")
    order = dict(order)
    order["cur_status"] = cur_status
    return order, jc, procs


@app.route("/jobcard/<int:order_id>")
@login_required
def jobcard(order_id):
    order, jc, procs = prep_jobcard(order_id)
    if not order:
        flash("Order not found.", "error")
        return redirect_with_token(url_for("orders"))
    qty_pcs = order["qty"]
    jc_logs = db.query("SELECT * FROM jobcard_log WHERE order_id=? ORDER BY id DESC LIMIT 15", (order_id,))
    dispatch_logs = db.query("SELECT * FROM dispatch_log WHERE order_id=? ORDER BY id DESC", (order_id,))
    dispatch = dispatch_logs[0] if dispatch_logs else None
    _plist, proc_label = db.process_list_for(jc["sheet_material"])
    models = db.query("SELECT * FROM product_models ORDER BY name")
    inv_items = db.query("SELECT * FROM inventory ORDER BY CASE WHEN stock<=min_stock THEN 0 ELSE 1 END, category, name")
    inv_names = {it["name"] for it in inv_items}
    bmodel = db.query("SELECT * FROM product_models WHERE name=?", (jc["party_model"],), one=True)
    bom_rows = []
    if bmodel:
        for br in db.bom_rows_for(bmodel["id"]):
            d = dict(br)
            up = d["unit_pcs"] or 1000
            d["required"] = round(qty_pcs / up * d["qty_per"], 2) if up else 0
            d["shortfall"] = round(max(0, d["required"] - d["stock"]), 2)
            d["ok"] = d["required"] <= d["stock"]
            d["req_10k"] = round(10000 / up * d["qty_per"], 2) if up else 0
            bom_rows.append(d)
    pm_values = {m["name"] for m in models}
    md_values = {m["model_code"] for m in models if m["model_code"]}
    party_names = {p["name"] for p in db.query("SELECT name FROM parties")}
    issues = db.query("SELECT * FROM material_issues WHERE order_id=? ORDER BY id DESC", (order_id,))
    issue_summary = {}
    for mi in issues:
        k = (mi["item_name"], mi["unit"])
        issue_summary[k] = round(issue_summary.get(k, 0) + (mi["qty"] or 0), 4)
    issue_total = "; ".join(f"{q:g} {u} {n}" for (n, u), q in issue_summary.items())
    worker_names = [r["name"] for r in db.query("SELECT name FROM employees ORDER BY name")]
    return render_template("jobcard.html", active="orders", order=order, jc=jc, procs=procs,
                           flat_next=db.all_process_options(),
                           qty_pcs=qty_pcs, models=models, proc_label=proc_label, jc_logs=jc_logs,
                           dispatch=dispatch, dispatch_logs=dispatch_logs,
                           inv_items=inv_items, inv_names=inv_names,
                           bom_rows=bom_rows, bmodel=bmodel,
                           issues=issues, issue_total=issue_total, worker_names=worker_names,
                           pm_extra=(jc["party_model"] not in pm_values and jc["party_model"] != ""),
                           md_extra=(jc["model"] not in md_values and jc["model"] != ""),
                           party_extra=(order["party"] not in party_names and bool(order["party"])))


@app.route("/jobcard/<int:order_id>/update", methods=["POST"])
@login_required
def jobcard_update(order_id):
    f = request.form
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        flash("Order not found.", "error")
        return redirect_with_token(url_for("orders"))
    jc = ensure_jobcard(order_id)

    # orders core fields
    qty_pcs = int(f.get("qty_pcs", 0) or 0)
    party_model = f.get("party_model", "").strip()
    model = f.get("model", "").strip()
    product = f"{party_model} - {model} \u00b7 {qty_pcs} pcs" if party_model else ""
    total_value = round(_jc_num(f.get("price")) * qty_pcs, 2)
    db.execute("UPDATE orders SET party=?, product=?, qty=?, value=?, delivery_date=?, priority=? WHERE id=?",
               (f.get("party", "").strip(), product, qty_pcs,
                total_value, f.get("exp_delivery", ""), f.get("priority", "normal"), order_id))

    # jobcard fields
    sets = []
    vals = []
    # raw materials: user khali chhode to BOM se auto-compose (kitna kitna use hoga)
    rm_val = f.get("raw_materials", "").strip()
    if not rm_val:
        bmodel_pre = db.query("SELECT * FROM product_models WHERE name=?",
                              (f.get("party_model", "").strip(),), one=True)
        if bmodel_pre:
            rm_lines = []
            for br in db.bom_rows_for(bmodel_pre["id"]):
                up = br["unit_pcs"] or 1000
                required = round(qty_pcs / up * br["qty_per"], 2) if up else 0
                rm_lines.append(f"{br['item_name']}: {required:g} {br['unit']}")
            rm_val = chr(10).join(rm_lines) if rm_lines else ""
    for k in ["party_model", "model", "odate", "board_type", "created_by", "checked_by", "order_via",
              "exp_delivery", "payment_status", "sheet_material", "copper_finish", "masking", "finish",
              "legend_printing", "pcb_type", "v_grooving", "customer_req", "short_reason",
              "handover_sign"]:
        sets.append(f"{k}=?")
        vals.append(f.get(k, "").strip())
    sets.append("raw_materials=?")
    vals.append(rm_val)
    for k in ["price", "rs_pcb"]:
        sets.append(f"{k}=?")
        vals.append(_jc_num(f.get(k)))
    for k in ["actual_pcb_x", "actual_pcb_y", "x_size", "y_size", "cnc_margin_x", "cnc_margin_y",
              "panel_x", "panel_y", "sheet_len", "sheet_w"]:
        sets.append(f"{k}=?")
        vals.append(_jc_num(f.get(k)))
    for k in ["x_qty", "y_qty", "panels_per_sheet", "sheets", "qty_panel", "pcs_panel",
              "total_qty", "short_qty"]:
        sets.append(f"{k}=?")
        vals.append(int(f.get(k, 0) or 0))

    # ---- stock maintenance: sheet material stock list se utha aur stock adjust karo ----
    def _inv_by_name(name):
        return db.query("SELECT * FROM inventory WHERE name=?", (name,), one=True)

    new_mat = f.get("sheet_material", "").strip()
    new_sheets = int(f.get("sheets", 0) or 0)
    try:
        old_mat = (jc["mat_code"] or "").strip()
        old_sheets = float(jc["mat_sheets"] or 0)
    except (KeyError, IndexError, TypeError):
        old_mat, old_sheets = "", 0.0
    # pehle purana material stock mein wapas
    if old_mat:
        inv_old = _inv_by_name(old_mat)
        if inv_old:
            db.execute("UPDATE inventory SET stock=stock+? WHERE id=?", (old_sheets, inv_old["id"]))
    # ab naya material stock se deduct
    mat_code, mat_sheets = "", 0
    inv_new = _inv_by_name(new_mat)
    if inv_new:
        mat_code = inv_new["name"]
        mat_sheets = new_sheets
        db.execute("UPDATE inventory SET stock=stock-? WHERE id=?", (new_sheets, inv_new["id"]))
    sets.append("mat_code=?")
    vals.append(mat_code)
    sets.append("mat_sheets=?")
    vals.append(mat_sheets)

    # ---- BOM raw material deduction (finished product ke hisaab se kitna use hoga) ----
    bmodel = db.query("SELECT * FROM product_models WHERE name=?", (f.get("party_model", "").strip(),), one=True)
    old_boms = db.query("SELECT * FROM jobcard_bom WHERE order_id=?", (order_id,))
    for ob in old_boms:
        db.execute("UPDATE inventory SET stock=stock+? WHERE id=?", (ob["deducted"], ob["item_id"]))
        db.execute("DELETE FROM jobcard_bom WHERE id=?", (ob["id"],))
    if bmodel:
        for br in db.bom_rows_for(bmodel["id"]):
            up = br["unit_pcs"] or 1000
            required = round(qty_pcs / up * br["qty_per"], 2) if up else 0
            if br["item_name"] == new_mat:
                # sheet material already mat_code flow se deduct hota hai -> double nahi
                db.execute("INSERT INTO jobcard_bom (order_id, item_id, required, deducted) VALUES (?,?,?,?)",
                           (order_id, br["item_id"], required, 0))
            else:
                db.execute("UPDATE inventory SET stock=stock-? WHERE id=?", (required, br["item_id"]))
                db.execute("INSERT INTO jobcard_bom (order_id, item_id, required, deducted) VALUES (?,?,?,?)",
                           (order_id, br["item_id"], required, required))

    vals.append(order_id)
    db.execute(f"UPDATE jobcard SET {', '.join(sets)} WHERE order_id=?", vals)
    flash("Job card saved. All changes saved.", "success")
    return redirect_with_token(url_for("jobcard", order_id=order_id))


def _dt_pair(f, d_key, t_key):
    d, t = f.get(d_key, ""), f.get(t_key, "")
    return f"{d} {t}".strip() if (d and t) else ""


@app.route("/jobcard/<int:order_id>/process", methods=["POST"])
@login_required
def jobcard_process(order_id):
    f = request.form
    rows = db.get_jc_processes(order_id)   # ORDER BY ord, id — extra rows included

    # ---- 1) existing rows update (rename / dates / qty / NEXT) ----
    for r in rows:
        pid = str(r["id"])
        new_proc = f.get(f"process_{pid}", "").strip()
        if new_proc and new_proc != r["process"] and not r.get("fixed"):
            if r["is_dropdown"] and new_proc not in db.JC_TOOL_OPTIONS:
                new_proc = r["process"]  # dropdown sirf TOOL options mein
            db.execute("UPDATE jobcard_process SET process=? WHERE id=?", (new_proc, r["id"]))
            r["process"] = new_proc
        start_dt = _dt_pair(f, f"start_date_{pid}", f"start_time_{pid}")
        end_dt = _dt_pair(f, f"end_date_{pid}", f"end_time_{pid}")
        nxt = f.get(f"next_{pid}", "").strip()
        db.execute(
            "UPDATE jobcard_process SET start_dt=?, end_dt=?, start_name=?, end_name=?, qty=?, next_process=? WHERE id=?",
            (start_dt, end_dt, f.get(f"start_name_{pid}", "").strip(), f.get(f"end_name_{pid}", "").strip(),
             int(f.get(f"qty_{pid}", 0) or 0), nxt, r["id"]))
        r["start_dt"], r["end_dt"], r["next_process"] = start_dt, end_dt, nxt

    worklist = list(rows)

    def insert_after(parent_pid, nd):
        if parent_pid == 0:
            worklist.insert(0, nd)   # starter row se aayi pehli process
            return
        for i, x in enumerate(worklist):
            if x["id"] == parent_pid:
                worklist.insert(i + 1, nd)
                return
        worklist.append(nd)

    # ---- 2) JS se aayi nayi rows (NEXT select karne par neeche line banti hai) ----
    # fields: add_process_<parentpid>_<k> wagerah
    add_groups = {}
    for key in f:
        m = re.match(r"^add_process_(\d+)_(\d+)$", key)
        if m and f.get(key, "").strip():
            pid, k = int(m.group(1)), int(m.group(2))
            add_groups[(pid, k)] = {
                "id": None,
                "process": f.get(key, "").strip(),
                "start_dt": _dt_pair(f, f"add_start_date_{pid}_{k}", f"add_start_time_{pid}_{k}"),
                "end_dt": _dt_pair(f, f"add_end_date_{pid}_{k}", f"add_end_time_{pid}_{k}"),
                "start_name": f.get(f"add_start_name_{pid}_{k}", "").strip(),
                "end_name": f.get(f"add_end_name_{pid}_{k}", "").strip(),
                "qty": int(f.get(f"add_qty_{pid}_{k}", 0) or 0),
                "next_process": f.get(f"add_next_{pid}_{k}", "").strip(),
            }
    starter_groups = sorted([gk for gk in add_groups if gk[0] == 0], key=lambda x: x[1])
    normal_groups = sorted([gk for gk in add_groups if gk[0] != 0], key=lambda x: (x[0], x[1]))
    # starter rows (pid=0) sabse pehli lines hain — k ke order mein shuru mein daalo
    for i, gk in enumerate(starter_groups):
        worklist.insert(i, add_groups[gk])
    for gk in normal_groups:
        insert_after(gk[0], add_groups[gk])

    # ---- 3) chain enforcement: NEXT selected hai par agli line wahi process nahi -
    #        to nayi line bana do (tab tak chalta rahega jab tak finish nahi) ----
    i = 0
    while i < len(worklist):
        r = worklist[i]
        nxt = (r.get("next_process") or "").strip()
        if nxt and nxt.upper() != "NONE":
            following = worklist[i + 1] if i + 1 < len(worklist) else None
            if not following or following["process"] != nxt:
                worklist.insert(i + 1, {"id": None, "process": nxt,
                                        "start_dt": "", "end_dt": "", "start_name": "",
                                        "end_name": "", "qty": 0, "next_process": ""})
        i += 1

    # ---- 4) ord renumber + nayi rows insert ----
    added = 0
    for pos, r in enumerate(worklist):
        ordv = (pos + 1) * 10
        if r.get("id"):
            db.execute("UPDATE jobcard_process SET ord=? WHERE id=?", (ordv, r["id"]))
        else:
            db.execute(
                "INSERT INTO jobcard_process (order_id, process, start_dt, end_dt, start_name, end_name, qty, next_process, ord) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (order_id, r["process"], r["start_dt"], r["end_dt"], r["start_name"],
                 r["end_name"], r["qty"], r["next_process"], ordv))
            added += 1
    current = advance_current(order_id)
    if added:
        flash(f"Process table saved. {added} nayi process line add hui. Current process: {current}.", "success")
    else:
        flash(f"Process table saved. Current process: {current}.", "success")
    return redirect_with_token(url_for("jobcard", order_id=order_id))


@app.route("/jobcard/<int:order_id>/process/<int:pid>/delete", methods=["POST"])
@login_required
def jobcard_process_delete(order_id, pid):
    row = next((r for r in db.get_jc_processes(order_id) if r["id"] == pid), None)
    if row:
        if row.get("fixed"):
            flash("🔒 Laminate Cutting pehla fixed process hai — delete nahi ho sakta.", "error")
            return redirect_with_token(url_for("jobcard", order_id=order_id))
        db.execute("DELETE FROM jobcard_process WHERE id=?", (pid,))
        flash(f"Process row '{row['process']}' hata diya 🗑", "success")
    return redirect_with_token(url_for("jobcard", order_id=order_id))


@app.route("/jobcard/<int:order_id>/dispatch", methods=["POST"])
@login_required
def jobcard_dispatch(order_id):
    f = request.form
    mode = f.get("mode", "").strip()
    if not mode:
        flash("Dispatch mode select karein.", "error")
        return redirect_with_token(url_for("jobcard", order_id=order_id))
    ddate = f.get("ddate", "") or datetime.date.today().isoformat()
    dtime = f.get("dtime", "") or datetime.datetime.now().strftime("%H:%M")
    by = f.get("dispatched_by", "").strip() or session.get("user_name", "")
    db.execute("INSERT INTO dispatch_log (order_id, ddate, dtime, mode, details, dispatched_by, ts) "
               "VALUES (?,?,?,?,?,?,?)",
               (order_id, ddate, dtime, mode, f.get("details", "").strip(), by,
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    flash(f"PCB dispatched: {mode} · {ddate} {dtime}.", "success")
    return redirect_with_token(url_for("jobcard", order_id=order_id))


@app.route("/jobcard/<int:order_id>/complete", methods=["POST"])
@login_required
def jobcard_complete(order_id):
    db.execute("UPDATE orders SET status='done', progress=100 WHERE id=?", (order_id,))
    flash(f"Job card marked COMPLETE.", "success")
    return redirect_with_token(url_for("jobcard", order_id=order_id))


@app.route("/jobcard/<int:order_id>/duplicate", methods=["POST"])
@login_required
def jobcard_duplicate(order_id):
    order, jc, _ = prep_jobcard(order_id)
    if not order:
        return redirect_with_token(url_for("orders"))
    count = db.query("SELECT COUNT(*) c FROM orders", one=True)["c"]
    new_no = f"#{count + 1}"
    new_id = db.execute(
        "INSERT INTO orders (order_no, party, board, product, qty, value, current_process, status, progress, priority, delivery_date, operator, started_qty, finished_qty, created_on) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_no, order["party"], order["board"], order["product"], order["qty"], order["value"],
         PROCESS_STEPS[0], "pending", 0, order["priority"], order["delivery_date"], "", 0, 0,
         datetime.date.today().isoformat()))
    db.execute(
        "INSERT INTO jobcard (order_id, party_model, model, odate, board_type, created_by, checked_by, order_via, "
        "exp_delivery, price, rs_pcb, payment_status, sheet_material, copper_finish, masking, finish, legend_printing, "
        "pcb_type, actual_pcb_x, actual_pcb_y, x_size, x_qty, y_size, y_qty, cnc_margin_x, cnc_margin_y, panel_x, panel_y, "
        "panels_per_sheet, sheets, qty_panel, pcs_panel, v_grooving, customer_req, raw_materials, total_qty, short_qty, "
        "short_reason, handover_sign) SELECT ?, party_model, model, odate, board_type, created_by, checked_by, order_via, "
        "exp_delivery, price, rs_pcb, payment_status, sheet_material, copper_finish, masking, finish, legend_printing, "
        "pcb_type, actual_pcb_x, actual_pcb_y, x_size, x_qty, y_size, y_qty, cnc_margin_x, cnc_margin_y, panel_x, panel_y, "
        "panels_per_sheet, sheets, qty_panel, pcs_panel, v_grooving, customer_req, raw_materials, total_qty, short_qty, "
        "short_reason, handover_sign FROM jobcard WHERE order_id=?",
        (new_id, order_id))
    db.get_jc_processes(new_id)
    flash(f"Duplicated as {new_no}.", "success")
    return redirect_with_token(url_for("jobcard", order_id=new_id))


@app.route("/jobcard/<int:order_id>/delete", methods=["POST"])
@login_required
def jobcard_delete(order_id):
    db.execute("DELETE FROM jobcard WHERE order_id=?", (order_id,))
    db.execute("DELETE FROM jobcard_process WHERE order_id=?", (order_id,))
    db.execute("DELETE FROM process_log WHERE order_id=?", (order_id,))
    db.execute("DELETE FROM orders WHERE id=?", (order_id,))
    flash("Job card deleted.", "success")
    return redirect_with_token(url_for("orders"))


@app.route("/jobcard/<int:order_id>/download")
@login_required
def jobcard_download(order_id):
    order, jc, procs = prep_jobcard(order_id)
    if not order:
        flash("Order not found.", "error")
        return redirect_with_token(url_for("orders"))
    dlogs = db.query("SELECT * FROM dispatch_log WHERE order_id=? ORDER BY id DESC", (order_id,))
    dispatch = dlogs[0] if dlogs else None
    issues = db.query("SELECT * FROM material_issues WHERE order_id=? ORDER BY id", (order_id,))
    html = render_template("jobcard_print.html", order=order, jc=jc, procs=procs, dispatch=dispatch,
                           issues=issues)
    return Response(html, mimetype="text/html",
                    headers={"Content-Disposition": f"attachment; filename=jobcard_{order['order_no'].replace('#','')}.html"})


# ---------------------------------------------------------------- product BOM editor
@app.route("/products/<int:pid>/bom", methods=["GET", "POST"])
@login_required
def product_bom(pid):
    pmodel = db.query("SELECT * FROM product_models WHERE id=?", (pid,), one=True)
    if not pmodel:
        flash("Finished product not found.", "error")
        return redirect_with_token(url_for("products"))
    if request.method == "POST":
        f = request.form
        if f.get("action") == "add" and (f.get("item_id") or "").strip():
            try:
                db.execute("INSERT INTO bom (model_id, item_id, qty_per, unit_pcs) VALUES (?,?,?,?)",
                           (pid, int(f.get("item_id")), float(f.get("qty_per", 0) or 0),
                            int(f.get("unit_pcs", 1000) or 1000)))
                flash("Raw material added to BOM.", "success")
            except Exception as e:
                flash(f"Add failed: {e}", "error")
        elif f.get("action") == "update":
            try:
                db.execute("UPDATE bom SET qty_per=?, unit_pcs=? WHERE id=? AND model_id=?",
                           (float(f.get("qty_per", 0) or 0), int(f.get("unit_pcs", 1000) or 1000),
                            int(f.get("bid") or 0), pid))
                flash("BOM row updated.", "success")
            except Exception as e:
                flash(f"Update failed: {e}", "error")
        elif f.get("action") == "delete":
            try:
                db.execute("DELETE FROM bom WHERE id=?", (int(f.get("bid") or 0),))
                flash("Removed from BOM.", "success")
            except Exception as e:
                flash(f"Delete failed: {e}", "error")
        return redirect_with_token(url_for("product_bom", pid=pid))
    bom_rows = db.bom_rows_for(pid)
    inv_items = db.query("SELECT * FROM inventory ORDER BY name")
    return render_template("bom.html", active="products", pmodel=pmodel, bom_rows=bom_rows,
                           inv_items=inv_items)


# ---------------------------------------------------------------- parties & ledger
def party_financials(name, ptype="customer"):
    billed = db.query("SELECT COALESCE(SUM(amount),0) s FROM billing WHERE party=?", (name,), one=True)["s"]
    received = db.query("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE party=? AND ptype='receipt'", (name,), one=True)["s"]
    purchased = db.query("SELECT COALESCE(SUM(amount),0) s FROM purchase_orders WHERE vendor=?", (name,), one=True)["s"]
    paid_out = db.query("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE party=? AND ptype='payment'", (name,), one=True)["s"]
    if ptype == "supplier":
        total, credits = purchased, paid_out
    else:
        total, credits = billed, received
    return billed, received, purchased, paid_out, total, credits, (total - credits)


def party_overdue(name, credit_days, due):
    """(label, days) - On time / n days late / n days left"""
    if due <= 0:
        return "Settled", None
    oldest = db.query("SELECT MIN(date) d FROM billing WHERE party=? AND status!='paid'", (name,), one=True)["d"]
    today = datetime.date.today()
    if not oldest:
        return "On time", None
    try:
        d = datetime.date.fromisoformat(oldest)
    except ValueError:
        return "On time", None
    due_date = d + datetime.timedelta(days=int(credit_days or 0))
    days = (today - due_date).days
    if days > 0:
        return f"{days} days late", days
    if days < 0:
        return f"{-days}d left", days
    return "Due today", 0


def ledger_entries(party):
    name, ptype = party["name"], party["ptype"]
    credit = int(party["credit_days"] or 0)
    today = datetime.date.today()
    entries = []
    if ptype == "supplier":
        for r in db.query("SELECT * FROM purchase_orders WHERE vendor=? ORDER BY date, id", (name,)):
            entries.append({"date": r["date"], "type": "Purchase", "ref": r["po_no"],
                            "amount": r["amount"], "status": r["status"]})
        for r in db.query("SELECT * FROM payments WHERE party=? AND ptype='payment' ORDER BY date, id", (name,)):
            entries.append({"date": r["date"], "type": "Payment", "ref": r["ref_no"],
                            "amount": -r["amount"], "status": r["mode"]})
    else:
        for r in db.query("SELECT * FROM billing WHERE party=? ORDER BY date, id", (name,)):
            st = r["status"]
            if st == "paid":
                status = "Paid"
            elif st == "overdue":
                status = "Overdue"
            else:
                try:
                    d = datetime.date.fromisoformat(r["date"])
                    days = (d + datetime.timedelta(days=credit) - today).days
                    status = f"{days}d left" if days >= 0 else f"{-days}d late"
                except ValueError:
                    status = "Pending"
            entries.append({"date": r["date"], "type": "Invoice", "ref": r["invoice_no"],
                            "amount": r["amount"], "status": status})
        for r in db.query("SELECT * FROM payments WHERE party=? AND ptype='receipt' ORDER BY date, id", (name,)):
            entries.append({"date": r["date"], "type": "Receipt", "ref": r["ref_no"],
                            "amount": -r["amount"], "status": r["mode"]})
    entries.sort(key=lambda e: (e["date"] or "", e["type"]))
    run = 0
    for e in entries:
        run += e["amount"]
        e["balance"] = run
    return entries


@app.route("/parties", methods=["GET", "POST"])
@login_required
def parties_page():
    if request.method == "POST":
        f = request.form
        action = f.get("action", "add")
        name = f.get("name", "").strip()
        if action == "add" and name:
            try:
                db.execute("INSERT INTO parties (name, ptype, credit_days, phone, gst, address) VALUES (?,?,?,?,?,?)",
                           (name, f.get("ptype", "customer"), int(f.get("credit_days", 0) or 0),
                            f.get("phone", "").strip(), f.get("gst", "").strip(), f.get("address", "").strip()))
                flash(f"Party '{name}' added.", "success")
            except Exception as e:
                flash(f"Could not add party: {e}", "error")
        elif action == "update":
            try:
                pid = int(f.get("pid", 0) or 0)
                db.execute("UPDATE parties SET name=?, ptype=?, credit_days=?, phone=?, gst=?, address=? WHERE id=?",
                           (name, f.get("ptype", "customer"), int(f.get("credit_days", 0) or 0),
                            f.get("phone", "").strip(), f.get("gst", "").strip(), f.get("address", "").strip(), pid))
                flash("Party updated.", "success")
            except Exception as e:
                flash(f"Update failed: {e}", "error")
        return redirect_with_token(url_for("parties_page"))

    q = request.args.get("q", "").strip().lower()
    rows = []
    for p in db.query("SELECT * FROM parties ORDER BY ptype DESC, name"):
        if q and q not in (p["name"] or "").lower():
            continue
        d = dict(p)
        b, rec, pur, paid, total, credits, due = party_financials(p["name"], p["ptype"])
        d["billed"] = b
        d["received"] = rec
        d["purchased"] = pur
        d["paid_out"] = paid
        d["due"] = due
        label, _days = party_overdue(p["name"], p["credit_days"], due)
        d["overdue_label"] = label
        d["credit"] = p["credit_days"]
        rows.append(d)
    total_parties = len(rows)
    customers = sum(1 for r in rows if r["ptype"] == "customer")
    suppliers = total_parties - customers
    edit_id = request.args.get("edit")
    edit_row = None
    if edit_id:
        edit_row = db.query("SELECT * FROM parties WHERE id=?", (int(edit_id),), one=True)
    return render_template("parties.html", active="parties", rows=rows,
                           total_parties=total_parties, customers=customers, suppliers=suppliers,
                           edit_row=edit_row, show_add=request.args.get("add"))


@app.route("/parties/<int:pid>/ledger")
@login_required
def party_ledger(pid):
    party = db.query("SELECT * FROM parties WHERE id=?", (pid,), one=True)
    if not party:
        flash("Party not found.", "error")
        return redirect_with_token(url_for("parties_page"))
    b, rec, pur, paid, total, credits, due = party_financials(party["name"], party["ptype"])
    entries = ledger_entries(party)
    label, _days = party_overdue(party["name"], party["credit_days"], due)
    return render_template("ledger.html", active="parties", party=party, entries=entries,
                           billed=b, received=rec, purchased=pur, paid_out=paid,
                           total=total, credits=credits, due=due, overdue_label=label)


@app.route("/parties/<int:pid>/delete", methods=["POST"])
@login_required
@admin_required
def party_delete(pid):
    db.execute("DELETE FROM parties WHERE id=?", (pid,))
    flash("Party removed.", "success")
    return redirect_with_token(url_for("parties_page"))


@app.route("/export/parties.csv")
@login_required
def export_parties():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Type", "Credit Days", "Balance"])
    for p in db.query("SELECT * FROM parties ORDER BY name"):
        *_, _due = party_financials(p["name"], p["ptype"])
        w.writerow([p["name"], p["ptype"], p["credit_days"], round(_due, 2)])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=parties.csv"})


# ---------------------------------------------------------------- reports
@app.route("/reports")
@login_required
def reports():
    order_stats = db.query("SELECT COUNT(*) total, "
                           "COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) done, "
                           "COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending, "
                           "COALESCE(SUM(value),0) total_value FROM orders", one=True)
    revenue = db.query("SELECT COALESCE(SUM(amount),0) billed, "
                       "COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) collected, "
                       "COALESCE(SUM(CASE WHEN status!='paid' THEN amount ELSE 0 END),0) outstanding FROM billing", one=True)
    att = db.query("SELECT e.name, e.department, "
                   "SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) present, "
                   "SUM(CASE WHEN a.status='absent' THEN 1 ELSE 0 END) absent "
                   "FROM employees e LEFT JOIN attendance a ON a.emp_id=e.id GROUP BY e.id ORDER BY e.id")
    machines = db.query("SELECT status, COUNT(*) c FROM machines GROUP BY status")
    machine_counts = {r["status"]: r["c"] for r in machines}
    inventory_low = db.query("SELECT * FROM inventory WHERE stock<=min_stock ORDER BY name")
    orders_by_party = db.query("SELECT party, COUNT(*) c, COALESCE(SUM(value),0) v FROM orders GROUP BY party ORDER BY v DESC")
    return render_template("reports.html", active="reports", order_stats=order_stats, revenue=revenue,
                           att=att, machine_counts=machine_counts, inventory_low=inventory_low,
                           orders_by_party=orders_by_party)


# ---------------------------------------------------------------- users & access
@app.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
def users():
    if request.method == "POST":
        f = request.form
        if f.get("action") == "update":
            try:
                uid = int(f.get("pid") or 0)
                existing = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
                if not existing:
                    flash("User not found.", "error")
                else:
                    new_pass = f.get("password", "")
                    db.execute("UPDATE users SET username=?, name=?, role=? WHERE id=?",
                               (f.get("username", "").strip(), f.get("name", "").strip(),
                                f.get("role", "operator"), uid))
                    if new_pass:
                        db.execute("UPDATE users SET password=? WHERE id=?", (new_pass, uid))
                    flash(f"User '{existing['name']}' updated.", "success")
            except Exception as e:
                flash(f"Update failed: {e}", "error")
        elif f.get("username", "").strip() and f.get("password", ""):
            try:
                db.execute("INSERT INTO users (username, password, name, role) VALUES (?,?,?,?)",
                           (f.get("username").strip(), f.get("password"), f.get("name", "").strip(),
                            f.get("role", "operator")))
                flash("User created.", "success")
            except Exception as e:
                flash(f"Could not create user: {e}", "error")
        return redirect_with_token(url_for("users"))
    rows = db.query("SELECT * FROM users ORDER BY id")
    edit_id = request.args.get("edit")
    edit_row = None
    if edit_id:
        edit_row = db.query("SELECT * FROM users WHERE id=?", (int(edit_id),), one=True)
    return render_template("users.html", active="users", users=rows,
                           show_add=request.args.get("add"), edit_row=edit_row)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def user_delete(user_id):
    if user_id == session.get("user_id"):
        flash("You cannot delete your own account.", "error")
    else:
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        flash("User removed.", "success")
    return redirect_with_token(url_for("users"))


# ---------------------------------------------------------------- export (Download button)
@app.route("/export/tracking.csv")
@login_required
def export_tracking():
    rows = db.query("SELECT order_no, party, board, current_process, status, operator, product, qty, progress, delivery_date "
                    "FROM orders ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order #", "Party", "Board", "Current Process", "Status", "Operator", "Finished Products", "Qty", "Progress %", "Delivery"])
    for r in rows:
        w.writerow([r["order_no"], r["party"], r["board"], r["current_process"],
                    r["status"].upper(), r["operator"], r["product"], r["qty"], r["progress"], r["delivery_date"]])
    out = buf.getvalue()
    return Response(out, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=live_process_tracking.csv"})


try:
    db.ensure_db()
except Exception:
    import traceback
    print("INIT_DB_ERROR at import:")
    traceback.print_exc()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
