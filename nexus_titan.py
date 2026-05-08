
import os
import sqlite3
import secrets
import datetime as dt
import time
import logging
from functools import wraps
from html import escape

from flask import Flask, g, request, session, redirect, url_for, flash, render_template_string, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(APP_DIR, "nexus_titan_pro.db")
UPLOAD_FOLDER = os.path.join(APP_DIR, "static", "uploads")
WALLPAPER_FOLDER = os.path.join(APP_DIR, "static", "wallpapers")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(WALLPAPER_FOLDER, exist_ok=True)

ADMIN_USERNAME = os.getenv("NEXUS_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("NEXUS_ADMIN_PASS", "admin123")
ADMIN_LOGIN_PATH = os.getenv("NEXUS_ADMIN_LOGIN_PATH", "/nx-console-7f3a")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(days=7)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nexus_titan_pro")

DEFAULT_AVATAR = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
DEFAULT_WALLPAPER = "https://images.unsplash.com/photo-1517816743773-6e0fd518b4a6?auto=format&fit=crop&w=1600&q=80"

def connect_db():
    db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db

def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def now_utc():
    return dt.datetime.utcnow()

def ts():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S")

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"png", "jpg", "jpeg", "gif", "webp", "mp4", "mov", "mkv", "webm", "mp3", "wav", "ogg", "pdf", "docx", "zip"}

def get_media_type(filename):
    ext = filename.rsplit(".", 1)[1].lower()
    if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
        return "image"
    if ext in {"mp4", "mov", "mkv", "webm"}:
        return "video"
    if ext in {"mp3", "wav", "ogg"}:
        return "audio"
    return "file"

def log_action(actor, action, detail=""):
    db = get_db()
    db.execute("INSERT INTO server_logs(actor, action, detail, created_at) VALUES (?,?,?,?)", (actor or "system", action, detail, ts()))
    db.commit()

def notify(target, actor, action, ref_type=None, ref_id=None):
    if not target or target == actor:
        return
    db = get_db()
    db.execute(
        "INSERT INTO notifications(target, actor, action, ref_type, ref_id, is_seen, created_at) VALUES (?,?,?,?,?,?,?)",
        (target, actor, action, ref_type, ref_id, 0, ts())
    )
    db.commit()

def ensure_column(table, column_def):
    col_name = column_def.split()[0]
    db = connect_db()
    try:
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        if col_name not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
            db.commit()
    finally:
        db.close()

def init_db():
    db = connect_db()
    c = db.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            status TEXT DEFAULT 'Ready on Nexus Titan',
            profile_pic TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            theme_mode TEXT DEFAULT 'dark',
            accent_color TEXT DEFAULT '#238636',
            chat_wallpaper TEXT DEFAULT '',
            last_active TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            follower TEXT NOT NULL,
            followed TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(follower, followed)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT NOT NULL,
            content TEXT,
            media TEXT,
            media_type TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user TEXT NOT NULL,
            comment_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT DEFAULT '',
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            user TEXT NOT NULL,
            post_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user, post_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            media TEXT NOT NULL,
            media_type TEXT,
            caption TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            msg TEXT,
            media TEXT,
            media_type TEXT,
            is_read INTEGER DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            ref_type TEXT,
            ref_id INTEGER,
            is_seen INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS server_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()

    # migration helpers
    for table, cols in {
        "users": [
            "is_admin INTEGER DEFAULT 0",
            "is_banned INTEGER DEFAULT 0",
            "theme_mode TEXT DEFAULT 'dark'",
            "accent_color TEXT DEFAULT '#238636'",
            "chat_wallpaper TEXT DEFAULT ''",
            "last_active TEXT DEFAULT ''",
            "created_at TEXT DEFAULT ''",
            "profile_pic TEXT DEFAULT ''",
            "status TEXT DEFAULT 'Ready on Nexus Titan'",
            "email TEXT",
        ],
        "comments": [
            "updated_at TEXT DEFAULT ''",
        ],
        "messages": [
            "is_deleted INTEGER DEFAULT 0",
            "is_read INTEGER DEFAULT 0",
        ],
    }.items():
        for col in cols:
            try:
                ensure_column(table, col)
            except Exception:
                pass

    # bootstrap admin user if absent
    db = connect_db()
    try:
        row = db.execute("SELECT 1 FROM users WHERE username=?", (ADMIN_USERNAME.lower(),)).fetchone()
        if not row:
            db.execute(
                "INSERT INTO users(username, password_hash, email, status, is_admin, created_at, last_active) VALUES (?,?,?,?,?,?,?)",
                (
                    ADMIN_USERNAME.lower(),
                    generate_password_hash(ADMIN_PASSWORD),
                    "",
                    "System Administrator",
                    1,
                    ts(),
                    ts(),
                ),
            )
            db.commit()
            log_action("system", "bootstrap_admin", f"Created {ADMIN_USERNAME.lower()}")
    finally:
        db.close()

init_db()

def current_user():
    if "user" not in session:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE username=?", (session["user"],)).fetchone()

def user_dict(username):
    db = get_db()
    return db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u["is_banned"]:
            session.clear()
            flash("Your account is banned.")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or not u["is_admin"]:
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper

@app.before_request
def touch_activity():
    u = current_user()
    if u:
        db = get_db()
        db.execute("UPDATE users SET last_active=? WHERE username=?", (ts(), u["username"]))
        db.commit()
        # cleanup expired stories
        db.execute("DELETE FROM stories WHERE created_at < datetime('now', '-24 hours')")
        db.commit()

