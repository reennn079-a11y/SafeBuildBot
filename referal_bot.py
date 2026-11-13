"""💰 Referal Bot — do'stlarni taklif qilib pul ishlash.
Konstruktor BOT_TOKEN, ADMIN_IDS, PLAN_* (tarif limitlari), WEB_PORT/WEB_KEY (ixtiyoriy) muhitini beradi.
Baza: joriy papkadagi referal.db. Mini ilova (Kabinet) uchun https manzil kerak: admin panel → Sozlamalar → Web manzil."""
import asyncio, csv, hashlib, hmac, html, io, logging, os, sqlite3, time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo)
from aiohttp import web

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("referal")
TOKEN = os.environ["BOT_TOKEN"]
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()}
WEB_PORT = os.getenv("WEB_PORT")
WEB_KEY = os.getenv("WEB_KEY", "")
BOT_USERNAME = ""

# ───────────── baza ─────────────
con = sqlite3.connect("referal.db", check_same_thread=False)
con.row_factory = sqlite3.Row
con.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, ref_by INTEGER, balance INTEGER DEFAULT 0,
  earned INTEGER DEFAULT 0, withdrawn INTEGER DEFAULT 0, refs INTEGER DEFAULT 0, joined INTEGER, act_ts INTEGER DEFAULT 0,
  active INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, phone TEXT, last_bonus TEXT, streak INTEGER DEFAULT 0, noreward INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS methods(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount INTEGER, method TEXT,
  details TEXT, status TEXT DEFAULT 'new', created INTEGER);
CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, amount INTEGER, limit_n INTEGER, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS promo_uses(code TEXT, uid INTEGER, PRIMARY KEY(code, uid));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT UNIQUE, link TEXT);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount INTEGER, kind TEXT, note TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS devices(fp TEXT PRIMARY KEY, uid INTEGER);
CREATE TABLE IF NOT EXISTS verified(uid INTEGER PRIMARY KEY);
""")
con.commit()


def q(sql, *a):
    cur = con.execute(sql, a)
    con.commit()
    return cur.fetchall()


def one(sql, *a):
    r = q(sql, *a)
    return r[0] if r else None


def run(sql, *a):
    cur = con.execute(sql, a)
    con.commit()
    return cur.rowcount


DEFAULTS = dict(ref_reward="1000", ref2_reward="200", new_bonus="500", daily_bonus="100", min_withdraw="10000", min_refs="3",
                cur="so'm", protect="0", phone_check="0", codes="+998", pay_chan="", web_url=os.getenv("WEB_URL", ""))


def gs(k):
    r = one("SELECT value FROM settings WHERE key=?", k)
    return r["value"] if r else DEFAULTS.get(k, "")


def gi(k):
    try: return int(gs(k))
    except ValueError: return 0


def ss(k, v):
    q("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", k, str(v))


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


def money(n):
    return f"{fmt(n)} {gs('cur')}"


def esc(s):
    return html.escape(str(s or ""))


# ───────────── tahrirlanadigan matnlar va tugmalar ─────────────
TEXTS = {
    "btn_earn": "💰 Pul ishlash", "btn_bonus": "🎁 Kunlik bonus", "btn_promo": "🎟 Promokod", "btn_top": "🏆 Reyting",
    "btn_acc": "💵 Hisobim", "btn_wd": "💳 Pul yechish", "btn_hist": "🧾 To'lovlar tarixi", "btn_help": "☎️ Murojaat",
    "btn_cab": "📱 Kabinet", "btn_admin": "⚙️ Admin panel",
    "welcome": "👋 Salom, <b>{name}</b>!\n\nDo'stlaringizni taklif qiling va haqiqiy pul ishlang 💰\nBoshlash uchun pastdagi tugmalardan foydalaning.",
    "earn": ("💰 <b>Pul ishlash</b>\n\nHar bir do'st uchun: <b>{r1}</b>\nDo'stingiz taklif qilgan odam uchun ham: <b>{r2}</b> (2-daraja)\n\n"
             "🔗 Sizning havolangiz:\n<code>{link}</code>\n\n👥 Takliflaringiz: <b>{refs}</b>"),
    "need_sub": "📢 Davom etish uchun quyidagi kanallarga obuna bo'ling, so'ng «✅ Tekshirish» ni bosing:",
    "need_phone": "📱 Telefon raqamingizni tasdiqlang (pastdagi tugma orqali).",
    "bad_phone": "❌ Bu raqam qabul qilinmaydi (ruxsat etilgan kodlar: {codes}).",
    "dup_phone": "❌ Bu raqam boshqa akkauntga bog'langan. Bitta raqam — bitta akkaunt.",
    "need_dev": "🔒 Xavfsizlik uchun qurilmangizni tasdiqlang: «📱 Tasdiqlash» tugmasini bosing.",
    "dev_blocked": "🚫 Bu qurilmadan allaqachon boshqa akkaunt ishlatilgan.",
    "ok_in": "✅ Tasdiqlandi! Endi botdan to'liq foydalanishingiz mumkin.",
    "banned": "🚫 Siz bloklangansiz.",
    "bonus_ok": "🎁 Kunlik bonus: <b>+{amt}</b>\n🔥 Ketma-ket kun: {streak}\nHar 7-kuni bonus ikki baravar!",
    "bonus_no": "⏳ Bugungi bonusni oldingiz. Ertaga qaytib keling!",
    "promo_ask": "🎟 Promokodni kiriting:",
    "promo_ok": "✅ Promokod faollashdi: <b>+{amt}</b>",
    "promo_none": "❌ Bunday promokod yo'q.", "promo_end": "Bu promokod tugadi!", "promo_used": "❌ Siz bu kodni ishlatgansiz.",
    "acc": "💵 <b>Hisobim</b>\n\n💰 Balans: <b>{bal}</b>\n📈 Ishlagani: <b>{earned}</b>\n📤 Yechib olgani: <b>{wd}</b>\n👥 Takliflar: <b>{refs}</b>",
    "wd_low": "❌ Minimal yechish: {minw}. Balansingiz: {bal}.",
    "wd_refs": "🔒 Pul yechish uchun kamida {n} ta do'st taklif qilgan bo'lishingiz kerak (sizda: {have}).",
    "wd_nomethod": "Hozircha to'lov usullari qo'shilmagan. Keyinroq urinib ko'ring.",
    "wd_pick": "💳 To'lov usulini tanlang:", "wd_det": "Karta/hamyon raqamingizni yuboring:",
    "wd_amt": "Summani kiriting (minimal {minw}, balans {bal}) yoki «Hammasi»:",
    "wd_sent": "✅ So'rov yuborildi. Admin tasdiqlashini kuting.",
    "wd_paid": "✅ To'lov amalga oshirildi: <b>{amt}</b> ({method})", "wd_rej": "❌ So'rov rad etildi, <b>{amt}</b> balansga qaytarildi.",
    "help_ask": "☎️ Savol yoki muammoingizni yozing:", "help_sent": "✅ Xabaringiz egasiga yuborildi.",
    "pay_post": "💸 <b>Yangi to'lov!</b>\n\n👤 {name}\n💰 {amt}\n💳 {method}\n\nSiz ham do'stlaringizni taklif qilib pul ishlang 👇",
}


def T(key, **kw):
    txt = gs("t:" + key) or TEXTS[key]
    try: return txt.format(**kw)
    except Exception: return TEXTS[key].format(**kw) if kw else TEXTS[key]


class Btn(BaseFilter):
    def __init__(self, key): self.key = key

    async def __call__(self, m: Message) -> bool:
        return bool(m.text) and m.text == T(self.key)


def web_url():
    return (gs("web_url") or "").rstrip("/")


def sig(uid):
    return hmac.new(TOKEN.encode(), str(uid).encode(), hashlib.sha256).hexdigest()[:16]


def menu(uid):
    rows = [[T("btn_earn"), T("btn_bonus")], [T("btn_acc"), T("btn_wd")], [T("btn_promo"), T("btn_top")], [T("btn_hist"), T("btn_help")]]
    if web_url():
        rows.append([KeyboardButton(text=T("btn_cab"), web_app=WebAppInfo(url=f"{web_url()}/app?uid={uid}&sig={sig(uid)}"))])
    if uid in ADMINS:
        rows.append([T("btn_admin")])
    return ReplyKeyboardMarkup(keyboard=[[b if isinstance(b, KeyboardButton) else KeyboardButton(text=b) for b in r] for r in rows],
                               resize_keyboard=True)


# ───────────── hisob-kitob ─────────────
def U(uid): return one("SELECT * FROM users WHERE id=?", uid)


def credit(uid, amt, kind, note=""):
    if not amt: return
    run("UPDATE users SET balance=balance+? WHERE id=?", amt, uid)
    if amt > 0 and kind in ("ref", "ref2", "bonus", "daily", "promo"):
        run("UPDATE users SET earned=earned+? WHERE id=?", amt, uid)
    run("INSERT INTO ledger(uid,amount,kind,note,ts) VALUES(?,?,?,?,?)", uid, amt, kind, note, int(time.time()))


async def activate(bot: Bot, uid):
    """Foydalanuvchi barcha tekshiruvdan o'tganda: bonus va referal mukofotlarini BIR MARTA beradi."""
    if not run("UPDATE users SET active=1, act_ts=? WHERE id=? AND active=0", int(time.time()), uid):
        return
    u = U(uid)
    credit(uid, gi("new_bonus"), "bonus", "Xush kelibsiz bonusi")
    ref = u["ref_by"]
    if not ref or u["noreward"]: return
    r = U(ref)
    if not r or r["banned"]: return
    credit(ref, gi("ref_reward"), "ref", f"Do'st: {u['name']}")
    run("UPDATE users SET refs=refs+1 WHERE id=?", ref)
    try: await bot.send_message(ref, f"🎉 Yangi do'st: <b>{esc(u['name'])}</b>\n💰 +{money(gi('ref_reward'))}")
    except Exception: pass
    r2 = r["ref_by"]
    if r2 and gi("ref2_reward") and (U(r2) or {"banned": 1})["banned"] == 0:
        credit(r2, gi("ref2_reward"), "ref2", f"2-daraja: {u['name']}")
        try: await bot.send_message(r2, f"👥 2-daraja: <b>{esc(u['name'])}</b>\n💰 +{money(gi('ref2_reward'))}")
        except Exception: pass


async def unsubscribed(bot: Bot, uid):
    bad = []
    for c in q("SELECT * FROM channels"):
        try:
            st = (await bot.get_chat_member(c["chat"], uid)).status
            if st in ("left", "kicked"): bad.append(c)
        except Exception:
            continue                       # bot kanalda admin emas — o'tkazib yuboramiz
    return bad


async def gate(bot: Bot, m: Message, fu=None) -> bool:
    """True — foydalanuvchi to'liq o'tdi. Aks holda kerakli qadamni ko'rsatadi."""
    uid = (fu or m.from_user).id
    u = U(uid)
    if u["banned"]:
        await m.answer(T("banned"), reply_markup=ReplyKeyboardRemove()); return False
    if u["active"] or uid in ADMINS: return True
    if u["noreward"] == 2 and gs("protect") == "2":
        await m.answer(T("dev_blocked")); return False
    bad = await unsubscribed(bot, uid)
    if bad:
        kb = [[InlineKeyboardButton(text="📢 Obuna bo'lish", url=c["link"])] for c in bad]
        kb.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="chk")])
        await m.answer(T("need_sub"), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); return False
    if gs("phone_check") == "1" and not u["phone"]:
        await m.answer(T("need_phone"), reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]], resize_keyboard=True)); return False
    if gs("protect") != "0" and web_url() and not one("SELECT 1 FROM verified WHERE uid=?", uid):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="📱 Tasdiqlash", web_app=WebAppInfo(url=f"{web_url()}/app?uid={uid}&sig={sig(uid)}"))],
            [InlineKeyboardButton(text="✅ Tekshirish", callback_data="chk")]])
        await m.answer(T("need_dev"), reply_markup=kb); return False
    await activate(bot, uid)
    await m.answer(T("ok_in"), reply_markup=menu(uid))
    return True


