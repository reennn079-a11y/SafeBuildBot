import asyncio, hashlib, json, logging, os, re, secrets, sqlite3, string, time
from pathlib import Path
from html import escape
from string import Formatter
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import BaseFilter, Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (ChatJoinRequest, ChatMemberUpdated, LabeledPrice, PreCheckoutQuery, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import runner
from config import (BOT_TYPES, BUILDER_TOKEN, CARD_INFO, CLICK_PROVIDER_TOKEN, MIN_TOPUP, MIN_TRANSFER, SEED_BALANCES, PAYME_PROVIDER_TOKEN, PLAN_DAYS, PLANS,
                    REF_BONUS, SITE_URL, SUPER_ADMINS, TRIAL_DAYS, TRIAL_PLAN, WARN_HOURS)
from db import add_user, get_setting, ins, one, q, redeem_promo, set_setting, transfer
import i18n
from i18n import CHOOSE_LANG, S, LANGS, all_texts, money, register_type_title, t, tariffs_text

logging.basicConfig(level=logging.INFO)
router = Router()

BTN_KEYS = ["b_create", "b_my", "b_income", "b_cab", "b_topup", "b_transfer", "b_ref", "b_site", "b_guide", "b_help", "b_tariffs",
            "b_special", "b_admin", "b_promo"]
B_MENUS = "🧩 Menyularni boshqarish"                   # eski admin tugmasi (eski klaviaturalar ishlashda qolsin)
MAX_CUSTOM_MENUS = 30
ENV_ADMINS = tuple(SUPER_ADMINS)                       # .env dagi asosiy adminlar (ularni o'chirib bo'lmaydi)


def menu_buttons():
    # barcha menyu tugmalari matni (admin nomini o'zgartirganlari ham)
    return set().union(*(all_texts(k) for k in BTN_KEYS)) | {B_MENUS}


class BtnFilter(BaseFilter):
    # Menyu tugmasi filtri: tugma matni barcha tillarda va admin o'zgartirgan nomda ham mos keladi (har safar yangidan tekshiriladi)
    def __init__(self, key):
        self.key = key

    async def __call__(self, message: Message) -> bool:
        return (message.text or "") in all_texts(self.key)


def btn(key):
    return BtnFilter(key)


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u) and u.id in SUPER_ADMINS


ADMIN = IsAdmin()


def save_extra_admins():
    set_setting("extra_admins", ",".join(str(a) for a in SUPER_ADMINS if a not in ENV_ADMINS))


def load_state():
    # bazadan: qo'shimcha adminlar va admin o'zgartirgan nom/matnlar
    for x in (get_setting("extra_admins", "") or "").split(","):
        if x.strip().isdigit() and int(x) not in SUPER_ADMINS:
            SUPER_ADMINS.append(int(x))
    for k, pl in PLANS.items():                      # admin o'zgartirgan tarif narx/limitlari
        try:
            d = json.loads(get_setting(f"plan:{k}", "") or "{}")
            for f in ("price", "daily", "rate", "monthly"):
                if isinstance(d.get(f), int) and d[f] > 0:
                    pl[f] = d[f]
            if isinstance(d.get("design"), bool):
                pl["design"] = d["design"]
                pl["lang"] = d["design"]
        except Exception:
            pass
    i18n.OVR.clear()
    for r in q("SELECT lang, key, value FROM overrides"):
        i18n.OVR[(r["lang"], r["key"])] = r["value"]


load_state()


def custom_menus():
    """Admin qo'shgan menyular (tugmalar)."""
    return q("SELECT * FROM menus ORDER BY pos, id")


def custom_titles():
    return {r["title"] for r in custom_menus()}


def menu(lang, uid=None):
    b = lambda k: KeyboardButton(text=t(lang, k))
    rows = [[b("b_create")], [b("b_my"), b("b_cab")], [b("b_ref"), b("b_topup")], [b("b_guide"), b("b_help")], [b("b_special")]]
    extra = [KeyboardButton(text=r["title"]) for r in custom_menus()]       # adminlar qo'shgan menyular
    rows += [extra[i:i + 2] for i in range(0, len(extra), 2)]
    if uid in SUPER_ADMINS:
        rows.append([b("b_admin")])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=rows)


def lang_of(uid):
    r = one("SELECT lang FROM users WHERE id=?", uid)
    return r["lang"] if r and r["lang"] in LANGS else "uz"


def T(uid, key, **kw): return t(lang_of(uid), key, **kw)
def som(n, uid=None): return money(n, lang_of(uid) if uid else "uz")


@router.message.outer_middleware()
async def menu_resets_state(handler, event, data):
    """Menyu tugmasi yoki buyruq (/admin, /menus...) bosilsa, kutilayotgan holat (token, summa, chek...)
    bekor qilinadi — aks holda ular token/summa/murojaat matni sifatida o'qilib ketardi."""
    text = event.text or ""
    state = data.get("state")
    if state and (text in menu_buttons() or text in custom_titles() or (text.startswith("/") and text != "/cancel")):
        await state.clear()
    return await handler(event, data)


class St(StatesGroup):
    token = State()
    amount = State()
    method = State()
    receipt = State()
    help = State()
    design = State()
    tr_id = State()             # pul o'tkazish: qabul qiluvchi ID
    tr_amt = State()            # pul o'tkazish: summa
    tr_ok = State()             # pul o'tkazish: tasdiqlash
    mtitle = State()            # yangi menyu nomi
    mcontent = State()          # yangi menyu mazmuni
    special = State()           # maxsus bot buyurtmasi
    rename = State()            # admin: tugma nomini o'zgartirish
    edtext = State()            # admin: matnni tahrirlash
    addadmin = State()          # admin: yangi admin qo'shish
    promo = State()             # foydalanuvchi: promokod kiritish
    bcast = State()             # admin: xabar yuborish
    addch = State()             # admin: majburiy obuna kanali
    chmsg = State()             # admin: kanalga obuna bo'lganda yuboriladigan xabar
    pnew = State()              # admin: yangi promokod
    tedit = State()             # admin: tarif qiymati


PROVIDERS = {"click": ("Click", CLICK_PROVIDER_TOKEN), "payme": ("Payme", PAYME_PROVIDER_TOKEN)}


def bal(uid):
    r = one("SELECT balance FROM users WHERE id=?", uid)
    return r["balance"] if r else 0


def left(ts, uid):
    """Qolgan vaqtni foydalanuvchi tilida qaytaradi."""
    s = int(ts - time.time())
    if s <= 0: return T(uid, "lt_ended")
    d, h = s // 86400, s % 86400 // 3600
    return T(uid, "lt_dh", d=d, h=h) if d else T(uid, "lt_hm", h=h, m=s % 3600 // 60)


async def check_token(token):
    """Token to'g'ri bo'lsa bot ma'lumotini qaytaradi, aks holda None. Sessiya doim yopiladi."""
    if ":" not in token or " " in token:
        return None
    try:
        tb = Bot(token)                                  # noto'g'ri formatdagi tokenda TokenValidationError beradi
    except Exception:
        return None
    try:
        me = await tb.get_me()
        await tb.delete_webhook()
        return me
    except Exception:
        return None
    finally:
        await tb.session.close()


def tariffs_on():
    """Umumiy tarif tizimi yoqiqmi. O'chiq bo'lsa: hamma bot cheksiz, muddatsiz va hamma imkoniyat bilan ishlaydi."""
    return str(get_setting("tariffs_on", "1")) == "1"


def is_expired(b):
    return bool(tariffs_on() and b["expires"] and b["expires"] <= time.time())


def has_design(b):
    return (not tariffs_on()) or bool((PLANS.get(b["plan"]) or {}).get("design"))


def limits_env(plan):
    if not tariffs_on():
        return {"PLAN_NAME": "Bepul", "PLAN_DAILY": "0", "PLAN_RATE": "0", "PLAN_MONTHLY": "0", "PLAN_LANG": "1"}
    p = PLANS.get(plan) or PLANS[TRIAL_PLAN]
    # PLAN_LANG=1 — botda til tanlash yoqiladi (faqat Pro va undan yuqori tariflar: lang bayrog'i)
    return {"PLAN_NAME": p["name"], "PLAN_DAILY": str(p["daily"]), "PLAN_RATE": str(p["rate"]), "PLAN_MONTHLY": str(p["monthly"]),
            "PLAN_LANG": "1" if p.get("lang", p.get("design")) else "0"}


async def launch(bid):
    """Botni o'z tarifi limitlari bilan ishga tushiradi."""
    b = one("SELECT * FROM bots WHERE id=?", bid)
    if not b: return False
    env = limits_env(b["plan"])
    base = os.getenv("WEB_BASE_PORT")                      # web admin panel (Super yuklovchi): har bir botga alohida port
    if base and base.isdigit():
        env["WEB_PORT"] = str(int(base) + bid)
        env["WEB_KEY"] = hashlib.sha256(f"{b['token']}:web".encode()).hexdigest()[:20]
    return await runner.start(bid, BOT_TYPES[b["type"]][1], b["token"], [b["owner"]], env)


def usage(bid, plan):
    """Botning limits.db faylidan bugungi aktivlar va oylik so'rovlarni o'qiydi."""
    try:
        con = sqlite3.connect(f"file:{runner.DATA_DIR / str(bid) / 'limits.db'}?mode=ro", uri=True)
        try:
            day, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
            d = con.execute("SELECT COUNT(*) FROM daily WHERE day=?", (day,)).fetchone()[0]
            r = con.execute("SELECT n FROM monthly WHERE month=?", (month,)).fetchone()
            return d, (r[0] if r else 0)
        finally:
            con.close()
    except Exception:
        return 0, 0


# ---------------- /start, til, menyu ----------------
def lang_kb():
    kb = InlineKeyboardBuilder()
    for k, name in LANGS.items():
        kb.button(text=name, callback_data=f"lang:{k}")
    kb.adjust(2)
    return kb.as_markup()


@router.message(CommandStart())
async def start(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ref = int(command.args) if command.args and command.args.isdigit() else None
    add_user(m.from_user.id, m.from_user.full_name, ref)
    await m.answer(CHOOSE_LANG, reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def pick_lang(c: CallbackQuery):
    lang = c.data[5:]
    if lang not in LANGS: return await c.answer()
    add_user(c.from_user.id, c.from_user.full_name)
    q("UPDATE users SET lang=? WHERE id=?", lang, c.from_user.id)
    try: await c.message.delete()
    except Exception: pass
    await c.message.answer(t(lang, "welcome", days=TRIAL_DAYS), reply_markup=menu(lang, c.from_user.id))
    await c.message.answer(tariffs_text(lang))
    await c.answer()


@router.message(btn("b_tariffs"))
async def tariffs(m: Message):
    await m.answer(tariffs_text(lang_of(m.from_user.id)))


@router.message(btn("b_cab"))
async def cab(m: Message):
    n = one("SELECT COUNT(*) c FROM bots WHERE owner=?", m.from_user.id)["c"]
    await m.answer(t(lang_of(m.from_user.id), "cab", uid=m.from_user.id, bal=som(bal(m.from_user.id), m.from_user.id), n=n), parse_mode="HTML",
                   reply_markup=cab_kb(m.from_user.id))


def cab_kb(uid):
    # Hisobim ichidagi qo'shimcha bo'limlar (avval pastki menyuda edi)
    kb = InlineKeyboardBuilder()
    for k, d in (("b_transfer", "cb:tr"), ("b_income", "cb:inc"), ("b_tariffs", "cb:tar"), ("b_site", "cb:site"), ("b_promo", "cb:promo")):
        kb.button(text=T(uid, k), callback_data=d)
    kb.adjust(2)
    return kb.as_markup()


@router.callback_query(F.data.startswith("cb:"))
async def cab_cb(c: CallbackQuery, state: FSMContext):
    uid, act = c.from_user.id, c.data[3:]
    if act == "tr":
        await state.clear()
        await state.set_state(St.tr_id)
        await c.message.answer(T(uid, "tr_ask_id", min=som(min_transfer(), uid)))
    elif act == "inc":
        u = one("SELECT earned FROM users WHERE id=?", uid)
        cnt = one("SELECT COUNT(*) c FROM users WHERE ref_by=?", uid)["c"]
        paid = one("SELECT COUNT(*) c FROM users WHERE ref_by=? AND ref_paid=1", uid)["c"]
        await c.message.answer(T(uid, "income", cnt=cnt, paid=paid, earned=som(u["earned"] if u else 0, uid)), parse_mode="HTML")
    elif act == "tar":
        await c.message.answer(tariffs_text(lang_of(uid)))
    elif act == "promo":
        await state.set_state(St.promo)
        await c.message.answer(T(uid, "promo_ask"))
    elif act == "site":
        await c.message.answer(T(uid, "site_title"), reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=T(uid, "site_btn"), url=SITE_URL)]]))
    await c.answer()


