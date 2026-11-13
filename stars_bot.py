"""Stars Bot: referal orqali Stars ishlash, hisob to'ldirish (so'm), Stars sotib olish va yechish.
Tugmali admin panel: statistika, matnlarni tahrirlash, sozlamalar, foydalanuvchi boshqaruvi, xabar yuborish.
Token va adminlar muhitdan olinadi (BOT_TOKEN, ADMIN_IDS). Ma'lumotlar joriy papkadagi stars.db da."""
import asyncio, logging, os, sqlite3, time

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

logging.basicConfig(level=logging.INFO)

# Faqat bitta botni alohida sinash uchun: muhit bo'sh bo'lsa shu qiymatlar ishlatiladi.
TEST_TOKEN = ""
TEST_ADMINS = []          # masalan [7843654005]
TOKEN = os.getenv("BOT_TOKEN") or TEST_TOKEN
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()] or TEST_ADMINS

db = sqlite3.connect("stars.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, uname TEXT, stars INTEGER DEFAULT 0,
    balance INTEGER DEFAULT 0, ref_by INTEGER, refs INTEGER DEFAULT 0, verified INTEGER DEFAULT 0, joined INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, kind TEXT, stars INTEGER,
    amount INTEGER DEFAULT 0, status TEXT DEFAULT 'new', created INTEGER);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount INTEGER,
    file_id TEXT, status TEXT DEFAULT 'new', created INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS texts(k TEXT PRIMARY KEY, v TEXT);
""")
try:                                                     # eski stars.db uchun
    db.execute("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0")
    db.commit()
except sqlite3.OperationalError:
    pass


def q(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur.fetchall()


def one(sql, *a):
    r = q(sql, *a)
    return r[0] if r else None


def run(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur.rowcount


def ins(sql, *a):
    """INSERT qiladi va yangi qator ID'sini qaytaradi (MAX(id) poygasiz)."""
    cur = db.execute(sql, a)
    db.commit()
    return cur.lastrowid


# ================= SOZLAMALAR =================
SETTINGS = {  # kalit: (nomi, standart qiymat)
    "bonus": ("⭐ Referal bonusi (Stars)", "1"),
    "price": ("💵 1 Stars narxi (so'm)", "240"),
    "minwd": ("💸 Yechish uchun minimal Stars", "50"),
    "mintopup": ("💳 Minimal to'ldirish (so'm)", "5000"),
    "packs": ("📦 Stars paketlari (vergul bilan)", "50,100,250,500"),
    "card": ("🏦 Karta ma'lumoti", "💳 8600 0000 0000 0000\n👤 Ism Familiya"),
    "contact": ("☎️ Aloqa uchun (@username)", "@admin"),
    "channel": ("📢 Majburiy kanal (@kanal, bo'sh = o'chiq)", ""),
}


def S(k):
    r = one("SELECT v FROM settings WHERE k=?", k)
    return r["v"] if r else SETTINGS[k][1]


