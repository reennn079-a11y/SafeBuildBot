# -*- coding: utf-8 -*-
# Chiroyli shriftlar boti (aiogram 3) + Fontlar katalogi + Admin panel. Bitta fayl.
# O'rnatish: pip install aiogram
import asyncio
import math
import os
import sqlite3
import time
from html import escape as esc
from urllib.parse import quote_plus

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)

# ======================= SOZLAMALAR =======================
TOKEN = os.getenv("BOT_TOKEN") or "BOT_TOKEN_SHU_YERGA"      # konstruktor tokenni o'zi beradi
SUPER_ADMINS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()] or [123456789]
DB_PATH = "bot.db"
PER_PAGE = 10

LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = LOWER.upper()
DIGITS = "0123456789"


# ======================= USLUBLAR =======================
def build(upper_start=None, lower_start=None, digit_start=None, holes=None):
    m = {}
    if upper_start:
        for i, ch in enumerate(UPPER):
            m[ch] = chr(upper_start + i)
    if lower_start:
        for i, ch in enumerate(LOWER):
            m[ch] = chr(lower_start + i)
    if digit_start:
        for i, ch in enumerate(DIGITS):
            m[ch] = chr(digit_start + i)
    if holes:
        m.update(holes)
    return m


def from_strings(upper, lower, digits=None):
    m = dict(zip(UPPER, upper))
    m.update(zip(LOWER, lower))
    if digits:
        m.update(zip(DIGITS, digits))
    return m


def combining(mark):
    return lambda t: "".join(c + mark if c != " " else c for c in t)


MAPS = {}
MAPS["𝐁𝐨𝐥𝐝"] = build(0x1D400, 0x1D41A, 0x1D7CE)
MAPS["𝐼𝑡𝑎𝑙𝑖𝑐"] = build(0x1D434, 0x1D44E, holes={"h": "\u210E"})
MAPS["𝑩𝒐𝒍𝒅 𝑰𝒕𝒂𝒍𝒊𝒄"] = build(0x1D468, 0x1D482, 0x1D7CE)
MAPS["𝒮𝒸𝓇𝒾𝓅𝓉"] = build(
    0x1D49C, 0x1D4B6,
    holes={"B": "\u212C", "E": "\u2130", "F": "\u2131", "H": "\u210B",
           "I": "\u2110", "L": "\u2112", "M": "\u2133", "R": "\u211B",
           "e": "\u212F", "g": "\u210A", "o": "\u2134"},
)
MAPS["𝓑𝓸𝓵𝓭 𝓢𝓬𝓻𝓲𝓹𝓽"] = build(0x1D4D0, 0x1D4EA)
MAPS["𝔉𝔯𝔞𝔨𝔱𝔲𝔯"] = build(
    0x1D504, 0x1D51E,
    holes={"C": "\u212D", "H": "\u210C", "I": "\u2111", "R": "\u211C", "Z": "\u2128"},
)
MAPS["𝕭𝖔𝖑𝖉 𝕱𝖗𝖆𝖐𝖙𝖚𝖗"] = build(0x1D56C, 0x1D586)
MAPS["𝔻𝕠𝕦𝕓𝕝𝕖"] = build(
    0x1D538, 0x1D552, 0x1D7D8,
    holes={"C": "\u2102", "H": "\u210D", "N": "\u2115", "P": "\u2119",
           "Q": "\u211A", "R": "\u211D", "Z": "\u2124"},
)
MAPS["𝖲𝖺𝗇𝗌"] = build(0x1D5A0, 0x1D5BA, 0x1D7E2)
MAPS["𝗦𝗮𝗻𝘀 𝗕𝗼𝗹𝗱"] = build(0x1D5D4, 0x1D5EE, 0x1D7EC)
MAPS["𝘚𝘢𝘯𝘴 𝘐𝘵𝘢𝘭𝘪𝘤"] = build(0x1D608, 0x1D622)
MAPS["𝙎𝙖𝙣𝙨 𝘽𝙤𝙡𝙙 𝙄𝙩𝙖𝙡𝙞𝙘"] = build(0x1D63C, 0x1D656)
MAPS["𝙼𝚘𝚗𝚘"] = build(0x1D670, 0x1D68A, 0x1D7F6)
MAPS["Ｆｕｌｌｗｉｄｔｈ"] = build(0xFF21, 0xFF41, 0xFF10)

circled = build(0x24B6, 0x24D0)
circled.update(zip("123456789", "①②③④⑤⑥⑦⑧⑨"))
circled["0"] = "⓪"
MAPS["ⒸⒾⓇⒸⓁⒺⒹ"] = circled
MAPS["🅝🅔🅖 🅒🅘🅡🅒🅛🅔"] = build(0x1F150, 0x1F150)
MAPS["🄱🄾🅇"] = build(0x1F130, 0x1F130)
MAPS["🅱🅾🆇 🅱🅻🅰🅲🅺"] = build(0x1F170, 0x1F170)
MAPS["⒫⒜⒭⒠⒩"] = build(0x1F110, 0x249C)
MAPS["ꜱᴍᴀʟʟ ᴄᴀᴘꜱ"] = from_strings(
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
)
MAPS["ᴴⁱᵍʰ"] = from_strings(
    "ᴬᴮᶜᴰᴱᶠᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾQᴿˢᵀᵁⱽᵂˣʸᶻ",
    "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖqʳˢᵗᵘᵛʷˣʸᶻ",
    "⁰¹²³⁴⁵⁶⁷⁸⁹",
)

