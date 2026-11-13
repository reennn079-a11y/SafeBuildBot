import asyncio
import html
import json
import logging
import math
import os
import time
from datetime import datetime

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, ChatJoinRequest, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import nomer_db as db
import nomer_providers as providers
from nomer_providers import PROVIDERS, ProviderError
from nomer_texts import TEXTS

from pathlib import Path
load_dotenv(Path.cwd() / ".env")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

TOKEN = os.environ["BOT_TOKEN"]
SUPER_ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
WAIT_SECONDS = int(os.getenv("WAIT_SECONDS", "300"))
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "1000"))
PAGE = 10

bot: Bot
BOT_USERNAME = ""
ADMINS: set[int] = set(SUPER_ADMINS)
CACHE: dict[str, str] = {}
CK = "COALESCE(NULLIF(ckey,''), code)"  # davlatning umumiy kaliti (SQL)

UZ_NAMES = {
    "russia": "Rossiya", "kazakhstan": "Qozog'iston", "uzbekistan": "O'zbekiston", "ukraine": "Ukraina",
    "usa": "AQSH", "england": "Angliya", "indonesia": "Indoneziya", "india": "Hindiston",
    "kyrgyzstan": "Qirg'iziston", "tajikistan": "Tojikiston", "turkey": "Turkiya", "china": "Xitoy",
    "germany": "Germaniya", "poland": "Polsha", "brazil": "Braziliya", "philippines": "Filippin",
    "vietnam": "Vetnam", "thailand": "Tailand", "malaysia": "Malayziya", "azerbaijan": "Ozarbayjon",
    "georgia": "Gruziya", "armenia": "Armaniya", "belarus": "Belarus", "moldova": "Moldova",
    "egypt": "Misr", "nigeria": "Nigeriya", "france": "Fransiya", "spain": "Ispaniya",
}


# ───────────── yordamchilar ─────────────
def fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


class Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def t(key: str, **kw) -> str:
    txt = CACHE.get(key) or TEXTS[key][1]
    try:
        return txt.format_map(Safe(**kw))
    except Exception:
        return TEXTS[key][1].format_map(Safe(**kw))


def is_admin(uid: int) -> bool:
    return uid in ADMINS


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and is_admin(event.from_user.id)


def Btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def chunk(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def markup(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def safe_send(uid, text, **kw):
    try:
        return await bot.send_message(uid, text, **kw)
    except Exception as e:
        log.warning("send fail %s: %s", uid, e)


async def notify_admins(text: str, **kw):
    for a in ADMINS:
        await safe_send(a, text, **kw)


async def show(c: CallbackQuery, text: str, kb=None):
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await c.message.answer(text, reply_markup=kb)
    try:
        await c.answer()
    except TelegramBadRequest:
        pass


async def get_setting(key: str, default: str | None = None):
    r = await db.fetchone("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


async def set_setting(key: str, value: str):
    await db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


async def add_balance(uid: int, amount: int, reason: str | None = None):
    await db.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
    if reason:
        await db.execute("INSERT INTO balance_log(user_id,amount,reason,created) VALUES(?,?,?,?)",
                         (uid, amount, reason, int(time.time())))


async def get_balance(uid: int) -> int:
    r = await db.fetchone("SELECT balance FROM users WHERE id=?", (uid,))
    return r["balance"] if r else 0


def back_kb(data="home"):
    return markup([[Btn(t("btn_back"), data)]])


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=t("btn_buy"), callback_data="buy")
    kb.button(text=t("btn_topup"), callback_data="topup")
    kb.button(text=t("btn_cabinet"), callback_data="cabinet")
    kb.button(text=t("btn_rating"), callback_data="rating")
    kb.button(text=t("btn_orders"), callback_data="orders")
    kb.button(text=t("btn_ref"), callback_data="ref")
    kb.button(text=t("btn_help"), callback_data="help")
    kb.adjust(1, 2, 2, 2)
    return kb.as_markup()


async def welcome_text(uid: int) -> str:
    return t("welcome", id=uid, balance=fmt(await get_balance(uid)))


# ───────────── middleware: ban, texnik tanaffus, majburiy obuna ─────────────
async def not_subscribed(uid: int):
    missing = []
    for ch in await db.fetchall("SELECT * FROM channels"):
        if await db.fetchone("SELECT 1 FROM join_requests WHERE chat_id=? AND user_id=?", (ch["chat_id"], uid)):
            continue  # zayavka yuborgan
        try:
            m = await bot.get_chat_member(ch["chat_id"], uid)
            ok = m.status in ("member", "administrator", "creator") or (
                m.status == "restricted" and getattr(m, "is_member", False))
        except Exception as e:
            log.warning("obuna tekshirib bo'lmadi %s: %s", ch["chat_id"], e)
            ok = True
        if not ok:
            missing.append(ch)
    return missing


async def sub_kb(channels):
    rows = [[InlineKeyboardButton(text=f"📢 {ch['title']}", url=ch["url"])] for ch in channels]
    for l in await db.fetchall("SELECT * FROM links"):
        rows.append([InlineKeyboardButton(text=f"🔗 {l['title']}", url=l["url"])])
    rows.append([Btn(t("btn_check_sub"), "check_sub")])
    return markup(rows)


class MainMW(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = event.from_user
        if not u or (isinstance(event, Message) and event.chat.type != "private"):
            return await handler(event, data)
        row = await db.fetchone("SELECT banned FROM users WHERE id=?", (u.id,))
        if not row:
            ref = None
            if isinstance(event, Message) and event.text and event.text.startswith("/start "):
                arg = event.text.split(maxsplit=1)[1]
                if arg.startswith("ref_") and arg[4:].isdigit() and int(arg[4:]) != u.id:
                    ref = int(arg[4:])
                    if not await db.fetchone("SELECT 1 FROM users WHERE id=?", (ref,)):
                        ref = None
            await db.execute("INSERT INTO users(id,name,balance,joined,ref_by) VALUES(?,?,0,?,?)",
                             (u.id, u.full_name, int(time.time()), ref))
            banned = 0
        else:
            await db.execute("UPDATE users SET name=? WHERE id=?", (u.full_name, u.id))
            banned = row["banned"]
        msg = event if isinstance(event, Message) else event.message
        if is_admin(u.id) or (isinstance(event, CallbackQuery) and event.data == "check_sub"):
            return await handler(event, data)
        if banned:
            if isinstance(event, CallbackQuery):
                return await event.answer(t("banned"), show_alert=True)
            return await msg.answer(t("banned"))
        if await get_setting("maint", "0") == "1":
            if isinstance(event, CallbackQuery):
                return await event.answer(t("maintenance"), show_alert=True)
            return await msg.answer(t("maintenance"))
        missing = await not_subscribed(u.id)
        if not missing:
            return await handler(event, data)
        await msg.answer(t("sub_required"), reply_markup=await sub_kb(missing))
        if isinstance(event, CallbackQuery):
            await event.answer()


# ───────────── holatlar ─────────────
class Topup(StatesGroup):
    amount = State()
    receipt = State()


class Search(StatesGroup):
    wait = State()


class EditText(StatesGroup):
    wait = State()


class Broadcast(StatesGroup):
    wait = State()
    confirm = State()
    button = State()


class AddAdmin(StatesGroup):
    wait = State()


class AddChannel(StatesGroup):
    wait = State()


class AddLink(StatesGroup):
    wait = State()


class PriceAdd(StatesGroup):
    wait = State()


class PriceDel(StatesGroup):
    wait = State()


class PriceCfg(StatesGroup):
    wait = State()


class RateCfg(StatesGroup):
    wait = State()


class CtyPrice(StatesGroup):
    wait = State()


class BalEdit(StatesGroup):
    wait = State()


class UserFind(StatesGroup):
    wait = State()


class SettingEdit(StatesGroup):
    wait = State()


user = Router()
admin = Router()
user.message.filter(F.chat.type == "private")
admin.message.filter(F.chat.type == "private", IsAdmin())
admin.callback_query.filter(IsAdmin())


# ───────────── foydalanuvchi: asosiy ─────────────
@user.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(await welcome_text(m.from_user.id), reply_markup=main_menu())


@user.callback_query(F.data == "home")
async def home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, await welcome_text(c.from_user.id), main_menu())


@user.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()


@user.callback_query(F.data == "check_sub")
async def check_sub(c: CallbackQuery):
    if await not_subscribed(c.from_user.id):
        return await c.answer(t("sub_not_yet"), show_alert=True)
    try:
        await c.message.delete()
    except TelegramBadRequest:
        pass
    await c.message.answer(await welcome_text(c.from_user.id), reply_markup=main_menu())
    await c.answer()


@user.chat_join_request()
async def join_request(e: ChatJoinRequest):
    await db.execute("INSERT OR IGNORE INTO join_requests(chat_id,user_id) VALUES(?,?)",
                     (e.chat.id, e.from_user.id))


@user.callback_query(F.data == "help")
async def help_(c: CallbackQuery):
    await show(c, t("help"), back_kb())


@user.callback_query(F.data == "cabinet")
async def cabinet(c: CallbackQuery):
    u = await db.fetchone("SELECT * FROM users WHERE id=?", (c.from_user.id,))
    n = await db.fetchone("SELECT COUNT(*) n FROM orders WHERE user_id=? AND status='done'", (c.from_user.id,))
    await show(c, t("cabinet", id=u["id"], balance=fmt(u["balance"]), orders=n["n"],
                    joined=datetime.fromtimestamp(u["joined"]).strftime("%d.%m.%Y")),
               markup([[Btn(t("btn_history"), "hist")], [Btn(t("btn_back"), "home")]]))


@user.callback_query(F.data == "hist")
async def history(c: CallbackQuery):
    rows = await db.fetchall("SELECT * FROM balance_log WHERE user_id=? ORDER BY id DESC LIMIT 15", (c.from_user.id,))
    if not rows:
        return await show(c, t("history_empty"), back_kb("cabinet"))
    lines = [f"{'➕' if r['amount'] > 0 else '➖'} {fmt(abs(r['amount']))} · {html.escape(r['reason'] or '')} · "
             f"{datetime.fromtimestamp(r['created']).strftime('%d.%m %H:%M')}" for r in rows]
    await show(c, t("history_title") + "\n" + "\n".join(lines), back_kb("cabinet"))


@user.callback_query(F.data == "ref")
async def ref(c: CallbackQuery):
    uid = c.from_user.id
    n = await db.fetchone("SELECT COUNT(*) n FROM users WHERE ref_by=?", (uid,))
    u = await db.fetchone("SELECT ref_earned FROM users WHERE id=?", (uid,))
    pct = await get_setting("ref:percent", "3")
    await show(c, t("ref_info", link=f"https://t.me/{BOT_USERNAME}?start=ref_{uid}", count=n["n"],
                    percent=pct, earned=fmt(u["ref_earned"] or 0)), back_kb())


@user.callback_query(F.data == "rating")
async def rating(c: CallbackQuery):
    rows = await db.fetchall(
        "SELECT u.name, SUM(o.price) s, COUNT(*) n FROM orders o JOIN users u ON u.id=o.user_id "
        "WHERE o.status='done' GROUP BY o.user_id ORDER BY s DESC LIMIT 10")
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else str(i + 1) + '.'} {html.escape(r['name'] or '—')} — {r['n']} ta · {fmt(r['s'])} so'm"
             for i, r in enumerate(rows)]
    await show(c, t("rating_title") + "\n" + ("\n".join(lines) or "—"), back_kb())


