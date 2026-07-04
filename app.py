from flask import Flask, jsonify, request, render_template
import json, uuid, os
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL_RAW = os.environ.get("DATABASE_URL", "")
DATABASE_URL = DATABASE_URL_RAW.replace("postgres://", "postgresql://", 1) if DATABASE_URL_RAW.startswith("postgres://") else DATABASE_URL_RAW


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_db()
    cur = conn.cursor()
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
    cur.close()
    conn.close()
    print("DB ready")


try:
    if DATABASE_URL:
        init_db()
        print("DB initialized OK")
    else:
        print("WARNING: no DATABASE_URL set")
except Exception as e:
    print("DB init error:", e)


def get_turno_colombia():
    tz_col = timezone(timedelta(hours=-5))
    now_col = datetime.now(timezone.utc).astimezone(tz_col)
    h = now_col.hour
    if 6 <= h < 14:
        return "T1 (06:00-14:00)"
    elif 14 <= h < 22:
        return "T2 (14:00-22:00)"
    else:
        return "T3 (22:00-06:00)"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/api/orders", methods=["GET"])
def get_orders():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT data FROM orders ORDER BY (data->>'createdAt') ASC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"orders": [r["data"] for r in rows]})
    except Exception as e:
        print("get_orders error:", e)
        return jsonify({"orders": [], "error": str(e)})


@app.route("/api/orders", methods=["POST"])
def create_order():
    data = request.json or {}
    now = datetime.utcnow().isoformat() + "Z"
    order = {
        "id": str(uuid.uuid4()),
        "ordenId": data.get("ordenId", ""),
        "producto": data.get("producto", ""),
        "cliente": data.get("cliente", ""),
        "maquina": data.get("maquina", ""),
        "operario": data.get("operario", ""),
        "turno": get_turno_colombia(),
        "cantidad": data.get("cantidad", 0),
        "metros": data.get("metros", 0),
        "velocidadObjetivo": data.get("velocidadObjetivo", 0),
        "velocidadActual": data.get("velocidadActual", 0),
        "notas": data.get("notas", ""),
        "status": data.get("status", "setup"),
        "createdAt": now,
        "ajustes": [],
        "history": [{"status": data.get("status", "setup"), "at": now}],
    }
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (id, data) VALUES (%s, %s)", (order["id"], json.dumps(order)))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("create_order error:", e)
        return jsonify({"error": str(e)}), 500
    return jsonify(order), 201


@app.route("/api/orders/<oid>/status", methods=["PATCH"])
def update_status(oid):
    new_status = (request.json or {}).get("status")
    now = datetime.utcnow().isoformat() + "Z"
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT data FROM orders WHERE id=%s", (oid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "not found"}), 404
        order = row["data"]
        order["status"] = new_status
        order["history"] = order.get("history", []) + [{"status": new_status, "at": now}]
        if new_status == "finalizada":
            order["finalizadaAt"] = now
        cur.execute("UPDATE orders SET data=%s, updated_at=NOW() WHERE id=%s", (json.dumps(order), oid))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("update_status error:", e)
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/orders/<oid>/velocidad", methods=["PATCH"])
def update_velocidad(oid):
    vel = (request.json or {}).get("velocidadActual", 0)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT data FROM orders WHERE id=%s", (oid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "not found"}), 404
        order = row["data"]
        order["velocidadActual"] = vel
        cur.execute("UPDATE orders SET data=%s, updated_at=NOW() WHERE id=%s", (json.dumps(order), oid))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("update_velocidad error:", e)
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/orders/<oid>/ajuste", methods=["POST"])
def add_ajuste(oid):
    data = request.json or {}
    now = datetime.utcnow().isoformat() + "Z"
    ajuste = {
        "descripcion": data.get("descripcion", ""),
        "duracion": data.get("duracion", ""),
        "reporta": data.get("reporta", ""),
        "at": now,
    }
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT data FROM orders WHERE id=%s", (oid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({"error": "not found"}), 404
        order = row["data"]
        order.setdefault("ajustes", []).append(ajuste)
        cur.execute("UPDATE orders SET data=%s, updated_at=NOW() WHERE id=%s", (json.dumps(order), oid))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("add_ajuste error:", e)
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/catalog", methods=["POST"])
def upload_catalog():
    rows = (request.json or {}).get("rows", [])
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM catalog")
        if rows:
            cur.execute("INSERT INTO catalog (data) VALUES (%s)", (json.dumps(rows),))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("upload_catalog error:", e)
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "count": len(rows)})


@app.route("/api/catalog", methods=["GET"])
def get_catalog():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT data FROM catalog ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return jsonify({"catalog": row["data"] if row else []})
    except Exception as e:
        print("get_catalog error:", e)
        return jsonify({"catalog": [], "error": str(e)})


@app.route("/api/reset", methods=["POST"])
def reset_orders():
    secret = (request.json or {}).get("secret", "")
    if secret != "coditeq2024":
        return jsonify({"error": "unauthorized"}), 403
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM orders")
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        print("reset error:", e)
        return jsonify({"error": str(e)}), 500



@app.route("/api/import", methods=["POST"])
def import_orders():
    """Import historical orders from the downloaded report."""
    secret = (request.json or {}).get("secret", "")
    if secret != "coditeq2024":
        return jsonify({"error": "unauthorized"}), 403
    
    rows = (request.json or {}).get("rows", [])
    imported = 0
    errors = []
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        for row in rows:
            try:
                order_id = str(uuid.uuid4())
                now = datetime.utcnow().isoformat() + "Z"
                
                # Map estado label back to status key
                estado_map = {
                    "Finalizada": "finalizada",
                    "Producción": "produccion", 
                    "Montaje": "setup",
                    "Ajuste": "ajuste"
                }
                status = estado_map.get(row.get("estado", "Finalizada"), "finalizada")
                
                order = {
                    "id": order_id,
                    "ordenId": str(row.get("ordenId", "")),
                    "producto": str(row.get("producto", "")),
                    "cliente": str(row.get("cliente", "")),
                    "maquina": str(row.get("maquina", "")),
                    "operario": str(row.get("operario", "")),
                    "turno": str(row.get("turno", "")),
                    "cantidad": 0,
                    "metros": float(row.get("metros", 0) or 0),
                    "velocidadObjetivo": float(row.get("velocidadObjetivo", 0) or 0),
                    "velocidadActual": float(row.get("velocidadActual", 0) or 0),
                    "notas": "Importado del reporte histórico",
                    "status": status,
                    "createdAt": now,
                    "finalizadaAt": now if status == "finalizada" else None,
                    "ajustes": [],
                    "history": [
                        {"status": "setup", "at": now},
                        {"status": status, "at": now}
                    ],
                    "_imported": True,
                    "_importedDate": row.get("fecha", ""),
                    "_tMontaje": str(row.get("tMontaje", "")),
                    "_tAjuste": str(row.get("tAjuste", "")),
                    "_tProduccion": str(row.get("tProduccion", "")),
                }
                
                cur.execute(
                    "INSERT INTO orders (id, data) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                    (order_id, json.dumps(order))
                )
                imported += 1
            except Exception as e:
                errors.append(str(e))
        
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    return jsonify({"ok": True, "imported": imported, "errors": errors})

if __name__ == "__main__":
    app.run(debug=True, threaded=True, host="0.0.0.0", port=5000)
