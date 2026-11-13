import asyncio
import os
import sqlite3
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultCachedAudio, InlineQueryResultCachedDocument, Message,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ADMINS = ADMIN_IDS
PAGE = 10               # bir sahifadagi natijalar soni

router = Router()


class Admin(StatesGroup):
    action = State()    # qaysi admin amali kutilmoqda (data["action"] ichida)


def Btn(text, callback_data=None, url=None):
    return InlineKeyboardButton(text=text, callback_data=callback_data, url=url)


def Kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================= BAZA =======================
db = sqlite3.connect("kitobbot.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, joined INTEGER, last_active INTEGER);
CREATE TABLE IF NOT EXISTS books(
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, norm TEXT,
    file_id TEXT, kind TEXT, size INTEGER, added INTEGER);
CREATE TABLE IF NOT EXISTS downloads(user_id INTEGER, book_id INTEGER, ts INTEGER);
CREATE TABLE IF NOT EXISTS channels(
    chat_id INTEGER PRIMARY KEY, title TEXT, link TEXT, request INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS join_requests(chat_id INTEGER, user_id INTEGER,
    PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
""")
db.commit()

DEFAULTS = {
    "welcome": ("Salom, {name}! 📚\nKitob nomini yozing, men topib beraman.\n\n"
                "/last — yangi kitoblar\n/top — eng ko'p yuklanganlar"),
    "caption": "",
    "autoapprove": "0",
    "ad": "",
}


def get(key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else DEFAULTS.get(key, "")


def put(key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES(?, ?)", (key, value))
    db.commit()


def is_admin(uid):
    return uid in ADMINS


def now():
    return int(time.time())


def touch(uid):
    db.execute("INSERT OR IGNORE INTO users VALUES(?, ?, ?)", (uid, now(), now()))
    db.execute("UPDATE users SET last_active=? WHERE id=?", (now(), uid))
    db.commit()


def norm(s):
    s = (s or "").lower()
    for ch in "‘’ʻʼ`´":
        s = s.replace(ch, "'")
    return s.strip()


def fmt_size(size):
    if not size:
        return ""
    if size >= 1048576:
        return f"{size / 1048576:.0f} Mb"
    return f"{max(1, size // 1024)} Kb"


def emoji(kind):
    return "🎧" if kind == "audio" else "📖"


# ======================= MENYULAR =======================
MENU = Kb([
    [Btn("➕ Kitob qo'shish", callback_data="add"),
     Btn("🗑 Kitob o'chirish", callback_data="delete")],
    [Btn("✏️ Nomini o'zgartirish", callback_data="rename")],
    [Btn("📢 Majburiy kanal", callback_data="addch"),
     Btn("📨 Zayavkali kanal", callback_data="addch_req")],
    [Btn("➖ Kanalni o'chirish", callback_data="delch")],
    [Btn("📣 Reklama qo'yish", callback_data="setad"),
     Btn("🚫 Reklamani o'chirish", callback_data="delad")],
    [Btn("✉️ Oddiy xabar", callback_data="broadcast"),
     Btn("↪️ Forward xabar", callback_data="forward")],
    [Btn("📊 Statistika", callback_data="stats"),
     Btn("⚙️ Sozlamalar", callback_data="settings")],
])


def settings_kb():
    auto = "YOQIQ" if get("autoapprove") == "1" else "O'CHIQ"
    return Kb([
        [Btn("✏️ Kirish matni", callback_data="set:welcome")],
        [Btn("✏️ Kitob osti matni", callback_data="set:caption")],
        [Btn(f"📨 Zayavka avto-qabul: {auto}", callback_data="tog:autoapprove")],
    ])


# ======================= OBUNA =======================
async def missing_channels(bot: Bot, uid):
    missing = []
    for chat_id, title, link in db.execute("SELECT chat_id, title, link FROM channels").fetchall():
        try:
            member = await bot.get_chat_member(chat_id, uid)
            if member.status in ("left", "kicked"):
                sent = db.execute(
                    "SELECT 1 FROM join_requests WHERE chat_id=? AND user_id=?",
                    (chat_id, uid)).fetchone()
                if not sent:
                    missing.append((title, link))
        except Exception:
            pass
    return missing


async def gate(bot: Bot, target: Message, uid):
    """True bo'lsa foydalanuvchi o'tadi."""
    if is_admin(uid):
        return True
    missing = await missing_channels(bot, uid)
    if not missing:
        return True
    rows = [[Btn(f"📢 {t}", url=l)] for t, l in missing]
    rows.append([Btn("✅ Tekshirish", callback_data="check")])
    await target.answer(
        "Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:",
        reply_markup=Kb(rows),
    )
    return False


@router.chat_join_request()
async def join_request(r: ChatJoinRequest, bot: Bot):
    known = db.execute("SELECT 1 FROM channels WHERE chat_id=?", (r.chat.id,)).fetchone()
    if not known:
        return
    db.execute("INSERT OR IGNORE INTO join_requests VALUES(?, ?)", (r.chat.id, r.from_user.id))
    db.commit()
    if get("autoapprove") == "1":
        try:
            await bot.approve_chat_join_request(r.chat.id, r.from_user.id)
        except Exception:
            pass


# ======================= QIDIRUV VA NATIJALAR =======================
def fetch(mode, query):
    if mode == "s":
        words = norm(query).split()
        if not words:
            return []
        where = " AND ".join(["norm LIKE ?"] * len(words))
        return db.execute(
            f"SELECT id, title, size, kind FROM books WHERE {where} ORDER BY id DESC",
            [f"%{w}%" for w in words]).fetchall()
    if mode == "l":
        return db.execute(
            "SELECT id, title, size, kind FROM books ORDER BY id DESC LIMIT 50").fetchall()
    return db.execute(
        "SELECT b.id, b.title, b.size, b.kind FROM books b "
        "JOIN downloads d ON d.book_id=b.id GROUP BY b.id "
        "ORDER BY COUNT(*) DESC LIMIT 50").fetchall()


HEADERS = {"s": "« QIDIRUV »", "l": "« YANGI KITOBLAR »", "t": "« TOP KITOBLAR »"}


def render(mode, page, query, admin_view):
    rows = fetch(mode, query)
    total = len(rows)
    pages = max(1, (total + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * PAGE:(page + 1) * PAGE]

    lines = [f"{HEADERS[mode]} Natijalar ({total} ta, {page + 1}/{pages}-sahifa):", ""]
    nums = []
    for i, (bid, title, size, kind) in enumerate(chunk, 1):
        line = f"{i}) {emoji(kind)} {(title or 'Nomsiz')[:60]} {fmt_size(size)}"
        if admin_view:
            line += f" #{bid}"
        lines.append(line)
        nums.append(Btn(str(i), callback_data=f"get:{bid}"))

    kb = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    nav = []
    if page > 0:
        nav.append(Btn("◀️", callback_data=f"pg:{mode}:{page - 1}"))
    nav.append(Btn("🚫", callback_data="close"))
    if page < pages - 1:
        nav.append(Btn("▶️", callback_data=f"pg:{mode}:{page + 1}"))
    kb.append(nav)
    return "\n".join(lines), Kb(kb), total


# ======================= KITOB YUBORISH =======================
async def send_book(bot: Bot, chat_id, uid, bid):
    row = db.execute("SELECT title, file_id, kind FROM books WHERE id=?", (bid,)).fetchone()
    if not row:
        await bot.send_message(chat_id, "❌ Kitob topilmadi (o'chirilgan bo'lishi mumkin).")
        return
    title, file_id, kind = row
    caption = title or ""
    extra = get("caption").strip()
    if extra:
        caption = f"{caption}\n\n{extra}".strip()
    caption = caption[:1024]

    sent = False
    order = ["audio", "doc"] if kind == "audio" else ["doc"]
    for k in order:
        try:
            if k == "audio":
                await bot.send_audio(chat_id, file_id, caption=caption)
            else:
                await bot.send_document(chat_id, file_id, caption=caption)
            sent = True
            break
        except Exception:
            continue
    if not sent:
        await bot.send_message(chat_id, "⚠️ Kitobni yuborib bo'lmadi.")
        return

    db.execute("INSERT INTO downloads VALUES(?, ?, ?)", (uid, bid, now()))
    db.commit()

    ad = get("ad")
    if ad:
        try:
            from_chat, msg_id = ad.split(":")
            await bot.copy_message(chat_id, int(from_chat), int(msg_id))
        except Exception:
            pass


# ======================= STATISTIKA =======================
def one(sql, args=()):
    return db.execute(sql, args).fetchone()[0]


def stats_text():
    today = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    periods = [("Bugun", today), ("7 kun", now() - 7 * 86400), ("30 kun", now() - 30 * 86400)]
    lines = [
        f"👥 Jami foydalanuvchi: {one('SELECT COUNT(*) FROM users')}",
        f"📚 Kitoblar: {one('SELECT COUNT(*) FROM books')}",
        f"📢 Majburiy kanallar: {one('SELECT COUNT(*) FROM channels')}",
        "",
    ]
    for name, t in periods:
        new = one("SELECT COUNT(*) FROM users WHERE joined>=?", (t,))
        act = one("SELECT COUNT(*) FROM users WHERE last_active>=?", (t,))
        dl = one("SELECT COUNT(*) FROM downloads WHERE ts>=?", (t,))
        lines.append(f"📅 {name}: yangi {new} | faol {act} | yuklash {dl}")
    top = db.execute(
        "SELECT b.title, COUNT(*) c FROM downloads d JOIN books b ON b.id=d.book_id "
        "GROUP BY d.book_id ORDER BY c DESC LIMIT 5").fetchall()
    if top:
        lines.append("\n🔥 Top kitoblar:")
        for title, c in top:
            lines.append(f"• {(title or 'Nomsiz')[:35]} ({c})")
    return "\n".join(lines)


# ======================= BUYRUQLAR =======================
@router.message(Command("start"))
async def start(msg: Message, bot: Bot):
    uid = msg.from_user.id
    touch(uid)
    if not await gate(bot, msg, uid):
        return
    text = get("welcome").replace("{name}", msg.from_user.first_name or "")
    await msg.answer(text)


async def show_list(msg: Message, bot: Bot, mode):
    uid = msg.from_user.id
    touch(uid)
    if not await gate(bot, msg, uid):
        return
    text, kb, total = render(mode, 0, "", is_admin(uid))
    if total == 0:
        await msg.answer("Hozircha kitob yo'q.")
        return
    await msg.answer(text, reply_markup=kb)


@router.message(Command("last"))
async def last_cmd(msg: Message, bot: Bot):
    await show_list(msg, bot, "l")


@router.message(Command("top"))
async def top_cmd(msg: Message, bot: Bot):
    await show_list(msg, bot, "t")


@router.message(Command("admin"))
async def admin(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer("🛠 Admin panel:", reply_markup=MENU)


# ======================= TUGMALAR =======================
PROMPTS = {
    "add": "📚 Kitoblarni yuboring (PDF/fayl yoki audio). Bir vaqtda ko'pini yuborsa bo'ladi.\n"
           "Nomi — fayl nomidan yoki izohdan olinadi.\nTugatish: /admin",
    "delete": "🗑 O'chiriladigan kitob raqamini (#id) yuboring.",
    "rename": "✏️ Format: id yangi nom\nMasalan: 15 Alvido vatan",
    "addch": "📢 Botni kanalga ADMIN qiling, so'ng @username yuboring yoki kanaldan post forward qiling.",
    "addch_req": "📨 Zayavkali kanal: botni kanalga ADMIN qiling, so'ng @username yuboring yoki post forward qiling.",
    "setad": "📣 Reklama xabarini yuboring (matn, rasm yoki video). Uni o'chirib yubormang!",
    "broadcast": "✉️ Hammaga yuboriladigan xabarni yuboring.",
    "forward": "↪️ Hammaga forward qilinadigan xabarni yuboring.",
}


async def set_action(state: FSMContext, action: str):
    await state.set_state(Admin.action)
    await state.update_data(action=action)


@router.callback_query()
async def buttons(q: CallbackQuery, bot: Bot, state: FSMContext):
    uid = q.from_user.id
    data = q.data or ""

    if data == "check":
        if await missing_channels(bot, uid):
            await q.answer("Hali hamma kanalga a'zo bo'lmadingiz!", show_alert=True)
        else:
            await q.answer()
            await q.message.answer("✅ Rahmat! Endi kitob nomini yozing.")
        return

    if data == "close":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if data.startswith("get:"):
        await q.answer()
        touch(uid)
        if not await gate(bot, q.message, uid):
            return
        await send_book(bot, q.message.chat.id, uid, int(data[4:]))
        return

    if data.startswith("pg:"):
        await q.answer()
        _, mode, page = data.split(":")
        query = (await state.get_data()).get("q", "")
        if mode == "s" and not query:
            await q.message.answer("Qayta qidiring, iltimos.")
            return
        text, kb, _total = render(mode, int(page), query, is_admin(uid))
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        return

    await q.answer()
    if not is_admin(uid):
        return

    if data == "stats":
        await q.message.answer(stats_text())
    elif data == "settings":
        await q.message.answer("⚙️ Sozlamalar:", reply_markup=settings_kb())
    elif data.startswith("tog:"):
        key = data[4:]
        put(key, "0" if get(key) == "1" else "1")
        await q.message.edit_reply_markup(reply_markup=settings_kb())
    elif data.startswith("set:"):
        key = data[4:]
        await set_action(state, "edit:" + key)
        hint = "\n(Kirish matnida {name} — foydalanuvchi ismi. Tozalash uchun: -)" if key == "welcome" else "\n(Tozalash uchun: -)"
        await q.message.answer(f"Hozirgi matn:\n\n{get(key) or '(bo`sh)'}\n\nYangi matnni yuboring.{hint}")
    elif data == "delch":
        rows = [[Btn(f"❌ {t}", callback_data=f"rmch:{cid}")]
                for cid, t in db.execute("SELECT chat_id, title FROM channels").fetchall()]
        if rows:
            await q.message.answer("Qaysi kanalni o'chirasiz?", reply_markup=Kb(rows))
        else:
            await q.message.answer("Majburiy kanallar yo'q.")
    elif data.startswith("rmch:"):
        db.execute("DELETE FROM channels WHERE chat_id=?", (int(data[5:]),))
        db.commit()
        await q.message.answer("✅ Kanal o'chirildi.")
    elif data == "delad":
        put("ad", "")
        await q.message.answer("🚫 Reklama o'chirildi.")
    elif data in PROMPTS:
        await set_action(state, data)
        await q.message.answer(PROMPTS[data])


# ======================= ADMIN AMALLARI =======================
async def admin_action(msg: Message, bot: Bot, state: FSMContext, action: str):
    if action.startswith("edit:"):
        put(action[5:], "" if (msg.text or "").strip() == "-" else (msg.text or ""))
        await state.clear()
        await msg.answer("✅ Saqlandi.")

    elif action == "add":
        if msg.audio:
            file_id, size, kind = msg.audio.file_id, msg.audio.file_size, "audio"
            name = msg.audio.title or msg.audio.file_name or "Nomsiz"
        elif msg.document:
            file_id, size, kind = msg.document.file_id, msg.document.file_size, "doc"
            name = msg.document.file_name or "Nomsiz"
        else:
            await msg.answer("Iltimos, fayl (PDF) yoki audio yuboring.")
            return
        title = (msg.caption or "").strip() or os.path.splitext(name)[0].replace("_", " ")
        cur = db.execute(
            "INSERT INTO books(title, norm, file_id, kind, size, added) VALUES(?,?,?,?,?,?)",
            (title, norm(title), file_id, kind, size, now()))
        db.commit()
        await msg.answer(f"✅ #{cur.lastrowid} {emoji(kind)} {title} {fmt_size(size)}")

    elif action == "delete":
        if not (msg.text and msg.text.strip().lstrip("#").isdigit()):
            await msg.answer("Faqat raqam yuboring.")
            return
        db.execute("DELETE FROM books WHERE id=?", (int(msg.text.strip().lstrip("#")),))
        db.commit()
        await state.clear()
        await msg.answer("🗑 Kitob o'chirildi.")

    elif action == "rename":
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[0].lstrip("#").isdigit():
            await msg.answer("Format: id yangi nom")
            return
        db.execute("UPDATE books SET title=?, norm=? WHERE id=?",
                   (parts[1], norm(parts[1]), int(parts[0].lstrip("#"))))
        db.commit()
        await state.clear()
        await msg.answer("✅ Nomi o'zgartirildi.")

    elif action in ("addch", "addch_req"):
        chat = None
        origin = getattr(msg, "forward_origin", None)
        if origin is not None and getattr(origin, "chat", None):
            chat = origin.chat
        elif msg.text and msg.text.strip().startswith("@"):
            try:
                chat = await bot.get_chat(msg.text.strip())
            except Exception:
                chat = None
        if chat is None:
            await msg.answer("❌ Kanal topilmadi. @username yuboring yoki post forward qiling.")
            return
        try:
            me = await bot.get_chat_member(chat.id, bot.id)
            if me.status not in ("administrator", "creator"):
                await msg.answer("❌ Avval botni kanalda ADMIN qiling.")
                return
        except Exception:
            await msg.answer("❌ Bot kanalda topilmadi. Uni admin qiling.")
            return
        request = 1 if action == "addch_req" else 0
        try:
            if request:
                inv = await bot.create_chat_invite_link(chat.id, creates_join_request=True)
                link = inv.invite_link
            elif chat.username:
                link = f"https://t.me/{chat.username}"
            else:
                link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            await msg.answer("❌ Havola yaratib bo'lmadi. Botga 'havola yaratish' huquqini bering.")
            return
        db.execute("INSERT OR REPLACE INTO channels VALUES(?, ?, ?, ?)",
                   (chat.id, chat.title or "Kanal", link, request))
        db.commit()
        await state.clear()
        await msg.answer(f"✅ Qo'shildi: {chat.title}" + (" (zayavkali)" if request else ""))

    elif action == "setad":
        put("ad", f"{msg.chat.id}:{msg.message_id}")
        await state.clear()
        await msg.answer("📣 Reklama o'rnatildi. Har kitobdan keyin chiqadi.")

    elif action in ("broadcast", "forward"):
        ids = [r[0] for r in db.execute("SELECT id FROM users").fetchall()]
        await state.clear()
        ok = 0
        for user_id in ids:
            try:
                if action == "broadcast":
                    await bot.copy_message(user_id, msg.chat.id, msg.message_id)
                else:
                    await bot.forward_message(user_id, msg.chat.id, msg.message_id)
                ok += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        await msg.answer(f"✉️ Yuborildi: {ok}/{len(ids)}")


# ======================= XABARLAR =======================
# PTB: filters.ChatType.PRIVATE & filters.ALL & ~filters.COMMAND
PRIVATE_NOT_COMMAND = (F.chat.type == "private", F.func(lambda m: not (m.text or "").startswith("/")))


@router.message(Admin.action, *PRIVATE_NOT_COMMAND, F.func(lambda m: is_admin(m.from_user.id)))
async def admin_state_router(msg: Message, bot: Bot, state: FSMContext):
    action = (await state.get_data()).get("action")
    if action:
        await admin_action(msg, bot, state, action)


@router.message(*PRIVATE_NOT_COMMAND)
async def user_router(msg: Message, bot: Bot, state: FSMContext):
    uid = msg.from_user.id
    if not msg.text:
        return
    touch(uid)
    if not await gate(bot, msg, uid):
        return

    query = msg.text.strip()
    await state.update_data(q=query)
    text, kb, total = render("s", 0, query, is_admin(uid))
    if total == 0:
        await msg.answer("❌ Hech narsa topilmadi. Boshqacha yozib ko'ring.")
        return
    await msg.answer(text, reply_markup=kb)


# ======================= INLINE QIDIRUV =======================
@router.inline_query()
async def inline(iq: InlineQuery):
    q = iq.query.strip()
    if q:
        words = norm(q).split()
        where = " AND ".join(["norm LIKE ?"] * len(words))
        rows = db.execute(
            f"SELECT id, title, file_id, kind FROM books WHERE {where} ORDER BY id DESC LIMIT 20",
            [f"%{w}%" for w in words]).fetchall()
    else:
        rows = db.execute(
            "SELECT id, title, file_id, kind FROM books ORDER BY id DESC LIMIT 20").fetchall()

    results = []
    for bid, title, file_id, kind in rows:
        title = title or "Nomsiz"
        if kind == "audio":
            results.append(InlineQueryResultCachedAudio(
                id=str(bid), audio_file_id=file_id, caption=title[:1024]))
        else:
            results.append(InlineQueryResultCachedDocument(
                id=str(bid), title=title[:100], document_file_id=file_id,
                caption=title[:1024]))
    await iq.answer(results, cache_time=5)


async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    import plan_limits; plan_limits.install(dp)   # tarif limitlari
    dp.include_router(router)
    print("Kitob bot ishga tushdi...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
