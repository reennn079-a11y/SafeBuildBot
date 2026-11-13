"""
Telegram konkurs / referal boti + admin panel, majburiy obuna, xabar yuborish.

Qanday ishlaydi:
  - Har bir foydalanuvchi shaxsiy havola oladi (t.me/bot?start=ref_ID).
  - Havola orqali YANGI foydalanuvchi kelib, (majburiy obuna bo'lsa) kanallarga
    obuna bo'lgach, taklif qilgan odamga +1 ball beriladi.
  - Reyting, ballarni ko'rish; admin esa g'olibni ball bo'yicha tasodifiy tanlaydi.

O'rnatish:
    pip install aiogram

Ishga tushirish:
    export BOT_TOKEN="..."
    python contest_bot.py
"""
import asyncio
import logging
import os
import random
import sqlite3
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari


# ---------- Baza (SQLite), adminlar, sozlamalar ----------

DB = sqlite3.connect("contest_bot.db", check_same_thread=False)
DB.executescript(
    """
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS channels(chat TEXT PRIMARY KEY, title TEXT, link TEXT);
    CREATE TABLE IF NOT EXISTS refs(uid INTEGER PRIMARY KEY, ref_by INTEGER, counted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS points(uid INTEGER PRIMARY KEY, n INTEGER);
    CREATE TABLE IF NOT EXISTS names(uid INTEGER PRIMARY KEY, name TEXT);
    """
)
for _admin_id in ADMIN_IDS:
    DB.execute("INSERT OR IGNORE INTO admins VALUES(?)", (_admin_id,))
DB.commit()

DEFAULT_WELCOME = (
    "Salom! 👋 Konkursga xush kelibsiz!\n\n"
    "🔗 Shaxsiy havolangizni oling, do'stlaringizni taklif qiling va ball to'plang.\n"
    "🏆 Eng ko'p ball to'plaganlar sovrin yutadi.\n\n"
    "Pastdagi menyudan foydalaning 👇"
)


def is_admin(uid: int) -> bool:
    return DB.execute("SELECT 1 FROM admins WHERE id=?", (uid,)).fetchone() is not None


def get_setting(key: str, default: str = "") -> str:
    row = DB.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    DB.execute("INSERT OR REPLACE INTO settings VALUES(?, ?)", (key, value))
    DB.commit()


class TrackUsers(BaseMiddleware):
    """Botga yozgan har bir foydalanuvchini bazaga qo'shadi."""

    async def __call__(self, handler, event: Message, data):
        if event.from_user and event.chat.type == "private":
            u = event.from_user
            cur = DB.execute("INSERT OR IGNORE INTO users VALUES(?)", (u.id,))
            is_new = cur.rowcount == 1
            DB.execute("INSERT OR REPLACE INTO names VALUES(?, ?)", (u.id, (u.first_name or "—")[:30]))
            if is_new and event.text and event.text.startswith("/start"):
                parts = event.text.split(maxsplit=1)
                payload = parts[1] if len(parts) > 1 else ""
                if payload.startswith("ref_") and payload[4:].isdigit():
                    ref = int(payload[4:])
                    exists = DB.execute("SELECT 1 FROM users WHERE id=?", (ref,)).fetchone()
                    if ref != u.id and exists:
                        DB.execute("INSERT OR IGNORE INTO refs(uid, ref_by) VALUES(?, ?)", (u.id, ref))
            DB.commit()
        return await handler(event, data)


dp.message.outer_middleware(TrackUsers())

# ---------- Admin panel (faqat adminlarga ko'rinadi) ----------

ADMIN = F.from_user.id.func(is_admin)
BG_TASKS: set = set()


class AdminSt(StatesGroup):
    broadcast = State()
    confirm = State()
    new_admin = State()
    welcome = State()
    channel = State()
    contest = State()
    winners = State()


def panel_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="ad:bc")],
        [InlineKeyboardButton(text="👋 Kirish matni", callback_data="ad:welcome")],
        [InlineKeyboardButton(text="🏆 Konkurs shartlari", callback_data="ad:ct")],
        [InlineKeyboardButton(text="⏯ Konkursni yoqish/to'xtatish", callback_data="ad:tg")],
        [InlineKeyboardButton(text="🎲 G'olibni tanlash", callback_data="ad:win")],
        [InlineKeyboardButton(text="♻️ Ballarni nolga tushirish", callback_data="ad:reset")],
        [InlineKeyboardButton(text="📣 Majburiy obuna", callback_data="ad:ch")],
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="ad:add")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="ad:stats")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("admin"), ADMIN)
async def admin_panel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 Admin panel", reply_markup=panel_kb())