# ================= MATNLAR (hammasi admin paneldan tahrirlanadi) =================
V_USER = "{bonus} {price} {balance} {stars} {refs} {minwd} {min} {amount} {link} {contact} {card}"
TEXTS = {  # kalit: (nomi, standart matn, o'zgaruvchilar)
    "btn_earn": ("🔘 Tugma: Stars ishlash", "⭐ Stars ishlash", ""),
    "btn_bal": ("🔘 Tugma: Hisobim", "💰 Hisobim", ""),
    "btn_topup": ("🔘 Tugma: Hisob to'ldirish", "💳 Hisob to'ldirish", ""),
    "btn_buy": ("🔘 Tugma: Stars sotib olish", "🛒 Stars sotib olish", ""),
    "btn_wd": ("🔘 Tugma: Stars yechish", "💸 Stars yechish", ""),
    "btn_top": ("🔘 Tugma: Top", "🏆 Top", ""),
    "btn_help": ("🔘 Tugma: Aloqa", "☎️ Aloqa", ""),
    "welcome": ("👋 Salomlashuv (/start)", "⭐ Stars botiga xush kelibsiz!\nDo'stlaringizni taklif qilib Stars ishlang yoki hisobingizni to'ldirib sotib oling.", ""),
    "gate": ("📢 Obuna talabi matni", "Botdan foydalanish uchun kanalga obuna bo'ling:", ""),
    "gate_btn": ("🔘 Obuna tugmasi", "📢 Kanalga obuna bo'lish", ""),
    "gate_chk": ("🔘 Tekshirish tugmasi", "✅ Tekshirish", ""),
    "gate_no": ("Obuna yo'q ogohlantirishi", "Hali obuna bo'lmagansiz.", ""),
    "gate_ok": ("Obuna tasdiqlandi", "✅ Tasdiqlandi!", ""),
    "earn": ("⭐ Stars ishlash bo'limi", "⭐ Har bir taklif qilgan do'stingiz uchun {bonus} ⭐ olasiz.\n\n🔗 Havolangiz:\n{link}\n\n👥 Takliflaringiz: {refs} ta", "{bonus} {link} {refs}"),
    "ref_new": ("Yangi referal xabari", "🎉 Yangi referal! Balansingizga {bonus} ⭐ qo'shildi.", "{bonus}"),
    "balance": ("💰 Hisobim bo'limi", "💰 Hisob: {balance}\n⭐ Stars: {stars}\n👥 Takliflar: {refs} ta\n💸 Yechish uchun minimal: {minwd} ⭐", "{balance} {stars} {refs} {minwd}"),
    "top_title": ("🏆 Top sarlavhasi", "🏆 Top referallar:", ""),
    "top_empty": ("Top bo'sh", "Hozircha reyting bo'sh.", ""),
    "help": ("☎️ Aloqa matni", "☎️ Savol va muammolar uchun: {contact}", "{contact}"),
    "topup_ask": ("💳 To'ldirish: summa so'rash", "💳 Necha so'm to'ldirmoqchisiz? (kamida {min})\nBekor qilish: /cancel", "{min}"),
    "topup_bad": ("To'ldirish: noto'g'ri summa", "Kamida {min} ni raqam bilan yozing.", "{min}"),
    "topup_pay": ("To'ldirish: to'lov ko'rsatmasi", "💰 Summa: {amount}\n\nKartaga o'tkazing:\n\n{card}\n\nSo'ng chek skrinshotini yuboring.", "{amount} {card}"),
    "topup_photo": ("To'ldirish: rasm kerak", "Iltimos, chek skrinshotini rasm qilib yuboring yoki /cancel.", ""),
    "topup_sent": ("To'ldirish: chek yuborildi", "✅ Chek yuborildi. Admin tasdiqlagach hisobingiz to'ldiriladi.", ""),
    "topup_ok": ("To'ldirish: tasdiqlandi", "✅ Hisobingiz {amount}ga to'ldirildi.\n💰 Hisob: {balance}", "{amount} {balance}"),
    "topup_no": ("To'ldirish: rad etildi", "❌ To'lovingiz tasdiqlanmadi. Savol bo'lsa: {contact}", "{contact}"),
    "buy_title": ("🛒 Sotib olish sarlavhasi", "🛒 Qancha Stars kerak? (1 ⭐ = {price})\n💰 Hisobingiz: {balance}", "{price} {balance}"),
    "buy_nofunds": ("Mablag' yetarli emas", "❌ Hisobingizda mablag' yetarli emas. «💳 Hisob to'ldirish» orqali to'ldiring.", ""),
    "need_username": ("@username kerak", "Stars yuborish uchun Telegram'da @username o'rnating, so'ng qayta urining.", ""),
    "buy_ok": ("Buyurtma qabul qilindi", "✅ {stars} ⭐ buyurtma qabul qilindi. Admin tez orada yuboradi.", "{stars}"),
    "wd_low": ("Yechish: Stars yetarli emas", "❌ Yechish uchun kamida {minwd} ⭐ kerak. Sizda: {stars} ⭐", "{minwd} {stars}"),
    "wd_ask": ("Yechish: miqdor so'rash", "Necha ⭐ yechmoqchisiz? ({minwd} dan {stars} gacha). Bekor qilish: /cancel", "{minwd} {stars}"),
    "wd_bad": ("Yechish: noto'g'ri miqdor", "{minwd} dan {stars} gacha raqam yozing.", "{minwd} {stars}"),
    "wd_sent": ("Yechish: so'rov yuborildi", "✅ So'rov yuborildi: {stars} ⭐. Admin tasdiqlagach Stars yuboriladi.", "{stars}"),
    "order_done": ("Buyurtma bajarildi", "⭐ {stars} Stars yuborildi. Rahmat!", "{stars}"),
    "order_no": ("Buyurtma rad etildi", "❌ So'rovingiz rad etildi, mablag' qaytarildi. Savol bo'lsa: {contact}", "{contact}"),
    "cancelled": ("Bekor qilindi", "Bekor qilindi.", ""),
}


