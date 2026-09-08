"""
Shivaya Circuit — Smart ERP (PCB Manufacturing)
Database schema + seed data.
Backend: local SQLite file, ya TURSO_URL env set hone par Turso (cloud) —
Vercel/serverless ke liye taaki mobile + PC par same data dikhe.
"""
import os
import sqlite3
import tempfile
import threading
import datetime
import json
import base64
import http.client
import urllib.parse

# Schema version — bump karo jab SCHEMA/migrate badle, taaki agla deploy tables update kare.
SCHEMA_VERSION = "2026-09-08.4"

DB_PATH = os.environ.get("DB_PATH") or (
    os.path.join(tempfile.gettempdir(), "circuit.db") if os.environ.get("VERCEL") else "circuit.db"
)
TURSO_URL = os.environ.get("TURSO_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    department TEXT DEFAULT '',
    designation TEXT DEFAULT '',
    joining_date TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    salary REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'present',
    UNIQUE(emp_id, date)
);

CREATE TABLE IF NOT EXISTS emp_salary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    advance REAL DEFAULT 0,
    paid INTEGER DEFAULT 0,
    UNIQUE(emp_id, month)
);

CREATE TABLE IF NOT EXISTS bom (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER,
    item_id INTEGER,
    qty_per REAL DEFAULT 0,
    unit_pcs INTEGER DEFAULT 1000
);

CREATE TABLE IF NOT EXISTS jobcard_bom (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    item_id INTEGER,
    required REAL DEFAULT 0,
    deducted REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dispatch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    ddate TEXT DEFAULT '',
    dtime TEXT DEFAULT '',
    mode TEXT DEFAULT '',
    details TEXT DEFAULT '',
    dispatched_by TEXT DEFAULT '',
    ts TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobcard_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    process TEXT DEFAULT '',
    action TEXT DEFAULT '',
    operator TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    details TEXT DEFAULT '',
    ts TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS order_assignments (
    order_id INTEGER UNIQUE NOT NULL,
    machine TEXT DEFAULT 'Unassigned',
    shift TEXT DEFAULT '',
    operator TEXT DEFAULT 'Unassigned',
    planned_start TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'idle',
    current_job TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL,
    party TEXT NOT NULL,
    board TEXT DEFAULT 'Single Side',
    product TEXT DEFAULT '',
    qty INTEGER DEFAULT 0,
    value REAL DEFAULT 0,
    current_process TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    priority TEXT DEFAULT 'normal',
    delivery_date TEXT DEFAULT '',
    operator TEXT DEFAULT '',
    started_qty INTEGER DEFAULT 0,
    finished_qty INTEGER DEFAULT 0,
    created_on TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS process_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    process TEXT NOT NULL,
    status TEXT DEFAULT 'done',
    operator TEXT DEFAULT '',
    updated_on TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT NOT NULL,
    vendor TEXT NOT NULL,
    item TEXT DEFAULT '',
    qty TEXT DEFAULT '',
    amount REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    date TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT DEFAULT '',
    stock REAL DEFAULT 0,
    min_stock REAL DEFAULT 0,
    unit TEXT DEFAULT 'pcs'
);

CREATE TABLE IF NOT EXISTS billing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no TEXT NOT NULL,
    party TEXT NOT NULL,
    amount REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    date TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_no TEXT NOT NULL,
    party TEXT NOT NULL,
    ptype TEXT DEFAULT 'receipt',
    amount REAL DEFAULT 0,
    mode TEXT DEFAULT 'Bank',
    date TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qc_no TEXT NOT NULL,
    order_id INTEGER,
    remarks TEXT DEFAULT '',
    result TEXT DEFAULT 'pending',
    date TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_no TEXT NOT NULL,
    order_id INTEGER,
    desc TEXT DEFAULT '',
    start_date TEXT DEFAULT '',
    end_date TEXT DEFAULT '',
    status TEXT DEFAULT 'planned'
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mp_no TEXT NOT NULL,
    item TEXT NOT NULL,
    required REAL DEFAULT 0,
    available REAL DEFAULT 0,
    shortfall REAL DEFAULT 0,
    action TEXT DEFAULT '',
    unit TEXT DEFAULT 'pcs'
);