@dp.message(Command("cancel"), ADMIN)
async def admin_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.", reply_markup=panel_kb())


@dp.callback_query(F.data == "ad:stats", ADMIN)
async def ad_stats(c: CallbackQuery):
    users = DB.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    admins = DB.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    await c.message.answer(f"👥 Foydalanuvchilar: {users}\n🛠 Adminlar: {admins}")
    await c.answer()


# --- Xabar yuborish (istalgan turdagi media) ---

@dp.callback_query(F.data == "ad:bc", ADMIN)
async def ad_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.broadcast)
    await c.message.answer(
        "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring "
        "(matn, rasm, video, audio, fayl, ovozli xabar va h.k.).\n"
        "Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.broadcast, ADMIN)
async def ad_bc_preview(m: Message, state: FSMContext):
    await state.update_data(chat_id=m.chat.id, msg_id=m.message_id)
    await state.set_state(AdminSt.confirm)
    await m.answer("👆 Shu xabar hammaga yuboriladi. Tasdiqlaysizmi?")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yuborish", callback_data="ad:go"),
            InlineKeyboardButton(text="❌ Bekor", callback_data="ad:no"),
        ]]
    )
    await m.answer("Tasdiqlang:", reply_markup=kb)


async def _broadcast(bot: Bot, admin_id: int, from_chat: int, msg_id: int):
    ok = fail = 0
    for (uid,) in DB.execute("SELECT id FROM users").fetchall():
        try:
            await bot.copy_message(uid, from_chat, msg_id)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(uid, from_chat, msg_id)
                ok += 1
            except Exception:
                fail += 1
        except Exception:
            fail += 1  # bot bloklangan yoki chat topilmadi
        await asyncio.sleep(0.05)
    await bot.send_message(admin_id, f"✅ Yuborildi: {ok}\n❌ Yetib bormadi: {fail}")


@dp.callback_query(F.data == "ad:go", AdminSt.confirm, ADMIN)
async def ad_go(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await c.message.edit_text("📤 Yuborish boshlandi, tugagach xabar beraman.")
    task = asyncio.create_task(
        _broadcast(c.bot, c.from_user.id, data["chat_id"], data["msg_id"])
    )
    BG_TASKS.add(task)
    task.add_done_callback(BG_TASKS.discard)
    await c.answer()


@dp.callback_query(F.data == "ad:no", ADMIN)
async def ad_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Bekor qilindi.")
    await c.answer()


# --- Admin qo'shish ---

@dp.callback_query(F.data == "ad:add", ADMIN)
async def ad_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.new_admin)
    await c.message.answer("Yangi adminning Telegram ID raqamini yuboring.\nBekor qilish: /cancel")
    await c.answer()


@dp.message(AdminSt.new_admin, ADMIN)
async def ad_add_save(m: Message, state: FSMContext):
    if not m.text or not m.text.strip().isdigit():
        return await m.answer("ID faqat raqamlardan iborat bo'lishi kerak. Qayta yuboring.")
    DB.execute("INSERT OR IGNORE INTO admins VALUES(?)", (int(m.text.strip()),))
    DB.commit()
    await state.clear()
    await m.answer(f"✅ {m.text.strip()} admin qilindi.", reply_markup=panel_kb())


# --- Kirish matni ---

