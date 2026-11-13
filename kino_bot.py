import os
import asyncio
import sqlite3
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ADMINS = ADMIN_IDS

router = Router()


class Admin(StatesGroup):
    action = State()    # qaysi admin amali kutilmoqda (data["action"] ichida)


def Btn(text, callback_data=None, url=None):
    return InlineKeyboardButton(text=text, callback_data=callback_data, url=url)


def Kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================= BAZA =======================
db = sqlite3.connect("kinobot.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, joined INTEGER, last_active INTEGER);
CREATE TABLE IF NOT EXISTS movies(
    code INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, file_id TEXT,
    ftype TEXT, premium INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS downloads(user_id INTEGER, code INTEGER, ts INTEGER);
CREATE TABLE IF NOT EXISTS channels(
    chat_id INTEGER PRIMARY KEY, title TEXT, link TEXT, request INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS join_requests(chat_id INTEGER, user_id INTEGER,
    PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS premium(user_id INTEGER PRIMARY KEY, until INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
""")
db.commit()

DEFAULTS = {
    "welcome": "Salom, {name}! 🎬\nKino kodini yuboring (masalan: 5).",
    "caption": "",
    "premium_text": "💎 Bu kino faqat premium obunachilar uchun.\nPremium olish uchun admin bilan bog'laning.",
    "send_mode": "media",     # media yoki file
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


def is_premium(uid):
    row = db.execute("SELECT until FROM premium WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0] > now())


# ======================= MENYULAR =======================
MENU = Kb([
    [Btn("➕ Kino qo'shish", callback_data="add"),
     Btn("🗑 Kino o'chirish", callback_data="delete")],
    [Btn("💎 Kinoni premium qilish", callback_data="setpremium"),
     Btn("💎 Premium berish", callback_data="givepremium")],
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
    mode = "Media (video)" if get("send_mode") == "media" else "Fayl (document)"
    auto = "YOQIQ" if get("autoapprove") == "1" else "O'CHIQ"
    return Kb([
        [Btn("✏️ Kirish matni", callback_data="set:welcome")],
        [Btn("✏️ Kino osti matni", callback_data="set:caption")],
        [Btn("✏️ Premium matni", callback_data="set:premium_text")],
        [Btn(f"🎞 Yuborish turi: {mode}", callback_data="tog:send_mode")],
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
            pass  # bot kanalda admin bo'lmasa, to'smaymiz
    return missing


async def ask_to_subscribe(target: Message, missing):
    rows = [[Btn(f"📢 {t}", url=l)] for t, l in missing]
    rows.append([Btn("✅ Tekshirish", callback_data="check")])
    await target.answer(
        "Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:",
        reply_markup=Kb(rows),
    )


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


# ======================= KINO YUBORISH =======================
async def send_movie(bot: Bot, chat_id, uid, code):
    row = db.execute(
        "SELECT title, file_id, ftype, premium FROM movies WHERE code=?", (code,)
    ).fetchone()
    if not row:
        await bot.send_message(chat_id, "❌ Bunday kodli kino topilmadi.")
        return
    title, file_id, ftype, premium = row

    if premium and not is_premium(uid) and not is_admin(uid):
        await bot.send_message(chat_id, get("premium_text"))
        return

    caption = title or ""
    extra = get("caption").strip()
    if extra:
        caption = f"{caption}\n\n{extra}".strip()
    caption = caption[:1024]

    order = ["video", "document"] if get("send_mode") == "media" else ["document", "video"]
    sent = False
    for kind in order:
        try:
            if kind == "video":
                await bot.send_video(chat_id, file_id, caption=caption)
            else:
                await bot.send_document(chat_id, file_id, caption=caption)
            sent = True
            break
        except Exception:
            continue
    if not sent:
        await bot.send_message(chat_id, "⚠️ Kinoni yuborib bo'lmadi.")
        return

    db.execute("INSERT INTO downloads VALUES(?, ?, ?)", (uid, code, now()))
    db.commit()

    ad = get("ad")
    if ad and not is_premium(uid):
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
        f"🎬 Kinolar: {one('SELECT COUNT(*) FROM movies')}",
        f"💎 Faol premium: {one('SELECT COUNT(*) FROM premium WHERE until>?', (now(),))}",
        f"📢 Majburiy kanallar: {one('SELECT COUNT(*) FROM channels')}",
        "",
    ]
    for name, t in periods:
        new = one("SELECT COUNT(*) FROM users WHERE joined>=?", (t,))
        act = one("SELECT COUNT(*) FROM users WHERE last_active>=?", (t,))
        dl = one("SELECT COUNT(*) FROM downloads WHERE ts>=?", (t,))
        lines.append(f"📅 {name}: yangi {new} | faol {act} | yuklash {dl}")
    top = db.execute(
        "SELECT m.code, m.title, COUNT(*) c FROM downloads d "
        "JOIN movies m ON m.code=d.code GROUP BY d.code ORDER BY c DESC LIMIT 5"
    ).fetchall()
    if top:
        lines.append("\n🔥 Top kinolar:")
        for code, title, c in top:
            lines.append(f"{code} — {(title or 'Nomsiz')[:30]} ({c})")
    return "\n".join(lines)


# ======================= BUYRUQLAR =======================
@router.message(Command("start"))
async def start(msg: Message, bot: Bot):
    uid = msg.from_user.id
    touch(uid)
    if not is_admin(uid):
        missing = await missing_channels(bot, uid)
        if missing:
            await ask_to_subscribe(msg, missing)
            return
    text = get("welcome").replace("{name}", msg.from_user.first_name or "")
    await msg.answer(text)


@router.message(Command("admin"))
async def admin(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer("🛠 Admin panel:", reply_markup=MENU)


# ======================= TUGMALAR =======================
PROMPTS = {
    "add": "🎬 Kinoni yuboring (video yoki fayl). Nomini izohga yozing.\nTugatish: /admin",
    "delete": "🗑 O'chiriladigan kino kodini yuboring.",
    "setpremium": "💎 Premium qilinadigan (yoki premiumdan chiqariladigan) kino kodini yuboring.",
    "givepremium": "💎 Foydalanuvchi ID va kunni yuboring.\nMasalan: 123456789 30",
    "addch": "📢 Botni kanalga ADMIN qiling, so'ng kanal @username'ini yuboring yoki kanaldan post forward qiling.",
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
        missing = await missing_channels(bot, uid)
        if missing:
            await q.answer("Hali hamma kanalga a'zo bo'lmadingiz!", show_alert=True)
        else:
            await q.answer()
            await q.message.answer("✅ Rahmat! Endi kino kodini yuboring.")
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
        if key == "send_mode":
            put(key, "file" if get(key) == "media" else "media")
        else:
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


# ======================= XABARLAR =======================
async def admin_action(msg: Message, bot: Bot, state: FSMContext, action: str):
    if action.startswith("edit:"):
        key = action[5:]
        put(key, "" if (msg.text or "").strip() == "-" else (msg.text or ""))
        await state.clear()
        await msg.answer("✅ Saqlandi.")

    elif action == "add":
        if msg.video:
            file_id, ftype = msg.video.file_id, "video"
        elif msg.document:
            file_id, ftype = msg.document.file_id, "document"
        else:
            await msg.answer("Iltimos, video yoki fayl yuboring.")
            return
        title = msg.caption or ""
        cur = db.execute(
            "INSERT INTO movies(title, file_id, ftype) VALUES(?, ?, ?)", (title, file_id, ftype))
        db.commit()
        await msg.answer(f"✅ Qo'shildi!\nKod: {cur.lastrowid}\nNomi: {title or 'Nomsiz'}")

    elif action == "delete":
        if not (msg.text and msg.text.strip().isdigit()):
            await msg.answer("Faqat raqamli kod yuboring.")
            return
        db.execute("DELETE FROM movies WHERE code=?", (int(msg.text),))
        db.commit()
        await state.clear()
        await msg.answer("🗑 Kino o'chirildi.")

    elif action == "setpremium":
        if not (msg.text and msg.text.strip().isdigit()):
            await msg.answer("Faqat raqamli kod yuboring.")
            return
        code = int(msg.text)
        row = db.execute("SELECT premium FROM movies WHERE code=?", (code,)).fetchone()
        if not row:
            await msg.answer("❌ Bunday kod yo'q.")
            return
        new = 0 if row[0] else 1
        db.execute("UPDATE movies SET premium=? WHERE code=?", (new, code))
        db.commit()
        await state.clear()
        await msg.answer(f"💎 {code} kodli kino endi {'PREMIUM' if new else 'ODDIY'}.")

    elif action == "givepremium":
        parts = (msg.text or "").split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            await msg.answer("Format: ID kun. Masalan: 123456789 30")
            return
        user_id, days = int(parts[0]), int(parts[1])
        row = db.execute("SELECT until FROM premium WHERE user_id=?", (user_id,)).fetchone()
        base = max(now(), row[0]) if row else now()
        until = base + days * 86400
        db.execute("INSERT OR REPLACE INTO premium VALUES(?, ?)", (user_id, until))
        db.commit()
        await state.clear()
        await msg.answer(
            f"💎 {user_id} ga {days} kun premium berildi.\n"
            f"Tugash: {datetime.fromtimestamp(until):%Y-%m-%d}")
        try:
            await bot.send_message(user_id, f"💎 Sizga {days} kunlik premium berildi!")
        except Exception:
            pass

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
        await msg.answer("📣 Reklama o'rnatildi. Endi har kinodan keyin chiqadi (premium'lardan tashqari).")

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


# buyruq bo'lmagan xabarlar (PTB: filters.ALL & ~filters.COMMAND)
NOT_COMMAND = F.func(lambda m: not (m.text or "").startswith("/"))


@router.message(Admin.action, NOT_COMMAND, F.func(lambda m: is_admin(m.from_user.id)))
async def admin_state_router(msg: Message, bot: Bot, state: FSMContext):
    action = (await state.get_data()).get("action")
    if action:
        await admin_action(msg, bot, state, action)


@router.message(NOT_COMMAND)
async def user_router(msg: Message, bot: Bot):
    uid = msg.from_user.id
    touch(uid)
    if not is_admin(uid):
        missing = await missing_channels(bot, uid)
        if missing:
            await ask_to_subscribe(msg, missing)
            return

    text = (msg.text or "").strip()
    if not text.isdigit():
        await msg.answer("Iltimos, faqat raqamli kod yuboring.")
        return
    await send_movie(bot, msg.chat.id, uid, int(text))


async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    import plan_limits; plan_limits.install(dp)   # tarif limitlari
    dp.include_router(router)
    print("Bot ishga tushdi...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
