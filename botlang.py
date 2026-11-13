"""Barcha botlar uchun til tanlash (konstruktordagidek: O'zbekcha, Ўзбекча, Русский, English).

FAQAT Pro va undan yuqori tariflarda ishlaydi: konstruktor shunday botga PLAN_LANG=1 beradi.
Boshqa tariflarda install() hech narsa qilmaydi — bot avvalgidek o'zbekcha (lotin) ishlaydi.

Botlar kodiga tegilmaydi: plan_limits.install(dp) shu modulni o'zi ulaydi. Nima qiladi:
  1. Yangi foydalanuvchi /start bosganda til tanlash tugmalari chiqadi, tanlangach /start davom etadi.
     Istalgan vaqtda /lang — tilni o'zgartirish. Til foydalanuvchi bo'yicha lang.db da saqlanadi.
  2. Foydalanuvchiga ketayotgan matn (xabar, tugmalar, callback javobi) tanlangan tilga o'giriladi:
       • Ўзбекча — botning o'zbekcha matnlari lotindan avtomatik o'giriladi (kodni o'qib topiladi);
       • Русский / English — bots/botlang_tr.py lug'atidan; lug'atda yo'q matn o'zbekcha qoladi.
     Admin yozgan matn, qo'shiq/kino nomlari kabi dinamik qismlar va media izohlari O'GIRILMAYDI.
  3. Foydalanuvchi o'zgartirilgan tildagi tugmani bossa (reply-tugma), bot uni asl matn sifatida oladi —
     handlerlar o'zgarishsiz ishlayveradi.
Boshqa botlar tilni `botlang.lang_of(uid)` bilan bilib oladi (yoqilmagan bo'lsa har doim «uz»)."""
import ast
import contextvars
import logging
import os
import re
import sqlite3
import sys
import time
from functools import lru_cache
from pathlib import Path

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup, Update)

log = logging.getLogger("botlang")

LANGS = {"uz": "🇺🇿 O'zbekcha", "uzc": "🇺🇿 Ўзбекча", "ru": "🇷🇺 Русский", "en": "🇺🇸 English"}
CHOOSE = ("O'zingizga qulay tilni tanlang 🇺🇿\n\nЎзингизга қулай тилни танланг 🇺🇿\n\n"
          "Выбери язык, который тебе нравится 🇷🇺\n\nChoose the language you like 🇺🇸")
PICKED = {"uz": "✅ Til tanlandi: {name}", "uzc": "✅ Тил танланди: {name}",
          "ru": "✅ Язык выбран: {name}", "en": "✅ Language selected: {name}"}
HINT = {"uz": "Menyuni yangilash uchun /start bosing.", "uzc": "Менюни янгилаш учун /start босинг.",
        "ru": "Чтобы обновить меню, нажмите /start.", "en": "Press /start to refresh the menu."}

_cur: contextvars.ContextVar = contextvars.ContextVar("botlang_cur", default=None)   # (uid, lang)


def enabled() -> bool:
    return os.getenv("PLAN_LANG") == "1" or os.getenv("BOTLANG_FORCE") == "1"


# ------------------------------------------------------------ lotin -> kirill (i18n.py bilan bir xil qoidalar)
_PROTECT = re.compile(
    r"(\{[^}]*\}|<[^>]*>|&\w+;|https?://\S+|@\w+|(?<![\w])/[A-Za-z_]+|BotFather|Telegram|Instagram|TikTok|YouTube|"
    r"Pinterest|Facebook|Likee|RuTube|Shazam|Creative Commons|MP3|HD|ID|Plus|Pro)")
_APOS = "'‘’ʻʼ`"
_MAP = dict(zip("abdefghijklmnopqrstuvxyz", "абдефгҳижклмнопқрстувхйз"))
_MAP["c"], _MAP["w"] = "с", "в"


def _translit_plain(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        ch, low = s[i], s[i].lower()
        nxt = s[i + 1] if i + 1 < n else ""
        pair = (low + nxt.lower()) if nxt else low
        up = ch.isupper()
        if low in "og" and nxt and nxt in _APOS:
            res, step = ("ў" if low == "o" else "ғ"), 2
        elif pair in ("sh", "ch"):
            res, step = ("ш" if pair == "sh" else "ч"), 2
        elif pair in ("yo", "yu", "ya", "ye"):
            res, step = {"yo": "ё", "yu": "ю", "ya": "я", "ye": "е"}[pair], 2
        elif ch in _APOS:
            res, step = "ъ", 1
        elif low == "e" and (i == 0 or not s[i - 1].isalpha()):
            res, step = "э", 1
        elif low in _MAP:
            res, step = _MAP[low], 1
        else:
            out.append(ch)
            i += 1
            continue
        out.append(res.upper() if up else res)
        i += step
    return "".join(out)


def translit(text: str) -> str:
    return "".join(p if _PROTECT.fullmatch(p) else _translit_plain(p) for p in _PROTECT.split(text))


# ------------------------------------------------------------ matn bo'laklari (botning o'z kodidan)
_SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|PRAGMA|ALTER|DROP)\b", re.I)
_IDENT = re.compile(r"^[\w\-./:%=?&{}]+$")