@user.callback_query(F.data == "orders")
async def orders(c: CallbackQuery):
    rows = await db.fetchall("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (c.from_user.id,))
    if not rows:
        return await show(c, t("orders_empty"), back_kb())
    ico = {"done": "✅", "waiting": "⏳", "expired": "❌", "canceled": "🚫"}
    lines = [f"{ico.get(r['status'], '•')} +{r['phone']} · {r['country']} · {fmt(r['price'])} so'm"
             + (f" · <code>{r['code']}</code>" if r["code"] else "") for r in rows]
    await show(c, t("orders_title") + "\n" + "\n".join(lines), back_kb())


# ───────────── foydalanuvchi: server / davlat tanlash ─────────────
def in_clause(ids: list) -> str:
    return ",".join("?" * len(ids)) if ids else "NULL"


@user.callback_query(F.data == "buy")
async def buy(c: CallbackQuery, state: FSMContext):
    await state.clear()
    sids = list(PROVIDERS)
    rows = await db.fetchall(
        f"SELECT server, COUNT(*) n, MIN(price) mn FROM prices WHERE hidden=0 AND server IN ({in_clause(sids)}) "
        f"GROUP BY server ORDER BY server", sids)
    kb = [[Btn(f"🖥 Server {r['server']} · {r['n']} ta davlat · {fmt(r['mn'])} so'mdan", f"srv:{r['server']}:p:0")]
          for r in rows]
    if len(rows) >= 2:
        a = await db.fetchone(
            f"SELECT COUNT(DISTINCT {CK}) n, MIN(price) mn FROM prices WHERE hidden=0 AND server IN ({in_clause(sids)})", sids)
        kb.insert(0, [Btn(f"{t('btn_cheapest')} · {a['n']} ta davlat · {fmt(a['mn'])} so'mdan", "srv:0:p:0")])
    kb.append([Btn(t("btn_back"), "home")])
    await show(c, t("choose_server") if rows else t("no_servers"), markup(kb))


async def list_countries(s: int) -> list[dict]:
    if s == 0:
        sids = list(PROVIDERS)
        rows = await db.fetchall(
            f"SELECT {CK} AS ck, MIN(name) AS name, MIN(price) AS price, MAX(pinned) AS pinned FROM prices "
            f"WHERE hidden=0 AND server IN ({in_clause(sids)}) GROUP BY {CK}", sids)
    else:
        rows = await db.fetchall(
            "SELECT code AS ck, name, price, pinned FROM prices WHERE hidden=0 AND server=?", (s,))
    return [dict(r) for r in rows]


async def country_view(s: int, sort: str = "p", page: int = 0, q: str | None = None):
    items = await list_countries(s)
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["name"].lower() or ql in i["ck"].lower()
                 or ql in UZ_NAMES.get(i["ck"], "").lower()]
    if sort == "p":
        items.sort(key=lambda i: (-(i["pinned"] or 0), i["price"]))
    else:
        items.sort(key=lambda i: (-(i["pinned"] or 0), i["name"].lower()))
    pages = max(1, math.ceil(len(items) / PAGE))
    page = min(max(page, 0), pages - 1)
    part = items[:30] if q else items[page * PAGE:(page + 1) * PAGE]
    btns = [Btn(f"{'📌 ' if i['pinned'] else ''}{i['name']} · {fmt(i['price'])}",
                f"go:{s}:{i['ck']}" if s else f"goc:{i['ck']}") for i in part]
    rows = chunk(btns, 2)
    if not q:
        nav = []
        if page > 0:
            nav.append(Btn("◀️", f"srv:{s}:{sort}:{page - 1}"))
        nav.append(Btn(f"{page + 1}/{pages}", "noop"))
        if page < pages - 1:
            nav.append(Btn("▶️", f"srv:{s}:{sort}:{page + 1}"))
        rows.append(nav)
        rows.append([Btn(t("btn_sort_name") if sort == "p" else t("btn_sort_price"),
                         f"srv:{s}:{'n' if sort == 'p' else 'p'}:0"),
                     Btn(t("btn_search"), f"find:{s}")])
    rows.append([Btn(t("btn_back"), "buy")])
    label = t("btn_cheapest") if s == 0 else f"Server {s}"
    return t("choose_country", server=label), markup(rows), len(items)


