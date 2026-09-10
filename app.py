
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from dotenv import load_dotenv
import sqlite3
import os
import json
import requests
from functools import wraps
from pathlib import Path

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = APP_DIR / "myai.db"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-local-secret")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
YDC_API_KEY = os.getenv("YDC_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "owner")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "change-me-now")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY belum ada di .env")

gemini = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """
Kamu adalah My AI, asisten AI serbaguna.

Kamu membantu percakapan, coding, matematika, teknologi, Android,
belajar, menulis, debugging, brainstorming, pengetahuan umum,
dan memahami gambar/dokumen yang pengguna kirim.

Gunakan bahasa Indonesia bila pengguna memakai bahasa Indonesia.
Jawab natural, jelas, dan sesuai kebutuhan.
Untuk coding, utamakan kode yang dapat dijalankan dan aman.
Jangan mengarang fakta. Untuk informasi yang cepat berubah, gunakan
mode web bila tersedia.
Patuhi kebijakan keselamatan dan hukum. Jangan memberikan instruksi
untuk tindakan berbahaya atau ilegal.
"""

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'free',
                is_owner INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (OWNER_USERNAME,)
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(username, password_hash, tier, is_owner) VALUES (?, ?, 'premium', 1)",
                (OWNER_USERNAME, generate_password_hash(OWNER_PASSWORD))
            )

init_db()

def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT id, username, tier, is_owner FROM users WHERE id = ?",
            (uid,)
        ).fetchone()
        return dict(row) if row else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Login dulu."}), 401
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user["is_owner"]:
            return jsonify({"error": "Owner only."}), 403
        return fn(*args, **kwargs)
    return wrapper

def allowed(user, feature):
    if not user:
        return False
    if user["is_owner"]:
        return True
    if user["tier"] == "premium":
        return True
    free_limits = {
        "web": False,
        "file": False,
    }
    return free_limits.get(feature, True)

def needs_web(message):
    t = message.lower().strip()

    # Explicit web intent.
    explicit = (
        "cari di web", "search web", "browsing", "cari online",
        "sumber terbaru", "cek internet", "menurut berita"
    )
    if any(x in t for x in explicit):
        return True

    # Time-sensitive/current information.
    current = (
        "sekarang", "hari ini", "terbaru", "terkini", "kemarin",
        "besok", "minggu ini", "bulan ini", "tahun ini",
        "harga", "kurs", "nilai bitcoin", "bitcoin", "ethereum",
        "dogecoin", "crypto", "saham", "cuaca", "jadwal",
        "berita", "update", "rilis terbaru", "versi terbaru",
        "hasil pertandingan"
    )
    if any(x in t for x in current):
        return True

    # Named entities/topics that often benefit from fresh lookup.
    newsy = ("presiden", "menteri", "peraturan baru", "gempa", "banjir",
             "pemilu", "pemenang", "juara")
    if any(x in t for x in newsy) and len(t) > 12:
        return True

    return False

def build_contents(history, message, uploaded_file=None):
    contents = []

    for item in history[-8:]:
        role = item.get("role")
        text = item.get("content", "").strip()
        if role in ("user", "model") and text:
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part(text=text)]
                )
            )

    parts = [types.Part(text=message)]
    if uploaded_file is not None:
        # Files API objects can be passed directly to generate_content.
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(text=message),
                    uploaded_file
                ]
            )
        )
    else:
        contents.append(
            types.Content(role="user", parts=parts)
        )
    return contents

def stream_gemini(message, history, uploaded_file=None):
    contents = build_contents(history, message, uploaded_file)
    return gemini.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2048,
        )
    )

def ask_you(message):
    if not YDC_API_KEY:
        raise RuntimeError("YDC_API_KEY belum ada di .env")
    r = requests.post(
        "https://api.you.com/v1/answer",
        headers={
            "X-API-Key": YDC_API_KEY,
            "Content-Type": "application/json",
        },
        json={"query": message[:400]},
        timeout=20
    )
    if not r.ok:
        raise RuntimeError(f"You.com API {r.status_code}")
    data = r.json()
    return data.get("answer") or "You.com tidak mengembalikan jawaban."

@app.route("/")
def home():
    return render_template("index.html", user=current_user())

@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if len(username) < 3 or len(password) < 6:
        return jsonify({"error": "Username minimal 3 karakter, password minimal 6."}), 400
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password))
            )
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username sudah dipakai."}), 409

@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Username atau password salah."}), 401
    session["uid"] = row["id"]
    return jsonify({"ok": True, "user": {
        "username": row["username"],
        "tier": row["tier"],
        "owner": bool(row["is_owner"])
    }})

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.get("/api/me")
def me():
    return jsonify({"user": current_user()})