def _looks_like_text(s: str) -> bool:
    if not (2 <= len(s) <= 1500) or not any(c.isalpha() for c in s) or _SQL.match(s):
        return False
    if _IDENT.match(s) and s.isascii():          # callback_data, kalit, fayl nomi va h.k.
        return False
    return True


@lru_cache(maxsize=1)
def _fragments() -> frozenset:
    """Ishlayotgan bot va u import qilgan yonma-yon modullardagi barcha matn bo'laklari."""
    files = set()
    main = sys.modules.get("__main__")
    mpath = getattr(main, "__file__", None)
    if mpath:
        files.add(Path(mpath).resolve())
        folder = Path(mpath).resolve().parent
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None)
            if f and name not in ("botlang", "botlang_tr", "plan_limits") and Path(f).resolve().parent == folder:
                files.add(Path(f).resolve())
    out = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _looks_like_text(node.value):
                out.add(node.value)
    return frozenset(out)


class Lang:
    """Bitta til uchun o'girish jadvali."""

    def __init__(self, code: str):
        from botlang_tr import DICT
        self.code = code
        self.table = {}                                   # asl matn -> o'girilgani
        if code == "uzc":
            for s in _fragments() | set(DICT):
                self.table[s] = translit(s)
        else:
            idx = 0 if code == "ru" else 1
            for s, v in DICT.items():
                self.table[s] = v[idx]
        self.table = {k: v for k, v in self.table.items() if k != v}
        long_keys = sorted((k for k in self.table if len(k) >= 8), key=len, reverse=True)
        self.rx = re.compile("|".join(re.escape(k) for k in long_keys)) if long_keys else None
        # teskari jadval: tugma bosilganda o'girilgan matn -> asl matn (noaniq bo'lsa tashlab ketiladi)
        rev, bad = {}, set()
        for k, v in self.table.items():
            if 2 <= len(v) <= 64 and v:
                if v in rev and rev[v] != k:
                    bad.add(v)
                rev[v] = k
        self.rev = {v: k for v, k in rev.items() if v not in bad}

    def tr(self, text: str) -> str:
        if not text:
            return text
        hit = self.table.get(text)
        if hit is not None:
            return hit
        if self.rx:
            return self.rx.sub(lambda m: self.table[m.group(0)], text)
        return text


_langs: dict = {}


def _lang_obj(code: str) -> Lang:
    if code not in _langs:
        _langs[code] = Lang(code)
    return _langs[code]


# ------------------------------------------------------------ foydalanuvchi tili (SQLite)
class Store:
    def __init__(self, path="lang.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS langs(uid INTEGER PRIMARY KEY, lang TEXT)")
        self.db.commit()
        self.cache = {r[0]: r[1] for r in self.db.execute("SELECT uid, lang FROM langs")}

    def get(self, uid):
        return self.cache.get(uid)

    def set(self, uid, lang):
        self.cache[uid] = lang
        self.db.execute("INSERT INTO langs(uid, lang) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET lang=excluded.lang", (uid, lang))
        self.db.commit()


_store: Store | None = None


def lang_of(uid) -> str:
    """Foydalanuvchi tili: uz | uzc | ru | en. Funksiya yoqilmagan yoki til tanlanmagan bo'lsa — «uz»."""
    if not enabled():
        return "uz"
    global _store
    if _store is None:
        _store = Store()
    return _store.get(uid) or "uz"


def user_lang() -> str:
    """Hozirgi update egasining tili (handler ichida)."""
    c = _cur.get()
    return c[1] if c else "uz"


# ------------------------------------------------------------ chiquvchi so'rovlarni o'girish
def _kb(markup, lang: Lang):
    try:
        if isinstance(markup, InlineKeyboardMarkup):
            rows = [[b.model_copy(update={"text": lang.tr(b.text)}) for b in row] for row in markup.inline_keyboard]
            return markup.model_copy(update={"inline_keyboard": rows})
        if isinstance(markup, ReplyKeyboardMarkup):
            rows = [[(b.model_copy(update={"text": lang.tr(b.text)}) if isinstance(b, KeyboardButton) else lang.tr(b))
                     for b in row] for row in markup.keyboard]
            return markup.model_copy(update={"keyboard": rows})
    except Exception:
        log.exception("keyboard translate")
    return markup