@user.callback_query(F.data.startswith("srv:"))
async def server(c: CallbackQuery, state: FSMContext):
    await state.clear()
    _, s, sort, page = c.data.split(":")
    text, kb, _ = await country_view(int(s), sort, int(page))
    await show(c, text, kb)


@user.callback_query(F.data.startswith("find:"))
async def find(c: CallbackQuery, state: FSMContext):
    s = int(c.data.split(":")[1])
    await state.set_state(Search.wait)
    await state.update_data(s=s)
    await show(c, t("search_ask"), back_kb(f"srv:{s}:p:0"))


@user.message(Search.wait)
async def find_result(m: Message, state: FSMContext):
    s = (await state.get_data())["s"]
    await state.clear()
    text, kb, n = await country_view(s, "p", 0, q=(m.text or "").strip())
    await m.answer(text if n else t("search_empty"), reply_markup=kb)


# ───────────── foydalanuvchi: raqam olish ─────────────
async def set_status(oid: int, new: str, code: str | None = None) -> bool:
    cur = await db.execute(
        "UPDATE orders SET status=?, code=COALESCE(?, code) WHERE id=? AND status='waiting'", (new, code, oid))
    return cur.rowcount > 0


async def purchase(c: CallbackQuery, rows: list):
    """rows — arzonidan qimmatiga tartiblangan variantlar. Birinchisi bo'lmasa keyingisiga o'tadi."""
    uid = c.from_user.id
    rows = [p for p in rows if p["server"] in PROVIDERS]
    if not rows:
        return await c.answer(t("no_numbers"), show_alert=True)
    if await get_balance(uid) < rows[0]["price"]:
        return await c.answer(t("insufficient", price=fmt(rows[0]["price"])), show_alert=True)
    for p in rows:
        price, cost = p["price"], p["cost"] or 0
        if cost and price < cost:
            continue  # zararga sotilmaydi
        cur = await db.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?", (price, uid, price))
        if cur.rowcount == 0:
            continue
        try:
            pid, phone = await PROVIDERS[p["server"]].get_number(p["code"])
        except Exception as e:
            log.warning("get_number server %s: %s", p["server"], e)
            await add_balance(uid, price)  # jim qaytarish (tarixga yozilmaydi)
            continue
        await db.execute("INSERT INTO balance_log(user_id,amount,reason,created) VALUES(?,?,?,?)",
                         (uid, -price, f"Raqam: {p['name']} (S{p['server']})", int(time.time())))
        cur = await db.execute(
            "INSERT INTO orders(user_id,server,pid,phone,country,price,cost,status,created) "
            "VALUES(?,?,?,?,?,?,?, 'waiting',?)",
            (uid, p["server"], pid, phone, p["name"], price, cost, int(time.time())))
        oid = cur.lastrowid
        await show(c, t("number_given", phone=phone, country=p["name"], price=fmt(price),
                        minutes=WAIT_SECONDS // 60),
                   markup([[Btn(t("btn_cancel"), f"cancel:{oid}")]]))
        asyncio.create_task(watch(oid))
        return
    await c.answer(t("no_numbers"), show_alert=True)


@user.callback_query(F.data.startswith("go:"))
async def go(c: CallbackQuery):
    _, s, code = c.data.split(":", 2)
    p = await db.fetchone("SELECT * FROM prices WHERE server=? AND code=?", (int(s), code))
    if not p:
        return await c.answer("Topilmadi", show_alert=True)
    await purchase(c, [p])


@user.callback_query(F.data.startswith("goc:"))
async def go_cheapest(c: CallbackQuery):
    ck = c.data.split(":", 1)[1]
    rows = await db.fetchall(
        f"SELECT * FROM prices WHERE hidden=0 AND {CK}=? ORDER BY price ASC", (ck,))
    await purchase(c, list(rows))


@user.callback_query(F.data.startswith("cancel:"))
async def cancel(c: CallbackQuery):
    oid = int(c.data.split(":")[1])
    o = await db.fetchone("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, c.from_user.id))
    if not o or o["status"] != "waiting":
        return await c.answer("Buyurtma faol emas", show_alert=True)
    try:
        await PROVIDERS[o["server"]].cancel(o["pid"])
    except Exception as e:
        log.info("cancel denied: %s", e)
        return await c.answer(t("cancel_denied"), show_alert=True)
    if await set_status(oid, "canceled"):
        await add_balance(o["user_id"], o["price"], "Bekor qilingan buyurtma")
        await show(c, t("order_canceled", price=fmt(o["price"])), back_kb())


async def post_proof(o):
    chat = await get_setting("proof:chat")
    if not chat or chat == "0":
        return
    ph = o["phone"]
    masked = ph[:3] + "****" + ph[-2:] if len(ph) > 6 else "****"
    await safe_send(int(chat) if chat.lstrip("-").isdigit() else chat,
                    f"✅ <b>Yangi sotuv</b>\n🌍 {html.escape(o['country'])}\n📲 +{masked}\n💰 {fmt(o['price'])} so'm")


async def watch(oid: int):
    while True:
        o = await db.fetchone("SELECT * FROM orders WHERE id=?", (oid,))
        if not o or o["status"] != "waiting":
            return
        prov = PROVIDERS.get(o["server"])
        if prov is None:  # server o'chirilgan: pulni qaytaramiz
            if await set_status(oid, "expired"):
                await add_balance(o["user_id"], o["price"], "Server mavjud emas, qaytarildi")
                await safe_send(o["user_id"], t("code_timeout", price=fmt(o["price"])), reply_markup=back_kb())
            return
        if time.time() - o["created"] > WAIT_SECONDS:
            if await set_status(oid, "expired"):
                try:
                    await prov.cancel(o["pid"])
                except Exception as e:
                    log.warning("expire cancel: %s", e)
                await add_balance(o["user_id"], o["price"], "Kod kelmadi, qaytarildi")
                await safe_send(o["user_id"], t("code_timeout", price=fmt(o["price"])), reply_markup=back_kb())
            return
        try:
            code = await prov.get_code(o["pid"])
        except Exception as e:
            log.warning("get_code: %s", e)
            code = None
        if code:
            if await set_status(oid, "done", code):
                try:
                    await prov.finish(o["pid"])
                except Exception:
                    pass
                await safe_send(o["user_id"], t("code_received", code=code, phone=o["phone"]),
                                reply_markup=back_kb())
                await post_proof(o)
            return
        await asyncio.sleep(5)


# ───────────── foydalanuvchi: pul kiritish ─────────────
@user.callback_query(F.data == "topup")
async def topup(c: CallbackQuery, state: FSMContext):
    await state.set_state(Topup.amount)
    await show(c, t("topup_info"), back_kb())


@user.message(Topup.amount)
async def topup_amount(m: Message, state: FSMContext):
    txt = (m.text or "").replace(" ", "")
    if not txt.isdigit() or int(txt) < MIN_TOPUP:
        return await m.answer(t("topup_amount_bad", min=fmt(MIN_TOPUP)))
    await state.update_data(amount=int(txt))
    await state.set_state(Topup.receipt)
    await m.answer(t("topup_receipt_ask"), reply_markup=back_kb())


@user.message(Topup.receipt, F.photo | F.document)
async def topup_receipt(m: Message, state: FSMContext):
    amount = (await state.get_data())["amount"]
    cur = await db.execute("INSERT INTO payments(user_id,amount,created) VALUES(?,?,?)",
                           (m.from_user.id, amount, int(time.time())))
    pid = cur.lastrowid
    kb = markup([[Btn("✅ Tasdiqlash", f"pay:ok:{pid}"), Btn("❌ Rad etish", f"pay:no:{pid}")]])
    info = (f"💳 <b>Yangi to'lov #{pid}</b>\n👤 {html.escape(m.from_user.full_name)} "
            f"(<code>{m.from_user.id}</code>)\n💵 {fmt(amount)} so'm")
    for a in ADMINS:
        try:
            await bot.copy_message(a, m.chat.id, m.message_id)
            await bot.send_message(a, info, reply_markup=kb)
        except Exception as e:
            log.warning("admin notify %s: %s", a, e)
    await state.clear()
    await m.answer(t("topup_sent"), reply_markup=back_kb())


@user.message(Topup.receipt)
async def topup_receipt_bad(m: Message):
    await m.answer(t("topup_receipt_ask"))


# ───────────── admin: to'lovlar ─────────────
@admin.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery):
    _, act, pid = c.data.split(":")
    p = await db.fetchone("SELECT * FROM payments WHERE id=?", (int(pid),))
    cur = await db.execute("UPDATE payments SET status=? WHERE id=? AND status='pending'",
                           ("ok" if act == "ok" else "no", int(pid)))
    if cur.rowcount == 0:
        return await c.answer("Allaqachon ko'rib chiqilgan", show_alert=True)
    if act == "ok":
        await add_balance(p["user_id"], p["amount"], "To'lov tasdiqlandi")
        await safe_send(p["user_id"], t("topup_approved", amount=fmt(p["amount"])))
        u = await db.fetchone("SELECT ref_by FROM users WHERE id=?", (p["user_id"],))
        pct = float(await get_setting("ref:percent", "3") or 0)
        if u and u["ref_by"] and pct > 0:
            bonus = int(p["amount"] * pct / 100)
            if bonus > 0:
                await add_balance(u["ref_by"], bonus, "Referral bonusi")
                await db.execute("UPDATE users SET ref_earned=ref_earned+? WHERE id=?", (bonus, u["ref_by"]))
                await safe_send(u["ref_by"], t("ref_bonus", amount=fmt(bonus)))
        mark = f"✅ Tasdiqlandi ({c.from_user.full_name})"
    else:
        await safe_send(p["user_id"], t("topup_rejected"))
        mark = f"❌ Rad etildi ({c.from_user.full_name})"
    await c.message.edit_text(c.message.html_text + "\n\n" + html.escape(mark))
    await c.answer()


# ───────────── admin panel ─────────────
def admin_menu():
    kb = InlineKeyboardBuilder()
    for text, data in [("📊 Statistika", "adm:stats"), ("📢 Xabar yuborish", "adm:bc"),
                       ("✏️ Matnlar", "adm:texts"), ("🔗 Majburiy obuna", "adm:subs"),
                       ("👮 Adminlar", "adm:admins"), ("💲 Narxlar", "adm:prices"),
                       ("🌍 Davlatlar", "adm:cty"), ("👤 Foydalanuvchi", "adm:user"),
                       ("⚙️ Sozlamalar", "adm:set")]:
        kb.button(text=text, callback_data=data)
    kb.adjust(2)
    return kb.as_markup()


def adm_back():
    return markup([[Btn("◀️ Admin panel", "adm")]])


@admin.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 <b>Admin panel</b>", reply_markup=admin_menu())


@admin.callback_query(F.data == "adm")
async def admin_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🛠 <b>Admin panel</b>", admin_menu())


@admin.callback_query(F.data == "adm:stats")
async def stats(c: CallbackQuery):
    day = int(datetime.now().replace(hour=0, minute=0, second=0).timestamp())
    u = await db.fetchone("SELECT COUNT(*) n, COALESCE(SUM(balance),0) b FROM users")
    nu = await db.fetchone("SELECT COUNT(*) n FROM users WHERE joined>=?", (day,))
    o = await db.fetchone("SELECT COUNT(*) n, COALESCE(SUM(price),0) s, COALESCE(SUM(price-cost),0) p "
                          "FROM orders WHERE status='done'")
    od = await db.fetchone("SELECT COUNT(*) n, COALESCE(SUM(price),0) s, COALESCE(SUM(price-cost),0) p "
                           "FROM orders WHERE status='done' AND created>=?", (day,))
    w = await db.fetchone("SELECT COUNT(*) n FROM orders WHERE status='waiting'")
    p = await db.fetchone("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE status='ok'")
    top = await db.fetchall("SELECT country, COUNT(*) n FROM orders WHERE status='done' "
                            "GROUP BY country ORDER BY n DESC LIMIT 5")
    bal = []
    for sid, prov in PROVIDERS.items():
        try:
            bal.append(f"Server {sid}: {await prov.balance():.2f}")
        except Exception:
            bal.append(f"Server {sid}: —")
    text = (f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: {u['n']} (bugun +{nu['n']})\n"
            f"💰 Jami balanslar: {fmt(u['b'])} so'm\n💵 Kiritilgan pul: {fmt(p['s'])} so'm\n"
            f"⏳ Kutilayotgan: {w['n']}\n\n"
            f"<b>Bugun:</b> {od['n']} ta · tushum {fmt(od['s'])} · foyda {fmt(od['p'])}\n"
            f"<b>Jami:</b> {o['n']} ta · tushum {fmt(o['s'])} · sof foyda {fmt(o['p'])}\n\n"
            f"🏆 <b>TOP davlatlar</b>\n" + ("\n".join(f"{i + 1}. {html.escape(r['country'])} — {r['n']}"
                                                  for i, r in enumerate(top)) or "—") +
            "\n\n🏦 <b>Provayder balanslari</b>\n" + ("\n".join(bal) or "—"))
    await show(c, text, adm_back())


# ── matnlarni o'zgartirish
@admin.callback_query(F.data == "adm:texts")
async def texts_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    for k, v in TEXTS.items():
        kb.button(text=("✏️ " if k in CACHE else "") + v[0], callback_data=f"tx:{k}")
    kb.button(text="◀️ Admin panel", callback_data="adm")
    kb.adjust(2)
    await show(c, "✏️ Qaysi matnni o'zgartirasiz? (✏️ = o'zgartirilgan)", kb.as_markup())


@admin.callback_query(F.data.startswith("tx:"))
async def text_pick(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":", 1)[1]
    label, default, ph = TEXTS[key]
    await state.set_state(EditText.wait)
    await state.update_data(key=key)
    kb = markup([[Btn("♻️ Standartga qaytarish", f"txr:{key}")], [Btn("◀️ Orqaga", "adm:texts")]])
    cur = CACHE.get(key) or default
    await show(c, f"<b>{label}</b>\n\nHozirgi matn:\n<code>{html.escape(cur)}</code>\n\n"
                  f"O'zgaruvchilar: {html.escape(ph) or '—'}\n\nYangi matnni yuboring "
                  f"(Telegram formatlash ishlaydi).", kb)


@admin.callback_query(F.data.startswith("txr:"))
async def text_reset(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":", 1)[1]
    await db.execute("DELETE FROM settings WHERE key=?", (f"t:{key}",))
    CACHE.pop(key, None)
    await state.clear()
    await c.answer("Standart matn tiklandi", show_alert=True)
    await texts_menu(c, state)


@admin.message(EditText.wait)
async def text_save(m: Message, state: FSMContext):
    if not m.text:
        return await m.answer("Faqat matn yuboring")
    key = (await state.get_data())["key"]
    new = m.html_text
    try:
        new.format_map(Safe(id=1, balance=0, phone=1, code=1, price=1, country=1, minutes=1, server=1,
                            amount=1, orders=1, joined=1, min=1, link=1, count=1, percent=1, earned=1))
        await m.answer(new)  # HTML to'g'riligini tekshirish
    except Exception as e:
        return await m.answer(f"❌ Matnda xato: {html.escape(str(e))}\nQaytadan yuboring.")
    await set_setting(f"t:{key}", new)
    CACHE[key] = new
    await state.clear()
    await m.answer("✅ Saqlandi", reply_markup=adm_back())


# ── xabar yuborish (tugma bilan)
async def bc_preview(chat_id: int, state: FSMContext):
    d = await state.get_data()
    rm = markup([[InlineKeyboardButton(text=d["btn"][0], url=d["btn"][1])]]) if d.get("btn") else None
    await bot.copy_message(chat_id, d["chat"], d["mid"], reply_markup=rm)
    await bot.send_message(chat_id, "👆 Shu xabar hammaga yuboriladi. Tasdiqlaysizmi?", reply_markup=markup([
        [Btn("✅ Yuborish", "bc:go")], [Btn("🔘 Tugma qo'shish", "bc:btn")], [Btn("❌ Bekor", "adm")]]))


@admin.callback_query(F.data == "adm:bc")
async def bc_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.wait)
    await show(c, "📢 Yuboriladigan xabarni yuboring (matn, rasm, video — istalgan).", adm_back())


