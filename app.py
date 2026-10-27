"""
Shivaya Circuit — Smart ERP (PCB Manufacturing)
Run: python3 app.py  → http://0.0.0.0:8000
Login: sahil / admin123
"""
import os
import re
import json
import datetime
import calendar
import io
import math
import csv
import base64
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from functools import wraps
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import db

app = Flask(__name__)
# static files (CSS) browser mein cache ho — har page load par dobara download na ho
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400
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
_ATTACHMENT_VIEW_ENDPOINTS = {"model_pdf_view", "product_attachment_view"}

OPERATOR_READ = {"dashboard", "orders", "machines", "quality", "operator_view",
                 "product_attachment_view", "dispatch_photo", "jobcard", "my_work", "my_password"}
OPERATOR_WRITE = {"operator_action", "operator_issue", "machine_status", "quality", "quality_result",
                  "dispatch_attach_photo", "my_work_claim", "request_material", "request_action", "my_password"}


@app.before_request
def role_gate():
    """v3.70 — per-user permissions: admin SAB, baaki users sirf admin dikhaye gaye modules.
    (Purana operator gate unlisted endpoints ke liye barkarar.)"""
    if request.endpoint in ("login", "logout", "static", "pick_user", "switch_user", None):
        return
    if not session.get("user_id") or session.get("pending_pick"):
        return
    if session.get("user_role") == "admin":
        return
    # v3.78 — attachment/drawing sirf DEKHNE wale GET routes sab logged-in ke liye khule:
    # jis user ko pcbcalc/cutlist jaise page ki permission hai, uske liye us page par
    # dikh raha drawing/PDF bhi dekhna zaroori hai (warna panel me 'permission nahi' aata tha).
    # Upload/edit/delete is bypass me NAHI aate — wo POST par hai aur module gate se block rahenge.
    if request.method == "GET" and request.endpoint in _ATTACHMENT_VIEW_ENDPOINTS:
        return
    p = request.path or "/"
    need = None
    for pref, key in _MODULE_ROUTES:
        if p == pref or p.startswith(pref + "/"):
            need = key
            break
    if need == "admin":
        flash("Is page ki permission nahi hai — admin se permission mangwao.", "error")
        return redirect_with_token(url_for("dashboard"))
    if need is not None:
        if need in _user_perm_set():
            return
        flash("Is page ki permission nahi hai — admin se permission mangwao.", "error")
        return redirect_with_token(url_for("dashboard"))
    # v3.70: unlisted endpoint ka purana operator rule
    allowed = (request.endpoint in OPERATOR_WRITE
               or (request.endpoint in OPERATOR_READ and request.method == "GET"))
    if not allowed:
        flash("Is page ki permission nahi hai — admin se permission mangwao.", "error")
        return redirect_with_token(url_for("operator_view"))


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# ============ v3.70 — COMMON LOGIN + PER-USER PERMISSIONS ============
_COMMON_USER = "shivaya"
_COMMON_PASS = "shivaya@123"
_MODULES = [
    ("tracking", "Live Process Tracking"), ("orders", "Job Orders"), ("parties", "Parties"),
    ("products", "Finished Products"), ("pdfs", "PDF Library"), ("purchase", "Purchase Orders"),
    ("purchases", "Purchases (Stock In)"), ("cutlist", "Cut List Optimizer"), ("pcbcalc", "PCB Cost Calculator"),
    ("inventory", "Inventory"), ("billing", "Billing"), ("proforma", "Proforma (PI)"),
    ("payments", "Payments & Receipts"), ("employees", "Employees"), ("machines", "Machines"),
    ("quality", "Quality"), ("operator", "Operator View"), ("mywork", "Mera Work"),
    ("planning", "Production Planning"), ("material", "Material Planning"), ("reports", "Reports"),
]
_DEFAULT_PERMS = "orders,machines,quality,operator,mywork"
_MODULE_ROUTES = [
    ("/tracking", "tracking"), ("/orders", "orders"), ("/jobcard", "orders"), ("/parties", "parties"),
    ("/products", "products"), ("/pdfs", "pdfs"), ("/model-pdf", "pdfs"), ("/purchase-orders", "purchase"),
    ("/purchases", "purchases"), ("/cutlist", "cutlist"), ("/pcbcalc", "pcbcalc"), ("/inventory", "inventory"),
    ("/billing", "billing"), ("/proforma", "proforma"), ("/payments", "payments"), ("/employees", "employees"),
    ("/machines", "machines"), ("/quality", "quality"), ("/operator", "operator"), ("/my-work", "mywork"),
    ("/production-planning", "planning"), ("/material-planning", "material"), ("/reports", "reports"),
    ("/users", "admin"), ("/settings", "admin"),
]


def _users_perms_ensure():
    """users table me perms column ki guarantee (self-heal)."""
    try:
        _uc = [r["name"] for r in db.query("PRAGMA table_info(users)")]
        if "perms" not in _uc:
            db.execute("ALTER TABLE users ADD COLUMN perms TEXT DEFAULT ''")
    except Exception:
        pass


def _user_perm_set():
    """Logged-in user ke allowed module keys (admin = sab)."""
    if session.get("user_role") == "admin":
        return {k for k, _ in _MODULES} | {"admin"}
    raw = session.get("user_perms", "") or ""
    return {k for k in raw.split(",") if k}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if session.get("pending_pick"):
            return redirect(url_for("pick_user"))
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
    today = _today_ist()
    try:
        parties = db.query("SELECT id, name, ptype, credit_days FROM parties ORDER BY name")
    except Exception:
        parties = []
    return {"today": today.strftime("%A, %d %B %Y"),
            "today_iso": today.isoformat(),
            "ui_brand": _ui_brand(),
            "current_user_name": session.get("user_name", ""),
            "current_user_role": session.get("user_role", ""),
            "current_user_perms": _user_perm_set(),
            "all_parties": parties,
            "party_names": [p["name"] for p in parties]}


@app.template_filter("inr0")
def inr0_filter(value):
    # Paise exact dikhate hain (₹3.50 / ₹7.75), round NAHI hota —
    # lekin .00 hone par clean integer (₹4 / ₹45,000).
    try:
        s = f"{float(value):,.2f}"
        if s.endswith(".00"):
            s = s[:-3]
        return s
    except (TypeError, ValueError):
        return "0"


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


@app.template_filter("nosize")
def nosize_filter(value):
    # v3.32 — PI/Invoice item text ke aakhir se single-PCB size hatao ("77×77mm", "0×0mm", "82.5×83.4mm").
    # Sirf trailing "N×N mm" pattern cut hota hai — "BRACKET 50mm" jaise naam untouched rehte hain.
    return re.sub(r"\s*\d+(?:\.\d+)?\s*[\u00d7xX]\s*\d+(?:\.\d+)?\s*mm\s*$", "", str(value or ""),
                  flags=re.IGNORECASE)


@app.template_filter("dash")
def dash_filter(value):
    return value or "—"


@app.template_filter("gfmt")
def gfmt_filter(value):
    try:
        return f"{value:g}"
    except (TypeError, ValueError):
        return ""


@app.template_filter("modelname")
def modelname_filter(value):
    return _model_name(value) if value is not None else "—"

@app.template_filter("toolmm")
def toolmm_filter(value):
    """TOOL model ka naam/size nikalta hai — 'TOOL 78MM' ya '105 MM TOOL PCB' se '78MM'/'105MM'."""
    if not value:
        return ""
    mm = re.search(r"(\d{1,4}(?:\.\d+)?)\s*MM\b", str(value).upper())
    return f"{mm.group(1)}MM" if mm else ""


# ---------------------------------------------------------------- login
@app.route("/ping")
def ping():
    return jsonify({"ok": True})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # v3.70 \u2014 COMMON LOGIN: sab (owner samet) ek hi login se; baad me apna naam chunte hai
        if username.strip().lower() == _COMMON_USER and password == _COMMON_PASS:
            session.clear()
            session["pending_pick"] = 1
            target = url_for("pick_user")
            if request.form.get("ajax") == "1":
                return jsonify(ok=True, redirect=target, name="Common login")
            return redirect(target)
        # per-user accounts bhi chale (internal/tests) \u2014 UI par sirf common login dikh raha hai
        user = db.query("SELECT * FROM users WHERE username=? AND password=?", (username, password), one=True)
        if user:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user["role"]
            _users_perms_ensure()
            session["user_perms"] = ((user["perms"] or "").strip() if "perms" in user.keys() else "") or _DEFAULT_PERMS
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


@app.route("/pick-user", methods=["GET", "POST"])
def pick_user():
    """v3.70 \u2014 common login ke baad APNA NAAM chuno (yahi identity + permissions set karta hai)."""
    _users_perms_ensure()
    if request.method == "POST":
        try:
            uid = int(request.form.get("uid") or 0)
        except (TypeError, ValueError):
            uid = 0
        u = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if not u:
            flash("Pehle apna naam select karo.", "error")
            return redirect(url_for("pick_user"))
        session.pop("pending_pick", None)
        session["user_id"] = u["id"]
        session["user_name"] = u["name"]
        session["user_role"] = u["role"]
        session["user_perms"] = ((u["perms"] or "").strip() if "perms" in u.keys() else "") or _DEFAULT_PERMS
        token = make_token(u["id"])
        # v3.73 \u2014 naam chunte waqt hi password set karna ho to seedha Mera Password par
        if request.form.get("next") == "pw":
            return redirect(url_for("my_password") + f"?token={token}")
        home = "operator_view" if u["role"] != "admin" else "dashboard"
        return redirect(url_for(home) + f"?token={token}")
    if not session.get("pending_pick") and not session.get("user_id"):
        return redirect(url_for("login"))
    rows = db.query("SELECT * FROM users ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END, name")
    return render_template("pick_user.html", users=rows)


@app.route("/my-password", methods=["GET", "POST"])
@login_required
def my_password():
    """v3.72 \u2014 har user APNA password khud change kar sake (sidebar My Account se)."""
    u = db.query("SELECT * FROM users WHERE id=?", (session.get("user_id"),), one=True)
    if not u:
        return redirect(url_for("logout"))
    if request.method == "POST":
        cur = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        new2 = request.form.get("new_password2", "")
        # v3.75 — SIRF ADMIN apna password bhool jaye to bhi reset kar sake: current OPTIONAL
        # (admin waise bhi Users page se sabke password reset kar sakta hai; khaali = skip,
        #  agar likha hai to sahi hona zaroori. Operator ke liye current AB BHI zaroori.)
        _is_admin = (session.get("user_role") == "admin")
        if (not _is_admin or cur) and cur != (u["password"] or ""):
            flash("Current password galat hai.", "error")
        elif len(new) < 4:
            flash("Naya password kam se kam 4 characters ka ho.", "error")
        elif new != new2:
            flash("Naya password aur Confirm match nahi kar rahe.", "error")
        elif new == cur:
            flash("Naya password purane jaisa nahi ho sakta.", "error")
        else:
            db.execute("UPDATE users SET password=? WHERE id=?", (new, u["id"]))
            flash("Password change ho gaya \u2705 \u2014 agli baar naya password lagana.", "success")
            return redirect_with_token(url_for("my_password"))
    return render_template("my_password.html", active="mypassword", me=u)


@app.route("/switch-user")
def switch_user():
    """Kaun chal raha hai badalna ho \u2014 wapas naam-chuno par (login dobara nahi)."""
    session.clear()
    session["pending_pick"] = 1
    return redirect(url_for("pick_user"))


def _working_ops(order_ids):
    """v2.98 — kaun kar raha hai: current/last process row ka start_name (fallback end_name)."""
    if not order_ids:
        return {}
    ph = ",".join("?" * len(order_ids))
    wop = {}
    try:
        rows = db.query(f"SELECT order_id, start_name, end_name, start_dt, end_dt FROM jobcard_process "
                        f"WHERE order_id IN ({ph}) ORDER BY ord, id", tuple(order_ids))
    except Exception:
        return {}
    for r in rows:
        oid = r["order_id"]
        name = (r["start_name"] or "").strip() or (r["end_name"] or "").strip()
        if r["start_dt"] and not r["end_dt"] and (r["start_name"] or "").strip():
            wop[oid] = r["start_name"].strip()   # ABHI chal raha hai
        elif oid not in wop and name:
            wop[oid] = name                       # fallback: pehla known naam
    return wop


def _decorate_working_ops(rows):
    """orders rows ke operator ko live working operator se bharo (dict list return)."""
    rows = [dict(r) for r in rows]
    ids = [r["id"] for r in rows]
    wop = _working_ops(ids)
    for r in rows:
        r["operator"] = wop.get(r["id"]) or (r.get("operator") or "")
    return rows


# ---------------------------------------------------------------- dashboard
@app.route("/")
@login_required
def dashboard():
    today = _today_ist().isoformat()
    q = request.args.get("q", "").strip()

    # 13 queries -> 1 hi round trip (turso batch) — page 10x fast
    sqls = [
        ("SELECT COUNT(*) c FROM employees WHERE status='active'", ()),
        ("SELECT COUNT(*) c FROM attendance WHERE date=? AND status='present'", (today,)),
        ("SELECT COUNT(*) c FROM orders WHERE status!='done' AND NOT (status='pending' AND COALESCE(po_no,'')!='')", ()),  # v3.96
        ("SELECT COUNT(*) c FROM orders", ()),
        ("SELECT COUNT(*) c FROM orders WHERE status='pending' AND priority='urgent' AND COALESCE(po_no,'')=''", ()),  # v3.96
        ("SELECT COALESCE(SUM(amount),0) s FROM billing WHERE status='overdue'", ()),
        ("SELECT COALESCE(SUM(amount),0) s FROM billing WHERE status!='paid'", ()),
        ("SELECT COUNT(*) c FROM inventory WHERE stock <= min_stock", ()),
        ("SELECT COUNT(*) c FROM machines WHERE status='running'", ()),
        ("SELECT COUNT(*) c FROM machines", ()),
    ]
    if q:
        sqls.append(("SELECT * FROM orders WHERE status IN ('pending','hold','running','done') AND COALESCE(po_no,'')='' AND (order_no LIKE ? OR party LIKE ? OR product LIKE ?) "
                     "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'hold' THEN 1 WHEN 'running' THEN 2 ELSE 3 END, id DESC LIMIT 4",  # v3.96
                     (f"%{q}%", f"%{q}%", f"%{q}%")))
    else:
        sqls.append(("SELECT * FROM orders WHERE status IN ('pending','hold','running','done') AND COALESCE(po_no,'')='' "
                     "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'hold' THEN 1 WHEN 'running' THEN 2 ELSE 3 END, id DESC LIMIT 4", ()))  # v3.96
    sqls.append(("SELECT * FROM orders WHERE status IN ('pending','hold','running','done') AND COALESCE(po_no,'')='' "
                 "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'hold' THEN 1 WHEN 'running' THEN 2 ELSE 3 END, id DESC LIMIT 4", ()))  # v3.96
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
                           tracking=_decorate_working_ops(tracking), overview=_decorate_working_ops(overview),  # v2.98
                           notices=notices, q=q)


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
    rows = _decorate_working_ops(rows)   # v2.98 — OPERATOR = kaun kar raha hai
    for r in rows:
        r["step_idx"] = PROCESS_STEPS.index(r["current_process"]) if r["current_process"] in PROCESS_STEPS else 0
        # agla process AUTO (order ke flow se) + uski assignment (kaun karega)
        nxt = flow_next(r["id"]) if r["status"] == "pending" else ""
        r["next_proc"] = nxt or None
        r["next_op"] = None
        if r["next_proc"] and r["next_proc"] != "Completed":
            asg = proc_assignment(r["id"], r["next_proc"])
            r["next_op"] = (asg["operator"] or "") if asg and asg["operator"] not in ("", "Unassigned") else None
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
        _started = db.query("SELECT COUNT(*) c FROM jobcard_process WHERE order_id=? AND start_dt!='' AND end_dt=''",
                            (order_id,), one=True)
        _st = "done" if next_step == "Completed" else ("running" if (_started["c"] or 0) > 0 else "pending")
        db.execute("UPDATE orders SET current_process=?, operator=?, progress=?, status=? WHERE id=?",
                   (next_step, operator, max(order["progress"], progress), _st, order_id))
        db.execute("UPDATE process_log SET status='done' WHERE order_id=? AND process=?", (order_id, order["current_process"]))
        # v2.99: assignment table bhi sync — "Mera Work" me sahii dikhe
        _nx = db.query("SELECT operator FROM order_assignments WHERE order_id=? AND process=?",
                       (order_id, next_step), one=True)
        if _nx:
            db.execute("UPDATE order_assignments SET operator=? WHERE order_id=? AND process=?",
                       (operator, order_id, next_step))
        else:
            db.execute("INSERT INTO order_assignments (order_id, process, operator) VALUES (?,?,?)",
                       (order_id, next_step, operator))
        db.execute("INSERT INTO process_log (order_id, process, status, operator, updated_on) VALUES (?,?,?,?,?)",
                   (order_id, next_step, "done" if next_step == "Completed" else "active", operator,
                    _today_ist().isoformat()))
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
            # cut list / finished product se QTY OF PANEL + PCS/PANEL seedha job order par
            qty_panel = math.ceil(qty_pcs / pmodel["pcs_panel"]) if pmodel and pmodel["pcs_panel"] and qty_pcs else 0
            pcs_panel = (pmodel["pcs_panel"] or 0) if pmodel else 0
            count = db.query("SELECT COUNT(*) c FROM orders", one=True)["c"]
            new_id = db.execute(
                "INSERT INTO orders (order_no, party, board, product, qty, value, current_process, status, progress, priority, delivery_date, operator, started_qty, finished_qty, created_on, qty_panel, pcs_panel) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"#{count + 1}", f.get("party").strip(), f.get("board", "Single Side"),
                 product_str, qty_pcs, float(f.get("value", 0) or 0),
                 PROCESS_STEPS[0], "pending", 0, f.get("priority", "normal"),
                 f.get("delivery_date", ""), "", 0, 0, _now_dt(),
                 qty_panel, pcs_panel))
            if pmodel:
                # job card ko finished product ki poori sizing se pre-fill karo
                db.execute(
                    "INSERT OR IGNORE INTO jobcard (order_id, party_model, model, odate, exp_delivery, price, total_qty, "
                    "actual_pcb_x, actual_pcb_y, panel_x, panel_y, panels_per_sheet, sheets, pcs_panel, sheet_len, sheet_w) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id, party_model, model_code, _now_dt(),
                     f.get("delivery_date", ""), float(f.get("value", 0) or 0), qty_pcs,
                     pmodel["pcb_len"], pmodel["pcb_w"], pmodel["panel_len"], pmodel["panel_w"],
                     pmodel["panels_sheet"], pmodel["sheets"], pmodel["pcs_panel"],
                     pmodel["sheet_len"], pmodel["sheet_w"]))
            else:
                db.execute("INSERT OR IGNORE INTO jobcard (order_id, party_model, odate, exp_delivery, price, total_qty) "
                           "VALUES (?,?,?,?,?,?)",
                           (new_id, party_model, _now_dt(),
                            f.get("delivery_date", ""), float(f.get("value", 0) or 0), qty_pcs))
            flash("Job order created.", "success")
        return redirect_with_token(url_for("orders"))
    q = request.args.get("q", "").strip()
    filt = request.args.get("filter", "active")
    today = _today_ist().isoformat()
    where = ""
    params = ()
    if q:
        where = "WHERE party LIKE ? OR product LIKE ? OR order_no LIKE ?"
        params = (f"%{q}%", f"%{q}%", f"%{q}%")
    rows = db.query(f"SELECT * FROM orders {where} ORDER BY id DESC", params)
    rows = [r for r in rows if not (r["status"] == "pending" and (r["po_no"] or "").strip())]  # v3.96: PO card list me sirf APPROVE ke baad
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

    def _panel_info(r):
        """QTY OF PANEL + NO OF PCS/PANEL — cut list se aaya ho to wahi, warna
        finished product model se nikalo (product string 'name - code' se match)."""
        qp = r.get("qty_panel") or 0
        pp = r.get("pcs_panel") or 0
        if not pp:
            prod = (r["product"] or "").split(" · ")[0].strip()
            pm = db.query("SELECT * FROM product_models WHERE (name || ' - ' || model_code)=? OR name=?",
                          (prod, prod), one=True) if prod else None
            if pm and pm["pcs_panel"]:
                pp = pm["pcs_panel"]
        if not qp and pp and (r["qty"] or 0) > 0:
            qp = math.ceil((r["qty"] or 0) / pp)
        return qp, pp

    def _is_held(oid, proc):
        if not proc or proc.lower() == "completed":
            return False
        last = db.query("SELECT action FROM jobcard_log WHERE order_id=? AND process=? "
                        "ORDER BY id DESC LIMIT 1", (oid, proc), one=True)
        return bool(last and last["action"] == "pause")

    cols = [[] for _ in KANBAN_COLS]
    for r in rows:
        r = dict(r)
        r["disp"] = dmap.get(r["id"])
        r["cur_proc"], r["cur_status"] = _jc_status(r)
        if r["cur_status"] != "done" and _is_held(r["id"], r["cur_proc"]):
            r["cur_status"] = "hold"
        r["qty_panel"], r["pcs_panel"] = _panel_info(r)
        # v3.87: REVIEW card (PO se bana, approve nahi hua) pehle column me dikhe
        _kc = 0 if (not r["current_process"] and r["status"] == "pending") else kanban_col(r["current_process"])
        cols[_kc].append(r)
    done = [dict(r) for r in done_rows]
    for r in done:
        r["disp"] = dmap.get(r["id"])
        r["qty_panel"], r["pcs_panel"] = _panel_info(r)
    return render_template("orders.html", active="orders", cols=cols, done=done, q=q, filt=filt,
                           KANBAN_COLS=KANBAN_COLS, PROCESS_STEPS=PROCESS_STEPS,
                           models=models, show_add=request.args.get("add"))


# ---------------------------------------------------------------- purchase orders
# Business IST (Asia/Kolkata) me chalta hai — Vercel server UTC par hota hai,
# isliye har entry ka DATE/TIME explicit IST me record karte hain.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")


def _now_ist():
    return datetime.datetime.now(IST)


def _today_ist():
    return datetime.datetime.now(IST).date()


def _now_dt():
    """Entry ke waqt ka current DATE + TIME (IST) — har entry me save hota hai."""
    return _now_ist().strftime("%Y-%m-%d %H:%M")


def _fl(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ui_brand():
    """v3.29 — poore software ka theme color (meta 'ui_theme'), default amber."""
    r = db.query("SELECT value FROM meta WHERE key='ui_theme'", one=True)
    v = (r["value"] if r else "") or ""
    return v if re.match(r"^#[0-9a-fA-F]{6}$", v) else "#f59e0b"


def _doc_brand():
    """v3.25 — PI/Invoice print ka brand color (meta 'doc_brand'), default blue."""
    r = db.query("SELECT value FROM meta WHERE key='doc_brand'", one=True)
    v = (r["value"] if r else "") or ""
    return v if re.match(r"^#[0-9a-fA-F]{6}$", v) else "#1d4ed8"


_BANK_DEFAULT = ("Company's Bank Details\nA/c Holder's Name : SHIVAYA CIRCUIT PRIVATE LIMITED\n"
                 "Bank Name : HDFC BANK\nA/c No. : 50200117041601\n"
                 "Branch & IFS Code : HARGOVIND ENCLAVE & HDFC0000481")


def _po_company():
    """PO/Invoice print ka company header — Purchase Orders page se editable (meta me save)."""
    import json as _json
    def _clean(v):
        # v3.26: kabhi-kabhi meta me unrendered jinja literal save ho gaye the — unhe blank maano
        v = (v or "").strip() if isinstance(v, str) else ""
        return "" if ("{{" in v or "}}" in v) else v
    row = db.query("SELECT value FROM meta WHERE key='po_company'", one=True)
    if row and row["value"]:
        try:
            d = _json.loads(row["value"])
            return {"name": _clean(d.get("name")) or "Shivaya Circuit Pvt. Ltd.",
                    "address": _clean(d.get("address")), "phone": _clean(d.get("phone")),
                    "gstin": _clean(d.get("gstin"))}
        except Exception:
            pass
    return {"name": "Shivaya Circuit Pvt. Ltd.", "address": "", "phone": "", "gstin": ""}


def _amt_words(n):
    """Amount in words (Indian system): 25,000 -> Rupees Twenty Five Thousand Only."""
    if n is None:
        return ""
    try:
        n = round(float(n), 2)
    except (TypeError, ValueError):
        return ""
    rupees = int(n)
    paise = int(round((n - rupees) * 100))
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
            "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    def two(x):
        if x < 20:
            return ones[x]
        return (tens[x // 10] + (" " + ones[x % 10] if x % 10 else "")).strip()
    def three(x):
        h, r = x // 100, x % 100
        return ((ones[h] + " Hundred " if h else "") + (two(r) if r else "")).strip()
    if rupees == 0:
        words = "Zero"
    else:
        parts = []
        cr = rupees // 10000000
        lk = (rupees // 100000) % 100
        th = (rupees // 1000) % 100
        rest = rupees % 1000
        if cr:
            parts.append(two(cr) + " Crore")
        if lk:
            parts.append(two(lk) + " Lakh")
        if th:
            parts.append(two(th) + " Thousand")
        if rest:
            parts.append(three(rest))
        words = " ".join(parts)
    out = "Rupees " + words
    if paise:
        out += " and " + two(paise) + " Paise"
    return out + " Only"


def _items_from_form(f):
    """PO/Billing form se line items nikaalo: item/qty/rate/amount; legacy single item bhi chalega."""
    items_in = f.getlist("item_n")
    rows = []
    for i, it in enumerate(items_in):
        it = (it or "").strip()
        if not it:
            continue
        qty = (f.getlist("qty_n")[i] if i < len(f.getlist("qty_n")) else "").strip()
        rate = _fl(f.getlist("rate_n")[i] if i < len(f.getlist("rate_n")) else "")
        amt = _fl(f.getlist("amt_n")[i] if i < len(f.getlist("amt_n")) else "")
        if amt == 0 and rate > 0:
            try:
                amt = round(rate * float(qty), 2) if qty else 0
            except ValueError:
                amt = 0
        rows.append((it, qty, rate, amt))
    if not rows and (f.get("item") or "").strip():
        rows = [(f.get("item", "").strip(), (f.get("qty") or "").strip(), 0.0, _fl(f.get("amount")))]
    return rows


def _hsn_aligned(f):
    """v3.18 — hsn_n inputs ko item_n jaisa hi align karo (khali item skip, PCB default 85340000)."""
    hs = [(x or "").strip() for x in f.getlist("hsn_n")]
    out = []
    for i, it in enumerate(f.getlist("item_n")):
        if not (it or "").strip():
            continue
        out.append((hs[i] if i < len(hs) else "") or "85340000")
    return out


def _totals(items, tax):
    subtotal = round(sum(r[3] for r in items), 2)
    tax_amt = round(subtotal * (float(tax or 0)) / 100, 2)
    return subtotal, tax_amt, round(subtotal + tax_amt, 2)


@app.route("/purchase-orders", methods=["GET", "POST"])
@login_required
def purchase_orders():
    if request.method == "POST":
        f = request.form
        # COMPANY DETAILS (PO print ka header)
        if f.get("action") == "company":
            import json as _json
            comp = {"name": (f.get("co_name") or "").strip() or "Shivaya Circuit Pvt. Ltd.",
                    "address": (f.get("co_address") or "").strip(),
                    "phone": (f.get("co_phone") or "").strip(),
                    "gstin": (f.get("co_gstin") or "").strip()}
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('po_company', ?)",
                       (_json.dumps(comp, ensure_ascii=False),))
            flash("Company details save ho gaye — PO print me yahi dikhenge ✅", "success")
            return redirect_with_token(url_for("purchase_orders"))
        rows = _items_from_form(f)
        vendor = (f.get("vendor") or "").strip()
        if vendor:
            subtotal, tax_amt, grand = _totals(rows, f.get("tax_percent"))
            count = db.query("SELECT COUNT(*) c FROM purchase_orders", one=True)["c"]
            po_no = f"PO-{304 + count}"
            po_id = db.execute(
                "INSERT INTO purchase_orders (po_no, vendor, item, qty, amount, status, date, "
                "vendor_address, vendor_phone, delivery_date, tax_percent, note, created_on) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (po_no, vendor, rows[0][0] + (f" +{len(rows) - 1} aur" if len(rows) > 1 else ""),
                 rows[0][1], grand, f.get("status", "pending"), _today_ist().isoformat(),
                 (f.get("vendor_address") or "").strip(), (f.get("vendor_phone") or "").strip(),
                 (f.get("delivery_date") or "").strip(), _fl(f.get("tax_percent")), (f.get("note") or "").strip(),
                 _now_dt()))
            for r in rows:
                db.execute("INSERT INTO purchase_order_items (po_id, item, qty, rate, amount) "
                           "VALUES (?,?,?,?,?)", (po_id, r[0], r[1], r[2], r[3]))
            flash(f"Purchase order {po_no} create ho gaya ✅", "success")
        return redirect_with_token(url_for("purchase_orders"))
    rows = db.query("SELECT * FROM purchase_orders ORDER BY id DESC")
    items_by_po = {}
    for it in db.query("SELECT * FROM purchase_order_items ORDER BY id"):
        items_by_po.setdefault(it["po_id"], []).append(it)
    parties = db.query("SELECT * FROM parties ORDER BY name")
    party_names = {p["name"] for p in parties}
    import json as _json
    parties_json = _json.dumps([{"name": p["name"], "phone": p["phone"], "address": p["address"]}
                                for p in parties])
    edit_po = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_po = db.query("SELECT * FROM purchase_orders WHERE id=?", (int(eid),), one=True)
    return render_template("purchase_orders.html", active="purchase", rows=rows,
                           items_by_po=items_by_po, comp=_po_company(),
                           all_parties=parties, party_names=party_names, parties_json=parties_json,
                           edit_items=items_by_po.get(edit_po["id"], []) if edit_po else [],
                           show_add=request.args.get("add"), edit_po=edit_po)


@app.route("/purchases", methods=["GET", "POST"])
@login_required
def purchases():
    """🛒 PURCHASE (STOCK IN) — product/item kharido → inventory stock mein add."""
    if request.method == "POST":
        f = request.form
        item_name = (f.get("item_name") or "").strip()
        custom = (f.get("custom_item") or "").strip()
        if custom:
            item_name = custom
        qty = _fl(f.get("qty"))
        rate = _fl(f.get("rate"))
        gst = _fl(f.get("gst_percent"))
        unit = (f.get("unit") or "pcs").strip() or "pcs"
        if not item_name or qty <= 0:
            flash("Item + Qty bharna (qty 0 se zyada).", "error")
            return redirect_with_token(url_for("purchases"))
        amount = round(qty * rate, 2)
        total = round(amount + amount * gst / 100.0, 2)
        vendor = (f.get("vendor") or "").strip()
        bill_no = (f.get("bill_no") or "").strip()
        pdate = (f.get("pdate") or "").strip() or _today_ist().isoformat()
        status = (f.get("status") or "pending").strip()
        notes = (f.get("notes") or "").strip()
        # item inventory se link — naya item ho to inventory mein banao (stock 0 se)
        inv = db.query("SELECT * FROM inventory WHERE name=?", (item_name,), one=True)
        if inv:
            item_id = inv["id"]
            if inv["unit"]:
                unit = unit if (f.get("unit") or "").strip() else inv["unit"]
        else:
            import time as _t
            code = "PUR-" + str(int(_t.time()))[-6:]
            item_id = db.execute("INSERT INTO inventory (code, name, category, stock, min_stock, unit) "
                                 "VALUES (?,?,?,?,?,?)", (code, item_name, "Purchased", 0, 0, unit))
        db.execute("INSERT INTO purchases (item_id, item_name, qty, unit, rate, amount, gst_percent, "
                   "total, vendor, bill_no, date, status, notes, created_on) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (item_id, item_name, qty, unit, rate, amount, gst, total, vendor, bill_no,
                    pdate, status, notes, _now_ist().strftime("%Y-%m-%d %H:%M")))
        # STOCK IN: inventory stock + qty
        if item_id:
            db.execute("UPDATE inventory SET stock = COALESCE(stock,0) + ? WHERE id=?", (qty, item_id))
        flash(f"🛒 Purchase save: {item_name} +{qty:g} {unit} — stock mein add ho gaya ✅", "success")
        return redirect_with_token(url_for("purchases"))
    rows = db.query("SELECT * FROM purchases ORDER BY id DESC")
    tot = db.query("SELECT COALESCE(SUM(total),0) t, "
                   "COALESCE(SUM(CASE WHEN status='paid' THEN total END),0) paid, "
                   "COALESCE(SUM(CASE WHEN status!='paid' THEN total END),0) pend FROM purchases", one=True)
    this_month = db.query("SELECT COALESCE(SUM(total),0) t FROM purchases WHERE date LIKE ?",
                          (_today_ist().strftime("%Y-%m") + "%",), one=True)["t"]
    inv_items = db.query("SELECT * FROM inventory ORDER BY name")
    parties = db.query("SELECT name FROM parties ORDER BY name")
    months = ["", "January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    _today = _today_ist()
    return render_template("purchases.html", active="purchases", rows=rows, tot=tot,
                           this_month=this_month, inv_items=inv_items, parties=parties,
                           today=_today.isoformat(),
                           now_month=months[_today.month] + " " + str(_today.year),
                           show_add=request.args.get("add"))


@app.route("/purchases/<int:pid>/delete", methods=["POST"])
@login_required
def purchase_delete(pid):
    """Galti se entry? Delete = stock wapas minus."""
    p = db.query("SELECT * FROM purchases WHERE id=?", (pid,), one=True)
    if p:
        if p["item_id"]:
            db.execute("UPDATE inventory SET stock = COALESCE(stock,0) - ? WHERE id=?",
                       (p["qty"], p["item_id"]))
        db.execute("DELETE FROM purchases WHERE id=?", (pid,))
        flash(f"Purchase delete — {p['item_name']} {p['qty']:g} {p['unit']} stock se minus ho gaya.", "success")
    return redirect_with_token(url_for("purchases"))


@app.route("/purchase-orders/<int:po_id>/edit", methods=["POST"])
@login_required
def po_edit(po_id):
    f = request.form
    po_no = (f.get("po_no") or "").strip()
    vendor = (f.get("vendor") or "").strip()
    if not vendor:
        flash("Vendor zaroori hai.", "error")
        return redirect_with_token(url_for("purchase_orders", edit=po_id))
    rows = _items_from_form(f)
    subtotal, tax_amt, grand = _totals(rows, f.get("tax_percent"))
    try:
        db.execute(
            "UPDATE purchase_orders SET po_no=?, vendor=?, item=?, qty=?, amount=?, status=?, date=?, "
            "vendor_address=?, vendor_phone=?, delivery_date=?, tax_percent=?, note=? WHERE id=?",
            (po_no, vendor, rows[0][0] + (f" +{len(rows) - 1} aur" if len(rows) > 1 else "") if rows else "",
             rows[0][1] if rows else "", grand, f.get("status", "pending"),
             f.get("date") or _today_ist().isoformat(),
             (f.get("vendor_address") or "").strip(), (f.get("vendor_phone") or "").strip(),
             (f.get("delivery_date") or "").strip(), _fl(f.get("tax_percent")), (f.get("note") or "").strip(),
             po_id))
        db.execute("DELETE FROM purchase_order_items WHERE po_id=?", (po_id,))
        for r in rows:
            db.execute("INSERT INTO purchase_order_items (po_id, item, qty, rate, amount) "
                       "VALUES (?,?,?,?,?)", (po_id, r[0], r[1], r[2], r[3]))
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
    if st in ("pending", "ordered", "in_transit", "received", "cancelled"):
        db.execute("UPDATE purchase_orders SET status=? WHERE id=?", (st, po_id))
    return redirect_with_token(url_for("purchase_orders"))


@app.route("/purchase-orders/<int:po_id>/print")
@login_required
def po_print(po_id):
    po = db.query("SELECT * FROM purchase_orders WHERE id=?", (po_id,), one=True)
    if not po:
        flash("Purchase order nahi mila.", "error")
        return redirect_with_token(url_for("purchase_orders"))
    items = db.query("SELECT * FROM purchase_order_items WHERE po_id=? ORDER BY id", (po_id,))
    if not items:
        # purane single-item POs
        items = [{"item": po["item"], "qty": po["qty"], "rate": 0, "amount": po["amount"]}]
    subtotal, tax_amt, grand = _totals([(i["item"], i["qty"], i["rate"], i["amount"]) for i in items],
                                       po["tax_percent"])
    return render_template("po_print.html", po=po, items=items, active="purchase",
                           comp=_po_company(), subtotal=subtotal, tax=float(po["tax_percent"] or 0),
                           tax_amt=tax_amt, grand=grand, grand_words=_amt_words(grand))


# ---------------------------------------------------------------- cut list optimizer (PCB Panel Builder)
FIELD_KEYS = ["pcb_len", "pcb_w", "pcbs_x", "pcbs_y", "gap_x", "gap_y",
              "border_l", "border_r", "border_t", "border_b",
              "gang_x", "gang_y", "sheet_len", "sheet_w", "kerf_x", "kerf_y", "sheets", "use",
              "panel_len", "panel_w", "panel_base_len", "panel_base_w",
              "per_sq_inch", "pcb_price", "gaps_x", "gaps_y", "pgaps_x", "pgaps_y", "order_qty",
              "pcb_code", "party_code", "party_name", "model_code", "shape", "sheet_thickness"]
DEFAULTS = {"pcb_len": "40", "pcb_w": "50", "pcbs_x": "10", "pcbs_y": "5",
            "gap_x": "0", "gap_y": "0",
            "border_l": "0", "border_r": "0", "border_t": "5", "border_b": "5",
            "gang_x": "1", "gang_y": "1", "sheet_len": "1000", "sheet_w": "1200",
            "kerf_x": "2", "kerf_y": "2", "sheets": "1", "use": "1",
            "panel_len": "400", "panel_w": "260", "shape": "rect",
            "per_sq_inch": "", "pcb_price": "", "pgaps_x": "", "pgaps_y": ""}
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


def _parse_gaps(val, n, default):
    """PCB-to-PCB gaps — HAR boundary ka apna gap (comma separated: '0,2.4,0').
    n = kitne boundaries chahiye (pcbs-1). Khali/kam values -> default gap se pad.
    Isse har pcb ke beech alag gap rakh sakte ho (jaise 1-2 me 0, 2-3 me 2.4...)."""
    n = max(0, n)
    out = []
    if val:
        for part in str(val).split(","):
            try:
                out.append(max(0.0, float(part.strip())))
            except ValueError:
                out.append(default)
            if len(out) >= n:
                break
    while len(out) < n:
        out.append(default)
    return out[:n]


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

    if min(sheet_len, sheet_w) <= 0:
        return None
    # locked mode: PCB builder data zaroori; manual mode: panel size zaroori
    if use_locked and min(pcb_len, pcb_w, pcbs_x, pcbs_y) <= 0:
        return None
    if not use_locked and (panel_len_in <= 0 or panel_w_in <= 0):
        return None

    # --- single panel size: locked (computed from PCB builder) or manual ---
    # HAR PCB-to-PCB boundary ka apna gap (gaps_x/gaps_y) — kahin 0, kahin gap
    gaps_x = _parse_gaps(p.get("gaps_x"), pcbs_x - 1, gap_x)
    gaps_y = _parse_gaps(p.get("gaps_y"), pcbs_y - 1, gap_y)
    if use_locked or panel_len_in <= 0 or panel_w_in <= 0:
        panel_len = pcb_len * pcbs_x + sum(gaps_x) + border_l + border_r
        panel_w = pcb_w * pcbs_y + sum(gaps_y) + border_t + border_b
        locked = True
    else:
        # v2.87 — visible field me pehle ka CUTTING size ho to base panel use karo (double-add guard)
        _bl87, _bw87 = _f(p, "panel_base_len"), _f(p, "panel_base_w")
        if _bl87 > 0 and _bw87 > 0:
            panel_len, panel_w = _bl87, _bw87
        else:
            panel_len, panel_w = panel_len_in, panel_w_in
        locked = False

    pcs_panel = pcbs_x * pcbs_y
    gx_str = "+".join(f"{g:g}" for g in gaps_x) if len(set(gaps_x)) > 1 else f"{gaps_x[0]:g}x{len(gaps_x)}" if gaps_x else "0"
    gy_str = "+".join(f"{g:g}" for g in gaps_y) if len(set(gaps_y)) > 1 else f"{gaps_y[0]:g}x{len(gaps_y)}" if gaps_y else "0"
    formula = (f"({pcb_len:g}x{pcbs_x} + gap[{gx_str}] +{border_l:g}+{border_r:g} border={panel_len:g}mm, "
               f"{pcb_w:g}x{pcbs_y} + gap[{gy_str}] +{border_t:g}+{border_b:g} border={panel_w:g}mm)")

    # --- gang / cutting size ---
    # Multiplier active ho to PANEL fields mein CUTTING SIZE dikhta hai (screenshot jaisa).
    # Hidden base fields mein single panel rehta hai — roundtrip ke liye.
    gang_active = gang_x > 1 or gang_y > 1
    # PANEL-TO-PANEL GAPS (per joint): '0,2,0' — sirf jahan gap chahiye
    pgaps_x = _gap_list(p.get("pgaps_x"), gang_x - 1, kerf_x) if gang_x > 1 else []
    pgaps_y = _gap_list(p.get("pgaps_y"), gang_y - 1, kerf_y) if gang_y > 1 else []
    # v2.87 \u2014 kerf (CNC MARGIN) CUTTING SIZE me ADD hota hai: cutting = panel(+gaps) + 2*margin
    def _cut_add(_l, _w):
        return _l + 2 * kerf_x, _w + 2 * kerf_y
    # v3.58 — GANG PANEL = panels + joints ONLY (CNC margin YAHAN nahi jodta)
    if gang_active:
        gang_len = panel_len * gang_x + (sum(pgaps_x) if pgaps_x else 0)
        gang_w = panel_w * gang_y + (sum(pgaps_y) if pgaps_y else 0)
    else:
        gang_len, gang_w = panel_len, panel_w
    # CUTTING SIZE = GANG + 2×CNC MARGIN — sirf PANEL IN SHEET me yahi fit/show hota hai
    cutting_len, cutting_w = _cut_add(gang_len, gang_w)

    def fits(unit_l, unit_w):
        if unit_l <= 0 or unit_w <= 0:
            return 0, 0
        # v3.50 \u2014 CNC MARGIN cutting size ke ANDAR hai: units edge-to-edge fit (extra kerf spacing nahi)
        nx = int(sheet_len // unit_l)
        ny = int(sheet_w // unit_w)
        return nx, ny

    # sheet fitting unit = cutting size (gang); counts = gang units
    nx, ny = fits(cutting_len, cutting_w)
    normal_panels = nx * ny
    rx, ry = fits(cutting_w, cutting_len)
    rotated_panels = rx * ry

    # --- mixed rows: normal rows + rotated rows on one sheet ---
    # v3.50 \u2014 margin cutting size ke andar: pitch = cutting size khud (kerf spacing double nahi)
    per_normal = int(sheet_len // cutting_len) if cutting_len > 0 else 0
    per_rot = int(sheet_len // cutting_w) if cutting_w > 0 else 0
    h_n, h_r = cutting_w, cutting_len
    max_n = int(sheet_w // h_n) if h_n > 0 else 0
    max_m = int(sheet_w // h_r) if h_r > 0 else 0
    mixed_n = mixed_m = 0
    mixed_panels = 0
    for n in range(max_n + 1):
        for m in range(max_m + 1):
            if n == 0 and m == 0:
                continue
            height = n * h_n + m * h_r
            if height <= sheet_w + 1e-9:
                panels = n * per_normal + m * per_rot
                if panels > mixed_panels:
                    mixed_panels, mixed_n, mixed_m = panels, n, m

    if mixed_panels > normal_panels and mixed_panels > rotated_panels:
        best = "mixed"
        panels_per_sheet = mixed_panels
    elif rotated_panels > normal_panels:
        best, panels_per_sheet = "rotated", rotated_panels
    else:
        best, panels_per_sheet = "normal", normal_panels

    grid_x, grid_y, cell_len, cell_w = nx, ny, cutting_len, cutting_w
    if best == "rotated":
        grid_x, grid_y, cell_len, cell_w = rx, ry, cutting_w, cutting_len
    pcs_unit = pcs_panel * gang_x * gang_y
    pcs_per_sheet = panels_per_sheet * pcs_unit
    total_panels = panels_per_sheet * sheets
    total_pcs = pcs_per_sheet * sheets

    # used area = cutting unit ka pura area (multiplier ke beech ka kerf gap included)
    # v2.96 — ORDER QTY (PANELS): N sheets ke TOTAL PANELS/PCB + WASTE PANELS (dim = cutting size)
    _oq96 = _i(p, "order_qty", 0)
    order_qty = _oq96 if _oq96 > 0 else 0
    if order_qty:
        panels_needed = order_qty                                     # order PANELS me hi hai
        sheets_needed = (order_qty + panels_per_sheet - 1) // panels_per_sheet if panels_per_sheet else 0
        waste_panels = max(0, total_panels - order_qty)
        waste_pcs = max(0, total_pcs - order_qty * pcs_unit)
    else:
        panels_needed = sheets_needed = waste_panels = waste_pcs = 0
    order_info = {"order_qty": order_qty, "panels_needed": panels_needed,
                  "sheets_needed": sheets_needed, "waste_panels": waste_panels, "waste_pcs": waste_pcs}

    used = total_panels * cutting_len * cutting_w
    sheet_area = sheets * sheet_len * sheet_w
    wastage = round(100 * (sheet_area - used) / sheet_area, 1) if sheet_area else 0
    inches = f"{sheet_len / 25.4:.1f}\u2033 \u00d7 {sheet_w / 25.4:.1f}\u2033"

    return {
        "pcb_len": pcb_len, "pcb_w": pcb_w, "pcbs_x": pcbs_x, "pcbs_y": pcbs_y,
        "shape": (p.get("shape") or "rect").strip().lower() if isinstance(p.get("shape"), str) else "rect",
        "gap_x": gap_x, "gap_y": gap_y,
        "gaps_x": gaps_x, "gaps_y": gaps_y,
        "border_l": border_l, "border_r": border_r, "border_t": border_t, "border_b": border_b,
        "gang_x": gang_x, "gang_y": gang_y,
        "pgaps_x": pgaps_x, "pgaps_y": pgaps_y,
        "sheet_len": sheet_len, "sheet_w": sheet_w, "kerf_x": kerf_x, "kerf_y": kerf_y, "sheets": sheets,
        "panel_len": panel_len, "panel_w": panel_w, "locked": locked,
        "gang_active": gang_active, "cutting_len": cutting_len, "cutting_w": cutting_w,
        "pcs_panel": pcs_panel, "pcs_unit": pcs_unit, "formula": formula,
        "nx": nx, "ny": ny, "normal_panels": normal_panels,
        "rx": rx, "ry": ry, "rotated_panels": rotated_panels,
        "per_normal": per_normal, "per_rot": per_rot,
        "mixed_n": mixed_n, "mixed_m": mixed_m, "mixed_panels": mixed_panels,
        "best": best, "grid_x": grid_x, "grid_y": grid_y,
        "cell_len": cell_len, "cell_w": cell_w,
        "gang_len": gang_len, "gang_w": gang_w,
        "panels_per_sheet": panels_per_sheet, "pcs_per_sheet": pcs_per_sheet,
        "total_panels": total_panels, "total_pcs": total_pcs,
        "order_qty": order_qty, "panels_needed": panels_needed, "sheets_needed": sheets_needed,
        "waste_panels": waste_panels, "waste_pcs": waste_pcs, "order_info": order_info,
        "wastage": wastage, "inches": inches,
    }


def _model_cut_calc(m):
    """v4.27 — SHEET CUTTING SIZE (4A/N/4B/N) + UPS ko GANG engine (compute_layout) se nikaalo.
    Model ka apna panel+gang+kerf+sheet use hota hai — GANG preview jaisa hi jawab.
    Data kam (panel/sheet missing) -> None (stored value chalega)."""
    try:
        if not m or not (m.get("panel_len") or 0) > 0 or not (m.get("panel_w") or 0) > 0 \
           or not (m.get("sheet_len") or 0) > 0 or not (m.get("sheet_w") or 0) > 0:
            return None
        _p = {"pcb_len": m.get("pcb_len") or 0, "pcb_w": m.get("pcb_w") or 0,
              "pcbs_x": m.get("pcbs_x") or 0, "pcbs_y": m.get("pcbs_y") or 0,
              "gap_x": 0, "gap_y": 0, "gaps_x": "", "gaps_y": "",
              "border_l": 0, "border_r": 0, "border_t": 0, "border_b": 0,
              "panel_len": m.get("panel_len"), "panel_w": m.get("panel_w"),
              "gang_x": m.get("gang_x") or 1, "gang_y": m.get("gang_y") or 1,
              "sheet_len": m.get("sheet_len"), "sheet_w": m.get("sheet_w"),
              "kerf_x": m.get("kerf_x") or 0, "kerf_y": m.get("kerf_y") or 0,
              "sheets": 1, "use": "0"}
        r = compute_layout(_p)
    except Exception:
        return None
    if not r:
        return None
    _gp = max(1, int(m.get("gang_x") or 1))
    _gq = max(1, int(m.get("gang_y") or 1))
    _pp = int(m.get("pcs_panel") or 0)
    _unit = _pp * _gp * _gq if _pp else int(r.get("pcs_unit") or 0)
    return {"cutting_len": float(r.get("cutting_len") or 0),
            "cutting_w": float(r.get("cutting_w") or 0),
            "x_qty": int(r.get("grid_x") or 0), "y_qty": int(r.get("grid_y") or 0),
            "ups": _unit, "pps": int(r.get("panels_per_sheet") or 0)}


def svg_panel_preview(r):
    """PREVIEW #1 — PANEL (v4.35: DARK BLUEPRINT — user reference jaisa):
    dark navy canvas, white panel outline + corner mounting holes, teal PCB cells with
    cyan L/W arrows (plain numbers), top/left SEGMENT chains (border|cell|gap), overall
    dims (top = width, right = height), legend (PCB board / Rail and waste) + 5-box strip."""
    BG, PANELB = "#0d1b28", "#e8eef4"
    CELLF, CELLB, CYAN = "#17384a", "#4a7c93", "#56ccf2"
    TXT, CHAIN, MUT, TEAL, NAVY = "#eaf4f8", "#7f9db0", "#8fb0bf", "#2ba7b5", "#eef6fa"
    W, TOP, AVH, BOTG, STR = 470, 48, 330, 58, 96
    H = TOP + AVH + BOTG + STR + 8
    pl, pw = r["panel_len"], r["panel_w"]
    scale = min((W - 128) / pl, (AVH - 44) / pw)
    P, Q = pl * scale, pw * scale
    x0, y0 = (W - P) / 2, TOP + 44 + (AVH - 44 - Q) / 2
    cw, ch = r["pcb_len"] * scale, r["pcb_w"] * scale
    gxs, gys = r["gap_x"] * scale, r["gap_y"] * scale
    bx, by = r["border_l"] * scale, r["border_t"] * scale
    gaps_x_s = r.get("gaps_x") or [r["gap_x"]] * max(0, r["pcbs_x"] - 1)
    gaps_y_s = r.get("gaps_y") or [r["gap_y"]] * max(0, r["pcbs_y"] - 1)
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block;background:{BG}">']

    def _g35(n):
        return f"{n:g}"

    # Title + teal rule
    s.append(f'<text x="{W / 2:.0f}" y="24" text-anchor="middle" font-size="15.5" font-weight="800" fill="#f2f8fb" font-family="Segoe UI,Arial" letter-spacing="1">PCB PANELIZATION LAYOUT</text>')
    s.append(f'<line x1="26" y1="33" x2="{W - 26}" y2="33" stroke="{TEAL}" stroke-width="2"/>')
    # hatch pattern — rail/waste (dark subtle diagonal)
    s.append('<defs><pattern id="hatchPL" patternUnits="userSpaceOnUse" width="5" height="5" '
             'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="5" stroke="#24425a" stroke-width="1.4"/></pattern></defs>')
    # RAIL / WASTE — border strips + gaps hatch me
    if r["border_l"] > 0.05:
        s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bx:.1f}" height="{Q:.1f}" fill="url(#hatchPL)"/>')
    if r["border_r"] > 0.05:
        s.append(f'<rect x="{x0 + P - r['border_r'] * scale:.1f}" y="{y0:.1f}" width="{r['border_r'] * scale:.1f}" height="{Q:.1f}" fill="url(#hatchPL)"/>')
    if r["border_t"] > 0.05:
        s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{P:.1f}" height="{by:.1f}" fill="url(#hatchPL)"/>')
    if r["border_b"] > 0.05:
        s.append(f'<rect x="{x0:.1f}" y="{y0 + Q - r['border_b'] * scale:.1f}" width="{P:.1f}" height="{r['border_b'] * scale:.1f}" fill="url(#hatchPL)"/>')
    inner_w = r["pcbs_x"] * cw + sum(gaps_x_s) * scale
    inner_h = r["pcbs_y"] * ch + sum(gaps_y_s) * scale
    gox = bx
    for i in range(r["pcbs_x"]):
        if i:
            gw_ = (gaps_x_s[i - 1] if i - 1 < len(gaps_x_s) else r["gap_x"]) * scale
            if gw_ > 0.05:
                s.append(f'<rect x="{x0 + gox - gw_:.1f}" y="{y0 + by:.1f}" width="{gw_:.1f}" height="{inner_h:.1f}" fill="url(#hatchPL)"/>')
        if i < r["pcbs_x"] - 1:
            gox += cw + (gaps_x_s[i] * scale if i < len(gaps_x_s) else gxs)
    goy = by
    for j in range(r["pcbs_y"]):
        if j:
            gh_ = (gaps_y_s[j - 1] if j - 1 < len(gaps_y_s) else r["gap_y"]) * scale
            if gh_ > 0.05:
                s.append(f'<rect x="{x0 + bx:.1f}" y="{y0 + goy - gh_:.1f}" width="{inner_w:.1f}" height="{gh_:.1f}" fill="url(#hatchPL)"/>')
        if j < r["pcbs_y"] - 1:
            goy += ch + (gaps_y_s[j] * scale if j < len(gaps_y_s) else gys)
    # Panel outline (white rounded) + 4 corner mounting holes
    s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{P:.1f}" height="{Q:.1f}" fill="none" stroke="{PANELB}" stroke-width="1.8" rx="7"/>')
    for hx, hy in ((x0 + 5.5, y0 + 5.5), (x0 + P - 5.5, y0 + 5.5), (x0 + 5.5, y0 + Q - 5.5), (x0 + P - 5.5, y0 + Q - 5.5)):
        s.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="2.4" fill="{BG}" stroke="{PANELB}" stroke-width="1"/>')
    # PCB cells — v4.35: teal fill + cyan L/W arrows (plain numbers — reference jaisa)
    if cw > 2.4 and ch > 2.4:
        cl35, cwd35 = _g35(r["pcb_len"]), _g35(r["pcb_w"])
        shp = r.get("shape") or "rect"
        ox = bx
        for i in range(r["pcbs_x"]):
            oy = by
            for j in range(r["pcbs_y"]):
                cx_, cy_ = x0 + ox, y0 + oy
                if shp == "round":
                    s.append(f'<ellipse cx="{cx_ + cw / 2:.1f}" cy="{cy_ + ch / 2:.1f}" rx="{cw / 2:.1f}" ry="{ch / 2:.1f}" fill="{CELLF}" stroke="{CELLB}" stroke-width="1"/>')
                else:
                    s.append(f'<rect x="{cx_:.1f}" y="{cy_:.1f}" width="{cw:.1f}" height="{ch:.1f}" fill="{CELLF}" stroke="{CELLB}" stroke-width="1"/>')
                if cw >= 26 and ch >= 24:
                    ay = cy_ + ch * 0.30
                    ax1, ax2 = cx_ + 6, cx_ + cw - 6
                    s.append(f'<line x1="{ax1:.1f}" y1="{ay:.1f}" x2="{ax2:.1f}" y2="{ay:.1f}" stroke="{CYAN}" stroke-width="0.9"/>')
                    s.append(f'<path d="M {ax1:.1f} {ay:.1f} l 3.4 -2 v 4 z" fill="{CYAN}"/><path d="M {ax2:.1f} {ay:.1f} l -3.4 -2 v 4 z" fill="{CYAN}"/>')
                    s.append(f'<text x="{cx_ + cw / 2:.1f}" y="{ay - 2.6:.1f}" text-anchor="middle" font-size="7.4" font-weight="700" fill="{TXT}" font-family="Segoe UI,Arial">{cl35}</text>')
                    xa = cx_ + cw * 0.26
                    ay1, ay2 = cy_ + ch * 0.44, cy_ + ch - 4
                    s.append(f'<line x1="{xa:.1f}" y1="{ay1:.1f}" x2="{xa:.1f}" y2="{ay2:.1f}" stroke="{CYAN}" stroke-width="0.9"/>')
                    s.append(f'<path d="M {xa:.1f} {ay1:.1f} l -2 3.4 h 4 z" fill="{CYAN}"/><path d="M {xa:.1f} {ay2:.1f} l -2 -3.4 h 4 z" fill="{CYAN}"/>')
                    s.append(f'<text x="{xa + 4.5:.1f}" y="{(ay1 + ay2) / 2 + 2.6:.1f}" font-size="7.4" font-weight="700" fill="{TXT}" font-family="Segoe UI,Arial">{cwd35}</text>')
                elif cw >= 20 and ch >= 13:
                    s.append(f'<text x="{cx_ + cw / 2:.1f}" y="{cy_ + ch / 2 + 2.6:.1f}" text-anchor="middle" font-size="7.2" font-weight="700" fill="{CYAN}" font-family="Segoe UI,Arial">{cl35} \u00d7 {cwd35}</text>')
                oy += ch + (gaps_y_s[j] * scale if j < len(gaps_y_s) else gys)
            if i < r["pcbs_x"] - 1:
                ox += cw + (gaps_x_s[i] * scale if i < len(gaps_x_s) else gxs)
    # ---- dimensions: overall arrows + SEGMENT chains (v4.35 reference style) ----
    def _arr_h(x, y, left):
        s.append(f'<path d="M {x:.1f} {y:.1f} l {3.4 if left else -3.4} -2 v 4 z" fill="{CHAIN}"/>')

    def _arr_v(x, y, up):
        s.append(f'<path d="M {x:.1f} {y:.1f} l -2 {3.4 if up else -3.4} h 4 z" fill="{CHAIN}"/>')

    # TOP overall (line + outward arrows, text upar)
    dy1 = y0 - 36
    s.append(f'<line x1="{x0:.1f}" y1="{dy1:.1f}" x2="{x0 + P:.1f}" y2="{dy1:.1f}" stroke="{CHAIN}" stroke-width="1"/>')
    _arr_h(x0, dy1, True)
    _arr_h(x0 + P, dy1, False)
    s.append(f'<line x1="{x0:.1f}" y1="{y0 - 2:.1f}" x2="{x0:.1f}" y2="{dy1 - 4:.1f}" stroke="{CHAIN}" stroke-width="0.6"/>')
    s.append(f'<line x1="{x0 + P:.1f}" y1="{y0 - 2:.1f}" x2="{x0 + P:.1f}" y2="{dy1 - 4:.1f}" stroke="{CHAIN}" stroke-width="0.6"/>')
    s.append(f'<text x="{x0 + P / 2:.1f}" y="{dy1 - 5:.1f}" text-anchor="middle" font-size="11.5" font-weight="700" fill="{TXT}" font-family="Segoe UI,Arial">{_g35(pl)} mm</text>')
    # TOP segment chain: border | cell | gap | cell | border (ticks + plain numbers)
    dy2 = y0 - 16
    s.append(f'<line x1="{x0:.1f}" y1="{dy2:.1f}" x2="{x0 + P:.1f}" y2="{dy2:.1f}" stroke="{CHAIN}" stroke-width="0.8"/>')
    pts = [x0]
    segs = []
    cur = x0
    if r["border_l"] > 0.05:
        segs.append((cur, cur + bx, _g35(r["border_l"])))
        cur += bx
        pts.append(cur)
    for i in range(r["pcbs_x"]):
        segs.append((cur, cur + cw, cl35))
        cur += cw
        pts.append(cur)
        if i < r["pcbs_x"] - 1:
            g_ = (gaps_x_s[i] * scale if i < len(gaps_x_s) else gxs)
            if g_ > 0.05:
                segs.append((cur, cur + g_, _g35(gaps_x_s[i] if i < len(gaps_x_s) else r["gap_x"])))
                cur += g_
                pts.append(cur)
    if r["border_r"] > 0.05:
        segs.append((cur, cur + r["border_r"] * scale, _g35(r["border_r"])))
        cur += r["border_r"] * scale
        pts.append(cur)
    for pt in pts:
        s.append(f'<line x1="{pt:.1f}" y1="{dy2 - 3.5:.1f}" x2="{pt:.1f}" y2="{dy2 + 3.5:.1f}" stroke="{CHAIN}" stroke-width="0.8"/>')
    for a_, b_, lab_ in segs:
        wpx = b_ - a_
        if wpx >= 3 and len(lab_) * 3.9 <= wpx + 8:
            s.append(f'<text x="{(a_ + b_) / 2:.1f}" y="{dy2 - 3.5:.1f}" text-anchor="middle" font-size="{"6.0" if wpx < 12 else "7.2"}" font-weight="600" fill="{TXT}" font-family="Segoe UI,Arial">{lab_}</text>')
    # LEFT segment chain: border | row | gap | row | border
    lx = x0 - 13
    s.append(f'<line x1="{lx:.1f}" y1="{y0:.1f}" x2="{lx:.1f}" y2="{y0 + Q:.1f}" stroke="{CHAIN}" stroke-width="0.8"/>')
    ypts = [y0]
    ysegs = []
    cur = y0
    if r["border_t"] > 0.05:
        ysegs.append((cur, cur + by, _g35(r["border_t"])))
        cur += by
        ypts.append(cur)
    for j in range(r["pcbs_y"]):
        ysegs.append((cur, cur + ch, cwd35))
        cur += ch
        ypts.append(cur)
        if j < r["pcbs_y"] - 1:
            g_ = (gaps_y_s[j] * scale if j < len(gaps_y_s) else gys)
            if g_ > 0.05:
                ysegs.append((cur, cur + g_, _g35(gaps_y_s[j] if j < len(gaps_y_s) else r["gap_y"])))
                cur += g_
                ypts.append(cur)
    if r["border_b"] > 0.05:
        ysegs.append((cur, cur + r["border_b"] * scale, _g35(r["border_b"])))
        cur += r["border_b"] * scale
        ypts.append(cur)
    for pt in ypts:
        s.append(f'<line x1="{lx - 3.5:.1f}" y1="{pt:.1f}" x2="{lx + 3.5:.1f}" y2="{pt:.1f}" stroke="{CHAIN}" stroke-width="0.8"/>')
    for a_, b_, lab_ in ysegs:
        wpx = b_ - a_
        if wpx >= 3 and len(lab_) * 3.9 <= wpx + 8:
            s.append(f'<text x="{lx - 5:.1f}" y="{(a_ + b_) / 2 + 2.5:.1f}" text-anchor="end" font-size="{"6.0" if wpx < 12 else "7.2"}" font-weight="600" fill="{TXT}" font-family="Segoe UI,Arial">{lab_}</text>')
    s.append(f'<line x1="{lx - 4:.1f}" y1="{y0:.1f}" x2="{x0 - 2:.1f}" y2="{y0:.1f}" stroke="{CHAIN}" stroke-width="0.6"/>')
    s.append(f'<line x1="{lx - 4:.1f}" y1="{y0 + Q:.1f}" x2="{x0 - 2:.1f}" y2="{y0 + Q:.1f}" stroke="{CHAIN}" stroke-width="0.6"/>')
    # RIGHT overall height (line + outward arrows, text horizontal right)
    dx = x0 + P + 15
    s.append(f'<line x1="{dx:.1f}" y1="{y0:.1f}" x2="{dx:.1f}" y2="{y0 + Q:.1f}" stroke="{CHAIN}" stroke-width="1"/>')
    _arr_v(dx, y0, True)
    _arr_v(dx, y0 + Q, False)
    s.append(f'<line x1="{x0 + P + 2:.1f}" y1="{y0:.1f}" x2="{dx + 4:.1f}" y2="{y0:.1f}" stroke="{CHAIN}" stroke-width="0.6"/>')
    s.append(f'<line x1="{x0 + P + 2:.1f}" y1="{y0 + Q:.1f}" x2="{dx + 4:.1f}" y2="{y0 + Q:.1f}" stroke="{CHAIN}" stroke-width="0.6"/>')
    s.append(f'<text x="{dx + 8:.1f}" y="{y0 + Q / 2 + 3.5:.1f}" font-size="10.5" font-weight="700" fill="{TXT}" font-family="Segoe UI,Arial">{_g35(pw)} mm</text>')
    # Legend — PCB board | Rail and waste | All dimensions in mm
    ly = y0 + Q + 17
    s.append(f'<rect x="16" y="{ly - 7.5:.1f}" width="9" height="9" fill="{CELLF}" stroke="{CELLB}" stroke-width="0.9"/>')
    s.append(f'<text x="29" y="{ly:.1f}" font-size="7.8" font-weight="600" fill="{MUT}" font-family="Segoe UI,Arial">PCB board</text>')
    s.append(f'<rect x="92" y="{ly - 7.5:.1f}" width="9" height="9" fill="url(#hatchPL)" stroke="{CHAIN}" stroke-width="0.7"/>')
    s.append(f'<text x="105" y="{ly:.1f}" font-size="7.8" font-weight="600" fill="{MUT}" font-family="Segoe UI,Arial">Rail and waste</text>')
    s.append(f'<text x="{W - 16}" y="{ly:.1f}" text-anchor="end" font-size="7.8" font-weight="600" fill="{MUT}" font-family="Segoe UI,Arial">All dimensions in mm</text>')
    # Info strip (5 boxes — dark, teal border)
    sy, sh_ = TOP + AVH + BOTG, STR
    sx, sw_ = 8, W - 16
    s.append(f'<rect x="{sx}" y="{sy}" width="{sw_}" height="{sh_}" fill="#0f2230" stroke="{TEAL}" stroke-width="1.5" rx="9"/>')
    cols = [0.245, 0.205, 0.15, 0.175, 0.225]
    labels = ["PANEL SIZE", "PCB SIZE", "QTY / ARRAY", "GAP", "BORDER"]
    vals = [
        [f"{pl:.2f} \u00d7 {pw:.2f} mm", ""],
        [f"{r['pcb_len']:.2f} \u00d7 {r['pcb_w']:.2f} mm", ""],
        [f'{r["pcs_panel"]} PCBs ({r["pcbs_x"]}\u00d7{r["pcbs_y"]})', ""],
        [f"X: {r['gap_x']:.2f} mm", f"Y: {r['gap_y']:.2f} mm"],
        [f"L: {r['border_l']:.2f}  R: {r['border_r']:.2f}", f"T: {r['border_t']:.2f}  B: {r['border_b']:.2f}"],
    ]

    def _fs19(t, big):
        # v4.19: text box me fit na ho to font apne-aap chhota (overlap nahi)
        return 8.2 if len(t) > 21 else (9.2 if len(t) > 17 else (10.2 if big else 9.3))
    cx = sx
    for k in range(5):
        cwid = sw_ * cols[k]
        if k:
            s.append(f'<line x1="{cx:.1f}" y1="{sy + 14}" x2="{cx:.1f}" y2="{sy + sh_ - 14}" stroke="#23485c" stroke-width="0.9"/>')
        mid = cx + cwid / 2
        s.append(f'<text x="{mid:.1f}" y="{sy + 22}" text-anchor="middle" font-size="7.6" font-weight="700" fill="{MUT}" font-family="Segoe UI,Arial" letter-spacing="0.5">{labels[k]}</text>')
        s.append(f'<text x="{mid:.1f}" y="{sy + 47}" text-anchor="middle" font-size="{_fs19(vals[k][0], k < 3)}" font-weight="800" fill="{NAVY}" font-family="Segoe UI,Arial">{vals[k][0]}</text>')
        if vals[k][1]:
            s.append(f'<text x="{mid:.1f}" y="{sy + 66}" text-anchor="middle" font-size="{_fs19(vals[k][1], False)}" font-weight="800" fill="{NAVY}" font-family="Segoe UI,Arial">{vals[k][1]}</text>')
        cx += cwid
    s.append("</svg>")
    return "".join(s)


def _svg_gang(s, cx, cy, cl, cw, gx, gy, kx, ky, fill, stroke, sw_,
              in_fill="#e4f2f3", in_stroke="#2ba7b5"):
    s.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cl:.1f}" height="{cw:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw_}" rx="2"/>')
    if gx > 1 or gy > 1:
        p = (cl - kx * (gx - 1)) / gx
        q = (cw - ky * (gy - 1)) / gy
        for a in range(gx):
            for b in range(gy):
                s.append(f'<rect x="{cx + a * (p + kx):.1f}" y="{cy + b * (q + ky):.1f}" width="{p:.1f}" height="{q:.1f}" fill="{in_fill}" stroke="{in_stroke}" stroke-width="0.7"/>')


def _pack_axis(rem, sl, sw, vertical):
    """v3.53 \u2014 ek axis me bands packing + har band ke BACHE HUE width me doosre
    panel ka filler column (rotation auto) \u2014 reference optimizer jaisa mixed layout."""
    A, B = (sw, sl) if vertical else (sl, sw)
    place = []
    band = 0.0
    guard = 0
    while band < B - 1e-9 and guard < 60:
        guard += 1
        best = None
        for i, p in enumerate(rem):
            if p["qty"] <= 0:
                continue
            for orient, (L, W) in (("n", (p["l"], p["w"])), ("r", (p["w"], p["l"]))):
                if L <= A + 1e-9 and W <= B - band + 1e-9:
                    n = min(int(A // L), p["qty"])
                    if n > 0:
                        area = n * L * W
                        if best is None or area > best["area"] + 1e-9:
                            best = {"pi": i, "l": L, "w": W, "n": n, "orient": orient, "area": area}
        if best is None:
            break

        def _emit(seg, b0, i0):
            bl = seg["n"] * seg["l"]
            if vertical:
                place.append({"pi": seg["pi"], "n": seg["n"], "pl": seg["l"], "pw": seg["w"],
                              "orient": seg["orient"], "x": b0, "y": i0, "bw": seg["w"], "bh": bl, "v": True})
            else:
                place.append({"pi": seg["pi"], "n": seg["n"], "pl": seg["l"], "pw": seg["w"],
                              "orient": seg["orient"], "x": i0, "y": b0, "bw": bl, "bh": seg["w"], "v": False})

        _emit(best, band, 0.0)
        rem[best["pi"]]["qty"] -= best["n"]
        inner = best["n"] * best["l"]
        left = A - inner
        fg = 0
        while left > 1e-9 and fg < 30:
            fg += 1
            fb = None
            for i, p in enumerate(rem):
                if p["qty"] <= 0:
                    continue
                for orient, (L, W) in (("n", (p["l"], p["w"])), ("r", (p["w"], p["l"]))):
                    if L <= left + 1e-9 and W <= best["w"] + 1e-9:
                        n = min(int(left // L), p["qty"])
                        if n > 0:
                            ar = n * L * W
                            if fb is None or ar > fb["area"] + 1e-9:
                                fb = {"pi": i, "l": L, "w": W, "n": n, "orient": orient, "area": ar}
            if fb is None:
                break
            _emit(fb, band, inner)
            rem[fb["pi"]]["qty"] -= fb["n"]
            inner += fb["n"] * fb["l"]
            left = A - inner
        band += best["w"]
    used = sum(s["n"] * s["pl"] * s["pw"] for s in place)
    return place, used


def _pack_sheet_best(rem, sl, sw):
    """v3.53 \u2014 horizontal AUR vertical dono packing try, jo zyada area bhare wahi."""
    cands = []
    for vertical in (False, True):
        cp = [dict(p) for p in rem]
        place, used = _pack_axis(cp, sl, sw, vertical)
        cands.append((used, place))
    cands.sort(key=lambda c: -c[0])
    return cands[0][1], cands[0][0]


def _mix_pack_plan(f):
    """v3.51/v3.53 \u2014 MULTI PANEL \u2192 SHEET PLANNER: alag-alag size ke panels (manual) x
    alag-alag size ki sheets (manual). Har sheet: best of horizontal/vertical packing,
    bands + filler columns (mixed sizes ek hi sheet me) \u2014 maximum USE."""
    def _fl(x):
        try:
            return float(str(x).strip())
        except (TypeError, ValueError):
            return 0.0
    panels = []
    for _L, _W, _Q in zip(f.getlist("mp_len"), f.getlist("mp_wid"), f.getlist("mp_qty")):
        L, W, Q = _fl(_L), _fl(_W), int(_fl(_Q) or 0)
        if L > 0 and W > 0 and Q > 0:
            panels.append({"l": L, "w": W, "qty": min(Q, 100000)})
    sheets_in = []
    for _L, _W in zip(f.getlist("mp_sl"), f.getlist("mp_sw")):
        L, W = _fl(_L), _fl(_W)
        if L > 0 and W > 0:
            sheets_in.append((L, W))
    if not panels or not sheets_in:
        return None
    sheets_in = sheets_in[:8]
    panels = panels[:12]
    out = []
    for (sl, sw) in sheets_in:
        rem = [dict(p) for p in panels]
        layouts = []
        for _s in range(400):
            if all(p["qty"] <= 0 for p in rem):
                break
            rows, used = _pack_sheet_best(rem, sl, sw)
            if not rows:
                break
            for _r in rows:
                rem[_r["pi"]]["qty"] -= _r["n"]
            layouts.append({"rows": rows, "use": round(100 * used / (sl * sw), 1)})
        complete = all(p["qty"] <= 0 for p in rem)
        totals = [0] * len(panels)
        for ly in layouts:
            for r in ly["rows"]:
                totals[r["pi"]] += r["n"]
        # v3.54 \u2014 lagatar IDENTICAL layouts ek group (reference jaisa x12/x23 multiplier)
        groups = []
        for ly in layouts:
            _sig = tuple((r["pi"], round(r["x"], 1), round(r["y"], 1), round(r["bw"], 1), round(r["bh"], 1)) for r in ly["rows"])
            if groups and groups[-1]["sig"] == _sig:
                groups[-1]["times"] += 1
            else:
                groups.append({"sig": _sig, "rows": ly["rows"], "use": ly["use"], "times": 1})
        # v3.54b \u2014 MIXED layout pehle dikhe, phir high-use
        groups.sort(key=lambda g: (-len({r["pi"] for r in g["rows"]}), -g["use"]))
        first = [0] * len(panels)
        if groups:
            for r in groups[0]["rows"]:
                first[r["pi"]] += r["n"]
        _shown = groups[:6]
        out.append({
            "sl": sl, "sw": sw,
            "layouts": [{"rows": g["rows"], "use": g["use"], "times": g["times"],
                         "svg": _svg_mix_sheet(sl, sw, g["rows"], g["use"])} for g in _shown],
            "more": max(0, len(groups) - 6),
            "per_sheet": first, "totals": totals,
            "need": [p["qty"] for p in panels],
            "sheets_needed": len(layouts) if complete else None,
            "complete": complete,
        })
    return {"panels": panels, "sheets": out}


def _svg_mix_sheet(sl, sw, placements, use):
    """v3.55 \u2014 FULL-CUTLIST-style planner drawing: HAR PIECE box + #N centered numbering +
    dims (top width / rotated left height) + RED band cuts / BLUE piece cuts +
    sheet dims BAHAR (bottom + right rotated) + WASTE strips + USE+WASTE caption (reference jaisa)."""
    W, H, pad = 500, 448, 30
    scale = min((W - pad - 84) / sl, (H - pad - 64) / sw)
    S, T = sl * scale, sw * scale
    x0 = pad + (W - pad - 84 - S) / 2
    y0 = pad + (H - pad - 64 - T) / 2
    fills = [("#eef2ff", "#6366f1"), ("#fdf4ff", "#c026d3"), ("#d2e8e9", "#16324f"),
             ("#fef9c3", "#ca8a04"), ("#e4f2f3", "#2ba7b5"), ("#fce7f3", "#db2777")]
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']
    s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{S:.1f}" height="{T:.1f}" fill="#fffdf5" stroke="#0f172a" stroke-width="1.6"/>')
    maxW = max((p["x"] + p["bw"]) for p in placements)
    maxH = max((p["y"] + p["bh"]) for p in placements)
    # WASTE strips (right + bottom) — halka lal
    waste_x, waste_y = max(sl - maxW, 0.0), max(sw - maxH, 0.0)
    if waste_x > 0.05:
        s.append(f'<rect x="{x0 + maxW * scale:.1f}" y="{y0:.1f}" width="{waste_x * scale:.1f}" height="{T:.1f}" fill="#fee2e2" fill-opacity="0.8" stroke="#fca5a5" stroke-width="1" stroke-dasharray="3 2"/>')
    if waste_y > 0.05:
        s.append(f'<rect x="{x0:.1f}" y="{y0 + maxH * scale:.1f}" width="{S:.1f}" height="{waste_y * scale:.1f}" fill="#fee2e2" fill-opacity="0.8" stroke="#fca5a5" stroke-width="1" stroke-dasharray="3 2"/>')
        if waste_y * scale > 14 and S > 60:
            s.append(f'<text x="{x0 + S / 2:.1f}" y="{y0 + maxH * scale + waste_y * scale / 2 + 3:.1f}" text-anchor="middle" font-size="8.5" font-weight="700" fill="#dc2626" font-family="Segoe UI,Arial">WASTE {waste_y:.2f}</text>')
    # pieces (axis-sahi repeat)
    pieces = []
    for p in placements:
        fill, _st = fills[p["pi"] % len(fills)]
        if p.get("v"):
            pw_, ph_ = p["bw"], p["bh"] / p["n"]
        else:
            pw_, ph_ = p["bw"] / p["n"], p["bh"]
        for k in range(p["n"]):
            if p.get("v"):
                pieces.append((p["x"], p["y"] + k * ph_, pw_, ph_, fill, _st))
            else:
                pieces.append((p["x"] + k * pw_, p["y"], pw_, ph_, fill, _st))
    for (px, py, pw_, ph_, fill, _st) in pieces:
        s.append(f'<rect x="{x0 + px * scale:.1f}" y="{y0 + py * scale:.1f}" width="{pw_ * scale:.1f}" height="{ph_ * scale:.1f}" fill="{fill}" stroke="#94a3b8" stroke-width="0.8"/>')
    # cuts: RED = band separator (pura lamba), BLUE = piece divider
    for p in placements:
        pw_, ph_ = (p["bw"], p["bh"] / p["n"]) if p.get("v") else (p["bw"] / p["n"], p["bh"])
        for k in range(1, p["n"]):
            off = (p["y"] + k * ph_) if p.get("v") else (p["x"] + k * pw_)
            if p.get("v"):
                yy = y0 + off * scale
                s.append(f'<line x1="{x0 + p["x"] * scale:.1f}" y1="{yy:.1f}" x2="{x0 + (p["x"] + p["bw"]) * scale:.1f}" y2="{yy:.1f}" stroke="#2ba7b5" stroke-width="1.4"/>')
            else:
                xx = x0 + off * scale
                s.append(f'<line x1="{xx:.1f}" y1="{y0 + p["y"] * scale:.1f}" x2="{xx:.1f}" y2="{y0 + (p["y"] + p["bh"]) * scale:.1f}" stroke="#2ba7b5" stroke-width="1.4"/>')
        if p.get("v") and p["x"] > 0.01:
            xx = x0 + p["x"] * scale
            s.append(f'<line x1="{xx:.1f}" y1="{y0:.1f}" x2="{xx:.1f}" y2="{y0 + maxH * scale:.1f}" stroke="#dc2626" stroke-width="2"/>')
        elif not p.get("v") and p["y"] > 0.01:
            yy = y0 + p["y"] * scale
            s.append(f'<line x1="{x0:.1f}" y1="{yy:.1f}" x2="{x0 + maxW * scale:.1f}" y2="{yy:.1f}" stroke="#dc2626" stroke-width="2"/>')
    # labels: top width + rotated left height + #N centered (reference jaisa)
    num = 1
    for (px, py, pw_, ph_, fill, _st) in pieces:
        P, Q = pw_ * scale, ph_ * scale
        cx, cy = x0 + (px + pw_ / 2) * scale, y0 + (py + ph_ / 2) * scale
        if P > 26 and Q > 13:
            s.append(f'<text x="{cx:.1f}" y="{y0 + py * scale + 9.5:.1f}" text-anchor="middle" font-size="8.5" fill="#1e293b" font-family="Segoe UI,Arial">{pw_:g}</text>')
        if Q > 30 and P > 13:
            s.append(f'<text x="{x0 + px * scale + 6.5:.1f}" y="{cy:.1f}" transform="rotate(-90 {x0 + px * scale + 6.5:.1f} {cy:.1f})" text-anchor="middle" font-size="8.5" fill="#1e293b" font-family="Segoe UI,Arial">{ph_:g}</text>')
        if P > 20 and Q > 20:
            s.append(f'<text x="{cx:.1f}" y="{cy + 3.5:.1f}" text-anchor="middle" font-size="10.5" font-weight="800" fill="{_st}" font-family="Segoe UI,Arial">#{num}</text>')
        elif P > 12 and Q > 12:
            s.append(f'<text x="{x0 + px * scale + 3.5:.1f}" y="{y0 + (py + ph_) * scale - 3.5:.1f}" font-size="7.5" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">{num}</text>')
        num += 1
    # WASTE text right strip me (rotated, red)
    if waste_x > 0.05 and waste_x * scale > 14 and T > 60:
        _rx, _ry = x0 + maxW * scale + waste_x * scale / 2, y0 + T / 2
        s.append(f'<text x="{_rx:.1f}" y="{_ry:.1f}" transform="rotate(-90 {_rx:.1f} {_ry:.1f})" text-anchor="middle" font-size="9" font-weight="700" fill="#dc2626" font-family="Segoe UI,Arial">WASTE {waste_x:.2f}</text>')
    # sheet dims BAHAR (bottom + right rotated) — reference jaisa
    s.append(f'<text x="{x0 + S / 2:.1f}" y="{y0 + T + 16:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">{sl:.2f} mm</text>')
    s.append(f'<text x="{x0 + S + 14:.1f}" y="{y0 + T / 2:.1f}" transform="rotate(90 {x0 + S + 14:.1f} {y0 + T / 2:.1f})" text-anchor="middle" font-size="10.5" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">{sw:.2f} mm</text>')
    # captions: sheet info + USE + WASTE breakdown (image-2 jaisa)
    s.append(f'<text x="{W / 2:.0f}" y="{H - 30:.0f}" text-anchor="middle" font-size="11" fill="#8a8f98" font-family="Segoe UI,Arial">SHEET {sl:g}\u00d7{sw:g} mm \u00b7 {len(pieces)} pieces \u00b7 USE {use}%</text>')
    s.append(f'<text x="{W / 2:.0f}" y="{H - 12:.0f}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">USE {maxW:.2f} + <tspan fill="#dc2626">WASTE {waste_x:.2f}</tspan> = {sl:.2f} mm</text>')
    s.append('</svg>')
    return "".join(s)


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
            cl, cw = r["cutting_len"] * scale, r["cutting_w"] * scale
            for i in range(r["per_normal"]):
                _svg_gang(s, x0 + i * cl, y, cl, cw, gx, gy, kx, ky, "#d2e8e9", "#16324f", 1.4)
            y += cw
        for _row in range(r["mixed_m"]):
            cl2p, cw2p = r["cutting_w"] * scale, r["cutting_len"] * scale
            for i in range(r["per_rot"]):
                _svg_gang(s, x0 + i * cl2p, y, cl2p, cw2p, gx, gy, kx, ky, "#e4f2f3", "#2ba7b5", 1.4)
            y += cw2p
        cap = (f"Sheet {sl:.2f}\u00d7{sw:.2f} mm \u00b7 {r['mixed_n']}\u00d7 row of {r['per_normal']} + "
               f"{r['mixed_m']}\u00d7 row of {r['per_rot']} = {r['panels_per_sheet']} panels \u00b7 "
               f"{r['pcs_per_sheet']} PCS \u00b7 {r['wastage']}% waste")
    else:
        cl, cw = r["cell_len"] * scale, r["cell_w"] * scale
        for i in range(r["grid_x"]):
            for j in range(r["grid_y"]):
                cx, cy = x0 + i * cl, y0 + j * cw
                _svg_gang(s, cx, cy, cl, cw, gx, gy, kx, ky, "#d2e8e9", "#16324f", 1.4)
        gang_cap = ""
        if gx > 1 or gy > 1:
            gang_cap = (f"{r['grid_x']}\u00d7{r['grid_y']} GANG \u00d7 {r['pcs_unit']} PCS = "
                        f"{r['pcs_per_sheet']} PCS \u00b7 ")
        cap = (f"Sheet {sl:.2f}\u00d7{sw:.2f} mm \u00b7 {gang_cap}"
               f"{r['grid_x']}\u00d7{r['grid_y']} = {r['panels_per_sheet']} panels \u00b7 "
               f"{r['pcs_per_sheet']} PCS \u00b7 {r['wastage']}% waste")
    s.append(f'<text x="{W/2:.0f}" y="{H - 6:.0f}" text-anchor="middle" font-size="12.5" fill="#8a8f98" font-family="Segoe UI,Arial">{cap}</text>')
    s.append('</svg>')
    return "".join(s)


def _svg_dim_w(s, cx, cy, mm):
    """Panel ke top edge par width label (white halo ke saath readable)."""
    s.append(f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" font-size="8" fill="#475569" '
             f'font-family="Segoe UI,Arial" style="paint-order:stroke" stroke="#ffffff" stroke-width="2.5">{mm:g}</text>')


def _svg_dim_h(s, cx, cy, mm):
    """Panel ke left edge par rotated height label."""
    s.append(f'<text x="{cx:.1f}" y="{cy:.1f}" transform="rotate(-90 {cx:.1f} {cy:.1f})" text-anchor="middle" '
             f'font-size="8" fill="#475569" font-family="Segoe UI,Arial" style="paint-order:stroke" '
             f'stroke="#ffffff" stroke-width="2.5">{mm:g}</text>')


def _pcb_shape_svg(x, y, w, h, shape):
    """v3.20 — PCB drawing by shape: round -> ellipse, square/rect -> rect."""
    if shape == "round":
        return (f'<ellipse cx="{x + w / 2:.1f}" cy="{y + h / 2:.1f}" rx="{w / 2:.1f}" ry="{h / 2:.1f}" '
                f'fill="#d2e8e9" stroke="#16324f" stroke-width="1"/>')
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="#d2e8e9" stroke="#16324f" stroke-width="1"/>')


def _pcb_dim_text(s, cx, cy, w, h, dl, dw):
    """v2.93 — PCB dims edge-style (sheet-layout jaisa): TOP = LENGTH, LEFT = WIDTH (rotated)."""
    lt, lw_ = f"{dl:g}", f"{dw:g}"
    fs = min(8.0, h * 0.3, w * 0.18)
    if fs < 5.5:
        return
    base = (f'text-anchor="middle" font-size="{fs:.1f}" font-weight="700" fill="#16324f" '
            f'font-family="Segoe UI,Arial" style="paint-order:stroke" stroke="#ffffff" stroke-width="1.6"')
    if len(lt) * fs * 0.6 <= w - 6 and h >= 13:
        s.append(f'<text x="{cx:.1f}" y="{cy - h / 2 + fs + 2:.1f}" {base}>{lt}</text>')
    if len(lw_) * fs * 0.6 <= h - 6 and w >= 12:
        lx = cx - w / 2 + fs * 0.7
        s.append(f'<text x="{lx:.1f}" y="{cy:.1f}" transform="rotate(-90 {lx:.1f} {cy:.1f})" {base}>{lw_}</text>')

def _svg_sheet_dims(s, x0, y0, S, T, sl, sw, qty):
    """Sheet ke dimensions — neeche length (tick line), right par rotated width, qty ×N."""
    c = "#94a3b8"
    yd = y0 + T + 11
    s.append(f'<line x1="{x0:.1f}" y1="{yd:.1f}" x2="{x0 + S:.1f}" y2="{yd:.1f}" stroke="{c}" stroke-width="1"/>')
    for tx in (x0, x0 + S):
        s.append(f'<line x1="{tx:.1f}" y1="{yd - 4:.1f}" x2="{tx:.1f}" y2="{yd + 4:.1f}" stroke="{c}" stroke-width="1"/>')
    s.append(f'<text x="{x0 + S / 2:.1f}" y="{yd + 13:.1f}" text-anchor="middle" font-size="10.5" font-weight="700" '
             f'fill="#475569" font-family="Segoe UI,Arial">{sl:g} mm</text>')
    xd = x0 + S + 11
    s.append(f'<line x1="{xd:.1f}" y1="{y0:.1f}" x2="{xd:.1f}" y2="{y0 + T:.1f}" stroke="{c}" stroke-width="1"/>')
    for ty in (y0, y0 + T):
        s.append(f'<line x1="{xd - 4:.1f}" y1="{ty:.1f}" x2="{xd + 4:.1f}" y2="{ty:.1f}" stroke="{c}" stroke-width="1"/>')
    txx, tyy = xd + 12, y0 + T / 2
    s.append(f'<text x="{txx:.1f}" y="{tyy:.1f}" transform="rotate(90 {txx:.1f} {tyy:.1f})" text-anchor="middle" '
             f'font-size="10.5" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">{sw:g} mm</text>')
    if qty and qty > 1:
        s.append(f'<text x="{x0 + S + 4:.1f}" y="{yd + 13:.1f}" text-anchor="end" font-size="10" font-weight="800" '
                 f'fill="#94a3b8" font-family="Segoe UI,Arial">×{qty:g}</text>')


def svg_gang_panel_preview(r):
    """PREVIEW #2 — GANG PANEL: EK gang unit zoom — multiplier panels, har panel me PCBs,
    per-joint gap labels, total cutting size (gang_len x gang_w) dimension dims ke saath.
    Sheet fit alag preview hai (#3 PANEL/GANG PANEL IN SHEET)."""
    W, H, pad = 470, 412, 30
    gx, gy = r["gang_x"], r["gang_y"]
    pl, pw = r["panel_len"], r["panel_w"]
    kx, ky = r["kerf_x"], r["kerf_y"]
    pgx = r.get("pgaps_x") or []
    pgy = r.get("pgaps_y") or []
    gl = pl * gx + (sum(pgx) if pgx else kx * (gx - 1))   # v3.58 GANG PANEL = panels + joints (margin NAHI)
    gw = pw * gy + (sum(pgy) if pgy else ky * (gy - 1))   # v3.58
    scale = min((W - pad - 46) / gl, (H - pad - 44) / gw)
    S, T = gl * scale, gw * scale
    x0, y0 = pad + (W - pad - 46 - S) / 2, pad + (H - pad - 44 - T) / 2
    P, Q = pl * scale, pw * scale
    off_xs = pgx if pgx else [kx]
    off_ys = pgy if pgy else [ky]
    cw, ch = r["pcb_len"] * scale, r["pcb_w"] * scale
    gxs, gys = r["gap_x"] * scale, r["gap_y"] * scale
    bx, by = r["border_l"] * scale, r["border_t"] * scale
    gaps_x_s = r.get("gaps_x") or [r["gap_x"]] * max(0, r["pcbs_x"] - 1)
    gaps_y_s = r.get("gaps_y") or [r["gap_y"]] * max(0, r["pcbs_y"] - 1)
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']
    s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{S:.1f}" height="{T:.1f}" fill="#ede9fe" fill-opacity="0.55" stroke="#7c3aed" stroke-width="3.2" rx="4"/>')
    pcs_panel = r["pcs_panel"]
    for a in range(gx):
        for b in range(gy):
            _oxa = sum(P + off_xs[i] * scale for i in range(a))
            _oyb = sum(Q + off_ys[i] * scale for i in range(b))
            xp, yp = x0 + _oxa, y0 + _oyb
            s.append(f'<rect x="{xp:.1f}" y="{yp:.1f}" width="{P:.1f}" height="{Q:.1f}" fill="#e4f2f3" stroke="#2ba7b5" stroke-width="1.6" rx="2"/>')
            if P > 26 and Q > 15:
                _svg_dim_w(s, xp + P / 2, yp + 8.5, pl)
            if Q > 26 and P > 15:
                _svg_dim_h(s, xp + 7.5, yp + Q / 2, pw)
            if cw > 2.4 and ch > 2.4:
                ox = bx
                for i in range(r["pcbs_x"]):
                    oy = by
                    for j in range(r["pcbs_y"]):
                        s.append(_pcb_shape_svg(xp + ox, yp + oy, cw, ch, r.get("shape") or "rect"))
                        _pcb_dim_text(s, xp + ox + cw / 2, yp + oy + ch / 2, cw, ch, r["pcb_len"], r["pcb_w"])   # v2.91
                        oy += ch + (gaps_y_s[j] * scale if j < len(gaps_y_s) else gys)
                    if i < r["pcbs_x"] - 1:
                        ox += cw + (gaps_x_s[i] * scale if i < len(gaps_x_s) else gxs)
                if r["border_l"] or r["border_r"] or r["border_t"] or r["border_b"]:
                    cw2 = r["pcbs_x"] * cw + sum(gaps_x_s) * scale
                    ch2 = r["pcbs_y"] * ch + sum(gaps_y_s) * scale
                    s.append(f'<rect x="{xp + bx:.1f}" y="{yp + by:.1f}" width="{cw2:.1f}" height="{ch2:.1f}" fill="none" stroke="#64748b" stroke-width="1" stroke-dasharray="4 3"/>')
    # per-joint gap labels (joint ke beech, white halo ke saath)
    for a in range(gx - 1):
        if P > 40:
            jx = x0 + sum(P + off_xs[i] * scale for i in range(a + 1)) - off_xs[a] * scale / 2
            s.append(f'<text x="{jx:.1f}" y="{y0 + T / 2:.1f}" text-anchor="middle" font-size="8" fill="#5b7484" font-family="Segoe UI,Arial" style="paint-order:stroke" stroke="#ffffff" stroke-width="2.5">{off_xs[a]:g}</text>')
    for b in range(gy - 1):
        if Q > 40:
            jy = y0 + sum(Q + off_ys[i] * scale for i in range(b + 1)) - off_ys[b] * scale / 2
            s.append(f'<text x="{x0 + S / 2:.1f}" y="{jy + 3:.1f}" text-anchor="middle" font-size="8" fill="#5b7484" font-family="Segoe UI,Arial" style="paint-order:stroke" stroke="#ffffff" stroke-width="2.5">{off_ys[b]:g}</text>')
    _svg_sheet_dims(s, x0, y0, S, T, gl, gw, 0)
    pcs_gang = r["pcs_unit"]
    cap = (f'GANG PANEL {gx}\u00d7{gy} \u2192 {gl:.2f}\u00d7{gw:.2f} mm \u00b7 {gx * gy} PANELS \u00d7 {pcs_panel} PCS = '
           f'<tspan fill="#7c3aed" font-weight="700">{pcs_gang} PCS</tspan>')
    s.append(f'<text x="{W/2:.0f}" y="{H - 6:.0f}" text-anchor="middle" font-size="12" fill="#8a8f98" font-family="Segoe UI,Arial">{cap}</text>')
    s.append('</svg>')
    return "".join(s)


def svg_sheet_layout_preview(r):
    """SHEET LAYOUT PREVIEW — poori sheet me cutting panels fit, NUMBERED + DIMENSION labels:
    har panel par width (top) aur height (left rotated), sheet length neeche, width right,
    qty ×N — cutting optimization software jaisa."""
    W, H, pad = 500, 448, 30
    sl, sw = r["sheet_len"], r["sheet_w"]
    scale = min((W - pad - 84) / sl, (H - pad - 64) / sw)
    S, T = sl * scale, sw * scale
    x0, y0 = pad + (W - pad - 84 - S) / 2, pad + (H - pad - 64 - T) / 2
    gx, gy = r["gang_x"], r["gang_y"]
    kx, ky = r["kerf_x"] * scale, r["kerf_y"] * scale
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">']
    s.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{S:.1f}" height="{T:.1f}" fill="#fffdf5" stroke="#b3ac99" stroke-width="2"/>')
    # v2.62 \u2014 axis-wise USE + WASTE breakdown (red strips + USE/WASTE labels)
    kx_m, ky_m = r["kerf_x"], r["kerf_y"]
    if r["best"] == "mixed":
        _un = r["per_normal"] * r["cutting_len"] if r["per_normal"] and r["mixed_n"] else 0
        _ur = r["per_rot"] * r["cutting_w"] if r["per_rot"] and r["mixed_m"] else 0
        used_x = max(_un, _ur)
        _rows = r["mixed_n"] + r["mixed_m"]
        used_y = (r["mixed_n"] * r["cutting_w"] + r["mixed_m"] * r["cutting_len"]) if _rows else 0
    else:
        used_x = r["grid_x"] * r["cell_len"] if r["grid_x"] else 0
        used_y = r["grid_y"] * r["cell_w"] if r["grid_y"] else 0
    waste_x, waste_y = max(sl - used_x, 0.0), max(sw - used_y, 0.0)
    ux, uy = used_x * scale, used_y * scale
    if used_x > 0 and waste_x > 0.05:
        s.append(f'<rect x="{x0 + ux:.1f}" y="{y0:.1f}" width="{S - ux:.1f}" height="{T:.1f}" fill="#fee2e2" fill-opacity="0.75" stroke="#fca5a5" stroke-width="1" stroke-dasharray="3 2"/>')
        if S - ux > 20 and T > 60:
            _wx, _wy2 = x0 + ux + (S - ux) / 2, y0 + T / 2
            s.append(f'<text x="{_wx:.1f}" y="{_wy2:.1f}" transform="rotate(90 {_wx:.1f} {_wy2:.1f})" text-anchor="middle" font-size="8.5" font-weight="700" fill="#dc2626" font-family="Segoe UI,Arial">WASTE {waste_x:.2f}</text>')
    if used_y > 0 and waste_y > 0.05:
        s.append(f'<rect x="{x0:.1f}" y="{y0 + uy:.1f}" width="{S:.1f}" height="{T - uy:.1f}" fill="#fee2e2" fill-opacity="0.75" stroke="#fca5a5" stroke-width="1" stroke-dasharray="3 2"/>')
        if T - uy > 14 and S > 60:
            s.append(f'<text x="{x0 + S / 2:.1f}" y="{y0 + uy + (T - uy) / 2 + 3:.1f}" text-anchor="middle" font-size="8.5" font-weight="700" fill="#dc2626" font-family="Segoe UI,Arial">WASTE {waste_y:.2f}</text>')

    def _panel_cell(cx, cy, cl, cw, num, fill, stroke, mm_l, mm_w, rot=False):
        s.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cl:.1f}" height="{cw:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="2" rx="3"/>')
        _sh = r.get("shape") or "rect"
        _pl, _pw = (r["pcb_w"] * scale, r["pcb_len"] * scale) if rot else (r["pcb_len"] * scale, r["pcb_w"] * scale)
        _nx, _ny = (r["pcbs_y"], r["pcbs_x"]) if rot else (r["pcbs_x"], r["pcbs_y"])

        def _pcb_out(x, y, wd, ht):
            if _sh == "round":
                s.append(f'<ellipse cx="{x + wd / 2:.1f}" cy="{y + ht / 2:.1f}" rx="{wd / 2:.1f}" ry="{ht / 2:.1f}" '
                         f'fill="#d2e8e9" stroke="#16324f" stroke-width="0.6"/>')
            else:
                s.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{wd:.1f}" height="{ht:.1f}" '
                         f'fill="#d2e8e9" stroke="#16324f" stroke-width="0.6"/>')

        def _pcb_grid(x, y, wd, ht, _rot=False):
            """v3.21 — panel ke andar PCB grid (shape-aware) — sab previews me same shape."""
            __pl, __pw = (_pw, _pl) if _rot else (_pl, _pw)
            __nx, __ny = (_ny, _nx) if _rot else (_nx, _ny)
            if __pl < 4 or __pw < 4 or __nx < 1 or __ny < 1:
                return
            if __nx * __pl > wd + 1 or __ny * __pw > ht + 1:
                return
            mx = (wd - __nx * __pl) / max(1, __nx - 1)
            my = (ht - __ny * __pw) / max(1, __ny - 1)
            for a in range(__nx):
                for b in range(__ny):
                    _pcb_out(x + a * (__pl + mx), y + b * (__pw + my), __pl, __pw)

        if gx > 1 or gy > 1:
            p = (cl - 2 * kx - kx * (gx - 1)) / gx   # v2.87: cl me CNC margin included
            q = (cw - 2 * ky - ky * (gy - 1)) / gy
            for a in range(gx):
                for b in range(gy):
                    s.append(f'<rect x="{cx + a * (p + kx):.1f}" y="{cy + b * (q + ky):.1f}" width="{p:.1f}" height="{q:.1f}" fill="none" stroke="{stroke}" stroke-width="0.7" opacity="0.55"/>')
                    _pcb_grid(cx + a * (p + kx), cy + b * (q + ky), p, q)
        else:
            _pcb_grid(cx, cy, cl, cw)
        # v3.59 — dims label = GANG PANEL size (margin nahi) + PCS per gang panel
        if cl > 26 and cw > 15:
            _svg_dim_w(s, cx + cl / 2, cy + 8.5, mm_l)
        if cw > 26 and cl > 15:
            _svg_dim_h(s, cx + 7.5, cy + cw / 2, mm_w)
        if r.get("gang_active") and cl > 60 and cw > 30:
            # v3.61 — GANG PCB count + CUTTING PANEL SIZE (margin ke baad) + GANG size, naam ke saath
            s.append(f'<text x="{cx + cl / 2:.1f}" y="{cy + cw / 2 + 15:.1f}" text-anchor="middle" font-size="8.5" font-weight="700" fill="{stroke}" font-family="Segoe UI,Arial">{r["pcs_unit"]} PCB</text>')
            if cw > 62:
                s.append(f'<text x="{cx + cl / 2:.1f}" y="{cy + cw / 2 + 26:.1f}" text-anchor="middle" font-size="7.5" font-weight="700" fill="#5b7484" font-family="Segoe UI,Arial">CUT {cl / scale:g}\u00d7{cw / scale:g} mm</text>')
            if cw > 78:
                _gl1, _gl2 = (r["gang_w"], r["gang_len"]) if rot else (r["gang_len"], r["gang_w"])
                s.append(f'<text x="{cx + cl / 2:.1f}" y="{cy + cw / 2 + 37:.1f}" text-anchor="middle" font-size="7.5" font-weight="600" fill="#64748b" font-family="Segoe UI,Arial">GANG {_gl1:g}\u00d7{_gl2:g} mm</text>')
        if cl > 34 and cw > 22:
            s.append(f'<text x="{cx + cl / 2:.1f}" y="{cy + cw / 2 + 4:.1f}" text-anchor="middle" font-size="10" font-weight="800" fill="{stroke}" font-family="Segoe UI,Arial">#{num}</text>')
        elif cl > 22 and cw > 34:
            _nx, _ny = cx + cl / 2 + 3.5, cy + cw / 2
            s.append(f'<text x="{_nx:.1f}" y="{_ny:.1f}" transform="rotate(-90 {_nx:.1f} {_ny:.1f})" text-anchor="middle" font-size="10" font-weight="800" fill="{stroke}" font-family="Segoe UI,Arial">#{num}</text>')

    if r["best"] == "mixed":
        num = 1
        y = y0
        cl, cw = r["cutting_len"] * scale, r["cutting_w"] * scale
        for _row in range(r["mixed_n"]):
            for i in range(r["per_normal"]):
                _panel_cell(x0 + i * cl, y, cl, cw, num, "#eef2ff", "#6366f1", r["gang_len"], r["gang_w"])
                num += 1
            y += cw
        cl2, cw2 = r["cutting_w"] * scale, r["cutting_len"] * scale
        for _row in range(r["mixed_m"]):
            for i in range(r["per_rot"]):
                _panel_cell(x0 + i * cl2, y, cl2, cw2, num, "#fdf4ff", "#c026d3", r["gang_w"], r["gang_len"], rot=True)
                num += 1
            y += cw2
        if r["gang_active"]:
            cap = (f"1 SHEET {sl:.0f}x{sw:.0f} mm \u2190 GANG PANEL {r['gang_len']:g}x{r['gang_w']:g} mm \u00b7 {r['pcs_unit']} PCB \u2192 "
                   f"CUTTING PANEL {r['cutting_len']:g}x{r['cutting_w']:g} mm (CNC margin ke baad) = "
                   f"{r['panels_per_sheet']} CUTTING PANELS "
                   f"({r['mixed_n']}x row {r['per_normal']} + {r['mixed_m']}x row {r['per_rot']}) = {r['pcs_per_sheet']} PCB")
        else:
            cap = (f"1 SHEET {sl:.0f}x{sw:.0f} mm \u2190 PANEL {r['panel_len']:g}x{r['panel_w']:g} mm = "
                   f"{r['panels_per_sheet']} PANELS "
                   f"({r['mixed_n']}x row {r['per_normal']} + {r['mixed_m']}x row {r['per_rot']}) = {r['pcs_per_sheet']} PCS")
    else:
        cl, cw = r["cell_len"] * scale, r["cell_w"] * scale
        num = 1
        for i in range(r["grid_x"]):
            for j in range(r["grid_y"]):
                _panel_cell(x0 + i * cl, y0 + j * cw, cl, cw, num, "#eef2ff", "#6366f1",
                            r["gang_len"], r["gang_w"], rot=(r["best"] == "rotated"))
                num += 1
        if r["gang_active"]:
            cap = (f"1 SHEET {sl:.0f}x{sw:.0f} mm \u2190 GANG PANEL {r['gang_len']:g}x{r['gang_w']:g} mm \u00b7 {r['pcs_unit']} PCB \u2192 "
                   f"CUTTING PANEL {r['cutting_len']:g}x{r['cutting_w']:g} mm (CNC margin ke baad) = "
                   f"{r['grid_x']}x{r['grid_y']} = {r['panels_per_sheet']} CUTTING PANELS x {r['pcs_unit']} PCB = {r['pcs_per_sheet']} PCB")
        else:
            cap = (f"1 SHEET {sl:.0f}x{sw:.0f} mm \u2190 PANEL {r['cell_len']:g}x{r['cell_w']:g} mm = "
                   f"{r['grid_x']}x{r['grid_y']} = {r['panels_per_sheet']} PANELS x {r['pcs_unit']} PCS = {r['pcs_per_sheet']} PCS")
    _svg_sheet_dims(s, x0, y0, S, T, sl, sw, r.get("sheets") or 0)
    # v2.62 \u2014 USE + WASTE dim labels (bottom = length axis, right = width axis)
    if used_x > 0 and waste_x > 0.05:
        s.append(f'<text x="{x0 + S / 2:.1f}" y="{y0 + T + 40:.1f}" text-anchor="middle" font-size="9.5" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">USE {used_x:.2f} + <tspan fill="#dc2626">WASTE {waste_x:.2f}</tspan> = {sl:.2f} mm</text>')
    if used_y > 0 and waste_y > 0.05 and T > 120:
        _rx, _ry = x0 + S + 40, y0 + T / 2
        s.append(f'<text x="{_rx:.1f}" y="{_ry:.1f}" transform="rotate(90 {_rx:.1f} {_ry:.1f})" text-anchor="middle" font-size="10" font-weight="700" fill="#475569" font-family="Segoe UI,Arial">USE {used_y:.2f} \u00b7 <tspan fill="#dc2626">WASTE {waste_y:.2f}</tspan></text>')
    s.append(f'<text x="{W/2:.0f}" y="{H - 6:.0f}" text-anchor="middle" font-size="11" fill="#475569" font-family="Segoe UI,Arial">{cap}</text>')
    s.append('</svg>')
    return "".join(s)


def _gap_list(raw, n, default):
    """'0,2,0' jaisa comma string -> n floats (kam pade to default se pad, -1/blank = default)."""
    vals = []
    for x in str(raw or "").split(","):
        x = x.strip()
        if not x:
            continue
        try:
            v = float(x)
        except ValueError:
            continue
        vals.append(v if v >= 0 else default)
    while len(vals) < max(0, n):
        vals.append(default)
    return vals[:max(0, n)] if n > 0 else []


def gang_info_for(r):
    """GANG PANEL PREVIEW data (multiplier > 1 ho to) — cutting size,
    PCS per unit, sheet layout gang terms mein, PCS per sheet."""
    if not r or r["gang_x"] <= 1 and r["gang_y"] <= 1:
        return None
    gx, gy = r["gang_x"], r["gang_y"]
    pcs_gang = r["pcs_unit"]
    gl, gw = r["gang_len"], r["gang_w"]   # v3.58 — GANG PANEL size (margin nahi)
    if r["best"] == "mixed":
        gang_count = r["mixed_n"] * r["per_normal"] + r["mixed_m"] * r["per_rot"]
        lay = f'{r["mixed_n"]} row × {r["per_normal"]} + {r["mixed_m"]} row × {r["per_rot"]}'
    elif r["best"] == "rotated":
        gang_count = r["rx"] * r["ry"]
        lay = f'{r["rx"]} × {r["ry"]}'
    else:
        gang_count = r["grid_x"] * r["grid_y"]
        lay = f'{r["grid_x"]} × {r["grid_y"]}'
    pcs_sheet = r["pcs_per_sheet"]
    def _gstr(arr, dfl):
        if not arr:
            return f"{dfl:.2f} × uniform"
        if all(abs(v - arr[0]) < 1e-9 for v in arr):
            return f"{arr[0]:.2f} x {len(arr)}"
        return "[" + "+".join(f"{v:g}" for v in arr) + "]"
    cut_html = (f'✂ <b>GANG PANEL SIZE: {gl:.2f} × {gw:.2f}mm - PCS/Unit: {pcs_gang}</b>'
                f'<div class="muted small" style="margin-top:3px">'
                f'({r["panel_len"]:.2f} x {gx} + gaps {_gstr(r.get("pgaps_x"), r["kerf_x"])} = {gl:.2f}mm, '
                f'{r["panel_w"]:.2f} x {gy} + gaps {_gstr(r.get("pgaps_y"), r["kerf_y"])} = {gw:.2f}mm — CNC margin isme NAHI. '
                f'CUTTING PANEL SIZE: {r["cutting_len"]:.2f} × {r["cutting_w"]:.2f} mm (CNC margin ke baad))</div>')
    layout_html = (f'<b>{lay}</b>'
                   f'<div class="gsize">{gl:.2f} × {gw:.2f} mm</div>')
    note = (f'💡 Panel {r["panel_len"]:.2f}×{r["panel_w"]:.2f} mm ({r["pcs_panel"]} PCS) × {gx}×{gy} '
            f'multiplier + joints = GANG PANEL {gl:.2f}×{gw:.2f} mm ({pcs_gang} PCS/unit) — CNC margin isme NAHI. '
            f'PANEL IN SHEET me CUTTING PANEL SIZE {r["cutting_len"]:.2f}×{r["cutting_w"]:.2f} mm (GANG + {r["kerf_x"]:.2f}/{r["kerf_y"]:.2f} mm margin) fit hoti hai — '
            f'Sheet {r["sheet_len"]:.2f}×{r["sheet_w"]:.2f} mm me <b>{lay.lower()}</b>: {gang_count} × {pcs_gang} PCS = '
            f'<b>{pcs_sheet} PCS per sheet</b>.')
    return {"gl": gl, "gw": gw, "pcs_gang": pcs_gang, "gx": gx, "gy": gy,
            "pcs_panel": r["pcs_panel"],
            "layout": layout_html, "pcs_sheet": pcs_sheet, "note": note,
            "cut_html": cut_html}


@app.route("/pcbcalc", methods=["GET", "POST"])
@login_required
def pcbcalc():
    """💰 PCB COST CALCULATOR — panel/PCB size + PCS + RATE ya PRICE (dono direction):
    RATE daalo → PRICE nikle, PRICE daalo → RATE nikle. Har calculation HISTORY me save."""
    # v3.69 — history me MODEL bhi (self-heal column — POST se bhi PEHLE)
    _phc = [r["name"] for r in db.query("PRAGMA table_info(pcb_calc_history)")]
    if "model_name" not in _phc:
        db.execute("ALTER TABLE pcb_calc_history ADD COLUMN model_name TEXT DEFAULT ''")
    if request.method == "POST":
        f = request.form
        if f.get("action") == "delete_history" and (f.get("id") or "").isdigit():
            db.execute("DELETE FROM pcb_calc_history WHERE id=?", (int(f.get("id")),))
            flash("History entry delete ho gayi.", "success")
            return redirect_with_token(url_for("pcbcalc"))
        if f.get("action") == "clear_history":
            db.execute("DELETE FROM pcb_calc_history")
            flash("Poori history clear ho gayi.", "success")
            return redirect_with_token(url_for("pcbcalc"))
        if f.get("action") == "save":
            L = _fl(f.get("input_len"))
            W = _fl(f.get("input_w"))
            pcs = int(_fl(f.get("pcs"))) if _fl(f.get("pcs")) > 0 else 0
            rate_in = _fl(f.get("rate"))
            price_in = _fl(f.get("price"))
            party = (f.get("party") or "").strip()
            mode = "panel" if pcs > 0 else "pcb"
            if L <= 0 or W <= 0 or (rate_in <= 0 and price_in <= 0):
                flash("SIZE (L×W) aur RATE ya PRICE me se koi ek value daalo.", "error")
                return redirect_with_token(url_for("pcbcalc"))
            sq_panel = (L / 25.4) * (W / 25.4)
            area_pcb = (sq_panel / pcs) if pcs > 0 else sq_panel
            if area_pcb <= 0:
                flash("Area calculate nahi hua — size check karo.", "error")
                return redirect_with_token(url_for("pcbcalc"))
            if rate_in > 0:      # RATE daala → PRICE nikla
                direction, rate, price = "rate", round(rate_in, 3), round(area_pcb * rate_in, 2)
            else:                # PRICE daala → RATE nikla
                direction, rate, price = "price", round(price_in / area_pcb, 3), round(price_in, 2)
            _hm = ""
            if (f.get("model_id") or "").strip().isdigit():
                _mrow = db.query("SELECT name FROM product_models WHERE id=?", (int(f["model_id"]),), one=True)
                _hm = (_mrow["name"] if _mrow else "")
            db.execute("INSERT INTO pcb_calc_history (mode, party, input_len, input_w, pcs, direction, "
                       "rate, price, area, created_on, model_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (mode, party, L, W, pcs, direction, rate, price, round(area_pcb, 4), _now_dt(), _hm))
            flash("Calculation history me save ho gayi ✅", "success")
            return redirect_with_token(url_for("pcbcalc"))
    models = db.query("SELECT id, name, model_code, pcb_len, pcb_w, cutting_len, cutting_w, "
                      "panel_len, panel_w, pcs_panel, per_sq_inch, gang_x, gang_y FROM product_models ORDER BY name")
    history = db.query("SELECT * FROM pcb_calc_history ORDER BY id DESC LIMIT 100")
    # v3.64 — MODEL select karne par uska attachment (drawing/PDF) next tab me
    _pdf_ensure()
    _pcc_pdfs = {}
    _mids = [m["id"] for m in models]
    if _mids:
        _q = ",".join("?" * len(_mids))
        for _r in db.query(f"SELECT id, model_id, filename FROM model_pdfs WHERE model_id IN ({_q}) ORDER BY id DESC", _mids):
            _pcc_pdfs.setdefault(str(_r["model_id"]), []).append(
                {"id": _r["id"], "filename": _r["filename"], "url": f"/model-pdf/{_r['id']}/view"})
    # v3.66 — FP 📎 attachment sabse pehle (wahi model ka doc hai)
    for _a in db.query("SELECT id AS pid, attachment_name FROM product_models "
                       "WHERE IFNULL(attachment_data,'') != ''"):
        _pcc_pdfs.setdefault(str(_a["pid"]), []).insert(
            0, {"filename": _a["attachment_name"] or "attachment", "url": f"/products/{_a['pid']}/attachment"})
    return render_template("pcbcalc.html", active="pcbcalc", models=models, history=history, model_pdfs_map=_pcc_pdfs)


@app.route("/cutlist", methods=["GET", "POST"])
@login_required
def cutlist():
    fields = {}
    load_model_name = ""
    load_model_id = request.args.get("model") or (request.values.get("model_id") if request.method == "POST" else None)

    mix_result = None
    mix_inputs = None
    if request.method == "POST":
        f = request.form
        action = f.get("action", "calculate")
        if action == "mixplan":
            mix_result = _mix_pack_plan(f)
            # v3.57 — LAYOUT NIKALO ke baad planner inputs wapas bhare rehne chahiye
            mix_inputs = {k: f.getlist(k) for k in ("mp_len", "mp_wid", "mp_qty", "mp_sl", "mp_sw")}
        if action == "load" and f.get("model_id"):
            return redirect_with_token(url_for("cutlist", model=f.get("model_id")))
        fields = {k: f.get(k, "") for k in FIELD_KEYS}
        # v3.11 — SHEET POOL USE: pool wali sheet size fields me daal ke normal calculate
        if action == "pool_use":
            try:
                _pid = int(f.get("pool_id", 0) or 0)
            except ValueError:
                _pid = 0
            _pr = db.query("SELECT * FROM sheet_pool WHERE id=?", (_pid,), one=True) if _pid else None
            if _pr:
                fields["sheet_len"] = format(_pr["sheet_len"] or 0, "g")
                fields["sheet_w"] = format(_pr["sheet_w"] or 0, "g")
                flash("Sheet " + format(_pr["sheet_len"] or 0, "g") + "\u00d7" + format(_pr["sheet_w"] or 0, "g") +
                      " USE ho gayi — layout isi sheet par recalculate hua.", "success")
            action = "calculate"
        result = compute_layout(fields)

        if action == "save_model" and result:
            # PCB PRICE + PER SQ.INCH — AREA = PANEL (cutting) size ÷ PCS/panel (PCB size nahi)
            pm_price = pm_rs = None
            cl_price = (f.get("pcb_price") or "").strip()
            cl_rate = (f.get("per_sq_inch") or "").strip()
            cl_x = result["cutting_len"]
            cl_y = result["cutting_w"]
            pcs_unit = result["pcs_unit"] or result["pcs_panel"]
            sq_in = (cl_x * cl_y) / (25.4 * 25.4) / pcs_unit if cl_x > 0 and cl_y > 0 and pcs_unit > 0 else 0.0
            if cl_price:
                try:
                    pm_price = round(float(cl_price), 2)
                    pm_rs = round(pm_price / sq_in, 3) if sq_in > 0 else None
                except ValueError:
                    pm_price = None
            if cl_rate and pm_price is None:
                try:
                    pm_rs = round(float(cl_rate), 3)
                    pm_price = round(pm_rs * sq_in, 2) if sq_in > 0 else None
                except ValueError:
                    pm_rs = None
            name = f.get("save_name", "").strip()
            sel = f.get("save_select", "").strip()
            # v3.12 — PARTY NAME naya hai to PARTIES me automatic add
            _pn12 = (f.get("party_name") or "").strip()
            if _pn12 and ensure_party(_pn12):
                flash("👤 Party '" + _pn12 + "' PARTIES me bhi add ho gayi (auto).", "success")
            # v3.04 — MODEL NO fallback: purana/stale form ho jisme model_code field nahi,
            # to MODEL NAME ko hi MODEL NO bana do — Model No. kabhi khali na rahe
            _mc = (f.get("model_code") or "").strip()
            if not _mc:
                _mc = name
            if sel:
                try:
                    model = db.query("SELECT * FROM product_models WHERE id=?", (int(sel),), one=True)
                except ValueError:
                    model = None
                if model:
                    name = name or model["name"]
                    cutting_len = result["cutting_len"]
                    cutting_w = result["cutting_w"]
                    up_price = pm_price if pm_price is not None else (model["pcb_price"] or 0)
                    up_rs = pm_rs if pm_rs is not None else (model["per_sq_inch"] or 0)
                    db.execute(
                        "UPDATE product_models SET name=?, pcb_len=?, pcb_w=?, pcbs_x=?, pcbs_y=?, gap_x=?, gap_y=?, "
                        "border_l=?, border_r=?, border_t=?, border_b=?, gang_x=?, gang_y=?, sheet_len=?, sheet_w=?, "
                        "panel_len=?, panel_w=?, cutting_len=?, cutting_w=?, kerf_x=?, kerf_y=?, orientation=?, "
                        "pcs_panel=?, panels_sheet=?, sheets=?, x_qty=?, y_qty=?, cnc_margin_x=?, cnc_margin_y=?, "
                        "pcb_price=?, per_sq_inch=?, gaps_x=?, gaps_y=?, sheet_thickness=?, note=?, "
                        "pcb_code=COALESCE(NULLIF(?, ''), pcb_code), party_code=COALESCE(NULLIF(?, ''), party_code), "
                        "party_name=COALESCE(NULLIF(?, ''), party_name), "
                        "model_code=COALESCE(NULLIF(?, ''), model_code), shape=? WHERE id=?",
                        (name, result["pcb_len"], result["pcb_w"], result["pcbs_x"], result["pcbs_y"],
                         result["gap_x"], result["gap_y"],
                         result["border_l"], result["border_r"], result["border_t"], result["border_b"],
                         result["gang_x"], result["gang_y"], result["sheet_len"], result["sheet_w"],
                         result["panel_len"], result["panel_w"], cutting_len, cutting_w,
                         result["kerf_x"], result["kerf_y"],
                         result["best"], result["pcs_panel"], result["panels_per_sheet"], result["sheets"],
                         result["grid_x"], result["grid_y"],
                         result["border_l"], result["border_t"],
                         up_price, up_rs,
                         ",".join(f"{g:g}" for g in result["gaps_x"]),
                         ",".join(f"{g:g}" for g in result["gaps_y"]),
                         (f.get("sheet_thickness") or "").strip(), (f.get("note_desc") or "").strip(),
                         (f.get("pcb_code") or "").strip(), (f.get("party_code") or "").strip(),
                         (f.get("party_name") or "").strip(),
                         (f.get("model_code") or "").strip(), result.get("shape") or "rect", model["id"]))
                    flash(f"Model '{name}' updated — saari cut list details + price save ho gayi.", "success")
                else:
                    flash("Select a valid finished product.", "error")
            else:
                # v3.02 — naya model HAMESHA save hoga: name/codes khali bhi ho to AUTO-NAME se save
                if not name:
                    name = "-".join(x for x in [(f.get("pcb_code") or "").strip(), (f.get("party_code") or "").strip(),
                                                (f.get("party_name") or "").strip(),
                                                (f.get("model_code") or "").strip()] if x)
                if not name:
                    name = "MODEL-" + _now_ist().strftime("%d%b-%H%M").upper()
                _bn, _nn = name, 2
                while db.query("SELECT id FROM product_models WHERE name=?", (name,), one=True):
                    name = _bn + "-" + str(_nn)
                    _nn += 1
                cutting_len = result["cutting_len"]
                cutting_w = result["cutting_w"]
                db.execute(
                    "INSERT INTO product_models (name, pcb_len, pcb_w, pcbs_x, pcbs_y, gap_x, gap_y, border_l, "
                    "border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, panel_len, panel_w, "
                    "cutting_len, cutting_w, kerf_x, kerf_y, orientation, pcs_panel, panels_sheet, sheets, "
                    "x_qty, y_qty, cnc_margin_x, cnc_margin_y, pcb_price, per_sq_inch, gaps_x, gaps_y, "
                    "sheet_thickness, note, pcb_code, party_code, party_name, model_code, shape, created_on) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, result["pcb_len"], result["pcb_w"], result["pcbs_x"], result["pcbs_y"],
                     result["gap_x"], result["gap_y"],
                     result["border_l"], result["border_r"], result["border_t"], result["border_b"],
                     result["gang_x"], result["gang_y"], result["sheet_len"], result["sheet_w"],
                     result["panel_len"], result["panel_w"], cutting_len, cutting_w,
                     result["kerf_x"], result["kerf_y"],
                     result["best"], result["pcs_panel"], result["panels_per_sheet"], result["sheets"],
                     result["grid_x"], result["grid_y"],
                     result["border_l"], result["border_t"],
                     pm_price or 0, pm_rs or 0,
                     ",".join(f"{g:g}" for g in result["gaps_x"]),
                     ",".join(f"{g:g}" for g in result["gaps_y"]),
                     (f.get("sheet_thickness") or "").strip(), (f.get("note_desc") or "").strip(),
                     (f.get("pcb_code") or "").strip(), (f.get("party_code") or "").strip(),
                     (f.get("party_name") or "").strip(), _mc,
                     result.get("shape") or "rect",
                     _today_ist().isoformat()))
                flash("Naya model '" + name + "' FINISHED PRODUCTS me save ho gaya — Products page par PCB/PARTY codes ke saath dikh raha hai.", "success")

        if action == "save_model" and not result:
            flash("Save nahi hua — layout invalid hai. Sheet L/W + PCB size bhar ke Calculate karo, phir Save dabao.", "error")

        if action == "pool_add":
            try:
                _pl = float(f.get("pool_len", 0) or 0)
                _pwd = float(f.get("pool_w", 0) or 0)
            except ValueError:
                _pl = _pwd = 0
            if _pl > 0 and _pwd > 0:
                db.execute("INSERT INTO sheet_pool (sheet_len, sheet_w, note, created_on) VALUES (?,?,?,?)",
                           (_pl, _pwd, (f.get("pool_note") or "").strip(), _today_ist().isoformat()))
                flash("Sheet " + format(_pl, "g") + "\u00d7" + format(_pwd, "g") +
                      " STOCK POOL me add ho gayi — ab compare me dikhegi.", "success")
            else:
                flash("Pool add: LENGTH aur WIDTH dono (0 se zyada) chahiye.", "error")
        if action == "pool_del":
            try:
                db.execute("DELETE FROM sheet_pool WHERE id=?", (int(f.get("pool_id", 0) or 0),))
                flash("Sheet size pool se hata di gayi.", "success")
            except ValueError:
                pass

        if action == "apply_order" and result:
            try:
                order_id = int(f.get("apply_order") or 0)
                order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
                if order:
                    unit_label = "PCS/unit" if result["gang_active"] else "PCS/panel"
                    info = (f"{result['pcs_unit']} {unit_label} \u00b7 {result['panels_per_sheet']} panels/sheet "
                            f"({result['sheet_len']:g}\u00d7{result['sheet_w']:g}) \u00b7 {result['best']}")
                    # v2.70 \u2014 BLANK GUARD: cut list me qty/panels 0 aa gaye to purani sahi values
                    # overwrite NAHI hoti (pehle apply ke baad order sab BLANK ho jata tha)
                    # v3.07 — Apply-to-Order se bane finished product me bhi MODEL NO jaye
                    _mc_ap = (f.get("model_code") or "").strip()
                    _qty = result["total_pcs"] if (result["total_pcs"] or 0) > 0 else (order["qty"] or 0)
                    _qpan = result["total_panels"] if (result["total_panels"] or 0) > 0 else (order["qty_panel"] or 0)
                    _qpcs = result["pcs_panel"] if (result["pcs_panel"] or 0) > 0 else (order["pcs_panel"] or 0)
                    db.execute("UPDATE orders SET cutlist_info=?, qty=?, product=?, qty_panel=?, pcs_panel=? WHERE id=?",
                               (info, _qty,
                                f"{order['product']} \u00b7 Panel {result['panel_len']:g}\u00d7{result['panel_w']:g}mm",
                                _qpan, _qpcs, order_id))
                    # JOB CARD bhi save karo — cut list ki saari details (sheet/panel/pcs/price) jobcard table me
                    cut_x = result["cutting_len"]
                    cut_y = result["cutting_w"]
                    # PCB PRICE — AREA = CUTTING PANEL ÷ PCS/unit (panel size se; PCB size nahi)
                    price = rs_pcb = None
                    cl_price = (f.get("pcb_price") or "").strip()
                    cl_rate = (f.get("per_sq_inch") or "").strip()
                    pcs_unit = result["pcs_unit"] or result["pcs_panel"]
                    sq_in = 0.0
                    if cut_x > 0 and cut_y > 0 and pcs_unit > 0:
                        sq_in = (cut_x / 25.4) * (cut_y / 25.4) / pcs_unit
                    if cl_price:
                        try:
                            price = round(float(cl_price), 2)
                            rs_pcb = round(price / sq_in, 3) if sq_in > 0 else None
                        except ValueError:
                            price = None
                    if cl_rate and price is None:
                        try:
                            rs_pcb = round(float(cl_rate), 3)
                            price = round(rs_pcb * sq_in, 2) if sq_in > 0 else None
                        except ValueError:
                            rs_pcb = None
                    db.execute("INSERT OR IGNORE INTO jobcard (order_id) VALUES (?)", (order_id,))
                    sets, vals = [], []
                    # v2.70 \u2014 sirf non-zero values likho (0 aaye to purani value rakho)
                    _jc_cur = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
                    for col, val in (("actual_pcb_x", result["pcb_len"]), ("actual_pcb_y", result["pcb_w"]),
                                     ("x_size", cut_x), ("x_qty", result["grid_x"]),
                                     ("y_size", cut_y), ("y_qty", result["grid_y"]),
                                     ("cnc_margin_x", result["border_l"]), ("cnc_margin_y", result["border_t"]),
                                     ("panel_x", cut_x), ("panel_y", cut_y),
                                     ("sheet_len", result["sheet_len"]), ("sheet_w", result["sheet_w"]),
                                     ("panels_per_sheet", result["panels_per_sheet"]), ("sheets", result["sheets"]),
                                     ("qty_panel", result["total_panels"]), ("pcs_panel", result["pcs_panel"])):
                        if (val is None or (isinstance(val, (int, float)) and val <= 0)) and _jc_cur is not None:
                            _prev = _jc_cur[col]
                            if _prev is not None and _prev != 0:
                                continue  # purani sahi value rakhi \u2014 blank nahi karenge
                        sets.append(f"{col}=?")
                        vals.append(val)
                    if price is not None:
                        sets.append("price=?")
                        vals.append(price)
                    if rs_pcb is not None:
                        sets.append("rs_pcb=?")
                        vals.append(rs_pcb)
                    vals.append(order_id)
                    db.execute(f"UPDATE jobcard SET {', '.join(sets)} WHERE order_id=?", tuple(vals))
                    # PRICE HISTORY: cut list se price set hua to record
                    if price is not None and price > 0:
                        base = (order["product"] or "").split(" · ")[0].strip()
                        _record_price(base, "", price, rs_pcb, order_id,
                                      order["order_no"], order["party"],
                                      _today_ist().isoformat(), _model_thickness(base))
                    # v2.74 \u2014 CUT LIST -> SEEDHA FINISHED PRODUCT: apply karte hi model
                    # finished products me bhi chala jata hai (dropdown kabhi blank nahi hoga)
                    _pname = (f.get("save_name") or "").strip()
                    if not _pname:
                        _jcm = db.query("SELECT party_model FROM jobcard WHERE order_id=?", (order_id,), one=True)
                        _pname = ((_jcm["party_model"] if _jcm else "") or "").strip()
                    if _pname and not db.query("SELECT id FROM product_models WHERE name=?", (_pname,), one=True):
                        _cx74 = result["cutting_len"]
                        _cy74 = result["cutting_w"]
                        db.execute(
                            "INSERT INTO product_models (name, pcb_len, pcb_w, pcbs_x, pcbs_y, gap_x, gap_y, border_l, "
                            "border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, panel_len, panel_w, "
                            "cutting_len, cutting_w, kerf_x, kerf_y, orientation, pcs_panel, panels_sheet, sheets, "
                            "x_qty, y_qty, cnc_margin_x, cnc_margin_y, pcb_price, per_sq_inch, gaps_x, gaps_y, "
                            "model_code, pcb_code, party_code, party_name, created_on) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (_pname, result["pcb_len"], result["pcb_w"], result["pcbs_x"], result["pcbs_y"],
                             result["gap_x"], result["gap_y"],
                             result["border_l"], result["border_r"], result["border_t"], result["border_b"],
                             result["gang_x"], result["gang_y"], result["sheet_len"], result["sheet_w"],
                             result["panel_len"], result["panel_w"], _cx74, _cy74,
                             result["kerf_x"], result["kerf_y"],
                             result["best"], result["pcs_panel"], result["panels_per_sheet"], result["sheets"],
                             result["grid_x"], result["grid_y"],
                             result["border_l"], result["border_t"],
                             price if price is not None else 0, rs_pcb if rs_pcb is not None else 0,
                             ",".join(f"{g:g}" for g in result["gaps_x"]),
                             ",".join(f"{g:g}" for g in result["gaps_y"]),
                             _mc_ap or _pname, (f.get("pcb_code") or "").strip(),
                             (f.get("party_code") or "").strip(), (f.get("party_name") or "").strip(),
                             _today_ist().isoformat()))
                        flash(f"Finished Product '{_pname}' bhi save ho gaya \u2014 ab FINISHED PRODUCT dropdown me milega.", "success")
                    _qtxt = f"Qty set to {result['total_pcs']} pcs" if (result["total_pcs"] or 0) > 0 \
                        else f"Qty purani rakhi ({order['qty'] or 0} pcs) \u2014 cut list me sheets 0 thi"
                    flash(f"Layout applied to {order['order_no']} ({order['party']}). "
                          f"{_qtxt} \u2014 Job Card update ho gaya "
                          f"(sheet, panels/sheet, qty panel, pcs/panel, price).", "success")
                else:
                    flash("Select a valid job order.", "error")
            except (ValueError, TypeError):
                flash("Select a valid job order.", "error")

        from urllib.parse import urlencode
        # v2.74 \u2014 apply ke baad CUT LIST se SEEDHA JOB ORDER (preview) par
        if action == "apply_order" and result and (f.get("apply_order") or "").strip().isdigit():
            return redirect_with_token(url_for("jobcard", order_id=int(f.get("apply_order"))))
        # v3.51 \u2014 mixplan redirect SKIP (tail render me mix_result dikhta hai)
        if action != "mixplan":
            params = urlencode({k: fields.get(k, "") for k in FIELD_KEYS})
            return redirect_with_token(url_for("cutlist") + "?" + params)

    # GET
    q = request.args
    if load_model_id:
        try:
            model = db.query("SELECT * FROM product_models WHERE id=?", (int(load_model_id),), one=True)
            if model:
                load_model_name = model["name"]   # v2.97: print preview me MODEL NAME
                _m96 = dict(model)
                for _c96 in ("pcb_code", "party_code", "party_name"):
                    fields[_c96] = _m96.get(_c96) or ""
                fields["note_desc"] = _m96.get("note") or ""   # v3.22 EXTRA DESCRIPTION wapas
                for k in FIELD_KEYS:
                    val = model[k] if k in model.keys() and model[k] is not None else ""
                    # 0 values ko KHAALI chhodo — '0.0' likha user ko blank hi dikhna chahiye
                    if val in (0, 0.0) or str(val) in ("0", "0.0"):
                        val = ""
                    fields[k] = str(val)
                has_pcb = (model["pcb_len"] or 0) > 0 and (model["pcb_w"] or 0) > 0
                fields["use"] = "1" if has_pcb else ""
                # PCBs grid khaali ho par PCS/Panel (UP) ho -> 1 row mein count set karo
                if (model["pcbs_x"] or 0) <= 0 and (model["pcbs_y"] or 0) <= 0 and (model["pcs_panel"] or 0) > 0:
                    fields["pcbs_x"] = str(model["pcs_panel"])
                    fields["pcbs_y"] = "1"
                # gang model: PANEL fields mein cutting size dikhao, single panel hidden base mein
                if (model["gang_x"] or 1) > 1 or (model["gang_y"] or 1) > 1:
                    cl = model["cutting_len"] or 0
                    cw = model["cutting_w"] or 0
                    if cl <= 0:
                        cl = (model["panel_len"] or 0) * (model["gang_x"] or 1) + \
                             (model["kerf_x"] or 0) * ((model["gang_x"] or 1) - 1) + 2 * (model["kerf_x"] or 0)  # v2.87
                    if cw <= 0:
                        cw = (model["panel_w"] or 0) * (model["gang_y"] or 1) + \
                             (model["kerf_y"] or 0) * ((model["gang_y"] or 1) - 1) + 2 * (model["kerf_y"] or 0)  # v2.87
                    fields["panel_len"] = f"{cl:g}"
                    fields["panel_w"] = f"{cw:g}"
                    fields["panel_base_len"] = f"{(model['panel_len'] or 0):g}"
                    fields["panel_base_w"] = f"{(model['panel_w'] or 0):g}"
                    fields["use"] = ""
                if has_pcb:
                    _xmsg = "price + borders + gaps + gang/cutting size ke saath"
                    if (model["pcb_price"] or 0) > 0:
                        _xmsg += f" — PCB PRICE ₹{model['pcb_price']:g} bhi load ho gaya"
                    flash(f"Loaded model '{model['name']}' — saari details load ho gayi ({_xmsg}).", "success")
                else:
                    if not fields.get("panel_len"):
                        fields["panel_len"] = f"{(model['panel_len'] or 0):g}"
                    if not fields.get("panel_w"):
                        fields["panel_w"] = f"{(model['panel_w'] or 0):g}"
                    if not fields.get("sheet_len") and (model["sheet_len"] or 0) > 0:
                        fields["sheet_len"] = f"{model['sheet_len']:g}"
                    if not fields.get("sheet_w") and (model["sheet_w"] or 0) > 0:
                        fields["sheet_w"] = f"{model['sheet_w']:g}"
                    if not fields.get("kerf_x"):
                        fields["kerf_x"] = "0"   # v2.97: CNC margin default 0
                    if not fields.get("kerf_y"):
                        fields["kerf_y"] = "0"
                    flash(f"Loaded model '{model['name']}' — panel/PCB details load ho gayi. PCB size khaali hai: {model['pcs_panel']} PCS/panel diya hai. Sheet size bharo, Calculate dabao.", "success")
        except (ValueError, TypeError):
            pass
    elif any(q.get(k, "") for k in FIELD_KEYS):
        fields = {k: q.get(k, "") for k in FIELD_KEYS}

    if fields:
        result = compute_layout(fields)  # fail ho to None — form mein wahi values dikhengi jo load hui
    else:
        # NAYA ENTRY = form BLANK khulega, SIRF SHEET SIZE default 1000x1200 (v3.22 user ask)
        fields = {"sheet_len": "1000", "sheet_w": "1200"}
        result = None

    # v3.11 — SHEET STOCK POOL compare: har pool sheet par layout compute, best-pehle sort
    pool_rows = []
    try:
        _oq11 = float(fields.get("order_qty", 0) or 0)
    except ValueError:
        _oq11 = 0
    try:
        _cur_l = float(fields.get("sheet_len", 0) or 0)
        _cur_w = float(fields.get("sheet_w", 0) or 0)
    except ValueError:
        _cur_l = _cur_w = 0
    for _pr in db.query("SELECT * FROM sheet_pool ORDER BY sheet_len, sheet_w"):
        p2 = dict(DEFAULTS)
        p2.update({k: v for k, v in fields.items() if v not in ("", None)})
        p2["sheet_len"] = str(_pr["sheet_len"] or 0)
        p2["sheet_w"] = str(_pr["sheet_w"] or 0)
        try:
            r2 = compute_layout(p2)
        except Exception:
            r2 = None
        if r2 and (r2["panels_per_sheet"] or 0) > 0 and (r2["pcs_per_sheet"] or 0) > 0:
            pool_rows.append({
                "id": _pr["id"], "sheet_len": _pr["sheet_len"] or 0, "sheet_w": _pr["sheet_w"] or 0,
                "note": _pr["note"] or "", "panels": r2["panels_per_sheet"],
                "pcs": r2["pcs_per_sheet"],
                "unit_label": ("PCS/unit" if r2["gang_active"] else "PCS/panel"),
                "waste": round(r2["wastage"] or 0, 1), "best": r2["best"],
                "sheets_needed": (r2["sheets_needed"] or 0) if _oq11 else 0,
                "is_current": (abs((_pr["sheet_len"] or 0) - _cur_l) < 0.01
                               and abs((_pr["sheet_w"] or 0) - _cur_w) < 0.01),
            })
    if pool_rows:
        _mx = max(r["pcs"] for r in pool_rows)
        for r in pool_rows:
            r["is_best"] = (r["pcs"] == _mx)
        pool_rows.sort(key=lambda r: (-r["pcs"], r["sheet_len"], r["sheet_w"]))

    models = db.query("SELECT * FROM product_models ORDER BY id DESC")
    orders = db.query("SELECT id, order_no, party, product FROM orders WHERE status!='done' ORDER BY id DESC")
    svg_panel = svg_panel_preview(result) if result else ""
    svg_sheet = svg_sheet_preview(result) if result else ""
    gang_info = gang_info_for(result)
    svg_gang_panel = svg_gang_panel_preview(result) if gang_info else ""
    svg_sheet_layout = svg_sheet_layout_preview(result) if result else ""
    # PANEL fields display: gang active -> cutting size; warna single panel
    disp_pl = disp_pw = None
    if result:
        if result["gang_active"]:
            disp_pl, disp_pw = result["cutting_len"], result["cutting_w"]
        else:
            disp_pl, disp_pw = result["panel_len"], result["panel_w"]
    _cl_pdfs = []
    try:
        _cl_mid = int(load_model_id or 0)
    except (TypeError, ValueError):
        _cl_mid = 0
    if _cl_mid:
        _pdf_ensure()
        _cl_pdfs = [dict(r) for r in db.query("SELECT id, model_name, filename, uploaded_on FROM model_pdfs "
                                              "WHERE model_id=? ORDER BY id DESC", (_cl_mid,))]
        # v3.66 — FP 📎 attachment bhi cutlist ke doc-area me
        _att_cl = db.query("SELECT id AS pid, name, attachment_name AS filename FROM product_models "
                           "WHERE id=? AND IFNULL(attachment_data,'') != ''", (_cl_mid,))
        if _att_cl:
            _cl_pdfs.insert(0, {"id": None, "pid": _att_cl[0]["pid"], "is_att": True,
                                "model_name": _att_cl[0]["name"],
                                "filename": _att_cl[0]["filename"] or "attachment", "uploaded_on": "FP 📎"})
    return render_template("cutlist.html", active="cutlist", fields=fields, pool_rows=pool_rows, result=result,
                           models=models, orders=orders, SHEET_PRESETS=SHEET_PRESETS,
                           svg_panel=svg_panel, svg_sheet=svg_sheet, gang_info=gang_info,
                           svg_gang_panel=svg_gang_panel, svg_sheet_layout=svg_sheet_layout,
                           disp_pl=disp_pl, disp_pw=disp_pw, load_model_name=load_model_name,
                           cl_pdfs=_cl_pdfs, mix_result=mix_result, mix_inputs=mix_inputs)


# ---------------------------------------------------------------- finished products
@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    if request.method == "POST":
        f = request.form
        # ---- THICKNESS RATE CARD: add / delete (product form se alag) ----
        if f.get("rate_action") == "add":
            _mat = (f.get("rate_material") or "").strip().upper()
            _thk = (f.get("rate_thickness") or "").strip().upper()
            try:
                _rate = float(f.get("rate_per_sq_inch") or 0)
            except ValueError:
                _rate = 0
            if _mat and _thk and _rate > 0:
                db.execute("INSERT INTO thickness_rates (material, thickness, per_sq_inch) VALUES (?,?,?) "
                           "ON CONFLICT(material, thickness) DO UPDATE SET per_sq_inch=excluded.per_sq_inch",
                           (_mat, _thk, _rate))
                flash(f"Rate card: {_mat} {_thk} → ₹{_rate:g}/sq.inch save ho gaya ✅", "success")
            else:
                flash("Rate add nahi hua — Material, Thickness aur Rate teeno chahiye.", "error")
            return redirect_with_token(url_for("products"))
        if f.get("rate_action") == "del":
            try:
                db.execute("DELETE FROM thickness_rates WHERE id=?", (int(f.get("rate_id") or 0),))
                flash("Rate card entry delete ho gayi.", "success")
            except ValueError:
                pass
            return redirect_with_token(url_for("products"))
        if f.get("rate_action") == "edit":
            try:
                _rid = int(f.get("rate_id") or 0)
                _nrate = float(f.get("rate_per_sq_inch") or 0)
                if _rid and _nrate > 0:
                    db.execute("UPDATE thickness_rates SET per_sq_inch=? WHERE id=?", (_nrate, _rid))
                    flash(f"Rate update ho gaya → ₹{_nrate:g}/sq.inch ✅", "success")
                else:
                    flash("Rate 0 se bada hona chahiye.", "error")
            except (ValueError, TypeError):
                flash("Rate update nahi hua.", "error")
            return redirect_with_token(url_for("products"))
        name = (f.get("name") or "").strip()
        if not name:
            flash("Product name zaroori hai.", "error")
            return redirect_with_token(url_for("products"))
        # GANG PANEL (cutting) size: khali ho to panel × gang + kerf se auto nikal lo
        _gang_x = max(1, int(f.get("gang_x", 1) or 1))
        _gang_y = max(1, int(f.get("gang_y", 1) or 1))
        _plen = float(f.get("panel_len", 0) or 0)
        _pwid = float(f.get("panel_w", 0) or 0)
        _kx = float(f.get("kerf_x", 2) or 2)
        _ky = float(f.get("kerf_y", 2) or 2)
        _clen = float(f.get("cutting_len", 0) or 0)
        _cwid = float(f.get("cutting_w", 0) or 0)
        if _gang_x > 1 or _gang_y > 1:
            if _clen <= 0 and _plen > 0:
                _clen = _plen * _gang_x + _kx * (_gang_x - 1) + 2 * _kx   # v2.87 + CNC MARGIN
            if _cwid <= 0 and _pwid > 0:
                _cwid = _pwid * _gang_y + _ky * (_gang_y - 1) + 2 * _ky   # v2.87 + CNC MARGIN
        # THICKNESS → RATE: per sq.inch khali hai aur thickness rate card me hai to auto price
        _thick = (f.get("sheet_thickness") or "").strip()
        _per_sq = float(f.get("per_sq_inch", 0) or 0)
        if _per_sq <= 0 and _thick:
            _tr = _rate_for_thickness(_thick)
            if _tr:
                _per_sq = _tr
        vals = (name, (f.get("model_code") or "").strip(),
                float(f.get("pcb_len", 0) or 0), float(f.get("pcb_w", 0) or 0),
                int(f.get("pcbs_x", 0) or 0), int(f.get("pcbs_y", 0) or 0),
                float(f.get("gap_x", 0) or 0), float(f.get("gap_y", 0) or 0),
                float(f.get("border_l", 0) or 0), float(f.get("border_r", 0) or 0),
                float(f.get("border_t", 0) or 0), float(f.get("border_b", 0) or 0),
                _gang_x, _gang_y,
                float(f.get("sheet_len", 0) or 0), float(f.get("sheet_w", 0) or 0),
                _plen, _pwid,
                _kx, _ky,
                (f.get("orientation") or "normal").strip(),
                int(f.get("pcs_panel", 0) or 0), int(f.get("panels_sheet", 0) or 0),
                int(f.get("sheets", 1) or 1),
                _clen, _cwid,
                int(f.get("x_qty", 0) or 0), int(f.get("y_qty", 0) or 0),
                float(f.get("cnc_margin_x", 0) or 0), float(f.get("cnc_margin_y", 0) or 0),
                float(f.get("pcb_price", 0) or 0), _per_sq,
                _thick,
                (f.get("pcb_code") or "").strip(), (f.get("party_code") or "").strip(),
                (f.get("party_name") or "").strip())
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
                    "pcs_panel=?, panels_sheet=?, sheets=?, cutting_len=?, cutting_w=?, x_qty=?, y_qty=?, "
                    "cnc_margin_x=?, cnc_margin_y=?, pcb_price=?, per_sq_inch=?, sheet_thickness=?, "
                    "pcb_code=COALESCE(NULLIF(?, \'\'), pcb_code), party_code=COALESCE(NULLIF(?, \'\'), party_code), "
                    "party_name=COALESCE(NULLIF(?, \'\'), party_name) WHERE id=?",
                    vals + (edit_id,))
                db.execute("UPDATE product_models SET hsn=?, shape=? WHERE id=?",
                           ((f.get("hsn") or "").strip() or "85340000", (f.get("shape") or "rect").strip(), edit_id))
                flash(f"Finished product '{name}' update ho gaya ✅ (cut list layout + price ke saath)", "success")
            else:
                dup = db.query("SELECT COUNT(*) c FROM product_models WHERE name=?", (name,), one=True)["c"]
                if dup:
                    flash(f"'{name}' pehle se hai — duplicate nahi ban sakta. Us row ke ✏️ Edit button se update karo.", "error")
                    return redirect_with_token(url_for("products"))
                db.execute(
                    "INSERT INTO product_models (name, model_code, pcb_len, pcb_w, pcbs_x, pcbs_y, gap_x, gap_y, "
                    "border_l, border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, panel_len, panel_w, "
                    "kerf_x, kerf_y, orientation, pcs_panel, panels_sheet, sheets, cutting_len, cutting_w, "
                    "x_qty, y_qty, cnc_margin_x, cnc_margin_y, pcb_price, per_sq_inch, sheet_thickness, "
                    "pcb_code, party_code, party_name, order_id, created_on) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    vals + (None, _today_ist().isoformat()))
                db.execute("UPDATE product_models SET hsn=?, shape=? WHERE id=?",
                           ((f.get("hsn") or "").strip() or "85340000", (f.get("shape") or "rect").strip(), db.query(
                               "SELECT id FROM product_models WHERE name=? ORDER BY id DESC LIMIT 1", (name,), one=True)["id"]))
                flash(f"Finished product '{name}' manually add ho gaya ✅ — BOM set karne ke liye 🧪 BOM button dabao.", "success")
        except Exception as e:
            flash(f"Save failed: {e}", "error")
        return redirect_with_token(url_for("products"))
    # v3.31 — SEARCH + FILTERS: q (model/MODEL NO/order/note), party-wise, date-wise
    flt_q = (request.args.get("q") or "").strip()
    flt_party = (request.args.get("party") or "").strip()
    flt_from = (request.args.get("from") or "").strip()
    flt_to = (request.args.get("to") or "").strip()
    _w, _prm = [], []
    # v4.32: EXCEL JAISI SHEETS — sheet_id par filter + tabs ka data
    _ensure_sheet31()
    db.execute("CREATE TABLE IF NOT EXISTS sheet_prefs (k TEXT PRIMARY KEY, v TEXT DEFAULT '')")
    _doc32 = {}
    _r32 = db.query("SELECT v FROM sheet_prefs WHERE k='align'", one=True)
    if _r32 and _r32["v"]:
        try:
            _doc32 = json.loads(_r32["v"])
        except Exception:
            _doc32 = {}
    if not isinstance(_doc32, dict):
        _doc32 = {}
    _sheets32 = _doc32.get("sheets") if isinstance(_doc32.get("sheets"), list) and _doc32.get("sheets") else [{"id": 1, "name": "SHEET 1"}]
    _sheets32 = [{"id": int(s["id"]), "name": str(s.get("name") or ("SHEET " + str(s["id"])))} for s in _sheets32]
    _cmap32 = {}
    for _rc in db.query("SELECT IFNULL(sheet_id,1) sid, COUNT(*) c FROM product_models GROUP BY sid"):
        _cmap32[int(_rc["sid"])] = _rc["c"]
    try:
        _sid32 = int(request.args.get("sheet") or 0)
    except (TypeError, ValueError):
        _sid32 = 0
    if _sid32 not in [s["id"] for s in _sheets32]:
        _sid32 = _sheets32[0]["id"]
    _w.append("IFNULL(m.sheet_id,1) = ?")
    _prm.append(_sid32)
    if flt_q:
        _lk = "%" + flt_q + "%"
        _w.append("(m.name LIKE ? OR IFNULL(m.model_code,'') LIKE ? OR IFNULL(o.order_no,'') LIKE ? "
                  "OR IFNULL(m.note,'') LIKE ? OR IFNULL(m.pcb_code,'') LIKE ?)")
        _prm += [_lk] * 5
    if flt_party:
        _w.append("IFNULL(m.party_name,'') = ?")
        _prm.append(flt_party)
    if flt_from:
        _w.append("substr(IFNULL(m.created_on,''),1,10) >= ?")
        _prm.append(flt_from)
    if flt_to:
        _w.append("substr(IFNULL(m.created_on,''),1,10) <= ?")
        _prm.append(flt_to)
    _sql = "SELECT m.*, o.order_no FROM product_models m LEFT JOIN orders o ON o.id=m.order_id"
    if _w:
        _sql += " WHERE " + " AND ".join(_w)
    _sql += " ORDER BY m.id DESC"
    rows = db.query(_sql, _prm)
    total_models = db.query("SELECT COUNT(*) AS n FROM product_models", one=True)["n"]
    # v4.16: SHEET par SAB models (2358+) — 400-cap hata. Asli problem page-size thi
    # (v4.09 me 3068 B/row -> 2358 rows = 9MB -> Vercel 502), wo slim-rows (CSS classes,
    # inline-style repeat band) se hal ki: ab ~0.9 KB/row -> @2358 page ~3.8MB = safe.
    # Upar wala Saved-Models table apne 400-cap par hi rahega (SHEET hi main list hai).
    models_total = len(rows)
    models_top = rows[:400]
    models = [dict(_r) for _r in rows]   # v4.27: dict — cut-calc attach hoga
    _ensure_cut_manual30()
    for _m in models:
        _c27 = None if _m.get("cut_manual") else _model_cut_calc(_m)   # v4.30: manual edit -> stored values hi
        _m["_c4a"] = _c27["cutting_len"] if _c27 else None
        _m["_cn4a"] = _c27["x_qty"] if _c27 else None
        _m["_c4b"] = _c27["cutting_w"] if _c27 else None
        _m["_cn4b"] = _c27["y_qty"] if _c27 else None
        _m["_cups"] = _c27["ups"] if _c27 else None
        _m["_cpps"] = _c27["pps"] if _c27 else None
        # v4.29: PRICE <-> PER SQ INCH — DEFAULT SINGLE PANEL (L x W inch / SINGLE-PANEL ke PCBs);
        # SINGLE PANEL nahi milta to CUTTING PANEL (4A x 4B inch / UPS) — user rule (SCPL-480 fix)
        if (_m.get("panel_len") or 0) > 0 and (_m.get("panel_w") or 0) > 0 and (int(_m.get("pcs_panel") or 0)) > 0:
            _sq28 = round(((float(_m["panel_len"]) / 25.4) * (float(_m["panel_w"]) / 25.4)) / float(_m["pcs_panel"]), 3)
        elif _c27 and _c27["ups"] and (_c27["cutting_len"] or 0) > 0 and (_c27["cutting_w"] or 0) > 0:
            _sq28 = round(((float(_c27["cutting_len"]) / 25.4) * (float(_c27["cutting_w"]) / 25.4)) / float(_c27["ups"]), 3)
        else:
            _sq28 = 0
        _m["_csq"] = _sq28
        _r28 = float(_m.get("per_sq_inch") or 0)
        _p28 = float(_m.get("pcb_price") or 0)
        _m["_cprice"] = round(_r28 * _sq28, 2) if (_sq28 and _r28 and not _p28) else None
        _m["_crate"] = round(_p28 / _sq28, 3) if (_sq28 and _p28 and not _r28) else None
    party_opts = [r["party_name"] for r in
                  db.query("SELECT DISTINCT party_name FROM product_models WHERE IFNULL(party_name,'')!='' ORDER BY party_name")]
    # v3.18 HSN sweep — koi bhi naya model (apply-order/import) bina HSN na rahe (PCB default 85340000)
    db.execute("UPDATE product_models SET hsn='85340000' WHERE hsn IS NULL OR hsn=''")
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
    # PRICE HISTORY: har model ka last price (table ke liye) + edit form me poori list
    _ph_ensure()
    lp_map = {}
    for lp in db.query("SELECT model_name, price, rs_pcb, sheet_thickness, ddate, party, order_no "
                       "FROM price_history ORDER BY id DESC"):
        if lp["model_name"] not in lp_map:
            lp_map[lp["model_name"]] = lp
    ph_rows = []
    if edit_model:
        if (edit_model["model_code"] or "").strip():
            ph_rows = db.query("SELECT * FROM price_history WHERE model_name=? OR model_code=? "
                               "ORDER BY id DESC LIMIT 15",
                               (edit_model["name"], edit_model["model_code"]))
        else:
            ph_rows = db.query("SELECT * FROM price_history WHERE model_name=? ORDER BY id DESC LIMIT 15",
                               (edit_model["name"],))
    _pdf_ensure()
    pdf_counts = {r["model_id"]: r["n"] for r in
                  db.query("SELECT model_id, COUNT(*) AS n FROM model_pdfs GROUP BY model_id")}
    _ib = request.args.get("import_batch")
    if _ib and not _import_preview(_ib):
        flash("\u26a0\ufe0f Import preview nahi mila (session expire/dua server par chala gaya) \u2014 file dobara upload karo.", "error")
    # v4.18/4.21: SHEET prefs (align + column-width + row-height) — DB se, har device par same
    _prefs21 = {}
    try:
        _pr21 = db.query("SELECT v FROM sheet_prefs WHERE k='align'", one=True)
        if _pr21 and _pr21["v"]:
            import json as _jp21
            _pv21 = _jp21.loads(_pr21["v"])
            _prefs21 = _pv21 if (isinstance(_pv21, dict) and "align" in _pv21) else {"align": _pv21 if isinstance(_pv21, dict) else {}}
    except Exception:
        _prefs21 = {}
    _align18 = _prefs21.get("align") or {}
    # v4.23: custom columns (registry + DB column self-heal)
    _cust23 = _prefs21.get("custom") if isinstance(_prefs21.get("custom"), list) else []
    try:
        _have23 = {r["name"] for r in db.query("PRAGMA table_info(product_models)")}
        for _c23 in _cust23:
            _cid23 = str(_c23.get("id", ""))
            if re.fullmatch(r"[a-f0-9]{4}", _cid23) and ("cc_" + _cid23) not in _have23:
                try:
                    db.execute(f"ALTER TABLE product_models ADD COLUMN cc_{_cid23} TEXT DEFAULT ''")
                except Exception:
                    pass
    except Exception:
        pass
    # v4.32: MERGE & CENTER — cc_ columns ke spans render ke liye
    _mgd32 = _doc32.get("colMerge") if isinstance(_doc32.get("colMerge"), dict) else {}
    for _m in models:
        _mg32 = _mgd32.get(str(_m["id"])) or {}
        _ccr32, _sk32 = [], 0
        for _cc in _cust23:
            if _sk32 > 0:
                _sk32 -= 1
                continue
            try:
                _sp32 = max(1, min(6, int(_mg32.get(_cc["id"]) or 1)))
            except (TypeError, ValueError):
                _sp32 = 1
            _ccr32.append({"id": _cc["id"], "span": _sp32})
            _sk32 = _sp32 - 1
        _m["_ccr"] = _ccr32
    return render_template("products.html", active="products", models=models, models_top=models_top, pdf_counts=pdf_counts,
                           sheets31=_sheets32, sid32=_sid32, scnt32=_cmap32,
                           models_total=models_total, align_prefs=_align18, prefs21=_prefs21, custom_cols=_cust23,
                           bom_map=bom_map, history=history,
                           show_add=request.args.get("add"),
                           edit_model=edit_model,
                           lp_map=lp_map, ph_rows=ph_rows,
                           rates=db.query("SELECT * FROM thickness_rates ORDER BY material, thickness"),
                           import_preview=_import_preview(request.args.get("import_batch")),
                           flt_q=flt_q, flt_party=flt_party, flt_from=flt_from, flt_to=flt_to,
                           party_opts=party_opts, total_models=total_models)


# ---------------------------------------------------------------- import (excel/csv)
# Google Sheet/Excel se finished products ek saath import karne ke liye.
# Column names flexible hain — alias matching se sahi field par map ho jaate hain.

IMPORT_FIELDS = [
    ("name", "Product Name", True),
    ("model_code", "Model No.", False),
    ("party", "Party", False),
    ("note", "CNC / Tool Note", False),
    ("pcb_len", "PCB Length (mm)", False),
    ("pcb_w", "PCB Width (mm)", False),
    ("pcbs_x", "PCBs in X", False),
    ("pcbs_y", "PCBs in Y", False),
    ("gap_x", "Gap X (PC to PC)", False),
    ("gap_y", "Gap Y (PC to PC)", False),
    ("border_l", "Border Left", False),
    ("border_r", "Border Right", False),
    ("border_t", "Border Top", False),
    ("border_b", "Border Bottom", False),
    ("gang_x", "X Multiplier", False),
    ("gang_y", "Y Multiplier", False),
    ("sheet_len", "Sheet Length (mm)", False),
    ("sheet_w", "Sheet Width (mm)", False),
    ("panel_len", "Panel Length (mm)", False),
    ("panel_w", "Panel Width (mm)", False),
    ("cutting_len", "Cutting Length (mm)", False),
    ("cutting_w", "Cutting Width (mm)", False),
    ("kerf_x", "Kerf / Panel Gap X (mm)", False),
    ("kerf_y", "Kerf / Panel Gap Y (mm)", False),
    ("cnc_margin_x", "CNC Margin X (mm)", False),
    ("cnc_margin_y", "CNC Margin Y (mm)", False),
    ("pcs_panel", "PCS/Panel (UP)", False),
    ("panels_sheet", "Panels/Sheet", False),
    ("sheets", "No. of Sheets", False),
    ("x_qty", "X Qty (Sheet Length Panels)", False),
    ("y_qty", "Y Qty (Sheet Width Panels)", False),
    ("orientation", "Orientation", False),
    ("party_code", "Party Code", False),
    ("cnc_tool", "CNC/Tool", False),
    ("sheet_thickness", "Material", False),
    ("per_sq_inch", "Per Sq Inch", False),
    ("pcb_price", "Price", False),
]

FIELD_ALIASES = {
    "name": ["name", "product name", "product", "finished product", "model name",
             "party model", "product/model", "item", "description", "particulars",
             "type", "product type", "pcb type"],
    "model_code": ["model code", "code", "model no", "model no.", "model number",
                   "part no", "part no.", "product code", "model"],
    "party": ["party", "customer", "client", "party name", "customer name"],
    "cnc_tool": ["cnc/tool", "cnc tool", "cnc", "tool", "machining", "tool no"],
    "note": ["machine note", "cutting note", "remark", "remarks", "notes", "note"],
    "pcb_len": ["pcb length", "pcb l", "pcb x", "actual pcb x", "actual pcb size x",
                "pcb size x", "pcb length (mm)", "pcb size l", "pcb x size", "x"],
    "pcb_w": ["pcb width", "pcb w", "pcb y", "actual pcb y", "actual pcb size y",
              "pcb size y", "pcb width (mm)", "pcb size w", "pcb y size", "y"],
    "pcbs_x": ["pcbs x", "pcbs in x", "pcb across x", "pcbs across x", "x pcs",
               "pcs x", "pcb qty x", "pcbs along x", "pcb in x"],
    "pcbs_y": ["pcbs y", "pcbs in y", "pcb across y", "pcbs across y", "y pcs",
               "pcs y", "pcb qty y", "pcbs along y", "pcb in y"],
    "gap_x": ["gap x", "pc to pc gap x", "gap x (pc to pc)", "pc gap x", "x gap"],
    "gap_y": ["gap y", "pc to pc gap y", "gap y (pc to pc)", "pc gap y", "y gap"],
    "border_l": ["border left", "border l", "margin left", "left border"],
    "border_r": ["border right", "border r", "margin right", "right border"],
    "border_t": ["border top", "border t", "margin top", "top border"],
    "border_b": ["border bottom", "border b", "margin bottom", "bottom border"],
    "gang_x": ["x multiplier", "multiplier x", "gang x", "panel multiplier x", "gang panel x"],
    "gang_y": ["y multiplier", "multiplier y", "gang y", "panel multiplier y", "gang panel y"],
    "sheet_len": ["sheet length", "sheet l", "sheet x", "sheet length (mm)",
                  "sheet size x", "sheet size l", "sheet length mm"],
    "sheet_w": ["sheet width", "sheet w", "sheet y", "sheet width (mm)",
                "sheet size y", "sheet size w", "sheet width mm"],
    "panel_len": ["panel length", "panel l", "panel x", "panel length (mm)",
                  "panel size x", "panel size l", "panel x size", "l"],
    "panel_w": ["panel width", "panel w", "panel y", "panel width (mm)",
                "panel size y", "panel size w", "panel y size", "w"],
    "cutting_len": ["cutting length", "cutting l", "cutting x", "cutting size x",
                    "cut size x", "x cutting size", "cutting length (mm)", "gang length",
                    "gang l", "cutting size l", "gang size x", "gang size l", "4a", "48", "cut l"],
    "cutting_w": ["cutting width", "cutting w", "cutting y", "cutting size y",
                  "cut size y", "y cutting size", "cutting width (mm)", "gang width",
                  "gang w", "cutting size w", "gang size y", "gang size w", "4b", "40", "cut w"],
    "kerf_x": ["kerf x", "panel gap x", "cutting gap x", "kerf x (mm)", "panel gap / kerf x (mm)"],
    "kerf_y": ["kerf y", "panel gap y", "cutting gap y", "kerf y (mm)", "panel gap / kerf y (mm)"],
    "cnc_margin_x": ["cnc margin x", "margin x", "cnc x", "cnc margin x (mm)"],
    "cnc_margin_y": ["cnc margin y", "margin y", "cnc y", "cnc margin y (mm)"],
    "pcs_panel": ["pcs/panel", "pcs per panel", "pcs panel", "pcb in one panel",
                  "pcbs/panel", "pcbs per panel", "pcs in panel", "pcs", "pcb/panel",
                  "up", "u.p.", "u.p", "units per panel", "units/panel",
                  "unit per panel", "pcs up", "up pcs", "pcb up", "panel up"],
    "panels_sheet": ["panels/sheet", "panels per sheet", "panels sheet", "panel per sheet",
                     "panels in sheet", "panel/sheet", "ups"],
    "sheets": ["no of sheets", "no. of sheet", "sheets", "sheet qty", "number of sheets",
               "no of sheet", "no. of sheets", "sheets qty", "total sheets"],
    "x_qty": ["x qty", "x cutting qty", "x panels", "sheet length panels", "qty x",
              "panels along length", "x layout", "sheet length (panels)", "x cutting qty (panels)",
              "x#", "x #", "cut x#", "cutting x#", "n", "n1", "n 1", "n-1", "n.1", "n2", "n 2"],
    "y_qty": ["y qty", "y cutting qty", "y panels", "sheet width panels", "qty y",
              "panels along width", "y layout", "sheet width (panels)", "y cutting qty (panels)",
              "y#", "y #", "cut y#", "cutting y#", "n", "n2", "n 2", "n-2", "n.2", "n1", "n 1"],
    "orientation": ["orientation", "layout", "layout type", "layout orientation", "best layout"],
    # v3.99: SIZE SHEET ke naye columns (PARTY CODE / PER SQ.INCH / MATERIAL / CNC/TOOL)
    "party_code": ["party code", "party code.", "party code no", "p code"],
    "per_sq_inch": ["per sq inch", "per sq.inch", "per sq. inch", "per square inch",
                    "rs/sq inch", "rs per sq inch", "rate/sq inch", "rate per sq inch",
                    "rs sq inch", "per sq.inch rate"],
    "sheet_thickness": ["material", "material grade", "fr4 grade", "pcb material",
                        "thickness", "pcb thickness", "sheet thickness", "thickness mm"],
    "pcb_price": ["price", "rate", "pcb price", "price rs", "rate rs", "price rs.",
                  "per sq inch", "per sq.inch", "per sq. inch", "rs/sq inch",
                  "rs per sq inch", "rate/sq inch", "rate per sq inch"],
}

# combined size columns: ek hi cell mein "54×54" / "328×237.9"
PAIR_COLUMNS = {
    "pcb size": ("pcb_len", "pcb_w"),
    "pcb actual size": ("pcb_len", "pcb_w"),
    "panel size": ("panel_len", "panel_w"),
    "cutting size": ("cutting_len", "cutting_w"),
    "gang size": ("cutting_len", "cutting_w"),
    "sheet size": ("sheet_len", "sheet_w"),
}


def _norm_hdr(h):
    s = re.sub(r"\s+", " ", (str(h) or "").strip().lower().rstrip("*").strip())
    # v4.20: Excel superscript headers (N\u00b2 -> n2, N\u00b9 -> n1) — warna Y-count column gayab!
    s = s.replace("\u00b2", "2").replace("\u00b9", "1").replace("\u00b3", "3")
    return s


def _num(v):
    """Cell value -> float. '54×54' -> [54, 54]; units/mm/pcs hatata hai;
    '15,092' -> 15092; '12,5' -> 12.5."""
    if v is None:
        return 0.0
    s = str(v).strip().lower()
    if not s or s in ("-", "—", "na", "n/a", "none", "nil", "null"):
        return 0.0
    s = re.sub(r"[₹rs]", "", s)
    s = re.sub(r"(mm|pcs|sheets?|panels?|gang|units?|nos?|pc)\b", "", s)
    s = s.replace("×", "x").strip()
    if "x" in s:
        parts = [p.strip() for p in s.split("x") if p.strip()][:2]
        return [_num(p) for p in parts]
    s = s.replace(" ", "")
    if "," in s:
        if "." in s:
            s = s.replace(",", "")
        else:
            head, tail = s.rsplit(",", 1)
            if len(tail) == 3 and head.isdigit():
                s = head + tail
            else:
                s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _hdr_score(mapping):
    """v4.00: header-candidate kitna accha hai (zyada mapped fields + name/model bonus)."""
    n = 0
    vals = set()
    for t in mapping.values():
        if t == "ignore_num":
            continue
        if isinstance(t, tuple):
            n += 2
            vals.add(t[1]); vals.add(t[2])
        else:
            n += 1
            vals.add(t)
    if "name" in vals or "model_code" in vals:
        n += 6
    return n


def _pick_header_row(rows):
    """v4.00: aapki Excel AS-IS — group/merged 2-row header (SINGLE PANEL / CUTTING SIZE
    upar, L-W-PCS-4a-4b niche) ho ya simple single-row, best header khud pakdo."""
    cands = []
    for ri in range(0, min(3, len(rows))):
        cands.append((ri, [("" if c is None else str(c).strip()) for c in rows[ri]]))
    for a, b in ((0, 1), (1, 2), (0, 2)):
        if b < len(rows):
            merged = []
            for i in range(max(len(rows[a]), len(rows[b]))):
                va = str(rows[a][i]).strip() if i < len(rows[a]) and rows[a][i] is not None else ""
                vb = str(rows[b][i]).strip() if i < len(rows[b]) and rows[b][i] is not None else ""
                merged.append(vb if vb else va)
            cands.append((b, merged))
    best_i, best_h, best_s = 0, cands[0][1], -1
    for ri, h in cands:
        sc = _hdr_score(_map_headers(h))
        if sc > best_s:
            best_i, best_h, best_s = ri, h, sc
    return best_i, best_h


def _map_headers(headers):
    """Har column header -> field mapping (dict idx -> field). Pair columns bhi.
    Numeric headers (jaise user ki sheet me '48', '40' = panel L×W) bhi handle hote hain."""
    mapping = {}
    pair_map = {_norm_hdr(k): v for k, v in PAIR_COLUMNS.items()}
    # pass 1: exact single-field match (v4.00: duplicate header ho to agla matching field
    # — jaise sheet me 2 baar PER SQ.INCH ho to pehla per_sq_inch, doosra RATE pcb_price)
    for idx, h in enumerate(headers):
        key = _norm_hdr(h)
        if not key:
            continue
        taken = set()
        for t in mapping.values():
            if isinstance(t, tuple):
                taken.update((t[1], t[2]))
            else:
                taken.add(t)
        matches = [f for f, aliases in FIELD_ALIASES.items() if key in aliases]
        if matches:
            # pehle naye (unused) field ko do; sab used hon to same field par remap
            # (v4.17: do N columns -> pehla x_qty, doosra y_qty; UPS ab panels_sheet)
            mapping[idx] = next((f for f in matches if f not in taken), matches[0])
    # pass 1.5: pure numeric headers -> panel length / width (order se)
    num_seen = 0
    for idx, h in enumerate(headers):
        if idx in mapping:
            continue
        key = _norm_hdr(h)
        if re.fullmatch(r"\d+(\.\d+)?", key):
            num_seen += 1
            if num_seen == 1:
                mapping[idx] = "panel_len"
            elif num_seen == 2:
                mapping[idx] = "panel_w"
            else:
                mapping[idx] = "ignore_num"
    # pass 2: pair (combined size) columns
    for idx, h in enumerate(headers):
        if idx in mapping:
            continue
        key = _norm_hdr(h)
        for pk, targets in pair_map.items():
            if key == pk or (pk in key and "x" not in key and "y" not in key):
                mapping[idx] = ("PAIR", targets[0], targets[1], pk)
                break
    # pass 3: contains fallback (specific aliases only — generic words excluded)
    for idx, h in enumerate(headers):
        if idx in mapping:
            continue
        key = _norm_hdr(h)
        if len(key) < 4:
            continue  # v4.00: khaali/chhota header kabhi contains-match nahi (pehle sab "name" ban rahe the!)
        best_field = None
        for field, aliases in FIELD_ALIASES.items():
            for a in aliases:
                if len(a) >= 5 and (a in key or key in a):
                    best_field = field
                    break
            if best_field:
                break
        if best_field:
            mapping[idx] = best_field
    return mapping


def _parse_upload(file_storage):
    fname = (file_storage.filename or "").lower()
    if fname.endswith(".csv"):
        raw = file_storage.read()
        text = raw.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect)
                if any((c or "").strip() for c in r)]
        return rows, fname
    if fname.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise ValueError("Excel (.xlsx) padhne ke liye openpyxl chahiye — requirements.txt mein add kar diya hai, redeploy karo. Abhi .csv use kar sakte ho.")
        try:
            wb = load_workbook(file_storage, read_only=True, data_only=True)
        except Exception as e:
            raise ValueError("Ye .xlsx file padh nahi paye. Google Sheets se download karo: File → Download → Microsoft Excel (.xlsx). Ya .csv try karo. Error: " + str(e))
        ws = wb.active
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [("" if c is None else (str(c).strip() if isinstance(c, str) else c)) for c in row]
            if any(cells):
                rows.append(cells)
        return rows, fname
    if fname.endswith(".xls"):
        try:
            import xlrd
        except ImportError:
            raise ValueError("Purani .xls file ke liye xlrd chahiye — requirements.txt mein add kar diya hai, redeploy karo. Ya Google Sheets se .xlsx/.csv download karke upload karo.")
        try:
            wb = xlrd.open_workbook(file_contents=file_storage.read())
        except Exception as e:
            raise ValueError("Ye .xls file padh nahi paye. Google Sheets se File → Download → Microsoft Excel (.xlsx) karke dobara try karo. Error: " + str(e))
        ws = wb.sheet_by_index(0)
        rows = []
        for r in range(ws.nrows):
            cells = []
            for c in range(ws.ncols):
                cell = ws.cell_value(r, c)
                if isinstance(cell, float) and cell.is_integer():
                    cell = int(cell)
                cells.append("" if cell is None else (str(cell).strip() if isinstance(cell, str) else cell))
            if any(cells):
                rows.append(cells)
        return rows, fname
    raise ValueError("Sirf .csv ya .xlsx/.xls file upload karo.")


TEXT_FIELDS = {"name", "model_code", "orientation", "party", "note",
                   "party_code", "cnc_tool", "sheet_thickness"}


def _blank_cell(v):
    """v4.14: Excel ka BLANK (khaali/-/na/N.A.) — user: blank BLANK hi rahega, 0 nahi bharenge."""
    s = str(v if v is not None else "").strip()
    return (not s) or s.lower() in ("-", "\u2014", "na", "n/a", "none", "nil", "null")


def _row_to_fields(headers, mapping, row):
    out = {}
    for idx, h in enumerate(headers):
        target = mapping.get(idx)
        if target is None:
            continue
        val = row[idx] if idx < len(row) else ""
        if isinstance(target, tuple) and target[0] == "PAIR":
            if _blank_cell(val):
                out[target[1]] = None   # v4.14: blank -> NULL (0 nahi)
                out[target[2]] = None
            else:
                nums = _num(val)
                if isinstance(nums, list):
                    out[target[1]] = nums[0] if nums else 0.0
                    out[target[2]] = nums[1] if len(nums) > 1 else 0.0
                else:
                    out[target[1]] = nums
                    out[target[2]] = 0.0
        elif target in TEXT_FIELDS:
            out[target] = str(val).strip()
        elif target == "ignore_num":
            continue
        else:
            if _blank_cell(val):
                out[target] = None      # v4.14: blank -> NULL (0 nahi)
            else:
                nums = _num(val)
                if isinstance(nums, list):
                    nums = nums[0] if nums else 0.0
                out[target] = nums
    return out


def _import_preview(batch):
    """Staging rows se preview + mapping banao (GET confirm page ke liye)."""
    if not batch:
        return None
    rows = db.query("SELECT * FROM import_staging WHERE batch=? ORDER BY row_no", (batch,))
    if not rows:
        return None
    header_row = next((r for r in rows if r["row_no"] == 0), None)
    data_rows = [r for r in rows if r["row_no"] > 0]
    if not header_row:
        return None
    import json as _json
    headers = _json.loads(header_row["data"])
    mapping = _map_headers(headers)
    mapped = []
    for r in data_rows:
        mapped.append(_row_to_fields(headers, mapping, _json.loads(r["data"])))
    existing = {m["name"] for m in db.query("SELECT name FROM product_models")}
    # v4.07: preview bhi MODEL NO.-fallback samjhe (confirm jaisa hi) — warna 0 naye dikhta hai
    def _nm(m):
        return (str(m.get("name") or "")).strip() or (str(m.get("model_code") or "")).strip()
    new_count = sum(1 for m in mapped if _nm(m) and _nm(m) not in existing)
    upd_count = sum(1 for m in mapped if _nm(m) and _nm(m) in existing)
    skip_count = sum(1 for m in mapped if not m.get("name") and not m.get("model_code"))
    ignored = [h for idx, h in enumerate(headers) if idx not in mapping and _norm_hdr(h)]
    return {
        "batch": batch,
        "total": len(mapped),
        "new_count": new_count,
        "upd_count": upd_count,
        "skip_count": skip_count,
        "mapping": [(h, _describe_mapping(mapping.get(i)), mapping.get(i)) for i, h in enumerate(headers)],
        "ignored": ignored,
        "preview": mapped[:6],
        "preview_fields": [f for f, _, _ in IMPORT_FIELDS if any((m.get(f) or 0) for m in mapped)],
    }


def _describe_mapping(target):
    if target is None or target == "ignore_num":
        return ("ignored", None)
    if isinstance(target, tuple):
        return ("✂ " + target[3].title() + " → 2 fields (L×W)", "pair")
    for f, label, _ in IMPORT_FIELDS:
        if f == target:
            return (label, f)
    return (target, f)


@app.route("/products/import/template")
@login_required
def products_import_template():
    """Template CSV download — user apna data isme paste karke upload kar sakta hai."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label, _ in IMPORT_FIELDS])
    w.writerow(["LED Driver 12W Board", "SCPL-105", "ADSUN", "52 MM TOOL",
                40, 50, 10, 5, 0.5, 0.5,
                3, 3, 1, 1, 1, 5, 1200, 1000, 328, 237.9, 328, 1195.5,
                0, 1.5, 3, 1, 50, 3, 52, 1, 3, "rotated",
                "CHRG", "TOOL", "FR4-1MM", 2.76, 11.5])
    w.writerow(["LED Board 8W", "SCPL-101", "SOFGLOW", "CNC",
                54, 54, 7, 7, 0, 0,
                0, 0, 0, 0, 1, 1, 1200, 1000, 384, 380, 0, 0,
                2, 2, 0, 0, 49, 6, 51, 2, 2, "normal",
                "CHRG", "CNC", "FR4-1MM", 0.95, 24.4])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=finished_products_template.csv"})


def _import_guard(f):
    """v4.02: import ka koi bhi crash chupchap na jaye — error ka naam+message flash me."""
    @wraps(f)
    def w(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            flash("\u26a0\ufe0f Import me problem aayi: " + type(e).__name__ + " \u2014 " + str(e)[:200] +
                  " \u2014 file dobara try karo, phir bhi ho to ye screenshot bhejo.", "error")
            return redirect_with_token(url_for("products") + "?import=1")
    return w


@app.route("/products/import", methods=["POST"])
@login_required
@admin_required
@_import_guard
@login_required
def products_import():
    if session.get("user_role") != "admin":
        flash("Sirf admin import kar sakta hai.", "error")
        return redirect_with_token(url_for("products"))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("File choose karo (Excel ya CSV).", "error")
        return redirect_with_token(url_for("products") + "?import=1")
    try:
        rows, fname = _parse_upload(f)
    except ValueError as e:
        flash(str(e), "error")
        return redirect_with_token(url_for("products") + "?import=1")
    except Exception as e:
        flash("File padhne mein problem aayi: " + str(e) + " — Google Sheets se File → Download → Microsoft Excel (.xlsx) karke dobara try karo. Phir bhi na ho to screenshot bhejo.", "error")
        return redirect_with_token(url_for("products") + "?import=1")
    if len(rows) < 2:
        flash("File mein sirf header hai ya khaali hai — data rows chahiye.", "error")
        return redirect_with_token(url_for("products") + "?import=1")
    hidx, headers = _pick_header_row(rows)
    mapping = _map_headers(headers)
    if "name" not in mapping.values() and "model_code" not in mapping.values():
        _peek = " | ".join([str(x) for x in headers[:12] if str(x).strip()]) or "(khaali)"
        flash("\u26a0\ufe0f Column names samajh nahi aaye. File ki pehli row: " + _peek +
              " \u2014 'Model No.' ya 'Product Name' jaisa column hona chahiye (template dekho).",
              "error")
        return redirect_with_token(url_for("products") + "?import=1")
    import json as _json
    db.execute("""CREATE TABLE IF NOT EXISTS import_staging (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch TEXT NOT NULL,
        row_no INTEGER DEFAULT 0,
        data TEXT DEFAULT '')""")
    batch = _now_ist().strftime("%Y%m%d%H%M%S") + "-" + os.urandom(3).hex()
    db.execute("DELETE FROM import_staging WHERE batch=?", (batch,))
    db.execute("INSERT INTO import_staging (batch, row_no, data) VALUES (?,?,?)",
               (batch, 0, _json.dumps([str(h) for h in headers])))
    for i, row in enumerate(rows[hidx + 1:], 1):
        db.execute("INSERT INTO import_staging (batch, row_no, data) VALUES (?,?,?)",
                   (batch, i, _json.dumps([("" if c is None else c) for c in row])))
    flash(f"File padh li gayi: {len(rows) - 1} rows. Neeche check karke Confirm dabao.", "success")
    return redirect_with_token(url_for("products", import_batch=batch))


@app.route("/products/import/confirm", methods=["POST"])
@login_required
@admin_required
@_import_guard
@login_required
def products_import_confirm():
    if session.get("user_role") != "admin":
        flash("Sirf admin import kar sakta hai.", "error")
        return redirect_with_token(url_for("products"))
    batch = (request.form.get("batch") or "").strip()
    do_update = request.form.get("update") == "1"
    if not batch:
        flash("Import session missing — dobara file upload karo.", "error")
        return redirect_with_token(url_for("products"))
    import json as _json
    db.ensure_product_models()   # v4.13: purana DB ho to yahin khud-theek (cnc_tool waghera guarantee)
    rows = db.query("SELECT * FROM import_staging WHERE batch=? ORDER BY row_no", (batch,))
    header_row = next((r for r in rows if r["row_no"] == 0), None)
    if not header_row:
        flash("Import session missing — dobara file upload karo.", "error")
        return redirect_with_token(url_for("products"))
    headers = _json.loads(header_row["data"])
    mapping = _map_headers(headers)
    added = updated = skipped = 0
    # v4.09: saare existing names EK query me (2352x per-row SELECT = Vercel timeout ka risk nahi)
    existing_map = {}
    for _er in db.query("SELECT id, name FROM product_models"):
        existing_map[_er["name"]] = _er["id"]   # baad wali (highest id) jeete — purane behavior jaisa
    ins_rows, upd_rows = [], []   # v4.09: writes BATCH me (2352 inserts = ~8 HTTP calls)
    for r in rows:
        if r["row_no"] == 0:
            continue
        fields = _row_to_fields(headers, mapping, _json.loads(r["data"]))
        name = (str(fields.get("name") or "")).strip()
        if not name:
            # TYPE column khaali ho to MODEL NO. se naam banao
            name = (str(fields.get("model_code") or "")).strip()
        if not name:
            skipped += 1
            continue
        existing_id = existing_map.get(name)
        gang_x = max(1, int(fields.get("gang_x") or 1))
        gang_y = max(1, int(fields.get("gang_y") or 1))
        vals = (
            (str(fields.get("model_code") or "")).strip(),
            (str(fields.get("party") or "")).strip(),
            (str(fields.get("note") or "")).strip(),
            (str(fields.get("party_code") or "")).strip(),
            (str(fields.get("cnc_tool") or "")).strip(),
            (str(fields.get("sheet_thickness") or "")).strip(),
            (float(fields.get("per_sq_inch")) if fields.get("per_sq_inch") is not None else None),
            (float(fields.get("pcb_price")) if fields.get("pcb_price") is not None else None),
            fields.get("pcb_len"), fields.get("pcb_w"),          # v4.14: blank -> NULL (blank hi rahe)
            int(fields.get("pcbs_x") or 0), int(fields.get("pcbs_y") or 0),
            float(fields.get("gap_x") or 0), float(fields.get("gap_y") or 0),
            float(fields.get("border_l") or 0), float(fields.get("border_r") or 0),
            float(fields.get("border_t") or 0), float(fields.get("border_b") or 0),
            gang_x, gang_y,
            float(fields.get("sheet_len") or 0), float(fields.get("sheet_w") or 0),
            (float(fields.get("panel_len")) if fields.get("panel_len") is not None else None),
            (float(fields.get("panel_w")) if fields.get("panel_w") is not None else None),
            (float(fields.get("cutting_len")) if fields.get("cutting_len") is not None else None),
            (float(fields.get("cutting_w")) if fields.get("cutting_w") is not None else None),
            float(fields.get("kerf_x") or 2), float(fields.get("kerf_y") or 2),
            float(fields.get("cnc_margin_x") or 0), float(fields.get("cnc_margin_y") or 0),
            (str(fields.get("orientation") or "normal")).strip() or "normal",
            fields.get("pcs_panel"), fields.get("panels_sheet"),   # v4.14: blank -> NULL
            fields.get("sheets"),
            (max(0, int(fields.get("x_qty") or 0)) if fields.get("x_qty") is not None else None),
            (max(0, int(fields.get("y_qty") or 0)) if fields.get("y_qty") is not None else None),
        )
        if existing_id:
            if do_update:
                upd_rows.append(vals + (existing_id,))
            else:
                skipped += 1
        else:
            ins_rows.append((name,) + vals + (_today_ist().isoformat(),))
    # v4.09: ek-ek row ka HTTP call nahi — poora batch ek saath (verified Turso batch)
    if ins_rows:
        db.execute_many(
            "INSERT INTO product_models (name, model_code, party, note, party_code, cnc_tool, "
            "sheet_thickness, per_sq_inch, pcb_price, pcb_len, pcb_w, pcbs_x, pcbs_y, "
            "gap_x, gap_y, border_l, border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, "
            "panel_len, panel_w, cutting_len, cutting_w, kerf_x, kerf_y, cnc_margin_x, cnc_margin_y, "
            "orientation, pcs_panel, panels_sheet, sheets, x_qty, y_qty, created_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ins_rows)
        added = len(ins_rows)
    if upd_rows:
        db.execute_many(
            "UPDATE product_models SET model_code=?, party=?, note=?, party_code=?, cnc_tool=?, "
            "sheet_thickness=?, per_sq_inch=?, pcb_price=?, pcb_len=?, pcb_w=?, pcbs_x=?, pcbs_y=?, "
            "gap_x=?, gap_y=?, border_l=?, border_r=?, border_t=?, border_b=?, gang_x=?, gang_y=?, "
            "sheet_len=?, sheet_w=?, panel_len=?, panel_w=?, cutting_len=?, cutting_w=?, "
            "kerf_x=?, kerf_y=?, cnc_margin_x=?, cnc_margin_y=?, orientation=?, pcs_panel=?, "
            "panels_sheet=?, sheets=?, x_qty=?, y_qty=? WHERE id=?", upd_rows)   # v4.09: SET order == vals order (pehle shift ho raha tha!)
        updated = len(upd_rows)
    db.execute("DELETE FROM import_staging WHERE batch=?", (batch,))
    msg = f"✅ Import complete: {added} naye products add hue"
    if do_update:
        msg += f", {updated} update hue"
    else:
        msg += f", {updated} pehle se the (update OFF tha) skip hue"
    if skipped:
        msg += f", {skipped} skip hue (name khaali)"
    flash(msg + ".", "success")
    return redirect_with_token(url_for("products"))


# ---------------------------------------------------------------- model attachment
# Har finished product/model ke saath ek PDF ya image (drawing/datasheet/photo) —
# DB mein base64 store hota hai (Vercel filesystem ephemeral hai, Turso me safe).

ALLOWED_ATTACH = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
                  "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
                  "bmp": "image/bmp"}
MAX_ATTACH = 3 * 1024 * 1024  # 3MB


@app.route("/products/<int:pid>/attachment", methods=["GET"])
@login_required
def product_attachment_view(pid):
    m = db.query("SELECT attachment_name, attachment_mime, attachment_data, name FROM product_models WHERE id=?",
                 (pid,), one=True)
    if not m or not m["attachment_data"]:
        flash("Is model ka koi attachment nahi hai — 📎 button se upload karo.", "error")
        return redirect_with_token(url_for("products"))
    try:
        data = base64.b64decode(m["attachment_data"])
    except Exception:
        flash("Attachment data corrupt hai — dobara upload karo.", "error")
        return redirect_with_token(url_for("products"))
    mime = m["attachment_mime"] or "application/octet-stream"
    fname = m["attachment_name"] or f"model-{pid}"
    resp = Response(data, mimetype=mime)
    resp.headers["Content-Disposition"] = f'inline; filename="{fname}"'
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.route("/products/<int:pid>/attachment", methods=["POST"])
@login_required
def product_attachment_upload(pid):
    if session.get("user_role") != "admin":
        return jsonify(ok=False, msg="Sirf admin attachment daal sakta hai."), 403
    model = db.query("SELECT id, name FROM product_models WHERE id=?", (pid,), one=True)
    if not model:
        return jsonify(ok=False, msg="Model nahi mila."), 404
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(ok=False, msg="File choose karo."), 400
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_ATTACH:
        return jsonify(ok=False, msg="Sirf PDF ya image (png/jpg/jpeg/webp/gif/bmp) file chalegi."), 400
    data = f.read()
    if not data:
        return jsonify(ok=False, msg="File khaali hai."), 400
    if len(data) > MAX_ATTACH:
        return jsonify(ok=False, msg="File 3MB se badi hai — chhoti file use karo."), 400
    b64 = base64.b64encode(data).decode("ascii")
    db.execute("UPDATE product_models SET attachment_name=?, attachment_mime=?, attachment_data=? WHERE id=?",
               (f.filename[:180], ALLOWED_ATTACH[ext], b64, pid))
    return jsonify(ok=True, msg=f"Attachment save ho gaya: {f.filename}")


@app.route("/products/<int:pid>/attachment/delete", methods=["POST"])
@login_required
def product_attachment_delete(pid):
    if session.get("user_role") != "admin":
        return jsonify(ok=False, msg="Sirf admin delete kar sakta hai."), 403
    db.execute("UPDATE product_models SET attachment_name='', attachment_mime='', attachment_data='' WHERE id=?",
               (pid,))
    return jsonify(ok=True, msg="Attachment hata di.")


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
    ts = _now_ist().strftime("%Y-%m-%d %H:%M")

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
    taken_on = _now_ist().strftime("%Y-%m-%d %H:%M")
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
                "INSERT INTO inventory (code, name, category, stock, min_stock, unit, hsn) VALUES (?,?,?,?,?,?,?)",
                (f"INV-{7 + count:02d}", f.get("name").strip(), f.get("category", "").strip(),
                 float(f.get("stock", 0) or 0), float(f.get("min_stock", 0) or 0), f.get("unit", "pcs"),
                 (f.get("hsn") or "").strip()))
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
        db.execute("UPDATE inventory SET code=?, name=?, category=?, stock=?, min_stock=?, unit=?, hsn=? WHERE id=?",
                   ((f.get("code") or "").strip(), name, (f.get("category") or "").strip(),
                    float(f.get("stock", 0) or 0), float(f.get("min_stock", 0) or 0),
                    (f.get("unit") or "pcs").strip(), (f.get("hsn") or "").strip(), item_id))
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
        party = (f.get("party") or "").strip()
        if party:
            rows = _items_from_form(f)
            if not rows:
                # legacy: sirf amount diya ho
                rows = [("", "", 0.0, _fl(f.get("amount")))]
            subtotal, tax_amt, grand = _totals(rows, f.get("tax_percent"))
            count = db.query("SELECT COUNT(*) c FROM billing", one=True)["c"]
            inv_id = db.execute(
                "INSERT INTO billing (invoice_no, party, amount, status, date, tax_percent, note, created_on) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"INV-{105 + count}", party, grand, f.get("status", "pending"),
                 _today_ist().isoformat(), _fl(f.get("tax_percent")), (f.get("note") or "").strip(),
                 _now_dt()))
            for r, _h18 in zip(rows, _hsn_aligned(f) or ["85340000"] * len(rows)):
                db.execute("INSERT INTO billing_items (bill_id, item, qty, rate, amount, hsn) "
                           "VALUES (?,?,?,?,?,?)", (inv_id, r[0], r[1], r[2], r[3], _h18))
            flash("Invoice created.", "success")
        return redirect_with_token(url_for("billing"))
    rows = db.query("SELECT * FROM billing ORDER BY id DESC")
    totals = db.query("SELECT COALESCE(SUM(amount),0) total, "
                      "COALESCE(SUM(CASE WHEN status='paid' THEN amount END),0) paid, "
                      "COALESCE(SUM(CASE WHEN status!='paid' THEN amount END),0) pending FROM billing", one=True)
    items_by_bill = {}
    for it in db.query("SELECT * FROM billing_items ORDER BY id"):
        items_by_bill.setdefault(it["bill_id"], []).append(it)
    parties = db.query("SELECT * FROM parties ORDER BY name")
    party_names = {p["name"] for p in parties}
    edit_inv = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_inv = db.query("SELECT * FROM billing WHERE id=?", (int(eid),), one=True)
    return render_template("billing.html", active="billing", rows=rows, totals=totals,
                           items_by_bill=items_by_bill, all_parties=parties, party_names=party_names,
                           comp=_po_company(),
                           edit_items=items_by_bill.get(edit_inv["id"], []) if edit_inv else [],
                           bill_models=db.query("SELECT * FROM product_models ORDER BY name"),
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
    rows = _items_from_form(f)
    if not rows:
        rows = [("", "", 0.0, _fl(f.get("amount")))]
    subtotal, tax_amt, grand = _totals(rows, f.get("tax_percent"))
    try:
        db.execute("UPDATE billing SET invoice_no=?, party=?, amount=?, status=?, date=?, "
                   "tax_percent=?, note=? WHERE id=?",
                   (invoice_no, party, grand, f.get("status", "pending"),
                    f.get("date") or _today_ist().isoformat(),
                    _fl(f.get("tax_percent")), (f.get("note") or "").strip(), inv_id))
        db.execute("DELETE FROM billing_items WHERE bill_id=?", (inv_id,))
        for r, _h18 in zip(rows, _hsn_aligned(f) or ["85340000"] * len(rows)):
            db.execute("INSERT INTO billing_items (bill_id, item, qty, rate, amount, hsn) "
                       "VALUES (?,?,?,?,?,?)", (inv_id, r[0], r[1], r[2], r[3], _h18))
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
        db.execute("DELETE FROM billing_items WHERE bill_id=?", (inv_id,))
        db.execute("DELETE FROM billing WHERE id=?", (inv_id,))
        flash(f"Invoice {inv['invoice_no']} delete ho gaya 🗑", "success")
    return redirect_with_token(url_for("billing"))


@app.route("/billing/<int:inv_id>/print")
@login_required
def billing_print(inv_id):
    inv = db.query("SELECT * FROM billing WHERE id=?", (inv_id,), one=True)
    if not inv:
        flash("Invoice nahi mila.", "error")
        return redirect_with_token(url_for("billing"))
    items = db.query("SELECT * FROM billing_items WHERE bill_id=? ORDER BY id", (inv_id,))
    if not items:
        # purane single-amount invoices
        items = [{"item": "", "qty": "", "rate": 0, "amount": inv["amount"]}]
    subtotal, tax_amt, grand = _totals([(i["item"], i["qty"], i["rate"], i["amount"]) for i in items],
                                       inv["tax_percent"])
    return render_template("bill_print.html", inv=inv, items=items, active="billing",
                           comp=_po_company(), subtotal=subtotal, tax=float(inv["tax_percent"] or 0),
                           tax_amt=tax_amt, grand=grand, grand_words=_amt_words(grand),
                           brand=_doc_brand(), bank_default=_BANK_DEFAULT,
                           generated=_today_ist().isoformat())


# ---------------------------------------------------------------- proforma invoices (PI) — v3.13
@app.route("/proforma", methods=["GET", "POST"])
@login_required
def proforma():
    """v3.13 — PI list + naya PI (manual ya order se) + print + convert-to-invoice."""
    if request.method == "POST":
        f = request.form
        action = f.get("action", "add")
        if action == "del":
            try:
                _pid = int(f.get("pi_id", 0) or 0)
                db.execute("DELETE FROM proforma_items WHERE pi_id=?", (_pid,))
                db.execute("DELETE FROM proforma_invoices WHERE id=? AND status='open'", (_pid,))
                flash("PI delete ho gaya (sirf OPEN PI hi delete hote hain).", "success")
            except ValueError:
                pass
            return redirect_with_token(url_for("proforma"))
        if action == "convert":
            try:
                _pid = int(f.get("pi_id", 0) or 0)
            except ValueError:
                _pid = 0
            pi = db.query("SELECT * FROM proforma_invoices WHERE id=?", (_pid,), one=True) if _pid else None
            if not pi:
                flash("PI nahi mila.", "error")
                return redirect_with_token(url_for("proforma"))
            if pi["converted_bill_id"]:
                flash("Ye PI pehle hi convert ho chuka hai (Invoice #" + str(pi["converted_bill_id"]) + ").", "error")
                return redirect_with_token(url_for("proforma"))
            _pitems = db.query("SELECT * FROM proforma_items WHERE pi_id=?", (_pid,))
            rows = [(it["item"], it["qty"], it["rate"], it["amount"], (it["hsn"] or "85340000")) for it in _pitems]
            if not rows:
                rows = [("", "", 0.0, pi["amount"] or 0, "85340000")]
            subtotal, tax_amt, grand = _totals(rows, pi["tax_percent"])
            count = db.query("SELECT COUNT(*) c FROM billing", one=True)["c"]
            inv_id = db.execute(
                "INSERT INTO billing (invoice_no, party, amount, status, date, tax_percent, note, created_on) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("INV-PI-" + (pi["pi_no"] or str(_pid)), pi["party"], grand, "pending",
                 _today_ist().isoformat(), pi["tax_percent"] or 0,
                 "PI " + (pi["pi_no"] or str(_pid)) + " se convert hua" +
                 ((" — " + (pi["note"]) if pi["note"] else "")), _now_dt()))
            for r in rows:
                db.execute("INSERT INTO billing_items (bill_id, item, qty, rate, amount, hsn) VALUES (?,?,?,?,?,?)",
                           (inv_id, r[0], r[1], r[2], r[3], r[4] if len(r) > 4 else "85340000"))
            db.execute("UPDATE proforma_invoices SET status='converted', converted_bill_id=? WHERE id=?",
                       (inv_id, _pid))
            flash("PI " + (pi["pi_no"] or "") + " INVOICE me convert ho gaya — Billing me khul gaya.", "success")
            return redirect_with_token(url_for("billing") + f"?edit={inv_id}")
        if action == "edit":
            """v3.17 — PI edit (sirf open): party/items/terms sab update."""
            try:
                _pid = int(f.get("pi_id", 0) or 0)
            except ValueError:
                _pid = 0
            pi = db.query("SELECT * FROM proforma_invoices WHERE id=?", (_pid,), one=True) if _pid else None
            if not pi:
                flash("PI nahi mila.", "error")
                return redirect_with_token(url_for("proforma"))
            if pi["status"] == "converted":
                flash("Converted PI edit nahi hota — uske Invoice me changes Billing me karo.", "error")
                return redirect_with_token(url_for("proforma"))
            party = (f.get("party") or "").strip()
            if not party:
                flash("PI ke liye party zaroori hai.", "error")
                return redirect_with_token(url_for("proforma"))
            rows = _items_from_form(f)
            if not rows:
                rows = [("", "", 0.0, _fl(f.get("amount")))]
            subtotal, tax_amt, grand = _totals(rows, f.get("tax_percent"))
            db.execute("UPDATE proforma_invoices SET party=?, amount=?, tax_percent=?, note=?, "
                       "payment_terms=?, delivery_time=?, bank_details=?, other_terms=? WHERE id=?",
                       (party, grand, _fl(f.get("tax_percent")), (f.get("note") or "").strip(),
                        (f.get("payment_terms") or "").strip(), (f.get("delivery_time") or "").strip(),
                        (f.get("bank_details") or "").strip(), (f.get("other_terms") or "").strip(), _pid))
            db.execute("DELETE FROM proforma_items WHERE pi_id=?", (_pid,))
            for r, _h18 in zip(rows, _hsn_aligned(f) or ["85340000"] * len(rows)):
                db.execute("INSERT INTO proforma_items (pi_id, item, qty, rate, amount, hsn) VALUES (?,?,?,?,?,?)",
                           (_pid, r[0], r[1], r[2], r[3], _h18))
            flash("PI " + (pi["pi_no"] or "") + " update ho gaya — print dobara nikalo.", "success")
            return redirect_with_token(url_for("proforma"))
        # action == add
        party = (f.get("party") or "").strip()
        if not party:
            flash("PI ke liye party zaroori hai.", "error")
            return redirect_with_token(url_for("proforma"))
        rows = _items_from_form(f)
        if not rows:
            rows = [("", "", 0.0, _fl(f.get("amount")))]
        subtotal, tax_amt, grand = _totals(rows, f.get("tax_percent"))
        count = db.query("SELECT COUNT(*) c FROM proforma_invoices", one=True)["c"]
        order_id = int(f.get("order_id", 0) or 0)
        pi_no = f"PI-{101 + count}"
        if order_id:
            oo = db.query("SELECT order_no FROM orders WHERE id=?", (order_id,), one=True)
            if oo:
                pi_no += "-" + (oo["order_no"] or "").lstrip("#")
        pi_id = db.execute(
            "INSERT INTO proforma_invoices (pi_no, party, amount, status, date, tax_percent, note, order_id, created_on, "
            "payment_terms, delivery_time, bank_details, other_terms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pi_no, party, grand, "open", _today_ist().isoformat(), _fl(f.get("tax_percent")),
             (f.get("note") or "").strip(), order_id or None, _now_dt(),
             (f.get("payment_terms") or "").strip(), (f.get("delivery_time") or "").strip(),
             (f.get("bank_details") or "").strip(), (f.get("other_terms") or "").strip()))
        for r, _h18 in zip(rows, _hsn_aligned(f) or ["85340000"] * len(rows)):
            db.execute("INSERT INTO proforma_items (pi_id, item, qty, rate, amount, hsn) VALUES (?,?,?,?,?,?)",
                       (pi_id, r[0], r[1], r[2], r[3], _h18))
        flash("PI " + pi_no + " ban gaya — print/PDF nikalo ya baad me Invoice me convert karo.", "success")
        return redirect_with_token(url_for("proforma"))
    rows = db.query("SELECT * FROM proforma_invoices ORDER BY id DESC")
    totals = db.query("SELECT COALESCE(SUM(CASE WHEN status='open' THEN amount END),0) open_amt, "
                      "COUNT(*) total, SUM(CASE WHEN status='converted' THEN 1 ELSE 0 END) converted "
                      "FROM proforma_invoices", one=True)
    items_by_pi = {}
    for it in db.query("SELECT * FROM proforma_items ORDER BY id"):
        items_by_pi.setdefault(it["pi_id"], []).append(it)
    edit_pi = None
    edit_items = []
    try:
        _eid = int(request.args.get("edit", 0) or 0)
    except ValueError:
        _eid = 0
    if _eid:
        edit_pi = db.query("SELECT * FROM proforma_invoices WHERE id=?", (_eid,), one=True)
        if edit_pi:
            edit_items = db.query("SELECT * FROM proforma_items WHERE pi_id=? ORDER BY id", (_eid,))
    return render_template("proforma.html", active="proforma", rows=rows, totals=totals,
                           items_by_pi=items_by_pi, all_parties=db.query("SELECT * FROM parties ORDER BY name"),
                           orders=db.query("SELECT o.id, o.order_no, o.party, o.product, o.qty, o.value, "
                                           "(SELECT pcb_price FROM product_models WHERE order_id=o.id ORDER BY id DESC LIMIT 1) m_price, "
                                           "(SELECT hsn FROM product_models WHERE order_id=o.id ORDER BY id DESC LIMIT 1) m_hsn, "
                                           "(SELECT name FROM product_models WHERE order_id=o.id ORDER BY id DESC LIMIT 1) m_name "
                                           "FROM orders o ORDER BY o.id DESC LIMIT 100"),
                           bill_models=db.query("SELECT * FROM product_models ORDER BY name"),
                           show_add=request.args.get("add"), comp=_po_company(),
                           edit_pi=edit_pi, edit_items=edit_items,
                           bank_default="Company's Bank Details\nA/c Holder's Name : SHIVAYA CIRCUIT PRIVATE LIMITED\n"
                                        "Bank Name : HDFC BANK\nA/c No. : 50200117041601\n"
                                        "Branch & IFS Code : HARGOVIND ENCLAVE & HDFC0000481")


@app.route("/proforma/<int:pi_id>/print")
@login_required
def proforma_print(pi_id):
    """v3.13 — PI print sheet (PROFORMA INVOICE title + status stamp)."""
    pi = db.query("SELECT * FROM proforma_invoices WHERE id=?", (pi_id,), one=True)
    if not pi:
        flash("PI nahi mila.", "error")
        return redirect_with_token(url_for("proforma"))
    items = db.query("SELECT * FROM proforma_items WHERE pi_id=? ORDER BY id", (pi_id,))
    if not items:
        items = [{"item": "", "qty": "", "rate": 0, "amount": pi["amount"]}]
    subtotal, tax_amt, grand = _totals([(i["item"], i["qty"], i["rate"], i["amount"]) for i in items],
                                       pi["tax_percent"])
    _order_no = ""
    if pi["order_id"]:
        _oo = db.query("SELECT order_no FROM orders WHERE id=?", (pi["order_id"],), one=True)
        _order_no = _oo["order_no"] if _oo else ""
    return render_template("pi_print.html", inv=pi, items=items, active="proforma",
                           comp=_po_company(), subtotal=subtotal, tax=float(pi["tax_percent"] or 0),
                           tax_amt=tax_amt, grand=grand, grand_words=_amt_words(grand), order_no=_order_no,
                           generated=_today_ist().isoformat(), brand=_doc_brand(), bank_default=_BANK_DEFAULT)


# ---------------------------------------------------------------- v3.25 document brand color
@app.route("/doc-brand", methods=["POST"])
@login_required
def doc_brand():
    color = (request.form.get("color") or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", color):
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('doc_brand', ?)", (color,))
    nxt = request.form.get("next") or url_for("proforma")
    return redirect(nxt)


@app.route("/ui-brand", methods=["POST"])
@login_required
@admin_required
def ui_brand():
    color = (request.form.get("color") or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", color):
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ui_theme', ?)", (color,))
    nxt = request.form.get("next") or url_for("settings")
    return redirect(nxt)


def _pdf_ensure():
    """v3.35/38 — Final PDFs per model. data=BLOB (local) + data64=base64 TEXT (Turso-safe)."""
    db.execute("""CREATE TABLE IF NOT EXISTS model_pdfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id INTEGER, model_name TEXT, filename TEXT,
        data BLOB, uploaded_on TEXT, data64 TEXT DEFAULT '')""")
    _cols = [r["name"] for r in db.query("PRAGMA table_info(model_pdfs)")]
    if "data64" not in _cols:
        db.execute("ALTER TABLE model_pdfs ADD COLUMN data64 TEXT DEFAULT ''")


@app.route("/pdfs")
@login_required
def pdf_library():
    _pdf_ensure()
    q = (request.args.get("q") or "").strip()
    mid = (request.args.get("model") or "").strip()
    _w, _p = [], []
    if q:
        _w.append("(IFNULL(model_name,'') LIKE ? OR IFNULL(filename,'') LIKE ?)")
        _p += ["%" + q + "%"] * 2
    if mid:
        _w.append("model_id=?")
        _p.append(mid)
    _sql = ("SELECT id, model_id, model_name, filename, LENGTH(COALESCE(data64, data)) AS sz, uploaded_on FROM model_pdfs")  # v3.79: naye rows ka data NULL hota hai, data64 se size
    if _w:
        _sql += " WHERE " + " AND ".join(_w)
    _sql += " ORDER BY id DESC"
    rows = [dict(r) for r in db.query(_sql, _p)]
    # v3.66 — Finished Products ka 📎 attachment BHI yahan dikhe (ek hi doc-duniya)
    _att_sql = ("SELECT id AS pid, name AS model_name, attachment_name AS filename, "
                "LENGTH(attachment_data) AS sz FROM product_models WHERE IFNULL(attachment_data,'') != ''")
    _att_p = []
    if q:
        _att_sql += " AND (name LIKE ? OR attachment_name LIKE ?)"
        _att_p += ["%" + q + "%"] * 2
    if mid:
        try:
            _att_sql += " AND id=?"
            _att_p.append(int(mid))
        except (TypeError, ValueError):
            pass
    _att_rows = [dict(r, is_att=True, uploaded_on="FP 📎") for r in db.query(_att_sql, _att_p)]
    rows = _att_rows + rows
    return render_template("pdfs.html", active="pdfs", rows=rows,
                           models=db.query("SELECT id, name FROM product_models ORDER BY name"),
                           q=q, sel_model=mid, add_focus=request.args.get("add"))


@app.route("/model-pdf", methods=["POST"])
@login_required
@admin_required
def model_pdf_upload():
    _pdf_ensure()
    try:
        mid = int(request.form.get("model_id") or 0)
    except ValueError:
        mid = 0
    # v3.36 — NAYA MODEL: naam likha hai to model_id ki zaroorat nahi
    new_name = (request.form.get("new_model") or "").strip()
    if new_name:
        existing = db.query("SELECT id, name FROM product_models WHERE LOWER(name)=LOWER(?)", (new_name,), one=True)
        if existing:
            mid = existing["id"]
            flash("Model '" + existing["name"] + "' pehle se tha — usi me PDF save ki.", "success")
        else:
            mid = db.execute("INSERT INTO product_models (name, hsn, created_on) VALUES (?,?,?)",
                             (new_name, "85340000", _today_ist().isoformat()))
            flash("Naya model '" + new_name + "' ban gaya (HSN 85340000 default) — Finished Products me bhi dikhega.", "success")
    file = request.files.get("pdf_file")
    if not mid or file is None or not file.filename:
        flash("Model chuno YA naya model ka naam likho + PDF file dono chahiye.", "error")
        return redirect_with_token(url_for("pdf_library"))
    if not file.filename.lower().endswith(".pdf"):
        flash("Sirf PDF file upload karo (.pdf).", "error")
        return redirect_with_token(url_for("pdf_library"))
    data = file.read()
    if len(data) > 4 * 1024 * 1024:
        flash("PDF 4MB se chhoti honi chahiye (server limit).", "error")
        return redirect_with_token(url_for("pdf_library"))
    m = db.query("SELECT name FROM product_models WHERE id=?", (mid,), one=True)
    if not m:
        flash("Model nahi mila.", "error")
        return redirect_with_token(url_for("pdf_library"))
    # v3.79 — PDF sirf EK baar store (data64 base64 TEXT — Turso/Vercel proven pattern,
    # FP-attachment jaisa). Pehle blob+base64 DOUBLE hota tha -> row ~10MB -> VIEW par
    # Turso/Vercel limit tut kar "server error" deta tha.
    db.execute("INSERT INTO model_pdfs (model_id, model_name, filename, data, uploaded_on, data64) VALUES (?,?,?,?,?,?)",
               (mid, m["name"], file.filename, None, _today_ist().isoformat(),
                base64.b64encode(data).decode("ascii")))
    flash("PDF '" + file.filename + "' \u2192 " + m["name"] + " save ho gayi (Cutlist + Products + Library teeno jagah dikhegi).", "success")
    return redirect_with_token(url_for("pdf_library"))


def _pdf_bytes(raw):
    """v3.38 — PDF bytes ko kisi bhi stored shape se nikaalo (bytes/str/base64/json-envelope)."""
    if raw is None:
        return b""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    if isinstance(raw, str):
        t = raw.strip()
        if t.startswith("%PDF"):
            return t.encode("latin-1", "ignore")
        try:
            b = base64.b64decode(t, validate=False)
            if b.startswith(b"%PDF"):
                return b
        except Exception:
            pass
        try:
            j = json.loads(t)
            if isinstance(j, dict) and j.get("base64"):
                return base64.b64decode(j["base64"])
            if isinstance(j, str) and j.startswith("%PDF"):
                return j.encode("latin-1", "ignore")
        except Exception:
            pass
    return b""


@app.route("/model-pdf/<int:pdf_id>/view")
@login_required
def model_pdf_view(pdf_id):
    """v3.79 — HALKA read: pehle sirf data64 (TEXT = aadha payload), phir data (BLOB).
    Dono koshish alag-alag — ek toote to doosri chalti hai, aur error me ASLI wajah dikhti hai.
    (Pehle ek hi SELECT me blob+base64 DONO aate the — badi PDF par Turso/Vercel limit
     tut kar 500 'server error' aata tha.)"""
    errs = []
    for _col in ("data64", "data"):
        try:
            r = db.query(f"SELECT filename, {_col} AS _pdf FROM model_pdfs WHERE id=?", (pdf_id,), one=True)
            if not r:
                return "PDF nahi mili (delete ho gayi?).", 404
            fname = (r["filename"] or "model.pdf").replace('"', "").replace("\r", "").replace("\n", "")
            raw = _pdf_bytes(r["_pdf"])
            if raw and raw[:1024].lstrip()[:4] != b"%PDF":
                raw = b""  # v3.79 — garbage bytes browser ko serve nahi karte
            if raw:
                try:
                    fname.encode("ascii")
                    cd = "inline; filename=\"" + fname + "\""
                except UnicodeEncodeError:
                    from urllib.parse import quote as _q
                    cd = "inline; filename*=UTF-8''" + _q(fname)
                return Response(raw, mimetype="application/pdf", headers={"Content-Disposition": cd})
            errs.append(_col + "=khaali/adhoora")
        except Exception as e:
            errs.append(f"{_col}: {e!r}"[:180])
            try:
                app.logger.error("PDF view fail id=%s col=%s: %r", pdf_id, _col, e)
            except Exception:
                pass
    return ("PDF kholne me dikkat aayi — " + " | ".join(errs)[:400]
            + " — is PDF ko delete karke dobara upload karo (agar bar-bar aaye to chhoti PDF try karo).", 500)


@app.route("/model-pdf/<int:pdf_id>/edit", methods=["POST"])
@login_required
@admin_required
def model_pdf_edit(pdf_id):
    """v3.39 — PDF ko doosre Finished Product se link karo (move/re-link)."""
    _pdf_ensure()
    row = db.query("SELECT id, filename FROM model_pdfs WHERE id=?", (pdf_id,), one=True)
    if not row:
        flash("PDF nahi mili (delete ho gayi?).", "error")
        return redirect_with_token(url_for("pdf_library"))
    try:
        mid = int(request.form.get("model_id") or 0)
    except ValueError:
        mid = 0
    m = db.query("SELECT id, name FROM product_models WHERE id=?", (mid,), one=True) if mid else None
    if not m:
        flash("Naya model chuno — PDF kis Finished Product se link karni hai?", "error")
        return redirect_with_token(url_for("pdf_library"))
    db.execute("UPDATE model_pdfs SET model_id=?, model_name=? WHERE id=?", (m["id"], m["name"], pdf_id))
    flash("'" + (row["filename"] or "PDF") + "' ab " + m["name"] + " se link ho gayi — Cutlist + Products me wahin dikhegi.", "success")
    return redirect_with_token(url_for("pdf_library"))


@app.route("/model-pdf/<int:pdf_id>/delete", methods=["POST"])
@login_required
@admin_required
def model_pdf_delete(pdf_id):
    _pdf_ensure()
    db.execute("DELETE FROM model_pdfs WHERE id=?", (pdf_id,))
    flash("PDF delete ho gayi.", "success")
    return redirect_with_token(url_for("pdf_library"))


@app.route("/settings")
@login_required
@admin_required
def settings():
    return render_template("settings.html", active="settings",
                           ui_brand=_ui_brand(), doc_brand=_doc_brand())


# ---------------------------------------------------------------- payments & receipts
@app.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    if request.method == "POST":
        f = request.form
        if f.get("party", "").strip():
            count = db.query("SELECT COUNT(*) c FROM payments", one=True)["c"]
            db.execute("INSERT INTO payments (ref_no, party, ptype, amount, mode, date, created_on) VALUES (?,?,?,?,?,?,?)",
                       (f"PMT-{204 + count}", f.get("party").strip(), f.get("ptype", "receipt"),
                        float(f.get("amount", 0) or 0), f.get("mode", "Bank"),
                        _today_ist().isoformat(), _now_dt()))
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
                    f.get("mode", "Bank"), f.get("date") or _today_ist().isoformat(), pmt_id))
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




# ---------------------------------------------------------------- v3.84 PO SE JOB CARD
def _po_ensure():
    """PO files store + orders par po_no/po_sno (self-heal)."""
    db.execute("""CREATE TABLE IF NOT EXISTS po_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_no TEXT, filename TEXT, mime TEXT, data64 TEXT DEFAULT '', uploaded_on TEXT)""")
    _oc = [r["name"] for r in db.query("PRAGMA table_info(orders)")]
    if "po_no" not in _oc:
        db.execute("ALTER TABLE orders ADD COLUMN po_no TEXT DEFAULT ''")
    if "po_sno" not in _oc:
        db.execute("ALTER TABLE orders ADD COLUMN po_sno INTEGER DEFAULT 0")
    _jc = [r["name"] for r in db.query("PRAGMA table_info(jobcard)")]
    if "party_code" not in _jc:
        db.execute("ALTER TABLE jobcard ADD COLUMN party_code TEXT DEFAULT ''")
    _mc = [r["name"] for r in db.query("PRAGMA table_info(product_models)")]
    if "design64" not in _mc:
        db.execute("ALTER TABLE product_models ADD COLUMN design64 TEXT DEFAULT ''")
    if "design_mime" not in _mc:
        db.execute("ALTER TABLE product_models ADD COLUMN design_mime TEXT DEFAULT ''")
    try:
        db.commit()
    except Exception:
        pass


_PO_MODEL_RE = re.compile(r"\b([A-Z]{2,4})\s*[-\u2013]?\s*([0-9][A-Z0-9\-_/\\.]*)", re.I)  # v3.84 generic prefix (IIS/SCPL/OM/OSI/LP/SB/DHL...)


def _po_parse_text(text):
    """PO text -> S.No. lines. KABHI invent nahi karte: model/qty jo PO me likha hai wahi.
    Sparse serials (1,7,8,12) -> 12 slots pad (OCR-miss bhi row rahegi, editable)."""
    rows = []
    for l in (text or "").replace("\r", "").split("\n"):
        l = l.strip()
        if not l:
            continue
        sno = None
        ms = re.match(r"^(\d{1,3})\s*[\.\)\-:]\s*(.+)$", l)
        if ms:
            sno = int(ms.group(1))
            l = ms.group(2).strip()
        mm = _PO_MODEL_RE.search(l)
        if not mm and sno is None:
            continue  # na serial na model -> entry nahi (header/noise)
        qm = re.search(r"(\d[\d,\.]*)\s*(?:pcs|pcs\.|nos\.?|pc\b|pieces)", l, re.I)
        qty = qm.group(1).replace(",", "") if qm else ""
        if not qty:
            body2 = l.replace(mm.group(0), " ") if mm else l  # model ka apna number qty nahi
            big = re.search(r"\b(\d{3,})\b", body2)  # formal table: khada bada number = qty
            qty = big.group(1) if big else ""
        rm = re.search(r"(?:@|rs\.?|\u20b9)\s*(\d+(?:\.\d+)?)", l, re.I)
        note = ""
        if re.search(r"scan incomplete|adhura|incomplete", l, re.I):
            note = "scan incomplete"
        rows.append({"sno": sno,
                     "model": (mm.group(1).upper() + "-" + mm.group(2)) if mm else "",
                     "party_model": "", "qty": qty, "rate": rm.group(1) if rm else "", "note": note})
    # same serial do baar (model + qty alag lines) -> merge
    by = {}
    merged = []
    for r in rows:
        if r["sno"] is not None and r["sno"] in by:
            k = by[r["sno"]]
            if not k["model"] and r["model"]:
                k["model"] = r["model"]
            if not k["qty"] and r["qty"]:
                k["qty"] = r["qty"]
            if not k["rate"] and r["rate"]:
                k["rate"] = r["rate"]
            continue
        if r["sno"] is not None:
            by[r["sno"]] = r
        merged.append(r)
    rows = merged
    serials = [r["sno"] for r in rows if r["sno"]]
    # "S.No. 12 - scan incomplete" hint bhi expected count batata hai (v3.84)
    for _hl in (text or "").split("\n"):
        _mh = re.search(r"s\.?\s*no\.?[^0-9]{0,6}(\d{1,3})", _hl, re.I)
        if _mh and ("incomplete" in _hl.lower() or "scan" in _hl.lower()):
            serials.append(int(_mh.group(1)))
    if serials:
        mx = max(serials)
        have = {r["sno"] for r in rows if r["sno"]}
        for n in range(1, mx + 1):
            if n not in have:
                rows.append({"sno": n, "model": "", "party_model": "", "qty": "", "rate": "",
                             "note": "scan me nahi mila — khud bharo"})
        rows.sort(key=lambda r: (r["sno"] is not None, r["sno"] or 0))  # bina-serial lines pehle (text ke order me), phir 1..N
    else:
        for i, r in enumerate(rows):
            r["sno"] = i + 1
    return rows


def _po_key(s):
    """PO number compare-key: case/punctuation free. WHATSAPP = khaali (duplicate nahi maana)."""
    k = re.sub(r"[^A-Z0-9]", "", (s or "").upper())
    return "" if k in ("", "WHATSAPP") else k


def _po_guess_no(text):
    """PO text se PO number guess - sirf clear patterns (PO26-27366, PO-02002, Purchase Order #...)."""
    t = text or ""
    pats = [r"\bPO\d{2}-\d{2,}(?:/\d+)?\b",
            r"\bPO-\d{3,}\b",
            r"\bP\.?O\.?\s*#\s*([A-Z0-9][A-Z0-9\-/]*[A-Z0-9])",
            r"Purchase Order\s*(?:No\.?|Number)?\s*[:#]\s*([A-Z0-9\-/]*[A-Z][A-Z0-9\-/]*)"]
    for pat in pats:
        m = re.search(pat, t, re.I)
        if m:
            g = (m.group(1) if m.groups() else m.group(0)).strip().upper()
            if _po_key(g):
                return g
    return ""


def _po_find_dups(po_no):
    """Same PO ke purane orders (order_no/product/qty). WHATSAPP duplicate nahi maana."""
    k = _po_key(po_no)
    if not k:
        return []
    out = []
    for r in db.query("SELECT order_no, product, qty, po_no FROM orders "
                      "WHERE po_no IS NOT NULL AND po_no != ''"):
        if _po_key(r["po_no"]) == k:
            out.append({"order_no": r["order_no"], "product": r["product"], "qty": r["qty"]})
            if len(out) >= 10:
                break
    return out


@app.route("/api/po/dups")
@login_required
@admin_required
def po_dups():
    """v3.86b - PO number ke purane orders (warning ke liye; save kabhi block nahi hota)."""
    _po_ensure()
    po_no = request.args.get("po_no") or ""
    dups = _po_find_dups(po_no)
    return jsonify(ok=True, po_no=po_no, dups=dups, dup=bool(dups))


@app.route("/po")
@login_required
@admin_required
def po_entry():
    """v3.84 — PO/WhatsApp upload -> auto-read -> har S.No. se ek job card."""
    _po_ensure()
    parties = [p["name"] for p in db.query("SELECT name FROM parties ORDER BY name")]
    models = [dict(m) for m in db.query(
        "SELECT id, name, model_code, pcb_len, pcb_w, panel_len, panel_w, pcs_panel, "
        "cutting_len, cutting_w, sheet_len, sheet_w, sheet_thickness, party_name, "
        "party_code, pcb_price "
        "FROM product_models ORDER BY name")]
    inv = [{"id": i["id"], "name": i["name"], "unit": i["unit"]} for i in db.query(
        "SELECT id, name, unit FROM inventory ORDER BY name")]
    saved = db.query(
        "SELECT o.id, o.order_no, o.party, o.product, o.qty, o.po_sno, o.status FROM orders o "
        "WHERE IFNULL(o.po_no,'') != '' ORDER BY o.id DESC LIMIT 60")
    dup_pos = [r["po_no"] for r in db.query("SELECT DISTINCT po_no FROM po_files")]
    ctx = dict(active="po", parties=parties, models=models, inv=inv,
               models_json=json.dumps(models), saved=saved, dup_pos=dup_pos)
    if request.args.get("embed"):   # v3.85: jobcard_new ke PO tab ke andar (iframe, sidebar nahi)
        return render_template("po_embed.html", **ctx)
    return render_template("po.html", **ctx)


@app.route("/api/po/upload", methods=["POST"])
@login_required
@admin_required
def po_upload():
    """PDF/JPG/PNG/WebP save + (PDF ho to) text nikaal ke parse. Image par OCR browser karega."""
    _po_ensure()
    f = request.files.get("file")
    po_no = (request.form.get("po_no") or "WhatsApp").strip() or "WhatsApp"
    if not f or not f.filename:
        return jsonify(ok=False, error="File nahi mili."), 400
    blob = f.read()
    if len(blob) > 10 * 1024 * 1024:
        return jsonify(ok=False, error="File 10MB se chhoti honi chahiye."), 400
    fn = f.filename
    ext = (fn.rsplit(".", 1)[-1] if "." in fn else "").lower()
    if ext not in ("pdf", "jpg", "jpeg", "png", "webp"):
        return jsonify(ok=False, error="Sirf PDF / JPG / PNG / WebP chalega."), 400
    mime = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}[ext]
    text, needs_ocr = "", False
    imgs64 = []
    if ext == "pdf":
        try:
            import io as _io
            from pypdf import PdfReader
            rd = PdfReader(_io.BytesIO(blob))
            text = "\n".join((pg.extract_text() or "") for pg in rd.pages)
        except Exception:
            text = ""
        if not text.strip():
            needs_ocr = True  # scanned-PDF: browser OCR / khud lines bharo
    else:
        needs_ocr = True
    if needs_ocr and ext == "pdf":
        # v3.93: scanned PDF ke andar ki asli image seedha nikaalo (browser me pdf.js render ki zaroorat nahi)
        try:
            import io as _io2
            from pypdf import PdfReader as _PR2
            _rd2 = _PR2(_io2.BytesIO(blob))
            for _pg2 in _rd2.pages[:2]:
                for _im in _pg2.images:
                    try:
                        _d = _im.data
                        _mt = "image/png"
                        if _d[:3] == b"\xff\xd8\xff":
                            _mt = "image/jpeg"
                        elif _d[:4] == b"\x89PNG":
                            _mt = "image/png"
                        elif _d[:2] == b"BM":
                            _mt = "image/bmp"
                        else:
                            _im.image.save(_io2.BytesIO(), format="PNG")
                        imgs64.append("data:" + _mt + ";base64," + base64.b64encode(_d).decode("ascii"))
                    except Exception:
                        try:
                            _buf = _io2.BytesIO()
                            _im.image.save(_buf, format="JPEG")
                            imgs64.append("data:image/jpeg;base64," + base64.b64encode(_buf.getvalue()).decode("ascii"))
                        except Exception:
                            pass
                if imgs64:
                    break
        except Exception:
            imgs64 = []
    if po_no.upper() == "WHATSAPP" and text.strip():   # v3.86b: PDF me PO number mila to wahi folder naam
        g = _po_guess_no(text)
        if g:
            po_no = g
    fid = db.execute("INSERT INTO po_files (po_no, filename, mime, data64, uploaded_on) VALUES (?,?,?,?,?)",
                     (po_no, fn, mime, base64.b64encode(blob).decode("ascii"), _today_ist().isoformat()))
    lines = _po_parse_text(text)
    dups = _po_find_dups(po_no)
    return jsonify(ok=True, file_id=fid, po_no=po_no, filename=fn, mime=mime, lines=lines,
                   needs_ocr=needs_ocr, text=text[:30000], dups=dups, dup=bool(dups), imgs=imgs64[:2])


@app.route("/api/po/parse", methods=["POST"])
@login_required
@admin_required
def po_parse():
    """Browser-OCR (tesseract.js) ka text yahan same parser se lines banta hai."""
    j = request.get_json(silent=True) or {}
    return jsonify(ok=True, lines=_po_parse_text(j.get("text") or ""))


def _po_extract(blob, ext):
    """v3.97: blob -> (text, needs_ocr, imgs64) — upload aur re-process dono use karte hain."""
    text, needs_ocr, imgs64 = "", False, []
    if ext == "pdf":
        try:
            import io as _io
            from pypdf import PdfReader
            rd = PdfReader(_io.BytesIO(blob))
            text = "\n".join((pg.extract_text() or "") for pg in rd.pages)
        except Exception:
            text = ""
        if not text.strip():
            needs_ocr = True
    else:
        needs_ocr = True
    if needs_ocr and ext == "pdf":
        try:
            import io as _io2
            from pypdf import PdfReader as _PR2
            _rd2 = _PR2(_io2.BytesIO(blob))
            for _pg2 in _rd2.pages[:2]:
                for _im in _pg2.images:
                    try:
                        _d = _im.data
                        _mt = "image/png"
                        if _d[:3] == b"\xff\xd8\xff":
                            _mt = "image/jpeg"
                        elif _d[:4] == b"\x89PNG":
                            _mt = "image/png"
                        elif _d[:2] == b"BM":
                            _mt = "image/bmp"
                        imgs64.append("data:" + _mt + ";base64," + base64.b64encode(_d).decode("ascii"))
                    except Exception:
                        try:
                            _buf = _io2.BytesIO()
                            _im.image.save(_buf, format="JPEG")
                            imgs64.append("data:image/jpeg;base64," + base64.b64encode(_buf.getvalue()).decode("ascii"))
                        except Exception:
                            pass
                if imgs64:
                    break
        except Exception:
            imgs64 = []
    return text, needs_ocr, imgs64


@app.route("/api/po/list")
@login_required
@admin_required
def po_list():
    """v3.97: PO FOLDER — saare saved PO (re-process ke liye) + kitne orders ban chuke."""
    _po_ensure()
    rows = db.query("SELECT id, po_no, filename, uploaded_on FROM po_files ORDER BY id DESC LIMIT 100")
    out = []
    for r in rows:
        n = db.query("SELECT COUNT(*) c FROM orders WHERE po_no=?", (r["po_no"],), one=True)["c"]
        nc = db.query("SELECT COUNT(*) c FROM jobcard WHERE po_no=?", (r["po_no"],), one=True)["c"]
        out.append({"id": r["id"], "po_no": r["po_no"], "filename": r["filename"],
                    "uploaded_on": (r["uploaded_on"] or "")[:10], "orders": n, "cards": nc})
    return jsonify(ok=True, files=out)


@app.route("/api/po/delete/<int:fid>", methods=["POST"])
@login_required
@admin_required
def po_file_delete(fid):
    """v3.98: PO FOLDER se UNUSED PO delete — orders/cards wale PO mana (use me hai)."""
    _po_ensure()
    r = db.query("SELECT id, po_no, filename FROM po_files WHERE id=?", (fid,), one=True)
    if not r:
        return jsonify(ok=False, err="missing", msg="Ye PO pehle hi delete ho chuka."), 404
    po_no = r["po_no"] or ""
    n_ord = db.query("SELECT COUNT(*) c FROM orders WHERE po_no=?", (po_no,), one=True)["c"] if po_no else 0
    n_jc = db.query("SELECT COUNT(*) c FROM jobcard WHERE po_no=?", (po_no,), one=True)["c"] if po_no else 0
    if n_ord or n_jc:
        return jsonify(ok=False, err="in_use", orders=n_ord, cards=n_jc,
                       msg="Ye PO USE ME hai — " + str(n_ord) + " orders, " + str(n_jc) +
                           " cards iske hain. Pehle wo hatao, phir delete hoga.")
    db.execute("DELETE FROM po_files WHERE id=?", (fid,))
    return jsonify(ok=True, msg="PO folder se delete ho gaya.")


@app.route("/api/po/discard/<int:oid>", methods=["POST"])
@login_required
@admin_required
def po_discard(oid):
    """v4.04: inbox ki ek junk line hatao (uska temp order + card). Approved/running block."""
    _po_ensure()
    o = db.query("SELECT id, po_no, status FROM orders WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(ok=True, msg="Pehle hi hat chuki.")
    if (o["status"] or "pending") not in ("pending", ""):
        return jsonify(ok=False, msg="Ye order APPROVE ho chuka hai — discard nahi hoga.")
    db.execute("DELETE FROM jobcard WHERE order_id=?", (oid,))
    db.execute("DELETE FROM orders WHERE id=?", (oid,))
    return jsonify(ok=True, msg="Line hat gayi.")


@app.route("/api/po/reprocess/<int:fid>")
@login_required
@admin_required
def po_reprocess(fid):
    """v3.97: saved PO ko DOBARA process karo (same response jaisa upload ka — lines/OCR/imgs/dups)."""
    _po_ensure()
    r = db.query("SELECT * FROM po_files WHERE id=?", (fid,), one=True)
    if not r:
        return jsonify(ok=False, error="PO file nahi mili."), 404
    try:
        blob = base64.b64decode(r["data64"])
    except Exception:
        blob = b""
    if not blob:
        return jsonify(ok=False, error="File data khali hai."), 400
    fn = r["filename"] or "po"
    ext = (fn.rsplit(".", 1)[-1] if "." in fn else "pdf").lower()
    if ext not in ("pdf", "jpg", "jpeg", "png", "webp"):
        ext = "pdf"
    text, needs_ocr, imgs64 = _po_extract(blob, ext)
    if r["po_no"].upper() == "WHATSAPP" and text.strip():
        g = _po_guess_no(text)
        po_no = g or r["po_no"]
    else:
        po_no = r["po_no"]
    # v4.04: reprocess = pehle isi PO ki PURANI pending lines/cards hatao (junk OCR lines
    # dobara na jame), approved/running orders bilkul chhupe nahi rahte.
    purged = 0
    _oids = [x["id"] for x in db.query("SELECT id FROM orders WHERE po_no=? AND IFNULL(status,'')='pending'", (po_no,))]
    if _oids:
        _q = ",".join("?" * len(_oids))
        db.execute(f"DELETE FROM jobcard WHERE order_id IN ({_q})", tuple(_oids))
        db.execute(f"DELETE FROM orders WHERE id IN ({_q})", tuple(_oids))
        purged = len(_oids)
    lines = _po_parse_text(text)
    dups = _po_find_dups(po_no)
    return jsonify(ok=True, file_id=fid, po_no=po_no, filename=fn, mime=r["mime"], lines=lines,
                   needs_ocr=needs_ocr, text=text[:30000], dups=dups, dup=bool(dups), imgs=imgs64[:2],
                   purged=purged)


@app.route("/api/po/reread/<int:fid>")
@login_required
def po_reread(fid):
    """v4.10: EDITOR ka RE-READ — PO file ka text SIRF dobara nikalta hai (kuch purge/save nahi).
    Editor ke khaali PO NO/DATE/MODEL isse khud bhar jaate hain."""
    _po_ensure()
    r = db.query("SELECT * FROM po_files WHERE id=?", (fid,), one=True)
    if not r:
        return jsonify(ok=False, error="PO file nahi mili.")
    try:
        blob = base64.b64decode(r["data64"])
    except Exception:
        blob = b""
    if not blob:
        return jsonify(ok=False, error="File data khali hai.")
    fn = r["filename"] or "po"
    ext = (fn.rsplit(".", 1)[-1] if "." in fn else "pdf").lower()
    if ext not in ("pdf", "jpg", "jpeg", "png", "webp"):
        ext = "pdf"
    text, needs_ocr, imgs64 = _po_extract(blob, ext)
    g = _po_guess_no(text) if text.strip() else ""
    return jsonify(ok=True, file_id=fid, po_no=g or r["po_no"] or "", filename=fn, mime=r["mime"],
                   needs_ocr=needs_ocr, text=text[:30000])


# v3.97: SHEET inline edit (Excel-jaisi sheet se direct save)
_SHEET_FIELDS = {"model_code": str, "name": str, "party_code": str, "party_name": str,
                 "pcb_len": float, "pcb_w": float, "panel_len": float, "panel_w": float,
                 "pcs_panel": int, "cutting_len": float, "cutting_w": float,
                 "x_qty": int, "y_qty": int, "panels_sheet": int, "sheets": int,
                 "sheet_thickness": str, "per_sq_inch": float, "pcb_price": float, "cnc_tool": str}
_SHEET_LABELS = {"model_code": "MODEL NO.", "name": "TYPE", "party_code": "PARTY CODE", "party_name": "PARTY",
                 "pcb_len": "X", "pcb_w": "Y", "panel_len": "L", "panel_w": "W", "pcs_panel": "PCS",
                 "cutting_len": "4a", "cutting_w": "4b", "x_qty": "N (4a)", "y_qty": "N (4b)",
                 "panels_sheet": "UPS", "sheets": "SHEETS", "sheet_thickness": "MATERIAL",
                 "per_sq_inch": "PER SQ INCH", "pcb_price": "PRICE", "cnc_tool": "CNC/TOOL"}

@app.template_filter("numfmt")
def _numfmt18(v):
    """v4.18: SHEET display Excel-jaisa — 2.89333912 -> 2.89334, 10.0 -> 10.
    Sirf numbers / pure-numeric strings ('171.3528427' jaise MATERIAL-junk);
    text (FR4-1MM, model codes) bilkul as-is."""
    if v is None:
        return ""
    if isinstance(v, str):
        s2 = v.strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?", s2):
            return v
        v = float(s2)
    return "%g" % v


@app.route("/products/sheet/download")
@login_required
@admin_required
def products_sheet_download():
    """v4.17: SHEET download — user ki Excel ka EXACT naya format (2-row group header:
    SINGLE PCB SIZE (X,Y) / SINGLE PANEL (L,W,PCS) / CUTTING SIZE (4a,N,4b,N) / UPS /
    CNC/TOOL / PER SQ INCH / MATERIAL / PRICE). Re-upload isi tarah round-trip hota hai."""
    from openpyxl import Workbook
    from io import BytesIO as _BIO
    rows = db.query("SELECT * FROM product_models ORDER BY id")
    wb = Workbook()
    ws = wb.active
    ws.title = "SIZE SHEET"
    grp = ["", "", "", "", "SINGLE PCB SIZE", "", "SINGLE PANEL", "", "", "CUTTING SIZE", "", "", "", "", "", "", "", ""]
    sub = ["S.N.", "PARTY", "MODEL NO.", "PARTY CODE", "X", "Y", "L", "W", "PCS", "4a", "N", "4b", "N",
           "UPS", "CNC/TOOL", "PER SQ INCH", "MATERIAL", "PRICE"]
    ws.append(grp)
    ws.append(sub)
    from openpyxl.styles import Font, Alignment
    for c in ("E1", "G1", "J1"):
        ws[c].font = Font(bold=True)
        ws[c].alignment = Alignment(horizontal="center")
    ws.merge_cells("E1:F1")
    ws.merge_cells("G1:I1")
    ws.merge_cells("J1:M1")
    for _i, m in enumerate(rows, 1):
        ws.append([_i, m["party_name"] or m["party"] or "", m["model_code"] or m["name"] or "", m["party_code"] or "",
                   m["pcb_len"] if m["pcb_len"] is not None else "", m["pcb_w"] if m["pcb_w"] is not None else "",
                   m["panel_len"] if m["panel_len"] is not None else "", m["panel_w"] if m["panel_w"] is not None else "",
                   m["pcs_panel"] if m["pcs_panel"] is not None else "",
                   m["cutting_len"] if m["cutting_len"] is not None else "", m["x_qty"] if m["x_qty"] is not None else "",
                   m["cutting_w"] if m["cutting_w"] is not None else "", m["y_qty"] if m["y_qty"] is not None else "",
                   m["panels_sheet"] if m["panels_sheet"] is not None else "",
                   m["cnc_tool"] or m["note"] or "",
                   m["per_sq_inch"] if m["per_sq_inch"] is not None else "",
                   m["sheet_thickness"] or "", m["pcb_price"] if m["pcb_price"] is not None else ""])
    out = _BIO()
    wb.save(out)
    return Response(out.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=SIZE_SHEET.xlsx"})


@app.route("/api/product/<int:pid>/delete", methods=["POST"])
@login_required
@admin_required
def product_delete_row(pid):
    """v4.03: SHEET me se hi model DELETE — PDF/BOM/production me use ho raha to block (USE ME)."""
    m = db.query("SELECT id, name FROM product_models WHERE id=?", (pid,), one=True)
    if not m:
        return jsonify(ok=False, err="missing", msg="Model pehle hi delete ho chuka."), 404
    used = []
    if db.query("SELECT COUNT(*) c FROM model_pdfs WHERE model_id=?", (pid,), one=True)["c"]:
        used.append("PDF")
    if db.query("SELECT COUNT(*) c FROM bom WHERE model_id=?", (pid,), one=True)["c"]:
        used.append("BOM")
    if db.query("SELECT COUNT(*) c FROM fg_production WHERE model_id=?", (pid,), one=True)["c"]:
        used.append("PRODUCTION")
    if used:
        return jsonify(ok=False, err="in_use", msg="Ye model USE ME hai (" + ", ".join(used) +
                       ") — pehle wo hatao, phir delete hoga.")
    db.execute("DELETE FROM product_models WHERE id=?", (pid,))
    return jsonify(ok=True, msg="Model SHEET se delete ho gaya.")


def _sheet_apply18(pid, j, log=True):
    """v4.18: SHEET cell-edit apply (+ purani value LOG — UNDO isi se wapas karta hai)."""
    sets, vals = [], []
    for k, v in j.items():
        if k not in _SHEET_FIELDS:
            if re.fullmatch(r"cc_[a-f0-9]{4}", str(k)):
                vals.append(str(v or "").strip())   # v4.23: custom column (text)
                sets.append(f"{k}=?")
            continue
        t = _SHEET_FIELDS[k]
        try:
            if t is str:
                vals.append(str(v or "").strip())
            elif str(v).strip() == "":
                vals.append(None)      # v4.14: cell khaali kiya -> BLANK save (0 nahi)
            elif t is int:
                vals.append(int(float(v)))
            else:
                vals.append(float(v))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Galat value: " + k), 400
        sets.append(f"{k}=?")
    if not sets:
        return jsonify(ok=False, error="Koi valid field nahi mila."), 400
    m = db.query("SELECT id FROM product_models WHERE id=?", (pid,), one=True)
    if not m:
        return jsonify(ok=False, error="Model nahi mila."), 404
    if log:
        db.execute("CREATE TABLE IF NOT EXISTS sheet_edit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "model_id INTEGER, model_name TEXT DEFAULT '', field TEXT DEFAULT '', "
                   "old_val TEXT DEFAULT '', new_val TEXT DEFAULT '', edited_on TEXT DEFAULT '')")
        olds = db.query("SELECT name, party_name, model_code, party_code, pcb_len, pcb_w, panel_len, panel_w, pcs_panel, cutting_len, cutting_w, x_qty, y_qty, panels_sheet, sheets, sheet_thickness, per_sq_inch, pcb_price, cnc_tool FROM product_models WHERE id=?", (pid,), one=True)
        for k in j:
            if k in _SHEET_FIELDS and k in olds.keys():
                _ov = olds[k]
                db.execute("INSERT INTO sheet_edit_log (model_id, model_name, field, old_val, new_val, edited_on) VALUES (?,?,?,?,?,?)",
                           (pid, (olds["name"] or ""), k, ("" if _ov is None else str(_ov)),
                            ("" if v is None else str(j[k])), _now_ist().isoformat()))
        db.execute("DELETE FROM sheet_edit_log WHERE id NOT IN (SELECT id FROM sheet_edit_log ORDER BY id DESC LIMIT 300)")
    vals.append(pid)
    db.execute(f"UPDATE product_models SET {', '.join(sets)} WHERE id=?", tuple(vals))
    return jsonify(ok=True)


@app.route("/api/product/<int:pid>/sheet_edit", methods=["POST"])
@login_required
@admin_required
def product_sheet_edit(pid):
    """v3.97: SHEET cell-edit — sirf whitelist fields, kuch invent nahi. v4.18: har edit LOG (UNDO)."""
    j = request.get_json(silent=True) or {}
    # v4.23: ROW- placeholder row me MODEL NO. bhara to model ka naam bhi wahi ho jaye (PO-match ke liye)
    # v4.30: CUTTING SIZE/UPS manual edit -> model ka Engine-override ON (reload par bhi yahi dikhega)
    if any(k in _CUT_GROUP30 for k in (j or {})):
        _ensure_cut_manual30()
        try:
            db.execute("UPDATE product_models SET cut_manual=1 WHERE id=?", (pid,))
        except Exception:
            pass
    mc = str(j.get("model_code") or "").strip()
    if mc:
        m0 = db.query("SELECT name FROM product_models WHERE id=?", (pid,), one=True)
        if m0 and (m0["name"] or "").startswith("ROW-"):
            dup = db.query("SELECT id FROM product_models WHERE name=? AND id!=?", (mc, pid), one=True)
            if not dup:
                j = dict(j)
                j["name"] = mc
    return _sheet_apply18(pid, j, log=True)


@app.route("/api/products/sheet/undo", methods=["POST"])
@login_required
@admin_required
def sheet_undo():
    """v4.18: UNDO — pichhla cell-edit wapas (galti se type/save ho gaya to, Excel-jaisa)."""
    db.execute("CREATE TABLE IF NOT EXISTS sheet_edit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "model_id INTEGER, model_name TEXT DEFAULT '', field TEXT DEFAULT '', "
               "old_val TEXT DEFAULT '', new_val TEXT DEFAULT '', edited_on TEXT DEFAULT '')")
    last = db.query("SELECT * FROM sheet_edit_log ORDER BY id DESC LIMIT 1", one=True)
    if not last:
        return jsonify(ok=True, msg="Undo ke liye koi edit history nahi — sab theek hai.")
    field = last["field"]
    if field not in _SHEET_FIELDS or field == "name":
        db.execute("DELETE FROM sheet_edit_log WHERE id=?", (last["id"],))
        return jsonify(ok=True, msg="Skip — internal edit.")
    raw = last["old_val"] if last["old_val"] is not None else ""
    r = _sheet_apply18(last["model_id"], {field: raw}, log=False)
    ok = bool((r.get_json() or {}).get("ok")) if hasattr(r, "get_json") else False
    db.execute("DELETE FROM sheet_edit_log WHERE id=?", (last["id"],))
    if not ok:
        return jsonify(ok=True, msg="Skip — model ab nahi hai.")
    t = _SHEET_FIELDS[field]
    if raw == "":
        disp = ""
    elif t is str:
        disp = raw
    else:
        try:
            disp = "%g" % float(raw)
        except (TypeError, ValueError):
            disp = raw
    lbl = _SHEET_LABELS.get(field, field)
    return jsonify(ok=True, msg=f"UNDO: {last['model_name']} — {lbl} wapas '{disp or 'khaali'}' ho gaya",
                   model_id=last["model_id"], field=field, disp=disp)


@app.route("/api/products/sheet/prefs", methods=["GET", "POST"])
@login_required
@admin_required
def sheet_prefs():
    """v4.18: SHEET column-alignment prefs (DB me — har device par same)."""
    import json as _jp
    db.execute("CREATE TABLE IF NOT EXISTS sheet_prefs (k TEXT PRIMARY KEY, v TEXT DEFAULT '')")
    if request.method == "GET":
        r = db.query("SELECT v FROM sheet_prefs WHERE k='align'", one=True)
        doc = {}
        if r and r["v"]:
            try:
                doc = _jp.loads(r["v"])
            except Exception:
                doc = {}
        if not isinstance(doc, dict) or "align" not in doc:
            doc = {"align": doc if isinstance(doc, dict) else {}}
        return jsonify(ok=True, prefs=doc)
    j = request.get_json(silent=True) or {}
    doc = {}
    r = db.query("SELECT v FROM sheet_prefs WHERE k='align'", one=True)
    if r and r["v"]:
        try:
            doc = _jp.loads(r["v"])
        except Exception:
            doc = {}
    if not isinstance(doc, dict) or "align" not in doc:
        doc = {"align": doc if isinstance(doc, dict) else {}}   # v4.18 purana flat-align doc
    # v4.21: align + colw + rowh + rowh_def — ek hi prefs doc (top-level keys merge)
    for k in ("align", "colw", "rowh", "colhide", "colLabels", "colOrder", "colMerge"):   # v4.32: merge-spans   # v4.31: header rename + column order
        if k in j and isinstance(j[k], dict):
            if not j[k]:
                doc[k] = {}   # v4.21: khaali dict = CLEAR (merge-nop nahi — Reset kaam kare)
                continue
            _b = doc.get(k) if isinstance(doc.get(k), dict) else {}
            for _fk, _fv in j[k].items():
                if k == "align":
                    if (_fk in _SHEET_FIELDS or _fk == "fg_stock") and _fv in ("left", "center", "right", ""):
                        _b[_fk] = _fv
                else:
                    _b[_fk] = _fv
            doc[k] = _b
    if "rowh_def" in j:
        try:
            doc["rowh_def"] = max(20, min(120, int(j["rowh_def"])))
        except (TypeError, ValueError):
            pass
    db.execute("INSERT INTO sheet_prefs (k, v) VALUES ('align', ?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (_jp.dumps(doc),))
    return jsonify(ok=True, prefs=doc)


@app.route("/api/products/sheet/delete_many", methods=["POST"])
@login_required
@admin_required
def sheet_delete_many():
    """v4.22: MULTIPLE rows delete — checkbox select karke; USE-ME guard single-delete jaisa (batch queries)."""
    j = request.get_json(silent=True) or {}
    ids = []
    for x in (j.get("ids") or [])[:500]:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    if not ids:
        return jsonify(ok=False, error="Pehle rows tick karo (checkbox se).")
    ph = ",".join("?" * len(ids))
    used_ids = set()
    for tbl in ("model_pdfs", "bom", "fg_production"):
        for r in db.query(f"SELECT model_id FROM {tbl} WHERE model_id IN ({ph})", tuple(ids)):
            used_ids.add(r["model_id"])
    names = {r["id"]: r["name"] for r in db.query(f"SELECT id, name FROM product_models WHERE id IN ({ph})", tuple(ids))}
    deleted, blocked = [], []
    for pid in ids:
        if pid in used_ids:
            blocked.append({"id": pid, "name": names.get(pid, str(pid))})
        elif pid in names:
            db.execute("DELETE FROM product_models WHERE id=?", (pid,))
            deleted.append(pid)
    msg = f"{len(deleted)} model delete ho gaye."
    if blocked:
        msg += f" | {len(blocked)} USE-ME (PDF/BOM/PRODUCTION) block: " + ", ".join(b["name"] for b in blocked[:6])
    return jsonify(ok=True, deleted=len(deleted), blocked=blocked, msg=msg)


def _ensure_cut_manual30():
    """v4.30: cut_manual flag column (PRAGMA self-heal) — manual edit ka Engine-override."""
    try:
        _h30 = {r["name"] for r in db.query("PRAGMA table_info(product_models)")}
        if "cut_manual" not in _h30:
            db.execute("ALTER TABLE product_models ADD COLUMN cut_manual INTEGER DEFAULT 0")
    except Exception:
        pass


def _ensure_sheet31():
    """v4.32: sheet_id column (PRAGMA self-heal) — Excel-jaisi sheets (tabs)."""
    try:
        _h32 = {r["name"] for r in db.query("PRAGMA table_info(product_models)")}
        if "sheet_id" not in _h32:
            db.execute("ALTER TABLE product_models ADD COLUMN sheet_id INTEGER DEFAULT 1")
    except Exception:
        pass


_CUT_GROUP30 = ("cutting_len", "cutting_w", "x_qty", "y_qty", "panels_sheet")


@app.route("/api/products/sheet/calc_cut", methods=["POST"])
@login_required
@admin_required
def sheet_calc_cut97():
    """v4.27 — SAB models ka CUTTING SIZE (4A/N/4B/N) + UPS GANG engine se recalc,
    DB write-back (PO/jobcard bhi sahi panels_sheet uthayenge). Data kam wale skip."""
    upd, skip = 0, 0
    for m in db.query("SELECT * FROM product_models"):
        c = _model_cut_calc(dict(m))
        if not c or not c["x_qty"] or not c["y_qty"] or not c["ups"]:
            skip += 1
            continue
        db.execute("UPDATE product_models SET cutting_len=?, cutting_w=?, x_qty=?, y_qty=?, panels_sheet=?, cut_manual=0 WHERE id=?",
                   (c["cutting_len"], c["cutting_w"], c["x_qty"], c["y_qty"], c["pps"], m["id"]))
        upd += 1
    return jsonify(ok=True, updated=upd, skipped=skip)


@app.route("/api/products/sheet/sheet_ops", methods=["POST"])
@login_required
@admin_required
def sheet_ops32():
    """v4.32: EXCEL JAISI SHEETS — add / rename / del (del par models pehli sheet me chale)."""
    j = request.get_json(silent=True) or {}
    act = (j.get("action") or "").strip()
    db.execute("CREATE TABLE IF NOT EXISTS sheet_prefs (k TEXT PRIMARY KEY, v TEXT DEFAULT '')")
    doc = {}
    r = db.query("SELECT v FROM sheet_prefs WHERE k='align'", one=True)
    if r and r["v"]:
        try:
            doc = json.loads(r["v"])
        except Exception:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    sheets = doc.get("sheets") if isinstance(doc.get("sheets"), list) and doc.get("sheets") else [{"id": 1, "name": "SHEET 1"}]
    sheets = [{"id": int(s["id"]), "name": str(s.get("name") or ("SHEET " + str(s["id"])))} for s in sheets]
    if act == "add":
        nm = (j.get("name") or "").strip()[:24] or ("SHEET " + str(len(sheets) + 1))
        sheets.append({"id": max(s["id"] for s in sheets) + 1, "name": nm})
        doc["sheets"] = sheets
    elif act == "rename":
        sid = int(j.get("id") or 0)
        nm = (j.get("name") or "").strip()[:24]
        if not nm:
            return jsonify(ok=False, error="Naam likho.")
        for s in sheets:
            if s["id"] == sid:
                s["name"] = nm
        doc["sheets"] = sheets
    elif act == "del":
        sid = int(j.get("id") or 0)
        if len(sheets) <= 1:
            return jsonify(ok=False, error="Aakhri sheet delete nahi hoti.")
        sheets = [s for s in sheets if s["id"] != sid]
        db.execute("UPDATE product_models SET sheet_id=? WHERE IFNULL(sheet_id,1)=?", (sheets[0]["id"], sid))
        doc["sheets"] = sheets
    else:
        return jsonify(ok=False, error="Kya karna hai? (add/rename/del)")
    db.execute("INSERT INTO sheet_prefs (k, v) VALUES ('align', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (json.dumps(doc),))
    return jsonify(ok=True, sheets=sheets)


@app.route("/api/products/sheet/calc_update", methods=["POST"])
@login_required
@admin_required
def sheet_calc_update33():
    """v4.33: PCB CALCULATOR -> MODEL UPDATE — RATE (per_sq_inch) + PRICE (pcb_price) seedha SIZE MASTER me."""
    j = request.get_json(silent=True) or {}
    try:
        mid = int(j.get("model_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    if not mid:
        return jsonify(ok=False, error="Pehle MODEL SE BHARO — kisi model ke saath hi update hota hai.")
    m = db.query("SELECT id, name FROM product_models WHERE id=?", (mid,), one=True)
    if not m:
        return jsonify(ok=False, error="Model nahi mila.")
    try:
        rate = float(j.get("rate") or 0)
        price = float(j.get("price") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="RATE/PRICE number honi chahiye.")
    if rate <= 0 and price <= 0:
        return jsonify(ok=False, error="RATE ya PRICE me se koi ek bharo.")
    db.execute("UPDATE product_models SET per_sq_inch=?, pcb_price=? WHERE id=?", (rate, price, mid))
    return jsonify(ok=True, model=m["name"], rate=round(rate, 3), price=round(price, 2))


@app.route("/api/products/sheet/custom_col", methods=["POST"])
@login_required
@admin_required
def sheet_custom_col():
    """v4.23: SHEET me APNA column add/remove (Excel jaisa) — prefs registry + ALTER, self-heal."""
    import json as _jc
    db.execute("CREATE TABLE IF NOT EXISTS sheet_prefs (k TEXT PRIMARY KEY, v TEXT DEFAULT '')")
    j = request.get_json(silent=True) or {}
    doc = {}
    r = db.query("SELECT v FROM sheet_prefs WHERE k='align'", one=True)
    if r and r["v"]:
        try:
            doc = _jc.loads(r["v"])
        except Exception:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    if j.get("remove_id"):
        rid = str(j["remove_id"])
        doc["custom"] = [c for c in (doc.get("custom") or []) if str(c.get("id")) != rid]
        db.execute("INSERT INTO sheet_prefs (k, v) VALUES ('align', ?) "
                   "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (_jc.dumps(doc),))
        return jsonify(ok=True, custom=doc.get("custom") or [])
    name = (str(j.get("name") or "").strip())[:24]
    if not name:
        return jsonify(ok=False, error="Column ka naam likho.")
    if len(doc.get("custom") or []) >= 6:
        return jsonify(ok=False, error="Max 6 custom columns (pehle koi hatao).")
    cid = os.urandom(2).hex()
    try:
        db.execute(f"ALTER TABLE product_models ADD COLUMN cc_{cid} TEXT DEFAULT ''")
    except Exception as e:
        return jsonify(ok=False, error="Column ban nahi paya: " + str(e))
    doc["custom"] = (doc.get("custom") or []) + [{"id": cid, "name": name}]
    db.execute("INSERT INTO sheet_prefs (k, v) VALUES ('align', ?) "
               "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (_jc.dumps(doc),))
    return jsonify(ok=True, id=cid, name=name)


@app.route("/api/products/sheet/add_row", methods=["POST"])
@login_required
@admin_required
def sheet_add_row():
    """v4.23: SHEET me khaali ROW add (Excel jaisa) — ROW- placeholder; MODEL NO. bharte hi naam ban jata hai."""
    db.ensure_product_models()
    _ensure_sheet31()
    _jb32 = request.get_json(silent=True) or {}
    try:
        _sid32 = int(_jb32.get("sheet") or 1)
    except (TypeError, ValueError):
        _sid32 = 1
    nm = "ROW-" + _now_ist().strftime("%Y%m%d%H%M%S")
    while db.query("SELECT id FROM product_models WHERE name=?", (nm,), one=True):
        nm += "x"
    db.execute("INSERT INTO product_models (name, created_on, sheet_id) VALUES (?,?,?)",
               (nm, _today_ist().isoformat(), _sid32))
    r = db.query("SELECT id FROM product_models WHERE name=?", (nm,), one=True)
    return jsonify(ok=True, id=r["id"], name=nm)


@app.route("/api/po/file/<int:fid>")
@login_required
@admin_required
def po_file_view(fid):
    _po_ensure()
    r = db.query("SELECT filename, mime, data64 FROM po_files WHERE id=?", (fid,), one=True)
    if not r:
        return "File nahi mili.", 404
    try:
        raw = base64.b64decode(r["data64"])
    except Exception:
        raw = b""
    if not raw:
        return "File khaali hai — dobara upload karo.", 404
    fn = (r["filename"] or "po").replace('"', "")
    return Response(raw, mimetype=r["mime"] or "application/octet-stream",
                    headers={"Content-Disposition": "inline; filename=\"" + fn + "\"" })


@app.route("/api/po/save", methods=["POST"])
@login_required
@admin_required
def po_save_line():
    """Ek S.No. = ek job card. Kuch bhi invent nahi — party/qty/model zaroori.
    v3.94: auto=1 (PO direct-save, bina form) — party/qty khali chalega, VERIFY par bharte hain."""
    _po_ensure()
    f = request.form
    auto = (f.get("auto") or "") == "1"
    party = (f.get("party") or "").strip() or (("PO-" + ((f.get("po_no") or "WhatsApp").strip() or "WhatsApp")) if auto else "")
    qty = int(f.get("qty") or 0)
    po_no = (f.get("po_no") or "WhatsApp").strip() or "WhatsApp"
    try:
        po_sno = int(f.get("po_sno") or 0)
    except ValueError:
        po_sno = 0
    if not party:
        return jsonify(ok=False, error="Party chuno."), 400
    if qty <= 0 and not auto:
        return jsonify(ok=False, error="Quantity (PCS) zaroori hai."), 400
    if po_sno <= 0:
        return jsonify(ok=False, error="PO S.No. likho."), 400
    try:
        fid = int(f.get("file_id") or 0)
    except (TypeError, ValueError):
        fid = 0
    if fid and po_no.upper() != "WHATSAPP":   # v3.86b: file folder = asli PO no (verify window ko file mile)
        try:
            db.execute("UPDATE po_files SET po_no=? WHERE id=?", (po_no, fid))
        except Exception:
            pass
    mid = int(f.get("model_id") or 0)
    pm = db.query("SELECT * FROM product_models WHERE id=?", (mid,), one=True) if mid else None
    tmodel = (f.get("model") or "").strip()
    if not pm and not tmodel and not auto:
        return jsonify(ok=False, error="MODEL NO chuno ya likho (Size Master se aayega)."), 400
    model_code = pm["model_code"] if pm and pm["model_code"] else (pm["name"] if pm else tmodel)
    if pm and party.startswith("PO-") and not (f.get("party") or "").strip():   # v4.26: party name master se
        _mp = (pm["party_name"] or pm["party"] or "").strip()
        if _mp:
            party = _mp
    party_model = (f.get("party_model") or "").strip()
    party_code = (f.get("party_code") or "").strip()
    if pm:   # v4.26: PARTY MODEL/PARTY CODE SHEET MASTER se auto-fetch (khali hone par)
        if not party_model and pm["party_code"]:
            party_model = (pm["party_code"] or "").strip()
        if not party_code and pm["party_code"]:
            party_code = (pm["party_code"] or "").strip()
    prod = (f"{model_code}{(' · ' + party_model) if party_model else ''}"
            if model_code else f"(S.No. {po_sno} — khud bharo)")
    x_size = float(f.get("x_size") or 0) or (float(pm["cutting_len"] or pm["panel_len"] or 0) if pm else 0)
    y_size = float(f.get("y_size") or 0) or (float(pm["cutting_w"] or pm["panel_w"] or 0) if pm else 0)
    ups = int(f.get("ups") or 0) or (int(pm["pcs_panel"] or 0) if pm else 0)
    qty_panel = math.ceil(qty / ups) if ups else 0
    extra = int(f.get("extra_panels") or 0)
    if qty_panel:
        qty_panel += extra
    rate = f.get("rate") or ""
    if not rate and pm and pm["pcb_price"]:   # v4.26: PRICE master se
        rate = str(pm["pcb_price"])
    count = db.query("SELECT COUNT(*) c FROM orders", one=True)["c"]
    oid = db.execute(
        "INSERT INTO orders (order_no, party, board, product, qty, value, current_process, status, progress, "
        "priority, delivery_date, operator, started_qty, finished_qty, created_on, qty_panel, pcs_panel, po_no, po_sno) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"#{count + 1}", party, "Single Side", prod, qty,
         float(rate) * qty if rate.replace(".", "", 1).isdigit() and rate else 0,
         "", "pending", 0, (f.get("priority") or "normal"),  # v3.85: PO card REVIEW me — approve par process me
         (f.get("due") or ""), "", 0, 0,
         (f.get("odate") or _today_ist().isoformat()), qty_panel, ups, po_no, po_sno))
    db.execute(
        "INSERT OR IGNORE INTO jobcard (order_id, party_model, model, odate, exp_delivery, price, total_qty, "
        "actual_pcb_x, actual_pcb_y, panel_x, panel_y, panels_per_sheet, sheets, pcs_panel, sheet_len, sheet_w, "
        "sheet_material, sheet_thickness, po_no, party_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (oid, party_model or model_code, model_code, (f.get("odate") or _today_ist().isoformat()),
         (f.get("due") or ""), float(rate) if rate.replace(".", "", 1).isdigit() and rate else 0, qty,
         float(pm["pcb_len"] or 0) if pm else 0, float(pm["pcb_w"] or 0) if pm else 0,
         x_size, y_size,
         (int(pm["panels_sheet"] or 0) if pm else 0),
         (int(pm["sheets"] or 0) if pm else 0),
         ups, float(pm["sheet_len"] or 0) if pm else 0, float(pm["sheet_w"] or 0) if pm else 0,
         (f.get("material") or ""), (pm["sheet_thickness"] if pm else ""), po_no,
         party_code))
    db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, details, ts) VALUES (?,?,?,?,?,?)",
               (oid, "PO", "created", session.get("user_name") or "admin",
                f"PO S.No. {po_sno} · {po_no}", _now_dt()))
    ono = db.query("SELECT order_no FROM orders WHERE id=?", (oid,), one=True)["order_no"]
    return jsonify(ok=True, order_id=oid, order_no=ono)


@app.route("/api/po/draft", methods=["POST"])
@login_required
@admin_required
def po_draft():
    """v3.85 — TEMPORARY JOB CARD: naye blue format ka live draft (save se pehle).
    Kuch invent nahi: sirf form/model-master ki values dikhti hain."""
    _po_ensure()
    f = request.form
    mid = int(f.get("model_id") or 0)
    pm = db.query("SELECT * FROM product_models WHERE id=?", (mid,), one=True) if mid else None
    model_code = (f.get("model") or "").strip() or ((pm["model_code"] or pm["name"]) if pm else "")
    party = (f.get("party") or "").strip()
    qty = int(f.get("qty") or 0)
    rate = f.get("rate") or ""
    x = float(f.get("x_size") or 0) or (float(pm["cutting_len"] or pm["panel_len"] or 0) if pm else 0)
    y = float(f.get("y_size") or 0) or (float(pm["cutting_w"] or pm["panel_w"] or 0) if pm else 0)
    ups = int(f.get("ups") or 0) or (int(pm["pcs_panel"] or 0) if pm else 0)
    sheet_len = float(pm["sheet_len"] or 0) if pm else float(f.get("sheet_len") or 0)
    sheet_w = float(pm["sheet_w"] or 0) if pm else float(f.get("sheet_w") or 0)
    xa = int(sheet_len // x) if (x > 0 and sheet_len > 0) else 0   # sheet me panel across
    yr = int(sheet_w // y) if (y > 0 and sheet_w > 0) else 0       # rows
    pps = xa * yr if (xa and yr) else 0                            # panels/sheet
    per_sheet_pcb = ups * pps if (ups and pps) else 0
    panels = math.ceil(qty / ups) if ups else 0
    sheets = math.ceil(qty / per_sheet_pcb) if per_sheet_pcb else 0
    pcbx = float(pm["pcb_len"] or 0) if pm else 0
    pcby = float(pm["pcb_w"] or 0) if pm else 0
    jc_stub = {"party_model": (f.get("party_model") or "").strip(), "model": model_code,
               "odate": (f.get("odate") or _today_ist().isoformat()), "order_via": "",
               "po_no": (f.get("po_no") or ""), "exp_delivery": (f.get("due") or ""),
               "created_by": session.get("user_name") or "", "actual_pcb_x": pcbx, "actual_pcb_y": pcby,
               "x_size": x, "y_size": y, "x_qty": xa, "y_qty": yr, "pcb_gap": 0,
               "cnc_margin_x": 0, "cnc_margin_y": 0, "panel_x": x, "panel_y": y,
               "panels_per_sheet": pps, "pcs_panel": ups, "qty_panel": panels, "sheets": sheets,
               "sheet_len": sheet_len, "sheet_w": sheet_w,
               "sheet_material": (f.get("material") or ""), "sheet_thickness": (pm["sheet_thickness"] if pm else ""),
               "total_qty": qty, "rs_pcb": rate, "price": (float(rate) * qty if rate.replace(".", "", 1).isdigit() and rate else 0),
               "short_qty": 0, "short_reason": "", "handover_sign": "",
               "instructions": (f.get("notes") or ""), "v_grooving": "",
               "party_code": (f.get("party_code") or ""), "copper_finish": ""}
    order_stub = {"order_no": "(PO DRAFT)", "party": party}
    html = render_template("_jobcard_print.html", order=order_stub, jc=jc_stub, mix79=None, procs=[])
    return jsonify(ok=True, html=html)


@app.route("/po/verify/<int:oid>")
@login_required
@admin_required
def po_verify(oid):
    """v3.85 — VERIFY: saved job card (naya format) + PO window + Design window (movable) + APPROVE."""
    _po_ensure()
    order = db.query("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not order:
        return "Order nahi mili.", 404
    jc = ensure_jobcard(oid)
    mix85 = None
    try:
        if (jc["actual_pcb_x"] or 0) > 0 and (jc["actual_pcb_y"] or 0) > 0 \
                and (jc["sheet_len"] or 0) > 0 and (jc["sheet_w"] or 0) > 0:
            _px = (jc["x_size"] or jc["panel_x"] or 0)
            _py = (jc["y_size"] or jc["panel_y"] or 0)
            _f = {"use": "1", "pcb_len": str(_px), "pcb_w": str(_py), "pcbs_x": "1", "pcbs_y": "1",
                  "gap_x": "0", "gap_y": "0", "border_l": "0", "border_r": "0", "border_t": "0", "border_b": "0",
                  "gang_x": "1", "gang_y": "1", "sheet_len": str(jc["sheet_len"]), "sheet_w": str(jc["sheet_w"]),
                  "kerf_x": str(jc.get("kerf_x") or 0), "kerf_y": str(jc.get("kerf_y") or 0),
                  "sheets": "1", "panel_len": "0", "panel_w": "0"}
            _r = compute_layout(_f)
            if _r and _r.get("best") == "mixed" and (_r.get("mixed_n") or 0) > 0 and (_r.get("mixed_m") or 0) > 0:
                mix85 = {"xn": _r["mixed_n"], "xp": _r["per_normal"], "yn": _r["mixed_m"], "yp": _r["per_rot"],
                         "xt": _r["mixed_n"] * (_r["per_normal"] or 0), "yt": _r["mixed_m"] * (_r["per_rot"] or 0),
                         "total": _r["panels_per_sheet"] or 0}
    except Exception:
        mix85 = None
    pfile = db.query("SELECT id, filename, mime FROM po_files WHERE po_no=? ORDER BY id DESC LIMIT 1",
                     (order["po_no"] or "",), one=True) if (order["po_no"] or "") else None
    pm = None
    if jc["model"]:
        pm = db.query("SELECT id, name, model_code, design64, design_mime FROM product_models WHERE model_code=? OR name=? ORDER BY id DESC LIMIT 1",
                      (jc["model"], jc["model"]), one=True)
    design = {"mid": pm["id"] if pm else 0,
              "has": bool(pm and pm["id"] and pm["design64"]),
              "mime": (pm["design_mime"] if pm else "") or "",
              "url": (f"/api/model/design/{pm['id']}") if pm else ""}
    models = [dict(m) for m in db.query(
        "SELECT id, name, model_code, pcb_len, pcb_w, panel_len, panel_w, pcs_panel, "
        "cutting_len, cutting_w, sheet_len, sheet_w, sheet_thickness, party_name, "
        "party_code, pcb_price "
        "FROM product_models ORDER BY name")]
    in_process = (order["status"] == "running") or bool(order["current_process"])
    return render_template("po_verify.html", active="orders", order=order, jc=jc, mix79=mix85,
                           pfile=pfile, design=design, in_process=in_process,
                           models=models, models_json=json.dumps(models),
                           first_step=PROCESS_STEPS[0])


@app.route("/api/po/update/<int:oid>", methods=["POST"])
@login_required
@admin_required
def po_update_card(oid):
    """v3.86 — VERIFY page ka editable job card save. MODEL select = Size Master se auto-fill.
    Kuch invent nahi: jo bheja wahi update, master ke sizes sirf model select hone par."""
    _po_ensure()
    o = db.query("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(ok=False, error="Order nahi mili."), 404
    jc = ensure_jobcard(oid)
    f = request.form
    gv = lambda k: (f.get(k) or "").strip()

    # ---- MODEL: select-id aaya to Size Master se AUTO-FILL (sizes/UPS/thickness/PCB)
    mid = int(f.get("model_id") or 0)
    pm = db.query("SELECT * FROM product_models WHERE id=?", (mid,), one=True) if mid else None
    model_code = gv("model")
    if pm:
        model_code = pm["model_code"] or pm["name"]
        for col, src in (("x_size", "cutting_len"), ("y_size", "cutting_w"),
                         ("panel_x", "panel_len"), ("panel_y", "panel_w"),
                         ("actual_pcb_x", "pcb_len"), ("actual_pcb_y", "pcb_w"),
                         ("sheet_len", "sheet_len"), ("sheet_w", "sheet_w")):
            if pm[src]:
                db.execute(f"UPDATE jobcard SET {col}=? WHERE id=?", (float(pm[src]), jc["id"]))
        if pm["pcs_panel"]:
            db.execute("UPDATE jobcard SET pcs_panel=? WHERE id=?", (int(pm["pcs_panel"]), jc["id"]))
        if pm["panels_sheet"]:
            db.execute("UPDATE jobcard SET panels_per_sheet=? WHERE id=?", (int(pm["panels_sheet"]), jc["id"]))
        if pm["sheets"]:
            db.execute("UPDATE jobcard SET sheets=? WHERE id=?", (int(pm["sheets"]), jc["id"]))
        if pm["sheet_thickness"]:
            db.execute("UPDATE jobcard SET sheet_thickness=? WHERE id=?", (pm["sheet_thickness"], jc["id"]))
    elif model_code:
        db.execute("UPDATE jobcard SET model=? WHERE id=?", (model_code, jc["id"]))

    # ---- simple jobcard fields
    if gv("po_no"):   # v4.05: PO NO edit-window se badla to orders par bhi
        db.execute("UPDATE orders SET po_no=? WHERE id=?", (gv("po_no"), oid))
    if gv("odate"):
        db.execute("UPDATE orders SET created_on=? WHERE id=?", (gv("odate"), oid))
    _jmap = {"odate": "odate", "order_via": "order_via", "party_model": "party_model", "po_no": "po_no",
             "party_code": "party_code", "sheet_thickness": "sheet_thickness",
             "exp_delivery": "exp_delivery", "sheet_material": "sheet_material", "checked_by": "checked_by",
             "created_by": "created_by", "instructions": "instructions", "short_reason": "short_reason",
             "handover_sign": "handover_sign"}
    for fk, col in _jmap.items():
        if fk in f:
            db.execute(f"UPDATE jobcard SET {col}=? WHERE id=?", (gv(fk), jc["id"]))
    if pm and pm["party_code"]:   # v4.26: PARTY MODEL/PARTY CODE master se (form me khali ho to)
        if not gv("party_model"):
            db.execute("UPDATE jobcard SET party_model=? WHERE id=?", (pm["party_code"], jc["id"]))
        if not gv("party_code"):
            db.execute("UPDATE jobcard SET party_code=? WHERE id=?", (pm["party_code"], jc["id"]))
    for fk, col in (("rs_pcb", "rs_pcb"), ("total_qty", "total_qty"), ("qty_panel", "qty_panel"),
                    ("pcs_panel", "pcs_panel"), ("sheets", "sheets"), ("short_qty", "short_qty")):
        if fk in f and gv(fk):
            try:
                db.execute(f"UPDATE jobcard SET {col}=? WHERE id=?", (float(gv(fk)), jc["id"]))
            except ValueError:
                pass

    # ---- panel "x×y" parse (X/Y mm) — "239.98mm X 1000mm-150 pcs" jaisa format bhi chalega
    pan = gv("panel")
    if pan:
        _pn = re.findall(r"[\d.]+", pan.replace("×", "x"))
        if len(_pn) >= 2:
            try:
                _xv = float(_pn[0]); _yv = float(_pn[1])
                db.execute("UPDATE jobcard SET x_size=?, y_size=?, panel_x=?, panel_y=? WHERE id=?",
                           (_xv, _yv, _xv, _yv, jc["id"]))
            except ValueError:
                pass

    # ---- SHEET strip parse: "METAL-1000x1200-1.5mm-25µ" -> material/w/l/thickness
    sf = gv("sheet_fmt")
    if sf and "-" in sf:
        _sp = [x.strip() for x in sf.split("-")]
        try:
            if _sp[0]:
                db.execute("UPDATE jobcard SET sheet_material=? WHERE id=?", (_sp[0], jc["id"]))
            _dm = re.findall(r"[\d.]+", _sp[1]) if len(_sp) > 1 else []
            if len(_dm) >= 2:
                db.execute("UPDATE jobcard SET sheet_w=?, sheet_len=? WHERE id=?",
                           (float(_dm[0]), float(_dm[1]), jc["id"]))
            if len(_sp) > 2 and _sp[2]:
                db.execute("UPDATE jobcard SET sheet_thickness=? WHERE id=?", (_sp[2].upper(), jc["id"]))
        except (ValueError, IndexError):
            pass

    # ---- UPS badla to panels recompute
    ups = db.query("SELECT pcs_panel, total_qty FROM jobcard WHERE id=?", (jc["id"],), one=True)
    if ups["pcs_panel"] and ups["total_qty"]:
        db.execute("UPDATE jobcard SET qty_panel=? WHERE id=?",
                   (math.ceil(ups["total_qty"] / ups["pcs_panel"]), jc["id"]))

    # ---- orders sync
    qty = int(float(gv("total_qty") or 0)) if gv("total_qty") else None
    order_no = gv("order_no").lstrip("#")
    _ojc = db.query("SELECT pcs_panel, qty_panel, model, party_model FROM jobcard WHERE id=?", (jc["id"],), one=True)
    db.execute("""UPDATE orders SET order_no=COALESCE(?,order_no), party=COALESCE(?,party), po_no=COALESCE(?,po_no),
                  priority=COALESCE(?,priority), delivery_date=COALESCE(?,delivery_date), qty=COALESCE(?,qty),
                  product=COALESCE(?,product), pcs_panel=COALESCE(?,pcs_panel), qty_panel=COALESCE(?,qty_panel)
                  WHERE id=?""",
               (("#" + order_no) if order_no else None, gv("party") or None, gv("po_no") or None,
                gv("priority") or None, gv("exp_delivery") or None, qty,
                (model_code + ((" · " + gv("party_model")) if gv("party_model") and gv("party_model") != model_code else "")) if model_code else None,
                int(_ojc["pcs_panel"] or 0) or None, int(_ojc["qty_panel"] or 0) or None, oid))
    db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, details, ts) VALUES (?,?,?,?,?,?)",
               (oid, "PO", "edited", session.get("user_name") or "admin",
                "VERIFY page se edit + save", _now_dt()))
    return jsonify(ok=True)


@app.route("/api/po/approve/<int:oid>", methods=["POST"])
@login_required
@admin_required
def po_approve(oid):
    """v3.85 — APPROVE: job card process me aa jata hai (pehla step + running)."""
    _po_ensure()
    o = db.query("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not o:
        return jsonify(ok=False, error="Order nahi mili."), 404
    first = PROCESS_STEPS[0]
    db.execute("UPDATE orders SET status='running', current_process=? WHERE id=?", (first, oid))
    db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, details, ts) VALUES (?,?,?,?,?,?)",
               (oid, first, "approved", session.get("user_name") or "admin",
                "PO verify + APPROVE — process me aa gaya", _now_dt()))
    return jsonify(ok=True, process=first)


@app.route("/api/model/design/<int:mid>", methods=["GET"])
@login_required
@admin_required
def model_design_get(mid):
    """v3.85 — Size Master ka PCB design (image/PDF) dikhao."""
    _po_ensure()
    r = db.query("SELECT design64, design_mime, name FROM product_models WHERE id=?", (mid,), one=True)
    if not r or not r["design64"]:
        return "Design nahi hai.", 404
    try:
        raw = base64.b64decode(r["design64"])
    except Exception:
        return "Design kharab hai.", 404
    return Response(raw, mimetype=r["design_mime"] or "application/octet-stream",
                    headers={"Content-Disposition": "inline"})


@app.route("/api/model/design/<int:mid>", methods=["POST"])
@login_required
@admin_required
def model_design_post(mid):
    """v3.85 — Size Master par design upload (JPG/PNG/WebP/PDF, 10MB)."""
    _po_ensure()
    pm = db.query("SELECT id FROM product_models WHERE id=?", (mid,), one=True)
    if not pm:
        return jsonify(ok=False, error="Size Master nahi mila."), 404
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(ok=False, error="File nahi mili."), 400
    blob = f.read()
    if len(blob) > 10 * 1024 * 1024:
        return jsonify(ok=False, error="File 10MB se chhoti honi chahiye."), 400
    ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
    if ext not in ("pdf", "jpg", "jpeg", "png", "webp"):
        return jsonify(ok=False, error="Sirf PDF / JPG / PNG / WebP."), 400
    mime = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}[ext]
    db.execute("UPDATE product_models SET design64=?, design_mime=? WHERE id=?",
               (base64.b64encode(blob).decode("ascii"), mime, mid))
    return jsonify(ok=True)



# ---------------------------------------------------------------- employees
@app.route("/dispatch/<int:dl_id>/photo")
@login_required
def dispatch_photo(dl_id):
    d = db.query("SELECT photo_data, photo_mime FROM dispatch_log WHERE id=?", (dl_id,), one=True)
    if not d or not d["photo_data"]:
        return "No photo", 404
    return Response(d["photo_data"], mimetype=d["photo_mime"] or "image/png")


def _back_redirect(fallback):
    """Wahi page par wapas jao (referrer) — token pehle se ho to wahi rakhna."""
    back = request.referrer or fallback
    if "token=" not in back:
        tok = request.values.get("token")
        if tok:
            sep = "&" if "?" in back else "?"
            back = f"{back}{sep}token={tok}"
    return redirect(back)


@app.route("/dispatch/<int:dl_id>/attach_photo", methods=["POST"])
@login_required
def dispatch_attach_photo(dl_id):
    """Existing dispatch entry par photo/screenshot attach ya replace karo."""
    d = db.query("SELECT * FROM dispatch_log WHERE id=?", (dl_id,), one=True)
    if not d:
        flash("Dispatch entry nahi mili.", "error")
        return _back_redirect(url_for("reports"))
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        flash("Pehle photo/screenshot file chuno.", "error")
        return _back_redirect(url_for("reports"))
    ext = (photo.filename.rsplit(".", 1)[-1] if "." in photo.filename else "").lower()
    blob = photo.read()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic") or len(blob) > 3 * 1024 * 1024:
        flash("Sirf image file chalegi (max 3MB).", "error")
        return _back_redirect(url_for("reports"))
    db.execute("UPDATE dispatch_log SET photo_name=?, photo_mime=?, photo_data=? WHERE id=?",
               (photo.filename, photo.mimetype or "image/png", blob, dl_id))
    flash("📷 Dispatch entry par photo save ho gayi ✅", "success")
    return _back_redirect(url_for("reports"))


@app.route("/employees/provision", methods=["POST"])
@login_required
@admin_required
def employees_provision():
    db.provision_employee_logins()
    flash("Sab employees ke login accounts ban gaye ✅ (username = pehla naam, password shivaya@123).", "success")
    return redirect_with_token(url_for("employees"))


@app.route("/employees/<int:emp_id>/reset_password", methods=["POST"])
@login_required
@admin_required
def employee_reset_password(emp_id):
    new_pw = (request.form.get("new_password") or "").strip() or "shivaya@123"
    u = db.query("SELECT * FROM users WHERE employee_id=?", (emp_id,), one=True)
    if not u:
        db.provision_employee_logins()
        u = db.query("SELECT * FROM users WHERE employee_id=?", (emp_id,), one=True)
    if u:
        db.execute("UPDATE users SET password=? WHERE id=?", (new_pw, u["id"]))
        flash(f"'{u['username']}' ka password reset ho gaya ✅", "success")
    else:
        flash("Login account nahi mila.", "error")
    return redirect_with_token(url_for("employees"))


@app.route("/employees/<int:emp_id>/login_edit", methods=["POST"])
@login_required
def employee_login_edit(emp_id):
    """EMPLOYEE LOGIN ACCESS — username/password/role EDIT (sirf admin)."""
    if session.get("user_role") != "admin":
        flash("Sirf admin login access edit kar sakta hai.", "error")
        return redirect_with_token(url_for("employees"))
    username = (request.form.get("username") or "").strip().lower()
    password = (request.form.get("password") or "").strip()
    role = (request.form.get("role") or "operator").strip().lower()
    if role not in ("admin", "operator", "other"):
        role = "operator"
    u = db.query("SELECT * FROM users WHERE employee_id=?", (emp_id,), one=True)
    if not u:
        db.provision_employee_logins()
        u = db.query("SELECT * FROM users WHERE employee_id=?", (emp_id,), one=True)
    if not u:
        flash("Login account nahi mila — pehle 'Sab ke logins banao' dabao.", "error")
        return redirect_with_token(url_for("employees"))
    if not username:
        flash("Username khaali nahi ho sakta.", "error")
        return redirect_with_token(url_for("employees"))
    dup = db.query("SELECT * FROM users WHERE username=? AND id!=?", (username, u["id"]), one=True)
    if dup:
        flash(f"Username '{username}' pehle se kisi aur ka hai — dusra chuno.", "error")
        return redirect_with_token(url_for("employees"))
    final_pw = password if password else u["password"]
    db.execute("UPDATE users SET username=?, password=?, role=? WHERE id=?",
               (username, final_pw, role, u["id"]))
    flash(f"Login access update ho gaya ✅ {username} · role {role.upper()}" +
          (" · password bhi badla" if password else ""), "success")
    return redirect_with_token(url_for("employees"))


def _next_emp_code():
    """v3.74 — Employee Code BY DEFAULT: sabse bade EMP<number> se agla number (EMP001, EMP002, ...)."""
    mx = 0
    for r in db.query("SELECT emp_code FROM employees"):
        m = re.match(r"(?i)^EMP\s*0*(\d+)$", (r["emp_code"] or "").strip())
        if m:
            mx = max(mx, int(m.group(1)))
    return f"EMP{mx + 1:03d}"


@app.route("/employees", methods=["GET", "POST"])
@login_required
def employees():
    if request.method == "POST":
        f = request.form
        try:
            # v3.74 — code khaali chhoda ya kisi ka duplicate likha to APNE AAP agla number
            emp_code = (f.get("emp_code") or "").strip()
            auto_code = False
            if not emp_code or db.query("SELECT id FROM employees WHERE emp_code=?", (emp_code,), one=True):
                emp_code = _next_emp_code()
                auto_code = True
            db.execute(
                "INSERT INTO employees (emp_code, name, email, phone, department, designation, joining_date, status, salary, shift, leave_balance, address) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp_code, f.get("name", "").strip(), f.get("email", "").strip(),
                 f.get("phone", "").strip(), f.get("department", "").strip(), f.get("designation", "").strip(),
                 f.get("joining_date", "") or _today_ist().isoformat(), "active",
                 float(f.get("salary", 0) or 0), f.get("shift", "Day").strip() or "Day",
                 int(f.get("leave_balance", 12) or 0), f.get("address", "").strip()))
            db.provision_employee_logins()
            flash("Employee added ✅ Code " + emp_code + (" (apne aap bana)" if auto_code else "")
                  + " · Login bhi ban gaya (username = pehla naam, password shivaya@123).", "success")
        except Exception as e:
            flash(f"Could not add employee: {e}", "error")
        return redirect_with_token(url_for("employees"))
    cur_month = _today_ist().strftime("%Y-%m") + "%"
    rows = [dict(r) for r in db.query(
        "SELECT e.*, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='present' AND a.date LIKE ?) AS present_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='absent' AND a.date LIKE ?) AS absent_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='halfday' AND a.date LIKE ?) AS half_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='leave' AND a.date LIKE ?) AS leave_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.date LIKE ?) AS att_days "
        "FROM employees e ORDER BY e.id",
        (cur_month, cur_month, cur_month, cur_month, cur_month))]
    payroll = 0.0
    earned_total = 0.0
    for r in rows:
        sal = r["salary"] or 0
        if r["status"] == "active":
            payroll += sal
        if r["att_days"] == 0:
            r["earned"] = round(sal)
        else:
            ded = (r["absent_days"] + 0.5 * r["half_days"]) * ((sal) / 30.0)
            r["earned"] = max(0, round(sal - ded))
        if r["status"] == "active":
            earned_total += r["earned"]
    users = {u["employee_id"]: dict(u) for u in db.query("SELECT * FROM users WHERE employee_id>0")}
    for r in rows:
        r["login"] = users.get(r["id"])
    today = _today_ist().isoformat()
    att = {r["emp_id"]: r["status"] for r in db.query("SELECT emp_id, status FROM attendance WHERE date=?", (today,))}
    stats = {
        "total": sum(1 for r in rows if r["status"] == "active"),
        "present_today": sum(1 for r in rows if att.get(r["id"]) == "present"),
        "leave_today": sum(1 for r in rows if att.get(r["id"]) == "leave"),
        "payroll": round(payroll),
        "earned": round(earned_total),
    }
    edit_emp = None
    eid = request.args.get("edit")
    if eid and eid.isdigit():
        edit_emp = db.query("SELECT * FROM employees WHERE id=?", (int(eid),), one=True)
    # v3.74 — add-form me BY DEFAULT agla EMP code bhar ke dikhao (admin chahe to likh de)
    next_code = _next_emp_code()
    return render_template("employees.html", active="employees", employees=rows, att=att, stats=stats,
                           att_labels=ATT_LABELS, show_add=request.args.get("add"), edit_emp=edit_emp,
                           next_code=next_code,
                           dispatch_incharge=get_dispatch_incharge())


@app.route("/employees/dispatch-incharge", methods=["POST"])
@admin_required
def dispatch_incharge_set():
    """v3.08 — admin ek person chuno — Material + Product dispatch SIRF wahi karega."""
    name = (request.form.get("incharge") or "").strip()
    if not name:
        flash("In-charge chuno (koi ek employee).", "error")
        return redirect_with_token(url_for("employees"))
    set_dispatch_incharge(name)
    flash("🚚 DISPATCH IN-CHARGE set: " + name + " — ab Material + Product dispatch sirf ye karega.", "success")
    return redirect_with_token(url_for("employees"))


@app.route("/employees/<int:emp_id>/edit", methods=["POST"])
@login_required
def employee_edit(emp_id):
    f = request.form
    name = (f.get("name") or "").strip()
    if not name:
        flash("Name zaroori hai.", "error")
        return redirect_with_token(url_for("employees", edit=emp_id))
    # v3.74 — admin EMP code me KOI BHI number de sakta hai; khaali chhoda to agla auto number
    emp_code = (f.get("emp_code") or "").strip()
    old = db.query("SELECT emp_code FROM employees WHERE id=?", (emp_id,), one=True)
    if not emp_code:
        emp_code = (old["emp_code"] if old and old["emp_code"] else "") or _next_emp_code()
    dup = db.query("SELECT id FROM employees WHERE emp_code=? AND id!=?", (emp_code, emp_id), one=True)
    if dup:
        flash(f"Employee code '{emp_code}' pehle se kisi aur ka hai — koi dusra number do.", "error")
        return redirect_with_token(url_for("employees", edit=emp_id))
    try:
        db.execute(
            "UPDATE employees SET emp_code=?, name=?, email=?, phone=?, department=?, designation=?, "
            "joining_date=?, status=?, salary=?, shift=?, leave_balance=?, address=? WHERE id=?",
            (emp_code, name, (f.get("email") or "").strip(),
             (f.get("phone") or "").strip(), (f.get("department") or "").strip(),
             (f.get("designation") or "").strip(), (f.get("joining_date") or "").strip(),
             f.get("status", "active"), float(f.get("salary", 0) or 0),
             f.get("shift", "Day").strip() or "Day", int(f.get("leave_balance", 12) or 0),
             f.get("address", "").strip(), emp_id))
        flash(f"'{name}' update ho gaya ✅ (Code {emp_code})", "success")
    except Exception as e:
        flash(f"Update failed: {e}", "error")
    return redirect_with_token(url_for("employees"))


@app.route("/employees/download")
@login_required
@admin_required
def employees_download():
    cur_month = _today_ist().strftime("%Y-%m") + "%"
    rows = db.query(
        "SELECT e.*, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='present' AND a.date LIKE ?) AS present_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='absent' AND a.date LIKE ?) AS absent_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='halfday' AND a.date LIKE ?) AS half_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.status='leave' AND a.date LIKE ?) AS leave_days, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.emp_id=e.id AND a.date LIKE ?) AS att_days "
        "FROM employees e ORDER BY e.id",
        (cur_month, cur_month, cur_month, cur_month, cur_month))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Code", "Name", "Role", "Shift", "Salary", "Leave Balance",
                "Present Days (month)", "Absent", "Half Day", "Leave", "Earned (month)"])
    for r in rows:
        sal = r["salary"] or 0
        if r["att_days"] == 0:
            earned = round(sal)
        else:
            earned = max(0, round(sal - (r["absent_days"] + 0.5 * r["half_days"]) * (sal / 30.0)))
        w.writerow([r["emp_code"], r["name"], r["designation"] or "", r["shift"] or "Day",
                    sal, r["leave_balance"], r["present_days"], r["absent_days"],
                    r["half_days"], r["leave_days"], earned])
    fname = "employees_" + _today_ist().isoformat() + ".csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.route("/employees/<int:emp_id>/att", methods=["POST"])
@login_required
@admin_required
def employee_att_mark(emp_id):
    st = request.form.get("status", "present")
    if st not in ATT_STATUSES:
        st = "present"
    db.execute("INSERT OR REPLACE INTO attendance (emp_id, date, status) VALUES (?,?,?)",
               (emp_id, _today_ist().isoformat(), st))
    flash("Aaj ki attendance update ho gayi ✅", "success")
    return redirect_with_token(url_for("employees"))


@app.route("/employees/<int:emp_id>", methods=["GET", "POST"])
@login_required
def employee_profile(emp_id):
    emp = db.query("SELECT * FROM employees WHERE id=?", (emp_id,), one=True)
    if not emp:
        flash("Employee nahi mila.", "error")
        return redirect_with_token(url_for("employees"))
    if request.method == "POST":
        if session.get("user_role") != "admin":
            flash("Sirf admin attendance mark kar sakta hai.", "error")
            return redirect_with_token(url_for("employee_profile", emp_id=emp_id))
        date_str = request.form.get("date") or _today_ist().isoformat()
        try:
            d = datetime.date.fromisoformat(date_str)
            if d > _today_ist():
                flash("Future date ki attendance nahi ho sakti.", "error")
            else:
                st = request.form.get("status", "present")
                if st not in ATT_STATUSES:
                    st = "present"
                db.execute("INSERT OR REPLACE INTO attendance (emp_id, date, status) VALUES (?,?,?)",
                           (emp_id, date_str, st))
                flash(f"Attendance save ho gayi: {date_str} — {ATT_LABELS.get(st, st)} ✅", "success")
        except ValueError:
            flash("Invalid date.", "error")
        return redirect_with_token(url_for("employee_profile", emp_id=emp_id))
    cur_month = _today_ist().strftime("%Y-%m")
    m = _emp_month(emp_id, cur_month)
    is_admin = session.get("user_role") == "admin"
    return render_template("employee_profile.html", active="employees", emp=emp, m=m,
                           is_admin=is_admin, att_labels=ATT_LABELS,
                           att_statuses=ATT_STATUSES)


@app.route("/employees/<int:emp_id>/report")
@login_required
def employee_report(emp_id):
    emp = db.query("SELECT * FROM employees WHERE id=?", (emp_id,), one=True)
    if not emp:
        flash("Employee nahi mila.", "error")
        return redirect_with_token(url_for("employees"))
    month = request.args.get("month") or _today_ist().strftime("%Y-%m")
    if not _valid_month(month):
        month = _today_ist().strftime("%Y-%m")
    m = _emp_month(emp_id, month)
    y, mo = int(month[:4]), int(month[5:7])
    prev_d = datetime.date(y, mo, 1) - datetime.timedelta(days=1)
    next_d = datetime.date(y, mo, calendar.monthrange(y, mo)[1]) + datetime.timedelta(days=1)
    is_admin = session.get("user_role") == "admin"
    return render_template("employee_report.html", active="employees", emp=emp, m=m, month=month,
                           prev_m=prev_d.strftime("%Y-%m"), next_m=next_d.strftime("%Y-%m"),
                           is_admin=is_admin, att_labels=ATT_LABELS)


@app.route("/employees/<int:emp_id>/delete", methods=["POST"])
@login_required
@admin_required
def employee_delete(emp_id):
    db.execute("DELETE FROM attendance WHERE emp_id=?", (emp_id,))
    db.execute("DELETE FROM emp_salary WHERE emp_id=?", (emp_id,))
    db.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    flash("Employee removed.", "success")
    return redirect_with_token(url_for("employees"))


ATT_STATUSES = ["present", "halfday", "leave", "absent", "late"]
ATT_LABELS = {"present": "P", "halfday": "HD", "leave": "L", "absent": "A", "late": "T"}


def _att_month_rows(month):
    """Per employee: P/A/L/HD counts + advance + paid + salary calc (is month ka)."""
    rows = db.query(
        "SELECT e.id, e.name, e.emp_code, e.department, e.designation, e.salary, "
        "COALESCE(SUM(CASE WHEN a.status='present' THEN 1 END),0) AS p, "
        "COALESCE(SUM(CASE WHEN a.status='absent' THEN 1 END),0) AS a, "
        "COALESCE(SUM(CASE WHEN a.status='leave' THEN 1 END),0) AS l, "
        "COALESCE(SUM(CASE WHEN a.status='halfday' THEN 1 END),0) AS hd, "
        "COALESCE(SUM(CASE WHEN a.status='late' THEN 1 END),0) AS lt, "
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




def _emp_month(emp_id, month):
    """Ek employee ka poora month summary: counts, earned, advance, net, records,
    calendar grid data + cumulative earned chart points (profile/report ke liye)."""
    emp = db.query("SELECT * FROM employees WHERE id=?", (emp_id,), one=True)
    if not emp:
        return None
    y, mo = int(month[:4]), int(month[5:7])
    dim = calendar.monthrange(y, mo)[1]
    recs = db.query("SELECT date, status FROM attendance WHERE emp_id=? AND date LIKE ? ORDER BY date",
                    (emp_id, month + "%"))
    counts = {"present": 0, "absent": 0, "leave": 0, "halfday": 0, "late": 0}
    by_date = {}
    for r in recs:
        s = r["status"]
        if s in counts:
            counts[s] += 1
        by_date[r["date"]] = s
    sal = float(emp["salary"] or 0)
    per_day = round(sal / dim, 2) if dim else 0.0
    ded = round(per_day * counts["absent"] + per_day * 0.5 * counts["halfday"], 2)
    earned = round(sal - ded, 2)
    es = db.query("SELECT * FROM emp_salary WHERE emp_id=? AND month=?", (emp_id, month), one=True)
    advance = float((es["advance"] if es else 0) or 0)
    paid = int((es["paid"] if es else 0) or 0)
    net = round(earned - advance, 2)
    today = _today_ist().isoformat()
    no_records = len(recs) == 0
    first_wd = calendar.monthrange(y, mo)[0]  # 0=Mon
    cal = []
    for i in range(first_wd):
        cal.append({"blank": True})
    chart = [(0, 0.0)]
    cum = 0.0
    for d in range(1, dim + 1):
        ds = f"{month}-{d:02d}"
        st = by_date.get(ds)
        if st:
            f = {"present": 1.0, "late": 1.0, "halfday": 0.5}.get(st, 0.0)
            cls = {"present": "st-p", "late": "st-t", "halfday": "st-h",
                   "leave": "st-l", "absent": "st-a"}.get(st, "st-x")
        else:
            if month == today[:7] and ds > today:
                f, cls = 0.0, "st-f"
            else:
                f, cls = 1.0, "st-x"
        cum += per_day * f
        chart.append((d, cum))
        cal.append({"day": d, "ds": ds, "st": st, "cls": cls,
                    "is_today": ds == today, "label": ATT_LABELS.get(st, "·") if st else ""})
    # SVG points (600x260): actual earned line + full salary target
    W, H = 600, 260
    mx = W - 60
    my = H - 50
    maxv = sal if sal > 0 else 1.0
    pts = []
    for d, c in chart:
        x = 50 + (d / dim) * mx
        py = my + 10 - (c / maxv) * (my - 20)
        pts.append(f"{x:.1f},{py:.1f}")
    target_y = my + 10 - (sal / maxv) * (my - 20)
    return {
        "emp": emp, "month": month, "dim": dim, "records": [dict(r) for r in recs],
        "counts": counts, "by_date": by_date, "sal": sal, "per_day": per_day,
        "ded": ded, "earned": earned, "advance": advance, "paid": paid, "net": net,
        "leave_balance": emp["leave_balance"] or 0, "cal": cal, "chart_points": " ".join(pts),
        "target_y": f"{target_y:.1f}", "chart_max": round(maxv, 2),
        "no_records": no_records, "month_label": datetime.date(y, mo, 1).strftime("%B %Y"),
        "today_iso": today,
        "present_fraction": (counts["present"] + counts["late"] + 0.5 * counts["halfday"]) / dim if dim else 0,
    }


def _valid_month(m):
    return bool(re.fullmatch(r"\d{4}-\d{2}", m or ""))


@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    date_str = request.form.get("date") if request.method == "POST" else request.args.get("date")
    if not date_str:
        date_str = _today_ist().isoformat()
    if request.method == "POST":
        try:
            d = datetime.date.fromisoformat(date_str)
            if d > _today_ist():
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
    month = request.args.get("month") or _today_ist().strftime("%Y-%m")
    if not _valid_month(month):
        month = _today_ist().strftime("%Y-%m")
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        date_str = _today_ist().isoformat()
        d = _today_ist()
    rows = db.query(
        "SELECT e.id, e.name, e.department, a.status FROM employees e "
        "LEFT JOIN attendance a ON a.emp_id=e.id AND a.date=? WHERE e.status='active' ORDER BY e.id", (date_str,))
    summary = {"present": 0, "absent": 0, "halfday": 0, "leave": 0, "late": 0}
    for r in rows:
        if r["status"] in summary:
            summary[r["status"]] += 1
    sal_rows, dim = _att_month_rows(month)
    tot = {"salary": round(sum(r["salary"] or 0 for r in sal_rows), 2),
           "ded": round(sum(r["ded"] for r in sal_rows), 2),
           "advance": round(sum(r["advance"] or 0 for r in sal_rows), 2),
           "net": round(sum(r["net"] for r in sal_rows), 2)}
    return render_template("attendance.html", active="employees", employees=rows, date=date_str,
                           future=d > _today_ist(), summary=summary, month=month,
                           sal_rows=sal_rows, days_in_month=dim, tot=tot)


@app.route("/attendance/advance", methods=["POST"])
@login_required
def attendance_advance():
    emp_id = int(request.form.get("emp_id") or 0)
    month = request.form.get("month") or _today_ist().strftime("%Y-%m")
    if not _valid_month(month):
        month = _today_ist().strftime("%Y-%m")
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
    month = request.form.get("month") or _today_ist().strftime("%Y-%m")
    if not _valid_month(month):
        month = _today_ist().strftime("%Y-%m")
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
            db.execute("INSERT INTO machines (code, name, status, current_job, hsn) VALUES (?,?,?,?,?)",
                       (f"M{7 + count}", f.get("name").strip(), f.get("status", "idle"), f.get("current_job", "").strip(),
                        (f.get("hsn") or "").strip()))
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
        db.execute("UPDATE machines SET code=?, name=?, status=?, current_job=?, hsn=? WHERE id=?",
                   ((f.get("code") or "").strip(), name, f.get("status", "idle"),
                    (f.get("current_job") or "").strip(), (f.get("hsn") or "").strip(), m_id))
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
                    f.get("result", "pending"), _today_ist().isoformat()))
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
                f.get("date") or _today_ist().isoformat(), qc_id))
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
    return _now_ist().strftime("%Y-%m-%d %H:%M")


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


def flow_pending(order_id):
    """Order ka PENDING process — pehla process jiska kaam shuru nahi hua (start_dt empty).
    Production Planning isi par operator/machine assign karta hai."""
    rows = db.get_jc_processes(order_id)
    for r in rows:
        if not (r.get("start_dt") or ""):
            return r["process"]
    return ""


def flow_next(order_id):
    """Order ka AGLA process — pehla unfinished (end_dt empty); sab done -> Completed.
    Live Tracking ke 'Move To Next' mein ye AUTO dikhta hai."""
    rows = db.get_jc_processes(order_id)
    if not rows:
        return ""
    unfinished = [r for r in rows if not r.get("end_dt")]
    if not unfinished:
        return "Completed"
    unstarted = [r for r in unfinished if not r.get("start_dt")]
    return unstarted[0]["process"] if unstarted else unfinished[0]["process"]


def proc_assignment(order_id, process):
    """Kisi process ki assignment (machine/shift/operator) — purani order-level
    assignment (process='') fallback ke saath."""
    asg = db.query("SELECT * FROM order_assignments WHERE order_id=? AND process=?",
                   (order_id, process or ""), one=True)
    if asg:
        return asg
    return db.query("SELECT * FROM order_assignments WHERE order_id=? AND process=''",
                    (order_id,), one=True)


def _op_names(val):
    """Operator field me comma-separated naam (multi-employee: 2+ log milkar) —
    clean unique list deta hai."""
    return [x.strip() for x in (val or "").split(",") if x.strip() and x.strip() != "Unassigned"]


def get_dispatch_incharge():
    """v3.08 — Material + Product dispatch SIRF ye ek person karega (meta setting)."""
    try:
        r = db.query("SELECT value FROM meta WHERE key='dispatch_incharge'", one=True)
        return (r["value"] or "").strip() if r else ""
    except Exception:
        return ""


def set_dispatch_incharge(name):
    db.execute("DELETE FROM meta WHERE key='dispatch_incharge'")
    db.execute("INSERT INTO meta (key, value) VALUES ('dispatch_incharge', ?)", ((name or "").strip(),))


def ensure_party(name):
    """v3.12 — party ka naam pehli baar aaya to PARTIES me auto-add (customer)."""
    name = (name or "").strip()
    if not name:
        return False
    _ex = db.query("SELECT id FROM parties WHERE name=?", (name,), one=True)
    if _ex:
        return False
    try:
        db.execute("INSERT INTO parties (name, ptype) VALUES (?, 'customer')", (name,))
        return True
    except Exception:
        return False


def dispatch_block_msg():
    _i = get_dispatch_incharge()
    return ("🚚 Dispatch sirf IN-CHARGE (" + (_i or "SET NAHI — admin: Employees page par set karo") +
            ") kar sakta hai — aapko permission nahi.")


DESIGNATION_JOBS = {
    "CNC": ["CNC DRILLING", "DIE/CNC", "DRILLING"],
    "DRILL": ["CNC DRILLING", "DIE/CNC", "DRILLING"],
    "LAMINAT": ["LAMINATION"],
    "QC": ["PTH QC", "FQC", "BBT"],
    "TEST": ["BBT", "FQC", "TESTING"],
    "CUT": ["LAMINATE CUTTING", "V-CUT", "CUTTING"],
    "PRINT": ["PRINTING", "SOLDER MASK", "LEGEND"],
    "ETCH": ["ETCHING"],
    "PLAT": ["PLATING"],
    "HAL": ["HAL", "TINNING"],
    "SOLDER": ["SOLDER MASK"],
    "PTH": ["PTH"],
    "ROUT": ["ROUTING"],
    "DISPATCH": ["DISPATCH", "PACKING"],
    "PACK": ["PACKING", "DISPATCH"],
}


def designation_matches(designation, process):
    """Kya is designation ka employee ye process karta hai? (designation ke hisaab se work filter)"""
    if not designation or not process:
        return False
    d_up = (designation or "").upper()
    p_up = (process or "").upper()
    for key, procs in DESIGNATION_JOBS.items():
        if key in d_up:
            for p in procs:
                if p in p_up:
                    return True
    return False


def _dispatch_proc_row(order_id):
    """Job card ki Dispatch/Packing process row (naam se dhundo — flow ke hisaab se alag ho sakta hai)."""
    return db.query("SELECT * FROM jobcard_process WHERE order_id=? AND "
                    "(UPPER(process) LIKE '%DISPATCH%' OR UPPER(process) LIKE '%PACK%') "
                    "ORDER BY ord DESC, id DESC LIMIT 1", (order_id,), one=True)


def _mark_dispatch_process(order_id, by_name, ddate, dtime):
    """Dispatch entry hote hi job card ki Dispatch/Packing row me DATE + TIME + NAME auto bharo
    (row DONE ho jaye) — Job Card aur Print Preview me dispatch row date/time ke saath dikhe."""
    row = _dispatch_proc_row(order_id)
    if not row:
        return
    dts = f"{ddate} {dtime}"
    db.execute("UPDATE jobcard_process SET "
               "start_dt=CASE WHEN COALESCE(start_dt,'')='' THEN ? ELSE start_dt END, "
               "end_dt=?, "
               "start_name=CASE WHEN COALESCE(start_name,'')='' THEN ? ELSE start_name END, "
               "end_name=? WHERE id=?",
               (dts, dts, by_name, by_name, row["id"]))
    advance_current(order_id)


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
    # LIVE STATUS: sab done -> done; koi process chalu (started, not finished) -> running.
    # Kuch started nahi hai to status waise hi rehne do (pending/scheduled) — downgrade nahi.
    if current == "Completed":
        db.execute("UPDATE orders SET status='done' WHERE id=?", (order_id,))
    else:
        running = db.query("SELECT COUNT(*) c FROM jobcard_process "
                           "WHERE order_id=? AND start_dt!='' AND end_dt=''",
                           (order_id,), one=True)
        if (running["c"] or 0) > 0:
            db.execute("UPDATE orders SET status='running' WHERE id=? AND status NOT IN ('done')",
                       (order_id,))
    return current


def proc_skipped(order_id, process):
    """Kya ye process skip hai (iska predecessor NEXT = NONE)?"""
    rows = db.get_jc_processes(order_id)
    for i, r in enumerate(rows):
        if (r["process"] or "").lower() == (process or "").lower() and i > 0:
            return (rows[i - 1]["next_process"] or "").upper() == "NONE"
    return False


@app.route("/my-work")
@login_required
def my_work():
    """v2.99 — MERA WORK: employee ka pura working record + apne kaam + claim."""
    me = session.get("op_name") or session.get("user_name") or "Operator"
    is_admin = session.get("user_role") == "admin"
    view_emp = me
    if is_admin:
        view_emp = request.args.get("emp", "").strip() or me
    employees = db.query("SELECT name, designation FROM employees ORDER BY name") if is_admin else []

    _all = db.query("SELECT * FROM orders WHERE status != 'done' ORDER BY "
                    "CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, delivery_date, id")
    my_jobs, claimable = [], []
    for o in _all:
        _, prow = current_proc_row(o["id"])
        if not prow:
            continue
        asg = proc_assignment(o["id"], prow["process"])
        assigned = _op_names(asg["operator"]) if asg else []
        mine = (view_emp in assigned) or ((o["operator"] or "").strip() == view_emp)
        d = dict(o)
        d["proc"] = prow["process"]
        d["state"] = op_state(o["id"], prow["process"])
        d["pause_reason"] = ""
        if d["state"] == "paused":
            last = db.query("SELECT reason FROM jobcard_log WHERE order_id=? AND process=? AND action='pause' "
                            "ORDER BY id DESC LIMIT 1", (o["id"], prow["process"]), one=True)
            d["pause_reason"] = last["reason"] if last else ""
        (my_jobs if mine else claimable).append(d)

    history = db.query(
        "SELECT l.*, o.order_no FROM jobcard_log l LEFT JOIN orders o ON o.id = l.order_id "
        "WHERE l.operator=? ORDER BY l.id DESC LIMIT 150", (view_emp,))
    total_actions = db.query("SELECT COUNT(*) c FROM jobcard_log WHERE operator=?", (view_emp,), one=True)["c"]
    total_finishes = db.query("SELECT COUNT(*) c FROM jobcard_log WHERE operator=? AND action='finish'",
                              (view_emp,), one=True)["c"]
    my_issues = db.query("SELECT item_name, qty, unit, taken_on FROM material_issues WHERE worker=? "
                         "ORDER BY id DESC LIMIT 30", (view_emp,))
    my_dispatches = db.query("SELECT d.ddate, d.dtime, d.mode, o.order_no FROM dispatch_log d "
                             "LEFT JOIN orders o ON o.id = d.order_id WHERE d.dispatched_by=? "
                             "ORDER BY d.id DESC LIMIT 20", (view_emp,))
    return render_template("my_work.html", active="mywork", view_emp=view_emp, is_admin=is_admin,
                           employees=employees, my_jobs=my_jobs, claimable=claimable,
                           history=history, total_actions=total_actions, total_finishes=total_finishes,
                           my_issues=my_issues, my_dispatches=my_dispatches)


@app.route("/my-work/claim", methods=["POST"])
@login_required
def my_work_claim():
    """v2.99 — pending job apne account me lo: assignment + operator + log."""
    me = session.get("op_name") or session.get("user_name") or "Operator"
    order_id = int(request.form.get("order_id", 0) or 0)
    o = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o or o["status"] == "done":
        flash("Order not found.", "error")
        return redirect_with_token(url_for("my_work"))
    _, prow = current_proc_row(order_id)
    proc = prow["process"] if prow else (o["current_process"] or "")
    _ex = db.query("SELECT operator FROM order_assignments WHERE order_id=? AND process=?",
                   (order_id, proc), one=True)
    if _ex:
        db.execute("UPDATE order_assignments SET operator=? WHERE order_id=? AND process=?",
                   (me, order_id, proc))
    else:
        db.execute("INSERT INTO order_assignments (order_id, process, operator) VALUES (?,?,?)",
                   (order_id, proc, me))
    db.execute("UPDATE orders SET operator=? WHERE id=?", (me, order_id))
    db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, details, ts) VALUES (?,?,?,?,?,?)",
               (order_id, proc, "claim", me, "Job apne account me liya (claim)", op_now()))
    flash("Claim ho gaya: " + o["order_no"] + " " + chr(0x2014) + " " + proc + " ab aapke account me hai. Start dabake shuru karo.", "success")
    return redirect_with_token(url_for("my_work"))


@app.route("/my-work/reset", methods=["POST"])
@login_required
def my_work_reset():
    """v3.00 — admin: galat claim/start/finish hua process FRESH kar do
    (usi employee ke account me wapas fresh dikhega, assignment bani rehti hai)."""
    if session.get("user_role") != "admin":
        flash("Sirf admin process reset kar sakta hai.", "error")
        return redirect_with_token(url_for("my_work"))
    try:
        order_id = int(request.form.get("order_id", 0) or 0)
    except ValueError:
        order_id = 0
    process = request.form.get("process", "").strip()
    o = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    row = db.query("SELECT * FROM jobcard_process WHERE order_id=? AND process=?",
                   (order_id, process), one=True) if o else None
    if not row:
        flash("Order/process nahi mila — reset nahi hua.", "error")
        return redirect_with_token(url_for("my_work"))
    _by = session.get("user_name") or "admin"
    # 1) process row FRESH: start/finish dono undo
    db.execute("UPDATE jobcard_process SET start_dt='', start_name='', end_dt='', end_name='' WHERE id=?",
               (row["id"],))
    # 2) audit log (history me Reset dikhega)
    db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, reason, details, ts) "
               "VALUES (?,?,?,?,?,?,?)",
               (order_id, process, "reset", _by, request.form.get("reason", "").strip(),
                "Admin reset — process fresh kiya (claim/start/finish undo, assignment bani rahi)",
                op_now()))
    # 3) current_process + status recompute — reset wala process wapas current/pending
    advance_current(order_id)
    _rows = db.get_jc_processes(order_id)
    if _rows and all(r["end_dt"] for r in _rows):
        _st = "done"
    elif any(r["start_dt"] and not r["end_dt"] for r in _rows):
        _st = "running"
    else:
        _st = "pending"
    db.execute("UPDATE orders SET status=? WHERE id=?", (_st, order_id))
    flash("Reset ho gaya: " + o["order_no"] + " — " + process +
          " ab fresh hai — employee dobara Start kar sakta hai.", "success")
    return redirect_with_token(url_for("my_work"))


@app.route("/operator")
@login_required
def operator_view():
    op = session.get("op_name") or session.get("user_name") or "Operator"
    emp = db.query("SELECT * FROM employees WHERE name=?", (op,), one=True)
    designation = emp["designation"] if emp and emp["designation"] else "Production Operator"
    is_admin = session.get("user_role") == "admin"
    is_dispatch = ("DISPATCH" in (designation or "").upper()) or ("PACK" in (designation or "").upper())
    jobs = []
    for o in db.query("SELECT * FROM orders WHERE status != 'done' ORDER BY "
                      "CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, delivery_date, id"):
        d = dict(o)
        _order, prow = current_proc_row(o["id"])
        if not prow:
            continue
        d["proc"] = prow["process"]
        d["state"] = op_state(o["id"], prow["process"])
        # NEXT = flow ka agla process (auto) + usko kaun karega (assigned name)
        d["next_proc"] = ""
        d["next_none"] = False
        d["next_op"] = ""
        allrows = db.get_jc_processes(o["id"])
        nxtrow = next((r for r in allrows if r["idx"] == prow["idx"] + 1), None)
        if nxtrow:
            d["next_proc"] = nxtrow["process"]
            nasg = proc_assignment(o["id"], nxtrow["process"])
            d["next_op"] = (nasg["operator"] or "") if nasg and nasg["operator"] not in ("", "Unassigned") else ""
        d["pause_reason"] = ""
        if d["state"] == "paused":
            last = db.query("SELECT * FROM jobcard_log WHERE order_id=? AND process=? AND action='pause' "
                            "ORDER BY id DESC LIMIT 1", (o["id"], prow["process"]), one=True)
            d["pause_reason"] = last["reason"] if last else ""
        asg = proc_assignment(o["id"], prow["process"])
        d["assigned_op"] = asg["operator"] if asg else "Unassigned"
        d["assigned_machine"] = asg["machine"] if asg else "Unassigned"
        d["mine"] = ((asg and op in _op_names(asg["operator"]))
                     or (prow["start_name"] or "") == op
                     or (o["operator"] or "") == op)
        d["my_work"] = (d["mine"] or designation_matches(designation, prow["process"]))
        if not is_admin and not d["my_work"] and (d["priority"] or "") != "urgent":
            continue  # operator ko sirf apne kaam (designation/assignment) ke jobs dikhen; 🚨 URGENT sabko
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
    dispatch_rows = []
    if is_dispatch or is_admin:
        dispatch_rows = db.query(
            "SELECT dl.*, o.order_no, o.party FROM dispatch_log dl "
            "LEFT JOIN orders o ON o.id=dl.order_id ORDER BY dl.id DESC LIMIT 30")
    _inch = get_dispatch_incharge()
    _admin = session.get("user_role") == "admin"
    _can_issue = (bool(_inch) and op == _inch) or _admin
    # v3.10 LIVE PROCESS TRACKING (dashboard jaisi table) — live operator + progress ke saath
    _live_rows = db.query("SELECT * FROM orders WHERE status != 'done' ORDER BY "
                          "CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, "
                          "CASE status WHEN 'running' THEN 0 WHEN 'hold' THEN 1 ELSE 2 END, id")
    live_tracking = _decorate_working_ops(_live_rows)
    my_requests = db.query("SELECT * FROM material_requests WHERE requested_by=? ORDER BY id DESC LIMIT 30", (op,))
    pending_requests = db.query(
        "SELECT r.*, o.order_no FROM material_requests r LEFT JOIN orders o ON o.id=r.order_id "
        "WHERE r.status='pending' ORDER BY r.id DESC LIMIT 50") if _can_issue else []
    return render_template("operator.html", active="operator", jobs=jobs, logs=logs,
                           employees=employees, op=op, designation=designation,
                           inv_items=inv_items, my_issues=my_issues, is_dispatch=is_dispatch,
                           dispatch_rows=dispatch_rows,
                           incharge=_inch, is_incharge=_can_issue, is_admin_view=_admin,
                           my_requests=my_requests, pending_requests=pending_requests,
                           live_tracking=live_tracking,
                           admin_show=_admin)


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
    _op0 = session.get("op_name") or session.get("user_name") or ""
    if get_dispatch_incharge() != _op0 and session.get("user_role") != "admin":
        flash(dispatch_block_msg(), "error")
        return redirect_with_token(url_for("operator_view"))
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
        asg = proc_assignment(order_id, prow["process"]) if order_id and prow else None
        allowed = (bool(asg and asg["operator"] == op)
                   or (prow is not None and (prow["start_name"] or "") == op)
                   or (order is not None and (order["operator"] or "") == op))
        if not allowed:
            flash("Aap sirf apne assigned job order mein item issue kar sakte hain.", "error")
            return redirect_with_token(url_for("operator_view"))
    notes = f.get("notes", "").strip()
    taken_on = _now_ist().strftime("%Y-%m-%d %H:%M")
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


@app.route("/operator/request-material", methods=["POST"])
@login_required
def request_material():
    """v3.09 — KOI BHI employee (bina admin power) stock ke liye REQUEST bhej sakta hai.
    Entry pending request banti hai — in-charge/admin Issue dabayega tab stock katega."""
    op = session.get("op_name") or session.get("user_name") or "Operator"
    f = request.form
    try:
        order_id = int(f.get("order_id", 0) or 0)
        item_id = int(f.get("item_id", 0) or 0)
        qty = float(f.get("qty", 0) or 0)
    except ValueError:
        order_id, item_id, qty = 0, 0, 0
    item = db.query("SELECT * FROM inventory WHERE id=?", (item_id,), one=True) if item_id else None
    if not item or qty <= 0:
        flash("Request ke liye item aur qty (0 se zyada) dono chahiye.", "error")
        return redirect_with_token(url_for("operator_view"))
    db.execute("INSERT INTO material_requests (order_id, item_id, item_name, qty, unit, note, "
               "requested_by, status, req_on) VALUES (?,?,?,?,?,?,?,?,?)",
               (order_id or None, item_id, item["name"], qty, item["unit"] or "",
                f.get("note", "").strip(), op, "pending", _now_ist().strftime("%Y-%m-%d %H:%M")))
    flash("🧾 Request bhej di gayi: " + item["name"] + " " + format(qty, "g") + " " + (item["unit"] or "") +
          " — DISPATCH IN-CHARGE pass pending hai. Issue hone par inventory entry ban jayegi.", "success")
    return redirect_with_token(url_for("operator_view"))


@app.route("/operator/request-action", methods=["POST"])
@login_required
def request_action():
    """v3.09 — sirf in-charge/admin: request Issue (stock kata + entry) ya Reject."""
    op = session.get("op_name") or session.get("user_name") or ""
    if get_dispatch_incharge() != op and session.get("user_role") != "admin":
        flash(dispatch_block_msg(), "error")
        return redirect_with_token(url_for("operator_view"))
    try:
        rid = int(request.form.get("request_id", 0) or 0)
    except ValueError:
        rid = 0
    req = db.query("SELECT * FROM material_requests WHERE id=?", (rid,), one=True) if rid else None
    if not req or req["status"] != "pending":
        flash("Request nahi mili ya pehle process ho chuki.", "error")
        return redirect_with_token(url_for("operator_view"))
    do = request.form.get("do", "")
    if do == "issue":
        item = db.query("SELECT * FROM inventory WHERE id=?", (req["item_id"],), one=True)
        if not item:
            flash("Request ka item inventory me ab nahi hai.", "error")
            return redirect_with_token(url_for("operator_view"))
        qty = req["qty"] or 0
        new_stock = round((item["stock"] or 0) - qty, 4)
        db.execute("UPDATE inventory SET stock=? WHERE id=?", (new_stock, item["id"]))
        _now = _now_ist().strftime("%Y-%m-%d %H:%M")
        db.execute("INSERT INTO material_issues (order_id, item_id, item_name, qty, unit, worker, taken_on, notes) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (req["order_id"] or None, req["item_id"], item["name"], qty, item["unit"] or "",
                    req["requested_by"], _now,
                    (req["note"] or "").strip() + " [req #" + str(rid) + " issue by " + op + "]"))
        db.execute("UPDATE material_requests SET status='issued', issued_by=?, issued_on=? WHERE id=?",
                   (op, _now, rid))
        flash("✅ Request #" + str(rid) + " ISSUE ho gayi — " + item["name"] + " " + format(qty, "g") +
              " " + (item["unit"] or "") + " stock se kata, entry " + (req["requested_by"] or "?") +
              " ke naam par. " + ("⚠️ STOCK SHORT: " + format(new_stock, "g") if new_stock < 0 else ""), "error" if new_stock < 0 else "success")
    else:
        db.execute("UPDATE material_requests SET status='rejected', reject_reason=?, issued_by=? WHERE id=?",
                   (request.form.get("reason", "").strip() or "in-charge ne mana kiya", op, rid))
        flash("Request #" + str(rid) + " reject ho gayi.", "error")
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
        # STATUS -> RUNNING: dashboard + live process tracking me bhi dikhe
        db.execute("UPDATE orders SET status='running', operator=? WHERE id=? AND status NOT IN ('done')", (op, order_id))
        flash(f"Started: {process} — START {now} job card mein save ho gaya.", "success")
    elif action == "pause":
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, reason, details, ts) VALUES (?,?,?,?,?,?,?)",
                   (order_id, process, "pause", op, reason, details, now))
        # STATUS -> HOLD: kaam ruka hua hai — dashboard/tracking me Hold dikhe
        db.execute("UPDATE orders SET status='hold' WHERE id=? AND status NOT IN ('done')", (order_id,))
        flash(f"Paused: {process} — HOLD" + (f" — reason: {reason}" if reason else ""), "success")
    elif action == "resume":
        db.execute("INSERT INTO jobcard_log (order_id, process, action, operator, ts) VALUES (?,?,?,?,?)",
                   (order_id, process, "resume", op, now))
        # STATUS -> RUNNING wapas
        db.execute("UPDATE orders SET status='running', operator=? WHERE id=? AND status NOT IN ('done')", (op, order_id))
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
        if get_dispatch_incharge() != op and session.get("user_role") != "admin":
            flash(dispatch_block_msg(), "error")
            return redirect_with_token(url_for("operator_view"))
        mode = f.get("mode", "").strip()
        if mode:
            ph_name, ph_mime, ph_data = "", "", None
            photo = request.files.get("photo")
            if photo and photo.filename:
                ext = (photo.filename.rsplit(".", 1)[-1] if "." in photo.filename else "").lower()
                blob = photo.read()
                if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic") and len(blob) <= 3 * 1024 * 1024:
                    ph_name, ph_mime, ph_data = photo.filename, photo.mimetype or "image/png", blob
                else:
                    flash("📷 Photo nahi lagi (sirf image, max 3MB) — dispatch bina photo ke ho gaya.", "error")
            db.execute("INSERT INTO dispatch_log (order_id, ddate, dtime, mode, details, dispatched_by, ts, "
                       "photo_name, photo_mime, photo_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (order_id, _today_ist().isoformat(),
                        _now_ist().strftime("%H:%M"), mode,
                        f.get("details", "").strip(), op, now, ph_name, ph_mime, ph_data))
            # PRICE HISTORY: dispatch ke waqt item kis price pe gaya
            _jcd = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
            _ord2 = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
            if _jcd and (_jcd["price"] or 0) > 0 and _ord2:
                _record_price(_jcd["party_model"], _jcd["model"], _jcd["price"], _jcd["rs_pcb"],
                              order_id, _ord2["order_no"], _ord2["party"],
                              _today_ist().isoformat(), _model_thickness(_jcd["party_model"]))
            # JOB CARD + PREVIEW: Dispatch row me date/time/name auto bharo (row done)
            _mark_dispatch_process(order_id, op, _today_ist().isoformat(),
                                   _now_ist().strftime("%H:%M"))
            flash(f"PCB dispatched: {mode} — date/time auto save ho gaya." + (" 📷 Photo bhi save hui." if ph_data else ""), "success")
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
                proc = f.get(f"proc_{oid}", "").strip() or flow_pending(oid) or ""
                db.execute(
                    "INSERT INTO order_assignments (order_id, process, machine, shift, operator, planned_start) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(order_id, process) DO UPDATE SET machine=excluded.machine, shift=excluded.shift, "
                    "operator=excluded.operator, planned_start=excluded.planned_start",
                    (oid, proc, f.get(f"machine_{oid}", "Unassigned").strip() or "Unassigned",
                     f.get(f"shift_{oid}", "").strip(), f.get(f"op_{oid}", "Unassigned").strip() or "Unassigned",
                     f.get(f"start_{oid}", "").strip()))
            flash("Assignments saved (pending process ke liye).", "success")
        else:
            count = db.query("SELECT COUNT(*) c FROM plans", one=True)["c"]
            db.execute("INSERT INTO plans (plan_no, order_id, desc, start_date, end_date, status) VALUES (?,?,?,?,?,?)",
                       (f"PL-{503 + count}", int(f.get("order_id") or 0) or None, f.get("desc", "").strip(),
                        f.get("start_date", ""), f.get("end_date", ""), f.get("status", "planned")))
            flash("Plan created.", "success")
        return redirect_with_token(url_for("production_planning"))

    today = _today_ist()
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
    # assignment rows — ab per PROCESS (pending process par assign hota hai)
    assigns = db.query("SELECT * FROM order_assignments")
    amap = {}
    for a in assigns:
        amap.setdefault(a["order_id"], {})[a["process"] or ""] = a
    pending = [dict(o) for o in pending]
    for o in pending:
        pproc = flow_pending(o["id"]) or o["current_process"] or "—"
        o["pending_proc"] = pproc
        m = amap.get(o["id"], {})
        o["asm"] = m.get(pproc) or m.get("") or m.get(o["current_process"])
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
             "pcb_gap",
             "v_grooving", "customer_req", "raw_materials", "total_qty", "short_qty", "short_reason",
             "handover_sign", "priority", "board_side", "board_material"]


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
    # AUTO PROCESS FLOW: process table KHAALI ho to SIDE+MATERIAL se khud seed karo —
    # job card kholte hi table bhari milegi (khaali kabhi nahi dikhega). Data wali table
    # ko touch nahi karte — sirf khaali wali me default flow bharo.
    try:
        # RAW count — get_jc_processes() khud 'Laminate Cutting' auto-insert karta hai,
        # isliye guard uske upar nahi chal sakta
        _jcc = db.query("SELECT COUNT(*) c FROM jobcard_process WHERE order_id=?", (order_id,), one=True)["c"]
        if _jcc == 0:
            _side = (jc["board_side"] or "SINGLE SIDE").strip() or "SINGLE SIDE"
            _mat = (jc["board_material"] or "").strip()
            _plist, _plabel = db.process_preset_for(_side, _mat)
            for _i, _pn in enumerate(_plist):
                _proc = db.JC_TOOL_OPTIONS[0] if _pn == db.JC_TOOL_SLOT else _pn
                db.execute("INSERT INTO jobcard_process (order_id, process, next_process, ord) VALUES (?,?,?,?)",
                           (order_id, _proc, "", (_i + 1) * 10))
            db.execute("UPDATE orders SET current_process=? WHERE id=?", (_plist[0], order_id))
    except Exception:
        pass
    # SELF-HEAL: dispatch entry hai par job card ki Dispatch row khaali (purane orders) —
    # row me date/time/name bhar do taaki job card + preview me dikhe
    dl = db.query("SELECT * FROM dispatch_log WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,), one=True)
    if dl:
        _mark_dispatch_process(order_id, dl["dispatched_by"], dl["ddate"], dl["dtime"])
        order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True) or order
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
        asg = proc_assignment(order_id, d["process"])
        d["asg_op"] = (asg["operator"] or "") if asg and asg["operator"] not in ("", "Unassigned") else ""
        d["asg_machine"] = (asg["machine"] or "") if asg and asg["machine"] not in ("", "Unassigned") else ""
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
    # QTY OF PANEL auto-fallback: DB me 0 ho par order qty + pcs/panel available ho to
    # display/print ke liye ceil(qty ÷ pcs/panel) dikhao (save hote hi DB me bhi save hoga)
    if not (jc.get("qty_panel") or 0) and (jc.get("pcs_panel") or 0) > 0 and (order.get("qty") or 0) > 0:
        jc = dict(jc)
        jc["qty_panel"] = math.ceil(order["qty"] / jc["pcs_panel"])
    return order, jc, procs


@app.route("/jobcard/new", methods=["GET", "POST"])
@login_required
def jobcard_new():
    """v3.87: ADVANCED (manual) job card CREATION band — ab job cards sirf PO se bante hain (/po).
    ?edit=ID wala purana edit-mode chalu rahega (existing card ki editing). Naya card = /po."""
    _edit87 = request.args.get("edit") if request.method == "GET" else (request.form.get("edit_id") or "")
    if not _edit87:
        # v3.87: naya (manual/advanced) job card banana band — PO se banao (edit-mode chalu)
        return redirect("/po")
    parties = [p["name"] for p in db.query("SELECT name FROM parties ORDER BY name")]
    models = db.query("SELECT * FROM product_models ORDER BY name")
    inv_items = db.query("SELECT * FROM inventory ORDER BY CASE WHEN stock<=min_stock THEN 0 ELSE 1 END, category, name")
    thicknesses = [r["thickness"] for r in db.query(
        "SELECT DISTINCT thickness FROM thickness_rates WHERE thickness!='' ORDER BY thickness")]
    # v2.71 EDIT MODE: /jobcard/new?edit=ID \u2014 wahi ADVANCE page purane order ke data ke saath
    edit_id = 0
    edit_order = edit_jc = None
    edit_model_id = 0
    _eid = request.args.get("edit")
    if _eid and _eid.isdigit():
        edit_order = db.query("SELECT * FROM orders WHERE id=?", (int(_eid),), one=True)
        if edit_order:
            edit_jc = db.query("SELECT * FROM jobcard WHERE order_id=?", (int(_eid),), one=True)
            if edit_jc:
                edit_id = int(_eid)
                _hj78 = _jc_autofill(int(_eid))
                if _hj78:
                    edit_jc = _hj78
                if edit_jc["party_model"]:
                    _em = db.query("SELECT id FROM product_models WHERE name=?", (edit_jc["party_model"],), one=True)
                    edit_model_id = _em["id"] if _em else 0
    if request.method == "POST":
        f = request.form
        party = f.get("party", "").strip()
        if not party:
            flash("Party name daalo.", "error")
            return redirect_with_token(url_for("jobcard_new"))
        pmodel = None
        if (f.get("product_id") or "").isdigit() and int(f.get("product_id") or 0) > 0:
            pmodel = db.query("SELECT * FROM product_models WHERE id=?",
                              (int(f.get("product_id")),), one=True)
        # v2.73 EDIT MODE fallback: CUT LIST se bane job card ka model dropdown me NAHI hota
        # (party_model blank/feature hai) — purana jobcard data se VIRTUAL MODEL banao,
        # warna update par details 0 ho jaati (blank job card ka wahi purana bug)
        _edit_id_post = 0
        if (f.get("edit_id") or "").isdigit() and int(f.get("edit_id") or 0) > 0:
            _edit_id_post = int(f.get("edit_id"))
            _ej_p = db.query("SELECT * FROM jobcard WHERE order_id=?", (_edit_id_post,), one=True)
            if _ej_p and not pmodel:
                class _VM(dict):
                    pass
                _vpx = _ej_p["panel_x"] or _ej_p["x_size"] or 0
                _vpy = _ej_p["panel_y"] or _ej_p["y_size"] or 0
                _vpcb = _ej_p["actual_pcb_x"] if (_ej_p["actual_pcb_x"] or 0) > 0 else _vpx
                _vpcb_w = _ej_p["actual_pcb_y"] if (_ej_p["actual_pcb_y"] or 0) > 0 else _vpy
                _vpcs = _ej_p["pcs_panel"] if (_ej_p["pcs_panel"] or 0) > 0 else 1
                pmodel = _VM(name=_ej_p["party_model"] or _ej_p["model"] or "CUSTOM", model_code=_ej_p["model"] or "",
                             pcb_len=_vpcb, pcb_w=_vpcb_w,
                             pcs_panel=_vpcs,
                             cutting_len=_vpx, cutting_w=_vpy,
                             panel_len=_vpx, panel_w=_vpy,
                             sheet_len=_ej_p["sheet_len"] or 0, sheet_w=_ej_p["sheet_w"] or 0,
                             kerf_x=2.0, kerf_y=2.0, x_qty=_ej_p["x_qty"] or 0, y_qty=_ej_p["y_qty"] or 0,
                             cnc_margin_x=_ej_p["cnc_margin_x"] or 0, cnc_margin_y=_ej_p["cnc_margin_y"] or 0,
                             sheet_thickness=_ej_p["sheet_thickness"] or "", pcb_price=_ej_p["price"] or 0,
                             per_sq_inch=_ej_p["rs_pcb"] or 0)
                party_model = _ej_p["party_model"] or (f.get("party", "").strip() or "")
                model_code = _ej_p["model"] or ""
                prod_display = party_model if party_model else (_ej_p["model"] or "")
                product_str = f"{prod_display} · {int(f.get('qty', 0) or 0)} pcs" if prod_display else product_str
        if not pmodel:
            # MODEL zaroori — isi se PCB/panel/sheet saari details auto aati hai,
            # warna blank job card banta tha (sab 0/0.0).
            flash("FINISHED PRODUCT (MODEL) select karo — isi se PCB size, panel, "
                  "PCS/panel, sheet sab auto bharta hai. Bina model card blank banta hai.", "error")
            return redirect_with_token(url_for("jobcard_new"))
        qty_pcs = int(f.get("qty", 0) or 0)
        party_model = pmodel["name"] if pmodel else ""
        model_code = (pmodel["model_code"] or "") if pmodel else ""
        prod_display = f"{party_model} - {model_code}".strip(" -") if pmodel else ""
        product_str = f"{prod_display} · {qty_pcs} pcs" if prod_display else ""
        # --- auto calculation (CUT LIST jaisi): sheet size + kerf se har side ke panels ---
        # jaise 400(1200)×3 · 250(1000)×4 → 12 panels/sheet
        pcs_panel = int(pmodel["pcs_panel"] or 0) if pmodel else 0
        px0 = (pmodel["cutting_len"] or pmodel["panel_len"] or 0) if pmodel else 0
        py0 = (pmodel["cutting_w"] or pmodel["panel_w"] or 0) if pmodel else 0
        sheet_len = float(f.get("sheet_len", 0) or 0) or (
            float(pmodel["sheet_len"] or 0) if pmodel else 0)
        sheet_w = float(f.get("sheet_w", 0) or 0) or (
            float(pmodel["sheet_w"] or 0) if pmodel else 0)
        kfx = float(pmodel["kerf_x"] or 0) if pmodel else 0
        kfy = float(pmodel["kerf_y"] or 0) if pmodel else 0
        # v2.71 BEST LAYOUT (cutlist jaisa): NORMAL vs ROTATED vs MIXED rows \u2014
        # jis se saved panels/sheet = wizard screen wala number (24 vs 26 mismatch khatam)
        def _fl_div(a, b):
            return int(math.floor(a / b)) if b > 0 else 0
        # v3.50 \u2014 CNC margin cutting size ke andar: edge-to-edge fit (extra kerf spacing nahi)
        nx_ = _fl_div(sheet_len, px0) if px0 > 0 else 0
        ny_ = _fl_div(sheet_w, py0) if py0 > 0 else 0
        normal_ = nx_ * ny_
        rx_ = _fl_div(sheet_w, py0) if py0 > 0 else 0
        ry_ = _fl_div(sheet_len, px0) if px0 > 0 else 0
        rotated_ = rx_ * ry_
        perN_, perR_ = nx_, rx_
        hN_, hR_ = py0, px0
        maxN_ = _fl_div(sheet_w, hN_)
        maxM_ = _fl_div(sheet_len, hR_)
        mixed_ = mN_ = mM_ = 0
        for _n in range(maxN_ + 1):
            for _m in range(maxM_ + 1):
                if _n == 0 and _m == 0:
                    continue
                _hh = _n * hN_ + _m * hR_
                if _hh <= sheet_w + 1e-9:
                    _p = _n * perN_ + _m * perR_
                    if _p > mixed_:
                        mixed_, mN_, mM_ = _p, _n, _m
        if mixed_ > normal_ and mixed_ > rotated_:
            x_qty = perN_
            y_qty = mN_ + mM_
            panels_per_sheet = mixed_
        elif rotated_ > normal_:
            x_qty, y_qty = rx_, ry_
            panels_per_sheet = rotated_
        else:
            x_qty, y_qty = nx_, ny_
            panels_per_sheet = normal_
        if not panels_per_sheet:
            panels_per_sheet = (int(pmodel["panels_sheet"]) if "panels_sheet" in pmodel.keys() else 0) if pmodel else 0
        qty_panel = math.ceil(qty_pcs / pcs_panel) if pcs_panel and qty_pcs else 0
        # v2.73 fallback (bina-size card): model/panel-size kuch nahi to 1 panel/sheet rakho —
        # sheets = qty_panel, is order ka data 0-0 me na chale jaye
        if _edit_id_post and not panels_per_sheet and qty_panel > 0:
            panels_per_sheet = 1
        sheets = math.ceil(qty_panel / panels_per_sheet) if panels_per_sheet and qty_panel else 0
        price = float(f.get("value", 0) or 0) or (float(pmodel["pcb_price"] or 0) if pmodel else 0)
        rs_pcb = float(pmodel["per_sq_inch"] or 0) if pmodel else 0
        board_type = f.get("board_type", "Single Side").strip()
        board_side = ("SINGLE SIDE" if "SINGLE" in board_type.upper() else
                      ("DOUBLE SIDE" if "DOUBLE" in board_type.upper() else board_type.upper()))
        sheet_material = f.get("sheet_material", "").strip()
        thickness = f.get("sheet_thickness", "").strip()
        instructions = f.get("instructions", "").strip()
        delivery_date = f.get("delivery_date", "")
        priority = f.get("priority", "normal")
        # v2.85 \u2014 agar PURANA cached tab/form se POST aaya (naye fields POST me absent
        # hai) to un columns ko chhedo hi mat — warna user ki bhari values blank ho jati
        def _p85(name):
            return None if name not in f else (f.get(name) or "").strip()
        _odate79 = _p85("odate") or ""
        _ov79f = _p85("order_via") or ""
        _cb79f = _p85("created_by") or ""
        _ck79f = _p85("checked_by") or ""
        _cop79f = _p85("copper_finish") or ""
        _leg79f = _p85("legend_printing") or ""
        _mas79f = _p85("masking") or ""
        _po82 = _p85("po_no") or ""
        _fin84 = _p85("finish") or ""
        _typ84 = _p85("pcb_type") or ""
        # purani lines bhi chale rahi hai (new-form path me same values hi hoti hai)
        _odate79 = _odate79 if _odate79 is not None else ""
        # v2.79 \u2014 preview jaise extra fields ab ADVANCE page se bhi
        _odate79 = f.get("odate", "").strip()
        _ov79f = f.get("order_via", "").strip()
        _cb79f = f.get("created_by", "").strip()
        _ck79f = f.get("checked_by", "").strip()
        _cop79f = f.get("copper_finish", "").strip()
        _leg79f = f.get("legend_printing", "").strip()
        _mas79f = f.get("masking", "").strip()
        _cnx79f = f.get("cnc_margin_x", "").strip()
        _cny79f = f.get("cnc_margin_y", "").strip()
        _po82 = f.get("po_no", "").strip()

        # board_material auto-map (process flow ke liye): METAL naam me -> METAL CORE
        board_material = ""
        if sheet_material:
            board_material = ("METAL CORE PCB (META)" if any(
                w in sheet_material.upper() for w in ("METAL", "ALUMIN", "ALU"))
                else "FR4/CEM-1/FR1/XPC/OTHER")
        # ---- v2.71 EDIT MODE: naya order banane ke bajaye EXISTING order + jobcard UPDATE ----
        edit_id = _edit_id_post
        _e = db.query("SELECT * FROM orders WHERE id=?", (edit_id,), one=True) if edit_id else None
        if edit_id and not _e:
            edit_id = 0  # v2.85: order kahin aur se delete ho gaya (stale tab) \u2014 crash nahi, new save
        if edit_id:
            _ej = db.query("SELECT * FROM jobcard WHERE order_id=?", (edit_id,), one=True)
            _pri = priority if f.get("priority") else (_e["priority"] or "normal")
            _del = delivery_date if delivery_date else (_e["delivery_date"] or "")
            # v2.73: material khali submit ho to PURANA rakho (cutlist wale cards me
            # wizard ka material dropdown model-based hai — blank aa sakta hai)
            if not sheet_material and _ej:
                sheet_material = _ej["sheet_material"] or ""
            if not thickness and _ej:
                thickness = _ej["sheet_thickness"] or ""
            if not instructions and _ej:
                instructions = _ej["instructions"] or ""
            def _k85(_nv, _name, _old):
                # absent (purana form) => old; present => value (blank ho to bhi old rakho)
                if _nv is None and _name not in f:
                    return _old
                return _nv if _nv else _old
            _ov79 = _k85(_ov79f, "order_via", ((_ej["order_via"] if _ej else "") or ""))
            _cb79 = _k85(_cb79f, "created_by", ((_ej["created_by"] if _ej else "") or ""))
            _ck79 = _k85(_ck79f, "checked_by", ((_ej["checked_by"] if _ej else "") or ""))
            _cop79 = _k85(_cop79f, "copper_finish", ((_ej["copper_finish"] if _ej else "") or ""))
            _leg79 = _k85(_leg79f, "legend_printing", ((_ej["legend_printing"] if _ej else "") or ""))
            _mas79 = _k85(_mas79f, "masking", ((_ej["masking"] if _ej else "") or ""))
            _od79 = _k85(_odate79, "odate", (((_ej["odate"] or "")[:10] if _ej else "") or ""))
            _po82k = _k85(_po82, "po_no", ((_ej["po_no"] if _ej else "") or ""))
            _fin84k = _k85(_fin84, "finish", ((_ej["finish"] if _ej else "") or ""))
            _typ84k = _k85(_typ84, "pcb_type", ((_ej["pcb_type"] if _ej else "") or ""))
            _mmx79 = (float(_cnx79f) if _cnx79f else (((_ej["cnc_margin_x"] if _ej else 0) or 0) or (pmodel["cnc_margin_x"] or 0)))
            _mmy79 = (float(_cny79f) if _cny79f else (((_ej["cnc_margin_y"] if _ej else 0) or 0) or (pmodel["cnc_margin_y"] or 0)))
            _btype = board_type if f.get("board_type") else (_e["board"] or "Single Side")
            _bside = ("SINGLE SIDE" if "SINGLE" in _btype.upper() else
                      ("DOUBLE SIDE" if "DOUBLE" in _btype.upper() else _btype.upper()))
            _smat = sheet_material if sheet_material else ((_ej["sheet_material"] if _ej else "") or "")
            _thk = thickness if thickness else ((_ej["sheet_thickness"] if _ej else "") or (pmodel["sheet_thickness"] or ""))
            _instr = instructions if instructions else ((_ej["instructions"] if _ej else "") or "")
            _bmat = ("METAL CORE PCB (META)" if any(w in _smat.upper() for w in ("METAL", "ALUMIN", "ALU"))
                     else ("FR4/CEM-1/FR1/XPC/OTHER" if _smat else (_ej["board_material"] if _ej else "")))
            vgr = ""
            if (pmodel["pcb_len"] or 0) > 0 and (pmodel["pcb_w"] or 0) > 0:
                vgr = f"{float(pmodel['pcb_len']):g}\u00d7{float(pmodel['pcb_w']):g}"
            db.execute("UPDATE orders SET party=?, board=?, product=?, qty=?, value=?, priority=?, "
                       "delivery_date=?, qty_panel=?, pcs_panel=? WHERE id=?",
                       (party, _btype, product_str, qty_pcs, round(price * qty_pcs, 2), _pri, _del,
                        qty_panel, pcs_panel, edit_id))
            db.execute("INSERT OR IGNORE INTO jobcard (order_id) VALUES (?)", (edit_id,))
            db.execute(
                "UPDATE jobcard SET party_model=?, model=?, price=?, rs_pcb=?, total_qty=?, "
                "actual_pcb_x=?, actual_pcb_y=?, x_size=?, y_size=?, x_qty=?, y_qty=?, "
                "cnc_margin_x=?, cnc_margin_y=?, pcb_gap=?, panel_x=?, panel_y=?, panels_per_sheet=?, "
                "sheets=?, pcs_panel=?, qty_panel=?, sheet_len=?, sheet_w=?, board_type=?, board_side=?, "
                "board_material=?, sheet_material=?, sheet_thickness=?, instructions=?, v_grooving=?, "
                "exp_delivery=?, odate=?, order_via=?, created_by=?, checked_by=?, copper_finish=?, "
                "legend_printing=?, masking=?, po_no=?, finish=?, pcb_type=? WHERE order_id=?",
                (party_model, model_code, price, rs_pcb, qty_pcs,
                 pmodel["pcb_len"] or 0, pmodel["pcb_w"] or 0, px0, py0, x_qty, y_qty,
                 _mmx79, _mmy79, _fl(f.get("pcb_gap")),
                 px0, py0, panels_per_sheet, sheets, pcs_panel, qty_panel, sheet_len, sheet_w,
                 _btype, _bside, _bmat, _smat, _thk, _instr, vgr, _del,
                 _od79, _ov79, _cb79, _ck79, _cop79, _leg79, _mas79, _po82k, _fin84k, _typ84k, edit_id))
            flash(f"Job card {_e['order_no']} SAVE ho gaya (ADVANCE JOB CARD se edit) \u2014 "
                  f"qty {qty_pcs} pcs, {panels_per_sheet} panels/sheet.", "success")
            return redirect_with_token(url_for("jobcard", order_id=edit_id))

        count = db.query("SELECT COUNT(*) c FROM orders", one=True)["c"]
        new_id = db.execute(
            "INSERT INTO orders (order_no, party, board, product, qty, value, current_process, "
            "status, progress, priority, delivery_date, operator, started_qty, finished_qty, "
            "created_on, qty_panel, pcs_panel) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"#{count + 1}", party, board_type, product_str, qty_pcs, round(price * qty_pcs, 2),
             PROCESS_STEPS[0], "pending", 0, priority, delivery_date, "", 0, 0,
             _now_dt(), qty_panel, pcs_panel))
        today = _today_ist().isoformat()
        if pmodel:
            px = pmodel["cutting_len"] or pmodel["panel_len"] or 0
            py = pmodel["cutting_w"] or pmodel["panel_w"] or 0
            vgr = ""
            if (pmodel["pcb_len"] or 0) > 0 and (pmodel["pcb_w"] or 0) > 0:
                vgr = f"{float(pmodel['pcb_len']):g}×{float(pmodel['pcb_w']):g}"
            db.execute(
                "INSERT OR IGNORE INTO jobcard (order_id, party_model, model, odate, exp_delivery, "
                "price, rs_pcb, total_qty, actual_pcb_x, actual_pcb_y, x_size, y_size, x_qty, y_qty, "
                "cnc_margin_x, cnc_margin_y, pcb_gap, panel_x, panel_y, panels_per_sheet, sheets, pcs_panel, "
                "qty_panel, sheet_len, sheet_w, board_type, board_side, board_material, sheet_material, "
                "sheet_thickness, instructions, v_grooving, order_via, created_by, checked_by, "
                "copper_finish, legend_printing, masking, po_no, finish, pcb_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id, party_model, model_code, (_odate79 or _now_dt()), delivery_date, price, rs_pcb, qty_pcs,
                 pmodel["pcb_len"] or 0, pmodel["pcb_w"] or 0, px, py,
                 x_qty, y_qty,
                 (float(_cnx79f) if _cnx79f else (pmodel["cnc_margin_x"] or 0)),
                 (float(_cny79f) if _cny79f else (pmodel["cnc_margin_y"] or 0)), _fl(f.get("pcb_gap")),
                 px, py, panels_per_sheet, sheets, pcs_panel, qty_panel,
                 sheet_len, sheet_w,
                 board_type, board_side, board_material, sheet_material,
                 thickness if thickness else (pmodel["sheet_thickness"] or ""),
                 instructions, vgr, _ov79f, _cb79f, _ck79f, _cop79f, _leg79f, _mas79f, _po82, _fin84, _typ84))
        else:
            db.execute(
                "INSERT OR IGNORE INTO jobcard (order_id, party_model, odate, exp_delivery, price, "
                "total_qty, qty_panel, pcs_panel, sheets, board_type, board_side, sheet_material, "
                "sheet_thickness, instructions, order_via, created_by, checked_by, copper_finish, "
                "legend_printing, masking, po_no, finish, pcb_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id, "", (_odate79 or today), delivery_date, price, qty_pcs, qty_panel, pcs_panel, sheets,
                 board_type, board_side, sheet_material, thickness, instructions,
                 _ov79f, _cb79f, _ck79f, _cop79f, _leg79f, _mas79f, _po82, _fin84, _typ84))
        flash(f"Job card #{count + 1} create ho gaya ✅ — saari details MODEL se auto bhari hai, verify kar lo.", "success")
        return redirect_with_token(url_for("jobcard", order_id=new_id))
    return render_template("jobcard_new.html", active="orders", parties=parties, models=models,
                           inv_items=inv_items, thicknesses=thicknesses,
                           today=_today_ist().isoformat(),
                           edit_id=edit_id, edit_order=edit_order, edit_jc=edit_jc,
                           edit_model_id=edit_model_id)


def _jc_autofill(order_id):
    """v2.78 \u2014 jobcard row ki KHAALI details auto-heal (idempotent):
    product string ('MODEL - CODE ... N pcs') se finished product dhundo, blank
    sizing/price us se bharo; warna orders ke qty_panel/pcs_panel/board/delivery copy karo."""
    o = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o:
        return None
    jc = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
    if not jc:
        return jc
    sets, vals = [], []
    pmodel = None
    if not (jc["party_model"] or "").strip():
        base = (o["product"] or "").split(" \u00b7 ")[0].strip()
        cands = []
        if " - " in base:
            cands += [base.split(" - ")[0].strip(), base.split(" - ")[-1].strip()]
        cands.append(base)
        for c in cands:
            if not c:
                continue
            pmodel = db.query("SELECT * FROM product_models WHERE name=? OR model_code=? ORDER BY id LIMIT 1",
                              (c, c), one=True)
            if pmodel:
                break
        if pmodel:
            sets.append("party_model=?")
            vals.append(pmodel["name"])
            if pmodel["model_code"] and not (jc["model"] or "").strip():
                sets.append("model=?")
                vals.append(pmodel["model_code"])
    else:
        pmodel = db.query("SELECT * FROM product_models WHERE name=?", (jc["party_model"],), one=True)

    def _z(col):
        return not (jc[col] or 0)

    if pmodel:
        for col, v in (("actual_pcb_x", pmodel["pcb_len"]), ("actual_pcb_y", pmodel["pcb_w"]),
                       ("panel_x", pmodel["panel_len"]), ("panel_y", pmodel["panel_w"]),
                       ("x_size", pmodel["cutting_len"] or pmodel["panel_len"]),
                       ("y_size", pmodel["cutting_w"] or pmodel["panel_w"]),
                       ("x_qty", pmodel["x_qty"]), ("y_qty", pmodel["y_qty"]),
                       ("cnc_margin_x", pmodel["cnc_margin_x"]), ("cnc_margin_y", pmodel["cnc_margin_y"]),
                       ("sheet_len", pmodel["sheet_len"]), ("sheet_w", pmodel["sheet_w"]),
                       ("panels_per_sheet", pmodel["panels_sheet"]), ("sheets", pmodel["sheets"]),
                       ("pcs_panel", pmodel["pcs_panel"])):
            if _z(col) and (v or 0) > 0:
                sets.append(col + "=?")
                vals.append(v)
        if _z("price") and (pmodel["pcb_price"] or 0) > 0:
            sets.append("price=?")
            vals.append(pmodel["pcb_price"])
        if _z("rs_pcb") and (pmodel["per_sq_inch"] or 0) > 0:
            sets.append("rs_pcb=?")
            vals.append(pmodel["per_sq_inch"])
        if not (jc["sheet_material"] or "").strip() and "sheet_material" in pmodel.keys() \
                and (pmodel["sheet_material"] or "").strip():
            sets.append("sheet_material=?")
            vals.append(pmodel["sheet_material"])
        if not (jc["sheet_thickness"] or "").strip() and (pmodel["sheet_thickness"] or "").strip():
            sets.append("sheet_thickness=?")
            vals.append(pmodel["sheet_thickness"])
    if _z("pcs_panel") and (o["pcs_panel"] or 0) > 0:
        sets.append("pcs_panel=?")
        vals.append(o["pcs_panel"])
    if _z("qty_panel") and (o["qty_panel"] or 0) > 0:
        sets.append("qty_panel=?")
        vals.append(o["qty_panel"])
    # v2.80b: sheets STALE bhi ho to re-sync (qty badalne par 58 vs 116 jaisa mismatch nahi rahega)
    if (jc["panels_per_sheet"] or 0) > 0 and (jc["qty_panel"] or 0) > 0:
        _want_sheets80 = math.ceil(jc["qty_panel"] / jc["panels_per_sheet"])
        if (jc["sheets"] or 0) != _want_sheets80:
            sets.append("sheets=?")
            vals.append(_want_sheets80)
    if not (jc["board_side"] or "").strip() and (o["board"] or "").strip():
        sets.append("board_side=?")
        vals.append(o["board"].strip().upper())
    if not (jc["odate"] or "").strip() and (o["created_on"] or "")[:10]:
        sets.append("odate=?")
        vals.append(o["created_on"][:10])  # v2.82: DATE blank na rahe
    if not (jc["exp_delivery"] or "").strip() and (o["delivery_date"] or "").strip():
        sets.append("exp_delivery=?")
        vals.append(o["delivery_date"])
    if sets:
        vals.append(order_id)
        db.execute("UPDATE jobcard SET " + ", ".join(sets) + " WHERE order_id=?", tuple(vals))
        jc = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
    return jc


@app.route("/jobcard/<int:order_id>")
@login_required
def jobcard(order_id):
    order, jc, procs = prep_jobcard(order_id)
    if not order:
        flash("Order not found.", "error")
        return redirect_with_token(url_for("orders"))
    jc = _jc_autofill(order_id) or jc  # v2.78: blank details auto-heal (product/orders se)
    qty_pcs = order["qty"]
    # AUTO-HEAL: job card khulte hi QTY sync — order.qty hi SACH hai.
    # (Cut list se apply / advance create ke baad jc.total_qty purana/khaali reh jata tha —
    #  isliye job card me value khaali ya galat dikhti thi.)
    _jc_tot = jc["total_qty"] or 0
    _jc_pan = jc["qty_panel"] or 0
    _jc_pps = jc["pcs_panel"] or 0
    _sets, _vals = [], []
    if _jc_tot != qty_pcs:
        _sets.append("total_qty=?"); _vals.append(qty_pcs)
    if qty_pcs > 0 and _jc_pps > 0 and _jc_pan != math.ceil(qty_pcs / _jc_pps):
        _sets.append("qty_panel=?"); _vals.append(math.ceil(qty_pcs / _jc_pps))
    if _sets:
        _vals.append(order_id)
        db.execute(f"UPDATE jobcard SET {', '.join(_sets)} WHERE order_id=?", tuple(_vals))
        jc = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
    # QTY OF PANEL display default: saved nahi hai to selected model ke pcs/panel se nikaalo
    qty_panel_disp = 0
    bmodel_early = db.query("SELECT * FROM product_models WHERE name=?", (jc["party_model"],), one=True)
    if not (jc["qty_panel"] or 0) and bmodel_early and (bmodel_early["pcs_panel"] or 0) > 0 and qty_pcs > 0:
        qty_panel_disp = math.ceil(qty_pcs / bmodel_early["pcs_panel"])
    jc_logs = db.query("SELECT * FROM jobcard_log WHERE order_id=? ORDER BY id DESC LIMIT 15", (order_id,))
    dispatch_logs = db.query("SELECT * FROM dispatch_log WHERE order_id=? ORDER BY id DESC", (order_id,))
    dispatch = dispatch_logs[0] if dispatch_logs else None
    _plist, proc_label = db.process_preset_for(jc["board_side"], jc["board_material"])
    procs_started = any(p["status"] != "pending" for p in procs)
    models = db.query("SELECT * FROM product_models ORDER BY name")
    inv_items = db.query("SELECT * FROM inventory ORDER BY CASE WHEN stock<=min_stock THEN 0 ELSE 1 END, category, name")
    inv_names = {it["name"] for it in inv_items}
    bmodel = db.query("SELECT * FROM product_models WHERE name=?", (jc["party_model"],), one=True)
    # PRICE HISTORY: is model ka last price + har model ka (dropdown ke liye)
    _ph_ensure()
    last_price = None
    if jc["party_model"]:
        last_price = db.query("SELECT * FROM price_history WHERE model_name=? ORDER BY id DESC LIMIT 1",
                              (jc["party_model"],), one=True)
    lp_map = {}
    for lp in db.query("SELECT model_name, price, ddate, party, order_no FROM price_history ORDER BY id DESC"):
        if lp["model_name"] not in lp_map:
            lp_map[lp["model_name"]] = lp
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
    machines = db.query("SELECT * FROM machines ORDER BY code")
    all_employees = db.query("SELECT * FROM employees WHERE status='active' ORDER BY name")
    thicknesses = [r["thickness"] for r in db.query(
        "SELECT DISTINCT thickness FROM thickness_rates WHERE thickness!='' ORDER BY thickness")]
    # v2.74 \u2014 ADVANCE preview visuals: PANEL (PCB grid) + SHEET (panels grid)
    def _viz_grid(cols, rows, count, cls, lbl):
        cols = max(1, min(int(cols or 0) or 1, 12)); rows = max(1, min(int(rows or 0) or 1, 12))
        count = max(1, min(int(count or 0) or 1, cols * rows))
        gap = 5.0
        cw = (280.0 - (cols - 1) * gap) / cols
        ch = (148.0 - (rows - 1) * gap) / rows
        parts = []
        for i in range(count):
            r, c = divmod(i, cols)
            parts.append(f'<rect x="{6 + c * (cw + gap):.1f}" y="{6 + r * (ch + gap):.1f}" '
                         f'width="{cw:.1f}" height="{ch:.1f}" rx="3"/>')
        return ('<svg viewBox="0 0 292 176" class="' + cls + '" role="img">' + "".join(parts)
                + '<text x="146" y="172" text-anchor="middle" class="vizLbl">' + lbl + '</text></svg>')

    # v2.80 \u2014 MIXED layout breakdown: saved sizing se best-layout recompute karo
    # (cutlist jaisa hi engine) \u2014 mixed nikla to X/Y/PCB-in-sheet cells me split dikhega
    mix79 = None
    try:
        if (jc["actual_pcb_x"] or 0) > 0 and (jc["actual_pcb_y"] or 0) > 0 \
                and (jc["sheet_len"] or 0) > 0 and (jc["sheet_w"] or 0) > 0:
            # PANEL ko hi unit maano: pcb_len/w = PANEL size (x_size/cutting), grid 1x1.
            # (x_qty/y_qty = sheet me panels across/rows hote hai, PCB-grid nahi)
            _px80 = (jc["x_size"] or jc["panel_x"] or 0)
            _py80 = (jc["y_size"] or jc["panel_y"] or 0)
            _f80 = {"use": "1", "pcb_len": str(_px80), "pcb_w": str(_py80),
                    "pcbs_x": "1", "pcbs_y": "1",
                    "gap_x": "0", "gap_y": "0",
                    "border_l": "0", "border_r": "0", "border_t": "0", "border_b": "0",
                    "gang_x": "1", "gang_y": "1",
                    "sheet_len": str(jc["sheet_len"]), "sheet_w": str(jc["sheet_w"]),
                    "kerf_x": str(jc.get("kerf_x") or 0), "kerf_y": str(jc.get("kerf_y") or 0),
                    "sheets": "1", "panel_len": "0", "panel_w": "0"}
            _r80 = compute_layout(_f80)
            if _r80 and _r80.get("best") == "mixed" and (_r80.get("mixed_n") or 0) > 0 \
                    and (_r80.get("mixed_m") or 0) > 0:
                mix79 = {"xn": _r80["mixed_n"], "xp": _r80["per_normal"],
                         "yn": _r80["mixed_m"], "yp": _r80["per_rot"],
                         "xt": _r80["mixed_n"] * (_r80["per_normal"] or 0),
                         "yt": _r80["mixed_m"] * (_r80["per_rot"] or 0),
                         "total": _r80["panels_per_sheet"] or 0}
    except Exception:
        mix79 = None
    viz_panel_svg = viz_sheet_svg = ""
    _v_xq, _v_yq = int(jc["x_qty"] or 0), int(jc["y_qty"] or 0)
    _v_pcs = jc["pcs_panel"] or (_v_xq * _v_yq)
    if _v_xq > 0 and _v_yq > 0 and _v_pcs:
        viz_panel_svg = _viz_grid(_v_xq, _v_yq, _v_pcs, "vizP",
                                  f"PANEL {(jc['x_size'] or 0):g}×{(jc['y_size'] or 0):g} mm · {_v_pcs} PCS")
    _v_pps = int(jc["panels_per_sheet"] or 0)
    if _v_pps > 0:
        _v_c = math.isqrt(_v_pps)
        while _v_c > 1 and _v_pps % _v_c:
            _v_c -= 1
        _v_r = (_v_pps + _v_c - 1) // _v_c
        viz_sheet_svg = _viz_grid(_v_c, _v_r, _v_pps, "vizS",
                                  f"SHEET {(jc['sheet_len'] or 0):g}×{(jc['sheet_w'] or 0):g} mm · {_v_pps} PANELS/SHEET")
    return render_template("jobcard.html", active="orders", order=order, jc=jc, procs=procs,
                           machines=machines, all_employees=all_employees,
                           flat_next=db.all_process_options(),
                           thicknesses=thicknesses,
                           qty_pcs=qty_pcs, qty_panel_disp=qty_panel_disp, models=models, proc_label=proc_label, jc_logs=jc_logs,
                           procs_started=procs_started,
                           dispatch=dispatch, dispatch_logs=dispatch_logs,
                           inv_items=inv_items, inv_names=inv_names,
                           bom_rows=bom_rows, bmodel=bmodel, last_price=last_price, lp_map=lp_map,
                           issues=issues, issue_total=issue_total, worker_names=worker_names,
                           viz_panel_svg=viz_panel_svg, viz_sheet_svg=viz_sheet_svg, mix79=mix79,
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
    for k in ["party_model", "model", "odate", "created_by", "checked_by", "order_via",
              "exp_delivery", "payment_status", "sheet_material", "copper_finish", "masking", "finish",
              "legend_printing", "pcb_type", "customer_req", "short_reason",
              "sheet_thickness", "instructions", "handover_sign"]:
        sets.append(f"{k}=?")
        vals.append(f.get(k, "").strip())
    # V-GROOVING auto: khaali chhoda to selected finished product ke SINGLE PCB size se bhar do
    vgr = f.get("v_grooving", "").strip()
    if not vgr:
        bm = db.query("SELECT * FROM product_models WHERE name=?", (party_model,), one=True)
        if bm:
            _pl, _pw = _jc_num(bm.get("pcb_len")), _jc_num(bm.get("pcb_w"))
            if _pl > 0 and _pw > 0:
                vgr = f"{_pl:g}×{_pw:g}"
    sets.append("v_grooving=?")
    vals.append(vgr)
    sets.append("raw_materials=?")
    vals.append(rm_val)
    for k in ["price", "rs_pcb"]:
        sets.append(f"{k}=?")
        vals.append(_jc_num(f.get(k)))
    for k in ["actual_pcb_x", "actual_pcb_y", "x_size", "y_size", "cnc_margin_x", "cnc_margin_y",
              "panel_x", "panel_y", "sheet_len", "sheet_w", "pcb_gap"]:
        sets.append(f"{k}=?")
        vals.append(_jc_num(f.get(k)))
    for k in ["x_qty", "y_qty", "panels_per_sheet", "sheets",
              "total_qty", "short_qty"]:
        sets.append(f"{k}=?")
        vals.append(int(f.get(k, 0) or 0))
    # PCS/PANEL auto: form me khaali ho to selected finished product se le lo
    pcs_panel = int(f.get("pcs_panel", 0) or 0)
    if not pcs_panel:
        _bm3 = db.query("SELECT * FROM product_models WHERE name=?", (party_model,), one=True)
        if _bm3:
            pcs_panel = int(_bm3.get("pcs_panel") or 0)
    sets.append("pcs_panel=?")
    vals.append(pcs_panel)
    # QTY OF PANEL auto: user ne nahi bhara to order qty ÷ pcs/panel se khud nikaalo
    qty_panel = int(f.get("qty_panel", 0) or 0)
    if not qty_panel and pcs_panel > 0 and qty_pcs > 0:
        qty_panel = math.ceil(qty_pcs / pcs_panel)
    sets.append("qty_panel=?")
    vals.append(qty_panel)

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
    # PRICE HISTORY: price badla ho to record karo (item kis price pe chal raha)
    new_price = _jc_num(f.get("price"))
    if new_price > 0 and (float(jc["price"] or 0) != new_price
                          or float(jc["rs_pcb"] or 0) != _jc_num(f.get("rs_pcb"))):
        _record_price(party_model, model, new_price, _jc_num(f.get("rs_pcb")), order_id,
                      order["order_no"], f.get("party", "").strip(),
                      _today_ist().isoformat(), _model_thickness(party_model))
    flash("Job card saved. All changes saved.", "success")
    return redirect_with_token(url_for("jobcard", order_id=order_id))


def _dt_pair(f, d_key, t_key):
    d, t = f.get(d_key, ""), f.get(t_key, "")
    return f"{d} {t}".strip() if (d and t) else ""


@app.route("/jobcard/<int:order_id>/preset", methods=["POST"])
@login_required
def jobcard_preset(order_id):
    """SIDE + MATERIAL chuno -> uske related process flow table mein set ho jata hai.
    Ye jobcard_process rows replace karta hai — table, Operator view aur print
    sab isi table se aata hai, isliye 'sab jagah' wahi flow dikhta hai.
    JSON return karta hai (chhota response) — page JS ek baar reload karta hai,
    isse redirect + full page double download nahi hota (speed ke liye)."""
    if session.get("user_role") != "admin":
        return jsonify({"ok": False, "msg": "Sirf admin process flow set kar sakta hai."})
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        return jsonify({"ok": False, "msg": "Order not found."})
    side = (request.form.get("side") or "").strip().upper()
    material = (request.form.get("material") or "").strip()
    if not side:
        return jsonify({"ok": False, "msg": "Pehle SIDE chuno (SINGLE SIDE ya DOUBLE SIDE)."})
    plist, label = db.process_preset_for(side, material)
    force = request.form.get("force") == "1"
    rows = db.get_jc_processes(order_id)
    has_data = any((r["start_dt"] or r["end_dt"] or r["qty"] or r["start_name"] or r["end_name"])
                   for r in rows)
    if has_data and not force:
        return jsonify({"ok": False, "msg": "⚠️ Process table par kaam shuru ho chuka hai (dates/qty bhare hain). "
                                           "Dobara Apply dabao to purani entries hatke naya flow set hoga."})
    db.execute("DELETE FROM jobcard_process WHERE order_id=?", (order_id,))
    for i, p in enumerate(plist):
        # tool slot row -> dropdown bane, isliye pehla option ("TOOL") insert hota hai
        proc_name = db.JC_TOOL_OPTIONS[0] if p == db.JC_TOOL_SLOT else p
        db.execute("INSERT INTO jobcard_process (order_id, process, next_process, ord) VALUES (?,?,?,?)",
                   (order_id, proc_name, "", (i + 1) * 10))
    db.execute("UPDATE jobcard SET board_side=?, board_material=? WHERE order_id=?",
               (("SINGLE SIDE" if "SINGLE" in side else "DOUBLE SIDE"), material, order_id))
    db.execute("UPDATE orders SET current_process=? WHERE id=?", (plist[0], order_id))
    flash(f"✅ {label} — process flow set ho gaya. Ye table, Operator view aur Job Card print mein sab jagah lagega.",
          "success")
    return jsonify({"ok": True, "msg": label})


@app.route("/jobcard/<int:order_id>/flow_preview")
@login_required
def jobcard_flow_preview(order_id):
    """⚙️ PROCESS FLOW PREVIEW — Apply se PEHLE dikhao ki kaunse processes set honge.
    JSON: plist (order me), label, has_data (table par kaam shuru hai kya), current rows."""
    if session.get("user_role") != "admin":
        return jsonify({"ok": False, "msg": "Sirf admin process flow set kar sakta hai."})
    order = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not order:
        return jsonify({"ok": False, "msg": "Order not found."})
    side = (request.args.get("side") or "").strip().upper()
    material = (request.args.get("material") or "").strip()
    if not side:
        return jsonify({"ok": False, "msg": "Pehle SIDE chuno (SINGLE SIDE ya DOUBLE SIDE)."})
    plist, label = db.process_preset_for(side, material)
    rows = db.get_jc_processes(order_id)
    has_data = any((r["start_dt"] or r["end_dt"] or r["qty"] or r["start_name"] or r["end_name"])
                   for r in rows)
    current = [r["process"] for r in rows]
    return jsonify({"ok": True, "plist": plist, "label": label, "has_data": has_data,
                    "current": current, "count": len(plist)})


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
        # per-process assignment: kaun karega + kaunsi machine (PENDING PROCESS)
        # multi-employee: 2+ bande milkar karte hain to saare selects se naam lo
        asg_machine = f.get(f"asg_machine_{pid}", "").strip()
        op_names = []
        for x in request.form.getlist(f"asg_op_{pid}"):
            x = x.strip()
            if x and x not in op_names:
                op_names.append(x)
        asg_op = ", ".join(op_names)
        if asg_op or asg_machine:
            db.execute(
                "INSERT OR REPLACE INTO order_assignments (order_id, process, machine, shift, operator, planned_start) "
                "VALUES (?,?,?,?,?,?)",
                (order_id, r["process"], asg_machine or "Unassigned", "Day", asg_op or "Unassigned", ""))
        else:
            db.execute("DELETE FROM order_assignments WHERE order_id=? AND process=?",
                       (order_id, r["process"]))
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
    ddate = f.get("ddate", "") or _today_ist().isoformat()
    dtime = f.get("dtime", "") or _now_ist().strftime("%H:%M")
    by = f.get("dispatched_by", "").strip() or session.get("user_name", "")
    ph_name, ph_mime, ph_data = "", "", None
    photo = request.files.get("photo")
    if photo and photo.filename:
        ext = (photo.filename.rsplit(".", 1)[-1] if "." in photo.filename else "").lower()
        blob = photo.read()
        if ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic") and len(blob) <= 3 * 1024 * 1024:
            ph_name, ph_mime, ph_data = photo.filename, photo.mimetype or "image/png", blob
        else:
            flash("📷 Photo nahi lagi (sirf image, max 3MB) — dispatch bina photo ke ho gaya.", "error")
    db.execute("INSERT INTO dispatch_log (order_id, ddate, dtime, mode, details, dispatched_by, ts, "
               "photo_name, photo_mime, photo_data) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (order_id, ddate, dtime, mode, f.get("details", "").strip(), by,
                _now_ist().strftime("%Y-%m-%d %H:%M"), ph_name, ph_mime, ph_data))
    # PRICE HISTORY: dispatch ke waqt item kis price pe gaya — record karo
    jcd = db.query("SELECT * FROM jobcard WHERE order_id=?", (order_id,), one=True)
    ord2 = db.query("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    # FG STOCK: PCB dispatch hone par finished product ka stock LESS hota hai
    # (agar stock bana hai) — ek order par ek hi baar deduct (repeat dispatch par dobara nahi)
    if jcd and jcd.get("party_model") and ord2:
        pmd = db.query("SELECT * FROM product_models WHERE name=?", (jcd["party_model"],), one=True)
        if pmd and (pmd["fg_stock"] or 0) > 0 and not (jcd.get("fg_deducted") or 0):
            dq = int(ord2["qty"] or 0)
            if dq > 0:
                new_stock = max(0, (pmd["fg_stock"] or 0) - dq)
                deducted = (pmd["fg_stock"] or 0) - new_stock
                db.execute("UPDATE product_models SET fg_stock=? WHERE id=?", (new_stock, pmd["id"]))
                db.execute("UPDATE jobcard SET fg_deducted=1 WHERE order_id=?", (order_id,))
                flash(f"📦 FG STOCK: '{jcd['party_model']}' −{deducted} PCS (dispatch) → ab {new_stock} PCS.",
                      "success" if deducted == dq else "error")
    if jcd and (jcd["price"] or 0) > 0 and ord2:
        _record_price(jcd["party_model"], jcd["model"], jcd["price"], jcd["rs_pcb"], order_id,
                      ord2["order_no"], ord2["party"], ddate, _model_thickness(jcd["party_model"]))
    # JOB CARD + PREVIEW: Dispatch row me date/time/name auto bharo (row done)
    _mark_dispatch_process(order_id, by, ddate, dtime)
    flash(f"PCB dispatched: {mode} · {ddate} {dtime}." + (" 📷 Photo bhi save hui." if ph_data else ""), "success")
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
         _today_ist().isoformat()))
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
    today = _today_ist()
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
    today = _today_ist()
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
    rtype = request.args.get("type") or "dispatch"
    period = request.args.get("period") or "monthly"
    if rtype not in ("dispatch", "production", "salary"):
        rtype = "dispatch"
    if period not in ("daily", "monthly", "yearly"):
        period = "monthly"
    today = _today_ist()
    sel_date = request.args.get("date") or today.isoformat()
    sel_month = request.args.get("month") or today.strftime("%Y-%m")
    sel_year = request.args.get("year") or str(today.year)
    try:
        datetime.date.fromisoformat(sel_date)
    except ValueError:
        sel_date = today.isoformat()
    if not _valid_month(sel_month):
        sel_month = today.strftime("%Y-%m")
    if not (sel_year.isdigit() and len(sel_year) == 4):
        sel_year = str(today.year)
    y, mo = int(sel_month[:4]), int(sel_month[5:7])
    period_label = {"daily": datetime.date.fromisoformat(sel_date).strftime("%d %b %Y"),
                    "monthly": datetime.date(y, mo, 1).strftime("%B %Y"),
                    "yearly": sel_year}[period]
    d = {"type": rtype, "period": period, "date": sel_date, "month": sel_month, "year": sel_year,
         "period_label": period_label,
         "type_label": {"dispatch": "Dispatch", "production": "Production", "salary": "Employee Salary"}[rtype],
         "active_orders": {"running": 0, "pending": 0, "done": 0, "total": 0}}

    if rtype == "dispatch":
        pattern = sel_date if period == "daily" else (sel_month + "%" if period == "monthly" else sel_year + "%")
        rows = db.query("SELECT dl.*, o.order_no, o.party, o.value, o.qty, o.product, "
                        "jc.party_model, jc.model FROM dispatch_log dl "
                        "LEFT JOIN orders o ON o.id=dl.order_id "
                        "LEFT JOIN jobcard jc ON jc.order_id=o.id "
                        "WHERE dl.ddate LIKE ? "
                        "ORDER BY dl.ddate DESC, dl.id DESC", (pattern,))
        d["rows"] = rows
        d["total"] = len(rows)
        seen = {}
        for r in rows:
            seen[r["order_id"]] = (r["value"] or 0) + (r["qty"] or 0)
        d["orders"] = len(seen)
        d["value"] = round(sum(r["value"] or 0 for r in rows), 0)
        d["qty"] = sum(r["qty"] or 0 for r in rows)
        modes = {}
        for r in rows:
            modes[r["mode"] or "Other"] = modes.get(r["mode"] or "Other", 0) + 1
        d["modes"] = sorted(modes.items(), key=lambda x: -x[1])
        d["photos"] = sum(1 for r in rows if r["photo_data"])

    elif rtype == "production":
        pattern = sel_date if period == "daily" else (sel_month + "%" if period == "monthly" else sel_year + "%")
        d["rows"] = db.query("SELECT * FROM orders WHERE created_on LIKE ? ORDER BY id DESC", (pattern,))
        d["total"] = len(d["rows"])
        d["value"] = round(sum(r["value"] or 0 for r in d["rows"]), 0)
        d["qty"] = sum(r["qty"] or 0 for r in d["rows"])
        snap = db.query("SELECT COUNT(*) total, "
                        "COALESCE(SUM(CASE WHEN status='running' THEN 1 ELSE 0 END),0) running, "
                        "COALESCE(SUM(CASE WHEN status='hold' THEN 1 ELSE 0 END),0) hold, "
                        "COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) pending, "
                        "COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) done "
                        "FROM orders", one=True)
        d["active_orders"] = {"total": snap["total"] or 0, "running": snap["running"] or 0,
                              "hold": snap["hold"] or 0,
                              "pending": snap["pending"] or 0, "done": snap["done"] or 0}
        d["procs_done"] = db.query("SELECT COUNT(*) c FROM jobcard_process WHERE end_dt LIKE ?",
                                   (pattern,), one=True)["c"]
        d["procs_started"] = db.query("SELECT COUNT(*) c FROM jobcard_process WHERE start_dt LIKE ?",
                                      (pattern,), one=True)["c"]

    else:  # salary
        if period == "yearly":
            months_in_year = [m for m in range(1, 13)]
            cur_ym = today.strftime("%Y-%m")
            ag = {}
            for r in db.query("SELECT emp_id, substr(date,1,7) AS m, status, COUNT(*) AS c FROM attendance "
                              "WHERE date LIKE ? GROUP BY emp_id, m, status", (sel_year + "%",)):
                key = (r["emp_id"], r["m"])
                ag.setdefault(key, {"present": 0, "absent": 0, "leave": 0, "halfday": 0, "late": 0})
                if r["status"] in ag[key]:
                    ag[key][r["status"]] = r["c"]
            adv = {}
            paid = {}
            for r in db.query("SELECT emp_id, COALESCE(SUM(advance),0) a, SUM(CASE WHEN paid THEN 1 ELSE 0 END) p "
                              "FROM emp_salary WHERE month LIKE ? GROUP BY emp_id", (sel_year + "%",)):
                adv[r["emp_id"]] = r["a"]
                paid[r["emp_id"]] = r["p"]
            rows = []
            for e in db.query("SELECT * FROM employees WHERE status='active' ORDER BY id"):
                r = dict(e)
                cnt = {"present": 0, "absent": 0, "leave": 0, "halfday": 0, "late": 0}
                earned = 0.0
                sal = float(e["salary"] or 0)
                for mnum in months_in_year:
                    ym = f"{sel_year}-{mnum:02d}"
                    if sel_year == str(today.year) and ym > cur_ym:
                        continue
                    dim = calendar.monthrange(int(sel_year), mnum)[1]
                    a = ag.get((e["id"], ym))
                    if not a:
                        earned += sal
                        continue
                    ded = (a["absent"] + 0.5 * a["halfday"]) * (sal / dim if dim else 0)
                    earned += max(0.0, sal - ded)
                    for k in cnt:
                        cnt[k] += a[k]
                r.update(cnt)
                r["earned"] = round(earned)
                r["advance"] = float(adv.get(e["id"], 0) or 0)
                r["net"] = round(earned - r["advance"])
                r["paid_months"] = int(paid.get(e["id"], 0) or 0)
                rows.append(r)
            d["rows"] = rows
            d["payroll"] = round(sum((x["salary"] or 0) * 12 for x in rows), 0)
            d["earned_tot"] = sum(x["earned"] for x in rows)
            d["advance_tot"] = round(sum(x["advance"] for x in rows), 0)
            d["net_tot"] = sum(x["net"] for x in rows)
        else:
            rows = []
            for e in db.query("SELECT * FROM employees WHERE status='active' ORDER BY id"):
                m = _emp_month(e["id"], sel_month)
                rows.append({"id": e["id"], "emp_code": e["emp_code"], "name": e["name"],
                             "designation": e["designation"], "salary": e["salary"],
                             "present": m["counts"]["present"], "absent": m["counts"]["absent"],
                             "leave": m["counts"]["leave"], "halfday": m["counts"]["halfday"],
                             "late": m["counts"]["late"], "earned": m["earned"],
                             "advance": m["advance"], "net": m["net"], "paid": m["paid"]})
            d["rows"] = rows
            d["payroll"] = round(sum((x["salary"] or 0) for x in rows), 0)
            d["earned_tot"] = sum(x["earned"] for x in rows)
            d["advance_tot"] = round(sum(x["advance"] for x in rows), 0)
            d["net_tot"] = sum(x["net"] for x in rows)
        d["emp_count"] = len(d["rows"])

    return render_template("reports.html", active="reports", d=d)


def _ph_ensure():
    """PRICE HISTORY table ki guarantee — kahin bhi use karne se pehle (self-heal)."""
    try:
        db.execute("CREATE TABLE IF NOT EXISTS price_history ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT, model_name TEXT DEFAULT '', "
                   "model_code TEXT DEFAULT '', price REAL DEFAULT 0, rs_pcb REAL DEFAULT 0, "
                   "sheet_thickness TEXT DEFAULT '', order_id INTEGER, order_no TEXT DEFAULT '', "
                   "party TEXT DEFAULT '', ddate TEXT DEFAULT '', created_on TEXT DEFAULT '')")
        phc = [r["name"] for r in db.query("PRAGMA table_info(price_history)")]
        if "sheet_thickness" not in phc:
            db.execute("ALTER TABLE price_history ADD COLUMN sheet_thickness TEXT DEFAULT ''")
    except Exception:
        pass


def _record_price(model_name, model_code, price, rs_pcb, order_id, order_no, party, ddate="",
                  thickness=""):
    """PRICE HISTORY — item/model kis price pe bika/gaya, ye yaad rakho.
    Same order + same model + same price par duplicate nahi banega."""
    _ph_ensure()
    model_name = (model_name or "").strip()
    if not model_name or not price:
        return
    price = round(float(price), 2)
    rs = round(float(rs_pcb or 0), 3)
    dup = db.query("SELECT id FROM price_history WHERE model_name=? AND order_id=? AND price=? "
                   "ORDER BY id DESC LIMIT 1", (model_name, order_id or 0, price), one=True)
    if dup:
        return
    db.execute("INSERT INTO price_history (model_name, model_code, price, rs_pcb, sheet_thickness, order_id, "
               "order_no, party, ddate, created_on) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (model_name, model_code or "", price, rs, (thickness or "").strip(), order_id or 0,
                order_no or "", party or "", ddate or "",
                _now_ist().strftime("%Y-%m-%d %H:%M")))


def _model_thickness(name):
    """Finished product ki SHEET THICKNESS (e.g. 1.6MM) — price history ke saath record ke liye."""
    if not name:
        return ""
    m = db.query("SELECT sheet_thickness FROM product_models WHERE name=? LIMIT 1",
                 ((name or "").strip(),), one=True)
    return (m["sheet_thickness"] or "") if m else ""


def _norm_thick(s):
    """'metal-1mm' / 'FR4 1.0MM' -> 'METAL 1MM' / 'FR4 1 0MM' jaisa compare-able key."""
    import re
    return re.sub(r"\s+", " ", re.sub(r"[^0-9A-Z]", " ", (s or "").upper())).strip()


def _rate_for_thickness(thickness):
    """Rate card se material+thickness ka ₹/sq.inch — exact match, phir flexible match."""
    if not thickness:
        return None
    key = _norm_thick(thickness)
    rows = db.query("SELECT material, thickness, per_sq_inch FROM thickness_rates ORDER BY id")
    for r in rows:
        if key == _norm_thick(f"{r['material']} {r['thickness']}"):
            return float(r["per_sq_inch"] or 0)
    for r in rows:
        rk = _norm_thick(f"{r['material']} {r['thickness']}")
        if rk and (rk in key or key in rk):
            return float(r["per_sq_inch"] or 0)
    return None


def _model_name(r):
    """Dispatch row ka MODEL NAME — jobcard ke party_model · model, warna product name."""
    try:
        keys = r.keys()
    except AttributeError:
        return "—"
    pm = (r["party_model"] or "").strip() if "party_model" in keys else ""
    mo = (r["model"] or "").strip() if "model" in keys else ""
    name = " · ".join(x for x in (pm, mo) if x)
    if not name and "product" in keys:
        name = (r["product"] or "").strip()
    return name or "—"


@app.route("/reports/csv")
@login_required
def reports_csv():
    """Current report view ka CSV export (type + period params ke saath)."""
    args = request.args.to_dict()
    args.pop("token", None)
    target = url_for("reports", **args)
    # wahi data dobara compute (reports route se) — re-render se heavy hai, seedha query
    rtype = args.get("type") or "dispatch"
    period = args.get("period") or "monthly"
    today = _today_ist()
    sel_date = args.get("date") or today.isoformat()
    sel_month = args.get("month") or today.strftime("%Y-%m")
    sel_year = args.get("year") or str(today.year)
    buf = io.StringIO()
    w = csv.writer(buf)
    fname = f"report_{rtype}_{period}_{today.isoformat()}.csv"
    if rtype == "dispatch":
        pattern = sel_date if period == "daily" else (sel_month + "%" if period == "monthly" else sel_year + "%")
        rows = db.query("SELECT dl.*, o.order_no, o.party, o.value, o.qty, o.product, "
                        "jc.party_model, jc.model FROM dispatch_log dl "
                        "LEFT JOIN orders o ON o.id=dl.order_id "
                        "LEFT JOIN jobcard jc ON jc.order_id=o.id "
                        "WHERE dl.ddate LIKE ? ORDER BY dl.ddate, dl.id", (pattern,))
        w.writerow(["Date", "Order No", "Model", "Party", "Mode", "Details", "Dispatched By", "Qty", "Value", "Photo"])
        for r in rows:
            w.writerow([r["ddate"], r["order_no"] or "", _model_name(r), r["party"] or "", r["mode"],
                        r["details"] or "", r["dispatched_by"] or "", r["qty"] or 0, r["value"] or 0,
                        "Yes" if r["photo_data"] else "No"])
    elif rtype == "production":
        pattern = sel_date if period == "daily" else (sel_month + "%" if period == "monthly" else sel_year + "%")
        rows = db.query("SELECT * FROM orders WHERE created_on LIKE ? ORDER BY id", (pattern,))
        w.writerow(["Order No", "Party", "Product", "Qty", "Value", "Status", "Current Process", "Delivery Date", "Created"])
        for r in rows:
            w.writerow([r["order_no"], r["party"] or "", r["product"] or "", r["qty"] or 0, r["value"] or 0,
                        r["status"] or "", r["current_process"] or "", r["delivery_date"] or "", r["created_on"] or ""])
    else:  # salary
        if period == "yearly":
            args2 = {"type": "salary", "period": "yearly", "year": sel_year}
            # reports() jaisa hi data — chhota duplicate via flask test client nahi; seedha same loop
            ag = {}
            for r in db.query("SELECT emp_id, substr(date,1,7) AS m, status, COUNT(*) AS c FROM attendance "
                              "WHERE date LIKE ? GROUP BY emp_id, m, status", (sel_year + "%",)):
                key = (r["emp_id"], r["m"])
                ag.setdefault(key, {"present": 0, "absent": 0, "leave": 0, "halfday": 0, "late": 0})
                if r["status"] in ag[key]:
                    ag[key][r["status"]] = r["c"]
            adv = {r["emp_id"]: r["a"] for r in db.query(
                "SELECT emp_id, COALESCE(SUM(advance),0) a FROM emp_salary WHERE month LIKE ? GROUP BY emp_id", (sel_year + "%",))}
            cur_ym = today.strftime("%Y-%m")
            w.writerow(["Code", "Name", "Role", "Monthly Salary", "Present", "Absent", "Leave", "Half Day", "Late",
                        "Earned (year)", "Advance (year)", "Net Payable"])
            for e in db.query("SELECT * FROM employees WHERE status='active' ORDER BY id"):
                cnt = {"present": 0, "absent": 0, "leave": 0, "halfday": 0, "late": 0}
                earned = 0.0
                sal = float(e["salary"] or 0)
                for mnum in range(1, 13):
                    ym = f"{sel_year}-{mnum:02d}"
                    if sel_year == str(today.year) and ym > cur_ym:
                        continue
                    a = ag.get((e["id"], ym))
                    if not a:
                        earned += sal
                        continue
                    dim = calendar.monthrange(int(sel_year), mnum)[1]
                    earned += max(0.0, sal - (a["absent"] + 0.5 * a["halfday"]) * (sal / dim if dim else 0))
                    for k in cnt:
                        cnt[k] += a[k]
                w.writerow([e["emp_code"], e["name"], e["designation"] or "", sal, cnt["present"], cnt["absent"],
                            cnt["leave"], cnt["halfday"], cnt["late"], round(earned),
                            round(adv.get(e["id"], 0) or 0), round(earned - (adv.get(e["id"], 0) or 0))])
        else:
            w.writerow(["Code", "Name", "Role", "Monthly Salary", "Present", "Absent", "Leave", "Half Day", "Late",
                        "Earned", "Advance", "Net Payable", "Paid"])
            for e in db.query("SELECT * FROM employees WHERE status='active' ORDER BY id"):
                m = _emp_month(e["id"], sel_month)
                w.writerow([e["emp_code"], e["name"], e["designation"] or "", e["salary"] or 0,
                            m["counts"]["present"], m["counts"]["absent"], m["counts"]["leave"],
                            m["counts"]["halfday"], m["counts"]["late"], m["earned"], m["advance"], m["net"],
                            "Yes" if m["paid"] else "No"])
        fname = f"report_salary_{period}_{sel_month if period == 'monthly' else sel_year}_{today.isoformat()}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


# ---------------------------------------------------------------- users & access
@app.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
def users():
    _users_perms_ensure()
    if request.method == "POST":
        f = request.form
        if f.get("action") == "perms":
            try:
                uid = int(f.get("uid") or 0)
            except (TypeError, ValueError):
                uid = 0
            u = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
            if not u:
                flash("User not found.", "error")
            elif u["role"] == "admin":
                flash("Admin ko hamesha SAB ka access rehta hai \u2014 permissions sirf operator users par lagti hai.", "error")
            else:
                allowed = ",".join(f.getlist("perm"))
                db.execute("UPDATE users SET perms=? WHERE id=?", (allowed, uid))
                flash(f"{u['name']} ke permissions save ho gaye ({len(f.getlist('perm'))} modules).", "success")
            return redirect_with_token(url_for("users"))
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
    perms_row, perms_set = None, set()
    perms_id = request.args.get("perms")
    if perms_id:
        perms_row = db.query("SELECT * FROM users WHERE id=?", (int(perms_id),), one=True)
        if perms_row:
            _raw = (perms_row["perms"] if "perms" in perms_row.keys() else "") or ""
            perms_set = {k for k in _raw.split(",") if k}
            if not _raw and perms_row["role"] != "admin":
                perms_set = {k for k in _DEFAULT_PERMS.split(",") if k}
    return render_template("users.html", active="users", users=rows,
                           show_add=request.args.get("add"), edit_row=edit_row,
                           modules=_MODULES, perms_row=perms_row, perms_set=perms_set,
                           default_perms=_DEFAULT_PERMS)


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