# ───────────── holatlar ─────────────
class S(StatesGroup):
    promo = State(); wd_det = State(); wd_amt = State(); help = State(); reply = State()
    setv = State(); addm = State(); addc = State(); newp = State(); user = State(); bc = State(); text = State(); amt = State()


router = Router()


async def entry(m: Message, state: FSMContext, bot: Bot) -> bool:
    await state.clear()
    return await gate(bot, m)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uid = m.from_user.id
    if not U(uid):
        parts = (m.text or "").split(maxsplit=1)
        ref = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        if ref == uid or not (ref and U(ref)): ref = None
        q("INSERT INTO users(id,name,ref_by,joined) VALUES(?,?,?,?)", uid, m.from_user.full_name, ref, int(time.time()))
    if await gate(bot, m):
        await m.answer(T("welcome", name=esc(m.from_user.full_name)), reply_markup=menu(uid))


@router.callback_query(F.data == "chk")
async def chk(c: CallbackQuery, bot: Bot):
    await c.answer()
    if U(c.from_user.id) is None: return
    try: await c.message.delete()
    except Exception: pass
    await gate(bot, c.message, c.from_user)


@router.message(F.contact)
async def contact(m: Message, bot: Bot):
    u = U(m.from_user.id)
    if not u or u["phone"] or m.contact.user_id != m.from_user.id: return
    ph = "+" + "".join(ch for ch in m.contact.phone_number if ch.isdigit())
    codes = [x.strip() for x in gs("codes").split(",") if x.strip()]
    if codes and not any(ph.startswith(c) for c in codes):
        return await m.answer(T("bad_phone", codes=", ".join(codes)))
    if one("SELECT 1 FROM users WHERE phone=?", ph):
        return await m.answer(T("dup_phone"))
    run("UPDATE users SET phone=? WHERE id=?", ph, m.from_user.id)
    await gate(bot, m)