@admin.message(Broadcast.wait)
async def bc_got(m: Message, state: FSMContext):
    await state.update_data(chat=m.chat.id, mid=m.message_id, btn=None)
    await state.set_state(Broadcast.confirm)
    await bc_preview(m.chat.id, state)


@admin.callback_query(F.data == "bc:btn", Broadcast.confirm)
async def bc_btn(c: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.button)
    await show(c, "Tugma: <code>Matn | https://havola</code>", adm_back())


@admin.message(Broadcast.button)
async def bc_btn_save(m: Message, state: FSMContext):
    parts = [x.strip() for x in (m.text or "").split("|", 1)]
    if len(parts) != 2 or not parts[1].startswith(("http://", "https://", "tg://")):
        return await m.answer("❌ Format: <code>Matn | https://havola</code>")
    await state.update_data(btn=parts)
    await state.set_state(Broadcast.confirm)
    await bc_preview(m.chat.id, state)


@admin.callback_query(F.data == "bc:go", Broadcast.confirm)
async def bc_go(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await show(c, "⏳ Yuborish boshlandi...")
    asyncio.create_task(do_broadcast(c.from_user.id, d["chat"], d["mid"], d.get("btn")))


async def do_broadcast(admin_id: int, chat: int, mid: int, btn):
    rm = markup([[InlineKeyboardButton(text=btn[0], url=btn[1])]]) if btn else None
    ok = fail = 0
    for r in await db.fetchall("SELECT id FROM users WHERE banned=0 OR banned IS NULL"):
        while True:
            try:
                await bot.copy_message(r["id"], chat, mid, reply_markup=rm)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                fail += 1
                break
        await asyncio.sleep(0.05)
    await safe_send(admin_id, f"📢 Tugadi.\n✅ Yetib bordi: {ok}\n❌ Bormadi: {fail}", reply_markup=adm_back())


# ── majburiy obuna (kanallar + qo'shimcha havolalar)
@admin.callback_query(F.data == "adm:subs")
async def subs_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[Btn(f"🗑 {ch['title']}", f"chd:{ch['chat_id']}")] for ch in await db.fetchall("SELECT * FROM channels")]
    rows += [[Btn(f"🗑 🔗 {l['title']}", f"lkd:{l['id']}")] for l in await db.fetchall("SELECT * FROM links")]
    rows.append([Btn("➕ Kanal qo'shish", "ch:add"), Btn("➕ Havola qo'shish", "lk:add")])
    rows.append([Btn("◀️ Admin panel", "adm")])
    await show(c, "🔗 <b>Majburiy obuna</b>\nO'chirish uchun bosing.\n\n"
                  "Kanal — obuna tekshiriladi. Havola — faqat tugma sifatida ko'rsatiladi.", markup(rows))