class SafeDict(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def raw(k):
    r = one("SELECT v FROM texts WHERE k=?", k)
    return r["v"] if r else TEXTS[k][1]


def T(k, **kw):
    s = raw(k)
    kw.setdefault("contact", S("contact"))
    try:
        return s.format_map(SafeDict(**kw))
    except Exception:
        return s                                          # admin noto'g'ri { } yozgan bo'lsa ham bot yiqilmaydi


def som(n): return f"{n:,}".replace(",", " ") + " so'm"
def U(uid): return one("SELECT * FROM users WHERE id=?", uid)
def who(u): return f"{u['name']}" + (f" @{u['uname']}" if u["uname"] else "") + f" ({u['id']})"
def IK(rows): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


B_ADMIN = "🛠 Admin panel"


def menu(uid):
    b = lambda k: KeyboardButton(text=T(k))
    rows = [[b("btn_earn"), b("btn_bal")], [b("btn_topup"), b("btn_buy")], [b("btn_wd"), b("btn_top")], [b("btn_help")]]
    if uid in ADMIN_IDS:
        rows.append([KeyboardButton(text=B_ADMIN)])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=rows)


def BTN(key):                                             # tugma matni o'zgarsa ham ishlaydi
    return F.text.func(lambda t: t == T(key))


async def edit(msg, text, kb=None):
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=kb)
        else:
            await msg.edit_text(text, reply_markup=kb)
    except Exception:
        pass


async def show(m, view):
    await m.answer(view[0], reply_markup=view[1])


class Top(StatesGroup):
    amount = State()
    receipt = State()


class Wd(StatesGroup):
    amount = State()


class Adm(StatesGroup):
    text = State()
    setting = State()
    uid = State()
    amount = State()
    msgto = State()
    bc = State()


dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari
IS_ADMIN = F.from_user.id.in_(ADMIN_IDS)


# ================= MAJBURIY OBUNA =================
async def is_member(bot: Bot, uid: int) -> bool:
    ch = S("channel")
    if not ch:
        return True
    try:
        m = await bot.get_chat_member(ch, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return True                                       # sozlama xato bo'lsa foydalanuvchini to'smaymiz


def join_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=T("gate_btn"), url=f"https://t.me/{S('channel').lstrip('@')}")],
        [InlineKeyboardButton(text=T("gate_chk"), callback_data="chk")]])


class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        uid = event.from_user.id
        if uid in ADMIN_IDS or not S("channel"):
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "chk":
            return await handler(event, data)
        if await is_member(data["bot"], uid):
            return await handler(event, data)
        msg = event if isinstance(event, Message) else event.message
        await msg.answer(T("gate"), reply_markup=join_kb())
        if isinstance(event, CallbackQuery):
            await event.answer()


async def verify(bot: Bot, uid: int):
    u = U(uid)
    if u["verified"]:
        return
    q("UPDATE users SET verified=1 WHERE id=?", uid)
    if u["ref_by"]:
        b = int(S("bonus"))
        q("UPDATE users SET stars=stars+?, refs=refs+1 WHERE id=?", b, u["ref_by"])
        try:
            await bot.send_message(u["ref_by"], T("ref_new", bonus=b))
        except Exception:
            pass


# ================= FOYDALANUVCHI =================
@dp.message(CommandStart())
async def start(m: Message, command: CommandObject, bot: Bot, state: FSMContext):
    await state.clear()
    u = m.from_user
    if not U(u.id):
        ref = int(command.args) if command.args and command.args.isdigit() else None
        if ref == u.id or not ref or not U(ref):
            ref = None
        q("INSERT INTO users(id,name,uname,ref_by,joined) VALUES(?,?,?,?,?)", u.id, u.full_name, u.username, ref, int(time.time()))
    else:
        q("UPDATE users SET name=?, uname=? WHERE id=?", u.full_name, u.username, u.id)
    if not await is_member(bot, u.id):
        return await m.answer(T("gate"), reply_markup=join_kb())
    await verify(bot, u.id)
    await m.answer(T("welcome"), reply_markup=menu(u.id))


@dp.callback_query(F.data == "chk")
async def chk(c: CallbackQuery, bot: Bot):
    if not U(c.from_user.id):
        return await c.answer("Avval /start bosing.", show_alert=True)
    if not await is_member(bot, c.from_user.id):
        return await c.answer(T("gate_no"), show_alert=True)
    await verify(bot, c.from_user.id)
    await c.message.answer(T("gate_ok"), reply_markup=menu(c.from_user.id))
    await c.answer()


@dp.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(T("cancelled"), reply_markup=menu(m.from_user.id))