@router.message(btn("b_site"))
async def site(m: Message):
    uid = m.from_user.id
    await m.answer(T(uid, "site_title"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(uid, "site_btn"), url=SITE_URL)]]))


@router.message(btn("b_guide"))
async def guide(m: Message):
    await m.answer(T(m.from_user.id, "guide", days=TRIAL_DAYS), parse_mode="HTML")


# ---------------- Referal / Daromad ----------------
@router.message(btn("b_ref"))
async def ref(m: Message, bot: Bot):
    uid = m.from_user.id
    me = await bot.get_me()
    cnt = one("SELECT COUNT(*) c FROM users WHERE ref_by=?", uid)["c"]
    await m.answer(T(uid, "ref", bonus=som(REF_BONUS, uid), link=f"https://t.me/{me.username}?start={uid}", cnt=cnt), parse_mode="HTML")


@router.message(btn("b_income"))
async def income(m: Message):
    uid = m.from_user.id
    u = one("SELECT earned FROM users WHERE id=?", uid)
    cnt = one("SELECT COUNT(*) c FROM users WHERE ref_by=?", uid)["c"]
    paid = one("SELECT COUNT(*) c FROM users WHERE ref_by=? AND ref_paid=1", uid)["c"]
    await m.answer(T(uid, "income", cnt=cnt, paid=paid, earned=som(u["earned"] if u else 0, uid)), parse_mode="HTML")


# ---------------- Bot yaratish ----------------
@router.message(btn("b_create"))
async def create(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    kb = InlineKeyboardBuilder()
    for k, (_, _, price) in BOT_TYPES.items():
        kb.button(text=T(uid, "type_" + k) + (f" — {som(price, uid)}" if price else ""), callback_data=f"t:{k}")
    kb.adjust(2)
    await m.answer(T(uid, "pick_type"), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("t:"))
async def pick_type(c: CallbackQuery, state: FSMContext):
    k = c.data[2:]
    uid = c.from_user.id
    if k not in BOT_TYPES: return await c.answer()
    price = BOT_TYPES[k][2]
    if bal(uid) < price:
        return await c.answer(T(uid, "need_balance", price=som(price, uid)), show_alert=True)
    await state.set_state(St.token)
    await state.update_data(type=k)
    await c.message.answer(T(uid, "type_chosen", name=T(uid, "type_" + k)), parse_mode="HTML")
    await c.answer()


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(T(m.from_user.id, "cancelled"), reply_markup=menu(lang_of(m.from_user.id), m.from_user.id))


@router.message(St.token, F.text)
async def got_token(m: Message, state: FSMContext):
    uid = m.from_user.id
    token = m.text.strip()
    k = (await state.get_data())["type"]
    if one("SELECT 1 FROM bots WHERE token=?", token):
        return await m.answer(T(uid, "token_dup"))
    me = await check_token(token)
    if not me:
        return await m.answer(T(uid, "token_bad"))
    price = BOT_TYPES[k][2]
    if bal(uid) < price:
        await state.clear()
        return await m.answer(T(uid, "no_balance"))
    await state.clear()
    try: await m.delete()                                # token chatda qolib ketmasin
    except Exception: pass
    q("UPDATE users SET balance=balance-? WHERE id=?", price, uid)
    now = int(time.time())
    bid = ins("INSERT INTO bots(owner,type,token,username,created,expires,plan) VALUES(?,?,?,?,?,?,?)",
              uid, k, token, me.username, now, now + TRIAL_DAYS * 86400, TRIAL_PLAN)
    wait = await m.answer(T(uid, "starting"))
    if not await launch(bid):
        q("UPDATE users SET balance=balance+? WHERE id=?", price, uid)
        q("DELETE FROM bots WHERE id=?", bid)
        runner.purge(bid)
        return await wait.edit_text(T(uid, "start_fail"))
    await wait.edit_text(T(uid, "created", username=me.username, days=TRIAL_DAYS, hours=WARN_HOURS), reply_markup=None)


# ---------------- Botlarim ----------------
def my_kb(uid):
    kb = InlineKeyboardBuilder()
    for b in q("SELECT * FROM bots WHERE owner=? ORDER BY id", uid):
        st = "🟢" if b["running"] else "🔴"
        kb.button(text=f"{st} @{b['username']} · {T(uid, 'type_' + b['type'])}", callback_data=f"b:{b['id']}")
    kb.adjust(1)
    return kb.as_markup()


@router.message(btn("b_my"))
async def my(m: Message):
    uid = m.from_user.id
    if not one("SELECT 1 FROM bots WHERE owner=?", uid):
        return await m.answer(T(uid, "no_bots"))
    await m.answer(T(uid, "my_list"), reply_markup=my_kb(uid))


def own(c, bid):
    return one("SELECT * FROM bots WHERE id=? AND owner=?", bid, c.from_user.id)


async def show_card(c: CallbackQuery, b):
    uid = c.from_user.id
    plan = PLANS.get(b["plan"]) or PLANS[TRIAL_PLAN]
    expired = is_expired(b)
    kb = InlineKeyboardBuilder()
    if not expired:
        kb.button(text=T(uid, "btn_stop") if b["running"] else T(uid, "btn_start"), callback_data=f"{'x' if b['running'] else 'r'}:{b['id']}")
    if tariffs_on():
        kb.button(text=T(uid, "btn_plan"), callback_data=f"pl:{b['id']}")
    kb.button(text=T(uid, "btn_design") if has_design(b) else T(uid, "btn_design_lock"), callback_data=f"g:{b['id']}")
    kb.button(text=T(uid, "btn_delete"), callback_data=f"d:{b['id']}")
    kb.button(text=T(uid, "btn_back"), callback_data="back")
    kb.adjust(1)
    st = T(uid, "st_exp") if expired else (T(uid, "st_run") if b["running"] else T(uid, "st_stop"))
    d, mo = usage(b["id"], plan)
    try:
        await _edit_card(c, b, uid, plan, st, d, mo, kb)
    except Exception:                                    # "message is not modified" kabi xatolar e'tiborsiz
        pass


async def _edit_card(c, b, uid, plan, st, d, mo, kb):
    free = not tariffs_on()
    pname = "🆓 Bepul" if free else plan["name"]
    exp = T(uid, "forever") if (free or not b["expires"]) else left(b["expires"], uid)
    lim_d, lim_m = ("∞", "∞") if free else (plan["daily"], plan["monthly"])
    await c.message.edit_text(
        f"@{b['username']}\n{T(uid, 'lbl_type')}: {T(uid, 'type_' + b['type'])}\n{T(uid, 'lbl_status')}: {st}\n"
        f"{T(uid, 'lbl_plan')}: {pname}\n{T(uid, 'lbl_expiry')}: {exp}\n"
        f"{T(uid, 'lbl_usage')}: {T(uid, 'lbl_today')} {d}/{lim_d} · {T(uid, 'lbl_month')} {mo}/{lim_m}",
        reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("b:"))
async def bot_card(c: CallbackQuery):
    b = own(c, int(c.data[2:]))
    if not b: return await c.answer()
    await show_card(c, b)
    await c.answer()


@router.callback_query(F.data == "back")
async def back(c: CallbackQuery):
    await c.message.edit_text(T(c.from_user.id, "my_list"), reply_markup=my_kb(c.from_user.id))
    await c.answer()


@router.callback_query(F.data.regexp(r"^[xrd]:\d+$"))
async def bot_action(c: CallbackQuery):
    uid = c.from_user.id
    act, bid = c.data[0], int(c.data[2:])
    b = own(c, bid)
    if not b: return await c.answer()
    expired = is_expired(b)
    if act == "x":
        await runner.stop(bid); q("UPDATE bots SET running=0 WHERE id=?", bid)
    elif act == "r":
        if expired:
            return await c.answer(T(uid, "expired_alert"), show_alert=True)
        if not await launch(bid):
            return await c.answer(T(uid, "start_fail_alert"), show_alert=True)
        q("UPDATE bots SET running=1 WHERE id=?", bid)
    else:
        await runner.stop(bid); q("DELETE FROM bots WHERE id=?", bid); runner.purge(bid)
        await c.message.edit_text(T(uid, "deleted"))
        return await c.answer()
    await c.answer(T(uid, "done"))
    await show_card(c, own(c, bid))


# ---------------- Tarif tanlash ----------------
@router.callback_query(F.data.regexp(r"^pl:\d+$"))
async def plan_menu(c: CallbackQuery):
    uid = c.from_user.id
    bid = int(c.data[3:])
    if not own(c, bid): return await c.answer()
    if not tariffs_on():
        return await c.answer("🆓 Hozir tariflar o'chirilgan — hamma botlar bepul va cheksiz.", show_alert=True)
    kb = InlineKeyboardBuilder()
    for key, p in PLANS.items():
        kb.button(text=f"{p['name']} — {som(p['price'], uid)}", callback_data=f"pp:{bid}:{key}")
    kb.button(text=T(uid, "btn_back"), callback_data=f"b:{bid}")
    kb.adjust(2)
    await c.message.edit_text(T(uid, "plan_pick", days=PLAN_DAYS, bal=som(bal(uid), uid), tariffs=tariffs_text(lang_of(uid))), reply_markup=kb.as_markup())
    await c.answer()


@router.callback_query(F.data.regexp(r"^pp:\d+:\w+$"))
async def plan_buy(c: CallbackQuery):
    uid = c.from_user.id
    _, bid, key = c.data.split(":")
    bid = int(bid)
    b = own(c, bid)
    p = PLANS.get(key)
    if not b or not p: return await c.answer()
    if not tariffs_on():
        return await c.answer("🆓 Hozir tariflar o'chirilgan.", show_alert=True)
    if bal(uid) < p["price"]:
        return await c.answer(T(uid, "need_balance", price=som(p["price"], uid)), show_alert=True)
    q("UPDATE users SET balance=balance-? WHERE id=?", p["price"], uid)
    new_exp = max(b["expires"] or 0, int(time.time())) + PLAN_DAYS * 86400
    q("UPDATE bots SET plan=?, expires=?, warned=0 WHERE id=?", key, new_exp, bid)
    if not await launch(bid):                            # yangi limitlar bilan qayta ishga tushadi
        q("UPDATE users SET balance=balance+? WHERE id=?", p["price"], uid)      # pul qaytariladi, tarif eskicha qoladi
        q("UPDATE bots SET plan=?, expires=?, running=0 WHERE id=?", b["plan"], b["expires"], bid)
        await c.answer(T(uid, "start_fail_alert"), show_alert=True)
    else:
        q("UPDATE bots SET running=1 WHERE id=?", bid)
        await c.answer(T(uid, "plan_done", username=b["username"], plan=p["name"], left=left(new_exp, uid)), show_alert=True)
    await show_card(c, own(c, bid))


# ---------------- Dizayn (Pro va yuqori tariflar) ----------------
DESIGN = {"n": ("d_name", 64), "d": ("d_desc", 512), "s": ("d_short", 120)}


@router.callback_query(F.data.regexp(r"^g:\d+$"))
async def design_menu(c: CallbackQuery):
    uid = c.from_user.id
    b = own(c, int(c.data[2:]))
    if not b: return await c.answer()
    if not has_design(b):
        return await c.answer(T(uid, "btn_design_lock"), show_alert=True)
    kb = InlineKeyboardBuilder()
    for k, (label, _) in DESIGN.items():
        kb.button(text=T(uid, label), callback_data=f"gs:{b['id']}:{k}")
    kb.button(text=T(uid, "btn_back"), callback_data=f"b:{b['id']}")
    kb.adjust(1)
    await c.message.edit_text(T(uid, "design_menu", username=b["username"]), reply_markup=kb.as_markup())
    await c.answer()


@router.callback_query(F.data.regexp(r"^gs:\d+:[nds]$"))
async def design_ask(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    _, bid, k = c.data.split(":")
    b = own(c, int(bid))
    if not b or not has_design(b): return await c.answer()
    await state.set_state(St.design)
    await state.update_data(bid=int(bid), field=k)
    await c.message.answer(T(uid, "d_ask", limit=DESIGN[k][1]))
    await c.answer()


@router.message(St.design, F.text)
async def design_set(m: Message, state: FSMContext):
    uid = m.from_user.id
    data = await state.get_data()
    b = one("SELECT * FROM bots WHERE id=? AND owner=?", data["bid"], uid)
    if not b or not (PLANS.get(b["plan"]) or {}).get("design"):
        await state.clear()
        return
    limit = DESIGN[data["field"]][1]
    if len(m.text) > limit:
        return await m.answer(T(uid, "d_toolong", limit=limit))
    await state.clear()
    tb = Bot(b["token"])
    try:
        if data["field"] == "n": await tb.set_my_name(name=m.text)
        elif data["field"] == "d": await tb.set_my_description(description=m.text)
        else: await tb.set_my_short_description(short_description=m.text)
        await m.answer(T(uid, "d_ok"))
    except Exception as e:
        await m.answer(T(uid, "d_fail", err=str(e)[:200]))
    finally:
        await tb.session.close()


# ---------------- Hisob to'ldirish ----------------
async def credit(bot: Bot, uid: int, amt: int):
    """Balansni to'ldiradi; taklif qilingan odam birinchi marta to'ldirsa — taklif qilganga bonus beradi."""
    q("UPDATE users SET balance=balance+? WHERE id=?", amt, uid)
    u = one("SELECT ref_by, ref_paid FROM users WHERE id=?", uid)
    if u and u["ref_by"] and not u["ref_paid"]:
        q("UPDATE users SET ref_paid=1 WHERE id=?", uid)
        q("UPDATE users SET balance=balance+?, earned=earned+? WHERE id=?", REF_BONUS, REF_BONUS, u["ref_by"])
        try: await bot.send_message(u["ref_by"], T(u["ref_by"], "ref_bonus", amt=som(REF_BONUS, u["ref_by"])))
        except Exception: pass


@router.message(btn("b_topup"))
async def topup(m: Message, state: FSMContext):
    uid = m.from_user.id
    await state.set_state(St.amount)
    await m.answer(T(uid, "topup_ask", min=som(MIN_TOPUP, uid)))


@router.message(St.amount, F.text)
async def amount(m: Message, state: FSMContext):
    uid = m.from_user.id
    s = m.text.replace(" ", "")
    if not s.isdigit() or int(s) < MIN_TOPUP:
        return await m.answer(T(uid, "topup_bad", min=som(MIN_TOPUP, uid)))
    await state.update_data(amount=int(s))
    await state.set_state(St.method)
    kb = InlineKeyboardBuilder()
    for k, (name, tok) in PROVIDERS.items():
        if tok:
            kb.button(text=T(uid, "pay_auto", name=name), callback_data=f"pay:{k}")
    kb.button(text=T(uid, "pay_cheque_btn"), callback_data="pay:cheque")
    kb.adjust(1)
    await m.answer(T(uid, "pay_pick", amt=som(int(s), uid)), reply_markup=kb.as_markup())


@router.callback_query(St.method, F.data.startswith("pay:"))
async def pay_method(c: CallbackQuery, state: FSMContext, bot: Bot):
    uid = c.from_user.id
    k = c.data[4:]
    amt = (await state.get_data())["amount"]
    if k == "cheque":
        await state.set_state(St.receipt)
        await c.message.answer(T(uid, "pay_cheque", card=CARD_INFO, amt=som(amt, uid)), parse_mode="HTML")
        return await c.answer()
    name, tok = PROVIDERS.get(k, (None, None))
    if not tok:
        return await c.answer(T(uid, "pay_off"), show_alert=True)
    await state.clear()
    await bot.send_invoice(chat_id=uid, title=T(uid, "inv_title"), description=T(uid, "inv_desc", amt=som(amt, uid)),
                           payload=f"topup:{uid}:{amt}", provider_token=tok, currency="UZS",
                           prices=[LabeledPrice(label=T(uid, "inv_label"), amount=amt * 100)])   # UZS: tiyin hisobida (x100)
    await c.answer()


@router.pre_checkout_query()
async def pre_checkout(pcq: PreCheckoutQuery):
    ok = pcq.invoice_payload.startswith(f"topup:{pcq.from_user.id}:")
    await pcq.answer(ok=ok, error_message=None if ok else T(pcq.from_user.id, "inv_bad"))


@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    sp = m.successful_payment
    uid = m.from_user.id
    if one("SELECT 1 FROM payments WHERE file_id=?", sp.telegram_payment_charge_id):
        return                                           # ikki marta qo'shilib ketmasin
    amt = sp.total_amount // 100
    ins("INSERT INTO payments(uid,amount,file_id,status,created) VALUES(?,?,?,?,?)", uid, amt, sp.telegram_payment_charge_id, "auto", int(time.time()))
    await credit(bot, uid, amt)
    await m.answer(T(uid, "paid_ok", amt=som(amt, uid), bal=som(bal(uid), uid)), reply_markup=menu(lang_of(uid), uid))
    for a in SUPER_ADMINS:
        try: await bot.send_message(a, f"💳 Avto to'lov: {escape(m.from_user.full_name)} (<code>{uid}</code>) — {som(amt)}", parse_mode="HTML")
        except Exception: pass


@router.message(St.receipt, F.photo)
async def receipt(m: Message, state: FSMContext, bot: Bot):
    uid = m.from_user.id
    amt = (await state.get_data())["amount"]
    await state.clear()
    pid = ins("INSERT INTO payments(uid,amount,file_id,created) VALUES(?,?,?,?)", uid, amt, m.photo[-1].file_id, int(time.time()))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"p:ok:{pid}"),
                                                InlineKeyboardButton(text="❌ Rad", callback_data=f"p:no:{pid}")]])
    for a in SUPER_ADMINS:
        try: await bot.send_photo(a, m.photo[-1].file_id, caption=f"💳 To'lov #{pid}\n👤 {escape(m.from_user.full_name)} (<code>{uid}</code>)\n💰 {som(amt)}", parse_mode="HTML", reply_markup=kb)
        except Exception: pass
    await m.answer(T(uid, "receipt_sent"), reply_markup=menu(lang_of(uid), uid))


