"""Tarif limitlari — har bir yaratilgan bot ichida ishlaydi (konstruktor muhit orqali beradi):
  PLAN_DAILY   — kunlik aktiv foydalanuvchilar (har xil odam soni)
  PLAN_MONTHLY — oylik so'rovlar (xabar/tugma bosish) limiti
  PLAN_RATE    — xabarnoma tezligi: boshqa foydalanuvchilarga yuboriladigan xabarlar soni / daqiqa
Bot kodida faqat `import plan_limits; plan_limits.install(dp)` chaqiriladi. Admin (egasi) limitga kirmaydi.
Hisob joriy papkadagi limits.db da saqlanadi."""
import asyncio, contextvars, logging, os, sqlite3, time

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware

log = logging.getLogger("plan_limits")
cur_uid: contextvars.ContextVar = contextvars.ContextVar("cur_uid", default=None)

MSG = ("⚠️ Bot hozircha band, birozdan so'ng qayta urinib ko'ring.\n"
       "⚠️ Бот временно перегружен, попробуйте позже.\n"
       "⚠️ The bot is busy right now, please try again later.")


def _int(name):
    try:
        v = int(os.getenv(name, "0"))
        return v if v > 0 else None
    except ValueError:
        return None


class Store:
    """Kunlik aktivlar va oylik so'rovlar hisobi (SQLite)."""

    def __init__(self, path="limits.db", daily=None, monthly=None):
        self.daily, self.monthly = daily, monthly
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("CREATE TABLE IF NOT EXISTS daily(day TEXT, uid INTEGER, PRIMARY KEY(day, uid));"
                              "CREATE TABLE IF NOT EXISTS monthly(month TEXT PRIMARY KEY, n INTEGER DEFAULT 0);")
        self.db.commit()
        self._day = None
        self._today = set()
        self._n = 0
        self._month = None

    def _roll(self):
        day, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
        if day != self._day:
            self._day = day
            self._today = {r[0] for r in self.db.execute("SELECT uid FROM daily WHERE day=?", (day,))}
            self.db.execute("DELETE FROM daily WHERE day<?", (time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400)),))
        if month != self._month:
            self._month = month
            r = self.db.execute("SELECT n FROM monthly WHERE month=?", (month,)).fetchone()
            self._n = r[0] if r else 0

    def hit(self, uid):
        """(True, None) — o'tkazish mumkin; (False, 'day'|'month') — limit tugagan."""
        self._roll()
        if self.monthly and self._n >= self.monthly:
            return False, "month"
        if uid not in self._today:
            if self.daily and len(self._today) >= self.daily:
                return False, "day"
            self._today.add(uid)
            self.db.execute("INSERT OR IGNORE INTO daily(day, uid) VALUES(?,?)", (self._day, uid))
        self._n += 1
        self.db.execute("INSERT INTO monthly(month, n) VALUES(?,1) ON CONFLICT(month) DO UPDATE SET n=n+1", (self._month,))
        self.db.commit()
        return True, None


class Bucket:
    """Token bucket: boshida kichik «portlash» ruxsat, keyin daqiqada `per_min` ta xabar."""

    def __init__(self, per_min):
        self.rate = per_min / 60.0
        self.cap = max(20.0, per_min / 5.0)
        self.tokens = self.cap
        self.t = time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class RateMw(BaseRequestMiddleware):
    """Boshqa foydalanuvchilarga ketayotgan xabarlarni (ommaviy yuborish) tarif tezligiga tushiradi.
    Joriy foydalanuvchiga javoblar (chat_id == update egasi) kechiktirilmaydi."""

    def __init__(self, bucket):
        self.bucket = bucket

    async def __call__(self, make_request, bot, method):
        name = type(method).__name__
        if name.startswith(("Send", "Copy", "Forward")):
            chat = getattr(method, "chat_id", None)
            if chat is not None and chat != cur_uid.get():
                await self.bucket.take()
        return await make_request(bot, method)


class LimitMw(BaseMiddleware):
    def __init__(self, store, bucket, admins):
        self.store, self.bucket, self.admins = store, bucket, admins
        self.session_hooked = set()
        self.warned = {}            # (uid) -> oxirgi ogohlantirish vaqti
        self.owner_told = set()     # (kun, sabab)

    async def __call__(self, handler, event, data):
        bot = data.get("bot")
        if bot is not None and self.bucket and id(bot) not in self.session_hooked:
            self.session_hooked.add(id(bot))
            bot.session.middleware(RateMw(self.bucket))
        user = data.get("event_from_user")
        if not user or user.id in self.admins or getattr(user, "is_bot", False):
            return await handler(event, data)
        msg = getattr(event, "message", None)
        cb = getattr(event, "callback_query", None)
        inline = getattr(event, "inline_query", None)
        if not (msg or cb or inline):
            return await handler(event, data)
        if msg is not None and msg.chat.type != "private":      # guruh xabarlari hisobga olinmaydi
            return await handler(event, data)
        token = cur_uid.set(user.id)
        try:
            ok, why = self.store.hit(user.id)
            if ok:
                return await handler(event, data)
            await self._block(bot, user.id, cb, why)
        finally:
            cur_uid.reset(token)

    async def _block(self, bot, uid, cb, why):
        now = time.time()
        try:
            if cb is not None:
                await bot.answer_callback_query(cb.id, MSG.split("\n")[0], show_alert=True)
            elif now - self.warned.get(uid, 0) > 3600:
                self.warned[uid] = now
                await bot.send_message(uid, MSG)
            key = (time.strftime("%Y-%m-%d"), why)
            if key not in self.owner_told:                      # egasiga kuniga bir marta
                self.owner_told.add(key)
                txt = ("⚠️ Tarif limiti tugadi: " + ("kunlik aktivlar" if why == "day" else "oylik so'rovlar") +
                       ". Yangi foydalanuvchilar/so'rovlar vaqtincha bloklandi. Konstruktorda «🤖 Botlarim» → tarifni oshiring.")
                for a in self.admins:
                    try: await bot.send_message(a, txt)
                    except Exception: pass
        except Exception:
            log.exception("limit block")


def install(dp):
    """Dispatcher'ga tarif limitini ulaydi. Limit muhitda berilmagan bo'lsa — hech narsa qilmaydi."""
    try:
        import botlang
        botlang.install(dp)             # til tanlash: faqat Pro va undan yuqori tariflarda (PLAN_LANG=1)
    except Exception:
        log.exception("botlang")
    daily, monthly, rate = _int("PLAN_DAILY"), _int("PLAN_MONTHLY"), _int("PLAN_RATE")
    if not (daily or monthly or rate):
        return
    admins = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
    mw = LimitMw(Store(daily=daily, monthly=monthly), Bucket(rate) if rate else None, admins)
    dp.update.outer_middleware(mw)