# --- menyu tugmalari (holatni tozalaydi, shuning uchun jarayon o'rtasida bosilsa ham ishlaydi) ---
@dp.message(BTN("btn_earn"))
async def earn(m: Message, bot: Bot, state: FSMContext):
    await state.clear()
    me = await bot.get_me()
    u = U(m.from_user.id)
    await m.answer(T("earn", bonus=S("bonus"), refs=u["refs"], link=f"https://t.me/{me.username}?start={m.from_user.id}"))


@dp.message(BTN("btn_bal"))
async def bal(m: Message, state: FSMContext):
    await state.clear()
    u = U(m.from_user.id)
    await m.answer(T("balance", balance=som(u["balance"]), stars=u["stars"], refs=u["refs"], minwd=S("minwd")))


@dp.message(BTN("btn_top"))
async def top(m: Message, state: FSMContext):
    await state.clear()
    rows = q("SELECT name, refs FROM users WHERE refs>0 ORDER BY refs DESC LIMIT 10")
    if not rows:
        return await m.answer(T("top_empty"))
    await m.answer(T("top_title") + "\n\n" + "\n".join(f"{i}. {r['name']} — {r['refs']} ta" for i, r in enumerate(rows, 1)))


@dp.message(BTN("btn_help"))
async def helpm(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(T("help"))


@dp.message(F.text == B_ADMIN, IS_ADMIN)
async def admin_btn(m: Message, state: FSMContext):
    await state.clear()
    await show(m, panel())


# --- hisob to'ldirish ---
@dp.message(BTN("btn_topup"))
async def topup(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Top.amount)
    await m.answer(T("topup_ask", min=som(int(S("mintopup")))))


@dp.message(Top.amount, F.text)
async def topup_amount(m: Message, state: FSMContext):
    t = m.text.replace(" ", "")
    mn = int(S("mintopup"))
    if not t.isdigit() or int(t) < mn:
        return await m.answer(T("topup_bad", min=som(mn)))
    await state.update_data(amount=int(t))
    await state.set_state(Top.receipt)
    await m.answer(T("topup_pay", amount=som(int(t)), card=S("card")))


@dp.message(Top.receipt, F.photo)
async def topup_receipt(m: Message, state: FSMContext, bot: Bot):
    amt = (await state.get_data())["amount"]
    await state.clear()
    pid = ins("INSERT INTO payments(uid,amount,file_id,created) VALUES(?,?,?,?)", m.from_user.id, amt, m.photo[-1].file_id, int(time.time()))
    for a in ADMIN_IDS:
        try:
            await bot.send_photo(a, m.photo[-1].file_id, caption=f"💳 To'lov #{pid}\n👤 {who(U(m.from_user.id))}\n💰 {som(amt)}",
                                 reply_markup=IK([[("✅ Tasdiqlash", f"p:ok:{pid}"), ("❌ Rad", f"p:no:{pid}")]]))
        except Exception:
            pass
    await m.answer(T("topup_sent"), reply_markup=menu(m.from_user.id))


@dp.message(Top.receipt)
async def topup_wrong(m: Message):
    await m.answer(T("topup_photo"))


# --- Stars sotib olish (hisobdan) ---
@dp.message(BTN("btn_buy"))
async def buy(m: Message, state: FSMContext):
    await state.clear()
    price = int(S("price"))
    rows = [[(f"{n} ⭐ — {som(n * price)}", f"buy:{n}")] for n in (int(x) for x in S("packs").split(","))]
    await m.answer(T("buy_title", price=som(price), balance=som(U(m.from_user.id)["balance"])), reply_markup=IK(rows))


@dp.callback_query(F.data.startswith("buy:"))
async def buy_pick(c: CallbackQuery, bot: Bot):
    n = int(c.data[4:])
    if str(n) not in S("packs").split(","):
        return await c.answer()
    if not c.from_user.username:
        return await c.answer(T("need_username"), show_alert=True)
    cost = n * int(S("price"))
    if run("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", cost, c.from_user.id, cost) == 0:
        return await c.answer(T("buy_nofunds"), show_alert=True)
    oid = ins("INSERT INTO orders(uid,kind,stars,amount,status,created) VALUES(?,?,?,?,?,?)", c.from_user.id, "buy", n, cost, "paid", int(time.time()))
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, f"🛒 Buyurtma #{oid}\n👤 {who(U(c.from_user.id))}\n⭐ {n} Stars (hisobdan {som(cost)} yechildi)\n\nStars'ni yuboring va «✅ Yuborildi» ni bosing.", reply_markup=order_kb(oid))
        except Exception:
            pass
    await c.message.answer(T("buy_ok", stars=n))
    await c.answer()