@admin.callback_query(F.data == "ch:add")
async def ch_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddChannel.wait)
    await show(c, "Kanal @username yoki ID sini yuboring.\n"
                  "Yopiq yoki <b>zayavkali</b> kanal bo'lsa, oxiriga havolani ham yozing:\n"
                  "<code>-1001234567890 https://t.me/+abc...</code>\n\n"
                  "⚠️ Bot kanalda <b>admin</b> bo'lishi shart. Zayavkali kanalda zayavka yuborgan "
                  "foydalanuvchi obuna bo'lgan hisoblanadi.", adm_back())


@admin.message(AddChannel.wait)
async def ch_save(m: Message, state: FSMContext):
    parts = (m.text or "").split()
    if not parts:
        return await m.answer("Qaytadan yuboring")
    ref = int(parts[0]) if parts[0].lstrip("-").isdigit() else parts[0]
    try:
        chat = await bot.get_chat(ref)
        me = await bot.get_chat_member(chat.id, bot.id)
        if me.status not in ("administrator", "creator"):
            return await m.answer("❌ Bot bu kanalda admin emas. Admin qiling va qayta yuboring.")
    except Exception as e:
        return await m.answer(f"❌ Kanal topilmadi: {html.escape(str(e))}")
    url = parts[1] if len(parts) > 1 else (f"https://t.me/{chat.username}" if chat.username else None)
    if not url:
        return await m.answer("Bu shaxsiy kanal. ID dan keyin havolani ham yozing.")
    await db.execute("INSERT OR REPLACE INTO channels(chat_id,title,url) VALUES(?,?,?)",
                     (chat.id, chat.title or str(chat.id), url))
    await state.clear()
    await m.answer("✅ Kanal qo'shildi", reply_markup=adm_back())


@admin.callback_query(F.data == "lk:add")
async def lk_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddLink.wait)
    await show(c, "Havola: <code>Nomi | https://havola</code>", adm_back())


@admin.message(AddLink.wait)
async def lk_save(m: Message, state: FSMContext):
    parts = [x.strip() for x in (m.text or "").split("|", 1)]
    if len(parts) != 2 or not parts[1].startswith(("http://", "https://")):
        return await m.answer("❌ Format: <code>Nomi | https://havola</code>")
    await db.execute("INSERT INTO links(title,url) VALUES(?,?)", tuple(parts))
    await state.clear()
    await m.answer("✅ Havola qo'shildi", reply_markup=adm_back())


@admin.callback_query(F.data.startswith("chd:"))
async def ch_del(c: CallbackQuery, state: FSMContext):
    await db.execute("DELETE FROM channels WHERE chat_id=?", (int(c.data.split(":")[1]),))
    await subs_menu(c, state)


@admin.callback_query(F.data.startswith("lkd:"))
async def lk_del(c: CallbackQuery, state: FSMContext):
    await db.execute("DELETE FROM links WHERE id=?", (int(c.data.split(":")[1]),))
    await subs_menu(c, state)


# ── adminlar
@admin.callback_query(F.data == "adm:admins")
async def admins_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows, text = [], "👮 <b>Adminlar</b>\n"
    for a in sorted(ADMINS):
        if a in SUPER_ADMINS:
            text += f"\n⭐️ <code>{a}</code> (bosh admin)"
        else:
            text += f"\n• <code>{a}</code>"
            rows.append([Btn(f"🗑 {a}", f"ad:{a}")])
    rows.append([Btn("➕ Admin qo'shish", "ad:add")])
    rows.append([Btn("◀️ Admin panel", "adm")])
    await show(c, text, markup(rows))


@admin.callback_query(F.data == "ad:add")
async def ad_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddAdmin.wait)
    await show(c, "Yangi adminning Telegram ID sini yuboring (yoki uning xabarini forward qiling).", adm_back())


@admin.message(AddAdmin.wait)
async def ad_save(m: Message, state: FSMContext):
    if m.forward_origin and getattr(m.forward_origin, "sender_user", None):
        uid = m.forward_origin.sender_user.id
    elif (m.text or "").strip().isdigit():
        uid = int(m.text.strip())
    else:
        return await m.answer("❌ ID raqam yuboring")
    await db.execute("INSERT OR IGNORE INTO admins(id) VALUES(?)", (uid,))
    ADMINS.add(uid)
    await state.clear()
    await m.answer(f"✅ <code>{uid}</code> admin qilindi", reply_markup=adm_back())


@admin.callback_query(F.data.regexp(r"^ad:\d+$"))
async def ad_del(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split(":")[1])
    if uid in SUPER_ADMINS:
        return await c.answer("Bosh adminni o'chirib bo'lmaydi", show_alert=True)
    await db.execute("DELETE FROM admins WHERE id=?", (uid,))
    ADMINS.discard(uid)
    await admins_menu(c, state)


