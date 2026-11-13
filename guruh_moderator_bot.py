import os
import asyncio, re, sqlite3
from html import escape
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, ChatPermissions
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}  # bot adminlari: barcha guruhlarda panelga kira oladi
bot = Bot(TOKEN)
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari
GROUPS = F.chat.type.in_({"group", "supergroup"})

DEFAULT_WELCOME = ("👋 Xush kelibsiz, {name}!\n"
                   "{group} guruhiga qo'shilganingizdan xursandmiz.\n"
                   "Iltimos, guruh qoidalariga rioya qiling.")

db = sqlite3.connect("bot.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS settings(chat_id INTEGER PRIMARY KEY, filter INTEGER DEFAULT 1, max_warns INTEGER DEFAULT 3,
                                    welcome INTEGER DEFAULT 1, welcome_text TEXT);
CREATE TABLE IF NOT EXISTS words(chat_id INTEGER, word TEXT, UNIQUE(chat_id, word));
CREATE TABLE IF NOT EXISTS warns(chat_id INTEGER, user_id INTEGER, n INTEGER DEFAULT 0, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS msgs(chat_id INTEGER, user_id INTEGER, name TEXT, day TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(chat_id, user_id, day));
""")
for col in ("welcome INTEGER DEFAULT 1", "welcome_text TEXT"):  # eski bazani yangilash
    try:
        db.execute(f"ALTER TABLE settings ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass


def q(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur.fetchall()


def settings(cid):
    q("INSERT OR IGNORE INTO settings(chat_id) VALUES(?)", cid)
    return q("SELECT filter, max_warns, welcome, welcome_text FROM settings WHERE chat_id=?", cid)[0]


async def is_admin(cid, uid):
    if uid in SUPER_ADMINS:
        return True
    m = await bot.get_chat_member(cid, uid)
    return m.status in ("administrator", "creator")


# ---------- Yashirin so'kinishlarni aniqlash ----------
CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
               ["a", "b", "v", "g", "d", "e", "yo", "j", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                "s", "t", "u", "f", "x", "ts", "ch", "sh", "sh", "", "i", "", "e", "yu", "ya", "o", "q", "g", "h"]))
LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "|": "i", "€": "e"}
TR = str.maketrans({**CYR, **LEET})
SPACED = re.compile(r"(?<!\w)(?:\w[\s.\-_,]+){2,}\w(?!\w)")  # "s o k i s h", "s.o.k.i.s.h"


def norm(text: str) -> str:
    t = text.lower()
    for ch in "'ʻʼ’`´":
        t = t.replace(ch, "")                      # o' -> o, g' -> g
    t = t.translate(TR)                            # kirill -> lotin, 0->o, @->a ...
    t = SPACED.sub(lambda m: re.sub(r"[\s.\-_,]", "", m.group()), t)  # ajratib yozilganini birlashtirish
    return re.sub(r"(.)\1+", r"\1", t)             # sooookish -> sokish


def has_bad_word(cid, text):
    words = {norm(w) for (w,) in q("SELECT word FROM words WHERE chat_id=?", cid)} - {""}
    if not words:
        return False
    for tok in re.findall(r"[\w*]+", norm(text)):
        for w in words:
            head = tok[:len(w)]
            if head == w:
                return True
            if "*" in head and len(head) == len(w) and re.fullmatch(re.escape(head).replace(r"\*", "."), w):
                return True                        # s*kish kabi yulduzchali yozuv
    return False


# ---------- Admin panel ----------
def panel_kb(cid):
    f, mw, wl, _ = settings(cid)
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🛡 So'kinish filtri: {'ON ✅' if f else 'OFF ❌'}", callback_data="p:filter")
    kb.button(text="➖", callback_data="p:warn-")
    kb.button(text=f"⚠️ Ogohlantirish: {mw}", callback_data="p:noop")
    kb.button(text="➕", callback_data="p:warn+")
    kb.button(text=f"👋 Salomlashish: {'ON ✅' if wl else 'OFF ❌'}", callback_data="p:welcome")
    kb.button(text="✏️ Salomlashish matni", callback_data="p:wtext")
    kb.button(text="📊 Statistika", callback_data="p:stats")
    kb.button(text="📝 So'zlar ro'yxati", callback_data="p:words")
    kb.adjust(1, 3, 1, 1, 1, 1)
    return kb.as_markup()


async def stats_text(cid):
    today = datetime.now().strftime("%Y-%m-%d")
    week = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    members = await bot.get_chat_member_count(cid)
    t = q("SELECT COALESCE(SUM(n),0) FROM msgs WHERE chat_id=? AND day=?", cid, today)[0][0]
    w = q("SELECT COALESCE(SUM(n),0) FROM msgs WHERE chat_id=? AND day>=?", cid, week)[0][0]
    top = q("SELECT name, SUM(n) s FROM msgs WHERE chat_id=? AND day>=? GROUP BY user_id ORDER BY s DESC LIMIT 5", cid, week)
    lines = "\n".join(f"{i+1}. {n}: {s}" for i, (n, s) in enumerate(top)) or "Hozircha yo'q"
    return (f"📊 Statistika\n👥 A'zolar: {members}\n💬 Bugun: {t}\n📅 7 kunda: {w}\n\n"
            f"🏆 Eng faollar (7 kun):\n{lines}")


@dp.message(Command("admin"), GROUPS)
async def admin_panel(m: Message):
    if await is_admin(m.chat.id, m.from_user.id):
        await m.answer("⚙️ Admin panel", reply_markup=panel_kb(m.chat.id))


@dp.callback_query(F.data.startswith("p:"))
async def panel_cb(c: CallbackQuery):
    cid = c.message.chat.id
    if not await is_admin(cid, c.from_user.id):
        return await c.answer("Faqat adminlar uchun", show_alert=True)
    act = c.data[2:]
    if act == "filter":
        q("UPDATE settings SET filter=1-filter WHERE chat_id=?", cid)
    elif act == "welcome":
        q("UPDATE settings SET welcome=1-welcome WHERE chat_id=?", cid)
    elif act == "warn+":
        q("UPDATE settings SET max_warns=MIN(max_warns+1,10) WHERE chat_id=?", cid)
    elif act == "warn-":
        q("UPDATE settings SET max_warns=MAX(max_warns-1,1) WHERE chat_id=?", cid)
    elif act == "stats":
        await c.message.answer(await stats_text(cid))
    elif act == "wtext":
        cur = settings(cid)[3] or DEFAULT_WELCOME
        await c.message.answer("✏️ Hozirgi salomlashish matni:\n\n" + cur +
                               "\n\nO'zgartirish: /setwelcome yangi matn\n"
                               "Joy belgilari: {name} — yangi a'zo, {group} — guruh nomi\n"
                               "Asl holiga qaytarish: /setwelcome default")
    elif act == "words":
        ws = [w for (w,) in q("SELECT word FROM words WHERE chat_id=?", cid)]
        await c.message.answer("So'zlar: " + (", ".join(ws) or "bo'sh") +
                               "\n\nQo'shish: /addword so'z\nO'chirish: /delword so'z\n"
                               "Yashirin yozilishlar (s0kish, s.o.k.i.s.h, sooookish, kirill) avtomatik aniqlanadi.")
    if act in ("filter", "welcome", "warn+", "warn-"):
        await c.message.edit_reply_markup(reply_markup=panel_kb(cid))
    await c.answer()


@dp.message(Command("addword"), GROUPS)
async def addword(m: Message, command: CommandObject):
    if await is_admin(m.chat.id, m.from_user.id) and command.args:
        q("INSERT OR IGNORE INTO words VALUES(?,?)", m.chat.id, command.args.lower().strip())
        await m.reply("✅ Qo'shildi")


@dp.message(Command("delword"), GROUPS)
async def delword(m: Message, command: CommandObject):
    if await is_admin(m.chat.id, m.from_user.id) and command.args:
        q("DELETE FROM words WHERE chat_id=? AND word=?", m.chat.id, command.args.lower().strip())
        await m.reply("🗑 O'chirildi")


@dp.message(Command("setwelcome"), GROUPS)
async def setwelcome(m: Message, command: CommandObject):
    if not await is_admin(m.chat.id, m.from_user.id) or not command.args:
        return
    settings(m.chat.id)
    text = None if command.args.strip().lower() == "default" else command.args.strip()
    q("UPDATE settings SET welcome_text=? WHERE chat_id=?", text, m.chat.id)
    await m.reply("✅ Salomlashish matni saqlandi")


@dp.message(Command("stats"), GROUPS)
async def stats(m: Message):
    if await is_admin(m.chat.id, m.from_user.id):
        await m.answer(await stats_text(m.chat.id))


# ---------- /start (shaxsiy chat) ----------
@dp.message(Command("start", "help"), F.chat.type == "private")
async def start(m: Message):
    me = await bot.me()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Guruhga qo'shish", url=f"https://t.me/{me.username}?startgroup=true&admin=delete_messages+restrict_members")
    text = ("👋 Salom! Men guruh boshqaruvchi botman.\n\n"
            "🛡 So'kinishlarni (yashirinlarini ham) o'chiraman\n"
            "👋 Yangi a'zolarni kutib olaman\n"
            "📊 Guruh statistikasini ko'rsataman\n\n"
            "Meni guruhga qo'shing, admin qiling va guruhda /admin yozing.")
    if m.from_user.id in SUPER_ADMINS:
        text += "\n\n⭐ Siz bot adminisiz: barcha guruhlarda /admin ishlaydi."
    await m.answer(text, reply_markup=kb.as_markup())


# ---------- Salomlashish ----------
@dp.message(F.new_chat_members, GROUPS)
async def greet(m: Message):
    _, _, wl, wtext = settings(m.chat.id)
    if not wl:
        return
    for u in m.new_chat_members:
        if u.is_bot:
            continue
        mention = f'<a href="tg://user?id={u.id}">{escape(u.full_name)}</a>'
        text = escape(wtext or DEFAULT_WELCOME).replace("{name}", mention).replace("{group}", escape(m.chat.title or ""))
        await m.answer(text, parse_mode="HTML")


# ---------- Moderatsiya ----------
async def moderate(m: Message):
    cid, uid = m.chat.id, m.from_user.id
    text = m.text or m.caption or ""
    f, max_warns, _, _ = settings(cid)
    if not f or not has_bad_word(cid, text) or await is_admin(cid, uid):
        return
    try:
        await m.delete()
    except Exception:                              # bot adminligi/o'chirish huquqi yo'q
        return
    q("INSERT INTO warns VALUES(?,?,1) ON CONFLICT(chat_id,user_id) DO UPDATE SET n=n+1", cid, uid)
    n = q("SELECT n FROM warns WHERE chat_id=? AND user_id=?", cid, uid)[0][0]
    if n >= max_warns:
        try:
            await bot.restrict_chat_member(cid, uid, ChatPermissions(can_send_messages=False),
                                           until_date=timedelta(hours=1))
        except Exception:                          # cheklash huquqi yo'q
            await bot.send_message(cid, "⚠️ Cheklash uchun botga «A'zolarni cheklash» huquqini bering.")
            return
        q("UPDATE warns SET n=0 WHERE chat_id=? AND user_id=?", cid, uid)
        await bot.send_message(cid, f"🔇 {m.from_user.full_name} 1 soatga cheklandi.")
    else:
        await bot.send_message(cid, f"⚠️ {m.from_user.full_name}, so'kinmang! Ogohlantirish: {n}/{max_warns}")


@dp.message(GROUPS, F.text | F.caption)
async def watcher(m: Message):
    day = datetime.now().strftime("%Y-%m-%d")
    q("INSERT INTO msgs VALUES(?,?,?,?,1) ON CONFLICT(chat_id,user_id,day) DO UPDATE SET n=n+1, name=excluded.name",
      m.chat.id, m.from_user.id, m.from_user.full_name, day)
    await moderate(m)


@dp.edited_message(GROUPS, F.text | F.caption)
async def edited(m: Message):  # xabarni tahrirlab filtrni aldash urinishi
    await moderate(m)


async def main():
    await dp.start_polling(bot)

asyncio.run(main())