# ───────────── foydalanuvchi bo'limlari ─────────────
@router.message(Btn("btn_earn"))
async def earn(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    u = U(m.from_user.id)
    await m.answer(T("earn", r1=money(gi("ref_reward")), r2=money(gi("ref2_reward")),
                     link=f"https://t.me/{BOT_USERNAME}?start={m.from_user.id}", refs=u["refs"]))


@router.message(Btn("btn_acc"))
async def acc(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    u = U(m.from_user.id)
    await m.answer(T("acc", bal=money(u["balance"]), earned=money(u["earned"]), wd=money(u["withdrawn"]), refs=u["refs"]))


@router.message(Btn("btn_bonus"))
async def bonus(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    u = U(m.from_user.id)
    today = time.strftime("%Y-%m-%d")
    if u["last_bonus"] == today: return await m.answer(T("bonus_no"))
    yest = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    streak = u["streak"] + 1 if u["last_bonus"] == yest else 1
    amt = gi("daily_bonus") * (2 if streak % 7 == 0 else 1)
    if not run("UPDATE users SET last_bonus=?, streak=? WHERE id=? AND (last_bonus IS NULL OR last_bonus<>?)", today, streak, u["id"], today):
        return await m.answer(T("bonus_no"))
    credit(u["id"], amt, "daily", f"Kunlik bonus ({streak}-kun)")
    await m.answer(T("bonus_ok", amt=money(amt), streak=streak))


@router.message(Btn("btn_promo"))
async def promo(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    await state.set_state(S.promo)
    await m.answer(T("promo_ask"))


@router.message(S.promo)
async def promo_in(m: Message, state: FSMContext):
    await state.clear()
    code = (m.text or "").strip().upper()
    r = one("SELECT * FROM promos WHERE code=?", code)
    if not r: return await m.answer(T("promo_none"))
    if one("SELECT 1 FROM promo_uses WHERE code=? AND uid=?", code, m.from_user.id): return await m.answer(T("promo_used"))
    if not run("UPDATE promos SET used=used+1 WHERE code=? AND used<limit_n", code): return await m.answer(T("promo_end"))
    run("INSERT INTO promo_uses(code,uid) VALUES(?,?)", code, m.from_user.id)
    credit(m.from_user.id, r["amount"], "promo", f"Promokod {code}")
    await m.answer(T("promo_ok", amt=money(r["amount"])))


@router.message(Btn("btn_top"))
async def top(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    week = q("SELECT ref_by AS id, COUNT(*) AS n FROM users WHERE active=1 AND ref_by IS NOT NULL AND act_ts>? GROUP BY ref_by "
             "ORDER BY n DESC LIMIT 10", int(time.time()) - 7 * 86400)
    allt = q("SELECT id, name, refs AS n FROM users WHERE refs>0 ORDER BY refs DESC LIMIT 10")
    medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7

    def nm(i):
        r = U(i); return esc(r["name"]) if r else str(i)
    t = "🏆 <b>Bu hafta</b>\n" + ("\n".join(f"{medals[i]} {nm(r['id'])} — {r['n']}" for i, r in enumerate(week)) or "—")
    t += "\n\n🏆 <b>Hamma vaqt</b>\n" + ("\n".join(f"{medals[i]} {esc(r['name'])} — {r['n']}" for i, r in enumerate(allt)) or "—")
    await m.answer(t)


@router.message(Btn("btn_hist"))
async def hist(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    rows = q("SELECT * FROM ledger WHERE uid=? ORDER BY id DESC LIMIT 15", m.from_user.id)
    wd = q("SELECT * FROM withdrawals WHERE uid=? ORDER BY id DESC LIMIT 5", m.from_user.id)
    st = {"new": "⏳", "paid": "✅", "rej": "❌"}
    t = "🧾 <b>To'lovlar tarixi</b>\n\n"
    t += "\n".join(f"{st[w['status']]} Yechish #{w['id']}: {money(w['amount'])} ({esc(w['method'])})" for w in wd) + "\n\n" if wd else ""
    t += "\n".join(f"{time.strftime('%d.%m %H:%M', time.localtime(r['ts']))}  {'+' if r['amount'] > 0 else ''}{fmt(r['amount'])}  {esc(r['note'])}"
                   for r in rows) or "Hozircha bo'sh."
    await m.answer(t)


@router.message(Btn("btn_cab"))
async def cab_fallback(m: Message, state: FSMContext, bot: Bot):
    """Mini ilova manzili berilmagan bo'lsa (tugma web_app bo'lmasa) — matnli kabinet."""
    if not await entry(m, state, bot): return
    u = U(m.from_user.id)
    await m.answer(f"📱 <b>Kabinet</b>\n\n💰 {money(u['balance'])}\n👥 {u['refs']}\n🔗 https://t.me/{BOT_USERNAME}?start={u['id']}")


# ── pul yechish ──
@router.message(Btn("btn_wd"))
async def wd(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    u = U(m.from_user.id)
    if u["refs"] < gi("min_refs"): return await m.answer(T("wd_refs", n=gi("min_refs"), have=u["refs"]))
    if u["balance"] < gi("min_withdraw"): return await m.answer(T("wd_low", minw=money(gi("min_withdraw")), bal=money(u["balance"])))
    ms = q("SELECT * FROM methods")
    if not ms: return await m.answer(T("wd_nomethod"))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x["title"], callback_data=f"wm:{x['id']}")] for x in ms])
    await m.answer(T("wd_pick"), reply_markup=kb)


@router.callback_query(F.data.startswith("wm:"))
async def wd_method(c: CallbackQuery, state: FSMContext):
    r = one("SELECT * FROM methods WHERE id=?", int(c.data[3:]))
    if not r: return await c.answer()
    await state.set_state(S.wd_det); await state.update_data(method=r["title"])
    await c.message.answer(T("wd_det")); await c.answer()


@router.message(S.wd_det)
async def wd_details(m: Message, state: FSMContext):
    await state.update_data(details=(m.text or "").strip()[:200]); await state.set_state(S.wd_amt)
    u = U(m.from_user.id)
    await m.answer(T("wd_amt", minw=money(gi("min_withdraw")), bal=money(u["balance"])),
                   reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Hammasi")]], resize_keyboard=True, one_time_keyboard=True))


@router.message(S.wd_amt)
async def wd_amount(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data(); u = U(m.from_user.id)
    txt = (m.text or "").strip().lower()
    amt = u["balance"] if txt == "hammasi" else int("".join(ch for ch in txt if ch.isdigit()) or 0)
    if amt < gi("min_withdraw") or amt > u["balance"]:
        return await m.answer(T("wd_amt", minw=money(gi("min_withdraw")), bal=money(u["balance"])))
    if not run("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", amt, u["id"], amt): return
    wid = q("INSERT INTO withdrawals(uid,amount,method,details,created) VALUES(?,?,?,?,?) RETURNING id", u["id"], amt, d["method"], d["details"], int(time.time()))[0][0]
    run("INSERT INTO ledger(uid,amount,kind,note,ts) VALUES(?,?,?,?,?)", u["id"], -amt, "wd", f"Yechish #{wid}", int(time.time()))
    await state.clear()
    await m.answer(T("wd_sent"), reply_markup=menu(u["id"]))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ To'landi", callback_data=f"wp:{wid}"),
                                                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"wr:{wid}")]])
    for a in ADMINS:
        try: await bot.send_message(a, f"💳 <b>Yechish #{wid}</b>\n👤 {esc(u['name'])} (<code>{u['id']}</code>)\n💰 {money(amt)}\n"
                                       f"📲 {esc(d['method'])}: <code>{esc(d['details'])}</code>", reply_markup=kb)
        except Exception: pass


async def resolve_wd(c: CallbackQuery, bot: Bot, ok: bool):
    if c.from_user.id not in ADMINS: return await c.answer()
    w = one("SELECT * FROM withdrawals WHERE id=?", int(c.data[3:]))
    if not w or not run("UPDATE withdrawals SET status=? WHERE id=? AND status='new'", "paid" if ok else "rej", w["id"]):
        return await c.answer("Allaqachon hal qilingan", show_alert=True)
    u = U(w["uid"])
    if ok:
        run("UPDATE users SET withdrawn=withdrawn+? WHERE id=?", w["amount"], w["uid"])
        try: await bot.send_message(w["uid"], T("wd_paid", amt=money(w["amount"]), method=esc(w["method"])))
        except Exception: pass
        ch = gs("pay_chan")
        if ch:
            try: await bot.send_message(ch, T("pay_post", name=esc(u["name"]), amt=money(w["amount"]), method=esc(w["method"])),
                                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                                            text="💰 Pul ishlash", url=f"https://t.me/{BOT_USERNAME}")]]))
            except Exception: log.warning("to'lovlar kanaliga yuborib bo'lmadi")
    else:
        credit(w["uid"], w["amount"], "refund", f"Yechish #{w['id']} rad etildi")
        try: await bot.send_message(w["uid"], T("wd_rej", amt=money(w["amount"])))
        except Exception: pass
    await c.message.edit_text(c.message.html_text + ("\n\n✅ To'landi" if ok else "\n\n❌ Rad etildi"))
    await c.answer()


@router.callback_query(F.data.startswith("wp:"))
async def wd_ok(c: CallbackQuery, bot: Bot): await resolve_wd(c, bot, True)