# --- Stars yechish ---
@dp.message(BTN("btn_wd"))
async def wd(m: Message, state: FSMContext):
    await state.clear()
    u = U(m.from_user.id)
    mn = int(S("minwd"))
    if u["stars"] < mn:
        return await m.answer(T("wd_low", minwd=mn, stars=u["stars"]))
    if not m.from_user.username:
        return await m.answer(T("need_username"))
    await state.set_state(Wd.amount)
    await m.answer(T("wd_ask", minwd=mn, stars=u["stars"]))


@dp.message(Wd.amount, F.text)
async def wd_amount(m: Message, state: FSMContext, bot: Bot):
    t, u, mn = m.text.strip(), U(m.from_user.id), int(S("minwd"))
    if not t.isdigit() or not (mn <= int(t) <= u["stars"]):
        return await m.answer(T("wd_bad", minwd=mn, stars=u["stars"]))
    n = int(t)
    await state.clear()
    q("UPDATE users SET stars=stars-? WHERE id=?", n, m.from_user.id)
    oid = ins("INSERT INTO orders(uid,kind,stars,created) VALUES(?,?,?,?)", m.from_user.id, "wd", n, int(time.time()))
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, f"💸 Yechish #{oid}\n👤 {who(U(m.from_user.id))}\n⭐ {n} Stars\n\nStars'ni yuboring va «✅ Yuborildi» ni bosing.", reply_markup=order_kb(oid))
        except Exception:
            pass
    await m.answer(T("wd_sent", stars=n), reply_markup=menu(m.from_user.id))


# ================= ADMIN: buyurtma va to'lovlarni ko'rib chiqish =================
def order_kb(oid):
    return IK([[("✅ Yuborildi", f"o:done:{oid}"), ("❌ Rad (qaytarish)", f"o:no:{oid}")]])


@dp.callback_query(F.data.startswith("o:"), IS_ADMIN)
async def order_act(c: CallbackQuery, bot: Bot):
    _, act, oid = c.data.split(":")
    o = one("SELECT * FROM orders WHERE id=?", int(oid))
    new = "done" if act == "done" else "rejected"
    if not o or run("UPDATE orders SET status=? WHERE id=? AND status IN ('new','paid')", new, o["id"]) == 0:
        return await c.answer("Allaqachon yakunlangan.", show_alert=True)
    if act == "done":
        msg, note = T("order_done", stars=o["stars"]), "\n\n✅ Yuborildi"
    else:
        if o["kind"] == "buy":
            q("UPDATE users SET balance=balance+? WHERE id=?", o["amount"], o["uid"])
        else:
            q("UPDATE users SET stars=stars+? WHERE id=?", o["stars"], o["uid"])
        msg, note = T("order_no"), "\n\n❌ Rad etildi, qaytarildi"
    await edit(c.message, (c.message.text or "") + note)
    try:
        await bot.send_message(o["uid"], msg)
    except Exception:
        pass
    await c.answer()


@dp.callback_query(F.data.startswith("p:"), IS_ADMIN)
async def pay_act(c: CallbackQuery, bot: Bot):
    _, act, pid = c.data.split(":")
    p = one("SELECT * FROM payments WHERE id=?", int(pid))
    if not p or run("UPDATE payments SET status=? WHERE id=? AND status='new'", "ok" if act == "ok" else "no", p["id"]) == 0:
        return await c.answer("Allaqachon ko'rib chiqilgan.", show_alert=True)
    if act == "ok":
        q("UPDATE users SET balance=balance+? WHERE id=?", p["amount"], p["uid"])
        msg, note = T("topup_ok", amount=som(p["amount"]), balance=som(U(p["uid"])["balance"])), "\n\n✅ Tasdiqlandi"
    else:
        msg, note = T("topup_no"), "\n\n❌ Rad etildi"
    await edit(c.message, (c.message.caption or c.message.text or "") + note)
    try:
        await bot.send_message(p["uid"], msg)
    except Exception:
        pass
    await c.answer()


# ================= ADMIN PANEL =================
def panel():
    pend = one("SELECT (SELECT COUNT(*) FROM orders WHERE status IN ('new','paid')) + (SELECT COUNT(*) FROM payments WHERE status='new') c")["c"]
    txt = f"🛠 Admin panel\n\n⏳ Kutilayotgan: {pend} ta\n\nKerakli bo'limni tanlang:"
    return txt, IK([[("📊 Statistika", "a:stat"), ("⏳ Kutilayotganlar", "a:pend")],
                    [("📝 Matnlar", "a:txt:0"), ("⚙️ Sozlamalar", "a:set")],
                    [("👤 Foydalanuvchi", "a:usr"), ("📢 Xabar yuborish", "a:bc")]])