@app.post("/api/new-chat")
@login_required
def new_chat():
    session.pop("history", None)
    return jsonify({"ok": True})

@app.post("/api/upload")
@login_required
def upload():
    user = current_user()
    if not allowed(user, "file"):
        return jsonify({"error": "Upload file tersedia untuk Premium/Owner."}), 403

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "File tidak ditemukan."}), 400

    ext = Path(f.filename).suffix.lower()
    allowed_exts = {
        ".png", ".jpg", ".jpeg", ".webp",
        ".pdf", ".txt", ".csv", ".json",
        ".html", ".css", ".js", ".md"
    }
    if ext not in allowed_exts:
        return jsonify({"error": "Tipe file belum didukung."}), 400

    if (request.content_length or 0) > 50 * 1024 * 1024:
        return jsonify({"error": "File terlalu besar. Maksimal 50 MB."}), 413

    safe_name = f"{session['uid']}_{os.urandom(6).hex()}_{Path(f.filename).name}"
    path = UPLOAD_DIR / safe_name
    f.save(path)

    try:
        uploaded = gemini.files.upload(file=str(path))
    except Exception as e:
        path.unlink(missing_ok=True)
        return jsonify({"error": f"Upload ke Gemini gagal: {e}"}), 500

    # Store only temporary file metadata in session.
    session["pending_file"] = {
        "name": uploaded.name,
        "display_name": f.filename,
        "mime_type": getattr(uploaded, "mime_type", None),
        "local_path": str(path),
    }
    session.modified = True

    return jsonify({
        "ok": True,
        "filename": f.filename
    })

@app.get("/admin")
def admin_page():
    user = current_user()
    if not user or not user["is_owner"]:
        return ("Forbidden", 403)
    with db() as conn:
        users = [dict(r) for r in conn.execute(
            "SELECT id, username, tier, is_owner, created_at FROM users ORDER BY id DESC"
        ).fetchall()]
    return render_template("admin.html", users=users)

@app.post("/api/admin/set-tier")
@admin_required
def set_tier():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id", 0))
    tier = data.get("tier")
    if tier not in ("free", "premium"):
        return jsonify({"error": "Tier harus free/premium."}), 400
    with db() as conn:
        conn.execute(
            "UPDATE users SET tier = ? WHERE id = ? AND is_owner = 0",
            (tier, user_id)
        )
    return jsonify({"ok": True})

@app.post("/api/admin/delete-user")
@admin_required
def delete_user():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id", 0))
    with db() as conn:
        conn.execute(
            "DELETE FROM users WHERE id = ? AND is_owner = 0",
            (user_id,)
        )
    return jsonify({"ok": True})

@app.post("/api/chat")
@login_required
def chat():
    user = current_user()
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    mode = data.get("mode", "auto")

    if not message:
        return jsonify({"error": "Pesan kosong."}), 400


    history = session.get("history", [])
    pending = session.pop("pending_file", None)

    if mode == "web":
        try:
            answer = ask_you(message)
            history += [{"role": "user", "content": message},
                        {"role": "model", "content": answer}]
            session["history"] = history[-8:]
            return jsonify({"answer": answer})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    uploaded_obj = None
    local_path = None
    if pending:
        local_path = pending.get("local_path")
        try:
            uploaded_obj = gemini.files.get(name=pending["name"])
        except Exception:
            uploaded_obj = None

    def generate():
        full = ""
        try:
            stream = stream_gemini(message, history, uploaded_obj)
            for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    full += text
                    yield "data: " + json.dumps({"type": "text", "text": text}) + "\n\n"
            history2 = history + [
                {"role": "user", "content": message},
                {"role": "model", "content": full}
            ]
            session["history"] = history2[-8:]
            session.modified = True
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"type": "error", "error": str(e)}) + "\n\n"
        finally:
            if local_path:
                try:
                    Path(local_path).unlink(missing_ok=True)
                except Exception:
                    pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.get("/api/mode")
@login_required
def api_mode():
    user = current_user()
    return jsonify({
        "mode": "auto",
        "web_available": bool(user and (user["is_owner"] or user["tier"] == "premium"))
    })

@app.get("/health")
def health():
    return jsonify({
        "status": "online",
        "gemini": bool(GEMINI_API_KEY),
        "youcom": bool(YDC_API_KEY),
        "model": GEMINI_MODEL
    })

if __name__ == "__main__":
    print("My AI:", "http://127.0.0.1:5000")
    print("Owner:", OWNER_USERNAME)
    print("Model:", GEMINI_MODEL)
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