@router.message(St.receipt)
async def receipt_bad(m: Message):
    await m.answer(T(m.from_user.id, "receipt_bad"), parse_mode="HTML")


@router.callback_query(F.data.startswith("p:"), ADMIN)
async def pay_decide(c: CallbackQuery, bot: Bot):
    _, act, pid = c.data.split(":")
    p = one("SELECT * FROM payments WHERE id=?", int(pid))
    if not p or p["status"] != "new":
        return await c.answer("Allaqachon ko'rib chiqilgan.", show_alert=True)
    if act == "ok":
        q("UPDATE payments SET status='ok' WHERE id=?", p["id"])
        await credit(bot, p["uid"], p["amount"])
        msg = T(p["uid"], "pay_approved", amt=som(p["amount"], p["uid"]))
    else:
        q("UPDATE payments SET status='no' WHERE id=?", p["id"])
        msg = T(p["uid"], "pay_rejected")
    try: await bot.send_message(p["uid"], msg)
    except Exception: pass
    try:
        await c.message.edit_caption(caption=(c.message.caption or "") + ("\n\n✅ Tasdiqlandi" if act == "ok" else "\n\n❌ Rad etildi"), reply_markup=None)
    except Exception: pass
    await c.answer()


# ---------------- Foydalanuvchilar o'rtasida pul o'tkazish ----------------
def min_transfer():
    try: return max(1, int(get_setting("min_transfer", MIN_TRANSFER)))
    except (TypeError, ValueError): return MIN_TRANSFER


@router.message(btn("b_transfer"))
async def tr_start(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    await state.set_state(St.tr_id)
    await m.answer(T(uid, "tr_ask_id", min=som(min_transfer(), uid)))


@router.message(St.tr_id, F.text)
async def tr_got_id(m: Message, state: FSMContext):
    uid, s = m.from_user.id, m.text.strip()
    if not s.isdigit():
        return await m.answer(T(uid, "tr_bad_id"))
    rid = int(s)
    if rid == uid:
        return await m.answer(T(uid, "tr_self"))
    r = one("SELECT id, name FROM users WHERE id=?", rid)
    if not r:
        return await m.answer(T(uid, "tr_nouser"))
    await state.update_data(rid=rid, rname=r["name"] or str(rid))
    await state.set_state(St.tr_amt)
    await m.answer(T(uid, "tr_ask_amt", name=r["name"] or rid, id=rid, bal=som(bal(uid), uid), min=som(min_transfer(), uid)))


@router.message(St.tr_amt, F.text)
async def tr_got_amt(m: Message, state: FSMContext):
    uid, s = m.from_user.id, m.text.replace(" ", "")
    mn = min_transfer()
    if not s.isdigit() or int(s) < mn:
        return await m.answer(T(uid, "tr_min", min=som(mn, uid)))
    amt = int(s)
    if amt > bal(uid):
        return await m.answer(T(uid, "tr_nofunds"))
    d = await state.get_data()
    await state.update_data(amt=amt)
    await state.set_state(St.tr_ok)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(uid, "tr_yes"), callback_data="tr:ok"),
                                                InlineKeyboardButton(text=T(uid, "tr_no"), callback_data="tr:no")]])
    await m.answer(T(uid, "tr_confirm", name=d["rname"], id=d["rid"], amt=som(amt, uid)), reply_markup=kb)


@router.callback_query(St.tr_ok, F.data.in_({"tr:ok", "tr:no"}))
async def tr_confirm(c: CallbackQuery, state: FSMContext, bot: Bot):
    uid = c.from_user.id
    d = await state.get_data()
    await state.clear()
    if c.data == "tr:no" or "amt" not in d:
        await c.message.edit_text(T(uid, "cancelled"))
        return await c.answer()
    amt, rid = d["amt"], d["rid"]
    mn = min_transfer()
    if amt < mn or not one("SELECT 1 FROM users WHERE id=?", rid) or not transfer(uid, rid, amt):
        await c.message.edit_text(T(uid, "tr_nofunds"))
        return await c.answer()
    await c.message.edit_text(T(uid, "tr_ok", amt=som(amt, uid), name=d["rname"], bal=som(bal(uid), uid)))
    try: await bot.send_message(rid, T(rid, "tr_got", name=c.from_user.full_name, amt=som(amt, rid)))
    except Exception: pass
    await c.answer()


# ---------------- Murojaat ----------------
@router.message(btn("b_help"))
async def help_(m: Message, state: FSMContext):
    await state.set_state(St.help)
    await m.answer(T(m.from_user.id, "help_ask"))


@router.message(St.help)
async def help_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    for a in SUPER_ADMINS:
        try:
            await bot.send_message(a, f"✉️ Murojaat: {escape(m.from_user.full_name)} (<code>{m.from_user.id}</code>)", parse_mode="HTML")
            await m.copy_to(a)
        except Exception: pass
    await m.answer(T(m.from_user.id, "help_sent"), reply_markup=menu(lang_of(m.from_user.id), m.from_user.id))


# ---------------- Maxsus bot (buyurtma) ----------------
@router.message(btn("b_special"))
async def special(m: Message, state: FSMContext):
    await state.set_state(St.special)
    await m.answer(T(m.from_user.id, "special_ask"))