@dp.callback_query(F.data == "ad:welcome", ADMIN)
async def ad_welcome(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.welcome)
    await c.message.answer(
        "Yangi kirish (/start) matnini yuboring.\n"
        "Qalin, qiya kabi formatlashdan foydalansangiz bo'ladi.\n"
        "Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.welcome, ADMIN, F.text)
async def ad_welcome_save(m: Message, state: FSMContext):
    set_setting("welcome", m.html_text)
    await state.clear()
    await m.answer("✅ Kirish matni saqlandi.", reply_markup=panel_kb())


# ---------- Majburiy obuna ----------

def channels() -> list[tuple]:
    return DB.execute("SELECT chat, title, link FROM channels").fetchall()


async def _is_member(bot: Bot, chat: str, uid: int) -> bool:
    try:
        cm = await bot.get_chat_member(int(chat), uid)
    except Exception:
        logging.warning("Obunani tekshirib bo'lmadi (%s). Bot kanalda admin ekanini tekshiring.", chat)
        return True  # tekshirib bo'lmasa, foydalanuvchini bloklamaymiz
    if cm.status in ("member", "administrator", "creator"):
        return True
    return cm.status == "restricted" and getattr(cm, "is_member", False)


async def missing_channels(bot: Bot, uid: int) -> list[tuple]:
    return [c for c in channels() if not await _is_member(bot, c[0], uid)]


def sub_kb(missing: list[tuple]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {t}", url=link)] for _, t, link in missing]
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="chk")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


SUB_TEXT = "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling, so'ng «Tekshirish» tugmasini bosing:"


class ForceSub(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        is_cb = isinstance(event, CallbackQuery)
        private = is_cb or event.chat.type == "private"
        if (
            not user
            or not private
            or is_admin(user.id)
            or (is_cb and event.data == "chk")
            or not channels()
        ):
            return await handler(event, data)
        missing = await missing_channels(data["bot"], user.id)
        if not missing:
            return await handler(event, data)
        if is_cb:
            await event.answer("Avval kanallarga obuna bo'ling.", show_alert=True)
            return await event.message.answer(SUB_TEXT, reply_markup=sub_kb(missing))
        return await event.answer(SUB_TEXT, reply_markup=sub_kb(missing))


dp.message.outer_middleware(ForceSub())
dp.callback_query.outer_middleware(ForceSub())


@dp.callback_query(F.data == "chk")
async def check_sub(c: CallbackQuery):
    missing = await missing_channels(c.bot, c.from_user.id)
    if missing:
        return await c.answer("Hali hamma kanalga obuna bo'lmagansiz.", show_alert=True)
    await c.message.delete()
    await credit_referral(c.bot, c.from_user.id)
    await c.message.answer(
        get_setting("welcome", DEFAULT_WELCOME), parse_mode="HTML", reply_markup=main_kb()
    )
    await c.answer()


# --- Adminlar uchun: kanal qo'shish / o'chirish ---

def _channels_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🗑 {t}", callback_data=f"ad:chdel:{chat}")]
        for chat, t, _ in channels()
    ]
    rows.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="ad:chadd")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "ad:ch", ADMIN)
async def ad_channels(c: CallbackQuery):
    n = len(channels())
    await c.message.answer(
        f"📣 Majburiy obuna kanallari: {n}\n"
        "Kanal o'chirish uchun 🗑 tugmasini bosing.",
        reply_markup=_channels_kb(),
    )
    await c.answer()


@dp.callback_query(F.data.startswith("ad:chdel:"), ADMIN)
async def ad_channel_del(c: CallbackQuery):
    DB.execute("DELETE FROM channels WHERE chat=?", (c.data.split(":", 2)[2],))
    DB.commit()
    await c.message.edit_reply_markup(reply_markup=_channels_kb())
    await c.answer("O'chirildi")