# ── narxlarni sinxronlash (barcha provayderlar)
async def sync_server(sid: int) -> int:
    prov = PROVIDERS[sid]
    rate = float(await get_setting(f"rate:{sid}", "150"))
    margin = float(await get_setting("sync:margin", "30"))
    limit = int(await get_setting("sync:limit", "40"))
    best = await prov.prices()
    items = sorted(best.items(), key=lambda x: x[1])[:limit]
    codes = []
    for code, native in items:
        cost = int(math.ceil(native * rate))
        price = int(math.ceil(cost * (1 + margin / 100) / 100)) * 100
        ck = prov.ckey(code)
        name = UZ_NAMES.get(ck, ck.replace("_", " ").title() if not ck.startswith("id") else f"Davlat {code}")
        codes.append(code)
        await db.execute(
            "INSERT INTO prices(server,code,name,price,ckey,cost) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(server,code) DO UPDATE SET cost=excluded.cost, ckey=excluded.ckey, "
            "price=CASE WHEN prices.custom=1 THEN prices.price ELSE excluded.price END", (sid, code, name, price, ck, cost))
    if codes:
        await db.execute(f"DELETE FROM prices WHERE server=? AND custom=0 AND code NOT IN ({in_clause(codes)})",
                         (sid, *codes))
    return len(items)


async def sync_all() -> list[str]:
    report = []
    for sid in list(PROVIDERS):
        try:
            report.append(f"Server {sid}: {await sync_server(sid)} ta davlat")
        except Exception as e:
            report.append(f"Server {sid}: ❌ {str(e)[:80]}")
    return report


async def auto_sync_loop():
    last = 0.0
    while True:
        await asyncio.sleep(30)
        try:
            if await get_setting("sync:auto", "0") != "1":
                continue
            interval = max(1, int(await get_setting("sync:interval", "5"))) * 60
            if time.time() - last >= interval:
                last = time.time()
                await sync_all()
        except Exception as e:
            log.warning("auto sync: %s", e)


async def monitor_loop():
    alerted: dict[int, float] = {}
    while True:
        await asyncio.sleep(600)
        try:
            thr = float(await get_setting("alert:min", "0") or 0)
            if thr <= 0:
                continue
            for sid, prov in PROVIDERS.items():
                try:
                    b = await prov.balance()
                except Exception as e:
                    log.warning("balance %s: %s", sid, e)
                    continue
                if b < thr and time.time() - alerted.get(sid, 0) > 3600:
                    alerted[sid] = time.time()
                    await notify_admins(f"⚠️ Server {sid} balansi kam: <b>{b:.2f}</b> (chegara {thr})")
        except Exception as e:
            log.warning("monitor: %s", e)


@admin.callback_query(F.data == "adm:prices")
async def prices_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    margin = await get_setting("sync:margin", "30")
    limit = await get_setting("sync:limit", "40")
    interval = await get_setting("sync:interval", "5")
    auto = await get_setting("sync:auto", "0") == "1"
    rates = [f"Server {sid}: 1 birlik = {await get_setting(f'rate:{sid}', '150')} so'm" for sid in PROVIDERS]
    cnt = await db.fetchall("SELECT server, COUNT(*) n FROM prices GROUP BY server ORDER BY server")
    text = ("💲 <b>Narxlar</b>\n\n" + ("\n".join(f"Server {r['server']}: {r['n']} ta davlat" for r in cnt) or "Bo'sh") +
            "\n\n💱 <b>Kurslar</b>\n" + ("\n".join(rates) or "Provayder ulanmagan") +
            f"\n\n📈 Ustama {margin}% · har serverdan {limit} ta eng arzon davlat · yangilash har {interval} daqiqa")
    rows = [[Btn("🔄 Hozir yangilash (hamma server)", "pr:sync")],
            [Btn("📈 Ustama / limit", "pr:cfg"), Btn("💱 Server kursi", "pr:rate")],
            [Btn(f"⏱ Avto yangilash: {'ON ✅' if auto else 'OFF'}", "pr:auto")],
            [Btn("➕ Qo'lda qo'shish", "pr:add"), Btn("🗑 O'chirish", "pr:del")],
            [Btn("◀️ Admin panel", "adm")]]
    await show(c, text, markup(rows))


@admin.callback_query(F.data == "pr:sync")
async def pr_sync(c: CallbackQuery, state: FSMContext):
    await c.answer("⏳ Yangilanmoqda...")
    report = await sync_all()
    await prices_menu(c, state)
    await c.message.answer("🔄 " + "\n".join(report))


@admin.callback_query(F.data == "pr:cfg")
async def pr_cfg(c: CallbackQuery, state: FSMContext):
    await state.set_state(PriceCfg.wait)
    await show(c, "Format: <code>ustama% limit</code>\n• ustama — foyda foizi\n"
                  "• limit — har serverdan nechta eng arzon davlat (1–90)\n\nMasalan: <code>30 40</code>", adm_back())


@admin.message(PriceCfg.wait)
async def pr_cfg_save(m: Message, state: FSMContext):
    try:
        margin, limit = (m.text or "").split()
        margin, limit = float(margin), int(limit)
        assert margin >= 0 and 1 <= limit <= 90
    except Exception:
        return await m.answer("❌ Format: <code>30 40</code>")
    await set_setting("sync:margin", str(margin))
    await set_setting("sync:limit", str(limit))
    await state.clear()
    await m.answer("✅ Saqlandi", reply_markup=adm_back())


@admin.callback_query(F.data == "pr:rate")
async def pr_rate(c: CallbackQuery, state: FSMContext):
    await state.set_state(RateCfg.wait)
    await show(c, "Format: <code>server kurs</code>\nProvayder valyutasining 1 birligi necha so'm "
                  "(5sim — rubl, boshqalar — provayder valyutasi).\n\nMasalan: <code>1 150</code>", adm_back())


@admin.message(RateCfg.wait)
async def pr_rate_save(m: Message, state: FSMContext):
    try:
        sid, rate = (m.text or "").split()
        assert int(sid) in PROVIDERS and float(rate) > 0
    except Exception:
        return await m.answer(f"❌ Format: <code>server kurs</code> (serverlar: {list(PROVIDERS)})")
    await set_setting(f"rate:{int(sid)}", str(float(rate)))
    await state.clear()
    await m.answer("✅ Saqlandi. «Hozir yangilash» ni bosing.", reply_markup=adm_back())


@admin.callback_query(F.data == "pr:auto")
async def pr_auto(c: CallbackQuery, state: FSMContext):
    await set_setting("sync:auto", "0" if await get_setting("sync:auto", "0") == "1" else "1")
    await prices_menu(c, state)


@admin.callback_query(F.data == "pr:add")
async def pr_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(PriceAdd.wait)
    await show(c, "Har qatorda bittadan:\n<code>server kod narx nom | umumiy_kalit</code>\n\n"
                  "Masalan:\n<code>1 russia 3600 Rossiya | russia\n2 0 3400 Rossiya | russia</code>\n\n"
                  "«umumiy_kalit» — «Eng arzon» rejimida turli serverlardagi bir xil davlatni "
                  "birlashtiradi (ixtiyoriy). Qo'lda qo'shilganlar avto-yangilashda o'chmaydi.", adm_back())


@admin.message(PriceAdd.wait)
async def pr_save(m: Message, state: FSMContext):
    n, errs = 0, []
    for line in (m.text or "").splitlines():
        body, _, ck = line.partition("|")
        p = body.split(maxsplit=3)
        if len(p) < 4 or not p[0].isdigit() or not p[2].isdigit():
            errs.append(line)
            continue
        await db.execute(
            "INSERT OR REPLACE INTO prices(server,code,name,price,ckey,cost,custom) VALUES(?,?,?,?,?,0,1)",
            (int(p[0]), p[1], p[3].strip(), int(p[2]), (ck.strip() or p[1]).lower()))
        n += 1
    await state.clear()
    msg = f"✅ Saqlandi: {n}"
    if errs:
        msg += "\n❌ Xato qatorlar:\n" + html.escape("\n".join(errs))
    await m.answer(msg, reply_markup=adm_back())