flip_map = from_strings(
    "∀ᗺƆᗡƎℲ⅁HIſꓘ˥WNOԀΌꓤS┴∩ΛMX⅄Z",
    "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz",
)
flip_map.update({"1": "Ɩ", "2": "ᄅ", "3": "Ɛ", "4": "ㄣ", "5": "ϛ", "6": "9",
                 "7": "ㄥ", "8": "8", "9": "6", "0": "0",
                 "?": "¿", "!": "¡", ".": "˙", ",": "'"})

FUNCS = {}
FUNCS["u̲n̲d̲e̲r̲l̲i̲n̲e̲"] = combining("\u0332")
FUNCS["d̳o̳u̳b̳l̳e̳ ̳u̳n̳d̳e̳r̳"] = combining("\u0333")
FUNCS["s̶t̶r̶i̶k̶e̶"] = combining("\u0336")
FUNCS["s̷l̷a̷s̷h̷"] = combining("\u0338")
FUNCS["o̅v̅e̅r̅l̅i̅n̅e̅"] = combining("\u0305")
FUNCS["ḍọṭ ̣ḅẹḷọẉ"] = combining("\u0323")
FUNCS["d̈ȯẗ̈ ̈äb̈öv̈ë"] = combining("\u0308")
FUNCS["s p a c e d"] = lambda t: " ".join(t)
FUNCS["ʇxǝʇ pǝddᴉlɟ"] = lambda t: "".join(flip_map.get(c, c) for c in translit(t)[::-1])
FUNCS["★ stars ★"] = lambda t: "★ " + t + " ★"
FUNCS["『brackets』"] = lambda t: "『" + t + "』"
FUNCS["✦ sparkle ✦"] = lambda t: "✦ " + t + " ✦"
FUNCS["꧁ fancy ꧂"] = lambda t: "꧁ " + t + " ꧂"
FUNCS["♡ heart ♡"] = lambda t: "♡ " + t + " ♡"


CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}
for _k, _v in list(CYR.items()):
    CYR[_k.upper()] = _v[:1].upper() + _v[1:]


def translit(t):
    return "".join(CYR.get(c, c) for c in t)


def make_mapper(mm):
    return lambda t: "".join(mm.get(c, c) for c in translit(t))


STYLES = [make_mapper(m) for m in MAPS.values()] + list(FUNCS.values())
TOTAL = len(STYLES)

SAMPLE = "Example / Текст"
SAMPLE_TEXT = [fn(SAMPLE) for fn in STYLES]
SAMPLE_MAP = {t: i for i, t in enumerate(SAMPLE_TEXT)}   # tugma matni -> uslub raqami

# Font tugmalari: nomi turli chiroyli yozuvlarda ko'rinadi
FONT_MAPPERS = [make_mapper(m) for m in list(MAPS.values())[:13]]
ADMIN_BTN = "⚙️ Admin panel"


def font_label(fid, name):
    return FONT_MAPPERS[fid % len(FONT_MAPPERS)](name)


# ======================= FONTLAR KATALOGI (boshlang'ich 100 ta) =======================
FONTS = {
    "Sans-serif": [
        "Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins", "Nunito",
        "Source Sans 3", "Work Sans", "Raleway", "Helvetica", "Helvetica Neue", "Arial",
        "Futura", "Gotham", "Avenir", "Proxima Nova", "DM Sans", "Manrope", "Rubik",
        "IBM Plex Sans", "Noto Sans", "Segoe UI", "SF Pro", "Barlow", "Mulish", "Karla",
        "Outfit", "Plus Jakarta Sans", "Univers",
    ],
    "Serif": [
        "Times New Roman", "Georgia", "Garamond", "EB Garamond", "Baskerville",
        "Libre Baskerville", "Caslon", "Bodoni", "Didot", "Palatino", "Merriweather",
        "Playfair Display", "Lora", "PT Serif", "Source Serif", "Crimson Text",
        "Cormorant", "Noto Serif", "Roboto Slab", "Bitter", "Cambria", "Minion",
        "Century Schoolbook", "Spectral", "DM Serif Display",
    ],
    "Sarlavha / dekorativ": [
        "Bebas Neue", "Oswald", "Anton", "Lobster", "Pacifico", "Abril Fatface",
        "Righteous", "Fredoka", "Comfortaa", "Cinzel", "Archivo Black", "Alfa Slab One",
        "Bangers", "Permanent Marker", "Press Start 2P", "Impact", "Staatliches",
        "Josefin Sans", "Quicksand", "Titillium Web",
    ],
    "Qo'lyozma / script": [
        "Dancing Script", "Great Vibes", "Satisfy", "Caveat", "Sacramento",
        "Shadows Into Light", "Amatic SC", "Kaushan Script", "Indie Flower", "Parisienne",
    ],
    "Monospace / kod uchun": [
        "Fira Code", "JetBrains Mono", "Source Code Pro", "Roboto Mono", "IBM Plex Mono",
        "Courier New", "Consolas", "Menlo", "Inconsolata", "Space Mono",
    ],
    "Tizim fontlari": ["Verdana", "Tahoma", "Trebuchet MS", "Calibri", "Comic Sans MS"],
}