@dp.callback_query(F.data == "ad:chadd", ADMIN)
async def ad_channel_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.channel)
    await c.message.answer(
        "Avval botni kanalga ADMIN qilib qo'shing, so'ng yuboring:\n\n"
        "• Ochiq kanal: @kanal_username\n"
        "• Yopiq kanal: -1001234567890 https://t.me/+taklif_havola\n\n"
        "Bekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.channel, ADMIN, F.text)
async def ad_channel_save(m: Message, state: FSMContext):
    parts = m.text.split()
    ref = parts[0]
    link = parts[1] if len(parts) > 1 else (f"https://t.me/{ref[1:]}" if ref.startswith("@") else None)
    if not link:
        return await m.answer("Yopiq kanal uchun taklif havolasini ham yozing. Qayta yuboring.")
    try:
        chat = await m.bot.get_chat(ref if ref.startswith("@") else int(ref))
        me = await m.bot.get_chat_member(chat.id, (await m.bot.me()).id)
        if me.status not in ("administrator", "creator"):
            return await m.answer("Bot bu kanalda admin emas. Admin qiling va qayta yuboring.")
    except Exception as e:
        return await m.answer(f"Kanal topilmadi yoki bot unda yo'q: {str(e)[:150]}")
    DB.execute(
        "INSERT OR REPLACE INTO channels VALUES(?, ?, ?)", (str(chat.id), chat.title or ref, link)
    )
    DB.commit()
    await state.clear()
    await m.answer(f"✅ «{chat.title}» qo'shildi.", reply_markup=panel_kb())


# ---------- Konkurs: ballar, reyting ----------

B_LINK = "🔗 Mening havolam"
B_POINTS = "📊 Ballarim"
B_TOP = "🏆 Reyting"
B_RULES = "ℹ️ Shartlar"

DEFAULT_RULES = (
    "🎁 <b>Konkurs shartlari</b>\n\n"
    "1. «🔗 Mening havolam» tugmasi orqali shaxsiy havolangizni oling.\n"
    "2. Do'stlaringizga yuboring. Har bir <b>yangi</b> qo'shilgan do'st uchun +1 ball.\n"
    "3. Eng ko'p ball to'plaganlar o'rtasida sovrinlar o'ynaladi."
)


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=B_LINK)],
            [KeyboardButton(text=B_POINTS), KeyboardButton(text=B_TOP)],
            [KeyboardButton(text=B_RULES)],
        ],
        resize_keyboard=True,
    )


def get_points(uid: int) -> int:
    row = DB.execute("SELECT n FROM points WHERE uid=?", (uid,)).fetchone()
    return row[0] if row else 0


def add_point(uid: int):
    DB.execute(
        "INSERT INTO points VALUES(?, 1) ON CONFLICT(uid) DO UPDATE SET n = n + 1", (uid,)
    )
    DB.commit()


def contest_on() -> bool:
    return get_setting("contest_on", "1") == "1"


async def credit_referral(bot: Bot, uid: int):
    """Foydalanuvchi talablarni bajargach (obuna bo'lgach), taklif qilganga ball beradi."""
    if not contest_on():
        return
    row = DB.execute("SELECT ref_by FROM refs WHERE uid=? AND counted=0", (uid,)).fetchone()
    if not row:
        return
    DB.execute("UPDATE refs SET counted=1 WHERE uid=?", (uid,))
    DB.commit()
    add_point(row[0])
    name = DB.execute("SELECT name FROM names WHERE uid=?", (uid,)).fetchone()
    try:
        await bot.send_message(
            row[0],
            f"🎉 Havolangiz orqali {name[0] if name else 'yangi foydalanuvchi'} qo'shildi!\n"
            f"Ballaringiz: {get_points(row[0])}",
        )
    except Exception:
        pass  # taklif qiluvchi botni bloklagan bo'lishi mumkin


class RefCredit(BaseMiddleware):
    """Obuna tekshiruvidan o'tgan (ForceSub'dan keyingi) xabarlarda ball hisoblaydi."""

    async def __call__(self, handler, event: Message, data):
        if event.from_user and event.chat.type == "private":
            await credit_referral(data["bot"], event.from_user.id)
        return await handler(event, data)


dp.message.outer_middleware(RefCredit())


@dp.message(F.text == B_LINK)
async def my_link(m: Message):
    me = await m.bot.me()
    link = f"https://t.me/{me.username}?start=ref_{m.from_user.id}"
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('Konkursda qatnashing! 🎁')}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📤 Do'stlarga yuborish", url=share)]]
    )
    await m.answer(
        f"🔗 Sizning shaxsiy havolangiz:\n{link}\n\n"
        "Har bir yangi qo'shilgan do'st uchun +1 ball oling!",
        reply_markup=kb,
    )


@dp.message(F.text == B_POINTS)
async def my_points(m: Message):
    uid = m.from_user.id
    pts = get_points(uid)
    invited = DB.execute(
        "SELECT COUNT(*) FROM refs WHERE ref_by=? AND counted=1", (uid,)
    ).fetchone()[0]
    place = DB.execute(
        "SELECT COUNT(*) + 1 FROM points WHERE n > ?", (pts,)
    ).fetchone()[0]
    text = f"📊 Ballaringiz: {pts}\n👥 Taklif qilganlar: {invited}"
    if pts > 0:
        text += f"\n🏅 O'rningiz: {place}"
    await m.answer(text)