@admin.callback_query(F.data == "pr:del")
async def pr_del(c: CallbackQuery, state: FSMContext):
    await state.set_state(PriceDel.wait)
    await show(c, "O'chirish: <code>server kod</code> (masalan <code>2 0</code>)", adm_back())


@admin.message(PriceDel.wait)
async def pr_del_save(m: Message, state: FSMContext):
    p = (m.text or "").split()
    if len(p) != 2 or not p[0].isdigit():
        return await m.answer("Format: server kod")
    await db.execute("DELETE FROM prices WHERE server=? AND code=?", (int(p[0]), p[1]))
    await state.clear()
    await m.answer("✅ O'chirildi", reply_markup=adm_back())


# ── davlatlarni boshqarish (yashirish / qadash / alohida narx)
@admin.callback_query(F.data == "adm:cty")
async def cty_servers(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[Btn(f"Server {r['server']} ({r['n']})", f"ac:{r['server']}:0")]
            for r in await db.fetchall("SELECT server, COUNT(*) n FROM prices GROUP BY server ORDER BY server")]
    rows.append([Btn("◀️ Admin panel", "adm")])
    await show(c, "🌍 Qaysi server davlatlari?", markup(rows))


@admin.callback_query(F.data.startswith("ac:"))
async def cty_list_cb(c: CallbackQuery):
    _, s, page = c.data.split(":")
    await cty_list(c, int(s), int(page))