@router.callback_query(F.data.startswith("wr:"))
async def wd_no(c: CallbackQuery, bot: Bot): await resolve_wd(c, bot, False)


# ── murojaat ──
@router.message(Btn("btn_help"))
async def help_(m: Message, state: FSMContext, bot: Bot):
    if not await entry(m, state, bot): return
    await state.set_state(S.help); await m.answer(T("help_ask"))


@router.message(S.help)
async def help_in(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✍️ Javob berish", callback_data=f"rp:{m.from_user.id}")]])
    for a in ADMINS:
        try:
            await bot.send_message(a, f"📩 <b>Murojaat</b> — {esc(m.from_user.full_name)} (<code>{m.from_user.id}</code>)", reply_markup=kb)
            await m.copy_to(a)
        except Exception: pass
    await m.answer(T("help_sent"), reply_markup=menu(m.from_user.id))


@router.callback_query(F.data.startswith("rp:"))
async def reply_btn(c: CallbackQuery, state: FSMContext):
    if c.from_user.id not in ADMINS: return await c.answer()
    await state.set_state(S.reply); await state.update_data(to=int(c.data[3:]))
    await c.message.answer("✍️ Javob matnini yuboring:"); await c.answer()


@router.message(S.reply)
async def reply_send(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data(); await state.clear()
    try: await bot.send_message(d["to"], "📩 <b>Egadan javob:</b>"); await m.copy_to(d["to"]); await m.answer("✅ Yuborildi")
    except Exception: await m.answer("❌ Yuborib bo'lmadi")


# ───────────── ADMIN PANEL ─────────────
class IsAdmin(BaseFilter):
    async def __call__(self, e) -> bool: return bool(e.from_user) and e.from_user.id in ADMINS


def kb(rows): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


ADMIN_KB = kb([[("📊 Statistika", "a:stat"), ("⚙️ Sozlamalar", "a:set")],
               [("💳 To'lov tizimlari", "a:meth"), ("💸 Yechish so'rovlari", "a:wds")],
               [("🎟 Promokodlar", "a:promo"), ("👤 Foydalanuvchi", "a:user")],
               [("📢 Majburiy obuna", "a:chan"), ("✏️ Matnlar", "a:txt")],
               [("↪️ Xabar yuborish", "a:bc"), ("📞 Telefonlar fayli", "a:ph")],
               [("🌐 Web panel", "a:web")]])
SETS = [("ref_reward", "Referal mukofoti"), ("ref2_reward", "2-daraja mukofot"), ("new_bonus", "Yangi odam bonusi"),
        ("daily_bonus", "Kunlik bonus"), ("min_withdraw", "Minimal yechish"), ("min_refs", "Yechish uchun min. referal"),
        ("cur", "Valyuta nomi"), ("codes", "Ruxsat etilgan kodlar (+998,+7)"), ("pay_chan", "To'lovlar kanali (@kanal yoki ID)"),
        ("web_url", "Web manzil (https://...)")]
PROT = ["Hammaga ruxsat", "1 qurilma = 1 referal", "Qattiq blok (1 qurilma = 1 akkaunt)"]


@router.message(Btn("btn_admin"), IsAdmin())
@router.message(Command("admin"), IsAdmin())
async def admin(m: Message, state: FSMContext):
    await state.clear(); await m.answer("⚙️ <b>Admin panel</b>", reply_markup=ADMIN_KB)


@router.callback_query(F.data == "a:home", IsAdmin())
async def a_home(c: CallbackQuery, state: FSMContext):
    await state.clear(); await c.message.edit_text("⚙️ <b>Admin panel</b>", reply_markup=ADMIN_KB); await c.answer()


def stats_text():
    n = lambda s, *a: q(s, *a)[0][0] or 0
    top = q("SELECT name, refs FROM users WHERE refs>0 ORDER BY refs DESC LIMIT 5")
    return (f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {n('SELECT COUNT(*) FROM users')} (faol: {n('SELECT COUNT(*) FROM users WHERE active=1')})\n"
            f"🔗 Referallar: {n('SELECT COUNT(*) FROM users WHERE active=1 AND ref_by IS NOT NULL')}\n"
            f"💸 To'langan: {money(n('SELECT SUM(amount) FROM withdrawals WHERE status=?', 'paid'))}\n"
            f"⏳ Kutayotgan: {n('SELECT COUNT(*) FROM withdrawals WHERE status=?', 'new')} ta\n"
            f"💰 Balanslar jami: {money(n('SELECT SUM(balance) FROM users'))}\n\n🏆 Eng yaxshi taklifchilar:\n"
            + ("\n".join(f"{i + 1}. {esc(r['name'])} — {r['refs']}" for i, r in enumerate(top)) or "—"))


@router.callback_query(F.data == "a:stat", IsAdmin())
async def a_stat(c: CallbackQuery):
    await c.message.edit_text(stats_text(), reply_markup=kb([[("⬅️ Orqaga", "a:home")]])); await c.answer()


@router.callback_query(F.data == "a:set", IsAdmin())
async def a_set(c: CallbackQuery):
    rows = [[(f"{lbl}: {gs(k) or '—'}", f"s:{k}")] for k, lbl in SETS]
    rows.append([(f"🛡 Himoya: {PROT[gi('protect')]}", "s:protect")])
    rows.append([(f"📱 Telefon tekshiruvi: {'✅' if gs('phone_check') == '1' else '❌'}", "s:phone")])
    rows.append([("⬅️ Orqaga", "a:home")])
    note = "" if web_url() else "\n\n⚠️ Qurilma tekshiruvi (1 qurilma = 1 referal) Mini ilova uchun «Web manzil» (https) ni talab qiladi."
    await c.message.edit_text("⚙️ <b>Sozlamalar</b>" + note, reply_markup=kb(rows)); await c.answer()


@router.callback_query(F.data.in_({"s:protect", "s:phone"}), IsAdmin())
async def a_toggle(c: CallbackQuery):
    if c.data == "s:protect": ss("protect", (gi("protect") + 1) % 3)
    else: ss("phone_check", "0" if gs("phone_check") == "1" else "1")
    await a_set(c)


@router.callback_query(F.data.startswith("s:"), IsAdmin())
async def a_setv(c: CallbackQuery, state: FSMContext):
    k = c.data[2:]
    await state.set_state(S.setv); await state.update_data(k=k)
    await c.message.answer(f"Yangi qiymatni yuboring ({dict(SETS)[k]}). Bo'sh qoldirish uchun «-» yuboring."); await c.answer()


@router.message(S.setv, IsAdmin())
async def a_setv_in(m: Message, state: FSMContext):
    k = (await state.get_data())["k"]; v = (m.text or "").strip()
    v = "" if v == "-" else v
    if k in ("ref_reward", "ref2_reward", "new_bonus", "daily_bonus", "min_withdraw", "min_refs") and not v.isdigit():
        return await m.answer("❌ Faqat raqam yuboring.")
    ss(k, v); await state.clear(); await m.answer("✅ Saqlandi. /admin", reply_markup=menu(m.from_user.id))


# ── to'lov tizimlari ──
@router.callback_query(F.data == "a:meth", IsAdmin())
async def a_meth(c: CallbackQuery):
    rows = [[(f"🗑 {x['title']}", f"md:{x['id']}")] for x in q("SELECT * FROM methods")]
    rows += [[("➕ Qo'shish (Uzcard/Humo, Payme, USDT...)", "ma")], [("⬅️ Orqaga", "a:home")]]
    await c.message.edit_text("💳 <b>To'lov tizimlari</b>", reply_markup=kb(rows)); await c.answer()


@router.callback_query(F.data == "ma", IsAdmin())
async def a_meth_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.addm); await c.message.answer("To'lov tizimi nomini yuboring:"); await c.answer()


@router.message(S.addm, IsAdmin())
async def a_meth_in(m: Message, state: FSMContext):
    run("INSERT OR IGNORE INTO methods(title) VALUES(?)", (m.text or "").strip()[:40]); await state.clear(); await m.answer("✅ Qo'shildi. /admin")


@router.callback_query(F.data.startswith("md:"), IsAdmin())
async def a_meth_del(c: CallbackQuery):
    run("DELETE FROM methods WHERE id=?", int(c.data[3:])); await a_meth(c)


# ── yechish so'rovlari ──
@router.callback_query(F.data == "a:wds", IsAdmin())
async def a_wds(c: CallbackQuery, bot: Bot):
    rows = q("SELECT * FROM withdrawals WHERE status='new' ORDER BY id LIMIT 10")
    await c.answer()
    if not rows: return await c.message.edit_text("Kutayotgan so'rov yo'q.", reply_markup=kb([[("⬅️ Orqaga", "a:home")]]))
    for w in rows:
        u = U(w["uid"])
        k = kb([[("✅ To'landi", f"wp:{w['id']}"), ("❌ Rad etish", f"wr:{w['id']}")]])
        await c.message.answer(f"💳 <b>#{w['id']}</b> {esc(u['name'])} (<code>{u['id']}</code>)\n💰 {money(w['amount'])}\n"
                               f"📲 {esc(w['method'])}: <code>{esc(w['details'])}</code>", reply_markup=k)


# ── promokodlar ──
@router.callback_query(F.data == "a:promo", IsAdmin())
async def a_promo(c: CallbackQuery):
    rows = q("SELECT * FROM promos ORDER BY rowid DESC LIMIT 20")
    t = "🎟 <b>Promokodlar</b>\n\n" + ("\n".join(f"<code>{r['code']}</code> — {money(r['amount'])} — {r['used']}/{r['limit_n']}"
                                              + (" (tugadi)" if r["used"] >= r["limit_n"] else "") for r in rows) or "Hali yo'q.")
    await c.message.edit_text(t, reply_markup=kb([[("➕ Yangi", "pn"), ("🗑 O'chirish", "pd")], [("⬅️ Orqaga", "a:home")]])); await c.answer()


@router.callback_query(F.data == "pn", IsAdmin())
async def a_promo_new(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.newp); await c.message.answer("Format: <code>KOD SUMMA LIMIT</code>\nMasalan: <code>YANGI100 500 50</code>"); await c.answer()


@router.message(S.newp, IsAdmin())
async def a_promo_in(m: Message, state: FSMContext):
    p = (m.text or "").split()
    if len(p) != 3 or not (p[1].isdigit() and p[2].isdigit()): return await m.answer("❌ Format noto'g'ri.")
    run("INSERT OR REPLACE INTO promos(code,amount,limit_n,used) VALUES(?,?,?,0)", p[0].upper(), int(p[1]), int(p[2]))
    await state.clear(); await m.answer("✅ Promokod yaratildi. /admin")


@router.callback_query(F.data == "pd", IsAdmin())
async def a_promo_del(c: CallbackQuery):
    rows = [[(f"🗑 {r['code']}", f"pk:{r['code']}")] for r in q("SELECT code FROM promos LIMIT 30")] + [[("⬅️ Orqaga", "a:promo")]]
    await c.message.edit_text("O'chiriladigan kodni tanlang:", reply_markup=kb(rows)); await c.answer()


@router.callback_query(F.data.startswith("pk:"), IsAdmin())
async def a_promo_kill(c: CallbackQuery):
    run("DELETE FROM promos WHERE code=?", c.data[3:]); await a_promo(c)


# ── majburiy obuna ──
@router.callback_query(F.data == "a:chan", IsAdmin())
async def a_chan(c: CallbackQuery):
    rows = [[(f"🗑 {x['chat']}", f"cd:{x['id']}")] for x in q("SELECT * FROM channels")] + [[("➕ Kanal qo'shish", "ca")], [("⬅️ Orqaga", "a:home")]]
    await c.message.edit_text("📢 <b>Majburiy obuna</b>\nBot kanalda admin bo'lishi shart.", reply_markup=kb(rows)); await c.answer()


@router.callback_query(F.data == "ca", IsAdmin())
async def a_chan_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.addc); await c.message.answer("Kanalni yuboring: <code>@kanal</code> yoki <code>@kanal https://t.me/+havola</code> (yopiq kanal uchun ID va havola)"); await c.answer()


