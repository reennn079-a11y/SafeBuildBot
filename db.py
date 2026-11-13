import os, sqlite3, time
from pathlib import Path
# Renderda doimiy disk (masalan /var/data) DATA_DIR orqali beriladi; bo'lmasa joriy papka
_base = Path(os.getenv("DATA_DIR") or ".")
_base.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(str(_base / "builder.db"), check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, balance INTEGER DEFAULT 0,
    ref_by INTEGER, earned INTEGER DEFAULT 0, ref_paid INTEGER DEFAULT 0, joined INTEGER, lang TEXT);
CREATE TABLE IF NOT EXISTS bots(id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER, type TEXT, token TEXT UNIQUE,
    username TEXT, running INTEGER DEFAULT 1, created INTEGER, expires INTEGER DEFAULT 0, warned INTEGER DEFAULT 0, plan TEXT DEFAULT 'start');
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount INTEGER,
    file_id TEXT, status TEXT DEFAULT 'new', created INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS transfers(id INTEGER PRIMARY KEY AUTOINCREMENT, src INTEGER, dst INTEGER, amount INTEGER, created INTEGER);
CREATE TABLE IF NOT EXISTS bot_types(key TEXT PRIMARY KEY, title TEXT, file TEXT, price INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, bonus INTEGER, limit_n INTEGER, used INTEGER DEFAULT 0, created INTEGER);
CREATE TABLE IF NOT EXISTS promo_uses(code TEXT, uid INTEGER, ts INTEGER, PRIMARY KEY(code, uid));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT UNIQUE, title TEXT, link TEXT);
CREATE TABLE IF NOT EXISTS ch_greeted(uid INTEGER, chat TEXT, ts INTEGER, PRIMARY KEY(uid, chat));
CREATE TABLE IF NOT EXISTS overrides(lang TEXT, key TEXT, value TEXT, PRIMARY KEY(lang, key));
CREATE TABLE IF NOT EXISTS menus(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, kind TEXT DEFAULT 'text',
    body TEXT, src_chat INTEGER, src_msg INTEGER, pos INTEGER DEFAULT 0);
""")
# eski bazalar uchun migratsiya: sinov muddati ustunlari
_cols = {r["name"] for r in db.execute("PRAGMA table_info(bots)")}
if "expires" not in _cols:
    db.execute("ALTER TABLE bots ADD COLUMN expires INTEGER DEFAULT 0")
if "warned" not in _cols:
    db.execute("ALTER TABLE bots ADD COLUMN warned INTEGER DEFAULT 0")
if "plan" not in _cols:
    db.execute("ALTER TABLE bots ADD COLUMN plan TEXT DEFAULT 'start'")
if "lang" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
    db.execute("ALTER TABLE users ADD COLUMN lang TEXT")
if "blocked" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
    db.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
    db.commit()
db.commit()


def q(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur.fetchall()


def ins(sql, *a):
    """INSERT qiladi va yangi qator ID'sini qaytaradi (MAX(id) poygasiz)."""
    cur = db.execute(sql, a)
    db.commit()
    return cur.lastrowid


def one(sql, *a):
    r = q(sql, *a)
    return r[0] if r else None


def add_user(uid, name, ref=None):
    if one("SELECT 1 FROM users WHERE id=?", uid):
        return False
    ref = ref if ref and ref != uid and one("SELECT 1 FROM users WHERE id=?", ref) else None
    q("INSERT INTO users(id,name,ref_by,joined) VALUES(?,?,?,?)", uid, name, ref, int(time.time()))
    return True


def get_setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", key)
    return r["value"] if r else default


def set_setting(key, value):
    q("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, str(value))


def run(sql, *a):
    """O'zgartiruvchi so'rov; ta'sirlangan qatorlar sonini qaytaradi."""
    cur = db.execute(sql, a)
    db.commit()
    return cur.rowcount


def transfer(src, dst, amt):
    """Pulni bitta tranzaksiyada o'tkazadi: yechish va qo'shish birga bajariladi yoki umuman bajarilmaydi."""
    try:
        cur = db.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (amt, src, amt))
        if cur.rowcount == 0:
            db.rollback()
            return False
        db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt, dst))
        db.execute("INSERT INTO transfers(src,dst,amount,created) VALUES(?,?,?,?)", (src, dst, amt, int(time.time())))
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def redeem_promo(code, uid):
    """Promokodni faollashtiradi. ("ok", bonus) yoki ("none"|"end"|"used", 0). Hammasi bitta tranzaksiyada."""
    code = (code or "").strip().upper()
    try:
        r = db.execute("SELECT * FROM promos WHERE code=?", (code,)).fetchone()
        if not r:
            return "none", 0
        if db.execute("SELECT 1 FROM promo_uses WHERE code=? AND uid=?", (code, uid)).fetchone():
            return "used", 0
        cur = db.execute("UPDATE promos SET used=used+1 WHERE code=? AND used<limit_n", (code,))
        if cur.rowcount == 0:
            db.rollback()
            return "end", 0
        db.execute("INSERT INTO promo_uses(code,uid,ts) VALUES(?,?,?)", (code, uid, int(time.time())))
        db.execute("UPDATE users SET balance=balance+? WHERE id=?", (r["bonus"], uid))
        db.commit()
        return "ok", r["bonus"]
    except Exception:
        db.rollback()
        raise