@router.message(St.special)
async def special_send(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    for a in SUPER_ADMINS:
        try:
            await bot.send_message(a, f"🛠 Maxsus bot buyurtmasi: {escape(m.from_user.full_name)} (<code>{m.from_user.id}</code>)\n"
                                      f"Javob: /reply {m.from_user.id} MATN", parse_mode="HTML")
            await m.copy_to(a)
        except Exception: pass
    await m.answer(T(m.from_user.id, "special_sent"), reply_markup=menu(lang_of(m.from_user.id), m.from_user.id))


# ---------------- Admin panel ----------------
RENAME_KEYS = ["b_create", "b_my", "b_cab", "b_ref", "b_topup", "b_guide", "b_help", "b_special", "b_admin",
               "b_transfer", "b_income", "b_tariffs", "b_site", "b_promo"]
# (kalit, nomi, HTML teglar ishlaydimi)
TEXT_KEYS = [("welcome", "👋 Xush kelibsiz (/start)", False), ("pick_type", "➕ Bot yaratish", False),
             ("cab", "👤 Hisobim", True), ("ref", "🗣 Referral", True), ("topup_ask", "💳 Hisob to'ldirish", False),
             ("guide", "📘 Qo'llanma", True), ("help_ask", "✉️ Murojaat — so'rov", False), ("help_sent", "✉️ Murojaat — javob", False),
             ("special_ask", "🛠 Maxsus bot — so'rov", False), ("sub_need", "🔒 Majburiy obuna matni", False), ("promo_ask", "🎁 Promokod — so'rov", False), ("special_sent", "🛠 Maxsus bot — javob", False)]
SAMPLE = {"days": "7", "uid": "123456789", "bal": "10 000 so'm", "n": "2", "min": "1 000 so'm", "bonus": "1 000 so'm",
          "link": "https://t.me/bot?start=1", "cnt": "0"}


def ap_lang(uid):
    l = get_setting(f"aplang:{uid}", "uz")
    return l if l in LANGS else "uz"


def placeholders(key):
    return {f for _, f, _, _ in Formatter().parse(S["uz"][key]) if f}


def check_rename(key, text):
    if not text or len(text) > 40 or text.startswith("/"):
        return "❌ Nom 1–40 belgi bo'lsin va «/» bilan boshlanmasin. Qayta yuboring yoki /cancel."
    if text in (menu_buttons() - all_texts(key)) or text in custom_titles():
        return "❌ Bu nom boshqa tugma bilan bir xil. Boshqa nom yozing yoki /cancel."
    return None


def check_text(key, text):
    if not text or len(text) > 3500:
        return "❌ Matn 1–3500 belgi bo'lsin. Qayta yuboring yoki /cancel."
    allowed = placeholders(key)
    try:
        used = [f for _, f, _, _ in Formatter().parse(text) if f is not None]
        if any(f not in allowed for f in used):
            raise KeyError
        text.format(**{f: SAMPLE.get(f, "1") for f in allowed})
    except Exception:
        ph = ", ".join("{" + f + "}" for f in sorted(allowed)) or "yo'q"
        return (f"❌ Figurali qavs { '{ }' } noto'g'ri ishlatilgan. Ruxsat etilgan o'rinlar: {ph}. "
                "Boshqa { yoki } belgisi kerak bo'lsa, uni ikkilang: {{ }}. Qayta yuboring yoki /cancel.")
    return None


def preview_text(key, text):
    return text.format(**{f: SAMPLE.get(f, "1") for f in placeholders(key)})


def admin_head():
    return (f"👑 <b>Admin panel</b>\n\n👥 Foydalanuvchilar: {one('SELECT COUNT(*) c FROM users')['c']}\n"
            f"🤖 Botlar: {one('SELECT COUNT(*) c FROM bots')['c']} (ishlayotgan: {len(runner.procs)})")


def admin_kb(uid):
    kb = InlineKeyboardBuilder()
    kb.button(text="🧩 Menyularni boshqarish", callback_data="ap:menus")
    kb.button(text="✏️ Tugma nomlari", callback_data="ap:ren")
    kb.button(text="📝 Matnlarni tahrirlash", callback_data="ap:txt")
    kb.button(text="📢 Xabar yuborish", callback_data="aq:bc")
    kb.button(text="🔒 Majburiy obuna", callback_data="aq:sub")
    kb.button(text="🎁 Promokodlar", callback_data="aq:pr")
    kb.button(text="💰 Tariflar", callback_data="aq:tar")
    kb.button(text="🟢 Tarif tizimi: YOQIQ — o'chirish" if tariffs_on() else "🔴 Tarif tizimi: O'CHIQ — yoqish", callback_data="aq:tof:home")
    kb.button(text=f"🌐 Til: {LANGS[ap_lang(uid)]}", callback_data="ap:lang")
    kb.button(text="👤 Adminlar", callback_data="ap:adm")
    kb.button(text="📋 Buyruqlar", callback_data="ap:cmds")
    kb.adjust(1, 2, 2, 2, 1, 1, 1, 1)
    return kb.as_markup()


def admin_cmds():
    return (f"/addbalance ID SUMMA — balans qo'shish\n/reply ID MATN — murojaatga javob\n"
            f"/addbot ID TUR TOKEN — foydalanuvchi uchun bot yaratish\n/extend BOT_ID KUN — bot muddatini uzaytirish\n"
            f"/setplan BOT_ID TARIF — bot tarifini o'zgartirish ({', '.join(PLANS)})\n"
            f"/menus — konstruktorga o'zingiz menyu (tugma) qo'shish / o'chirish\n"
            f"/mintransfer SUMMA — foydalanuvchilar o'rtasida pul o'tkazish minimumi (hozir: {som(min_transfer())})\n"
            f"/setbalance ID SUMMA — foydalanuvchi balansini aniq summaga qo'yish\n"
            f"📎 Yangi bot turi: .py faylni shu yerga yuboring (izohga: Nomi | narxi). /types, /deltype KALIT")


def adm_view(uid):
    extra = [a for a in SUPER_ADMINS if a not in ENV_ADMINS]
    owner = uid in ENV_ADMINS
    txt = "👤 <b>Adminlar</b>\n\n" + "\n".join(f"🔒 <code>{a}</code> (asosiy)" for a in ENV_ADMINS)
    txt += "".join(f"\n👤 <code>{a}</code>" for a in extra)
    kb = InlineKeyboardBuilder()
    if owner:
        for a in extra:
            kb.button(text=f"❌ {a}", callback_data=f"ap:ad:{a}")
        kb.button(text="➕ Admin qo'shish", callback_data="ap:aa")
        if extra:
            txt += "\n\nO'chirish uchun ❌ tugmasini bosing."
    else:
        txt += "\n\nAdmin qo'shish va o'chirish faqat asosiy adminlarga ruxsat."
    kb.button(text="⬅️ Orqaga", callback_data="ap:home")
    kb.adjust(2 if owner and extra else 1)
    return txt, kb.as_markup()


async def ap_edit(c: CallbackQuery, text, kb, html=True):
    try:
        await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML" if html else None)
    except Exception as e:
        if "not modified" not in str(e):
            await c.message.answer(text, reply_markup=kb, parse_mode="HTML" if html else None)


@router.message(Command("admin"), ADMIN)
@router.message(btn("b_admin"), ADMIN)
async def admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(admin_head(), reply_markup=admin_kb(m.from_user.id), parse_mode="HTML")


@router.callback_query(F.data.startswith("ap:"), ADMIN)
async def ap_cb(c: CallbackQuery, state: FSMContext):
    uid = c.from_user.id
    p = c.data.split(":")
    act = p[1]
    lang = ap_lang(uid)
    back = lambda to: InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=to)]])
    if act == "home":
        await state.clear()
        await ap_edit(c, admin_head(), admin_kb(uid))
    elif act == "menus":
        txt, kb = menus_view()
        await c.message.answer(txt, reply_markup=kb, parse_mode="HTML")
    elif act == "cmds":
        await c.message.answer(admin_cmds())
    elif act == "lang":
        kb = InlineKeyboardBuilder()
        for k, name in LANGS.items():
            kb.button(text=("✅ " if k == lang else "") + name, callback_data=f"ap:l:{k}")
        kb.button(text="⬅️ Orqaga", callback_data="ap:home")
        kb.adjust(2, 2, 1)
        await ap_edit(c, "🌐 Qaysi til uchun tugma nomlari va matnlarni tahrirlaysiz?\n(Foydalanuvchi o'z tilidagi matnni ko'radi.)",
                      kb.as_markup(), html=False)
    elif act == "l" and len(p) > 2 and p[2] in LANGS:
        set_setting(f"aplang:{uid}", p[2])
        await ap_edit(c, admin_head(), admin_kb(uid))
    elif act == "ren":
        await state.clear()
        kb = InlineKeyboardBuilder()
        for k in RENAME_KEYS:
            kb.button(text=("✱ " if (lang, k) in i18n.OVR else "") + t(lang, k), callback_data=f"ap:r:{k}")
        kb.button(text="⬅️ Orqaga", callback_data="ap:home")
        kb.adjust(2)
        await ap_edit(c, f"✏️ <b>Tugma nomlari</b> — {escape(LANGS[lang])}\n\nO'zgartirmoqchi bo'lgan tugmani tanlang (✱ — o'zgartirilgan).",
                      kb.as_markup())
    elif act == "r" and len(p) > 2 and p[2] in RENAME_KEYS:
        k = p[2]
        await state.set_state(St.rename)
        await state.update_data(key=k, lang=lang)
        kb = InlineKeyboardBuilder()
        if (lang, k) in i18n.OVR:
            kb.button(text="♻️ Asl nomiga qaytarish", callback_data=f"ap:rr:{k}")
        kb.button(text="⬅️ Orqaga", callback_data="ap:ren")
        kb.adjust(1)
        await c.message.answer(f"Hozirgi nom: «{escape(t(lang, k))}» ({escape(LANGS[lang])})\n\n"
                               "Yangi nomni yuboring (1–40 belgi, emoji ham mumkin). Bekor qilish: /cancel",
                               reply_markup=kb.as_markup(), parse_mode="HTML")
    elif act == "rr" and len(p) > 2 and p[2] in RENAME_KEYS:
        q("DELETE FROM overrides WHERE lang=? AND key=?", lang, p[2])
        i18n.OVR.pop((lang, p[2]), None)
        await state.clear()
        await c.message.answer("♻️ Asl nomga qaytdi.", reply_markup=menu(lang_of(uid), uid))
    elif act == "txt":
        await state.clear()
        kb = InlineKeyboardBuilder()
        for k, label, _ in TEXT_KEYS:
            kb.button(text=("✱ " if (lang, k) in i18n.OVR else "") + label, callback_data=f"ap:x:{k}")
        kb.button(text="⬅️ Orqaga", callback_data="ap:home")
        kb.adjust(1)
        await ap_edit(c, f"📝 <b>Matnlarni tahrirlash</b> — {escape(LANGS[lang])}\n\nQaysi bo'lim matnini o'zgartirasiz? (✱ — o'zgartirilgan)",
                      kb.as_markup())
    elif act in ("x", "xr") and len(p) > 2 and any(k == p[2] for k, _, _ in TEXT_KEYS):
        k = p[2]
        label, html = next((lb, h) for kk, lb, h in TEXT_KEYS if kk == k)
        if act == "xr":
            q("DELETE FROM overrides WHERE lang=? AND key=?", lang, k)
            i18n.OVR.pop((lang, k), None)
            await state.clear()
            await c.message.answer("♻️ Asl matnga qaytdi.", reply_markup=menu(lang_of(uid), uid))
        else:
            cur = i18n.OVR.get((lang, k)) or S[lang][k]
            ph = ", ".join("{" + f + "}" for f in sorted(placeholders(k)))
            await state.set_state(St.edtext)
            await state.update_data(key=k, lang=lang)
            kb = InlineKeyboardBuilder()
            if (lang, k) in i18n.OVR:
                kb.button(text="♻️ Asl matnga qaytarish", callback_data=f"ap:xr:{k}")
            kb.button(text="⬅️ Orqaga", callback_data="ap:txt")
            kb.adjust(1)
            await c.message.answer(
                f"📝 <b>{escape(label)}</b> — {escape(LANGS[lang])}\n\nHozirgi matn:\n<pre>{escape(cur[:2500])}</pre>\n"
                + (f"\n⚠️ Shu joylarni o'chirmang (ular avtomatik to'ldiriladi): <code>{escape(ph)}</code>\n" if ph else "")
                + ("\nHTML teglar ishlaydi: &lt;b&gt;qalin&lt;/b&gt;, &lt;i&gt;qiya&lt;/i&gt;, &lt;code&gt;kod&lt;/code&gt;.\n" if html
                   else "\nBu matn oddiy matn sifatida yuboriladi.\n")
                + "\nYangi matnni yuboring. Bekor qilish: /cancel",
                reply_markup=kb.as_markup(), parse_mode="HTML")
    elif act == "adm":
        await state.clear()
        txt, kb = adm_view(uid)
        await ap_edit(c, txt, kb)
    elif act == "aa":
        if uid not in ENV_ADMINS:
            return await c.answer("Faqat asosiy adminlar qo'sha oladi.", show_alert=True)
        await state.set_state(St.addadmin)
        await c.message.answer("👤 Yangi adminning Telegram ID raqamini yuboring (u shu botda /start bosgan bo'lsa yaxshi).\n"
                               "Bekor qilish: /cancel")
    elif act == "ad" and len(p) > 2 and p[2].isdigit():
        if uid not in ENV_ADMINS:
            return await c.answer("Faqat asosiy adminlar o'chira oladi.", show_alert=True)
        a = int(p[2])
        if a in SUPER_ADMINS and a not in ENV_ADMINS:
            SUPER_ADMINS.remove(a)
            save_extra_admins()
        txt, kb = adm_view(uid)
        await ap_edit(c, txt, kb)
    await c.answer()