@router.message(S.addc, IsAdmin())
async def a_chan_in(m: Message, state: FSMContext):
    p = (m.text or "").split()
    if not p: return
    link = p[1] if len(p) > 1 else f"https://t.me/{p[0].lstrip('@')}"
    run("INSERT OR REPLACE INTO channels(chat,link) VALUES(?,?)", p[0], link); await state.clear(); await m.answer("✅ Qo'shildi. /admin")


@router.callback_query(F.data.startswith("cd:"), IsAdmin())
async def a_chan_del(c: CallbackQuery):
    run("DELETE FROM channels WHERE id=?", int(c.data[3:])); await a_chan(c)


# ── foydalanuvchini boshqarish ──
@router.callback_query(F.data == "a:user", IsAdmin())
async def a_user(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.user); await c.message.answer("Foydalanuvchi ID sini yuboring:"); await c.answer()


def user_card(u):
    return (f"👤 <b>{esc(u['name'])}</b> (<code>{u['id']}</code>)\n💰 {money(u['balance'])} | 📈 {money(u['earned'])} | 📤 {money(u['withdrawn'])}\n"
            f"👥 Takliflar: {u['refs']} | Taklif qilgan: {u['ref_by'] or '—'}\n📱 {u['phone'] or '—'}\n{'🚫 BLOKLANGAN' if u['banned'] else '✅ Faol'}")