@dp.message(F.text == B_TOP)
async def top10(m: Message):
    rows = DB.execute(
        "SELECT p.n, COALESCE(nm.name, '—') FROM points p "
        "LEFT JOIN names nm ON nm.uid = p.uid ORDER BY p.n DESC LIMIT 10"
    ).fetchall()
    if not rows:
        return await m.answer("Hali hech kim ball to'plamagan. Birinchi bo'ling! 🚀")
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[i] if i < 3 else str(i + 1) + '.'} {name} — {n} ball"
        for i, (n, name) in enumerate(rows)
    ]
    await m.answer("🏆 Top 10:\n\n" + "\n".join(lines))


@dp.message(F.text == B_RULES)
async def rules(m: Message):
    await m.answer(get_setting("contest_text", DEFAULT_RULES), parse_mode="HTML")


# ---------- Admin: konkurs boshqaruvi ----------

@dp.callback_query(F.data == "ad:ct", ADMIN)
async def ad_rules(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.contest)
    await c.message.answer(
        "Konkurs shartlari matnini yuboring (formatlash ishlaydi).\nBekor qilish: /cancel"
    )
    await c.answer()


@dp.message(AdminSt.contest, ADMIN, F.text)
async def ad_rules_save(m: Message, state: FSMContext):
    set_setting("contest_text", m.html_text)
    await state.clear()
    await m.answer("✅ Shartlar saqlandi.", reply_markup=panel_kb())


@dp.callback_query(F.data == "ad:tg", ADMIN)
async def ad_toggle(c: CallbackQuery):
    new = "0" if contest_on() else "1"
    set_setting("contest_on", new)
    await c.message.answer(
        "▶️ Konkurs YOQILDI, ballar hisoblanadi." if new == "1"
        else "⏸ Konkurs TO'XTATILDI, yangi ballar hisoblanmaydi."
    )
    await c.answer()


@dp.callback_query(F.data == "ad:win", ADMIN)
async def ad_winners(c: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSt.winners)
    await c.message.answer("Nechta g'olib tanlansin? Son yuboring (1–20).\nBekor qilish: /cancel")
    await c.answer()


@dp.message(AdminSt.winners, ADMIN)
async def ad_winners_pick(m: Message, state: FSMContext):
    if not m.text or not m.text.strip().isdigit() or not 1 <= int(m.text) <= 20:
        return await m.answer("1 dan 20 gacha son yuboring.")
    pool = DB.execute(
        "SELECT p.uid, p.n, COALESCE(nm.name, '—') FROM points p "
        "LEFT JOIN names nm ON nm.uid = p.uid WHERE p.n > 0"
    ).fetchall()
    await state.clear()
    if not pool:
        return await m.answer("Ball to'plagan ishtirokchi yo'q.", reply_markup=panel_kb())
    winners = []
    for _ in range(min(int(m.text), len(pool))):
        w = random.choices(pool, weights=[r[1] for r in pool])[0]  # ball qancha ko'p, imkoniyat shuncha katta
        winners.append(w)
        pool.remove(w)
    lines = [f"{i}. {n} (ID: {u}) — {p} ball" for i, (u, p, n) in enumerate(winners, 1)]
    await m.answer("🎲 G'oliblar (ball bo'yicha tasodifiy):\n\n" + "\n".join(lines), reply_markup=panel_kb())


@dp.callback_query(F.data == "ad:reset", ADMIN)
async def ad_reset(c: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ha, nolga tushirish", callback_data="ad:resetyes"),
            InlineKeyboardButton(text="❌ Yo'q", callback_data="ad:no"),
        ]]
    )
    await c.message.answer("Barcha ballar o'chiriladi (yangi konkurs boshlash uchun). Aniqmi?", reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data == "ad:resetyes", ADMIN)
async def ad_reset_yes(c: CallbackQuery):
    DB.execute("DELETE FROM points")
    DB.commit()
    await c.message.edit_text("♻️ Barcha ballar nolga tushirildi.")
    await c.answer()


# ---------- Bot handlerlari ----------

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(get_setting("welcome", DEFAULT_WELCOME), parse_mode="HTML", reply_markup=main_kb())


@dp.message()
async def other(m: Message):
    await m.answer("Menyudan tanlang 👇", reply_markup=main_kb())


async def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan")
    await dp.start_polling(Bot(TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