async def cty_list(c: CallbackQuery, s: int, page: int = 0):
    rows = await db.fetchall("SELECT * FROM prices WHERE server=? ORDER BY pinned DESC, name", (s,))
    pages = max(1, math.ceil(len(rows) / PAGE))
    page = min(max(page, 0), pages - 1)
    kb = [[Btn(f"{'🙈' if r['hidden'] else ''}{'📌' if r['pinned'] else ''}{'✏️' if r['custom'] else ''} "
               f"{r['name']} · {fmt(r['price'])}", f"ar:{s}:{r['code']}")]
          for r in rows[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(Btn("◀️", f"ac:{s}:{page - 1}"))
    nav.append(Btn(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(Btn("▶️", f"ac:{s}:{page + 1}"))
    kb += [nav, [Btn("◀️ Orqaga", "adm:cty")]]
    await show(c, f"🌍 Server {s}\n🙈 yashirin · 📌 qadalgan · ✏️ qo'lda narx", markup(kb))


async def cty_detail(c: CallbackQuery, s: int, code: str):
    r = await db.fetchone("SELECT * FROM prices WHERE server=? AND code=?", (s, code))
    if not r:
        return await c.answer("Topilmadi", show_alert=True)
    text = (f"🌍 <b>{html.escape(r['name'])}</b>\nServer {s} · kod <code>{html.escape(code)}</code> · "
            f"kalit <code>{html.escape(r['ckey'] or '—')}</code>\n"
            f"Tannarx: {fmt(r['cost'] or 0)} · Sotish narxi: <b>{fmt(r['price'])}</b>"
            f"{' (qo' + chr(39) + 'lda)' if r['custom'] else ''}\n"
            f"Holati: {'🙈 yashirin' if r['hidden'] else '👁 ko' + chr(39) + 'rinadi'}"
            f"{' · 📌 qadalgan' if r['pinned'] else ''}")
    kb = markup([
        [Btn("👁 Ko'rsatish" if r["hidden"] else "🙈 Yashirish", f"ah:{s}:{code}")],
        [Btn("📌 Qadashni olish" if r["pinned"] else "📌 Tepaga qadash", f"ap:{s}:{code}")],
        [Btn("✏️ Narx belgilash", f"apr:{s}:{code}")] + ([Btn("♻️ Avto narx", f"acr:{s}:{code}")] if r["custom"] else []),
        [Btn("🗑 O'chirish", f"adl:{s}:{code}")],
        [Btn("◀️ Orqaga", f"ac:{s}:0")]])
    await show(c, text, kb)


@admin.callback_query(F.data.startswith("ar:"))
async def cty_open(c: CallbackQuery):
    _, s, code = c.data.split(":", 2)
    await cty_detail(c, int(s), code)


@admin.callback_query(F.data.startswith(("ah:", "ap:", "acr:", "adl:")))
async def cty_action(c: CallbackQuery):
    act, s, code = c.data.split(":", 2)
    s = int(s)
    if act == "ah":
        await db.execute("UPDATE prices SET hidden=1-COALESCE(hidden,0) WHERE server=? AND code=?", (s, code))
    elif act == "ap":
        await db.execute("UPDATE prices SET pinned=1-COALESCE(pinned,0) WHERE server=? AND code=?", (s, code))
    elif act == "acr":
        await db.execute("UPDATE prices SET custom=0 WHERE server=? AND code=?", (s, code))
        await c.answer("Keyingi yangilashda avto narx qaytadi", show_alert=True)
    elif act == "adl":
        await db.execute("DELETE FROM prices WHERE server=? AND code=?", (s, code))
        return await cty_list(c, s)
    await cty_detail(c, s, code)


@admin.callback_query(F.data.startswith("apr:"))
async def cty_price(c: CallbackQuery, state: FSMContext):
    _, s, code = c.data.split(":", 2)
    await state.set_state(CtyPrice.wait)
    await state.update_data(s=int(s), code=code)
    await show(c, "Yangi sotish narxini so'mda yozing (faqat raqam):", adm_back())


@admin.message(CtyPrice.wait)
async def cty_price_save(m: Message, state: FSMContext):
    txt = (m.text or "").replace(" ", "")
    if not txt.isdigit():
        return await m.answer("❌ Faqat raqam")
    d = await state.get_data()
    await db.execute("UPDATE prices SET price=?, custom=1 WHERE server=? AND code=?", (int(txt), d["s"], d["code"]))
    await state.clear()
    await m.answer("✅ Narx saqlandi (avto-yangilash bu narxni o'zgartirmaydi)", reply_markup=adm_back())


# ── foydalanuvchilarni boshqarish
async def user_card(uid: int):
    u = await db.fetchone("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return None, None
    o = await db.fetchone("SELECT COUNT(*) n, COALESCE(SUM(price),0) s FROM orders WHERE user_id=? AND status='done'", (uid,))
    refs = await db.fetchone("SELECT COUNT(*) n FROM users WHERE ref_by=?", (uid,))
    text = (f"👤 <b>{html.escape(u['name'] or '—')}</b> (<code>{uid}</code>)\n"
            f"💰 Balans: {fmt(u['balance'])} so'm\n📦 Sotib olgan: {o['n']} ta · {fmt(o['s'])} so'm\n"
            f"👥 Takliflar: {refs['n']}\n📅 {datetime.fromtimestamp(u['joined']).strftime('%d.%m.%Y')}\n"
            f"Holat: {'⛔️ bloklangan' if u['banned'] else '✅ faol'}")
    kb = markup([[Btn("✅ Blokdan chiqarish" if u["banned"] else "🚫 Bloklash", f"ub:{uid}")],
                 [Btn("💰 Balans", f"um:{uid}"), Btn("📦 Buyurtmalar", f"uo:{uid}")],
                 [Btn("📒 Balans tarixi", f"ul:{uid}")], [Btn("◀️ Admin panel", "adm")]])
    return text, kb


@admin.callback_query(F.data == "adm:user")
async def user_find(c: CallbackQuery, state: FSMContext):
    await state.set_state(UserFind.wait)
    await show(c, "Foydalanuvchi ID sini yuboring:", adm_back())


@admin.message(UserFind.wait)
async def user_find_res(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit():
        return await m.answer("❌ ID raqam yuboring")
    text, kb = await user_card(int(m.text.strip()))
    if not text:
        return await m.answer("❌ Topilmadi")
    await state.clear()
    await m.answer(text, reply_markup=kb)


@admin.callback_query(F.data.startswith("ub:"))
async def user_ban(c: CallbackQuery):
    uid = int(c.data.split(":")[1])
    if uid in ADMINS:
        return await c.answer("Adminni bloklab bo'lmaydi", show_alert=True)
    await db.execute("UPDATE users SET banned=1-COALESCE(banned,0) WHERE id=?", (uid,))
    text, kb = await user_card(uid)
    await show(c, text, kb)


@admin.callback_query(F.data.startswith("uo:"))
async def user_orders(c: CallbackQuery):
    uid = int(c.data.split(":")[1])
    rows = await db.fetchall("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15", (uid,))
    lines = [f"#{r['id']} {r['status']} · +{r['phone']} · {html.escape(r['country'])} · {fmt(r['price'])} · S{r['server']}"
             for r in rows]
    await show(c, "📦 <b>Buyurtmalar</b>\n\n" + ("\n".join(lines) or "—"), markup([[Btn("◀️ Orqaga", "adm:user")]]))


@admin.callback_query(F.data.startswith("ul:"))
async def user_log(c: CallbackQuery):
    uid = int(c.data.split(":")[1])
    rows = await db.fetchall("SELECT * FROM balance_log WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,))
    lines = [f"{'➕' if r['amount'] > 0 else '➖'} {fmt(abs(r['amount']))} · {html.escape(r['reason'] or '')} · "
             f"{datetime.fromtimestamp(r['created']).strftime('%d.%m %H:%M')}" for r in rows]
    await show(c, "📒 <b>Balans tarixi</b>\n\n" + ("\n".join(lines) or "—"), markup([[Btn("◀️ Orqaga", "adm:user")]]))


@admin.callback_query(F.data.startswith("um:"))
async def user_bal(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split(":")[1])
    await state.set_state(BalEdit.wait)
    await state.update_data(uid=uid)
    await show(c, f"<code>{uid}</code> balansiga qo'shiladigan summa (ayirish uchun minus: <code>-5000</code>):", adm_back())


@admin.message(BalEdit.wait)
async def bal_save(m: Message, state: FSMContext):
    try:
        uid, amount = ((await state.get_data())["uid"], int(m.text.strip()))
    except Exception:
        return await m.answer("❌ Faqat butun son yozing")
    if not await db.fetchone("SELECT 1 FROM users WHERE id=?", (uid,)):
        return await m.answer("❌ Foydalanuvchi topilmadi")
    await add_balance(uid, amount, "Admin tomonidan")
    await state.clear()
    await m.answer(f"✅ Yangi balans: {fmt(await get_balance(uid))} so'm", reply_markup=adm_back())
    if amount > 0:
        await safe_send(uid, t("topup_approved", amount=fmt(amount)))


# ── sozlamalar
SETTINGS = {
    "ref:percent": ("Referral foizi (0 = o'chiq)", float, "3"),
    "proof:chat": ("Isbot kanali: @username yoki ID (0 = o'chiq). Bot kanalda admin bo'lsin", str, "0"),
    "alert:min": ("Provayder balansi chegarasi, provayder valyutasida (0 = o'chiq)", float, "0"),
    "sync:interval": ("Narxlarni yangilash oralig'i, daqiqa (1–1440)", int, "5"),
}


@admin.callback_query(F.data == "adm:set")
async def settings_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    maint = await get_setting("maint", "0") == "1"
    rows = [[Btn(f"🛠 Texnik tanaffus: {'ON ✅' if maint else 'OFF'}", "st_maint")]]
    text = "⚙️ <b>Sozlamalar</b>\n"
    for k, (label, _, d) in SETTINGS.items():
        text += f"\n• {label}: <b>{html.escape(await get_setting(k, d))}</b>"
        rows.append([Btn(label.split(" (")[0].split(":")[0], f"st:{k}")])
    rows.append([Btn("◀️ Admin panel", "adm")])
    await show(c, text, markup(rows))


@admin.callback_query(F.data == "st_maint")
async def st_maint(c: CallbackQuery, state: FSMContext):
    await set_setting("maint", "0" if await get_setting("maint", "0") == "1" else "1")
    await settings_menu(c, state)


@admin.callback_query(F.data.startswith("st:"))
async def st_edit(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":", 1)[1]
    await state.set_state(SettingEdit.wait)
    await state.update_data(key=key)
    await show(c, f"{SETTINGS[key][0]}\n\nYangi qiymatni yuboring:", adm_back())


@admin.message(SettingEdit.wait)
async def st_save(m: Message, state: FSMContext):
    key = (await state.get_data())["key"]
    typ = SETTINGS[key][1]
    try:
        val = typ((m.text or "").strip())
        if key == "sync:interval":
            val = min(max(val, 1), 1440)
        if typ is float and val < 0:
            raise ValueError
    except Exception:
        return await m.answer("❌ Qiymat noto'g'ri")
    await set_setting(key, str(val))
    await state.clear()
    await m.answer("✅ Saqlandi", reply_markup=adm_back())



# ───────────── provayder kalitlari (har bir bot o'zinikini kiritadi) ─────────────
async def reload_providers():
    cfg = {}
    for r in await db.fetchall("SELECT key, value FROM settings WHERE key LIKE 'srv:%'"):
        try:
            cfg[int(r["key"][4:])] = json.loads(r["value"])
        except Exception:
            pass
    providers.load(cfg)


@admin.message(Command("server"))
async def cmd_server(m: Message):
    """/server N 5sim TOKEN | /server N activate URL KEY [XIZMAT] | /server N off | /server"""
    args = (m.text or "").split()[1:]
    if args:                                  # kalit chatda qolmasin
        try:
            await m.delete()
        except Exception:
            pass
    try:
        if not args:
            raise ValueError
        n = int(args[0])
        assert 1 <= n <= 9
        if args[1] == "off":
            await db.execute("DELETE FROM settings WHERE key=?", (f"srv:{n}",))
        elif args[1] == "5sim":
            await set_setting(f"srv:{n}", json.dumps({"type": "5sim", "token": args[2]}))
        elif args[1] == "activate":
            await set_setting(f"srv:{n}", json.dumps({"type": "activate", "url": args[2], "key": args[3],
                                                      "service": args[4] if len(args) > 4 else "tg"}))
        else:
            raise ValueError
    except Exception:
        return await m.answer(
            "🖥 <b>Serverlar</b> (hozir: %s)\n\n"
            "<code>/server 1 5sim TOKEN</code>\n"
            "<code>/server 2 activate URL KEY tg</code>\n"
            "<code>/server 1 off</code> — o'chirish\n\n"
            "Raqam 1–9. Kalit xabari avtomatik o'chiriladi." % (list(PROVIDERS) or "yo'q"))
    await reload_providers()
    await m.answer(f"✅ Saqlandi. Faol serverlar: {list(PROVIDERS) or 'yo`q'}")

# ───────────── ishga tushirish ─────────────
async def main():
    global bot, BOT_USERNAME
    await db.init()
    await reload_providers()
    for r in await db.fetchall("SELECT key, value FROM settings WHERE key LIKE 't:%'"):
        CACHE[r["key"][2:]] = r["value"]
    for r in await db.fetchall("SELECT id FROM admins"):
        ADMINS.add(r["id"])
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    dp = Dispatcher(storage=MemoryStorage())
    try:
        import plan_limits; plan_limits.install(dp)   # tarif limitlari
    except ImportError:
        pass
    dp.message.outer_middleware(MainMW())
    dp.callback_query.outer_middleware(MainMW())
    dp.include_router(admin)
    dp.include_router(user)
    asyncio.create_task(auto_sync_loop())
    asyncio.create_task(monitor_loop())
    for r in await db.fetchall("SELECT id FROM orders WHERE status='waiting'"):
        asyncio.create_task(watch(r["id"]))
    log.info("Providerlar: %s", list(PROVIDERS))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