def user_kb(u):
    return kb([[("➕ Balans qo'shish", f"ub:{u['id']}:1"), ("➖ Ayirish", f"ub:{u['id']}:-1")],
               [("✅ Blokdan chiqarish" if u["banned"] else "🚫 Bloklash", f"ux:{u['id']}")]])


@router.message(S.user, IsAdmin())
async def a_user_in(m: Message, state: FSMContext):
    await state.clear()
    u = U(int(m.text)) if (m.text or "").strip().isdigit() else None
    if not u: return await m.answer("❌ Topilmadi.")
    await m.answer(user_card(u), reply_markup=user_kb(u))


@router.callback_query(F.data.startswith("ux:"), IsAdmin())
async def a_ban(c: CallbackQuery):
    uid = int(c.data[3:]); run("UPDATE users SET banned=1-banned WHERE id=?", uid)
    u = U(uid); await c.message.edit_text(user_card(u), reply_markup=user_kb(u)); await c.answer()


@router.callback_query(F.data.startswith("ub:"), IsAdmin())
async def a_bal(c: CallbackQuery, state: FSMContext):
    _, uid, sign = c.data.split(":")
    await state.set_state(S.amt); await state.update_data(uid=int(uid), sign=int(sign))
    await c.message.answer("Summani yuboring:"); await c.answer()


@router.message(S.amt, IsAdmin())
async def a_bal_in(m: Message, state: FSMContext):
    d = await state.get_data()
    if not (m.text or "").strip().isdigit(): return await m.answer("❌ Raqam yuboring.")
    credit(d["uid"], int(m.text) * d["sign"], "admin", "Admin tomonidan"); await state.clear()
    await m.answer(user_card(U(d["uid"])), reply_markup=user_kb(U(d["uid"])))


# ── xabar yuborish ──
@router.callback_query(F.data == "a:bc", IsAdmin())
async def a_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.bc); await c.message.answer("Hamma foydalanuvchilarga yuboriladigan xabarni yuboring:"); await c.answer()


@router.message(S.bc, IsAdmin())
async def a_bc_in(m: Message, state: FSMContext, bot: Bot):
    await state.clear(); ok = bad = 0
    await m.answer("⏳ Yuborilmoqda...")
    for r in q("SELECT id FROM users WHERE banned=0"):
        try: await m.copy_to(r["id"]); ok += 1
        except Exception: bad += 1
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Yuborildi: {ok}\n❌ Xato: {bad}")


# ── matnlar ──
@router.callback_query(F.data == "a:txt", IsAdmin())
async def a_txt(c: CallbackQuery):
    rows = [[(k, f"tx:{k}")] for k in TEXTS]
    await c.message.edit_text("✏️ Tahrirlanadigan matn yoki tugmani tanlang:", reply_markup=kb(rows + [[("⬅️ Orqaga", "a:home")]])); await c.answer()


@router.callback_query(F.data.startswith("tx:"), IsAdmin())
async def a_txt_pick(c: CallbackQuery, state: FSMContext):
    k = c.data[3:]
    await state.set_state(S.text); await state.update_data(k=k)
    await c.message.answer(f"Hozirgi:\n<code>{esc(gs('t:' + k) or TEXTS[k])}</code>\n\nYangi matnni yuboring ({{...}} o'rinbosarlarni saqlang). Asliga qaytarish: «-»"); await c.answer()


@router.message(S.text, IsAdmin())
async def a_txt_in(m: Message, state: FSMContext):
    k = (await state.get_data())["k"]; v = m.html_text.strip()
    ss("t:" + k, "" if v == "-" else v); await state.clear(); await m.answer("✅ Saqlandi.", reply_markup=menu(m.from_user.id))


# ── telefonlar, web ──
@router.callback_query(F.data == "a:ph", IsAdmin())
async def a_ph(c: CallbackQuery):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["id", "ism", "telefon"])
    for r in q("SELECT id,name,phone FROM users WHERE phone IS NOT NULL"): w.writerow([r["id"], r["name"], r["phone"]])
    await c.message.answer_document(BufferedInputFile(buf.getvalue().encode("utf-8-sig"), "telefonlar.csv")); await c.answer()