@dp.message(Command("admin"), IS_ADMIN)
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await show(m, panel())


@dp.callback_query(F.data == "a:home", IS_ADMIN)
async def a_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(c.message, *panel())
    await c.answer()


BACK = [("⬅️ Orqaga", "a:home")]


@dp.callback_query(F.data == "a:stat", IS_ADMIN)
async def a_stat(c: CallbackQuery):
    def g(sql):
        return one(sql)["c"]
    users = g("SELECT COUNT(*) c FROM users")
    refs = g("SELECT COALESCE(SUM(refs),0) c FROM users")
    stars = g("SELECT COALESCE(SUM(stars),0) c FROM users")
    money = g("SELECT COALESCE(SUM(balance),0) c FROM users")
    topups = g("SELECT COALESCE(SUM(amount),0) c FROM payments WHERE status='ok'")
    done = g("SELECT COUNT(*) c FROM orders WHERE status='done'")
    pend = g("SELECT COUNT(*) c FROM orders WHERE status IN ('new','paid')") + g("SELECT COUNT(*) c FROM payments WHERE status='new'")
    txt = (f"📊 Statistika\n\n👥 Foydalanuvchilar: {users}\n🔗 Referallar: {refs}\n"
           f"⭐ Foydalanuvchilar balansidagi Stars: {stars}\n💰 Foydalanuvchilar hisobidagi pul: {som(money)}\n\n"
           f"💳 Tasdiqlangan to'ldirishlar: {som(topups)}\n📦 Yuborilgan buyurtmalar: {done} ta\n⏳ Kutilayotgan: {pend} ta")
    await edit(c.message, txt, IK([BACK]))
    await c.answer()


@dp.callback_query(F.data == "a:pend", IS_ADMIN)
async def a_pend(c: CallbackQuery, bot: Bot):
    orders = q("SELECT * FROM orders WHERE status IN ('new','paid') ORDER BY id LIMIT 10")
    pays = q("SELECT * FROM payments WHERE status='new' ORDER BY id LIMIT 10")
    if not orders and not pays:
        return await c.answer("Kutilayotgan narsa yo'q 👍", show_alert=True)
    for p in pays:
        await bot.send_photo(c.from_user.id, p["file_id"], caption=f"💳 To'lov #{p['id']}\n👤 {who(U(p['uid']))}\n💰 {som(p['amount'])}",
                             reply_markup=IK([[("✅ Tasdiqlash", f"p:ok:{p['id']}"), ("❌ Rad", f"p:no:{p['id']}")]]))
    for o in orders:
        kind = "🛒 Buyurtma" if o["kind"] == "buy" else "💸 Yechish"
        await bot.send_message(c.from_user.id, f"{kind} #{o['id']}\n👤 {who(U(o['uid']))}\n⭐ {o['stars']} Stars\n\nStars'ni yuboring va «✅ Yuborildi» ni bosing.", reply_markup=order_kb(o["id"]))
    await c.answer()


# ---------- matnlarni tahrirlash ----------
PER = 8