@router.message(St.rename, F.text, ADMIN)
async def ap_rename(m: Message, state: FSMContext):
    d = await state.get_data()
    key, lang = d.get("key"), d.get("lang")
    if key not in RENAME_KEYS or lang not in LANGS:
        await state.clear()
        return await m.answer("❌ Xatolik, qaytadan boshlang: /admin")
    text = m.text.strip()
    err = check_rename(key, text)
    if err:
        return await m.answer(err)
    q("INSERT INTO overrides(lang,key,value) VALUES(?,?,?) ON CONFLICT(lang,key) DO UPDATE SET value=excluded.value", lang, key, text)
    i18n.OVR[(lang, key)] = text
    await state.clear()
    await m.answer(f"✅ «{escape(text)}» saqlandi ({escape(LANGS[lang])}). Foydalanuvchilarda yangi nom /start yoki istalgan menyudan keyin chiqadi.",
                   reply_markup=menu(lang_of(m.from_user.id), m.from_user.id), parse_mode="HTML")


@router.message(St.edtext, F.text, ADMIN)
async def ap_edtext(m: Message, state: FSMContext):
    d = await state.get_data()
    key, lang = d.get("key"), d.get("lang")
    item = next(((k, lb, h) for k, lb, h in TEXT_KEYS if k == key), None)
    if not item or lang not in LANGS:
        await state.clear()
        return await m.answer("❌ Xatolik, qaytadan boshlang: /admin")
    text = m.text.strip()
    err = check_text(key, text)
    if err:
        return await m.answer(err)
    prev = preview_text(key, text)
    try:
        await m.answer(prev, parse_mode="HTML" if item[2] else None)       # foydalanuvchiga qanday ko'rinishi
    except Exception:
        return await m.answer("❌ HTML noto'g'ri (teg yopilmagan yoki taqiqlangan). Qayta yuboring yoki /cancel.")
    q("INSERT INTO overrides(lang,key,value) VALUES(?,?,?) ON CONFLICT(lang,key) DO UPDATE SET value=excluded.value", lang, key, text)
    i18n.OVR[(lang, key)] = text
    await state.clear()
    await m.answer(f"✅ Saqlandi ({LANGS[lang]}). Yuqorida — foydalanuvchiga ko'rinishi.", reply_markup=menu(lang_of(m.from_user.id), m.from_user.id))


@router.message(St.addadmin, F.text, ADMIN)
async def ap_addadmin(m: Message, state: FSMContext, bot: Bot):
    if m.from_user.id not in ENV_ADMINS:
        await state.clear()
        return await m.answer("❌ Faqat asosiy adminlar admin qo'sha oladi.")
    s_ = m.text.strip()
    if not s_.isdigit() or not (5 <= len(s_) <= 15):
        return await m.answer("❌ ID faqat raqamlardan iborat bo'lsin (masalan 123456789). Qayta yuboring yoki /cancel.")
    new = int(s_)
    await state.clear()
    if new in SUPER_ADMINS:
        return await m.answer("ℹ️ Bu foydalanuvchi allaqachon admin.")
    SUPER_ADMINS.append(new)
    save_extra_admins()
    await m.answer(f"✅ <code>{new}</code> admin qilindi.", parse_mode="HTML")
    try:
        await bot.send_message(new, "👑 Siz konstruktorda admin qilindingiz. Menyuni yangilash uchun /start bosing.")
    except Exception:
        await m.answer("ℹ️ Unga xabar yuborilmadi (u hali botda /start bosmagan bo'lishi mumkin), lekin admin huquqi berildi.")


# ================= Promokod (foydalanuvchi tomoni) =================
async def promo_try(m: Message, code: str):
    uid = m.from_user.id
    res, bonus = redeem_promo(code, uid)
    if res == "ok":
        await m.answer(T(uid, "promo_ok", bonus=som(bonus, uid), bal=som(bal(uid), uid)), reply_markup=menu(lang_of(uid), uid))
    else:
        await m.answer(T(uid, "promo_" + res), reply_markup=menu(lang_of(uid), uid))


@router.message(Command("promo"))
async def promo_cmd(m: Message, command: CommandObject, state: FSMContext):
    if command.args and command.args.strip():
        await state.clear()
        return await promo_try(m, command.args)
    await state.set_state(St.promo)
    await m.answer(T(m.from_user.id, "promo_ask"))


@router.message(St.promo, F.text)
async def promo_get(m: Message, state: FSMContext):
    await state.clear()
    await promo_try(m, m.text)


# ================= Majburiy obuna: tekshiruv =================
SUB_CACHE = {}          # uid -> a'zo ekani tasdiqlangan vaqt (60 soniya keshlanadi)


async def unsubscribed(bot: Bot, uid: int):
    chans = q("SELECT * FROM channels")
    if not chans or uid in SUPER_ADMINS or time.time() - SUB_CACHE.get(uid, 0) < 60:
        return []
    out = []
    for ch in chans:
        try:
            mem = await bot.get_chat_member(int(ch["chat"]), uid)
            if mem.status in ("left", "kicked"):
                out.append(ch)
        except Exception:
            pass                       # bot kanalda admin emas / kanal o'chgan — foydalanuvchini to'smaymiz
    if not out:
        SUB_CACHE[uid] = time.time()
    return out


def sub_kb(uid, chans):
    kb = InlineKeyboardBuilder()
    for ch in chans:
        kb.button(text=f"📢 {ch['title']}", url=ch["link"])
    kb.button(text=T(uid, "sub_check"), callback_data="sub:chk")
    kb.adjust(1)
    return kb.as_markup()


@router.message.outer_middleware()
async def sub_gate_msg(handler, event, data):
    u = event.from_user
    if (u and event.chat.type == "private" and not (event.text or "").startswith("/start")
            and not getattr(event, "successful_payment", None)):
        bad = await unsubscribed(data["bot"], u.id)
        if bad:
            await event.answer(T(u.id, "sub_need"), reply_markup=sub_kb(u.id, bad))
            return None
    return await handler(event, data)


@router.callback_query.outer_middleware()
async def sub_gate_cb(handler, event, data):
    d = event.data or ""
    if not d.startswith(("sub:", "lang:")) and event.message and event.message.chat.type == "private":
        bad = await unsubscribed(data["bot"], event.from_user.id)
        if bad:
            await event.message.answer(T(event.from_user.id, "sub_need"), reply_markup=sub_kb(event.from_user.id, bad))
            await event.answer()
            return None
    return await handler(event, data)


@router.callback_query(F.data == "sub:chk")
async def sub_chk(c: CallbackQuery, bot: Bot):
    uid = c.from_user.id
    SUB_CACHE.pop(uid, None)
    bad = await unsubscribed(bot, uid)
    if bad:
        await c.answer(T(uid, "sub_fail"), show_alert=True)
        try: await c.message.edit_reply_markup(reply_markup=sub_kb(uid, bad))
        except Exception: pass
        return
    try: await c.message.delete()
    except Exception: pass
    await c.message.answer(T(uid, "sub_ok"), reply_markup=menu(lang_of(uid), uid))
    await c.answer()


# ================= Admin: yangi bo'limlar (aq:) =================
BG = set()
AUD_LABELS = {"all": "👥 Hammaga", "own": "🤖 Bot egalariga", "pay": "💳 To'lov qilganlarga", "lang": "🌐 Tanlangan til foydalanuvchilariga"}
PLAN_FIELDS = {"price": "💰 Narx (so'm)", "daily": "📩 Kunlik limit", "rate": "⚡ Tezlik (daqiqasiga)", "monthly": "📆 Oylik limit"}
CODE_RE = re.compile(r"^[A-Z0-9]{4,20}$")


def audience(aud, lang):
    if aud == "own": rows = q("SELECT DISTINCT owner AS id FROM bots")
    elif aud == "pay": rows = q("SELECT DISTINCT uid AS id FROM payments WHERE status IN ('ok','auto')")
    elif aud == "lang": rows = q("SELECT id FROM users WHERE lang=?", lang)
    else: rows = q("SELECT id FROM users")
    return [r["id"] for r in rows]


async def bc_run(bot: Bot, admin_id: int, ids, src=None, text=None):
    """Hammaga xabar yuboradi. Botni bloklaganlarga ham HAR SAFAR urinadi:
    ular blokdan chiqargan bo'lsa xabar yetib boradi va 'blocked' belgisi olib tashlanadi."""
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
    ok = blocked = dead = other = revived = 0
    was_blocked = {r["id"] for r in q("SELECT id FROM users WHERE blocked=1")}
    for i in ids:
        for attempt in (1, 2, 3):
            try:
                if src: await bot.copy_message(i, src[0], src[1])
                else: await bot.send_message(i, text, parse_mode="HTML")
                ok += 1
                if i in was_blocked:
                    revived += 1
                    q("UPDATE users SET blocked=0 WHERE id=?", i)
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                continue
            except TelegramForbiddenError as e:
                if "deactivated" in str(e).lower():
                    dead += 1
                else:
                    blocked += 1
                q("UPDATE users SET blocked=1 WHERE id=?", i)
                break
            except Exception:
                other += 1
                break
        await asyncio.sleep(0.05)                       # Telegram limiti: soniyasiga ~20–30 xabar
    total_blocked = q("SELECT COUNT(*) AS n FROM users WHERE blocked=1")[0]["n"]
    try:
        await bot.send_message(
            admin_id,
            "📢 Yuborish tugadi.\n"
            f"✅ Yetkazildi: {ok}\n"
            f"♻️ Blokdan chiqqan va qayta yetkazilgan: {revived}\n"
            f"🚫 Botni bloklagan (yetkazib bo'lmaydi): {blocked}\n"
            f"👻 O'chirilgan akkaunt: {dead}\n"
            f"⚠️ Boshqa xato: {other}\n"
            f"📌 Hozir jami bloklaganlar: {total_blocked}")
    except Exception: pass


@router.my_chat_member()
async def on_my_chat_member(u: ChatMemberUpdated):
    """Foydalanuvchi botni bloklasa/blokdan chiqarsa — holatni yangilab boradi."""
    if u.chat.type != "private":
        return
    st = u.new_chat_member.status
    if st == "kicked":
        q("UPDATE users SET blocked=1 WHERE id=?", u.chat.id)
    elif st == "member":
        q("UPDATE users SET blocked=0 WHERE id=?", u.chat.id)


def sub_view():
    chans = q("SELECT * FROM channels ORDER BY id")
    kb = InlineKeyboardBuilder()
    for ch in chans:
        kb.button(text=f"🗑 {ch['title']}", callback_data=f"aq:sd:{ch['id']}")
    kb.button(text="➕ Kanal qo'shish", callback_data="aq:sa")
    kb.button(text="✉️ Obuna bo'lganda xabar: " + ("🔕 o'chiq" if get_setting("ch_welcome_off") == "1" else "🔔 yoqiq"), callback_data="aq:cw")
    kb.button(text="⬅️ Orqaga", callback_data="ap:home")
    kb.adjust(1)
    txt = ("🔒 <b>Majburiy obuna</b>\n\nFoydalanuvchi quyidagi kanallarga a'zo bo'lmaguncha botdan foydalana olmaydi. "
           "Bot har bir kanalda <b>admin</b> bo'lishi shart.\n\n")
    txt += "\n".join(f"📢 {escape(ch['title'])}" for ch in chans) if chans else "Hozircha kanal yo'q — obuna talab qilinmaydi."
    return txt, kb.as_markup()


def promo_card(r, hint=False):
    s = (f"🎁 <b>PROMOKOD</b>\n\n🏷 Promokod: <code>{r['code']}</code>\n💵 Bonus: {som(r['bonus'])}\n"
         f"👥 Limit: {r['used']}/{r['limit_n']}")
    if r["used"] >= r["limit_n"]:
        s += "\n\n⛔ <b>Bu promokod tugadi!</b>"
    elif hint:
        s += "\n\n👤 Faollashtirish: «Hisobim» → «🎁 Promokod» yoki <code>/promo " + r["code"] + "</code>"
    return s