class LangRequestMw(BaseRequestMiddleware):
    """Joriy foydalanuvchining o'ziga ketayotgan matn va tugmalarni o'giradi.
    Boshqalarga ketayotgan xabarlar (ommaviy yuborish) va media izohlari tegilmaydi."""

    async def __call__(self, make_request, bot, method):
        cur = _cur.get()
        if cur and cur[1] != "uz":
            uid, code = cur
            chat = getattr(method, "chat_id", None)
            if chat is None or chat == uid:
                try:
                    lang = _lang_obj(code)
                    upd = {}
                    name = type(method).__name__
                    if name in ("SendMessage", "EditMessageText", "AnswerCallbackQuery") and getattr(method, "text", None):
                        upd["text"] = lang.tr(method.text)
                    rm = getattr(method, "reply_markup", None)
                    if rm is not None:
                        new = _kb(rm, lang)
                        if new is not rm:
                            upd["reply_markup"] = new
                    if upd:
                        method = method.model_copy(update=upd)
                except Exception:
                    log.exception("translate")
        return await make_request(bot, method)


# ------------------------------------------------------------ kiruvchi update'lar: til tanlash, /lang, teskari o'girish
_LANG_CMD = re.compile(r"^/lang(@\w+)?\s*$", re.I)
_START_CMD = re.compile(r"^/start(@\w+)?(\s|$)", re.I)


class LangMw(BaseMiddleware):
    def __init__(self, store: Store, admins: set):
        self.store, self.admins = store, admins
        self.hooked = set()
        self.pending = {}                                 # uid -> til tanlanishini kutayotgan /start xabari

    def _picker(self):
        kb = [[InlineKeyboardButton(text=LANGS["uz"], callback_data="bl:uz"),
               InlineKeyboardButton(text=LANGS["uzc"], callback_data="bl:uzc")],
              [InlineKeyboardButton(text=LANGS["ru"], callback_data="bl:ru"),
               InlineKeyboardButton(text=LANGS["en"], callback_data="bl:en")]]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    async def __call__(self, handler, event: Update, data):
        bot, user = data.get("bot"), data.get("event_from_user")
        if bot is not None and id(bot) not in self.hooked:
            self.hooked.add(id(bot))
            bot.session.middleware(LangRequestMw())
        if not user or getattr(user, "is_bot", False):
            return await handler(event, data)
        msg, cb = event.message, event.callback_query

        if cb is not None and (cb.data or "").startswith("bl:"):
            return await self._picked(getattr(self, "_dp", None), bot, cb)

        if msg is not None and msg.chat.type == "private" and msg.text:
            if _LANG_CMD.match(msg.text):
                await msg.answer(CHOOSE, reply_markup=self._picker())
                return
            if _START_CMD.match(msg.text) and user.id not in self.admins and self.store.get(user.id) is None:
                self.pending[user.id] = msg
                await msg.answer(CHOOSE, reply_markup=self._picker())
                return
            code = self.store.get(user.id)
            if code and code != "uz":                      # tarjima qilingan reply-tugma -> asl matn
                orig = _lang_obj(code).rev.get(msg.text.strip())
                if orig:
                    event = event.model_copy(update={"message": msg.model_copy(update={"text": orig})})

        code = self.store.get(user.id) or "uz"
        token = _cur.set((user.id, code))
        try:
            return await handler(event, data)
        finally:
            _cur.reset(token)

    async def _picked(self, dp, bot, cb):
        code = cb.data[3:]
        if code not in LANGS:
            return await cb.answer()
        uid = cb.from_user.id
        self.store.set(uid, code)
        text = PICKED[code].format(name=LANGS[code])
        pend = self.pending.pop(uid, None)
        try:
            await cb.message.edit_text(text if pend else text + "\n" + HINT[code])
        except Exception:
            pass
        try:
            await cb.answer()
        except Exception:
            pass
        if pend is not None and dp is not None:
            upd = Update(update_id=int(time.time() * 1000) % 2_000_000_000, message=pend).as_(bot)
            try:
                await dp.feed_update(bot, upd)
            except Exception:
                log.exception("feed /start")


def install(dp):
    """Dispatcher'ga til tanlashni ulaydi (faqat PLAN_LANG=1 bo'lsa)."""
    if not enabled():
        return None
    global _store
    if _store is None:
        _store = Store()
    admins = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
    mw = LangMw(_store, admins)
    mw._dp = dp
    dp.update.outer_middleware(mw)
    return mw
