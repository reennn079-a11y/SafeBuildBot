"""Super yuklovchi bot uchun yengil web admin panel (aiohttp). Faqat WEB_PORT va WEB_KEY berilganda ishga tushadi.
Kirish: http://SERVER:PORT/?key=WEB_KEY  (botda /web buyrug'i havolani beradi)."""
import hmac
import html
import time

from aiohttp import web

CSS = ("<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:16px auto;padding:0 12px;background:#f6f7f9;color:#111}"
       "h1{font-size:20px}.c{background:#fff;border-radius:10px;padding:12px 14px;margin:10px 0;box-shadow:0 1px 3px #0001}"
       "table{width:100%;border-collapse:collapse}td,th{padding:6px 4px;border-bottom:1px solid #eee;text-align:left;font-size:14px}"
       "input,button{padding:8px 10px;margin:3px 0;font-size:15px}button{background:#2a7de1;color:#fff;border:0;border-radius:6px}"
       "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}.c{background:#1c1c1e}td,th{border-color:#333}}</style>")


async def start(db, port: int, key: str, grant_plus, get_setting, set_setting):
    def ok(req):
        return hmac.compare_digest(req.query.get("key", ""), key)

    def q1(sql, *a):
        return db.execute(sql, a).fetchone()[0]

    async def index(req):
        if not ok(req):
            return web.Response(status=403, text="403")
        day = time.strftime("%Y-%m-%d")
        stats = [("👥 Foydalanuvchilar", q1("SELECT COUNT(*) FROM users")),
                 ("⭐ Plus", q1("SELECT COUNT(*) FROM users WHERE plus_until>?", int(time.time()))),
                 ("📥 Bugun yuklaganlar", q1("SELECT COUNT(*) FROM usage WHERE day=?", day)),
                 ("📥 Bugungi yuklashlar", q1("SELECT COALESCE(SUM(n),0) FROM usage WHERE day=?", day)),
                 ("🔵 Keshdagi fayllar", q1("SELECT COUNT(*) FROM cache")),
                 ("💳 Stars tushumi", q1("SELECT COALESCE(SUM(stars),0) FROM payments"))]
        rows = "".join(f"<tr><td>{a}</td><td><b>{b}</b></td></tr>" for a, b in stats)
        ads = "".join(
            f"<tr><td>#{i}</td><td>{'🟢' if a else '⚪️'}</td><td>👁 {v}</td><td>{html.escape(bt or '—')}</td>"
            f"<td><form method=post action='/ad?key={html.escape(key)}'><input type=hidden name=id value={i}><button>yoq/o'ch</button></form></td></tr>"
            for i, a, v, bt in db.execute("SELECT id, active, views, btn_text FROM ads ORDER BY id"))
        pay = "".join(f"<tr><td>{u}</td><td>{s} ⭐</td><td>{d} kun</td><td>{time.strftime('%d.%m %H:%M', time.localtime(t))}</td></tr>"
                      for u, s, d, t in db.execute("SELECT uid, stars, days, at FROM payments ORDER BY id DESC LIMIT 15"))
        k = html.escape(key)
        page = (f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Admin</title>{CSS}"
                f"<h1>📱 Web admin panel</h1><div class=c><table>{rows}</table></div>"
                f"<div class=c><b>⚙️ Sozlamalar</b><form method=post action='/set?key={k}'>"
                f"<div>Bepul kunlik limit (0 = cheksiz)<br><input name=free_daily value='{html.escape(get_setting('free_daily', '10'))}'></div>"
                f"<div>Reklama har N-yuklashda (0 = yo'q)<br><input name=ad_every value='{html.escape(get_setting('ad_every', '3'))}'></div>"
                f"<button>Saqlash</button></form></div>"
                f"<div class=c><b>🎁 Plus berish</b><form method=post action='/grant?key={k}'>"
                f"<input name=uid placeholder='Foydalanuvchi ID'> <input name=days placeholder='Kun' size=5><button>Berish</button></form></div>"
                f"<div class=c><b>📺 Reklamalar</b><table>{ads or '<tr><td>yoq</td></tr>'}</table></div>"
                f"<div class=c><b>💳 So'nggi to'lovlar</b><table>{pay or '<tr><td>yoq</td></tr>'}</table></div>")
        return web.Response(text=page, content_type="text/html")

    async def post_set(req):
        if not ok(req):
            return web.Response(status=403, text="403")
        f = await req.post()
        for name in ("free_daily", "ad_every"):
            v = str(f.get(name, "")).strip()
            if v.isdigit():
                set_setting(name, v)
        raise web.HTTPFound(f"/?key={key}")

    async def post_grant(req):
        if not ok(req):
            return web.Response(status=403, text="403")
        f = await req.post()
        u, d = str(f.get("uid", "")).strip(), str(f.get("days", "")).strip()
        if u.isdigit() and d.isdigit() and 0 < int(d) <= 3650:
            grant_plus(int(u), int(d))
        raise web.HTTPFound(f"/?key={key}")

    async def post_ad(req):
        if not ok(req):
            return web.Response(status=403, text="403")
        f = await req.post()
        if str(f.get("id", "")).isdigit():
            db.execute("UPDATE ads SET active=1-active WHERE id=?", (int(f["id"]),))
            db.commit()
        raise web.HTTPFound(f"/?key={key}")

    app = web.Application()
    app.add_routes([web.get("/", index), web.post("/set", post_set), web.post("/grant", post_grant), web.post("/ad", post_ad)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