def promo_list_view():
    rows = q("SELECT * FROM promos ORDER BY created DESC LIMIT 30")
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(text=f"{'⛔' if r['used'] >= r['limit_n'] else '🎁'} {r['code']} · {r['used']}/{r['limit_n']}", callback_data=f"aq:pv:{r['code']}")
    kb.button(text="➕ Yangi promokod", callback_data="aq:pn")
    kb.button(text="⬅️ Orqaga", callback_data="ap:home")
    kb.adjust(1)
    return "🎁 <b>Promokodlar</b>\n\n" + ("Kodni tanlang:" if rows else "Hozircha promokod yo'q."), kb.as_markup()


def promo_kb(code):
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Foydalanuvchilarga yuborish", callback_data=f"aq:pb:{code}")
    kb.button(text="🗑 O'chirish", callback_data=f"aq:pd:{code}")
    kb.button(text="⬅️ Orqaga", callback_data="aq:pr")
    kb.adjust(1)
    return kb.as_markup()


def tar_view():
    kb = InlineKeyboardBuilder()
    for k, pl in PLANS.items():
        kb.button(text=f"{pl['name']} · {som(pl['price'])}", callback_data=f"aq:tp:{k}")
    kb.button(text="🟢 Tarif tizimi: YOQIQ — o'chirish" if tariffs_on() else "🔴 Tarif tizimi: O'CHIQ — yoqish", callback_data="aq:tof:tar")
    kb.button(text="⬅️ Orqaga", callback_data="ap:home")
    kb.adjust(1)
    return ("💰 <b>Tariflarni tahrirlash</b>\n\nYangi narx va limitlar darhol ishlaydi; ishlab turgan botlarning limiti "
            "bot qayta ishga tushganda yangilanadi.\n\nHolat: " + ("🟢 tarif tizimi yoqiq" if tariffs_on() else "🔴 tarif tizimi o'chiq (hamma bot bepul va cheksiz)")), kb.as_markup()


def plan_view(key):
    p = PLANS[key]
    kb = InlineKeyboardBuilder()
    for f, lb in PLAN_FIELDS.items():
        kb.button(text=lb, callback_data=f"aq:tf:{key}:{f}")
    kb.button(text="🎨 Dizayn: " + ("✅ bor" if p["design"] else "❌ yo'q"), callback_data=f"aq:td:{key}")
    kb.button(text="⬅️ Orqaga", callback_data="aq:tar")
    kb.adjust(2, 2, 1, 1)
    dz = "bor" if p["design"] else "yo'q"
    txt = (f"💰 <b>{escape(p['name'])}</b>\n\n💰 Narx: {som(p['price'])} ({PLAN_DAYS} kun)\n📩 Kunlik limit: {p['daily']}\n"
           f"⚡ Tezlik: {p['rate']}/daqiqa\n📆 Oylik limit: {p['monthly']}\n🎨 Dizayn: {dz}")
    return txt, kb.as_markup()


def save_plan(key):
    p = PLANS[key]
    set_setting(f"plan:{key}", json.dumps({f: p[f] for f in ("price", "daily", "rate", "monthly", "design")}))


@router.callback_query(F.data.startswith("aq:"), ADMIN)
async def aq_cb(c: CallbackQuery, state: FSMContext, bot: Bot):
    uid = c.from_user.id
    p = c.data.split(":")
    act = p[1]
    lang = ap_lang(uid)
    n = len(p)
    if act == "bc" and n == 2:                                      # ---- xabar yuborish
        await state.clear()
        kb = InlineKeyboardBuilder()
        for k, lb in AUD_LABELS.items():
            kb.button(text=f"{lb} ({len(audience(k, lang))})", callback_data=f"aq:bc:{k}")
        kb.button(text="⬅️ Orqaga", callback_data="ap:home")
        kb.adjust(1)
        await ap_edit(c, f"📢 <b>Xabar yuborish</b>\n\nKimlarga yuboramiz? (til bo'yicha: {escape(LANGS[lang])})", kb.as_markup())
    elif act == "bc" and p[2] in AUD_LABELS:
        await state.set_state(St.bcast)
        await state.update_data(aud=p[2], src=None, text=None)
        await c.message.answer(f"{AUD_LABELS[p[2]]}\n\nYuboriladigan xabarni yuboring — matn, rasm, video yoki fayl "
                               "(formatlash saqlanadi). Bekor qilish: /cancel")
    elif act == "bcgo":
        d = await state.get_data()
        aud = d.get("aud")
        if aud not in AUD_LABELS or not (d.get("src") or d.get("text")):
            return await c.answer("Xabar topilmadi. Qaytadan boshlang.", show_alert=True)
        ids = audience(aud, lang)
        await state.clear()
        t_ = asyncio.create_task(bc_run(bot, uid, ids, d.get("src"), d.get("text")))
        BG.add(t_)
        t_.add_done_callback(BG.discard)
        await ap_edit(c, f"🚀 Yuborish boshlandi: {len(ids)} ta foydalanuvchi. Tugagach natijani yuboraman.", None, html=False)
    elif act == "sub":                                              # ---- majburiy obuna
        await state.clear()
        txt, kb = sub_view()
        await ap_edit(c, txt, kb)
    elif act == "sa":
        if len(q("SELECT id FROM channels")) >= 10:
            return await c.answer("Eng ko'pi bilan 10 ta kanal.", show_alert=True)
        await state.set_state(St.addch)
        await c.message.answer("📢 Kanalning @username'ini yoki ID raqamini (-100...) yuboring, yoki kanaldan biror postni shu yerga "
                               "forward qiling.\n\n⚠️ Bot shu kanalda <b>admin</b> bo'lishi shart.\nBekor qilish: /cancel", parse_mode="HTML")
    elif act == "cw":
        await state.set_state(St.chmsg)
        cur = get_setting("ch_welcome") or CH_WELCOME_DEFAULT
        await c.message.answer("✉️ <b>Kanalga obuna bo'lgan foydalanuvchiga avtomatik yuboriladigan xabar</b>\n\n"
                               f"Hozirgi matn:\n{cur}\n\n"
                               "Yangi matn yuboring (formatlash saqlanadi). <code>{name}</code> — foydalanuvchi ismi, "
                               "<code>{kanal}</code> — kanal nomi.\n"
                               "O'chirish: <code>0</code> • Qayta yoqish: <code>1</code> • Bekor qilish: /cancel", parse_mode="HTML")
    elif act == "sd" and n > 2 and p[2].isdigit():
        q("DELETE FROM channels WHERE id=?", int(p[2]))
        SUB_CACHE.clear()
        txt, kb = sub_view()
        await ap_edit(c, txt, kb)
    elif act == "pr":                                               # ---- promokodlar
        await state.clear()
        txt, kb = promo_list_view()
        await ap_edit(c, txt, kb)
    elif act == "pn":
        await state.set_state(St.pnew)
        await c.message.answer("🎁 Yangi promokod.\n\nFormat: <code>BONUS LIMIT [KOD]</code>\n"
                               "Masalan: <code>20 50</code> yoki <code>1000 100 YANGIYIL</code>\n"
                               "BONUS — so'm, LIMIT — nechta odam faollashtira oladi. KOD yozilmasa, avtomatik yaratiladi.\n"
                               "Bekor qilish: /cancel", parse_mode="HTML")
    elif act in ("pv", "pd", "pb") and n > 2:
        r = one("SELECT * FROM promos WHERE code=?", p[2])
        if not r:
            return await c.answer("Promokod topilmadi.", show_alert=True)
        if act == "pv":
            await ap_edit(c, promo_card(r), promo_kb(r["code"]))
        elif act == "pd":
            q("DELETE FROM promo_uses WHERE code=?", r["code"])
            q("DELETE FROM promos WHERE code=?", r["code"])
            txt, kb = promo_list_view()
            await ap_edit(c, txt, kb)
        else:
            await state.clear()
            await state.update_data(aud="all", src=None, text=promo_card(r, hint=True))
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yuborish", callback_data="aq:bcgo"),
                                                       InlineKeyboardButton(text="❌ Bekor", callback_data="ap:home")]])
            await c.message.answer(f"📢 Promokod <code>{r['code']}</code> hamma foydalanuvchiga ({len(audience('all', lang))} ta) yuboriladi. Tasdiqlaysizmi?",
                                   reply_markup=kb, parse_mode="HTML")
    elif act == "tar" and n == 2:                                   # ---- tariflar
        await state.clear()
        txt, kb = tar_view()
        await ap_edit(c, txt, kb)
    elif act == "tof":                                              # ---- tarif tizimini yoqish/o'chirish (bitta tugma)
        now = int(time.time())
        if tariffs_on():
            set_setting("tariffs_on", "0")
            set_setting("tariffs_off_since", now)
            note = "🔴 Tarif tizimi O'CHIRILDI: hamma botlar bepul, cheksiz va muddatsiz ishlaydi."
        else:
            off = int(get_setting("tariffs_off_since", now) or now)
            pause = max(0, now - off)
            if pause:                                               # o'chiq turgan vaqt hisobga olinmaydi: muddatlar shuncha uzayadi
                q("UPDATE bots SET expires=expires+?, warned=0 WHERE expires>?", pause, off)
            set_setting("tariffs_on", "1")
            note = f"🟢 Tarif tizimi YOQILDI. Bot muddatlari o'chiq turgan vaqtga ({pause // 3600} soat) uzaytirildi."
        for b in q("SELECT id FROM bots WHERE running=1"):         # yangi limitlar bilan qayta ishga tushirish
            if is_expired(one("SELECT * FROM bots WHERE id=?", b["id"])):
                continue
            asyncio.create_task(launch(b["id"]))
        if p[2:3] == ["tar"]:
            txt, kb = tar_view()
            await ap_edit(c, txt, kb)
        else:
            await ap_edit(c, admin_head(), admin_kb(c.from_user.id))
        await c.message.answer(note)
    elif act in ("tp", "td") and n > 2 and p[2] in PLANS:
        if act == "td":
            PLANS[p[2]]["design"] = not PLANS[p[2]]["design"]
            PLANS[p[2]]["lang"] = PLANS[p[2]]["design"]          # til tanlash dizayn bilan birga (Pro va yuqori)
            save_plan(p[2])
        await state.clear()
        txt, kb = plan_view(p[2])
        await ap_edit(c, txt, kb)
    elif act == "tf" and n > 3 and p[2] in PLANS and p[3] in PLAN_FIELDS:
        await state.set_state(St.tedit)
        await state.update_data(plan=p[2], field=p[3])
        await c.message.answer(f"{PLAN_FIELDS[p[3]]} — hozirgi: {PLANS[p[2]][p[3]]}\n\nYangi qiymatni raqam bilan yuboring. Bekor qilish: /cancel")
    await c.answer()


@router.message(St.bcast, ADMIN)
async def bc_msg(m: Message, state: FSMContext):
    aud = (await state.get_data()).get("aud")
    if aud not in AUD_LABELS:
        await state.clear()
        return await m.answer("❌ Xatolik, qaytadan boshlang: /admin")
    await state.update_data(src=[m.chat.id, m.message_id], text=None)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yuborish", callback_data="aq:bcgo"),
                                               InlineKeyboardButton(text="❌ Bekor", callback_data="ap:home")]])
    await m.answer(f"📢 {AUD_LABELS[aud]}: <b>{len(audience(aud, ap_lang(m.from_user.id)))}</b> ta foydalanuvchiga yuboriladi.\n\nTasdiqlaysizmi?",
                   reply_markup=kb, parse_mode="HTML")