CREATE TABLE IF NOT EXISTS cutlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cl_no TEXT NOT NULL,
    board TEXT NOT NULL,
    qty INTEGER DEFAULT 0,
    panels_per_sheet INTEGER DEFAULT 0,
    sheets INTEGER DEFAULT 0,
    status TEXT DEFAULT 'saved',
    created_on TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobcard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER UNIQUE NOT NULL,
    party_model TEXT DEFAULT '', model TEXT DEFAULT '', odate TEXT DEFAULT '',
    board_type TEXT DEFAULT '', created_by TEXT DEFAULT '', checked_by TEXT DEFAULT '',
    order_via TEXT DEFAULT '', exp_delivery TEXT DEFAULT '', price REAL DEFAULT 0,
    rs_pcb REAL DEFAULT 0, payment_status TEXT DEFAULT 'pending',
    sheet_material TEXT DEFAULT '', copper_finish TEXT DEFAULT '', masking TEXT DEFAULT '',
    finish TEXT DEFAULT '', legend_printing TEXT DEFAULT '', pcb_type TEXT DEFAULT '',
    actual_pcb_x REAL DEFAULT 0, actual_pcb_y REAL DEFAULT 0,
    x_size REAL DEFAULT 0, x_qty INTEGER DEFAULT 0, y_size REAL DEFAULT 0, y_qty INTEGER DEFAULT 0,
    cnc_margin_x REAL DEFAULT 0, cnc_margin_y REAL DEFAULT 0,
    panel_x REAL DEFAULT 0, panel_y REAL DEFAULT 0,
    panels_per_sheet INTEGER DEFAULT 0, sheets INTEGER DEFAULT 0,
    qty_panel INTEGER DEFAULT 0, pcs_panel INTEGER DEFAULT 0, v_grooving TEXT DEFAULT '',
    sheet_len REAL DEFAULT 0, sheet_w REAL DEFAULT 0,
    customer_req TEXT DEFAULT '', raw_materials TEXT DEFAULT '',
    total_qty INTEGER DEFAULT 0, short_qty INTEGER DEFAULT 0, short_reason TEXT DEFAULT '',
    handover_sign TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobcard_process (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    process TEXT NOT NULL,
    start_dt TEXT DEFAULT '', end_dt TEXT DEFAULT '',
    start_name TEXT DEFAULT '', end_name TEXT DEFAULT '',
    qty INTEGER DEFAULT 0, next_process TEXT DEFAULT '',
    ord INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    ptype TEXT DEFAULT 'customer',
    credit_days INTEGER DEFAULT 0,
    phone TEXT DEFAULT '',
    gst TEXT DEFAULT '',
    address TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS product_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    model_code TEXT DEFAULT '',
    pcb_len REAL DEFAULT 0, pcb_w REAL DEFAULT 0,
    pcbs_x INTEGER DEFAULT 1, pcbs_y INTEGER DEFAULT 1,
    gap_x REAL DEFAULT 0, gap_y REAL DEFAULT 0,
    border_l REAL DEFAULT 0, border_r REAL DEFAULT 0,
    border_t REAL DEFAULT 0, border_b REAL DEFAULT 0,
    gang_x INTEGER DEFAULT 1, gang_y INTEGER DEFAULT 1,
    sheet_len REAL DEFAULT 0, sheet_w REAL DEFAULT 0,
    panel_len REAL DEFAULT 0, panel_w REAL DEFAULT 0,
    kerf_x REAL DEFAULT 2, kerf_y REAL DEFAULT 2,
    orientation TEXT DEFAULT 'auto',
    pcs_panel INTEGER DEFAULT 0,
    panels_sheet INTEGER DEFAULT 0,
    sheets INTEGER DEFAULT 1,
    order_id INTEGER,
    created_on TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fg_production (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER,
    product_name TEXT DEFAULT '',
    qty INTEGER DEFAULT 0,
    made_by TEXT DEFAULT '',
    created_on TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fg_consumption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prod_id INTEGER,
    item_id INTEGER,
    item_name TEXT DEFAULT '',
    qty_used REAL DEFAULT 0,
    unit TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS material_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    item_id INTEGER,
    item_name TEXT DEFAULT '',
    qty REAL DEFAULT 0,
    unit TEXT DEFAULT '',
    worker TEXT DEFAULT '',
    taken_on TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
"""


class _Row(dict):
    """sqlite3.Row jaisa: row['col'] + row[0] + iteration — dono backends ke liye same."""

    def __init__(self, cols, vals):
        super().__init__(zip(cols, vals))
        self._vals = tuple(vals)

    def __getitem__(self, k):
        if isinstance(k, str):
            return dict.__getitem__(self, k)
        return self._vals[k]

    def __iter__(self):
        return iter(self._vals)


def _norm_rows(raw_rows, cols):
    out = []
    for r in raw_rows or []:
        if isinstance(r, _Row):
            out.append(r)
            continue
        try:
            vals = list(r)
        except Exception:
            vals = [r[c] for c in cols]
        out.append(_Row(cols, vals))
    return out


_turso_client = None
_turso_lock = threading.Lock()


class _TursoResult:
    """HTTP response se bana result — _Cur ko chahiye: cols, rows, rowcount, lastrowid."""

    def __init__(self, cols, rows, rowcount, lastrowid):
        self.cols = cols
        self.rows = rows
        self.rowcount = rowcount
        self.lastrowid = lastrowid


class _TursoHTTPError(ValueError):
    """Turso server ne jo error bheja (4xx/5xx) — ye retry nahi hota."""


class _TursoHTTP:
    """Turso (libSQL cloud) ke liye chhota HTTP client — sirf stdlib.

    Turso ne WebSocket endpoint band kar diya hai (purana libsql-client isi liye
    fail karta tha: 400 'protocol upgrade not supported (websocket)').
    Ab official rasta HTTP hai: POST https://<db-host>/v1/execute (Hrana over HTTP).

    SPEED ke liye 2 zaroori cheezein:
    1. KEEP-ALIVE: ek hi HTTPS connection har query ke liye reuse hota hai —
       nahi to har query par naya TLS handshake (3-4 round trips) lagta hai.
    2. BATCH: executescript ki saari statements ek hi HTTP call mein jaati hain.
    """

    def __init__(self, url, token):
        base = (url or "").strip()
        if base.lower().startswith("libsql://"):
            base = "https://" + base[len("libsql://"):]
        base = base.rstrip("/")
        parsed = urllib.parse.urlparse(base)
        self._hostname = parsed.hostname or ""
        self._port = parsed.port or (80 if parsed.scheme == "http" else 443)
        self._prefix = parsed.path or ""
        self._plain = parsed.scheme == "http"
        self._headers = {
            "Authorization": "Bearer " + (token or ""),
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }
        self._conn = None
        self._lock = threading.Lock()

    def _request(self, path, payload):
        """POST kar ke JSON response lao. Connection toota ho to ek baar
        naya bana ke retry. Server ka 4xx/5xx error retry nahi hota."""
        body = json.dumps(payload).encode("utf-8")
        last_err = None
        with self._lock:
            for _attempt in range(2):
                try:
                    if self._conn is None:
                        cls = http.client.HTTPConnection if self._plain else http.client.HTTPSConnection
                        self._conn = cls(self._hostname, self._port, timeout=30)
                    self._conn.request("POST", self._prefix + path,
                                       body=body, headers=self._headers)
                    resp = self._conn.getresponse()
                    data = resp.read()
                    if resp.status >= 400:
                        detail = data.decode("utf-8", "replace")
                        try:
                            j = json.loads(detail)
                            detail = j.get("error") or j.get("message") or detail
                        except Exception:
                            pass
                        raise _TursoHTTPError("Turso: %s (HTTP %s)" % (detail or "error", resp.status))
                    return json.loads(data.decode("utf-8"))
                except _TursoHTTPError:
                    raise
                except Exception as e:  # connection toota (timeout/reset/EOF)
                    last_err = e
                    try:
                        if self._conn:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
        raise ValueError("Turso connection error: %s" % last_err)

    @staticmethod
    def _arg(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool):
            return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        if isinstance(v, str):
            return {"type": "text", "value": v}
        if isinstance(v, (bytes, bytearray)):
            return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
        return {"type": "text", "value": str(v)}

    @staticmethod
    def _val(v):
        t = v.get("type")
        if t == "null":
            return None
        if t == "integer":
            return int(v.get("value", "0"))
        if t == "float":
            return float(v.get("value", 0.0))
        if t == "text":
            return v.get("value", "")
        if t == "blob":
            return base64.b64decode(v.get("base64", "") or "")
        return None

    def batch_fetch(self, stmts):
        """Kai queries (SELECT wagerah) ek hi HTTP call mein — results ke saath.
        Page render ke 10-15 queries -> 1 round trip = 10-15x fast."""
        steps = [{"stmt": {"sql": "BEGIN", "args": [], "named_args": [], "want_rows": False}}]
        for s in stmts:
            sql, params = (s, []) if isinstance(s, str) else (s[0], list(s[1]) or [])
            steps.append({
                "condition": {"type": "ok", "step": len(steps) - 1},
                "stmt": {"sql": sql, "args": [self._arg(p) for p in params],
                         "named_args": [], "want_rows": True},
            })
        steps.append({
            "condition": {"type": "ok", "step": len(steps) - 1},
            "stmt": {"sql": "COMMIT", "args": [], "named_args": [], "want_rows": False},
        })
        data = self._request("/v1/batch", {"batch": {"steps": steps}})
        result = data.get("result") or {}
        step_errors = result.get("step_errors") or []
        for i in range(len(stmts)):
            idx = i + 1
            if idx < len(step_errors) and step_errors[idx]:
                err = step_errors[idx]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise _TursoHTTPError("Turso batch step %d: %s" % (i, msg))
        step_results = result.get("step_results") or []
        out = []
        for i in range(len(stmts)):
            idx = i + 1
            res = step_results[idx] if idx < len(step_results) else None
            if res is None:
                raise _TursoHTTPError("Turso batch step %d: no result" % i)
            lid = res.get("last_insert_rowid")
            out.append(_TursoResult(
                cols=tuple((c.get("name") or "") for c in (res.get("cols") or [])),
                rows=[tuple(self._val(cell) for cell in r) for r in (res.get("rows") or [])],
                rowcount=int(res.get("affected_row_count") or 0),
                lastrowid=(int(lid) if lid is not None else None),
            ))
        return out

    def execute(self, sql, params):
        data = self._request("/v1/execute", {
            "stmt": {
                "sql": sql,
                "args": [self._arg(p) for p in (list(params) if params else [])],
                "named_args": [],
                "want_rows": True,
            }
        })
        result = data.get("result") or {}
        lid = result.get("last_insert_rowid")
        return _TursoResult(
            cols=tuple((c.get("name") or "") for c in (result.get("cols") or [])),
            rows=[tuple(self._val(cell) for cell in r) for r in (result.get("rows") or [])],
            rowcount=int(result.get("affected_row_count") or 0),
            lastrowid=(int(lid) if lid is not None else None),
        )

    def batch(self, stmts):
        """Kai statements ek hi HTTP call mein — init/seed 30x fast.
        stmts: plain sql strings YA (sql, params) tuples."""
        steps = [{"stmt": {"sql": "BEGIN", "args": [], "named_args": [], "want_rows": False}}]
        for s in stmts:
            sql, params = (s, []) if isinstance(s, str) else (s[0], list(s[1]) or [])
            steps.append({
                "condition": {"type": "ok", "step": len(steps) - 1},
                "stmt": {"sql": sql, "args": [self._arg(p) for p in params],
                         "named_args": [], "want_rows": False},
            })
        steps.append({
            "condition": {"type": "ok", "step": len(steps) - 1},
            "stmt": {"sql": "COMMIT", "args": [], "named_args": [], "want_rows": False},
        })
        data = self._request("/v1/batch", {"batch": {"steps": steps}})
        result = data.get("result") or {}
        step_errors = result.get("step_errors") or []
        for i in range(len(stmts)):
            idx = i + 1
            if idx < len(step_errors) and step_errors[idx]:
                err = step_errors[idx]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise _TursoHTTPError("Turso batch step %d: %s" % (i, msg))
        step_results = result.get("step_results") or []
        for i in range(len(stmts)):
            idx = i + 1
            if idx < len(step_results) and step_results[idx] is None:
                raise _TursoHTTPError("Turso batch step %d: no result" % i)

def _turso():
    global _turso_client
    with _turso_lock:
        if _turso_client is None:
            if not TURSO_URL.lower().startswith(("libsql://", "https://", "http://")):
                raise ValueError(
                    "TURSO_URL sahi format mein nahi hai: %r — "
                    "'libsql://<database>-<username>.turso.io' jaisa hona chahiye "
                    "(Turso dashboard se copy karo)" % TURSO_URL)
            _turso_client = _TursoHTTP(TURSO_URL, TURSO_AUTH_TOKEN)
    return _turso_client


class _Cur:
    """Cursor shim — sqlite cursor ya turso result, same interface."""

    def __init__(self, raw, turso):
        self._raw = raw
        self._turso = turso
        self._idx = 0
        self._rows = None

    def _all(self):
        if self._rows is None:
            if self._turso:
                self._rows = _norm_rows(self._raw.rows, self._raw.cols)
            else:
                cols = tuple(d[0] for d in (self._raw.description or ()))
                self._rows = _norm_rows(self._raw.fetchall(), cols)
        return self._rows

    def fetchone(self):
        rows = self._all()
        if self._idx >= len(rows):
            return None
        r = rows[self._idx]
        self._idx += 1
        return r

    def fetchall(self):
        rows = self._all()
        r = rows[self._idx:]
        self._idx = len(rows)
        return r

    def __iter__(self):
        return iter(self._all())

    @property
    def lastrowid(self):
        if self._turso:
            lid = getattr(self._raw, "lastrowid", None)
            if lid is None:
                try:
                    res = _turso().execute("SELECT last_insert_rowid() AS lid", [])
                    rows = _norm_rows(res.rows, res.cols)
                    lid = rows[0]["lid"] if rows else 0
                except Exception:
                    lid = 0
            return lid or 0
        return self._raw.lastrowid

    @property
    def rowcount(self):
        if self._turso:
            return getattr(self._raw, "rowcount", None) or 0
        return self._raw.rowcount or 0


class _Conn:
    """Connection shim — sqlite file ya turso client, same interface."""

    def __init__(self):
        self._turso = bool(TURSO_URL)
        if not self._turso:
            self._sql = sqlite3.connect(DB_PATH)
            self._sql.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        if self._turso:
            return _Cur(_turso().execute(sql, list(params) if params else []), True)
        return _Cur(self._sql.execute(sql, params), False)

    def executescript(self, script):
        if self._turso:
            stmts = [s.strip() for s in script.split(";") if s.strip()]
            if not stmts:
                return
            try:
                _turso().batch(stmts)   # ek hi HTTP call — 30x fast
            except Exception:
                for stmt in stmts:      # fallback: ek ek karke
                    _turso().execute(stmt, [])
            return
        self._sql.executescript(script)

    def executemany(self, sql, seq):
        if self._turso:
            try:
                _turso().batch([(sql, list(params)) for params in seq])
            except Exception:
                for params in seq:
                    _turso().execute(sql, list(params))
            return
        self._sql.executemany(sql, seq)

    def batch(self, stmts):
        """Ek saath bhejo: turso par 1 HTTP call (fast), sqlite par ek ek karke."""
        if self._turso:
            try:
                _turso().batch(stmts)
            except Exception:
                for s in stmts:
                    if isinstance(s, str):
                        _turso().execute(s, [])
                    else:
                        _turso().execute(s[0], list(s[1]) or [])
            return
        for s in stmts:
            if isinstance(s, str):
                self._sql.execute(s)
            else:
                self._sql.execute(s[0], s[1])

    def commit(self):
        if not self._turso:
            self._sql.commit()

    def close(self):
        if not self._turso:
            self._sql.close()


def _claim_seed(conn, key):
    """Serverless-safe seed gate: seed sirf pehli request karti hai (race-proof)."""
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?,?)", (key, "pending"))
    cur = conn.execute("UPDATE meta SET value='done' WHERE key=? AND value='pending'", (key,))
    return (cur.rowcount or 0) > 0


def get_db():
    return _Conn()


def migrate(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
    if "cutlist_info" not in cols:
        conn.execute("ALTER TABLE orders ADD COLUMN cutlist_info TEXT DEFAULT ''")
    pcols = [r[1] for r in conn.execute("PRAGMA table_info(product_models)")]
    if pcols and "model_code" not in pcols:
        conn.execute("ALTER TABLE product_models ADD COLUMN model_code TEXT DEFAULT ''")
    if pcols and "gap_x" not in pcols:
        conn.execute("ALTER TABLE product_models ADD COLUMN gap_x REAL DEFAULT 0")
    if pcols and "gap_y" not in pcols:
        conn.execute("ALTER TABLE product_models ADD COLUMN gap_y REAL DEFAULT 0")
    if pcols and "fg_stock" not in pcols:
        conn.execute("ALTER TABLE product_models ADD COLUMN fg_stock INTEGER DEFAULT 0")
    jcols = [r[1] for r in conn.execute("PRAGMA table_info(jobcard)")]
    if jcols and "mat_code" not in jcols:
        conn.execute("ALTER TABLE jobcard ADD COLUMN mat_code TEXT DEFAULT ''")
    if jcols and "mat_sheets" not in jcols:
        conn.execute("ALTER TABLE jobcard ADD COLUMN mat_sheets REAL DEFAULT 0")
    # employees: salary column (attendance se salary banane ke liye)
    ecols = [r[1] for r in conn.execute("PRAGMA table_info(employees)")]
    if ecols and "salary" not in ecols:
        conn.execute("ALTER TABLE employees ADD COLUMN salary REAL DEFAULT 0")
    conn.execute("UPDATE employees SET salary=60000 WHERE emp_code='EMP001' AND salary=0")
    conn.execute("UPDATE employees SET salary=18000 WHERE emp_code='EMP002' AND salary=0")
    conn.execute("UPDATE employees SET salary=17000 WHERE emp_code='EMP003' AND salary=0")
    conn.execute("UPDATE employees SET salary=16000 WHERE emp_code='EMP004' AND salary=0")
    conn.execute("UPDATE employees SET salary=15000 WHERE emp_code='EMP005' AND salary=0")
    conn.execute("UPDATE employees SET salary=12000 WHERE emp_code='EMP006' AND salary=0")
    # jobcard_process: ord column + UNIQUE hatao (NEXT se nayi process rows add karne
    # ke liye — same process dobara bhi aa sakta hai, jaise rework steps)
    jpcols = [r[1] for r in conn.execute("PRAGMA table_info(jobcard_process)")]
    if jpcols and "ord" not in jpcols:
        try:
            conn.batch([
                ("CREATE TABLE jobcard_process_new ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "order_id INTEGER NOT NULL, process TEXT NOT NULL, "
                 "start_dt TEXT DEFAULT '', end_dt TEXT DEFAULT '', "
                 "start_name TEXT DEFAULT '', end_name TEXT DEFAULT '', "
                 "qty INTEGER DEFAULT 0, next_process TEXT DEFAULT '', "
                 "ord INTEGER DEFAULT 0)", ()),
                ("INSERT INTO jobcard_process_new "
                 "(id, order_id, process, start_dt, end_dt, start_name, end_name, qty, next_process, ord) "
                 "SELECT id, order_id, process, start_dt, end_dt, start_name, end_name, qty, next_process, id*10 "
                 "FROM jobcard_process", ()),
                ("DROP TABLE jobcard_process", ()),
                ("ALTER TABLE jobcard_process_new RENAME TO jobcard_process", ()),
            ])
        except Exception:
            try:
                conn.execute("ALTER TABLE jobcard_process ADD COLUMN ord INTEGER DEFAULT 0")
            except Exception:
                pass
    jpcols = [r[1] for r in conn.execute("PRAGMA table_info(jobcard_process)")]
    if jpcols and "ord" in jpcols:
        conn.execute("UPDATE jobcard_process SET ord = id * 10 WHERE ord IS NULL OR ord = 0")
    # pre-filled standard process rows hatao (EK HI BAAR, meta flag se):
    # ab job card ka process table khaali rehta hai — sirf NEXT dropdown se
    # select karke hi process lines banti hain (user requirement)
    if conn.execute("SELECT value FROM meta WHERE key='prefill_cleanup'").fetchone() is None:
        conn.execute("DELETE FROM jobcard_process")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('prefill_cleanup','1')")
    # backfill parties from existing names (only when parties table is empty)
    pcnt = conn.execute("SELECT COUNT(*) c FROM parties").fetchone()["c"]
    if pcnt == 0:
        seen = {}
        for r in conn.execute("SELECT DISTINCT party FROM billing WHERE party!=''"):
            seen.setdefault(r["party"], "customer")
        for r in conn.execute("SELECT DISTINCT party FROM orders WHERE party!=''"):
            seen.setdefault(r["party"], "customer")
        for r in conn.execute("SELECT DISTINCT vendor FROM purchase_orders WHERE vendor!=''"):
            if r["vendor"] not in seen:
                seen[r["vendor"]] = "supplier"
        for name, ptype in seen.items():
            try:
                conn.execute("INSERT OR IGNORE INTO parties (name, ptype) VALUES (?,?)", (name, ptype))
            except Exception:
                pass
    conn.commit()


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)
    seed_if_empty(conn)
    seed_models(conn)
    seed_jobcards(conn)
    seed_parties(conn)
    seed_bom(conn)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                 (SCHEMA_VERSION,))
    conn.commit()
    conn.close()
    global _inited
    _inited = True


_inited = False
_inited_lock = threading.Lock()


def ensure_db():
    """init_db ek hi baar chalana hai — import par ya pehli request par (serverless safe)."""
    global _inited
    if _inited:
        return
    with _inited_lock:
        if not _inited:
            if TURSO_URL:
                # FAST PATH (cold start): cloud DB ka schema pehle se current hai
                # to sirf 1 query — poori init (80+ HTTP calls) skip.
                try:
                    c = get_db()
                    r = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                    c.close()
                    if r and r["value"] == SCHEMA_VERSION:
                        _inited = True
                        return
                except Exception:
                    pass  # meta nahi hai (naya DB) ya connection issue — full init
            init_db()


def seed_bom(conn):
    """Har finished product ka BOM: kitna raw material use hoga (per N PCBs).
    User example: 10000 PCB -> 100 sheet laminate, 5 kg masking, 2 Ltr lacquer, 10 drill bits."""
    if conn.execute("SELECT COUNT(*) c FROM bom").fetchone()["c"] > 0:
        return
    if not _claim_seed(conn, "seed_bom"):
        return

    mid_map = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM product_models")}
    iid_map = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM inventory")}

    # (finished product, raw material, qty_per, per kitne PCBs)
    rows = [
        ("RM 603 8W RD (L-936E)", "Aluminum MCPCB-1MM", 10, 1000),
        ("RM 603 8W RD (L-936E)", "Solder Mask Green Ink", 0.5, 1000),
        ("RM 603 8W RD (L-936E)", "Silkscreen White Ink", 0.3, 1000),
        ("RM 603 8W RD (L-936E)", "Lacquer", 0.2, 1000),
        ("RM 603 8W RD (L-936E)", "Drill Bits 0.8mm", 1, 1000),
        ("227-100W", "FR4 1.6MM", 10, 1000),
        ("227-100W", "Solder Mask Green Ink", 0.4, 1000),
        ("227-100W", "Silkscreen White Ink", 0.25, 1000),
        ("227-100W", "Lacquer", 0.15, 1000),
        ("227-100W", "Drill Bits 0.8mm", 1, 1000),
        ("LED Board 8W", "Aluminum MCPCB-1MM", 10, 1000),
        ("LED Board 8W", "Solder Mask Green Ink", 0.5, 1000),
        ("LED Board 8W", "Silkscreen White Ink", 0.3, 1000),
        ("LED Board 8W", "Lacquer", 0.2, 1000),
        ("LED Board 8W", "Drill Bits 0.8mm", 1, 1000),
        ("ADSF - 100 pcs", "FR4 Sheets 1.6mm", 10, 1000),
        ("ADSF - 100 pcs", "Solder Mask Green Ink", 0.4, 1000),
        ("ADSF - 100 pcs", "Silkscreen White Ink", 0.25, 1000),
        ("ADSF - 100 pcs", "Drill Bits 0.8mm", 1, 1000),
        ("ADSF - 100 pcs", "Lacquer", 0.15, 1000),
    ]
    ins = []
    for mname, iname, qp, up in rows:
        m, i = mid_map.get(mname), iid_map.get(iname)
        if m and i:
            ins.append(("INSERT OR IGNORE INTO bom (model_id, item_id, qty_per, unit_pcs) VALUES (?,?,?,?)",
                        (m, i, qp, up)))
    if ins:
        conn.batch(ins)   # 20 inserts -> 1 HTTP call
    conn.commit()


def seed_parties(conn):
    if not _claim_seed(conn, "seed_parties"):
        return
    was_empty = conn.execute("SELECT COUNT(*) c FROM parties").fetchone()["c"] == 0
    fixed = [
        ("Shiva Enterprises", "customer", 30, "9810011101", "07ABCDE1234F1Z5", "Plot 12, Udyog Vihar, Delhi"),
        ("Anand Electronics", "customer", 15, "9810011102", "07ABCDE1234F1Z6", "Sector 62, Noida"),
        ("Om Sai Controls", "customer", 45, "9810011103", "07ABCDE1234F1Z7", "Okhla Phase 2, Delhi"),
        ("arosan", "customer", 0, "", "", ""),
        ("AAS TECHNOLOGY (AADESH BHAIYYA)", "customer", 0, "", "", ""),
        ("LP GOLD", "customer", 0, "", "", ""),
        ("ALLIED", "customer", 45, "", "", ""),
        ("SHREE PCB WORKS", "customer", 0, "", "", ""),
        ("VERTEX ELECTRONICS", "customer", 0, "", "", ""),
        ("SunTech Laminates", "supplier", 0, "", "", ""),
        ("ChemCorp Industries", "supplier", 0, "", "", ""),
        ("DrillTech Tools", "supplier", 0, "", "", ""),
    ]
    conn.batch([
        ("INSERT OR IGNORE INTO parties (name, ptype, credit_days, phone, gst, address) VALUES (?,?,?,?,?,?)",
         (name, ptype, cd, ph, gst, addr))
        for name, ptype, cd, ph, gst, addr in fixed])
    if was_empty:
        # extra ledger demo entries (matches reference party ledger)
        conn.execute("INSERT OR IGNORE INTO billing (invoice_no, party, amount, status, date) VALUES (?,?,?,?,?)",
                     ("INV-100", "Shiva Enterprises", 154060.8, "pending", "2026-06-25"))
        conn.execute("INSERT OR IGNORE INTO billing (invoice_no, party, amount, status, date) VALUES (?,?,?,?,?)",
                     ("INV-099", "Anand Electronics", 42000, "pending", "2026-07-30"))
        conn.execute("INSERT OR IGNORE INTO billing (invoice_no, party, amount, status, date) VALUES (?,?,?,?,?)",
                     ("INV-098", "Om Sai Controls", 78000, "pending", "2026-08-05"))
        conn.execute("INSERT OR IGNORE INTO billing (invoice_no, party, amount, status, date) VALUES (?,?,?,?,?)",
                     ("INV-097", "arosan", 23500, "pending", "2026-08-31"))
    conn.commit()


JC_PROCESSES = ["Laminate Cutting", "CNC Drilling", "Circuit Printing", "Etching", "Center",
                "Solder Mask", "Top Printing", "Back Printing", "HAL", "TOOL", "CNC Routing",
                "V-Cut", "OSP", "FQC", "Lacker", "Packing"]

# Metal / MCPCB boards: total 12 processes; #7 is a dropdown (TOOL / CNC DRILLING / CNC DRILLING+ROUTING)
JC_TOOL_SLOT = "TOOL/CNC DRILLING/CNC DRILLING+ROUTING"
JC_TOOL_OPTIONS = ["TOOL", "CNC DRILLING", "CNC DRILLING+ROUTING"]
JC_PROCESSES_MCPCB = ["Laminate Cutting", "Circuit Printing", "Etching", "CENTER (CCD)", "Solder Mask",
                      "Legend Printing (Optional)", JC_TOOL_SLOT, "V-Cut", "Lacquer", "FQC",
                      "Packing", "Dispatch"]


def process_list_for(material=""):
    """MCPCB/metal boards -> 12-step list; otherwise FR4 16-step list."""
    m = (material or "").upper()
    if any(k in m for k in ("MCPCB", "METAL", "ALUMINUM", "ALUMINIUM")):
        return JC_PROCESSES_MCPCB, "MCPCB (Metal) · 12 processes"
    return JC_PROCESSES, "FR4 · 16 processes"


def seed_jobcards(conn):
    cur = conn.execute("SELECT COUNT(*) AS c FROM jobcard")
    if cur.fetchone()["c"] > 0:
        return
    if not _claim_seed(conn, "seed_jobcards"):
        return

    def rate_for(total, qty, panel_x, panel_y, pcs):
        """rate/sq.inch so that PRICE/PC = rate * panel_sq_inch / pcs (2dp exact)"""
        per_pc = round(total / qty, 2)
        sq_in = (panel_x / 25.4) * (panel_y / 25.4)
        return round(per_pc * pcs / sq_in, 4), per_pc

    cols = ("order_id, party_model, model, odate, board_type, created_by, checked_by, order_via, "
            "exp_delivery, price, rs_pcb, payment_status, sheet_material, copper_finish, masking, finish, "
            "legend_printing, pcb_type, actual_pcb_x, actual_pcb_y, x_size, x_qty, y_size, y_qty, "
            "cnc_margin_x, cnc_margin_y, panel_x, panel_y, panels_per_sheet, sheets, qty_panel, pcs_panel, "
            "v_grooving, sheet_len, sheet_w, customer_req, raw_materials, total_qty, short_qty, short_reason, handover_sign")

    jc_rows = []

    def jc(oid, **kw):
        sql = f"INSERT OR IGNORE INTO jobcard ({cols}) VALUES ({','.join('?' * 41)})"
        jc_rows.append((sql,
                        (oid, kw.get("party_model", ""), kw.get("model", ""), kw.get("odate", ""),
                         kw.get("board_type", ""), kw.get("created_by", ""), kw.get("checked_by", ""),
                         kw.get("order_via", ""), kw.get("exp_delivery", ""), kw.get("price", 0),
                         kw.get("rs_pcb", 0), kw.get("payment_status", "pending"), kw.get("sheet_material", ""),
                         kw.get("copper_finish", ""), kw.get("masking", ""), kw.get("finish", ""),
                         kw.get("legend_printing", ""), kw.get("pcb_type", ""), kw.get("actual_pcb_x", 0),
                         kw.get("actual_pcb_y", 0), kw.get("x_size", 0), kw.get("x_qty", 0),
                         kw.get("y_size", 0), kw.get("y_qty", 0), kw.get("cnc_margin_x", 0),
                         kw.get("cnc_margin_y", 0), kw.get("panel_x", 0), kw.get("panel_y", 0),
                         kw.get("panels_per_sheet", 0), kw.get("sheets", 0), kw.get("qty_panel", 0),
                         kw.get("pcs_panel", 0), kw.get("v_grooving", ""), kw.get("sheet_len", 0),
                         kw.get("sheet_w", 0), kw.get("customer_req", ""),
                         kw.get("raw_materials", ""), kw.get("total_qty", 0), kw.get("short_qty", 0),
                         kw.get("short_reason", ""), kw.get("handover_sign", ""))))

    # Order #4 - rich job card matching screenshot
    jc(4, party_model="RM 603 8W RD (L-936E)", model="SCPL-424", odate="2026-08-07",
       board_type="Single Side", created_by="Shivam", checked_by="", order_via="WhatsApp",
       exp_delivery="2026-08-28", price=rate_for(154060.8, 15092, 384, 380, 49)[1], rs_pcb=rate_for(154060.8, 15092, 384, 380, 49)[0], payment_status="Pending",
       sheet_material="Aluminum MCPCB-1MM", copper_finish="", masking="", finish="", legend_printing="",
       pcb_type="CNC", actual_pcb_x=54, actual_pcb_y=54, x_size=378, x_qty=3, y_size=378, y_qty=2,
       cnc_margin_x=3, cnc_margin_y=1, panel_x=384, panel_y=380, panels_per_sheet=6, sheets=52,
       qty_panel=308, pcs_panel=49, v_grooving="", sheet_len=1200, sheet_w=1000,
       customer_req="", raw_materials="", total_qty=15092, short_qty=0, short_reason="", handover_sign="")

    # Order #3
    jc(3, party_model="227-100W", model="SCPL-190", odate="2026-08-10", board_type="Single Side",
       created_by="Shivam", checked_by="", order_via="WhatsApp", exp_delivery="2026-08-20",
       price=rate_for(120000, 9984, 404, 264, 50)[1], rs_pcb=rate_for(120000, 9984, 404, 264, 50)[0], payment_status="Pending", sheet_material="FR4 1.6MM",
       pcb_type="CNC", actual_pcb_x=40, actual_pcb_y=50, x_size=400, x_qty=3, y_size=260, y_qty=2,
       cnc_margin_x=2, cnc_margin_y=2, panel_x=404, panel_y=264, panels_per_sheet=6, sheets=40,
       qty_panel=240, pcs_panel=50, sheet_len=1200, sheet_w=1000, total_qty=9984)

    # Order #1 (completed)
    jc(1, party_model="LED Board 8W", model="SCPL-101", odate="2026-08-05", board_type="Single Side",
       created_by="Shivam", checked_by="Amit Patel", order_via="Email", exp_delivery="2026-08-15",
       price=rate_for(245000, 15000, 384, 380, 49)[1], rs_pcb=rate_for(245000, 15000, 384, 380, 49)[0], payment_status="Paid", sheet_material="Aluminum MCPCB-1MM",
       pcb_type="CNC", actual_pcb_x=54, actual_pcb_y=54, x_size=378, x_qty=3, y_size=378, y_qty=2,
       cnc_margin_x=3, cnc_margin_y=1, panel_x=384, panel_y=380, panels_per_sheet=6, sheets=51,
       qty_panel=306, pcs_panel=49, sheet_len=1200, sheet_w=1000, total_qty=15000)

    # Order #2 (completed)
    jc(2, party_model="RM 603 8W RD", model="SCPL-402", odate="2026-08-06", board_type="Single Side",
       created_by="Shivam", checked_by="Amit Patel", order_via="WhatsApp", exp_delivery="2026-08-15",
       price=rate_for(187500, 15092, 384, 380, 49)[1], rs_pcb=rate_for(187500, 15092, 384, 380, 49)[0], payment_status="Paid", sheet_material="Aluminum MCPCB-1MM",
       pcb_type="CNC", actual_pcb_x=54, actual_pcb_y=54, x_size=378, x_qty=3, y_size=378, y_qty=2,
       cnc_margin_x=3, cnc_margin_y=1, panel_x=384, panel_y=380, panels_per_sheet=6, sheets=52,
       qty_panel=308, pcs_panel=49, sheet_len=1200, sheet_w=1000, total_qty=15092)

    conn.batch(jc_rows)   # saare jobcards 1 HTTP call mein

    # NOTE: process rows seed NAHI hote — job card ka process table khaali start
    # hota hai; user NEXT dropdown se select karke process lines banata hai.
    conn.commit()


def bom_rows_for(model_id):
    """BOM lines with inventory item info."""
    return query(
        "SELECT b.*, i.name AS item_name, i.category, i.stock, i.unit FROM bom b "
        "JOIN inventory i ON i.id=b.item_id WHERE b.model_id=? "
        "ORDER BY i.category, i.name", (model_id,))


def all_process_options():
    """NEXT dropdown ke liye saare processes (dono lists + tool options) — unique, order ke saath.

    NOTE: 'Dispatch' ko jaan-boojh kar HATA diya gaya hai — dispatch koi process
    line nahi hai. Wo ALAG rakha gaya hai: Packing finish hone ke baad operator ko
    🚚 Dispatch button dikhta hai, aur Job Card mein alag DISPATCH panel hai."""
    seen, out = [], []
    for lst in (JC_PROCESSES, JC_PROCESSES_MCPCB):
        for p in lst:
            if p == JC_TOOL_SLOT:
                continue
            if p == "Dispatch":
                continue  # dispatch process chain ka hissa nahi — alag action hai
            if p not in seen:
                seen.append(p)
                out.append(p)
    for o in JC_TOOL_OPTIONS:
        if o not in seen:
            seen.append(o)
            out.append(o)
    return out


def get_jc_processes(order_id):
    """Job card ki process rows — ORDER BY ord, id.

    Pehla process HAMESHA 'Laminate Cutting' (fixed) hota hai — table khaali ho
    ya ye row missing ho to auto-create hoti hai. Baaki process lines sirf
    NEXT dropdown se select karke banti hain (chain neeche tak chalti hai).
    Har row par: idx (1..N), is_dropdown (sirf tool-slot wali row), options,
    extra (non-fixed rows — editable + delete button), fixed (pehli row),
    next_process."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM jobcard_process WHERE order_id=?", (order_id,)).fetchall()
    rows = list(rows)
    if not any((r["process"] or "") == "Laminate Cutting" for r in rows):
        ordv = (min([(r["ord"] or 0) for r in rows] + [0]) - 10) if rows else 10
        conn.execute("INSERT INTO jobcard_process (order_id, process, next_process, ord) VALUES (?,?,?,?)",
                     (order_id, "Laminate Cutting", "", ordv))
        conn.commit()
        rows = conn.execute("SELECT * FROM jobcard_process WHERE order_id=?", (order_id,)).fetchall()
    rows = sorted(rows, key=lambda r: ((r["ord"] or 0), r["id"]))
    # tool-slot matching: TOOL/CNC DRILLING/CNC DRILLING+ROUTING -> dropdown row
    std_specs = [tuple(JC_TOOL_OPTIONS) if p == JC_TOOL_SLOT else (p,) for p in JC_PROCESSES]
    consumed = [False] * len(std_specs)
    out = []
    for idx, r in enumerate(rows):
        d = dict(r)
        d["idx"] = idx + 1
        matched = -1
        for si, spec in enumerate(std_specs):
            if not consumed[si] and r["process"] in spec:
                consumed[si] = True
                matched = si
                break
        is_std = matched >= 0
        d["is_dropdown"] = is_std and len(std_specs[matched]) > 1
        d["options"] = JC_TOOL_OPTIONS if d["is_dropdown"] else []
        # sirf PEHLI row (Laminate Cutting) fixed/static hai; baaki sab rows
        # editable + deletable hain (NEXT se bani lines ko user manage kar sake)
        d["extra"] = not (is_std and d["idx"] == 1)
        d["fixed"] = (d["idx"] == 1 and d["process"] == "Laminate Cutting")
        out.append(d)
    for i, d in enumerate(out):
        if not (d["next_process"] or "").strip() and i + 1 < len(out):
            d["next_process"] = out[i + 1]["process"]
    conn.close()
    return out


def seed_models(conn):
    if not _claim_seed(conn, "seed_models"):
        return
    today = datetime.date.today().isoformat()
    baseline = [
        # name, code, pcb_x, pcb_y, pcbs_x, pcbs_y, bl, br, bt, bb, gang_x, gang_y, sl, sw, pl, pw, kx, ky, orient, pcs, pps, sheets
        ("RM 603 8W RD (L-936E)", "SCPL-424", 54, 54, 7, 7, 3, 3, 1, 1, 1, 1, 1200, 1000, 384, 380, 2, 2, "normal", 49, 6, 52),
        ("227-100W", "SCPL-190", 40, 50, 10, 5, 2, 2, 2, 2, 1, 1, 1200, 1000, 404, 264, 2, 2, "rotated", 50, 6, 40),
        ("LED Board 8W", "SCPL-101", 54, 54, 7, 7, 3, 3, 1, 1, 1, 1, 1200, 1000, 384, 380, 2, 2, "normal", 49, 6, 51),
        ("ADSF - 100 pcs", "SCPL-901", 40, 50, 10, 5, 0, 0, 5, 5, 1, 1, 1200, 1000, 400, 260, 2, 2, "rotated", 50, 8, 1),
    ]
    existing = {r["name"]: r for r in conn.execute("SELECT * FROM product_models")}
    for (name, code, px, py, pcx, pcy, bl, br, bt, bb, gx, gy, sl, sw, pl, pw, kx, ky,
         orient, pcs, pps, sheets) in baseline:
        if name in existing:
            row = existing[name]
            if not (row["model_code"] or ""):
                conn.execute("UPDATE product_models SET model_code=? WHERE id=?", (code, row["id"]))
        else:
            conn.execute(
                "INSERT OR IGNORE INTO product_models (name, model_code, pcb_len, pcb_w, pcbs_x, pcbs_y, gap_x, gap_y, "
                "border_l, border_r, border_t, border_b, gang_x, gang_y, sheet_len, sheet_w, panel_len, panel_w, "
                "kerf_x, kerf_y, orientation, pcs_panel, panels_sheet, sheets, created_on) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, code, px, py, pcx, pcy, 0, 0, bl, br, bt, bb, gx, gy, sl, sw, pl, pw, kx, ky,
                 orient, pcs, pps, sheets, today))
    conn.commit()