@router.callback_query(F.data == "a:web", IsAdmin())
async def a_web(c: CallbackQuery):
    if not web_url() or not WEB_KEY:
        return await c.answer("Web panel uchun Web manzil va WEB_PORT kerak.", show_alert=True)
    await c.message.answer(f"🌐 Web panel:\n{web_url()}/panel?key={WEB_KEY}"); await c.answer()


# ───────────── Mini ilova va web panel ─────────────
PAGE = ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<script src='https://telegram.org/js/telegram-web-app.js'></script><style>:root{{color-scheme:light dark}}"
        "body{{font-family:system-ui;margin:0;padding:16px;background:#0f172a;color:#f1f5f9}}.c{{background:#1e293b;border-radius:16px;padding:16px;margin:12px 0}}"
        "h1{{font-size:34px;margin:4px 0}}small{{color:#94a3b8}}a,code{{word-break:break-all;color:#38bdf8}}td{{padding:4px 8px}}</style></head><body>{body}</body></html>")


def cabinet(uid):
    u = U(uid)
    top = q("SELECT name, refs FROM users WHERE refs>0 ORDER BY refs DESC LIMIT 5")
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    body = (f"<div class=c><small>💰 Balans</small><h1>{esc(money(u['balance']))}</h1><small>📈 Ishlagani: {esc(money(u['earned']))} · "
            f"📤 Yechilgani: {esc(money(u['withdrawn']))}</small></div><div class=c><small>🔗 Havolangiz</small><br><code>{link}</code>"
            f"<br><small>👥 Takliflar: {u['refs']} · 🔥 Bonus seriyasi: {u['streak']} kun</small></div>"
            "<div class=c><small>🏆 Reyting</small><table>" + "".join(f"<tr><td>{i + 1}</td><td>{esc(r['name'])}</td><td>{r['refs']}</td></tr>" for i, r in enumerate(top))
            + "</table></div><small>Botdagi tugmalar orqali bonus oling va pul yeching.</small>"
            "<script>(async()=>{try{const t=Telegram.WebApp;t.ready();const s=[navigator.userAgent,screen.width+'x'+screen.height,screen.colorDepth,"
            "navigator.language,Intl.DateTimeFormat().resolvedOptions().timeZone,navigator.hardwareConcurrency||0,navigator.platform].join('|');"
            "const b=new TextEncoder().encode(s);const h=await crypto.subtle.digest('SHA-256',b);const fp=[...new Uint8Array(h)].map(x=>x.toString(16).padStart(2,'0')).join('');"
            f"await fetch('/api/device',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{uid:{uid},sig:'{sig(uid)}',fp:fp}})}});}}catch(e){{}}}})();</script>")
    return PAGE.format(body=body)


async def h_app(req: web.Request):
    try: uid = int(req.query.get("uid", "0"))
    except ValueError: return web.Response(status=400)
    if not hmac.compare_digest(req.query.get("sig", ""), sig(uid)) or not U(uid): return web.Response(status=403, text="403")
    return web.Response(text=cabinet(uid), content_type="text/html")


async def h_device(req: web.Request):
    """Qurilma barmoq izi: 1 qurilma = 1 referal (daraja 1) yoki 1 qurilma = 1 akkaunt (daraja 2)."""
    d = await req.json()
    uid = int(d.get("uid", 0)); fp = str(d.get("fp", ""))[:64]
    if not hmac.compare_digest(str(d.get("sig", "")), sig(uid)) or not U(uid) or len(fp) < 16: return web.Response(status=403)
    other = one("SELECT uid FROM devices WHERE fp=?", fp)
    if other and other["uid"] != uid:
        if gi("protect") == 2:
            run("UPDATE users SET noreward=2 WHERE id=? AND active=0", uid)   # faollashtirish bloklanadi
            return web.json_response({"ok": False})
        run("UPDATE users SET noreward=1 WHERE id=? AND active=0 AND noreward=0", uid)   # daraja 1: referal mukofoti berilmaydi
    else:
        run("INSERT OR IGNORE INTO devices(fp,uid) VALUES(?,?)", fp, uid)
    run("INSERT OR IGNORE INTO verified(uid) VALUES(?)", uid)
    return web.json_response({"ok": True})


async def h_panel(req: web.Request):
    if not WEB_KEY or not hmac.compare_digest(req.query.get("key", ""), WEB_KEY): return web.Response(status=403, text="403")
    wds = q("SELECT * FROM withdrawals WHERE status='new' ORDER BY id LIMIT 30")
    body = (f"<div class=c>{esc(stats_text()).replace(chr(10), '<br>')}</div><div class=c><b>⏳ Kutayotgan so'rovlar</b><table>"
            + "".join(f"<tr><td>#{w['id']}</td><td>{w['uid']}</td><td>{fmt(w['amount'])}</td><td>{esc(w['method'])}</td></tr>" for w in wds)
            + "</table><small>Tasdiqlash — botdagi admin panelda.</small></div>")
    return web.Response(text=PAGE.format(body=body).replace("&lt;b&gt;", "").replace("&lt;/b&gt;", ""), content_type="text/html")


async def start_web():
    if not (WEB_PORT and WEB_PORT.isdigit()): return
    app = web.Application()
    app.add_routes([web.get("/app", h_app), web.post("/api/device", h_device), web.get("/panel", h_panel)])
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(WEB_PORT)).start()


# ───────────── ishga tushirish ─────────────
@router.message(F.text & ~F.text.startswith("/"))
async def fallback(m: Message, state: FSMContext, bot: Bot):
    if await state.get_state() is None and U(m.from_user.id) and await gate(bot, m):
        await m.answer(T("welcome", name=esc(m.from_user.full_name)), reply_markup=menu(m.from_user.id))


async def main():
    global BOT_USERNAME
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    import plan_limits; plan_limits.install(dp)   # tarif limitlari
    BOT_USERNAME = (await bot.get_me()).username
    await bot.delete_webhook()
    await start_web()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