@router.message(St.addch, ADMIN)
async def ch_add(m: Message, state: FSMContext, bot: Bot):
    fo = getattr(m, "forward_origin", None)
    co = getattr(fo, "chat", None) or getattr(m, "forward_from_chat", None)
    ref = None
    if co:
        ref = co.id
    elif m.text:
        s_ = m.text.strip()
        if re.fullmatch(r"-?\d{5,}", s_): ref = int(s_)
        elif re.fullmatch(r"@?[A-Za-z0-9_]{4,}", s_): ref = "@" + s_.lstrip("@")
    if ref is None:
        return await m.answer("❌ @username, ID (-100...) yuboring yoki kanaldan post forward qiling. Yoki /cancel.")
    try:
        chat = await bot.get_chat(ref)
        me = await bot.get_me()
        mem = await bot.get_chat_member(chat.id, me.id)
        is_admin = mem.status in ("administrator", "creator")
    except Exception:
        return await m.answer("❌ Kanal topilmadi yoki bot unga kira olmaydi. Botni kanalga admin qilib qo'shing va qayta yuboring.")
    if not is_admin:
        return await m.answer("❌ Bot bu kanalda admin emas. Avval botni kanalga admin qiling, keyin qayta yuboring.")
    link = f"https://t.me/{chat.username}" if getattr(chat, "username", None) else None
    if not link:
        try: link = await bot.export_chat_invite_link(chat.id)
        except Exception: link = getattr(chat, "invite_link", None)
    if not link:
        return await m.answer("❌ Kanal uchun havola olinmadi. Botga «havola yaratish» huquqini bering va qayta yuboring.")
    q("INSERT OR REPLACE INTO channels(chat,title,link) VALUES(?,?,?)", str(chat.id), chat.title or str(chat.id), link)
    SUB_CACHE.clear()
    await state.clear()
    await m.answer(f"✅ «{escape(chat.title or '')}» qo'shildi.", parse_mode="HTML")
    txt, kb = sub_view()
    await m.answer(txt, reply_markup=kb, parse_mode="HTML")


CH_WELCOME_DEFAULT = "🎉 Salom, {name}! «{kanal}» kanaliga obuna bo'lganingiz uchun rahmat!"


@router.message(St.chmsg, F.text, ADMIN)
async def ch_welcome_set(m: Message, state: FSMContext):
    t_ = (m.text or "").strip()
    if t_ == "0":
        set_setting("ch_welcome_off", "1")
        await m.answer("🔕 Obuna xabari o'chirildi.")
    elif t_ == "1":
        set_setting("ch_welcome_off", "0")
        await m.answer("🔔 Obuna xabari yoqildi.")
    else:
        set_setting("ch_welcome", m.html_text)
        set_setting("ch_welcome_off", "0")
        await m.answer("✅ Saqlandi va yoqildi.")
    await state.clear()
    txt, kb = sub_view()
    await m.answer(txt, reply_markup=kb, parse_mode="HTML")


async def ch_greet(bot: Bot, user, chat):
    """Kanalga obuna bo'lgan odamga shaxsiy xabar (har bir kanal uchun bir martadan)."""
    if user.is_bot or get_setting("ch_welcome_off") == "1":
        return
    if not q("SELECT 1 FROM channels WHERE chat=?", str(chat.id)):
        return
    if q("SELECT 1 FROM ch_greeted WHERE uid=? AND chat=?", user.id, str(chat.id)):
        return
    text = (get_setting("ch_welcome") or CH_WELCOME_DEFAULT).replace("{name}", escape(user.full_name or "")).replace("{kanal}", escape(chat.title or ""))
    try:
        await bot.send_message(user.id, text, parse_mode="HTML")
        q("INSERT OR IGNORE INTO ch_greeted(uid,chat,ts) VALUES(?,?,?)", user.id, str(chat.id), int(time.time()))
    except Exception:
        pass        # foydalanuvchi botni /start qilmagan yoki bloklagan — Telegram ruxsat bermaydi


@router.chat_member()
async def on_channel_member(u: ChatMemberUpdated, bot: Bot):
    if u.old_chat_member.status in ("left", "kicked") and u.new_chat_member.status in ("member", "administrator", "creator"):
        await ch_greet(bot, u.new_chat_member.user, u.chat)


@router.chat_join_request()
async def on_join_request(r: ChatJoinRequest, bot: Bot):
    # "So'rov bilan qo'shilish" yoqilgan kanallarda bot /start bosmagan odamga ham yoza oladi
    await ch_greet(bot, r.from_user, r.chat)


@router.message(St.pnew, F.text, ADMIN)
async def promo_new(m: Message, state: FSMContext):
    a = m.text.split()
    try:
        bonus, lim = int(a[0]), int(a[1])
        assert 1 <= bonus <= 10_000_000 and 1 <= lim <= 1_000_000 and len(a) <= 3
        code = a[2].upper() if len(a) == 3 else None
        assert code is None or CODE_RE.match(code)
    except Exception:
        return await m.answer("❌ Format: BONUS LIMIT [KOD]. Masalan: 20 50 yoki 1000 100 YANGIYIL (kod: 4–20 ta harf/raqam). Yoki /cancel.")
    if code is None:
        while True:
            code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(11))
            if not one("SELECT 1 FROM promos WHERE code=?", code): break
    elif one("SELECT 1 FROM promos WHERE code=?", code):
        return await m.answer("❌ Bu kod band. Boshqa kod yozing yoki kodsiz yuboring.")
    q("INSERT INTO promos(code,bonus,limit_n,used,created) VALUES(?,?,?,0,?)", code, bonus, lim, int(time.time()))
    await state.clear()
    r = one("SELECT * FROM promos WHERE code=?", code)
    await m.answer(promo_card(r, hint=True), reply_markup=promo_kb(code), parse_mode="HTML")


@router.message(St.tedit, F.text, ADMIN)
async def plan_edit(m: Message, state: FSMContext):
    d = await state.get_data()
    key, f = d.get("plan"), d.get("field")
    if key not in PLANS or f not in PLAN_FIELDS:
        await state.clear()
        return await m.answer("❌ Xatolik, qaytadan boshlang: /admin")
    s_ = m.text.replace(" ", "").replace(",", "")
    lo = 1000 if f == "price" else 1
    if not s_.isdigit() or not (lo <= int(s_) <= 1_000_000_000):
        return await m.answer(f"❌ {lo} dan 1 000 000 000 gacha raqam yuboring. Yoki /cancel.")
    PLANS[key][f] = int(s_)
    save_plan(key)
    await state.clear()
    txt, kb = plan_view(key)
    await m.answer("✅ Saqlandi.\n\n" + txt, reply_markup=kb, parse_mode="HTML")


@router.message(Command("mintransfer"), ADMIN)
async def mintransfer(m: Message, command: CommandObject):
    a = (command.args or "").replace(" ", "")
    if not a.isdigit() or int(a) < 1:
        return await m.answer(f"Hozirgi minimum: {som(min_transfer())}\nO'zgartirish: /mintransfer SUMMA (masalan /mintransfer 5000)")
    set_setting("min_transfer", int(a))
    await m.answer(f"✅ Minimal o'tkazma summasi: {som(int(a))}")


@router.message(Command("setbalance"), ADMIN)
async def setbalance(m: Message, command: CommandObject, bot: Bot):
    try:
        uid, amt = map(int, command.args.split())
        assert amt >= 0
    except Exception:
        return await m.answer("Format: /setbalance ID SUMMA")
    if not one("SELECT 1 FROM users WHERE id=?", uid):
        return await m.answer("❌ Foydalanuvchi konstruktorda /start bosmagan.")
    q("UPDATE users SET balance=? WHERE id=?", amt, uid)
    await m.answer(f"✅ {uid} balansi: {som(amt)}")


# ---------- Yangi bot turini .py fayl yuborib qo'shish (admin) ----------
CUSTOM_KEY = re.compile(r"[^a-z0-9_]+")


def custom_dir():
    d = runner.DATA_DIR.parent / "custom_bots"            # doimiy diskda (DATA_DIR) saqlanadi
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_type(key, title, file, price):
    BOT_TYPES[key] = (title, str(file), price)
    register_type_title(key, title)


def load_custom_types():
    """Konstruktor ishga tushganda yuklangan bot turlarini tiklaydi."""
    for r in q("SELECT * FROM bot_types"):
        if Path(r["file"]).exists():
            register_type(r["key"], r["title"], r["file"], r["price"])


@router.message(F.document.file_name.endswith(".py"), ADMIN)
async def add_type(m: Message, bot: Bot):
    doc = m.document
    if doc.file_size and doc.file_size > 1_000_000:
        return await m.answer("❌ Fayl 1 MB dan katta.")
    key = CUSTOM_KEY.sub("_", Path(doc.file_name).stem.lower()).strip("_")[:20]
    old = one("SELECT * FROM bot_types WHERE key=?", key) if key else None
    if not key or (key in BOT_TYPES and not old):
        return await m.answer("❌ Bu fayl nomi band yoki yaroqsiz. Faylni boshqa nom bilan (a-z, 0-9, _) yuboring.")
    src = (await bot.download(doc)).getvalue()
    try:
        compile(src, doc.file_name, "exec")
    except SyntaxError as e:
        return await m.answer(f"❌ Fayl ishlamaydi — sintaksis xatosi: {e.lineno}-qator, {e.msg}")
    parts = [p.strip() for p in (m.caption or "").split("|")]
    title = (parts[0] or (old["title"] if old else "🤖 " + key.replace("_", " ").title()))[:40]
    price_s = parts[1].replace(" ", "") if len(parts) > 1 else ""
    price = int(price_s) if price_s.isdigit() else (old["price"] if old else 0)
    path = custom_dir() / f"{key}.py"
    path.write_bytes(src)
    q("INSERT INTO bot_types(key,title,file,price) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET title=excluded.title, file=excluded.file, price=excluded.price",
      key, title, str(path), price)
    register_type(key, title, path, price)
    restarted = 0
    if old:                                               # fayl yangilandi — shu turdagi ishlayotgan botlar yangi kod bilan qayta ishga tushadi
        for b in q("SELECT id FROM bots WHERE type=? AND running=1", key):
            if await launch(b["id"]): restarted += 1
    note = "" if b"BOT_TOKEN" in src else "\n⚠️ Faylda BOT_TOKEN topilmadi — bot tokenni muhitdan (os.getenv(\"BOT_TOKEN\")) o'qishi kerak."
    await m.answer(f"✅ {'Yangilandi' if old else "Yangi bot turi qo'shildi"}: {title}\nKalit: {key} · narxi: {som(price)}"
                   + (f"\n🔄 Qayta ishga tushirildi: {restarted} ta bot" if old else "\nEndi «➕ Bot yaratish» da ko'rinadi.") + note)


@router.message(Command("types"), ADMIN)
async def types_cmd(m: Message):
    rows = q("SELECT * FROM bot_types ORDER BY key")
    if not rows:
        return await m.answer("Yuklangan bot turlari yo'q. .py fayl yuboring (izohga: Nomi | narxi).")
    await m.answer("📦 Yuklangan bot turlari:\n\n" + "\n".join(
        f"• {r['title']} — {r['key']} · {som(r['price'])} · botlar: {one('SELECT COUNT(*) c FROM bots WHERE type=?', r['key'])['c']}" for r in rows)
        + "\n\nO'chirish: /deltype KALIT")


@router.message(Command("deltype"), ADMIN)
async def deltype(m: Message, command: CommandObject):
    key = (command.args or "").strip()
    r = one("SELECT * FROM bot_types WHERE key=?", key)
    if not r:
        return await m.answer("Format: /deltype KALIT (kalitlar: /types). Faqat yuklangan turlarni o'chirish mumkin.")
    n = one("SELECT COUNT(*) c FROM bots WHERE type=?", key)["c"]
    if n:
        return await m.answer(f"❌ Bu turda {n} ta bot bor. Avval ularni o'chiring.")
    q("DELETE FROM bot_types WHERE key=?", key)
    BOT_TYPES.pop(key, None)
    try: Path(r["file"]).unlink()
    except Exception: pass
    await m.answer(f"🗑 «{r['title']}» turi o'chirildi.")


# ---------- Menyularni o'zingiz qo'shish (admin) ----------
URL_ONLY = re.compile(r"^https?://\S+$")


def menus_view():
    """Admin uchun menyular ro'yxati: har bir menyu yonida o'chirish tugmasi."""
    kb = InlineKeyboardBuilder()
    rows = custom_menus()
    for r in rows:
        icon = "🔗" if r["kind"] == "url" else ("📝" if r["kind"] == "text" else "📎")
        kb.button(text=f"🗑 {icon} {r['title']}", callback_data=f"mn:del:{r['id']}")
    kb.button(text="➕ Menyu qo'shish", callback_data="mn:add")
    kb.adjust(1)
    txt = ("🧩 <b>Menyularni boshqarish</b>\n\nQo'shgan menyularingiz foydalanuvchilarning pastki tugmalarida "
           "ko'rinadi.\n" + ("O'chirish uchun menyu tugmasini bosing." if rows else "Hozircha menyu yo'q."))
    return txt, kb.as_markup()


