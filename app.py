from flask import Flask, jsonify, request, render_template, Response
import json, uuid, time, threading, os
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

# ── PostgreSQL connection ────────────────────────────────────────────────────
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS catalog (
                    id SERIAL PRIMARY KEY,
                    data JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
        conn.commit()

# ── SSE broadcast ────────────────────────────────────────────────────────────
subscribers = []
subs_lock   = threading.Lock()

def get_all_orders():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM orders ORDER BY (data->>'createdAt') ASC")
                rows = cur.fetchall()
                return [r["data"] for r in rows]
    except Exception as e:
        print("DB error get_orders:", e)
        return []

def broadcast():
    orders = get_all_orders()
    data = json.dumps({"orders": orders, "updated_at": datetime.utcnow().isoformat()})
    msg = f"event: update\ndata: {data}\n\n"
    with subs_lock:
        dead = []
        for q in subscribers:
            try:    q.append(msg)
            except: dead.append(q)
        for q in dead:
            subscribers.remove(q)

# ── Colombia timezone helper ─────────────────────────────────────────────────
def get_turno_colombia():
    tz_col = timezone(timedelta(hours=-5))
    now_col = datetime.now(timezone.utc).astimezone(tz_col)
    h = now_col.hour
    if 6 <= h < 14:    return "T1 (06:00-14:00)"
    elif 14 <= h < 22: return "T2 (14:00-22:00)"
    else:              return "T3 (22:00-06:00)"

# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/stream")
def stream():
    q = []
    with subs_lock:
        subscribers.append(q)
    orders = get_all_orders()
    initial = json.dumps({"orders": orders, "updated_at": datetime.utcnow().isoformat()})
    q.append(f"event: init\ndata: {initial}\n\n")

    def generate():
        try:
            while True:
                if q:   yield q.pop(0)
                else:
                    yield ": ping\n\n"
                    time.sleep(2)
        except GeneratorExit:
            with subs_lock:
                if q in subscribers: subscribers.remove(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/orders", methods=["GET"])
def get_orders():
    return jsonify({"orders": get_all_orders()})

@app.route("/api/orders", methods=["POST"])
def create_order():
    data = request.json
    now  = datetime.utcnow().isoformat() + "Z"
    order = {
        "id":                str(uuid.uuid4()),
        "ordenId":           data.get("ordenId", ""),
        "producto":          data.get("producto", ""),
        "cliente":           data.get("cliente", ""),
        "maquina":           data.get("maquina", ""),
        "operario":          data.get("operario", ""),
        "turno":             get_turno_colombia(),
        "cantidad":          data.get("cantidad", 0),
        "velocidadObjetivo": data.get("velocidadObjetivo", 0),
        "velocidadActual":   data.get("velocidadActual", 0),
        "notas":             data.get("notas", ""),
        "status":            data.get("status", "setup"),
        "createdAt":         now,
        "ajustes":           [],
        "history":           [{"status": data.get("status", "setup"), "at": now}],
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO orders (id, data) VALUES (%s, %s)",
                    (order["id"], json.dumps(order))
                )
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    broadcast()
    return jsonify(order), 201

@app.route("/api/orders/<oid>/status", methods=["PATCH"])
def update_status(oid):
    new_status = request.json.get("status")
    now = datetime.utcnow().isoformat() + "Z"
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM orders WHERE id=%s", (oid,))
                row = cur.fetchone()
                if not row: return jsonify({"error": "not found"}), 404
                order = row["data"]
                order["status"]  = new_status
                order["history"] = order.get("history", []) + [{"status": new_status, "at": now}]
                if new_status == "finalizada":
                    order["finalizadaAt"] = now
                cur.execute(
                    "UPDATE orders SET data=%s, updated_at=NOW() WHERE id=%s",
                    (json.dumps(order), oid)
                )
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    broadcast()
    return jsonify({"ok": True})

@app.route("/api/orders/<oid>/velocidad", methods=["PATCH"])
def update_velocidad(oid):
    vel = request.json.get("velocidadActual", 0)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM orders WHERE id=%s", (oid,))
                row = cur.fetchone()
                if not row: return jsonify({"error": "not found"}), 404
                order = row["data"]
                order["velocidadActual"] = vel
                cur.execute(
                    "UPDATE orders SET data=%s, updated_at=NOW() WHERE id=%s",
                    (json.dumps(order), oid)
                )
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    broadcast()
    return jsonify({"ok": True})

@app.route("/api/orders/<oid>/ajuste", methods=["POST"])
def add_ajuste(oid):
    data = request.json
    now  = datetime.utcnow().isoformat() + "Z"
    ajuste = {
        "descripcion": data.get("descripcion", ""),
        "duracion":    data.get("duracion", ""),
        "reporta":     data.get("reporta", ""),
        "at":          now
    }
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM orders WHERE id=%s", (oid,))
                row = cur.fetchone()
                if not row: return jsonify({"error": "not found"}), 404
                order = row["data"]
                order.setdefault("ajustes", []).append(ajuste)
                cur.execute(
                    "UPDATE orders SET data=%s, updated_at=NOW() WHERE id=%s",
                    (json.dumps(order), oid)
                )
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    broadcast()
    return jsonify({"ok": True})

@app.route("/api/catalog", methods=["POST"])
def upload_catalog():
    rows = request.json.get("rows", [])
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM catalog")
                if rows:
                    cur.execute(
                        "INSERT INTO catalog (data) VALUES (%s)",
                        (json.dumps(rows),)
                    )
            conn.commit()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "count": len(rows)})

@app.route("/api/catalog", methods=["GET"])
def get_catalog():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM catalog ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                catalog = row["data"] if row else []
                return jsonify({"catalog": catalog})
    except Exception as e:
        return jsonify({"catalog": [], "error": str(e)})

# Auto-init DB on startup
try:
    if DATABASE_URL:
        init_db()
        print("✅ DB initialized")
    else:
        print("⚠️  No DATABASE_URL set — running without persistence")
except Exception as e:
    print("❌ DB init error:", e)

@app.route("/api/reset", methods=["POST"])
def reset_orders():
    """Delete all orders — use with care."""
    secret = request.json.get("secret","")
    if secret != "coditeq2024":
        return jsonify({"error": "unauthorized"}), 403
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM orders")
            conn.commit()
        broadcast()
        return jsonify({"ok": True, "msg": "All orders deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, threaded=True, host="0.0.0.0", port=5000)