@dp.callback_query(F.data.startswith("a:txt:"), IS_ADMIN)
async def a_txt(c: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(c.data.split(":")[2])
    keys = list(TEXTS)
    rows = [[(("✏️ " if one("SELECT 1 FROM texts WHERE k=?", k) else "") + TEXTS[k][0], f"a:t:{k}")] for k in keys[page * PER:(page + 1) * PER]]
    nav = []
    if page > 0:
        nav.append(("◀️", f"a:txt:{page - 1}"))
    if (page + 1) * PER < len(keys):
        nav.append(("▶️", f"a:txt:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(BACK)
    await edit(c.message, f"📝 Matnlar ({page + 1}/{(len(keys) - 1) // PER + 1})\n\nTahrirlamoqchi bo'lgan matnni tanlang.\n✏️ — o'zgartirilgan.", IK(rows))
    await c.answer()


def txt_view(k):
    label, _, vars_ = TEXTS[k]
    t = f"📝 {label}\n\nHozirgi matn:\n─────────\n{raw(k)}\n─────────"
    if vars_:
        t += f"\n\nO'zgaruvchilar (matn ichiga shunday yozing): {vars_}"
    return t, IK([[("✏️ O'zgartirish", f"a:te:{k}")], [("♻️ Standart holatga", f"a:tr:{k}")], [("⬅️ Matnlar", "a:txt:0")]])


@dp.callback_query(F.data.startswith("a:t:"), IS_ADMIN)
async def a_t(c: CallbackQuery, state: FSMContext):
    await state.clear()
    k = c.data[4:]
    if k in TEXTS:
        await edit(c.message, *txt_view(k))
    await c.answer()


@dp.callback_query(F.data.startswith("a:te:"), IS_ADMIN)
async def a_te(c: CallbackQuery, state: FSMContext):
    k = c.data[5:]
    if k not in TEXTS:
        return await c.answer()
    await state.set_state(Adm.text)
    await state.update_data(k=k)
    await c.message.answer("✏️ Yangi matnni yuboring. Bekor qilish: /cancel")
    await c.answer()


@dp.message(Adm.text, F.text, IS_ADMIN)
async def a_text_save(m: Message, state: FSMContext):
    k = (await state.get_data())["k"]
    await state.clear()
    q("INSERT INTO texts(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, m.text)
    await m.answer("✅ Saqlandi.")
    await show(m, txt_view(k))
    await m.answer("Menyu tugmalari yangilandi.", reply_markup=menu(m.from_user.id))


@dp.callback_query(F.data.startswith("a:tr:"), IS_ADMIN)
async def a_tr(c: CallbackQuery):
    k = c.data[5:]
    if k in TEXTS:
        q("DELETE FROM texts WHERE k=?", k)
        await edit(c.message, *txt_view(k))
    await c.answer("Standart matn qaytarildi")


# ---------- sozlamalar ----------
def set_view():
    rows = [[(f"{lab}: {(S(k) or '—').splitlines()[0][:25]}", f"a:s:{k}")] for k, (lab, _) in SETTINGS.items()]
    rows.append(BACK)
    return "⚙️ Sozlamalar\n\nO'zgartirmoqchi bo'lganingizni tanlang:", IK(rows)


@dp.callback_query(F.data == "a:set", IS_ADMIN)
async def a_set(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(c.message, *set_view())
    await c.answer()


@dp.callback_query(F.data.startswith("a:s:"), IS_ADMIN)
async def a_s(c: CallbackQuery, state: FSMContext):
    k = c.data[4:]
    if k not in SETTINGS:
        return await c.answer()
    await state.set_state(Adm.setting)
    await state.update_data(k=k)
    hint = "\n(o'chirish uchun: -)" if k in ("channel",) else ""
    await c.message.answer(f"{SETTINGS[k][0]}\nHozirgi: {S(k) or '—'}\n\nYangi qiymatni yuboring.{hint}\nBekor qilish: /cancel")
    await c.answer()


@dp.message(Adm.setting, F.text, IS_ADMIN)
async def a_set_save(m: Message, state: FSMContext):
    k, v = (await state.get_data())["k"], m.text.strip()
    if k in ("bonus", "price", "minwd", "mintopup") and not v.isdigit():
        return await m.answer("Faqat raqam yuboring.")
    if k == "packs":
        parts = [x.strip() for x in v.split(",")]
        if not all(x.isdigit() and int(x) > 0 for x in parts):
            return await m.answer("Masalan: 50,100,250,500")
        v = ",".join(parts)
    if k in ("channel", "contact") and v == "-":
        v = ""
    await state.clear()
    q("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, v)
    await m.answer("✅ Saqlandi.")
    await show(m, set_view())


# ---------- foydalanuvchini boshqarish ----------
def user_view(uid):
    u = U(uid)
    if not u:
        return None
    t = (f"👤 {who(u)}\n\n⭐ Stars: {u['stars']}\n💰 Hisob: {som(u['balance'])}\n👥 Referallar: {u['refs']}\n"
         f"🔗 Kim taklif qilgan: {u['ref_by'] or '—'}\n📅 Qo'shilgan: {time.strftime('%Y-%m-%d', time.localtime(u['joined']))}")
    return t, IK([[("⭐ Stars ±", f"a:ug:{uid}:stars"), ("💰 Hisob ±", f"a:ug:{uid}:balance")],
                  [("✉️ Xabar yuborish", f"a:um:{uid}")], BACK])


@dp.callback_query(F.data == "a:usr", IS_ADMIN)
async def a_usr(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.uid)
    await c.message.answer("👤 Foydalanuvchi ID raqamini yuboring (u botga /start bosgan bo'lishi kerak). Bekor qilish: /cancel")
    await c.answer()


@dp.message(Adm.uid, F.text, IS_ADMIN)
async def a_uid(m: Message, state: FSMContext):
    v = user_view(int(m.text)) if m.text.strip().isdigit() else None
    if not v:
        return await m.answer("Topilmadi. ID'ni tekshirib qayta yuboring yoki /cancel.")
    await state.clear()
    await show(m, v)


@dp.callback_query(F.data.startswith("a:ug:"), IS_ADMIN)
async def a_ug(c: CallbackQuery, state: FSMContext):
    _, _, uid, field = c.data.split(":")
    await state.set_state(Adm.amount)
    await state.update_data(uid=int(uid), field=field)
    unit = "⭐" if field == "stars" else "so'm"
    await c.message.answer(f"Qancha {unit} qo'shilsin? Ayirish uchun minus bilan yozing (masalan -50).\nBekor qilish: /cancel")
    await c.answer()


@dp.message(Adm.amount, F.text, IS_ADMIN)
async def a_amount(m: Message, state: FSMContext, bot: Bot):
    t = m.text.strip().replace(" ", "")
    if not t.lstrip("-").isdigit():
        return await m.answer("Faqat raqam yuboring (masalan 100 yoki -50).")
    d = await state.get_data()
    await state.clear()
    n, col = int(t), d["field"]
    if col not in ("stars", "balance"):
        return
    q(f"UPDATE users SET {col}=MAX(0,{col}+?) WHERE id=?", n, d["uid"])
    unit = f"{n:+d} ⭐" if col == "stars" else f"{n:+,} so'm".replace(",", " ")
    try:
        await bot.send_message(d["uid"], f"{'⭐ Stars balansingiz' if col == 'stars' else '💰 Hisobingiz'} {unit} ga o'zgartirildi.")
    except Exception:
        pass
    await m.answer("✅ Bajarildi.")
    await show(m, user_view(d["uid"]))


@dp.callback_query(F.data.startswith("a:um:"), IS_ADMIN)
async def a_um(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.msgto)
    await state.update_data(uid=int(c.data[5:]))
    await c.message.answer("✉️ Yubormoqchi bo'lgan xabarni yuboring (matn, rasm, video...). Bekor qilish: /cancel")
    await c.answer()


@dp.message(Adm.msgto, IS_ADMIN)
async def a_msgto(m: Message, state: FSMContext):
    uid = (await state.get_data())["uid"]
    await state.clear()
    try:
        await m.copy_to(uid)
        await m.answer("✅ Yuborildi.")
    except Exception:
        await m.answer("❌ Yuborib bo'lmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).")


# ---------- hammaga xabar yuborish ----------
@dp.callback_query(F.data == "a:bc", IS_ADMIN)
async def a_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.bc)
    await c.message.answer("📢 Hammaga yuboriladigan xabarni yuboring (matn, rasm, video — istalgani).\nAvval ko'rib chiqasiz, keyin tasdiqlaysiz. Bekor qilish: /cancel")
    await c.answer()


@dp.message(Adm.bc, IS_ADMIN)
async def a_bc_msg(m: Message, state: FSMContext, bot: Bot):
    await state.update_data(chat=m.chat.id, mid=m.message_id)
    n = one("SELECT COUNT(*) c FROM users")["c"]
    await bot.copy_message(m.chat.id, m.chat.id, m.message_id)
    await m.answer(f"☝️ Xabar shunday ko'rinadi. {n} ta foydalanuvchiga yuborilsinmi?",
                   reply_markup=IK([[("✅ Yuborish", "a:bcok"), ("❌ Bekor", "a:home")]]))


bg_tasks = set()


async def do_broadcast(bot: Bot, admin: int, chat: int, mid: int):
    ok = fail = 0
    for r in q("SELECT id FROM users"):
        try:
            await bot.copy_message(r["id"], chat, mid)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(admin, f"📢 Yuborish tugadi.\n✅ Yetib bordi: {ok} ta\n❌ Yetmadi: {fail} ta")


@dp.callback_query(F.data == "a:bcok", IS_ADMIN)
async def a_bc_ok(c: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    if "mid" not in d:
        return await c.answer("Xabar topilmadi, qaytadan boshlang.", show_alert=True)
    t = asyncio.create_task(do_broadcast(bot, c.from_user.id, d["chat"], d["mid"]))
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)
    await edit(c.message, "⏳ Yuborilmoqda... Tugagach hisobot keladi.")
    await c.answer()


# ================= ISHGA TUSHIRISH =================
async def main():
    if not TOKEN:
        raise SystemExit("Token yo'q: BOT_TOKEN o'rnating yoki fayl boshidagi TEST_TOKEN ga yozing.")
    dp.message.outer_middleware(Gate())
    dp.callback_query.outer_middleware(Gate())
    bot = Bot(TOKEN)
    await bot.delete_webhook()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