@router.message(Command("menus"), ADMIN)
async def menus_cmd(m: Message, state: FSMContext):
    await state.clear()
    txt, kb = menus_view()
    await m.answer(txt, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == B_MENUS, ADMIN)
async def menus_btn(m: Message, state: FSMContext):
    await menus_cmd(m, state)


@router.callback_query(F.data == "mn:add", ADMIN)
async def menu_add(c: CallbackQuery, state: FSMContext):
    if len(custom_menus()) >= MAX_CUSTOM_MENUS:
        return await c.answer(f"Eng ko'pi bilan {MAX_CUSTOM_MENUS} ta menyu qo'shish mumkin.", show_alert=True)
    await state.set_state(St.mtitle)
    await c.message.answer("✏️ Yangi menyu (tugma) nomini yuboring. Masalan: «🎁 Aksiyalar»\nBekor qilish: /cancel")
    await c.answer()


@router.message(St.mtitle, F.text, ADMIN)
async def menu_title(m: Message, state: FSMContext):
    title = m.text.strip()
    if not title or len(title) > 40:
        return await m.answer("❌ Nom 1–40 belgi bo'lsin. Qayta yuboring yoki /cancel.")
    if title in menu_buttons() or title in custom_titles():
        return await m.answer("❌ Bu nom band (yoki mavjud tugma bilan bir xil). Boshqa nom yozing.")
    await state.update_data(title=title)
    await state.set_state(St.mcontent)
    await m.answer("📩 Endi shu menyu bosilganda foydalanuvchiga chiqadigan xabarni yuboring.\n\n"
                   "• Matn, rasm, video, fayl — istalgani (qalin/qiya formatlash saqlanadi)\n"
                   "• Faqat havola (https://...) yuborsangiz — «ochish» tugmasi bo'lib chiqadi\n\nBekor qilish: /cancel")


@router.message(St.mtitle)
async def menu_title_bad(m: Message):
    await m.answer("❌ Menyu nomini matn ko'rinishida yuboring yoki /cancel.")


@router.message(St.mcontent, ADMIN)
async def menu_content(m: Message, state: FSMContext):
    title = (await state.get_data()).get("title")
    if not title:
        await state.clear()
        return await m.answer("❌ Xatolik, qaytadan boshlang: /menus")
    if m.text and URL_ONLY.match(m.text.strip()):
        kind, body, chat, msg = "url", m.text.strip(), None, None
    elif m.text:
        kind, body, chat, msg = "text", m.html_text, None, None
    else:                                                # rasm/video/fayl — admin xabaridan nusxa olinadi
        kind, body, chat, msg = "copy", None, m.chat.id, m.message_id
    nxt = (one("SELECT COALESCE(MAX(pos),0)+1 n FROM menus") or {"n": 1})["n"]
    try:
        ins("INSERT INTO menus(title,kind,body,src_chat,src_msg,pos) VALUES(?,?,?,?,?,?)", title, kind, body, chat, msg, nxt)
    except sqlite3.IntegrityError:
        await state.clear()
        return await m.answer("❌ Bunday nomli menyu allaqachon bor.")
    await state.clear()
    await m.answer(f"✅ «{title}» menyusi qo'shildi. Foydalanuvchilarga /start yoki istalgan menyu bosilgach ko'rinadi.",
                   reply_markup=menu(lang_of(m.from_user.id), m.from_user.id))
    txt, kb = menus_view()
    await m.answer(txt, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.regexp(r"^mn:del:\d+$"), ADMIN)
async def menu_del(c: CallbackQuery):
    q("DELETE FROM menus WHERE id=?", int(c.data.split(":")[2]))
    txt, kb = menus_view()
    try: await c.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")
    except Exception: pass
    await c.answer("O'chirildi")
    await c.message.answer("🗑 Menyu o'chirildi.", reply_markup=menu(lang_of(c.from_user.id), c.from_user.id))


def _is_custom(text):
    return bool(text) and text in custom_titles()


@router.message(F.text.func(_is_custom))
async def custom_open(m: Message, bot: Bot):
    """Admin qo'shgan menyu bosilganda uning mazmunini chiqaradi."""
    r = one("SELECT * FROM menus WHERE title=?", m.text)
    if not r: return
    try:
        if r["kind"] == "url":
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Ochish", url=r["body"])]])
            await m.answer(r["title"], reply_markup=kb)
        elif r["kind"] == "text":
            await m.answer(r["body"], parse_mode="HTML")
        else:
            await bot.copy_message(m.chat.id, r["src_chat"], r["src_msg"])
    except Exception:
        logging.exception("custom menu")
        await m.answer("⚠️")


@router.message(Command("addbalance"), ADMIN)
async def addbal(m: Message, command: CommandObject, bot: Bot):
    try:
        uid, amt = map(int, command.args.split())
    except Exception:
        return await m.answer("Format: /addbalance ID SUMMA")
    q("UPDATE users SET balance=balance+? WHERE id=?", amt, uid)
    await m.answer("✅ Bajarildi.")
    try: await bot.send_message(uid, f"💰 {som(amt, uid)} ➕")
    except Exception: pass


@router.message(Command("reply"), ADMIN)
async def reply(m: Message, command: CommandObject, bot: Bot):
    try:
        uid, text = command.args.split(maxsplit=1)
        await bot.send_message(int(uid), f"✉️ Admin:\n\n{text}")
        await m.answer("✅ Yuborildi.")
    except Exception:
        await m.answer("Format: /reply ID MATN")


@router.message(Command("addbot"), ADMIN)
async def addbot(m: Message, command: CommandObject, bot: Bot):
    try:
        uid, k, token = command.args.split()
        uid = int(uid)
    except Exception:
        return await m.answer("Format: /addbot FOYDALANUVCHI_ID TUR TOKEN\nTurlar: " + ", ".join(BOT_TYPES))
    try: await m.delete()                                # token chatda qolmasin
    except Exception: pass
    if k not in BOT_TYPES:
        return await m.answer("❌ Noto'g'ri tur. Turlar: " + ", ".join(BOT_TYPES))
    if not one("SELECT 1 FROM users WHERE id=?", uid):
        return await m.answer("❌ Foydalanuvchi konstruktorda /start bosmagan.")
    if one("SELECT 1 FROM bots WHERE token=?", token):
        return await m.answer("❌ Bu token allaqachon ulangan.")
    me = await check_token(token)
    if not me:
        return await m.answer("❌ Token noto'g'ri.")
    now = int(time.time())
    bid = ins("INSERT INTO bots(owner,type,token,username,created,expires,plan) VALUES(?,?,?,?,?,?,?)",
              uid, k, token, me.username, now, now + TRIAL_DAYS * 86400, TRIAL_PLAN)
    if not await launch(bid):
        q("DELETE FROM bots WHERE id=?", bid)
        runner.purge(bid)
        return await m.answer("❌ Bot ishga tushmadi, tokenni tekshiring.")
    await m.answer(f"✅ @{me.username} ({BOT_TYPES[k][0]}) {uid} uchun yaratildi.")
    try: await bot.send_message(uid, T(uid, "gift", username=me.username, type=T(uid, "type_" + k), days=TRIAL_DAYS))
    except Exception: pass


@router.message(Command("extend"), ADMIN)
async def extend(m: Message, command: CommandObject):
    try:
        bid, days = map(int, command.args.split())
        b = one("SELECT * FROM bots WHERE id=?", bid)
        assert b
    except Exception:
        return await m.answer("Format: /extend BOT_ID KUN")
    q("UPDATE bots SET expires=?, warned=0 WHERE id=?", max(b["expires"] or 0, int(time.time())) + days * 86400, bid)
    if not b["running"] and await launch(bid):
        q("UPDATE bots SET running=1 WHERE id=?", bid)
    await m.answer(f"✅ @{b['username']} muddati {days} kunga uzaytirildi.")


@router.message(Command("setplan"), ADMIN)
async def setplan(m: Message, command: CommandObject):
    try:
        bid, key = command.args.split()
        b = one("SELECT * FROM bots WHERE id=?", int(bid))
        assert b and key in PLANS
    except Exception:
        return await m.answer("Format: /setplan BOT_ID TARIF\nTariflar: " + ", ".join(PLANS))
    q("UPDATE bots SET plan=? WHERE id=?", key, b["id"])
    if b["running"]: await launch(b["id"])
    await m.answer(f"✅ @{b['username']} tarifi: {PLANS[key]['name']}")


# ---------------- Ishga tushirish ----------------
async def notify(bot: Bot, uid, text):
    try: await bot.send_message(uid, text)
    except Exception: pass


async def check_expiry(bot: Bot):
    """Muddati tugagan botlarni to'xtatadi; tugashiga WARN_HOURS soat qolganlarga ogohlantirish yuboradi."""
    if not tariffs_on():
        return                                           # tarif o'chiq — muddat va ogohlantirish yo'q
    now = int(time.time())
    for b in q("SELECT * FROM bots WHERE expires>0 AND expires<=?", now):
        if b["running"] or runner.alive(b["id"]):
            await runner.stop(b["id"])
            q("UPDATE bots SET running=0 WHERE id=?", b["id"])
            await notify(bot, b["owner"], T(b["owner"], "exp_end", username=b["username"]))
    cheapest = min(p["price"] for p in PLANS.values())
    for b in q("SELECT * FROM bots WHERE warned=0 AND expires>? AND expires<=?", now, now + WARN_HOURS * 3600):
        q("UPDATE bots SET warned=1 WHERE id=?", b["id"])
        o = b["owner"]
        await notify(bot, o, T(o, "exp_warn", username=b["username"], left=left(b["expires"], o), price=som(cheapest, o), days=PLAN_DAYS, bal=som(bal(o), o)))


FAILS: dict[int, int] = {}


async def watchdog(bot: Bot):
    while True:
        await asyncio.sleep(60)
        try:
            await check_expiry(bot)
            for b in q("SELECT * FROM bots WHERE running=1"):   # yiqilgan botlarni qayta tiklaydi
                if runner.alive(b["id"]): FAILS.pop(b["id"], None); continue
                if await launch(b["id"]):
                    FAILS.pop(b["id"], None)
                    continue
                FAILS[b["id"]] = FAILS.get(b["id"], 0) + 1
                if FAILS[b["id"]] >= 5:                        # token bekor qilingan va h.k. — abadiy urinmaymiz
                    q("UPDATE bots SET running=0 WHERE id=?", b["id"])
                    FAILS.pop(b["id"], None)
                    await notify(bot, b["owner"], T(b["owner"], "bot_dead", username=b["username"]))
        except Exception:
            logging.exception("watchdog")


async def health_server():
    """Render «Web Service» uchun: $PORT da oddiy HTTP javob (aks holda deploy «no open ports» xatosi bilan yiqiladi)."""
    port = os.getenv("PORT")
    if not (port and port.isdigit()):
        return
    from aiohttp import web
    app = web.Application()
    async def ok(_): return web.Response(text="ok")
    app.add_routes([web.get("/", ok), web.get("/health", ok)])
    r = web.AppRunner(app)
    await r.setup()
    await web.TCPSite(r, "0.0.0.0", int(port)).start()


async def main():
    if not BUILDER_TOKEN:
        raise SystemExit("BUILDER_TOKEN o'rnatilmagan")
    await health_server()
    bot = Bot(BUILDER_TOKEN)
    load_custom_types()                                      # yuklangan bot turlarini tiklash
    now = int(time.time())
    q("UPDATE bots SET expires=? WHERE expires=0 OR expires IS NULL", now + TRIAL_DAYS * 86400)   # eski botlarga sinov muddati
    q("UPDATE bots SET plan=? WHERE plan IS NULL", TRIAL_PLAN)
    for sid, amount in SEED_BALANCES.items():              # bir martalik boshlang'ich balans
        if str(get_setting(f"seed_{sid}")) != str(amount):
            q("INSERT OR IGNORE INTO users(id,name,joined) VALUES(?,?,?)", sid, str(sid), now)
            q("UPDATE users SET balance=? WHERE id=?", amount, sid)
            set_setting(f"seed_{sid}", amount)
    await check_expiry(bot)
    for b in q("SELECT * FROM bots WHERE running=1"):   # qayta ishga tushganda hamma botni tiklaydi
        await launch(b["id"])
    dp = Dispatcher()
    dp.include_router(router)
    task = asyncio.create_task(watchdog(bot))
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await runner.stop_all()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