def seed_if_empty(conn):
    cur = conn.execute("SELECT COUNT(*) AS c FROM orders")
    if cur.fetchone()["c"] > 0:
        return
    if not _claim_seed(conn, "core_seed"):
        return

    today = datetime.date.today()
    iso = lambda d: d.isoformat()

    # --- users (1 batch) ---
    conn.batch([
        ("INSERT OR IGNORE INTO users (username, password, name, role) VALUES (?,?,?,?)",
         ("sahil", "admin123", "Sahil Sharma", "admin")),
        ("INSERT OR IGNORE INTO users (username, password, name, role) VALUES (?,?,?,?)",
         ("ramesh", "op123", "Ramesh Kumar", "operator")),
        ("INSERT OR IGNORE INTO users (username, password, name, role) VALUES (?,?,?,?)",
         ("suresh", "op123", "Suresh Yadav", "operator")),
    ])

    # --- employees ---
    employees = [
        ("EMP001", "Sahil Sharma", "sahil@shivaya.in", "9810012301", "Management", "Director", "2019-04-01", "active", 60000),
        ("EMP002", "Ramesh Kumar", "ramesh@shivaya.in", "9810012302", "Production", "CNC Operator", "2021-06-15", "active", 18000),
        ("EMP003", "Suresh Yadav", "suresh@shivaya.in", "9810012303", "Production", "Lamination Operator", "2022-01-10", "active", 17000),
        ("EMP004", "Amit Patel", "amit@shivaya.in", "9810012304", "Quality", "QC Inspector", "2022-09-05", "active", 16000),
        ("EMP005", "Vikas Singh", "vikas@shivaya.in", "9810012305", "Administration", "Admin Executive", "2023-03-20", "active", 15000),
        ("EMP006", "Sonu", "sonu@shivaya.in", "9810012306", "Production", "Sheet cutter Man", "2023-06-01", "active", 12000),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO employees (emp_code, name, email, phone, department, designation, joining_date, status, salary) "
        "VALUES (?,?,?,?,?,?,?,?,?)", employees)
    conn.batch([("INSERT OR REPLACE INTO attendance (emp_id, date, status) VALUES (?,?,?)",
                 (i, iso(today), "present")) for i in range(1, 6)])

    # --- machines ---
    machines = [
        ("M1", "CNC Drilling Machine", "running", "#3"),
        ("M2", "Laminator Press", "running", "#4"),
        ("M3", "Etching Line", "running", "#2"),
        ("M4", "UV Exposure Unit", "running", "#1"),
        ("M5", "Routing Machine", "idle", ""),
        ("M6", "Flying Probe Tester", "maintenance", ""),
        ("M7", "Sheet Cutter", "running", "#3"),
    ]
    conn.executemany("INSERT OR IGNORE INTO machines (code, name, status, current_job) VALUES (?,?,?,?)", machines)

    # --- dispatch log demo: completed orders dispatched with mode/date/time ---
    if conn.execute("SELECT COUNT(*) c FROM dispatch_log").fetchone()["c"] == 0:
        conn.execute("INSERT OR IGNORE INTO dispatch_log (order_id, ddate, dtime, mode, details, dispatched_by, ts) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (1, "2026-08-16", "17:40", "Transport", "LR No. TS-2214 · Driver: Mahesh", "Ramesh Kumar",
                      "2026-08-16 17:45"))
        conn.execute("INSERT OR IGNORE INTO dispatch_log (order_id, ddate, dtime, mode, details, dispatched_by, ts) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (2, "2026-08-18", "11:15", "Courier", "DTDC · AWB 7788123456", "Suresh Yadav",
                      "2026-08-18 11:20"))

    # --- machine / shift / operator assignments (production planning) ---
    conn.execute("INSERT OR IGNORE INTO order_assignments (order_id, machine, shift, operator, planned_start) VALUES (?,?,?,?,?)",
                 (3, "Sheet Cutter", "Day", "Sonu", "2026-08-17"))
    conn.execute("INSERT OR IGNORE INTO order_assignments (order_id, machine, shift, operator, planned_start) VALUES (?,?,?,?,?)",
                 (4, "Unassigned", "", "Unassigned", ""))

    # --- orders (as per dashboard screenshot) ---
    orders = [
        ("#1", "AAS TECHNOLOGY (AADESH BHAIYYA)", "Single Side", "LED Board 8W - SCPL-101 · 15000 pcs", 15000, 245000,
         "Completed", "done", 100, "normal", "2026-08-15", "Amit Patel", 15000, 15000),
        ("#2", "LP GOLD", "Single Side", "RM 603 8W RD (L-936E) - SCPL-402 · 15092 pcs", 15092, 187500,
         "Completed", "done", 100, "urgent", "2026-08-15", "Amit Patel", 15092, 15092),
        ("#3", "ALLIED", "Single Side", "227-100W - SCPL-190 · 9984 pcs", 9984, 120000,
         "CNC Drilling", "pending", 6, "urgent", "2026-08-20", "Ramesh Kumar", 600, 600),
        ("#4", "ALLIED", "Single Side", "RM 603 8W RD (L-936E) - SCPL-424 · 15092 pcs", 15092, 154060.8,
         "Laminate Cutting", "pending", 0, "urgent", "2026-08-28", "Suresh Yadav", 0, 0),
        ("#5", "SHREE PCB WORKS", "Double Side", "LED Driver 12W - 5000 pcs", 5000, 85000,
         "Laminate Cutting", "scheduled", 0, "normal", "2026-09-05", "", 0, 0),
        ("#6", "VERTEX ELECTRONICS", "Single Side", "Power Board V2 - 8000 pcs", 8000, 110000,
         "Laminate Cutting", "scheduled", 0, "normal", "2026-09-12", "", 0, 0),
    ]
    for o in orders:
        conn.execute(
            "INSERT INTO orders (order_no, party, board, product, qty, value, current_process, status, progress, priority, delivery_date, operator, started_qty, finished_qty, created_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o[0], o[1], o[2], o[3], o[4], o[5], o[6], o[7], o[8], o[9], o[10], o[11], o[12], o[13], iso(today)))

    # --- process log ---
    plog = [
        (1, "Laminate Cutting", "done", "Suresh Yadav"),
        (1, "CNC Drilling", "done", "Ramesh Kumar"),
        (1, "Plating", "done", "Ramesh Kumar"),
        (1, "Etching", "done", "Ramesh Kumar"),
        (1, "Solder Mask", "done", "Amit Patel"),
        (1, "Silkscreen", "done", "Amit Patel"),
        (1, "Testing", "done", "Amit Patel"),
        (1, "Completed", "done", "Amit Patel"),
        (2, "Laminate Cutting", "done", "Suresh Yadav"),
        (2, "CNC Drilling", "done", "Ramesh Kumar"),
        (2, "Etching", "done", "Ramesh Kumar"),
        (2, "Testing", "done", "Amit Patel"),
        (2, "Completed", "done", "Amit Patel"),
        (3, "Laminate Cutting", "done", "Suresh Yadav"),
        (3, "CNC Drilling", "active", "Ramesh Kumar"),
    ]
    for order_id, process, status, operator in plog:
        conn.execute(
            "INSERT INTO process_log (order_id, process, status, operator, updated_on) VALUES (?,?,?,?,?)",
            (order_id, process, status, operator, iso(today)))

    # --- purchase orders ---
    pos = [
        ("PO-301", "SunTech Laminates", "FR4 Sheets 1.6mm - 200 pcs", "200 pcs", 96000, "received"),
        ("PO-302", "ChemCorp Industries", "Solder Mask Green Ink - 20 Ltr", "20 Ltr", 18000, "in_transit"),
        ("PO-303", "DrillTech Tools", "Carbide Drill Bits 0.8mm - 500 pcs", "500 pcs", 22500, "pending"),
    ]
    for po in pos:
        conn.execute("INSERT INTO purchase_orders (po_no, vendor, item, qty, amount, status, date) VALUES (?,?,?,?,?,?,?)",
                     (po[0], po[1], po[2], po[3], po[4], po[5], iso(today)))

    # --- inventory ---
    inventory = [
        ("INV-01", "FR4 Sheets 1.6mm", "Laminate", 120, 50, "sheets"),
        ("INV-02", "Copper Clad Laminate", "Laminate", 30, 40, "sheets"),
        ("INV-03", "Solder Mask Green Ink", "Chemical", 8, 10, "kg"),
        ("INV-04", "Silkscreen White Ink", "Chemical", 15, 10, "kg"),
        ("INV-05", "Etchant (Ferric Chloride)", "Chemical", 60, 30, "Ltr"),
        ("INV-06", "Drill Bits 0.8mm", "Tooling", 200, 100, "pcs"),
        ("INV-07", "Aluminum MCPCB-1MM", "Laminate", 150, 40, "sheets"),
        ("INV-08", "FR4 1.6MM", "Laminate", 180, 50, "sheets"),
        ("INV-09", "Lacquer", "Chemical", 30, 5, "Ltr"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO inventory (code, name, category, stock, min_stock, unit) VALUES (?,?,?,?,?,?)", inventory)

    # --- billing ---
    billing = [
        ("INV-101", "AAS TECHNOLOGY (AADESH BHAIYYA)", 245000, "paid", "2026-08-16"),
        ("INV-102", "LP GOLD", 187500, "paid", "2026-08-16"),
        ("INV-103", "ALLIED", 154060.8, "overdue", "2026-08-20"),
        ("INV-104", "SHREE PCB WORKS", 85000, "pending", "2026-08-26"),
    ]
    for b in billing:
        conn.execute("INSERT INTO billing (invoice_no, party, amount, status, date) VALUES (?,?,?,?,?)", b)

    # --- payments ---
    payments = [
        ("PMT-201", "AAS TECHNOLOGY (AADESH BHAIYYA)", "receipt", 245000, "UPI", "2026-08-17"),
        ("PMT-202", "LP GOLD", "receipt", 187500, "Bank Transfer", "2026-08-18"),
        ("PMT-203", "SunTech Laminates", "payment", 45000, "Bank Transfer", "2026-08-22"),
    ]
    for p in payments:
        conn.execute("INSERT INTO payments (ref_no, party, ptype, amount, mode, date) VALUES (?,?,?,?,?,?)", p)

    # --- quality ---
    quality = [
        ("QC-401", 1, "All 15000 pcs passed final inspection", "passed", "2026-08-15"),
        ("QC-402", 2, "15092 pcs passed. Packing done.", "passed", "2026-08-15"),
        ("QC-403", 3, "Inspection after CNC Drilling", "pending", ""),
    ]
    for q in quality:
        conn.execute("INSERT INTO quality (qc_no, order_id, remarks, result, date) VALUES (?,?,?,?,?)", q)

    # --- production plans ---
    plans = [
        ("PL-501", 3, "CNC Drilling → Plating → Testing", "2026-08-26", "2026-08-30", "inprogress"),
        ("PL-502", 5, "Laminate Cutting → Finished", "2026-09-01", "2026-09-08", "planned"),
    ]
    for p in plans:
        conn.execute("INSERT INTO plans (plan_no, order_id, desc, start_date, end_date, status) VALUES (?,?,?,?,?,?)", p)

    # --- material plans ---
    materials = [
        ("MP-601", "FR4 Sheets 1.6mm", 200, 120, 80, "Order 80 pcs", "pcs"),
        ("MP-602", "Solder Mask Green Ink", 15, 8, 7, "Order 7 Ltr", "Ltr"),
        ("MP-603", "Silkscreen White Ink", 10, 15, 0, "OK", "Ltr"),
    ]
    for m in materials:
        conn.execute(
            "INSERT INTO materials (mp_no, item, required, available, shortfall, action, unit) VALUES (?,?,?,?,?,?,?)", m)

    # --- cut lists ---
    cutlists = [
        ("CL-701", "ADSF - 100 pcs", 100, 4, 25, "saved"),
        ("CL-702", "227-100W - 9984 pcs", 9984, 6, 1664, "saved"),
    ]
    for c in cutlists:
        conn.execute(
            "INSERT INTO cutlists (cl_no, board, qty, panels_per_sheet, sheets, status, created_on) VALUES (?,?,?,?,?,?,?)",
            (c[0], c[1], c[2], c[3], c[4], c[5], iso(today)))

    conn.commit()


def multi(sqls):
    """Kai queries ka result ek hi round trip mein — [(sql, params), ...] -> [rows, ...].
    Turso par 1 HTTP batch call (fast); sqlite par sequential (same result)."""
    conn = get_db()
    if conn._turso:
        try:
            results = _turso().batch_fetch(sqls)
            conn.close()
            return [_norm_rows(r.rows, r.cols) for r in results]
        except Exception:
            pass  # batch fail -> neeche sequential fallback
    out = []
    for sql, params in sqls:
        out.append(conn.execute(sql, params).fetchall())
    conn.close()
    return out


def query(sql, params=(), one=False):
    conn = get_db()
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, params=()):
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id