def palette(u=None):
    mode = (u["theme_mode"] if u else "dark") if u else "dark"
    accent = (u["accent_color"] if u else "#238636") if u else "#238636"
    if mode == "light":
        return dict(
            mode="light",
            bg="#f5f7fb",
            card="rgba(255,255,255,0.92)",
            text="#101828",
            muted="#667085",
            border="rgba(16,24,40,0.10)",
            glass="rgba(255,255,255,0.72)",
            input_bg="#ffffff",
            bubble_in="rgba(16,24,40,0.06)",
            bubble_out=accent,
            accent=accent,
            danger="#d92d20",
            body_bg=f"linear-gradient(180deg, rgba(255,255,255,0.82), rgba(245,247,251,0.92))"
        )
    return dict(
        mode="dark",
        bg="#0b1220",
        card="rgba(15,23,42,0.88)",
        text="#e5e7eb",
        muted="#94a3b8",
        border="rgba(148,163,184,0.15)",
        glass="rgba(15,23,42,0.80)",
        input_bg="rgba(2,6,23,0.55)",
        bubble_in="rgba(148,163,184,0.12)",
        bubble_out=accent,
        accent=accent,
        danger="#f87171",
        body_bg="linear-gradient(180deg, rgba(2,6,23,0.92), rgba(15,23,42,0.98))"
    )

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{{ title }}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
:root{
  --bg: {{ ui.bg }};
  --card: {{ ui.card }};
  --text: {{ ui.text }};
  --muted: {{ ui.muted }};
  --border: {{ ui.border }};
  --glass: {{ ui.glass }};
  --input-bg: {{ ui.input_bg }};
  --bubble-in: {{ ui.bubble_in }};
  --bubble-out: {{ ui.bubble_out }};
  --accent: {{ ui.accent }};
  --danger: {{ ui.danger }};
  --accent-2: {{ ui.accent2|default("#7c3aed") }};
  --accent-3: {{ ui.accent3|default("#06b6d4") }};
  --accent-4: {{ ui.accent4|default("#f59e0b") }};
  --body-bg: {{ ui.body_bg }};
}
*{box-sizing:border-box; -webkit-tap-highlight-color:transparent; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif}
html,body{min-height:100%}
body{
  margin:0; color:var(--text);
  background:
    radial-gradient(circle at 10% 10%, color-mix(in srgb, var(--accent) 28%, transparent) 0, transparent 28%),
    radial-gradient(circle at 90% 12%, color-mix(in srgb, var(--accent-2) 22%, transparent) 0, transparent 24%),
    radial-gradient(circle at 80% 82%, color-mix(in srgb, var(--accent-3) 18%, transparent) 0, transparent 24%),
    radial-gradient(circle at 12% 86%, color-mix(in srgb, var(--accent-4) 18%, transparent) 0, transparent 24%),
    var(--body-bg), var(--bg);
  background-attachment: fixed;
  overflow-x:hidden;
  padding-bottom:96px;
}
a{color:inherit}
.container{max-width:960px; margin:0 auto; padding:16px}
.topbar{
  position:sticky; top:0; z-index:1000;
  display:flex; justify-content:space-between; align-items:center; gap:14px;
  padding:14px 16px; margin:0; border-bottom:1px solid var(--border);
  background: var(--glass); backdrop-filter: blur(18px);
}
.brand{font-weight:900; letter-spacing:-0.03em; font-size:22px}
.brand span{color:var(--accent)}
.toolbar{display:flex; align-items:center; gap:14px}
.icon-btn{position:relative; text-decoration:none; color:var(--text); font-size:18px; opacity:.95}
.badge{
  position:absolute; top:-7px; right:-10px; min-width:18px; height:18px;
  padding:0 5px; border-radius:999px; background:var(--danger); color:white;
  font-size:11px; display:flex; align-items:center; justify-content:center;
}
.card{
  background: linear-gradient(180deg, color-mix(in srgb, var(--card) 90%, white), var(--card));
  border:1px solid color-mix(in srgb, var(--border) 80%, var(--accent)); border-radius:24px;
  padding:18px; margin-bottom:14px; box-shadow: 0 20px 50px rgba(0,0,0,.18);
  backdrop-filter: blur(12px);
}
.grid{display:grid; gap:14px}
.grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}
@media (max-width:760px){ .grid.two{grid-template-columns:1fr} .container{padding:12px} }
input,textarea,select{
  width:100%; border:1px solid var(--border); background:var(--input-bg); color:var(--text);
  border-radius:16px; padding:14px 15px; outline:none; transition:.2s;
}
textarea{min-height:110px; resize:vertical}
input:focus,textarea:focus,select:focus{border-color:var(--accent); box-shadow:0 0 0 4px color-mix(in srgb, var(--accent) 18%, transparent)}
.btn{
  display:inline-flex; align-items:center; justify-content:center; gap:10px; border:none; cursor:pointer;
  border-radius:16px; padding:13px 18px; font-weight:700; text-decoration:none;
}
.btn.primary{background:var(--accent); color:white}
.btn.ghost{background:transparent; border:1px solid var(--border); color:var(--text)}
.btn.danger{background:var(--danger); color:white}
.btn.small{padding:9px 12px; border-radius:12px; font-size:13px}
.row{display:flex; gap:10px; align-items:center}
.stack{display:flex; flex-direction:column; gap:10px}
.muted{color:var(--muted)}
.avatar{width:46px; height:46px; border-radius:50%; object-fit:cover; border:2px solid rgba(255,255,255,.08)}
.avatar.big{width:120px; height:120px; border-width:4px}
.feed-stories{display:flex; gap:12px; overflow-x:auto; padding:6px 2px 14px}
.story-pill{min-width:72px; text-align:center; cursor:pointer}
.story-pill img, .story-pill .circle{
  width:66px; height:66px; border-radius:50%; object-fit:cover; border:3px solid var(--accent); padding:2px;
}
.story-pill .circle{display:flex; align-items:center; justify-content:center; border-style:dashed; color:var(--accent)}
.post-meta{display:flex; align-items:center; gap:10px}
.post-actions{display:flex; gap:22px; font-size:20px; align-items:center; margin-top:14px}
.post-actions i{cursor:pointer}
.count{font-size:12px; color:var(--muted); margin-top:10px}
.navbar{
  position:fixed; bottom:0; left:0; right:0; z-index:1000;
  background: var(--glass); backdrop-filter: blur(18px);
  border-top:1px solid var(--border); padding:10px 10px calc(10px + env(safe-area-inset-bottom));
}
.nav-inner{
  max-width:960px; margin:0 auto; display:flex; justify-content:space-around; align-items:center;
}
.nav-inner a{color:var(--muted); font-size:22px; position:relative; text-decoration:none}
.nav-inner a.active{color:var(--accent)}
.searchbar{display:flex; gap:10px; margin-bottom:14px}
.searchbar input{margin-bottom:0}
.flash{
  border-left:4px solid var(--accent); color:var(--text); padding:14px 16px; margin-bottom:14px
}
.section-title{display:flex; align-items:center; justify-content:space-between; gap:12px; margin:8px 0 14px}
.kpi{padding:16px; border-radius:22px; background:rgba(255,255,255,.03); border:1px solid var(--border)}
.kpi strong{font-size:26px}
.chat-shell{display:flex; flex-direction:column; min-height:calc(100vh - 170px); gap:12px}
.chat-head{display:flex; align-items:center; gap:12px; padding:14px 16px}
.chat-messages{display:flex; flex-direction:column; gap:10px; padding-bottom:130px}
.msg-row{display:flex}
.msg-row.out{justify-content:flex-end}
.msg-row.in{justify-content:flex-start}
.bubble{
  width:fit-content; max-width:min(86%, 620px); display:inline-flex; flex-direction:column;
  padding:12px 14px; border-radius:22px;
  border:1px solid var(--border); box-shadow:0 10px 25px rgba(0,0,0,.12);
  white-space:pre-wrap; line-height:1.5; word-break:break-word
}
.bubble.out{background:linear-gradient(135deg, var(--bubble-out), color-mix(in srgb, var(--accent-2) 35%, var(--bubble-out))); color:white; border-bottom-right-radius:6px}
.bubble.in{background:linear-gradient(135deg, var(--bubble-in), color-mix(in srgb, var(--accent-3) 10%, var(--bubble-in))); border-bottom-left-radius:6px}
.bubble img,.bubble video{max-width:100%; border-radius:16px; display:block; margin-bottom:10px}
.msg-time{font-size:11px; opacity:.65; margin-top:8px; text-align:right}
.msg-actions{display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; justify-content:flex-end}
.msg-actions a{font-size:11px; text-decoration:none; padding:7px 10px; border-radius:999px; border:1px solid rgba(255,255,255,.18); color:inherit; opacity:.92}
.bubble.in .msg-actions a{border-color:var(--border); color:var(--text)}
.chat-input{
  position:fixed; left:0; right:0; bottom:0; z-index:1200;
  padding:12px 12px calc(12px + env(safe-area-inset-bottom));
  background:linear-gradient(180deg, transparent, var(--bg) 24%, var(--bg));
}
.chat-input .inner{
  max-width:960px; margin:0 auto; display:flex; gap:10px; align-items:center;
  padding:10px 12px; border:1px solid var(--border); border-radius:22px; background:var(--glass); backdrop-filter:blur(18px);
}
.chat-input input{margin:0; border:none; background:transparent; padding:12px 8px}
.chat-input input:focus{box-shadow:none}
.badge-soft{
  display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px;
  border:1px solid var(--border); color:var(--muted); font-size:13px; background:rgba(255,255,255,.03)
}
</style>
</head>
<body>
{% if user %}
<div class="topbar">
  <div class="brand">NEXUS <span>TITAN</span></div>
  <div class="toolbar">
    <a class="icon-btn" href="/notifications"><i class="fa-solid fa-bolt"></i>{% if notif_count>0 %}<span class="badge">{{ notif_count }}</span>{% endif %}</a>
    <a class="icon-btn" href="/search"><i class="fa-solid fa-magnifying-glass"></i></a>
    <a class="icon-btn" href="/downloader"><i class="fa-solid fa-cloud-arrow-down"></i></a>
  </div>
</div>
{% endif %}

<div class="container">
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      {% for m in messages %}
      <div class="card flash">{{ m }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}
  {{ body|safe }}
</div>

{% if user %}
<div class="navbar">
  <div class="nav-inner">
    <a href="/feed" class="{{ 'active' if path=='/feed' else '' }}"><i class="fa-solid fa-house"></i></a>
    <a href="/explore" class="{{ 'active' if path=='/explore' else '' }}"><i class="fa-solid fa-compass"></i></a>
    <a href="/upload" class="{{ 'active' if path=='/upload' else '' }}"><i class="fa-solid fa-plus-square"></i></a>
    <a href="/chats" class="{{ 'active' if path.startswith('/chat') or path=='/chats' else '' }}"><i class="fa-solid fa-comment-dots"></i>{% if unread_msgs>0 %}<span class="badge">{{ unread_msgs }}</span>{% endif %}</a>
    <a href="/profile/{{ user.username }}" class="{{ 'active' if path.startswith('/profile') else '' }}"><i class="fa-solid fa-user"></i></a>
  </div>
</div>
{% endif %}

<script>
function togglePassword(btnId, inputId){
  const input = document.getElementById(inputId);
  const btn = document.getElementById(btnId);
  if(!input || !btn) return;
  const hidden = input.type === 'password';
  input.type = hidden ? 'text' : 'password';
  btn.innerHTML = hidden ? '<i class="fa-regular fa-eye-slash"></i>' : '<i class="fa-regular fa-eye"></i>';
}