CATALOG_BTN = "📚 Fontlar katalogi"
DEFAULT_WELCOME = (
    "Assalomu alaykum! Bu bot matnni chiroyli shriftlarda yozib beradi.\n"
    "Pastdan uslubni tanlang ({total} ta uslub), so'ng matn yuboring.\n"
    "📚 Fontlar katalogi ham bor."
)


# ======================= BAZA =======================
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row


def run(sql, args=()):
    db.execute(sql, args)
    db.commit()


def get(sql, args=()):
    return db.execute(sql, args).fetchone()


def rows(sql, args=()):
    return db.execute(sql, args).fetchall()


def scalar(sql, args=()):
    r = db.execute(sql, args).fetchone()
    return r[0] if r and r[0] is not None else 0


def init_db():
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, name TEXT, username TEXT,
        joined INTEGER, last_seen INTEGER,
        banned INTEGER DEFAULT 0, active INTEGER DEFAULT 1, style INTEGER);
    CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS fonts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, name TEXT,
        UNIQUE(category, name));
    CREATE TABLE IF NOT EXISTS disabled_styles(idx INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS usage(idx INTEGER PRIMARY KEY, cnt INTEGER DEFAULT 0);
    """)
    db.commit()
    # Eski bazalar uchun: yetishmayotgan ustunlarni qo'shish
    have = {r["name"] for r in db.execute("PRAGMA table_info(users)")}
    need = {"name": "TEXT", "username": "TEXT", "joined": "INTEGER",
            "last_seen": "INTEGER", "banned": "INTEGER DEFAULT 0",
            "active": "INTEGER DEFAULT 1", "style": "INTEGER"}
    for col, typ in need.items():
        if col not in have:
            db.execute("ALTER TABLE users ADD COLUMN %s %s" % (col, typ))
    db.commit()
    if scalar("SELECT COUNT(*) FROM fonts") == 0:
        for cat, names in FONTS.items():
            for n in names:
                db.execute("INSERT OR IGNORE INTO fonts(category,name) VALUES(?,?)", (cat, n))
        db.commit()


def get_setting(key, default=""):
    r = get("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    run("INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def is_admin(uid):
    return uid in SUPER_ADMINS or get("SELECT 1 FROM admins WHERE id=?", (uid,)) is not None


def touch_user(u):
    now = int(time.time())
    run("INSERT INTO users(id,name,username,joined,last_seen) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username, "
        "last_seen=excluded.last_seen, active=1",
        (u.id, (u.full_name or "")[:100], u.username, now, now))


def is_banned(uid):
    r = get("SELECT banned FROM users WHERE id=?", (uid,))
    return bool(r and r["banned"])


def disabled_set():
    return {r[0] for r in rows("SELECT idx FROM disabled_styles")}


def enabled_styles():
    dis = disabled_set()
    return [i for i in range(TOTAL) if i not in dis]


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


# ======================= YORDAMCHILAR =======================
def ikb(rws):
    out = []
    for row in rws:
        r = []
        for t, d in row:
            if d.startswith("http"):
                r.append(InlineKeyboardButton(text=t, url=d))
            else:
                r.append(InlineKeyboardButton(text=t, callback_data=d))
        out.append(r)
    return InlineKeyboardMarkup(inline_keyboard=out)


def paginate(items, page):
    pages = max(1, math.ceil(len(items) / PER_PAGE))
    page = min(max(page, 0), pages - 1)
    return items[page * PER_PAGE:(page + 1) * PER_PAGE], page, pages


def nav_row(prefix, page, pages):
    row = []
    if page > 0:
        row.append(("⬅️", "%s:%d" % (prefix, page - 1)))
    row.append(("%d/%d" % (page + 1, pages), "noop"))
    if page < pages - 1:
        row.append(("➡️", "%s:%d" % (prefix, page + 1)))
    return row


def two_cols(buttons):
    out, row = [], []
    for b in buttons:
        row.append(b)
        if len(row) == 2:
            out.append(row)
            row = []
    if row:
        out.append(row)
    return out


async def show(cb, text, kb=None, html=True):
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML" if html else None)
    except TelegramBadRequest:
        pass
    await cb.answer()


# ======================= FOYDALANUVCHI TOMONI =======================
user_router = Router()


def styles_keyboard(uid=None):
    # Pastki menyuda faqat uslublar + katalog tugmasi. Telegram bitta klaviaturada
    # 100 tadan ortiq tugmaga ruxsat bermaydi (REPLY_MARKUP_TOO_LONG), fontlar esa
    # 100 ta — shuning uchun ular "📚 Fontlar katalogi" ichida.
    btns = [KeyboardButton(text=SAMPLE_TEXT[i]) for i in enabled_styles()][:96]
    rws = two_cols(btns)
    rws.append([KeyboardButton(text=CATALOG_BTN)])
    if uid is not None and is_admin(uid):
        rws.append([KeyboardButton(text=ADMIN_BTN)])
    return ReplyKeyboardMarkup(keyboard=rws, resize_keyboard=True,
                               input_field_placeholder="Message")


def catalog_cats():
    return rows("SELECT category, COUNT(*) AS c FROM fonts GROUP BY category ORDER BY MIN(id)")


def render_cats():
    cats = catalog_cats()
    if not cats:
        return "Katalog hozircha bo'sh.", None
    total = sum(c["c"] for c in cats)
    text = "📚 Fontlar katalogi — jami %d ta font.\nKategoriyani tanlang:" % total
    kb = ikb([[("📁 %s (%d)" % (c["category"], c["c"]), "cat:%d:0" % i)]
              for i, c in enumerate(cats)])
    return text, kb


def render_cat_page(ci, page):
    cats = catalog_cats()
    if ci >= len(cats):
        return render_cats()
    cat = cats[ci]["category"]
    fonts = rows("SELECT id, name FROM fonts WHERE category=? ORDER BY id", (cat,))
    chunk, page, pages = paginate(fonts, page)
    start = page * PER_PAGE
    text = "📁 %s\n\n%s\n\nFont ustiga bosing 👇" % (
        cat, "\n".join("%d. %s" % (start + i + 1, r["name"]) for i, r in enumerate(chunk)))
    btns = [(r["name"], "font:%d:%d:%d" % (r["id"], ci, page)) for r in chunk]
    rws = two_cols(btns)
    rws.append(nav_row("cat:%d" % ci, page, pages))
    rws.append([("🔙 Kategoriyalar", "cats")])
    return text, ikb(rws)


@user_router.message(CommandStart())
async def start(m: Message, bot: Bot):
    me = await bot.me()
    group_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Guruhga qo'shish 👥",
        url="https://t.me/%s?startgroup=true" % me.username)]])
    welcome = get_setting("welcome", DEFAULT_WELCOME).replace(
        "{total}", str(len(enabled_styles())))
    if is_admin(m.from_user.id):
        welcome += "\n\n⚙️ Admin panel: /admin"
    await m.answer(welcome, reply_markup=group_btn)
    await m.answer("👇 Uslubni tanlang:", reply_markup=styles_keyboard(m.from_user.id))


@user_router.message(Command("fonts"))
async def fonts_cmd(m: Message):
    text, kb = render_cats()
    await m.answer(text, reply_markup=kb)


@user_router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    uid = m.from_user.id
    t = m.text
    if t == CATALOG_BTN:
        text, kb = render_cats()
        await m.answer(text, reply_markup=kb)
        return
    enabled = enabled_styles()
    if t in SAMPLE_MAP:                           # foydalanuvchi uslub tugmasini bosdi
        i = SAMPLE_MAP[t]
        if i in enabled:
            run("UPDATE users SET style=? WHERE id=?", (i, uid))
            await m.answer("• Iltimos, menga matn yuboring.")
        else:
            await m.answer("Bu uslub hozircha o'chirilgan.", reply_markup=styles_keyboard(uid))
        return
    f = None
    for fr in rows("SELECT id, name, category FROM fonts"):
        if t == font_label(fr["id"], fr["name"]) or t == fr["name"]:
            f = fr
            break
    if f:                                         # foydalanuvchi font tugmasini bosdi
        kb = ikb([[("🔎 Google Fonts'da qidirish",
                    "https://fonts.google.com/?query=" + quote_plus(f["name"]))]])
        await m.answer("🔤 %s\n📁 Kategoriya: %s" % (f["name"], f["category"]), reply_markup=kb)
        return
    r = get("SELECT style FROM users WHERE id=?", (uid,))
    s = r["style"] if r else None
    if s is not None and s in enabled:            # oddiy matn -> tanlangan uslubda
        run("INSERT INTO usage(idx,cnt) VALUES(?,1) "
            "ON CONFLICT(idx) DO UPDATE SET cnt=cnt+1", (s,))
        await m.answer(STYLES[s](t)[:4096])
    else:                                         # hali uslub tanlanmagan
        await m.answer("👇 Uslubni tanlang:", reply_markup=styles_keyboard(uid))


@user_router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()


@user_router.callback_query(F.data == "cats")
async def cb_cats(cb: CallbackQuery):
    text, kb = render_cats()
    await show(cb, text, kb, html=False)


@user_router.callback_query(F.data.startswith("cat:"))
async def cb_cat(cb: CallbackQuery):
    _, ci, page = cb.data.split(":")
    text, kb = render_cat_page(int(ci), int(page))
    await show(cb, text, kb, html=False)


@user_router.callback_query(F.data.startswith("font:"))
async def cb_font(cb: CallbackQuery):
    _, fid, ci, page = cb.data.split(":")
    f = get("SELECT name, category FROM fonts WHERE id=?", (int(fid),))
    if not f:
        await cb.answer("Font topilmadi", show_alert=True)
        return
    text = "🔤 %s\n📁 Kategoriya: %s" % (f["name"], f["category"])
    kb = ikb([
        [("🔎 Google Fonts'da qidirish",
          "https://fonts.google.com/?query=" + quote_plus(f["name"]))],
        [("🔙 Orqaga", "cat:%s:%s" % (ci, page))],
    ])
    await show(cb, text, kb, html=False)


@user_router.inline_query()
async def inline(q: InlineQuery):
    text = q.query.strip() or "Example"
    results = []
    for i in enabled_styles()[:50]:
        styled = STYLES[i](text)
        results.append(InlineQueryResultArticle(
            id=str(i),
            title=styled[:64],
            input_message_content=InputTextMessageContent(message_text=styled[:4096]),
        ))
    await q.answer(results, cache_time=5, is_personal=True)


# ======================= KIRISH NAZORATI (ban / texnik rejim / majburiy obuna) =======================
class Gate(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)
        touch_user(user)
        if is_admin(user.id):
            return await handler(event, data)

        if is_banned(user.id):
            return await self.deny(event, "🚫 Siz bloklangansiz.")
        if get_setting("maintenance") == "1":
            return await self.deny(event, "🛠 Bot texnik ishlar uchun vaqtincha to'xtatilgan.")

        ch = get_setting("channel")
        if ch:
            ok = True
            try:
                member = await data["bot"].get_chat_member(ch, user.id)
                ok = member.status not in ("left", "kicked")
            except Exception:
                ok = True
            if not ok:
                url = "https://t.me/%s" % ch.lstrip("@") if ch.startswith("@") else None
                kb = ikb([[("📌 Kanalga obuna bo'lish", url)]]) if url else None
                return await self.deny(
                    event, "Botdan foydalanish uchun kanalga obuna bo'ling, so'ng /start bosing.", kb)
        return await handler(event, data)

    @staticmethod
    async def deny(event, text, kb=None):
        if isinstance(event, Message):
            await event.answer(text, reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, InlineQuery):
            await event.answer([], cache_time=1)


# ======================= ADMIN PANEL =======================
admin_router = Router()


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and is_admin(u.id))


admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())


class Admin(StatesGroup):
    wait = State()


CANCEL_KB = ikb([[("❌ Bekor qilish", "adm:cancel")]])
TASKS = set()


def menu_view():
    maint = get_setting("maintenance") == "1"
    ch = get_setting("channel")
    text = (
        "⚙️ <b>Admin panel</b>\n\n"
        "🛠 Texnik rejim: <b>%s</b>\n"
        "📌 Majburiy obuna: <b>%s</b>\n"
        "👥 Foydalanuvchilar: <b>%d</b>"
    ) % ("YOQILGAN" if maint else "o'chiq", esc(ch) if ch else "o'chiq",
         scalar("SELECT COUNT(*) FROM users"))
    kb = ikb([
        [("📊 Statistika", "adm:stats"), ("📢 Xabar yuborish", "adm:bc")],
        [("👥 Foydalanuvchilar", "adm:users:0"), ("🔎 ID bo'yicha topish", "adm:find")],
        [("🎨 Uslublar", "adm:styles:0"), ("🔤 Fontlar katalogi", "adm:fonts")],
        [("📝 Salom matni", "adm:welcome"), ("📌 Majburiy obuna", "adm:channel")],
        [("🛠 Texnik rejim: %s" % ("O'CHIRISH" if maint else "YOQISH"), "adm:maint"),
         ("👮 Adminlar", "adm:admins")],
        [("💾 Bazani yuklash", "adm:backup"), ("✖️ Yopish", "adm:close")],
    ])
    return text, kb


def stats_text():
    now = int(time.time())
    total = scalar("SELECT COUNT(*) FROM users")
    new24 = scalar("SELECT COUNT(*) FROM users WHERE joined>=?", (now - 86400,))
    new7 = scalar("SELECT COUNT(*) FROM users WHERE joined>=?", (now - 7 * 86400,))
    act24 = scalar("SELECT COUNT(*) FROM users WHERE last_seen>=?", (now - 86400,))
    act7 = scalar("SELECT COUNT(*) FROM users WHERE last_seen>=?", (now - 7 * 86400,))
    banned = scalar("SELECT COUNT(*) FROM users WHERE banned=1")
    blocked = scalar("SELECT COUNT(*) FROM users WHERE active=0")
    conv = scalar("SELECT SUM(cnt) FROM usage")
    top = rows("SELECT idx, cnt FROM usage ORDER BY cnt DESC LIMIT 5")
    top_txt = "\n".join("  %d. %s — %d" % (n + 1, esc(SAMPLE_TEXT[r["idx"]]), r["cnt"])
                        for n, r in enumerate(top)) or "  —"
    return (
        "📊 <b>Statistika</b>\n\n"
        "👥 Jami: <b>%d</b>\n"
        "🆕 Yangi (24 soat / 7 kun): <b>%d / %d</b>\n"
        "🔥 Faol (24 soat / 7 kun): <b>%d / %d</b>\n"
        "🚫 Bloklangan: <b>%d</b>\n"
        "💤 Botni to'xtatgan: <b>%d</b>\n\n"
        "✍️ Jami matn o'zgartirishlar: <b>%d</b>\n"
        "🎨 Uslublar (yoqilgan/jami): <b>%d/%d</b>\n"
        "🔤 Katalogdagi fontlar: <b>%d</b>\n"
        "👮 Adminlar: <b>%d</b>\n\n"
        "🏆 Eng mashhur uslublar:\n%s"
    ) % (total, new24, new7, act24, act7, banned, blocked, conv,
         len(enabled_styles()), TOTAL, scalar("SELECT COUNT(*) FROM fonts"),
         len(set(SUPER_ADMINS) | {r[0] for r in rows("SELECT id FROM admins")}), top_txt)


def user_card(uid):
    u = get("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return None
    if u["banned"]:
        status = "🚫 Bloklangan"
    elif not u["active"]:
        status = "💤 Botni to'xtatgan"
    else:
        status = "✅ Faol"
    style = SAMPLE_TEXT[u["style"]] if u["style"] is not None else "—"
    text = (
        "👤 <b>%s</b>\nID: <code>%d</code>\nUsername: %s\n"
        "Qo'shilgan: %s\nOxirgi faollik: %s\nUslub: %s\nHolat: %s"
    ) % (esc(u["name"] or "—"), u["id"],
         ("@" + esc(u["username"])) if u["username"] else "—",
         fmt_time(u["joined"]), fmt_time(u["last_seen"]), esc(style), status)
    rws = []
    if not is_admin(uid):
        if u["banned"]:
            rws.append([("✅ Blokdan chiqarish", "adm:ban:%d:0" % uid)])
        else:
            rws.append([("🚫 Bloklash", "adm:ban:%d:1" % uid)])
    rws.append([("🔙 Ro'yxat", "adm:users:0"), ("🏠 Menyu", "adm:menu")])
    return text, ikb(rws)


def users_view(page):
    items = rows("SELECT id, name, banned FROM users ORDER BY joined DESC")
    chunk, page, pages = paginate(items, page)
    btns = [(("🚫 " if r["banned"] else "👤 ") + ((r["name"] or "—")[:18]) + " · " + str(r["id"]),
             "adm:u:%d" % r["id"]) for r in chunk]
    rws = [[b] for b in btns]
    rws.append(nav_row("adm:users", page, pages))
    rws.append([("🏠 Menyu", "adm:menu")])
    return "👥 <b>Foydalanuvchilar</b> (%d ta)" % len(items), ikb(rws)


def styles_view(page):
    dis = disabled_set()
    chunk, page, pages = paginate(list(range(TOTAL)), page)
    btns = [(("❌ " if i in dis else "✅ ") + SAMPLE_TEXT[i], "adm:st:%d:%d" % (i, page))
            for i in chunk]
    rws = two_cols(btns)
    rws.append(nav_row("adm:styles", page, pages))
    rws.append([("✅ Hammasini yoqish", "adm:stall"), ("🏠 Menyu", "adm:menu")])
    return ("🎨 <b>Uslublar</b>\nYoqilgan: %d / %d\nBosib yoqing/o'chiring:"
            % (TOTAL - len(dis), TOTAL)), ikb(rws)


def fonts_admin_cats():
    cats = catalog_cats()
    rws = [[("📁 %s (%d)" % (c["category"], c["c"]), "adm:fc:%d:0" % i)]
           for i, c in enumerate(cats)]
    rws.append([("➕ Font qo'shish", "adm:fadd")])
    rws.append([("🏠 Menyu", "adm:menu")])
    return "🔤 <b>Fontlar katalogi</b>\nKategoriyani tanlang (o'chirish uchun):", ikb(rws)


def fonts_admin_page(ci, page):
    cats = catalog_cats()
    if ci >= len(cats):
        return fonts_admin_cats()
    cat = cats[ci]["category"]
    items = rows("SELECT id, name FROM fonts WHERE category=? ORDER BY id", (cat,))
    chunk, page, pages = paginate(items, page)
    btns = [("🗑 " + r["name"], "adm:fd:%d:%d:%d" % (r["id"], ci, page)) for r in chunk]
    rws = two_cols(btns)
    rws.append(nav_row("adm:fc:%d" % ci, page, pages))
    rws.append([("🔙 Kategoriyalar", "adm:fonts")])
    return "📁 <b>%s</b>\nO'chirish uchun fontni bosing:" % esc(cat), ikb(rws)


def admins_view(uid):
    db_admins = [r[0] for r in rows("SELECT id FROM admins")]
    text = "👮 <b>Adminlar</b>\n\nBosh adminlar:\n%s\n\nQo'shilgan adminlar:\n%s" % (
        "\n".join("• <code>%d</code>" % a for a in SUPER_ADMINS),
        "\n".join("• <code>%d</code>" % a for a in db_admins) or "—")
    rws = []
    if uid in SUPER_ADMINS:
        rws.append([("➕ Admin qo'shish", "adm:aadd")])
        rws += two_cols([("🗑 %d" % a, "adm:adel:%d" % a) for a in db_admins])
    rws.append([("🏠 Menyu", "adm:menu")])
    return text, ikb(rws)


async def do_broadcast(bot: Bot, report_chat, from_chat, mid):
    ids = [r[0] for r in rows("SELECT id FROM users WHERE banned=0 AND active=1")]
    ok = fail = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, from_chat, mid)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(uid, from_chat, mid)
                ok += 1
            except Exception:
                fail += 1
        except TelegramForbiddenError:
            run("UPDATE users SET active=0 WHERE id=?", (uid,))
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(report_chat, "📢 Yuborish tugadi.\n✅ Yetkazildi: %d\n❌ Xato: %d" % (ok, fail))


@admin_router.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    text, kb = menu_view()
    await m.answer(text, reply_markup=kb, parse_mode="HTML")


@admin_router.message(F.text == ADMIN_BTN)
async def admin_btn(m: Message, state: FSMContext):
    await state.clear()
    text, kb = menu_view()
    await m.answer(text, reply_markup=kb, parse_mode="HTML")


@admin_router.message(Command("cancel"))
async def cancel_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi. /admin")


@admin_router.callback_query(F.data.startswith("adm:"))
async def adm_cb(cb: CallbackQuery, state: FSMContext, bot: Bot):
    p = cb.data.split(":")
    act = p[1]
    uid = cb.from_user.id
    back = [[("🏠 Menyu", "adm:menu")]]

    if act in ("menu", "cancel"):
        await state.clear()
        text, kb = menu_view()
        await show(cb, text, kb)

    elif act == "close":
        await state.clear()
        try:
            await cb.message.delete()
        except TelegramBadRequest:
            pass
        await cb.answer()

    elif act == "stats":
        await show(cb, stats_text(), ikb([[("🔄 Yangilash", "adm:stats")], back[0]]))

    elif act == "bc":
        await state.set_state(Admin.wait)
        await state.update_data(act="bc")
        await show(cb, "📢 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring "
                       "(matn, rasm, video, fayl — istalgani).", CANCEL_KB)

    elif act == "bcgo":
        d = await state.get_data()
        if d.get("act") != "bc_confirm":
            await cb.answer("Xabar topilmadi", show_alert=True)
            return
        await state.clear()
        task = asyncio.create_task(do_broadcast(bot, cb.message.chat.id, d["cid"], d["mid"]))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)
        await show(cb, "⏳ Yuborish boshlandi. Tugagach hisobot keladi.", ikb(back))

    elif act == "users":
        text, kb = users_view(int(p[2]))
        await show(cb, text, kb)

    elif act == "u":
        card = user_card(int(p[2]))
        if card:
            await show(cb, *card)
        else:
            await cb.answer("Topilmadi", show_alert=True)

    elif act == "ban":
        target, flag = int(p[2]), int(p[3])
        if is_admin(target):
            await cb.answer("Adminni bloklab bo'lmaydi", show_alert=True)
            return
        run("UPDATE users SET banned=? WHERE id=?", (flag, target))
        card = user_card(target)
        await show(cb, *card)

    elif act == "find":
        await state.set_state(Admin.wait)
        await state.update_data(act="find")
        await show(cb, "🔎 Foydalanuvchi ID raqamini yuboring:", CANCEL_KB)

    elif act == "styles":
        text, kb = styles_view(int(p[2]))
        await show(cb, text, kb)

    elif act == "st":
        i, page = int(p[2]), int(p[3])
        if i in disabled_set():
            run("DELETE FROM disabled_styles WHERE idx=?", (i,))
        else:
            run("INSERT OR IGNORE INTO disabled_styles(idx) VALUES(?)", (i,))
        text, kb = styles_view(page)
        await show(cb, text, kb)

    elif act == "stall":
        run("DELETE FROM disabled_styles")
        text, kb = styles_view(0)
        await show(cb, text, kb)

    elif act == "fonts":
        text, kb = fonts_admin_cats()
        await show(cb, text, kb)

    elif act == "fc":
        text, kb = fonts_admin_page(int(p[2]), int(p[3]))
        await show(cb, text, kb)

    elif act == "fd":
        fid, ci, page = int(p[2]), int(p[3]), int(p[4])
        run("DELETE FROM fonts WHERE id=?", (fid,))
        text, kb = fonts_admin_page(ci, page)
        await show(cb, text, kb)

    elif act == "fadd":
        await state.set_state(Admin.wait)
        await state.update_data(act="fadd")
        await show(cb, "➕ Font qo'shish. Har bir qatorga bittadan yozing:\n\n"
                       "<code>Font nomi | Kategoriya</code>\n\nMasalan:\n"
                       "<code>Ubuntu | Sans-serif\nLobster Two | Sarlavha / dekorativ</code>\n\n"
                       "Yangi kategoriya nomi yozsangiz, u avtomatik yaratiladi.", CANCEL_KB)

    elif act == "welcome":
        cur = get_setting("welcome", DEFAULT_WELCOME)
        await state.set_state(Admin.wait)
        await state.update_data(act="welcome")
        await show(cb, "📝 <b>Joriy salom matni:</b>\n\n%s\n\n"
                       "Yangi matnni yuboring. <code>{total}</code> — uslublar soni." % esc(cur),
                   ikb([[("♻️ Standartga qaytarish", "adm:wreset")],
                        [("❌ Bekor qilish", "adm:cancel")]]))

    elif act == "wreset":
        await state.clear()
        run("DELETE FROM settings WHERE key='welcome'")
        text, kb = menu_view()
        await show(cb, "✅ Standart matn tiklandi.\n\n" + text, kb)

    elif act == "channel":
        cur = get_setting("channel")
        await state.set_state(Admin.wait)
        await state.update_data(act="channel")
        await show(cb, "📌 <b>Majburiy obuna</b>\nJoriy kanal: <b>%s</b>\n\n"
                       "Kanal username'ini yuboring (masalan <code>@mening_kanalim</code>). "
                       "Bot o'sha kanalda admin bo'lishi shart." % (esc(cur) if cur else "o'chiq"),
                   ikb([[("🚫 O'chirish", "adm:chanoff")], [("❌ Bekor qilish", "adm:cancel")]]))

    elif act == "chanoff":
        await state.clear()
        set_setting("channel", "")
        text, kb = menu_view()
        await show(cb, "✅ Majburiy obuna o'chirildi.\n\n" + text, kb)

    elif act == "maint":
        set_setting("maintenance", "0" if get_setting("maintenance") == "1" else "1")
        text, kb = menu_view()
        await show(cb, text, kb)

    elif act == "admins":
        text, kb = admins_view(uid)
        await show(cb, text, kb)

    elif act == "aadd":
        if uid not in SUPER_ADMINS:
            await cb.answer("Faqat bosh admin", show_alert=True)
            return
        await state.set_state(Admin.wait)
        await state.update_data(act="aadd")
        await show(cb, "👮 Yangi adminning Telegram ID raqamini yuboring:", CANCEL_KB)

    elif act == "adel":
        if uid not in SUPER_ADMINS:
            await cb.answer("Faqat bosh admin", show_alert=True)
            return
        run("DELETE FROM admins WHERE id=?", (int(p[2]),))
        text, kb = admins_view(uid)
        await show(cb, text, kb)

    elif act == "backup":
        tmp = sqlite3.connect("backup_tmp.db")
        db.backup(tmp)
        tmp.close()
        await bot.send_document(cb.message.chat.id, FSInputFile("backup_tmp.db", filename="bot_backup.db"),
                                caption="💾 Baza nusxasi " + fmt_time(int(time.time())))
        await cb.answer()

    else:
        await cb.answer()


@admin_router.message(Admin.wait)
async def adm_input(m: Message, state: FSMContext, bot: Bot):
    if m.text and m.text.startswith("/"):
        raise SkipHandler                       # boshqa buyruqlar (/start va h.k.) ishlasin
    d = await state.get_data()
    act = d.get("act")
    txt = (m.text or "").strip()
    done_kb = ikb([[("🏠 Menyu", "adm:menu")]])

    if act in ("bc", "bc_confirm"):
        await state.update_data(act="bc_confirm", cid=m.chat.id, mid=m.message_id)
        n = scalar("SELECT COUNT(*) FROM users WHERE banned=0 AND active=1")
        await m.answer("👆 Shu xabar %d ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?" % n,
                       reply_to_message_id=m.message_id,
                       reply_markup=ikb([[("✅ Yuborish", "adm:bcgo"), ("❌ Bekor", "adm:cancel")]]))
        return

    if not txt:
        await m.answer("Iltimos, matn yuboring (yoki /cancel).")
        return

    if act == "find":
        if not txt.isdigit():
            await m.answer("ID faqat raqamlardan iborat bo'lishi kerak.")
            return
        card = user_card(int(txt))
        if not card:
            await m.answer("Bunday foydalanuvchi bazada yo'q.")
            return
        await state.clear()
        await m.answer(card[0], reply_markup=card[1], parse_mode="HTML")

    elif act == "welcome":
        set_setting("welcome", m.text)
        await state.clear()
        await m.answer("✅ Salom matni yangilandi.", reply_markup=done_kb)

    elif act == "channel":
        if not (txt.startswith("@") or txt.lstrip("-").isdigit()):
            await m.answer("Kanal @username yoki -100... ID ko'rinishida bo'lishi kerak.")
            return
        try:
            me = await bot.me()
            member = await bot.get_chat_member(txt, me.id)
            if member.status not in ("administrator", "creator"):
                await m.answer("Bot bu kanalda admin emas. Avval botni kanalga admin qiling.")
                return
        except Exception as e:
            await m.answer("Kanal topilmadi yoki bot unda yo'q: %s" % e)
            return
        set_setting("channel", txt)
        await state.clear()
        await m.answer("✅ Majburiy obuna yoqildi: %s" % txt, reply_markup=done_kb)

    elif act == "fadd":
        added = 0
        for line in txt.splitlines():
            if "|" not in line:
                continue
            name, cat = [x.strip() for x in line.split("|", 1)]
            if name and cat:
                cur = db.execute("INSERT OR IGNORE INTO fonts(category,name) VALUES(?,?)", (cat, name))
                added += cur.rowcount
        db.commit()
        await state.clear()
        await m.answer("✅ Qo'shildi: %d ta font." % added, reply_markup=done_kb)

    elif act == "aadd":
        if m.from_user.id not in SUPER_ADMINS:
            await state.clear()
            return
        if not txt.isdigit():
            await m.answer("ID faqat raqamlardan iborat bo'lishi kerak.")
            return
        run("INSERT OR IGNORE INTO admins(id) VALUES(?)", (int(txt),))
        await state.clear()
        await m.answer("✅ Admin qo'shildi: %s" % txt, reply_markup=done_kb)

    else:
        await state.clear()


# ======================= ISHGA TUSHIRISH =======================
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari
dp.message.outer_middleware(Gate())
dp.callback_query.outer_middleware(Gate())
dp.inline_query.outer_middleware(Gate())
dp.include_router(admin_router)     # admin handlerlari birinchi tekshiriladi
dp.include_router(user_router)


async def main():
    if TOKEN.startswith("BOT_TOKEN"):
        print("Avval TOKEN ni kodga yozing!")
        return
    init_db()
    bot = Bot(TOKEN)
    me = await bot.me()
    print("Bot ishga tushdi: @%s (%d ta uslub)" % (me.username, TOTAL))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