function scrollChatToBottom(){
  const box = document.getElementById('chatBox');
  if(box) box.scrollTop = box.scrollHeight;
}
scrollChatToBottom();
</script>
</body>
</html>
"""

def unread_counts(username):
    db = get_db()
    unread_msgs = db.execute("SELECT COUNT(*) FROM messages WHERE receiver=? AND is_read=0 AND is_deleted=0", (username,)).fetchone()[0]
    notif_count = db.execute("SELECT COUNT(*) FROM notifications WHERE target=? AND is_seen=0", (username,)).fetchone()[0]
    return unread_msgs, notif_count

@app.context_processor
def inject_globals():
    u = current_user()
    if u:
        um, nc = unread_counts(u["username"])
        return dict(user=u, unread_msgs=um, notif_count=nc, path=request.path)
    return dict(user=None, unread_msgs=0, notif_count=0, path=request.path)

def render_page(body, title="Nexus Titan"):
    u = current_user()
    ui = palette(u)
    return render_template_string(BASE_TEMPLATE, title=title, body=body, ui=ui, user=u, unread_msgs=unread_counts(u["username"])[0] if u else 0, notif_count=unread_counts(u["username"])[1] if u else 0, path=request.path)

def visible_story_authors(username):
    db = get_db()
    row = db.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return []
    following = [r["followed"] for r in db.execute("SELECT followed FROM follows WHERE follower=?", (username,)).fetchall()]
    followers = [r["follower"] for r in db.execute("SELECT follower FROM follows WHERE followed=?", (username,)).fetchall()]
    visible = set([username]) | set(following) | set(followers)
    return list(visible)

@app.route("/")
def index():
    u = current_user()
    if u:
        return redirect(url_for("feed"))
    return redirect(url_for("login"))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        email = (request.form.get("email") or "").strip()
        if len(username) < 3 or len(password) < 4:
            flash("Username aur password thoda strong rakho.")
            return redirect(url_for("signup"))
        db = get_db()
        try:
            is_admin = 1 if username == ADMIN_USERNAME.lower() and password == ADMIN_PASSWORD else 0
            db.execute(
                "INSERT INTO users(username, password_hash, email, status, is_admin, is_banned, theme_mode, accent_color, chat_wallpaper, last_active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (username, generate_password_hash(password), email, "Ready on Nexus Titan", is_admin, 0, "dark", "#238636", "", ts(), ts()),
            )
            db.commit()
            session["user"] = username
            log_action(username, "signup", "new account created")
            return redirect(url_for("feed"))
        except sqlite3.IntegrityError:
            flash("Ye username already exist karta hai.")
    body = """
    <div class="grid two" style="align-items:center; min-height:78vh">
      <div class="card">
        <h1 style="margin-top:0; font-size:44px; letter-spacing:-0.04em">Join <span style="color:var(--accent)">Nexus Titan</span></h1>
        <p class="muted">Modern social space with stories, chat, followers, moderation aur admin controls.</p>
        <form method="POST" class="stack" style="margin-top:18px">
          <input name="username" placeholder="Username" required>
          <input type="email" name="email" placeholder="Email (optional)">
          <div style="position:relative">
            <input id="signupPass" type="password" name="password" placeholder="Password" required style="padding-right:54px">
            <button type="button" class="btn ghost small" id="signupPassBtn" onclick="togglePassword('signupPassBtn','signupPass')" style="position:absolute; right:8px; top:8px; padding:8px 10px"><i class="fa-regular fa-eye"></i></button>
          </div>
          <button class="btn primary">Create account</button>
        </form>
        <div style="margin-top:14px">Already have account? <a href="/login" style="color:var(--accent)">Login</a></div>
      </div>
      <div class="card">
        <div class="section-title"><h2 style="margin:0">About the web</h2><span class="badge-soft"><i class="fa-solid fa-bolt"></i> Fast & Colorful</span></div>
        <div class="grid" style="grid-template-columns:1fr 1fr">
          <div class="kpi"><strong>Stories</strong><div class="muted">24h auto delete</div></div>
          <div class="kpi"><strong>Chat</strong><div class="muted">Live refresh</div></div>
          <div class="kpi"><strong>Admin</strong><div class="muted">Moderation tools</div></div>
          <div class="kpi"><strong>Themes</strong><div class="muted">Light / Dark</div></div>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Sign up")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u and check_password_hash(u["password_hash"], password):
            if u["is_banned"]:
                flash("Aapka account banned hai.")
                return redirect(url_for("login"))
            session["user"] = username
            session.permanent = True
            log_action(username, "login", "user logged in")
            return redirect(url_for("feed"))
        flash("Login invalid hai.")
    body = """
    <div class="grid two" style="align-items:center; min-height:72vh">
      <div class="card">
        <h1 style="margin-top:0; font-size:44px; letter-spacing:-0.04em">Welcome back</h1>
        <p class="muted">Login and continue your conversations, posts, stories and admin tools.</p>
        <form method="POST" class="stack" style="margin-top:18px">
          <input name="username" placeholder="Username" required>
          <div style="position:relative">
            <input id="loginPass" type="password" name="password" placeholder="Password" required style="padding-right:54px">
            <button type="button" class="btn ghost small" id="loginPassBtn" onclick="togglePassword('loginPassBtn','loginPass')" style="position:absolute; right:8px; top:8px; padding:8px 10px"><i class="fa-regular fa-eye"></i></button>
          </div>
          <button class="btn primary">Login</button>
        </form>
        <div style="margin-top:14px">No account? <a href="/signup" style="color:var(--accent)">Create one</a></div>
      </div>
      <div class="card">
        <div class="section-title"><h2 style="margin:0">About this web</h2><span class="badge-soft"><i class="fa-solid fa-wand-magic-sparkles"></i> Social App</span></div>
        <p class="muted">Nexus Titan ek colorful social platform hai jisme chat, followers, stories, posts aur admin moderation sab ek saath milta hai.</p>
        <div class="kpi" style="margin-top:14px; display:grid; gap:10px">
          <div><strong>Clean UI</strong><br><span class="muted">Modern cards, glass effect aur responsive layout.</span></div>
          <div><strong>Smart chat</strong><br><span class="muted">Live refresh, edit/delete message aur media support.</span></div>
          <div><strong>Privacy</strong><br><span class="muted">Stories sirf network me dikhengi, 24h me auto-remove.</span></div>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Login")

@app.route("/logout")
def logout():
    user = session.get("user")
    session.clear()
    if user:
        log_action(user, "logout", "user logged out")
    return redirect(url_for("login"))

@app.route(ADMIN_LOGIN_PATH, methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        u = user_dict(username)
        if u and u["is_admin"] and check_password_hash(u["password_hash"], password):
            session["user"] = username
            log_action(username, "admin_login", "admin login successful")
            return redirect(url_for("admin_panel"))
        flash("Admin login invalid hai.")
    body = """
    <div class="card" style="max-width:560px; margin:28px auto">
      <h2 style="margin-top:0">Secure Console</h2>
      <p class="muted">Authorized administrators only.</p>
      <form method="POST" class="stack">
        <input name="username" placeholder="Console username" autocomplete="username">
        <div style="position:relative">
          <input id="adminPass" type="password" name="password" placeholder="Console password" autocomplete="current-password" style="padding-right:54px">
          <button type="button" class="btn ghost small" id="adminPassBtn" onclick="togglePassword('adminPassBtn','adminPass')" style="position:absolute; right:8px; top:8px; padding:8px 10px"><i class="fa-regular fa-eye"></i></button>
        </div>
        <button class="btn primary">Open console</button>
      </form>
    </div>
    """
    return render_page(body, "Admin Login")

@app.route("/feed")
@login_required
def feed():
    u = current_user()
    db = get_db()
    visible = visible_story_authors(u["username"])
    stories = db.execute(
        f"""
        SELECT s.*, u.profile_pic
        FROM stories s
        JOIN users u ON u.username=s.username
        WHERE s.created_at >= datetime('now','-24 hours')
          AND s.username IN ({",".join(["?"]*len(visible))})
        GROUP BY s.username
        ORDER BY s.created_at DESC
        """,
        visible,
    ).fetchall() if visible else []
    posts = db.execute("""
        SELECT p.*, us.profile_pic,
          (SELECT COUNT(*) FROM likes WHERE post_id=p.id) AS likes_count,
          (SELECT COUNT(*) FROM comments WHERE post_id=p.id) AS comments_count,
          (SELECT 1 FROM likes WHERE post_id=p.id AND user=?) AS liked
        FROM posts p JOIN users us ON us.username=p.author
        ORDER BY p.created_at DESC
        LIMIT 50
    """, (u["username"],)).fetchall()

    body = """
    <div class="searchbar">
      <form action="/search" method="GET" style="display:flex; gap:10px; width:100%">
        <input name="q" placeholder="Search users or posts...">
        <button class="btn ghost" style="padding:0 16px"><i class="fa-solid fa-search"></i></button>
      </form>
    </div>

    <div class="feed-stories">
      <div class="story-pill" onclick="location.href='/upload_story'">
        <div class="circle"><i class="fa-solid fa-plus"></i></div>
        <div style="font-size:11px; margin-top:6px">Story</div>
      </div>
    """
    for s in stories:
        pic = f"/static/uploads/{s['profile_pic']}" if s["profile_pic"] else DEFAULT_AVATAR
        body += f"""
      <div class="story-pill" onclick="location.href='/story/{s['username']}'">
        <img src="{pic}" onerror="this.src='{DEFAULT_AVATAR}'">
        <div style="font-size:11px; margin-top:6px">@{escape(s['username'])}</div>
      </div>
        """
    body += "</div>"

    if not posts:
        body += "<div class='card'>No posts yet.</div>"
    for p in posts:
        pic = f"/static/uploads/{p['profile_pic']}" if p["profile_pic"] else DEFAULT_AVATAR
        media = ""
        if p["media"]:
            if p["media_type"] == "image":
                media = f'<img src="/static/uploads/{p["media"]}" style="width:100%; border-radius:18px; margin-top:12px">'
            elif p["media_type"] == "video":
                media = f'<video src="/static/uploads/{p["media"]}" controls style="width:100%; border-radius:18px; margin-top:12px"></video>'
        liked = bool(p["liked"])
        body += f"""
        <div class="card">
          <div class="post-meta">
            <img class="avatar" src="{pic}" onerror="this.src='{DEFAULT_AVATAR}'">
            <div style="min-width:0; flex:1">
              <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap">
                <strong onclick="location.href='/profile/{p['author']}'" style="cursor:pointer">@{escape(p['author'])}</strong>
                <span class="muted" style="font-size:12px">{p['created_at'][:16]}</span>
              </div>
              <div class="muted" style="font-size:13px">{escape((p['content'] or '')[:120])}</div>
            </div>
          </div>
          <div style="margin-top:10px; line-height:1.55">{escape(p['content'] or '')}</div>
          {media}
          <div class="post-actions">
            <a href="/like/{p['id']}" title="Like" style="color:{'var(--danger)' if liked else 'var(--text)'}"><i class="{'fa-solid' if liked else 'fa-regular'} fa-heart"></i></a>
            <a href="/post/{p['id']}" title="Comments"><i class="fa-regular fa-comment"></i></a>
            <a href="/report/post/{p['id']}" title="Report"><i class="fa-regular fa-flag"></i></a>
          </div>
          <div class="count">{p['likes_count']} likes • {p['comments_count']} comments</div>
        </div>
        """
    return render_page(body, "Feed")

@app.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    db = get_db()
    users = []
    posts = []
    if q:
        users = db.execute("SELECT username, profile_pic, status FROM users WHERE username LIKE ? ORDER BY username LIMIT 30", (f"%{q}%",)).fetchall()
        posts = db.execute("""
            SELECT p.id, p.author, p.content, p.created_at, u.profile_pic
            FROM posts p JOIN users u ON u.username=p.author
            WHERE p.content LIKE ? OR p.author LIKE ?
            ORDER BY p.created_at DESC LIMIT 30
        """, (f"%{q}%", f"%{q}%")).fetchall()
    body = f"""
    <div class="card">
      <form method="GET" class="searchbar">
        <input name="q" value="{escape(q)}" placeholder="Search users or posts...">
        <button class="btn primary">Search</button>
      </form>
    </div>
    <div class="grid two">
      <div class="card">
        <h3 style="margin-top:0">Users</h3>
        {"".join([f"<div class='row' style='justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border)'><div class='row'><img class='avatar' src='/static/uploads/{u['profile_pic']}' onerror=\"this.src='{DEFAULT_AVATAR}'\"><div><strong>@{escape(u['username'])}</strong><div class='muted' style='font-size:12px'>{escape((u['status'] or '')[:50])}</div></div></div><a class='btn ghost small' href='/profile/{u['username']}'>Open</a></div>" for u in users]) or "<div class='muted'>No users found.</div>"}
      </div>
      <div class="card">
        <h3 style="margin-top:0">Posts</h3>
        {"".join([f"<div style='padding:10px 0; border-bottom:1px solid var(--border)'><a href='/post/{p['id']}' style='text-decoration:none'><strong>@{escape(p['author'])}</strong><div class='muted' style='font-size:12px'>{escape((p['content'] or '')[:70])}</div></a></div>" for p in posts]) or "<div class='muted'>No posts found.</div>"}
      </div>
    </div>
    """
    return render_page(body, "Search")

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    u = current_user()
    if request.method == "POST":
        content = (request.form.get("content") or "").strip()
        file = request.files.get("media")
        fname = ftype = None
        if file and file.filename and allowed_file(file.filename):
            fname = secure_filename(f"post_{int(time.time())}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
            ftype = get_media_type(fname)
        db = get_db()
        db.execute("INSERT INTO posts(author, content, media, media_type, created_at) VALUES (?,?,?,?,?)", (u["username"], content, fname, ftype, ts()))
        db.commit()
        log_action(u["username"], "create_post", "posted content")
        flash("Post published.")
        return redirect(url_for("feed"))
    body = """
    <div class="card" style="max-width:760px; margin:0 auto">
      <h2 style="margin-top:0">Create Post</h2>
      <form method="POST" enctype="multipart/form-data" class="stack">
        <textarea name="content" placeholder="What's on your mind?"></textarea>
        <input type="file" name="media">
        <button class="btn primary">Publish</button>
      </form>
    </div>
    """
    return render_page(body, "Create Post")

@app.route("/post/<int:post_id>", methods=["GET", "POST"])
@login_required
def post_detail(post_id):
    u = current_user()
    db = get_db()
    post = db.execute("""
        SELECT p.*, us.profile_pic,
          (SELECT COUNT(*) FROM likes WHERE post_id=p.id) AS likes_count,
          (SELECT 1 FROM likes WHERE post_id=p.id AND user=?) AS liked
        FROM posts p JOIN users us ON us.username=p.author
        WHERE p.id=?
    """, (u["username"], post_id)).fetchone()
    if not post:
        return render_page("<div class='card'>Post not found.</div>", "Not found"), 404
    if request.method == "POST":
        ctext = (request.form.get("comment") or "").strip()
        if ctext:
            db.execute("INSERT INTO comments(post_id, user, comment_text, created_at, updated_at) VALUES (?,?,?,?,?)", (post_id, u["username"], ctext, ts(), ""))
            db.commit()
            if post["author"] != u["username"]:
                notify(post["author"], u["username"], "commented on your post", "post", post_id)
            log_action(u["username"], "add_comment", f"post={post_id}")
            flash("Comment added.")
            return redirect(url_for("post_detail", post_id=post_id))
    comments = db.execute("SELECT * FROM comments WHERE post_id=? ORDER BY created_at ASC", (post_id,)).fetchall()
    pic = f"/static/uploads/{post['profile_pic']}" if post["profile_pic"] else DEFAULT_AVATAR
    media = ""
    if post["media"]:
        if post["media_type"] == "image":
            media = f'<img src="/static/uploads/{post["media"]}" style="width:100%; border-radius:20px; margin-top:12px">'
        elif post["media_type"] == "video":
            media = f'<video src="/static/uploads/{post["media"]}" controls style="width:100%; border-radius:20px; margin-top:12px"></video>'
    body = f"""
    <div class="card">
      <div class="post-meta">
        <img class="avatar" src="{pic}" onerror="this.src='{DEFAULT_AVATAR}'">
        <div style="flex:1">
          <div class="row"><strong>@{escape(post['author'])}</strong> <span class="muted">{post['created_at'][:16]}</span></div>
          <div class="muted">{escape((post['content'] or '')[:120])}</div>
        </div>
      </div>
      <div style="margin-top:10px; line-height:1.6">{escape(post['content'] or '')}</div>
      {media}
      <div class="post-actions">
        <a href="/like/{post_id}" style="color:{'var(--danger)' if post['liked'] else 'var(--text)'}"><i class="{'fa-solid' if post['liked'] else 'fa-regular'} fa-heart"></i></a>
        <a href="/report/post/{post_id}"><i class="fa-regular fa-flag"></i></a>
        {"<a href='/admin/delete-post/"+str(post_id)+"'><i class='fa-solid fa-trash'></i></a>" if u["is_admin"] else ""}
      </div>
      <div class="count">{post['likes_count']} likes • {len(comments)} comments</div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Add comment</h3>
      <form method="POST" class="stack">
        <textarea name="comment" placeholder="Write a comment"></textarea>
        <button class="btn primary">Comment</button>
      </form>
    </div>
    """
    for c in comments:
        body += f"""
        <div class="card">
          <div class="row" style="justify-content:space-between">
            <strong>@{escape(c['user'])}</strong>
            <span class="muted" style="font-size:12px">{c['updated_at'][:16] if c['updated_at'] else c['created_at'][:16]}</span>
          </div>
          <div style="margin-top:10px; line-height:1.6">{escape(c['comment_text'])}</div>
          <div class="row" style="margin-top:12px">
            {(f"<a class='btn ghost small' href='/comment/edit/{c['id']}'>Edit</a>" if c["user"] == u["username"] or u["is_admin"] else "")}
            {(f"<a class='btn ghost small' href='/comment/delete/{c['id']}'>Delete</a>" if c["user"] == u["username"] or u["is_admin"] else "")}
            <a class="btn ghost small" href="/report/comment/{c['id']}">Report</a>
          </div>
        </div>
        """
    return render_page(body, f"Post #{post_id}")

@app.route("/comment/edit/<int:comment_id>", methods=["GET", "POST"])
@login_required
def edit_comment(comment_id):
    u = current_user()
    db = get_db()
    c = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
    if not c:
        flash("Comment not found.")
        return redirect(url_for("feed"))
    if c["user"] != u["username"] and not u["is_admin"]:
        flash("Permission denied.")
        return redirect(url_for("post_detail", post_id=c["post_id"]))
    if request.method == "POST":
        new_text = (request.form.get("comment") or "").strip()
        if new_text:
            db.execute("UPDATE comments SET comment_text=?, updated_at=? WHERE id=?", (new_text, ts(), comment_id))
            db.commit()
            log_action(u["username"], "edit_comment", f"comment={comment_id}")
            flash("Comment updated.")
            return redirect(url_for("post_detail", post_id=c["post_id"]))
    body = f"""
    <div class="card" style="max-width:720px">
      <h3 style="margin-top:0">Edit Comment</h3>
      <form method="POST" class="stack">
        <textarea name="comment">{escape(c['comment_text'])}</textarea>
        <button class="btn primary">Save</button>
      </form>
    </div>
    """
    return render_page(body, "Edit Comment")

@app.route("/comment/delete/<int:comment_id>")
@login_required
def delete_comment(comment_id):
    u = current_user()
    db = get_db()
    c = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
    if not c:
        flash("Comment not found.")
        return redirect(url_for("feed"))
    if c["user"] != u["username"] and not u["is_admin"]:
        flash("Permission denied.")
        return redirect(url_for("post_detail", post_id=c["post_id"]))
    db.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    db.commit()
    log_action(u["username"], "delete_comment", f"comment={comment_id}")
    flash("Comment deleted.")
    return redirect(url_for("post_detail", post_id=c["post_id"]))

@app.route("/message/edit/<int:message_id>", methods=["GET", "POST"])
@login_required
def edit_message(message_id):
    u = current_user()
    db = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    if not msg:
        flash("Message not found.")
        return redirect(request.referrer or url_for("chats"))
    if msg["sender"] != u["username"] and not u["is_admin"]:
        flash("Permission denied.")
        return redirect(url_for("chat", target=msg["receiver"]))
    if request.method == "POST":
        new_msg = (request.form.get("msg") or "").strip()
        db.execute("UPDATE messages SET msg=? WHERE id=?", (new_msg, message_id))
        db.commit()
        log_action(u["username"], "edit_message", f"message={message_id}")
        flash("Message updated.")
        return redirect(url_for("chat", target=msg["receiver"]))
    body = f"""
    <div class="card" style="max-width:760px">
      <h3 style="margin-top:0">Edit Message</h3>
      <form method="POST" class="stack">
        <textarea name="msg">{escape(msg['msg'] or '')}</textarea>
        <button class="btn primary">Save changes</button>
      </form>
    </div>
    """
    return render_page(body, "Edit Message")


@app.route("/message/delete/<int:message_id>")
@login_required
def delete_message(message_id):
    u = current_user()
    db = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    if not msg:
        flash("Message not found.")
        return redirect(request.referrer or url_for("chats"))
    if msg["sender"] != u["username"] and not u["is_admin"]:
        flash("Permission denied.")
        return redirect(url_for("chat", target=msg["receiver"]))
    db.execute("DELETE FROM messages WHERE id=?", (message_id,))
    db.commit()
    log_action(u["username"], "delete_message", f"message={message_id}")
    flash("Message deleted.")
    return redirect(url_for("chat", target=msg["receiver"]))


@app.route("/like/<int:post_id>")
@login_required
def like(post_id):
    u = current_user()
    db = get_db()
    existing = db.execute("SELECT 1 FROM likes WHERE user=? AND post_id=?", (u["username"], post_id)).fetchone()
    if existing:
        db.execute("DELETE FROM likes WHERE user=? AND post_id=?", (u["username"], post_id))
        log_action(u["username"], "unlike", f"post={post_id}")
    else:
        db.execute("INSERT OR IGNORE INTO likes(user, post_id, created_at) VALUES (?,?,?)", (u["username"], post_id, ts()))
        log_action(u["username"], "like", f"post={post_id}")
        post = db.execute("SELECT author FROM posts WHERE id=?", (post_id,)).fetchone()
        if post:
            notify(post["author"], u["username"], "liked your post", "post", post_id)
    db.commit()
    return redirect(request.referrer or url_for("feed"))

@app.route("/follow/<username>")
@login_required
def follow(username):
    u = current_user()
    if username == u["username"]:
        flash("Apne aap ko follow nahi kar sakte.")
        return redirect(url_for("profile", username=username))
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
    if not target:
        flash("User not found.")
        return redirect(url_for("search"))
    exists = db.execute("SELECT 1 FROM follows WHERE follower=? AND followed=?", (u["username"], username.lower())).fetchone()
    if exists:
        db.execute("DELETE FROM follows WHERE follower=? AND followed=?", (u["username"], username.lower()))
        log_action(u["username"], "unfollow", username.lower())
        flash(f"You unfollowed @{username}.")
    else:
        db.execute("INSERT OR IGNORE INTO follows(follower, followed, created_at) VALUES (?,?,?)", (u["username"], username.lower(), ts()))
        log_action(u["username"], "follow", username.lower())
        notify(username.lower(), u["username"], "started following you", "user", 0)
        flash(f"You followed @{username}.")
    db.commit()
    return redirect(request.referrer or url_for("profile", username=username))

@app.route("/followers/<username>")
@login_required
def followers(username):
    db = get_db()
    rows = db.execute("""
      SELECT u.username, u.profile_pic, u.status
      FROM follows f JOIN users u ON u.username=f.follower
      WHERE f.followed=?
      ORDER BY f.created_at DESC
    """, (username.lower(),)).fetchall()
    body = f"<div class='card'><h2 style='margin-top:0'>@{escape(username)} Followers</h2></div>"
    for r in rows:
        pic = f"/static/uploads/{r['profile_pic']}" if r["profile_pic"] else DEFAULT_AVATAR
        body += f"<div class='card'><div class='row'><img class='avatar' src='{pic}' onerror=\"this.src='{DEFAULT_AVATAR}'\"><div style='flex:1'><strong>@{escape(r['username'])}</strong><div class='muted'>{escape((r['status'] or '')[:60])}</div></div><a class='btn ghost small' href='/profile/{r['username']}'>View</a></div></div>"
    return render_page(body, f"{username} followers")

@app.route("/following/<username>")
@login_required
def following(username):
    db = get_db()
    rows = db.execute("""
      SELECT u.username, u.profile_pic, u.status
      FROM follows f JOIN users u ON u.username=f.followed
      WHERE f.follower=?
      ORDER BY f.created_at DESC
    """, (username.lower(),)).fetchall()
    body = f"<div class='card'><h2 style='margin-top:0'>@{escape(username)} Following</h2></div>"
    for r in rows:
        pic = f"/static/uploads/{r['profile_pic']}" if r["profile_pic"] else DEFAULT_AVATAR
        body += f"<div class='card'><div class='row'><img class='avatar' src='{pic}' onerror=\"this.src='{DEFAULT_AVATAR}'\"><div style='flex:1'><strong>@{escape(r['username'])}</strong><div class='muted'>{escape((r['status'] or '')[:60])}</div></div><a class='btn ghost small' href='/profile/{r['username']}'>View</a></div></div>"
    return render_page(body, f"{username} following")

@app.route("/profile/<username>")
@login_required
def profile(username):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
    if not u:
        return render_page("<div class='card'>User not found.</div>", "Profile"), 404
    me = current_user()
    posts_count = db.execute("SELECT COUNT(*) FROM posts WHERE author=?", (u["username"],)).fetchone()[0]
    followers_count = db.execute("SELECT COUNT(*) FROM follows WHERE followed=?", (u["username"],)).fetchone()[0]
    following_count = db.execute("SELECT COUNT(*) FROM follows WHERE follower=?", (u["username"],)).fetchone()[0]
    is_following = db.execute("SELECT 1 FROM follows WHERE follower=? AND followed=?", (me["username"], u["username"])).fetchone() is not None
    pic = f"/static/uploads/{u['profile_pic']}" if u["profile_pic"] else DEFAULT_AVATAR
    posts = db.execute("""
      SELECT * FROM posts WHERE author=? ORDER BY created_at DESC LIMIT 20
    """, (u["username"],)).fetchall()
    body = f"""
    <div class="card" style="text-align:center">
      <img class="avatar big" src="{pic}" onerror="this.src='{DEFAULT_AVATAR}'">
      <h2 style="margin:14px 0 4px">@{escape(u['username'])}</h2>
      <div class="muted">{escape(u['status'] or '')}</div>
      <div class="row" style="justify-content:center; gap:14px; margin:18px 0">
        <a class="badge-soft" href="/followers/{u['username']}"><strong>{followers_count}</strong> followers</a>
        <a class="badge-soft" href="/following/{u['username']}"><strong>{following_count}</strong> following</a>
        <span class="badge-soft"><strong>{posts_count}</strong> posts</span>
      </div>
      <div class="row" style="justify-content:center; flex-wrap:wrap">
        {"<a class='btn primary' href='/follow/"+u['username']+"'>Unfollow</a>" if is_following else "<a class='btn primary' href='/follow/"+u['username']+"'>Follow</a>"}
        <a class="btn ghost" href="/chat/{u['username']}">Message</a>
        <a class="btn ghost" href="/report/user/{u['username']}">Report</a>
        {"<a class='btn ghost' href='/settings'>Settings</a>" if u['username']==me['username'] else ""}
      </div>
    </div>
    <div class="section-title"><h3 style="margin:0">Posts</h3></div>
    """
    if not posts:
        body += "<div class='card'>No posts yet.</div>"
    for p in posts:
        body += f"<div class='card'><div class='row' style='justify-content:space-between'><strong>{escape(p['author'])}</strong><span class='muted'>{p['created_at'][:16]}</span></div><div style='margin-top:10px'>{escape(p['content'] or '')}</div><div class='row' style='margin-top:12px'><a class='btn ghost small' href='/post/{p['id']}'>Open</a>{"<a class='btn ghost small' href='/admin/delete-post/"+str(p['id'])+"'>Delete</a>" if me['is_admin'] else ""}</div></div>"
    return render_page(body, f"@{u['username']}")

@app.route("/stories/<username>")
@login_required
def story_user(username):
    db = get_db()
    vis = visible_story_authors(current_user()["username"])
    if username.lower() not in vis and username.lower() != current_user()["username"]:
        flash("Ye story aapke network me visible nahi hai.")
        return redirect(url_for("feed"))
    rows = db.execute("""
      SELECT * FROM stories WHERE username=? AND created_at >= datetime('now','-24 hours') ORDER BY created_at DESC
    """, (username.lower(),)).fetchall()
    body = f"<div class='card'><h2 style='margin-top:0'>@{escape(username)} Stories</h2></div>"
    if not rows:
        body += "<div class='card'>No active stories.</div>"
    for s in rows:
        media = f"/static/uploads/{s['media']}"
        inner = f"<img src='{media}' style='width:100%; border-radius:20px'>" if s["media_type"]=="image" else f"<video src='{media}' controls style='width:100%; border-radius:20px'></video>"
        body += f"<div class='card'>{inner}<div style='margin-top:10px' class='muted'>{escape(s['caption'] or '')}</div></div>"
    return render_page(body, "Stories")

@app.route("/upload_story", methods=["GET", "POST"])
@login_required
def upload_story():
    u = current_user()
    if request.method == "POST":
        caption = (request.form.get("caption") or "").strip()
        file = request.files.get("story")
        if not file or not file.filename or not allowed_file(file.filename):
            flash("Valid story file select karo.")
            return redirect(url_for("upload_story"))
        fname = secure_filename(f"story_{int(time.time())}_{file.filename}")
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
        ftype = get_media_type(fname)
        db = get_db()
        db.execute("INSERT INTO stories(username, media, media_type, caption, created_at) VALUES (?,?,?,?,?)", (u["username"], fname, ftype, caption, ts()))
        db.commit()
        log_action(u["username"], "upload_story", fname)
        flash("Story posted.")
        return redirect(url_for("feed"))
    body = """
    <div class="card" style="max-width:720px">
      <h2 style="margin-top:0">Upload Story</h2>
      <form method="POST" enctype="multipart/form-data" class="stack">
        <input type="file" name="story" accept="image/*,video/*" required>
        <input name="caption" placeholder="Caption (optional)">
        <button class="btn primary">Post story</button>
      </form>
    </div>
    """
    return render_page(body, "Upload Story")

@app.route("/chats")
@login_required
def chats():
    u = current_user()
    db = get_db()
    rows = db.execute("""
      SELECT us.username, us.profile_pic, us.status,
             (SELECT msg FROM messages m WHERE ((m.sender=us.username AND m.receiver=?) OR (m.sender=? AND m.receiver=us.username)) AND m.is_deleted=0 ORDER BY m.created_at DESC LIMIT 1) AS last_msg,
             (SELECT COUNT(*) FROM messages m WHERE m.sender=us.username AND m.receiver=? AND m.is_read=0 AND m.is_deleted=0) AS unread
      FROM users us
      WHERE us.username != ? AND us.is_banned=0
      ORDER BY unread DESC, us.last_active DESC
    """, (u["username"], u["username"], u["username"], u["username"])).fetchall()
    body = "<div class='card'><h2 style='margin-top:0'>Chats</h2><div class='muted'>Direct conversations</div></div>"
    if not rows:
        body += "<div class='card'>No chats yet.</div>"
    for r in rows:
        pic = f"/static/uploads/{r['profile_pic']}" if r["profile_pic"] else DEFAULT_AVATAR
        body += f"""
        <div class="card" style="cursor:pointer" onclick="location.href='/chat/{r['username']}'">
          <div class="row">
            <img class="avatar" src="{pic}" onerror="this.src='{DEFAULT_AVATAR}'">
            <div style="flex:1; min-width:0">
              <div class="row" style="justify-content:space-between">
                <strong>@{escape(r['username'])}</strong>
                {"<span class='badge-soft' style='color:var(--accent)'>"+str(r['unread'])+" new</span>" if r["unread"] else ""}
              </div>
              <div class="muted" style="font-size:13px">{escape((r['last_msg'] or r['status'] or '')[:70])}</div>
            </div>
          </div>
        </div>
        """
    return render_page(body, "Chats")

@app.route("/chat/<target>", methods=["GET", "POST"])
@login_required
def chat(target):
    me = current_user()
    db = get_db()
    t = db.execute("SELECT * FROM users WHERE username=?", (target.lower(),)).fetchone()
    if not t:
        flash("User not found.")
        return redirect(url_for("chats"))
    if request.method == "POST":
        msg = (request.form.get("msg") or "").strip()
        file = request.files.get("file")
        fname = ftype = None
        if file and file.filename and allowed_file(file.filename):
            fname = secure_filename(f"chat_{int(time.time())}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], fname))
            ftype = get_media_type(fname)
        if msg or fname:
            db.execute(
                "INSERT INTO messages(sender, receiver, msg, media, media_type, is_read, is_deleted, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (me["username"], t["username"], msg, fname, ftype, 0, 0, ts())
            )
            db.commit()
            notify(t["username"], me["username"], "sent you a message", "message", 0)
            log_action(me["username"], "send_message", f"to={t['username']}")
        return redirect(url_for("chat", target=target.lower()))
    msgs = db.execute("""
      SELECT * FROM messages
      WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?)) AND is_deleted=0
      ORDER BY created_at ASC
    """, (me["username"], t["username"], t["username"], me["username"])).fetchall()
    bg = me["chat_wallpaper"] or DEFAULT_WALLPAPER
    top_pic = f"/static/uploads/{t['profile_pic']}" if t["profile_pic"] else DEFAULT_AVATAR
    body = f"""
    <div class="card" style="background-image:linear-gradient(180deg, rgba(0,0,0,.12), rgba(0,0,0,.32)), url('{bg}'); background-size:cover; background-position:center; border:none">
      <div style="background:rgba(0,0,0,.30); border:1px solid rgba(255,255,255,.10); border-radius:22px; padding:12px 14px">
        <div class="row">
          <a href="/chats" style="text-decoration:none"><i class="fa-solid fa-chevron-left"></i></a>
          <img class="avatar" src="{top_pic}" onerror="this.src='{DEFAULT_AVATAR}'">
          <div style="flex:1">
            <strong>@{escape(t['username'])}</strong>
            <div class="muted" style="font-size:12px">{escape(t['status'] or '')}</div>
          </div>
          <a class="btn ghost small" href="/profile/{t['username']}">Profile</a>
        </div>
      </div>
    </div>

    <div class="chat-shell">
      <div id="chatBox" class="chat-messages">
    """
    if not msgs:
        body += "<div class='card'>Say hello 👋</div>"
    for m in msgs:
        out = m["sender"] == me["username"]
        bubble_class = "out" if out else "in"
        media = ""
        if m["media"]:
            if m["media_type"] == "image":
                media = f"<img src='/static/uploads/{m['media']}'>"
            elif m["media_type"] == "video":
                media = f"<video src='/static/uploads/{m['media']}' controls></video>"
            else:
                media = f"<div class='badge-soft'><i class='fa-solid fa-file'></i> {escape(m['media'])}</div>"
        actions = ""
        if out or me["is_admin"]:
            actions = (
                f"<div class='msg-actions'>"
                f"<a href='/message/edit/{m['id']}'><i class='fa-regular fa-pen-to-square'></i> Edit</a>"
                f"<a href='/message/delete/{m['id']}' onclick='return confirm(\"Delete this message?\")'><i class='fa-regular fa-trash-can'></i> Delete</a>"
                f"</div>"
            )
        body += f"""
        <div class="msg-row {'out' if out else 'in'}">
          <div class="bubble {bubble_class}">
            {media}
            <div>{escape(m['msg'] or '')}</div>
            <div class="msg-time">{m['created_at'][11:16]}</div>
            {actions}
          </div>
        </div>
        """
    body += f"""
      </div>
    </div>

    <form method="POST" enctype="multipart/form-data" class="chat-input">
      <div class="inner">
        <label style="cursor:pointer; color:var(--muted); font-size:20px">
          <i class="fa-solid fa-paperclip"></i>
          <input type="file" name="file" style="display:none" onchange="this.form.submit()">
        </label>
        <input id="msgInput" name="msg" placeholder="Type a message..." autocomplete="off">
        <button class="btn primary" style="border-radius:999px; width:48px; height:48px; padding:0"><i class="fa-solid fa-paper-plane"></i></button>
      </div>
    </form>

    <script>
      async function refreshChat(){{
        try {{
          const res = await fetch('/api/chat/{t['username']}');
          const data = await res.json();
          const box = document.getElementById('chatBox');
          if(!box) return;
          box.innerHTML = data.html;
          scrollChatToBottom();
        }} catch(e) {{}}
      }}
      setInterval(refreshChat, 3000);
    </script>
    """
    return render_page(body, f"Chat @{t['username']}")

@app.route("/api/chat/<target>")
@login_required
def api_chat(target):
    me = current_user()
    db = get_db()
    msgs = db.execute("""
      SELECT * FROM messages
      WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?)) AND is_deleted=0
      ORDER BY created_at ASC
    """, (me["username"], target.lower(), target.lower(), me["username"])).fetchall()
    html = ""
    for m in msgs:
        out = m["sender"] == me["username"]
        media = ""
        if m["media"]:
            if m["media_type"] == "image":
                media = f"<img src='/static/uploads/{m['media']}'>"
            elif m["media_type"] == "video":
                media = f"<video src='/static/uploads/{m['media']}' controls></video>"
            else:
                media = f"<div class='badge-soft'><i class='fa-solid fa-file'></i> {escape(m['media'])}</div>"
        actions = ""
        if out or me["is_admin"]:
            actions = (
                f"<div class='msg-actions'>"
                f"<a href='/message/edit/{m['id']}'><i class='fa-regular fa-pen-to-square'></i> Edit</a>"
                f"<a href='/message/delete/{m['id']}' onclick='return confirm(\"Delete this message?\")'><i class='fa-regular fa-trash-can'></i> Delete</a>"
                f"</div>"
            )
        html += f"""
        <div class="msg-row {'out' if out else 'in'}">
          <div class="bubble {'out' if out else 'in'}">
            {media}
            <div>{escape(m['msg'] or '')}</div>
            <div class="msg-time">{m['created_at'][11:16]}</div>
            {actions}
          </div>
        </div>
        """
    return jsonify({"html": html})

@app.route("/notifications")
@login_required
def notifications():
    u = current_user()
    db = get_db()
    rows = db.execute("SELECT * FROM notifications WHERE target=? ORDER BY created_at DESC LIMIT 80", (u["username"],)).fetchall()
    db.execute("UPDATE notifications SET is_seen=1 WHERE target=?", (u["username"],))
    db.commit()
    body = "<div class='card'><h2 style='margin-top:0'>Notifications</h2></div>"
    if not rows:
        body += "<div class='card'>No notifications.</div>"
    for n in rows:
        body += f"<div class='card'><div class='row' style='justify-content:space-between'><div><strong>@{escape(n['actor'])}</strong> {escape(n['action'])}</div><span class='muted'>{n['created_at'][:16]}</span></div></div>"
    return render_page(body, "Notifications")

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    u = current_user()
    db = get_db()
    if request.method == "POST":
        status = (request.form.get("status") or "").strip()
        theme_mode = request.form.get("theme_mode") or "dark"
        accent_color = (request.form.get("accent_color") or "#238636").strip()
        chat_wallpaper = (request.form.get("chat_wallpaper") or "").strip()
        pic = request.files.get("profile_pic")
        pic_name = u["profile_pic"]
        if pic and pic.filename and allowed_file(pic.filename):
            pic_name = secure_filename(f"profile_{u['username']}_{int(time.time())}_{pic.filename}")
            pic.save(os.path.join(app.config["UPLOAD_FOLDER"], pic_name))
        db.execute("""
            UPDATE users SET status=?, theme_mode=?, accent_color=?, chat_wallpaper=?, profile_pic=?
            WHERE username=?
        """, (status, theme_mode, accent_color, chat_wallpaper, pic_name, u["username"]))
        db.commit()
        log_action(u["username"], "update_settings", "profile/theme updated")
        flash("Settings saved.")
        return redirect(url_for("settings"))
    body = f"""
    <div class="grid two">
      <div class="card">
        <h2 style="margin-top:0">Your Settings</h2>
        <form method="POST" enctype="multipart/form-data" class="stack">
          <textarea name="status" placeholder="Your status">{escape(u['status'] or '')}</textarea>
          <select name="theme_mode">
            <option value="dark" {"selected" if u["theme_mode"]=="dark" else ""}>Dark mode</option>
            <option value="light" {"selected" if u["theme_mode"]=="light" else ""}>Light mode</option>
          </select>
          <input name="accent_color" value="{escape(u['accent_color'] or '#238636')}" placeholder="#238636">
          <input name="chat_wallpaper" value="{escape(u['chat_wallpaper'] or '')}" placeholder="Chat wallpaper URL">
          <input type="file" name="profile_pic">
          <button class="btn primary">Save</button>
        </form>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Quick stats</h3>
        <div class="grid two">
          <div class="kpi"><strong>{db.execute('SELECT COUNT(*) FROM posts WHERE author=?', (u['username'],)).fetchone()[0]}</strong><div class="muted">Posts</div></div>
          <div class="kpi"><strong>{db.execute('SELECT COUNT(*) FROM follows WHERE follower=?', (u['username'],)).fetchone()[0]}</strong><div class="muted">Following</div></div>
          <div class="kpi"><strong>{db.execute('SELECT COUNT(*) FROM follows WHERE followed=?', (u['username'],)).fetchone()[0]}</strong><div class="muted">Followers</div></div>
          <div class="kpi"><strong>{db.execute('SELECT COUNT(*) FROM comments WHERE user=?', (u['username'],)).fetchone()[0]}</strong><div class="muted">Comments</div></div>
        </div>
      </div>
    </div>
    """
    return render_page(body, "Settings")

@app.route("/report/<content_type>/<item>")
@login_required
def report(content_type, item):
    u = current_user()
    body = f"""
    <div class="card" style="max-width:650px">
      <h2 style="margin-top:0">Report {escape(content_type)}</h2>
      <form method="POST" action="/report/{escape(content_type)}/{escape(item)}" class="stack">
        <input name="reason" placeholder="Reason (spam, abuse, etc.)" required>
        <button class="btn danger">Submit report</button>
      </form>
    </div>
    """
    return render_page(body, "Report")

@app.route("/report/<content_type>/<item>", methods=["POST"])
@login_required
def report_submit(content_type, item):
    u = current_user()
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("Reason required.")
        return redirect(request.referrer or url_for("feed"))
    try:
        content_id = int(item)
    except Exception:
        content_id = 0
    db = get_db()
    db.execute("INSERT INTO reports(reporter, content_type, content_id, reason, status, created_at) VALUES (?,?,?,?,?,?)", (u["username"], content_type, content_id, reason, "open", ts()))
    db.commit()
    log_action(u["username"], "report", f"{content_type}:{item}")
    flash("Report submitted.")
    return redirect(request.referrer or url_for("feed"))

@app.route("/admin")
@admin_required
def admin_panel():
    u = current_user()
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_posts = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total_comments = db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    total_reports = db.execute("SELECT COUNT(*) FROM reports WHERE status='open'").fetchone()[0]
    active_users = db.execute("SELECT COUNT(*) FROM users WHERE last_active >= datetime('now','-5 minutes')").fetchone()[0]
    open_reports = db.execute("SELECT * FROM reports WHERE status='open' ORDER BY created_at DESC LIMIT 50").fetchall()
    logs = db.execute("SELECT * FROM server_logs ORDER BY created_at DESC LIMIT 60").fetchall()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 50").fetchall()
    posts = db.execute("""
      SELECT p.*, COUNT(c.id) AS ccount
      FROM posts p LEFT JOIN comments c ON c.post_id=p.id
      GROUP BY p.id
      ORDER BY p.created_at DESC LIMIT 25
    """).fetchall()
    active_rows = db.execute("SELECT username, last_active, is_banned FROM users WHERE last_active >= datetime('now','-5 minutes') ORDER BY last_active DESC").fetchall()
    ann = db.execute("SELECT * FROM announcements ORDER BY created_at DESC LIMIT 20").fetchall()

    body = f"""
    <div class="section-title"><h2 style="margin:0">Admin Dashboard</h2><span class="badge-soft"><i class="fa-solid fa-shield-halved"></i> @{escape(u['username'])}</span></div>
    <div class="grid two">
      <div class="card kpi"><strong>{total_users}</strong><div class="muted">Total Users</div></div>
      <div class="card kpi"><strong>{total_posts}</strong><div class="muted">Total Posts</div></div>
      <div class="card kpi"><strong>{total_comments}</strong><div class="muted">Total Comments</div></div>
      <div class="card kpi"><strong>{total_reports}</strong><div class="muted">Open Reports</div></div>
      <div class="card kpi"><strong>{active_users}</strong><div class="muted">Active Users (5 min)</div></div>
      <div class="card kpi"><strong>{db.execute('SELECT COUNT(*) FROM announcements').fetchone()[0]}</strong><div class="muted">Announcements</div></div>
    </div>

    <div class="grid two">
      <div class="card">
        <h3 style="margin-top:0">Recent chats overview</h3>
        <div class="stack">
          {''.join([f"<div class='badge-soft' style='justify-content:space-between'><span><strong>@{escape(m['sender'])}</strong> → @{escape(m['receiver'])}</span><span>{escape((m['msg'] or '')[:28])}</span></div>" for m in db.execute("SELECT sender, receiver, msg FROM messages ORDER BY created_at DESC LIMIT 10").fetchall()])}
        </div>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Followers snapshot</h3>
        <div class="stack">
          {''.join([f"<div class='badge-soft' style='justify-content:space-between'><span>@{escape(f['follower'])}</span><span>→ @{escape(f['followed'])}</span></div>" for f in db.execute("SELECT follower, followed FROM follows ORDER BY created_at DESC LIMIT 10").fetchall()])}
        </div>
      </div>
    </div>

    <div class="grid two">
      <div class="card">
        <h3 style="margin-top:0">Broadcast announcement</h3>
        <form method="POST" action="/admin/announce" class="stack">
          <input name="title" placeholder="Title" required>
          <textarea name="body" placeholder="Announcement body" required></textarea>
          <button class="btn primary">Publish</button>
        </form>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Live active users</h3>
        {"".join([f"<div class='row' style='justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border)'><strong>@{escape(r['username'])}</strong><span class='muted'>{r['last_active']}</span></div>" for r in active_rows]) or "<div class='muted'>No active users.</div>"}
      </div>
    </div>

    <div class="grid two">
      <div class="card">
        <h3 style="margin-top:0">Open reports</h3>
        {"".join([f"<div style='padding:10px 0; border-bottom:1px solid var(--border)'><strong>#{r['id']}</strong> {escape(r['content_type'])}:{r['content_id']}<div class='muted'>{escape(r['reason'])} • by @{escape(r['reporter'])}</div><div class='row' style='margin-top:8px'><a class='btn ghost small' href='/admin/resolve-report/{r['id']}'>Resolve</a><a class='btn danger small' href='/admin/delete-reported/{r['id']}'>Delete target</a></div></div>" for r in open_reports]) or "<div class='muted'>No open reports.</div>"}
      </div>
      <div class="card">
        <h3 style="margin-top:0">Server logs</h3>
        <div style="max-height:420px; overflow:auto">
        {"".join([f"<div style='padding:8px 0; border-bottom:1px solid var(--border)'><strong>{escape(l['action'])}</strong><div class='muted'>{escape(l['actor'])} • {escape(l['detail'] or '')}</div><div class='muted' style='font-size:12px'>{l['created_at']}</div></div>" for l in logs]) or "<div class='muted'>No logs.</div>"}
        </div>
      </div>
    </div>

    <div class="grid two">
      <div class="card">
        <h3 style="margin-top:0">User moderation</h3>
        {"".join([f"<div style='padding:10px 0; border-bottom:1px solid var(--border)'><div class='row' style='justify-content:space-between'><div><strong>@{escape(r['username'])}</strong><div class='muted'>{'ADMIN' if r['is_admin'] else 'USER'} • {'BANNED' if r['is_banned'] else 'OK'}</div></div><div class='row'><a class='btn ghost small' href='/admin/toggle-ban/{r['username']}'>{'Unban' if r['is_banned'] else 'Ban'}</a><a class='btn danger small' href='/admin/delete-user/{r['username']}'>Delete</a></div></div></div>" for r in users if r["username"] != ADMIN_USERNAME.lower()]) or "<div class='muted'>No users.</div>"}
      </div>
      <div class="card">
        <h3 style="margin-top:0">Posts moderation</h3>
        {"".join([f"<div style='padding:10px 0; border-bottom:1px solid var(--border)'><div class='row' style='justify-content:space-between'><div><strong>#{p['id']} @{escape(p['author'])}</strong><div class='muted'>{escape((p['content'] or '')[:80])}</div></div><a class='btn danger small' href='/admin/delete-post/{p['id']}'>Remove</a></div></div>" for p in posts]) or "<div class='muted'>No posts.</div>"}
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Announcements</h3>
      {"".join([f"<div style='padding:10px 0; border-bottom:1px solid var(--border)'><strong>{escape(a['title'])}</strong><div class='muted'>{escape(a['body'])}</div><div class='muted' style='font-size:12px'>{a['created_at']}</div></div>" for a in ann]) or "<div class='muted'>No announcements yet.</div>"}
    </div>
    """
    return render_page(body, "Admin")

@app.route("/admin/announce", methods=["POST"])
@admin_required
def admin_announce():
    u = current_user()
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    if title and body:
        db = get_db()
        db.execute("INSERT INTO announcements(admin, title, body, created_at) VALUES (?,?,?,?)", (u["username"], title, body, ts()))
        db.commit()
        log_action(u["username"], "announcement", title)
        flash("Announcement published.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/toggle-ban/<username>")
@admin_required
def admin_toggle_ban(username):
    u = current_user()
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE username=?", (username.lower(),)).fetchone()
    if not target:
        flash("User not found.")
        return redirect(url_for("admin_panel"))
    new_value = 0 if target["is_banned"] else 1
    db.execute("UPDATE users SET is_banned=? WHERE username=?", (new_value, username.lower()))
    db.commit()
    log_action(u["username"], "toggle_ban", f"{username.lower()} -> {new_value}")
    flash("User updated.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete-user/<username>")
@admin_required
def admin_delete_user(username):
    u = current_user()
    username = username.lower()
    if username == ADMIN_USERNAME.lower():
        flash("Default admin delete nahi kar sakte.")
        return redirect(url_for("admin_panel"))
    db = get_db()
    db.execute("DELETE FROM users WHERE username=?", (username,))
    db.execute("DELETE FROM posts WHERE author=?", (username,))
    db.execute("DELETE FROM comments WHERE user=?", (username,))
    db.execute("DELETE FROM likes WHERE user=?", (username,))
    db.execute("DELETE FROM follows WHERE follower=? OR followed=?", (username, username))
    db.execute("DELETE FROM messages WHERE sender=? OR receiver=?", (username, username))
    db.commit()
    log_action(u["username"], "delete_user", username)
    flash("User deleted.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete-post/<int:post_id>")
@admin_required
def admin_delete_post(post_id):
    u = current_user()
    db = get_db()
    db.execute("DELETE FROM posts WHERE id=?", (post_id,))
    db.execute("DELETE FROM comments WHERE post_id=?", (post_id,))
    db.execute("DELETE FROM likes WHERE post_id=?", (post_id,))
    db.execute("DELETE FROM reports WHERE content_type='post' AND content_id=?", (post_id,))
    db.commit()
    log_action(u["username"], "delete_post", f"post={post_id}")
    flash("Post removed.")
    return redirect(request.referrer or url_for("admin_panel"))

@app.route("/admin/resolve-report/<int:report_id>")
@admin_required
def admin_resolve_report(report_id):
    u = current_user()
    db = get_db()
    db.execute("UPDATE reports SET status='resolved' WHERE id=?", (report_id,))
    db.commit()
    log_action(u["username"], "resolve_report", f"report={report_id}")
    flash("Report resolved.")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete-reported/<int:report_id>")
@admin_required
def admin_delete_reported(report_id):
    u = current_user()
    db = get_db()
    report = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not report:
        flash("Report not found.")
        return redirect(url_for("admin_panel"))
    if report["content_type"] == "post":
        db.execute("DELETE FROM posts WHERE id=?", (report["content_id"],))
        db.execute("DELETE FROM comments WHERE post_id=?", (report["content_id"],))
        db.execute("DELETE FROM likes WHERE post_id=?", (report["content_id"],))
    elif report["content_type"] == "comment":
        db.execute("DELETE FROM comments WHERE id=?", (report["content_id"],))
    elif report["content_type"] == "user":
        db.execute("UPDATE users SET is_banned=1 WHERE username=?", (str(report["content_id"]).lower(),))
    db.execute("UPDATE reports SET status='resolved' WHERE id=?", (report_id,))
    db.commit()
    log_action(u["username"], "delete_reported", f"{report['content_type']}:{report['content_id']}")
    flash("Reported target handled.")
    return redirect(url_for("admin_panel"))

@app.route("/explore")
@login_required
def explore():
    u = current_user()
    db = get_db()
    top_posts = db.execute("""
        SELECT p.id, p.author, p.content, p.created_at, us.profile_pic,
        (SELECT COUNT(*) FROM likes WHERE post_id=p.id) AS likes_count
        FROM posts p JOIN users us ON us.username=p.author
        ORDER BY likes_count DESC, p.created_at DESC
        LIMIT 18
    """).fetchall()
    suggested = db.execute("""
      SELECT username, profile_pic, status
      FROM users
      WHERE username != ?
      ORDER BY last_active DESC
      LIMIT 18
    """, (u["username"],)).fetchall()
    body = "<div class='section-title'><h2 style='margin:0'>Explore</h2><span class='badge-soft'><i class='fa-solid fa-fire'></i> Trending</span></div><div class='grid two'>"
    for p in top_posts:
        body += f"<div class='card'><div class='row'><img class='avatar' src='/static/uploads/{p['profile_pic']}' onerror=\"this.src='{DEFAULT_AVATAR}'\"><div><strong>@{escape(p['author'])}</strong><div class='muted' style='font-size:12px'>{p['likes_count']} likes</div></div></div><div style='margin-top:10px'>{escape((p['content'] or '')[:120])}</div><div style='margin-top:12px'><a class='btn ghost small' href='/post/{p['id']}'>Open</a></div></div>"
    body += "</div><div class='section-title' style='margin-top:20px'><h2 style='margin:0'>People</h2></div><div class='grid two'>"
    for s in suggested:
        body += f"<div class='card'><div class='row'><img class='avatar' src='/static/uploads/{s['profile_pic']}' onerror=\"this.src='{DEFAULT_AVATAR}'\"><div><strong>@{escape(s['username'])}</strong><div class='muted' style='font-size:12px'>{escape((s['status'] or '')[:60])}</div></div></div><div style='margin-top:12px'><a class='btn ghost small' href='/profile/{s['username']}'>Profile</a></div></div>"
    body += "</div>"
    return render_page(body, "Explore")

@app.route("/downloader")
@login_required
def downloader():
    u = current_user()
    db = get_db()
    rows = db.execute("""
      SELECT media, media_type, sender as source, created_at FROM messages
      WHERE (sender=? OR receiver=?) AND media IS NOT NULL AND is_deleted=0
      UNION
      SELECT media, media_type, author as source, created_at FROM posts WHERE author=? AND media IS NOT NULL
      ORDER BY created_at DESC
      LIMIT 100
    """, (u["username"], u["username"], u["username"])).fetchall()
    body = "<div class='card'><h2 style='margin-top:0'>Downloader Hub</h2><div class='muted'>Your shared files</div></div>"
    for r in rows:
        body += f"<div class='card'><div class='row' style='justify-content:space-between'><div><strong>{escape(r['media'] or '')}</strong><div class='muted'>from @{escape(r['source'])}</div></div><a class='btn primary small' href='/static/uploads/{r['media']}' download>Download</a></div></div>"
    return render_page(body, "Downloader")

@app.route("/wipe")
@login_required
def wipe():
    u = current_user()
    db = get_db()
    db.execute("DELETE FROM users WHERE username=?", (u["username"],))
    db.execute("DELETE FROM follows WHERE follower=? OR followed=?", (u["username"], u["username"]))
    db.execute("DELETE FROM posts WHERE author=?", (u["username"],))
    db.execute("DELETE FROM comments WHERE user=?", (u["username"],))
    db.execute("DELETE FROM likes WHERE user=?", (u["username"],))
    db.execute("DELETE FROM stories WHERE username=?", (u["username"],))
    db.execute("DELETE FROM messages WHERE sender=? OR receiver=?", (u["username"], u["username"]))
    db.commit()
    log_action(u["username"], "wipe_account", "account deleted")
    session.clear()
    return redirect(url_for("signup"))

if __name__ == "__main__":
    logger.info("Nexus Titan Pro booting...")
    app.run(host="0.0.0.0", port=5000, debug=True)
