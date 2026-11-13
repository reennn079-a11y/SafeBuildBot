import asyncio
import json
import logging
import os
import time

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramRetryAfter,
)
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ==================================
# SOZLAMALAR
# ==================================

TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKENINGIZNI_BU_YERGA_YOZING")

MAIN_ADMINS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ADMIN_ID = MAIN_ADMINS[0] if MAIN_ADMINS else 0
SECOND_ADMIN_ID = ADMIN_ID

CONTACT = "@shahe20_12"

BASE_DIR = os.getcwd()
DATA_FILE = os.path.join(BASE_DIR, "menu_data.json")

# tur -> aiogram Bot metodi nomi (argument nomi tur nomi bilan bir xil)
MEDIA_METHODS = {
    "photo": "send_photo",
    "video": "send_video",
    "document": "send_document",
    "audio": "send_audio",
    "voice": "send_voice",
    "animation": "send_animation",
    "sticker": "send_sticker",
}

ICONS = {
    "text": "📝", "photo": "🖼", "video": "🎬", "document": "📄",
    "audio": "🎵", "voice": "🎤", "animation": "🎞", "sticker": "😀",
}

# Pastki (reply) tugmalar
BTN_HOME = "🏠 Bosh menyu"
BTN_CONTACT = "📞 Admin bilan bog‘lanish"
BTN_ADMIN = "⚙️ Admin panel"
BTN_BACK = "⬅️ Orqaga"
BTN_CANCEL = "❌ Bekor qilish"
BTN_DONE = "✅ Tayyor"
BTN_MENUS = "📁 Menyularni boshqarish"
BTN_CHANNELS = "📢 Kanallar"
BTN_ADMINS = "👮 Adminlar"
BTN_BROADCAST = "📨 Xabar yuborish"
BTN_STATS = "📊 Statistika"

RESERVED = {
    BTN_HOME, BTN_CONTACT, BTN_ADMIN, BTN_BACK, BTN_CANCEL, BTN_DONE,
    BTN_MENUS, BTN_CHANNELS, BTN_ADMINS, BTN_BROADCAST, BTN_STATS,
}

if TOKEN.startswith("BOT_TOKENINGIZNI"):
    print("XATO: TOKEN kiritilmagan! BOT_TOKEN muhit o‘zgaruvchisini yoki "
          "kod boshidagi TOKEN ni to‘ldiring.")
    raise SystemExit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()
import plan_limits; plan_limits.install(dp)   # tarif limitlari
router = Router()
dp.include_router(router)

# ==================================
# MA'LUMOTLARNI SAQLASH
# ==================================


def load():
    d = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print("JSON xatosi:", e)
            d = {}

    d.setdefault("admins", [])
    d.setdefault("channels", [])
    d.setdefault("users", [])
    d.setdefault("menus", {})

    for admin in MAIN_ADMINS:
        if admin not in d["admins"]:
            d["admins"].append(admin)
    return d


data = load()
states = {}   # admin kiritish holatlari
where = {}    # foydalanuvchi hozir turgan menyu (None = bosh menyu)


def save():
    try:
        temp = DATA_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, DATA_FILE)
        return True
    except Exception as e:
        print("SAQLASH XATOSI:", e)
        return False


save()

# ==================================
# TELEGRAM YORDAMCHILARI (aiogram)
# ==================================


async def send(chat_id, text, keyboard=None):
    try:
        return await bot.send_message(chat_id, str(text), reply_markup=keyboard)
    except TelegramAPIError as e:
        print("sendMessage xatosi:", e)
        return None


async def edit(chat_id, msg_id, text, keyboard):
    """Xabarni tahrirlaydi; bo'lmasa yangisini yuboradi."""
    if msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=msg_id, reply_markup=keyboard
            )
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
        except TelegramAPIError as e:
            print("editMessageText xatosi:", e)
    await send(chat_id, text, keyboard)


async def answer(cid, text=""):
    try:
        await bot.answer_callback_query(cid, text=text or None)
    except TelegramAPIError as e:
        print("answerCallbackQuery xatosi:", e)


def new_id():
    return str(time.time_ns())


def chunk(items, n=2):
    return [items[i:i + n] for i in range(0, len(items), n)]


def reply_kb(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


def ikb(rows):
    """rows: [[{"text":..., "callback_data":...} | {"text":..., "url":...}]]"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(**b) for b in row] for row in rows]
    )


# ==================================
# ADMIN / FOYDALANUVCHI
# ==================================


def is_admin(uid):
    return uid in data["admins"]


def add_user(uid):
    if uid not in data["users"]:
        data["users"].append(uid)
        save()


# ==================================
# MAJBURIY OBUNA
# ==================================


def normalize_channel(text):
    t = text.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    t = t.strip("/")
    if t.lstrip("-").isdigit():
        return t
    if not t.startswith("@"):
        t = "@" + t
    return t


async def check_sub(uid):
    if is_admin(uid):
        return True

    for channel in data["channels"]:
        try:
            member = await bot.get_chat_member(channel, uid)
        except TelegramAPIError as e:
            print("Obuna tekshirib bo'lmadi:", channel, e)
            continue

        status = member.status

        if status in ("member", "administrator", "creator"):
            continue
        if status == "restricted" and getattr(member, "is_member", False):
            continue
        return False
    return True


async def channel_link(channel):
    if channel.startswith("@"):
        return "https://t.me/" + channel[1:]
    try:
        chat = await bot.get_chat(channel)
    except TelegramAPIError:
        return None
    if chat.username:
        return "https://t.me/" + chat.username
    if chat.invite_link:
        return chat.invite_link
    return None


async def subscription_keyboard():
    rows = []
    for channel in data["channels"]:
        link = await channel_link(channel)
        if link:
            rows.append([{"text": "📢 " + channel, "url": link}])
    rows.append([{"text": "✅ Obunani tekshirish", "callback_data": "check_sub"}])
    return ikb(rows)


async def ask_subscribe(chat_id):
    await send(chat_id, "Botdan foydalanish uchun kanallarga obuna bo‘ling:",
               await subscription_keyboard())


# ==================================
# MENYU YORDAMCHILARI
# ==================================


def children(parent):
    return [(mid, m) for mid, m in data["menus"].items()
            if m.get("parent") == parent]


def menu_path(mid):
    names = []
    seen = set()
    while mid is not None and mid in data["menus"] and mid not in seen:
        seen.add(mid)
        names.append(data["menus"][mid]["name"])
        mid = data["menus"][mid].get("parent")
    return " > ".join(reversed(names))


def name_error(name, parent, exclude=None):
    """Nom yaroqsiz bo'lsa xato matnini qaytaradi."""
    if not name:
        return "Menyu nomini matn ko‘rinishida yuboring."
    if len(name) > 50:
        return "Nom juda uzun (50 belgigacha bo‘lsin)."
    if name in RESERVED:
        return "Bu nom tizim tugmasi uchun band. Boshqa nom yozing."
    for mid, m in children(parent):
        if mid != exclude and m["name"] == name:
            return "Bu darajada bunday nomli menyu allaqachon bor."
    return None


def remove_menu(menu_id):
    for child, _ in children(menu_id):
        remove_menu(child)
    data["menus"].pop(menu_id, None)


def move_menu(menu_id, direction):
    menus = data["menus"]
    parent = menus[menu_id].get("parent")
    ids = list(menus.keys())
    sibs = [i for i in ids if menus[i].get("parent") == parent]
    idx = sibs.index(menu_id)
    other = idx + direction
    if other < 0 or other >= len(sibs):
        return False
    a, b = ids.index(sibs[idx]), ids.index(sibs[other])
    ids[a], ids[b] = ids[b], ids[a]
    data["menus"] = {i: menus[i] for i in ids}
    return True


def item_label(item, number):
    icon = ICONS.get(item.get("type"), "📎")
    if item.get("type") == "text":
        preview = (item.get("value") or "").replace("\n", " ")[:25]
        return f"{number}. {icon} {preview}"
    cap = (item.get("caption") or "").replace("\n", " ")[:20]
    return f"{number}. {icon} {item.get('type')}" + (f" — {cap}" if cap else "")


async def send_items(chat_id, menu):
    for item in menu.get("items", []):
        kind = item.get("type")
        value = item.get("value")

        if kind == "text":
            await send(chat_id, value)
        elif kind in MEDIA_METHODS:
            kwargs = {kind: value}
            if item.get("caption") and kind != "sticker":
                kwargs["caption"] = item["caption"]
            try:
                await getattr(bot, MEDIA_METHODS[kind])(chat_id, **kwargs)
            except TelegramAPIError as e:
                print("Media yuborish xatosi:", e)


# ==================================
# FOYDALANUVCHI MENYUSI (PASTKI TUGMALAR)
# ==================================


def user_keyboard(uid, mid):
    names = [m["name"] for _, m in children(mid)]
    rows = chunk(names, 2)
    if mid is None:
        rows.append([BTN_CONTACT])
        if is_admin(uid):
            rows.append([BTN_ADMIN])
    else:
        rows.append([BTN_BACK, BTN_HOME])
    return reply_kb(rows)


async def show_home(chat_id, uid):
    states.pop(uid, None)
    if not await check_sub(uid):
        await ask_subscribe(chat_id)
        return

    where[uid] = None
    text = "👋 Xush kelibsiz!\n\nKerakli bo‘limni tanlang:"
    if not children(None):
        text = "👋 Xush kelibsiz!\n\nHozircha bo‘limlar yo‘q."
        if is_admin(uid):
            text += "\n⚙️ Admin panel orqali menyu qo‘shing."
    await send(chat_id, text, user_keyboard(uid, None))


async def open_menu(chat_id, uid, mid):
    menu = data["menus"].get(mid)
    if not menu:
        await show_home(chat_id, uid)
        return

    where[uid] = mid
    await send(chat_id, "📂 " + menu["name"], user_keyboard(uid, mid))

    if not menu.get("items") and not children(mid):
        await send(chat_id, "Bu bo‘lim hozircha bo‘sh.")
        return
    await send_items(chat_id, menu)


async def go_back(chat_id, uid):
    cur = where.get(uid)
    if cur is None or cur not in data["menus"]:
        await show_home(chat_id, uid)
        return
    parent = data["menus"][cur].get("parent")
    if parent is None:
        await show_home(chat_id, uid)
    else:
        await open_menu(chat_id, uid, parent)


async def navigate(chat_id, uid, text):
    """Matn biror menyu nomiga mos kelsa, ochadi."""
    cur = where.get(uid)
    if cur is not None and cur not in data["menus"]:
        cur = None

    for mid, m in children(cur):
        if m["name"] == text:
            await open_menu(chat_id, uid, mid)
            return True

    if cur is not None:
        for mid, m in children(None):
            if m["name"] == text:
                await open_menu(chat_id, uid, mid)
                return True
    return False


# ==================================
# ADMIN PANEL (PASTKI TUGMALAR)
# ==================================


def admin_keyboard():
    return reply_kb([
        [BTN_MENUS],
        [BTN_CHANNELS, BTN_ADMINS],
        [BTN_BROADCAST, BTN_STATS],
        [BTN_HOME],
    ])


async def admin_panel(chat_id, uid=None):
    if uid is not None:
        states.pop(uid, None)
    await send(chat_id, "⚙️ ADMIN PANEL\n\nKerakli bo‘limni tanlang:", admin_keyboard())


# ----- Menyularni boshqarish (inline) -----


async def manager_view(chat_id, mid="root", msg_id=None):
    if mid != "root" and mid not in data["menus"]:
        mid = "root"

    rows = []
    parent_key = None if mid == "root" else mid

    for cid, m in children(parent_key):
        rows.append([{"text": "📂 " + m["name"], "callback_data": "am:" + cid}])

    if mid == "root":
        text = "📁 MENYULAR (asosiy daraja)\n\nBo‘limni tanlang yoki yangisini qo‘shing:"
        rows.append([{"text": "➕ Menyu qo‘shish", "callback_data": "aadd:root"}])
    else:
        menu = data["menus"][mid]
        text = (
            f"📂 {menu_path(mid)}\n\n"
            f"📝 Xabarlar: {len(menu.get('items', []))}\n"
            f"📁 Ichki menyular: {len(children(mid))}"
        )
        rows.append([
            {"text": "➕ Ichki menyu", "callback_data": "aadd:" + mid},
            {"text": "📝 Xabar qo‘shish", "callback_data": "aitem:" + mid},
        ])
        rows.append([
            {"text": "🗂 Xabarlar ro‘yxati", "callback_data": "ails:" + mid},
            {"text": "👁 Ko‘rish", "callback_data": "aview:" + mid},
        ])
        rows.append([
            {"text": "✏️ Nomini o‘zgartirish", "callback_data": "aren:" + mid},
            {"text": "🗑 O‘chirish", "callback_data": "adel:" + mid},
        ])
        rows.append([
            {"text": "🔼 Yuqoriga", "callback_data": "aup:" + mid},
            {"text": "🔽 Pastga", "callback_data": "adn:" + mid},
        ])
        back = menu.get("parent") or "root"
        rows.append([{"text": "⬅️ Orqaga", "callback_data": "am:" + back}])

    await edit(chat_id, msg_id, text, ikb(rows))


async def items_view(chat_id, mid, msg_id=None):
    menu = data["menus"].get(mid)
    if not menu:
        await manager_view(chat_id, "root", msg_id)
        return

    items = menu.get("items", [])
    rows = []
    if items:
        text = f"🗂 {menu['name']} — xabarlar\n\nO‘chirish uchun xabar tugmasini bosing:"
        for i, item in enumerate(items):
            rows.append([{
                "text": "🗑 " + item_label(item, i + 1),
                "callback_data": f"aid:{mid}:{i}",
            }])
        rows.append([{"text": "🧹 Hammasini tozalash", "callback_data": "aclr:" + mid}])
    else:
        text = f"🗂 {menu['name']}\n\nBu menyuda hali xabar yo‘q."

    rows.append([{"text": "⬅️ Orqaga", "callback_data": "am:" + mid}])
    await edit(chat_id, msg_id, text, ikb(rows))


# ----- Kanallar -----


async def channels_view(chat_id, msg_id=None):
    rows = []
    for i, ch in enumerate(data["channels"]):
        rows.append([{"text": "🗑 " + ch, "callback_data": f"chdel:{i}"}])
    rows.append([{"text": "➕ Kanal qo‘shish", "callback_data": "chadd"}])

    text = "📢 MAJBURIY OBUNA KANALLARI\n\n"
    text += ("O‘chirish uchun kanal tugmasini bosing."
             if data["channels"] else "Hozircha kanal yo‘q.")
    await edit(chat_id, msg_id, text, ikb(rows))


# ----- Adminlar -----


async def admins_view(chat_id, msg_id=None):
    rows = []
    text = "👮 ADMINLAR\n\n"
    for admin in data["admins"]:
        if admin in MAIN_ADMINS:
            text += f"⭐ {admin} (asosiy)\n"
        else:
            text += f"👤 {admin}\n"
            rows.append([{"text": f"❌ {admin}", "callback_data": f"addel:{admin}"}])
    rows.append([{"text": "➕ Admin qo‘shish", "callback_data": "adadd"}])
    await edit(chat_id, msg_id, text, ikb(rows))


def stats_text():
    return (
        "📊 STATISTIKA\n\n"
        f"👥 Foydalanuvchilar: {len(data['users'])}\n"
        f"👮 Adminlar: {len(data['admins'])}\n"
        f"📁 Menyular: {len(data['menus'])}\n"
        f"📢 Kanallar: {len(data['channels'])}"
    )


# ==================================
# CALLBACK HANDLER
# ==================================


async def start_state(chat_id, uid, state, prompt, done_button=False):
    states[uid] = state
    rows = [[BTN_DONE], [BTN_CANCEL]] if done_button else [[BTN_CANCEL]]
    await send(chat_id, prompt, reply_kb(rows))


async def callback_handler(callback: CallbackQuery):
    msg = callback.message
    if not msg:
        return

    uid = callback.from_user.id
    chat_id = msg.chat.id
    msg_id = msg.message_id
    value = callback.data or ""

    await answer(callback.id)
    add_user(uid)

    if value == "check_sub":
        if await check_sub(uid):
            await show_home(chat_id, uid)
        else:
            await send(chat_id, "❌ Hali barcha kanallarga obuna bo‘lmagansiz.",
                       await subscription_keyboard())
        return

    if not is_admin(uid):
        return

    parts = value.split(":")
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else None

    # ----- Menyular -----
    if cmd == "am":
        await manager_view(chat_id, arg or "root", msg_id)

    elif cmd == "aadd":
        if arg != "root" and arg not in data["menus"]:
            await send(chat_id, "❌ Menyu topilmadi.")
            return
        prompt = ("Yangi menyu nomini yuboring:" if arg == "root"
                  else f"«{data['menus'][arg]['name']}» ichiga qo‘shiladigan menyu nomini yuboring:")
        await start_state(chat_id, uid, {"action": "add_menu", "parent": arg}, prompt)

    elif cmd == "aitem":
        if arg not in data["menus"]:
            await send(chat_id, "❌ Menyu topilmadi.")
            return
        await start_state(
            chat_id, uid, {"action": "add_item", "menu_id": arg, "count": 0},
            "Menyuga qo‘shiladigan matn, rasm, video yoki fayl yuboring.\n"
            "Bir nechta yuborishingiz mumkin. Tugagach «✅ Tayyor» ni bosing.",
            done_button=True,
        )

    elif cmd == "aren":
        if arg not in data["menus"]:
            await send(chat_id, "❌ Menyu topilmadi.")
            return
        await start_state(chat_id, uid, {"action": "rename", "menu_id": arg},
                          "Yangi nomni yuboring:")

    elif cmd == "aview":
        if arg in data["menus"]:
            menu = data["menus"][arg]
            await send(chat_id, "👁 Foydalanuvchi ko‘radigani: " + menu["name"])
            if menu.get("items"):
                await send_items(chat_id, menu)
            else:
                await send(chat_id, "(xabarlar yo‘q)")

    elif cmd == "ails":
        await items_view(chat_id, arg, msg_id)

    elif cmd == "aid":
        mid, idx = parts[1], int(parts[2])
        menu = data["menus"].get(mid)
        if menu and 0 <= idx < len(menu.get("items", [])):
            menu["items"].pop(idx)
            save()
        await items_view(chat_id, mid, msg_id)

    elif cmd == "aclr":
        if arg not in data["menus"]:
            return
        await edit(chat_id, msg_id,
                   "🧹 Menyudagi barcha xabarlar o‘chirilsinmi?",
                   ikb([[
                       {"text": "✅ Ha", "callback_data": "aclrok:" + arg},
                       {"text": "❌ Yo‘q", "callback_data": "ails:" + arg},
                   ]]))

    elif cmd == "aclrok":
        if arg in data["menus"]:
            data["menus"][arg]["items"] = []
            save()
        await items_view(chat_id, arg, msg_id)

    elif cmd == "adel":
        if arg not in data["menus"]:
            return
        await edit(chat_id, msg_id,
                   f"🗑 «{menu_path(arg)}» va uning barcha ichki menyulari o‘chirilsinmi?",
                   ikb([[
                       {"text": "✅ Ha, o‘chirish", "callback_data": "adelok:" + arg},
                       {"text": "❌ Yo‘q", "callback_data": "am:" + arg},
                   ]]))

    elif cmd == "adelok":
        menu = data["menus"].get(arg)
        parent = (menu.get("parent") if menu else None) or "root"
        remove_menu(arg)
        for u, cur in list(where.items()):
            if cur is not None and cur not in data["menus"]:
                where[u] = None
        save()
        await manager_view(chat_id, parent, msg_id)

    elif cmd in ("aup", "adn"):
        if arg in data["menus"]:
            move_menu(arg, -1 if cmd == "aup" else 1)
            save()
            await manager_view(chat_id, arg, msg_id)

    # ----- Kanallar -----
    elif cmd == "chadd":
        await start_state(chat_id, uid, {"action": "add_channel"},
                          "Kanal username (@kanal) yoki ID sini yuboring.\n"
                          "⚠️ Bot o‘sha kanalda admin bo‘lishi shart!")

    elif cmd == "chdel":
        idx = int(arg)
        if 0 <= idx < len(data["channels"]):
            data["channels"].pop(idx)
            save()
        await channels_view(chat_id, msg_id)

    # ----- Adminlar -----
    elif cmd == "adadd":
        await start_state(chat_id, uid, {"action": "add_admin"},
                          "Yangi admin Telegram ID sini yuboring:")

    elif cmd == "addel":
        admin_id = int(arg)
        if admin_id in MAIN_ADMINS:
            await send(chat_id, "Bu asosiy adminni o‘chirib bo‘lmaydi.")
            return
        if admin_id in data["admins"]:
            data["admins"].remove(admin_id)
            save()
        await admins_view(chat_id, msg_id)


# ==================================
# MEDIA ANIQLASH
# ==================================


def get_file_id(message: Message):
    for kind in MEDIA_METHODS:
        item = getattr(message, kind, None)
        if item:
            if kind == "photo":
                return kind, item[-1].file_id
            return kind, item.file_id
    return None, None


# ==================================
# XABAR TARQATISH
# ==================================


async def do_broadcast(chat_id, message_id):
    ok_count = 0
    fail_count = 0

    for user in list(data["users"]):
        try:
            await bot.copy_message(chat_id=user, from_chat_id=chat_id,
                                   message_id=message_id)
            ok_count += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.copy_message(chat_id=user, from_chat_id=chat_id,
                                       message_id=message_id)
                ok_count += 1
            except TelegramAPIError:
                fail_count += 1
        except TelegramAPIError:
            fail_count += 1
        await asyncio.sleep(0.05)

    return ok_count, fail_count


# ==================================
# ADMIN HOLATLARI (matn kiritish)
# ==================================


async def handle_state(uid, chat_id, message: Message, text):
    state = states[uid]
    action = state["action"]

    if text == BTN_CANCEL:
        states.pop(uid, None)
        await send(chat_id, "Bekor qilindi.")
        await admin_panel(chat_id)
        return

    if action == "add_menu":
        parent = state["parent"]
        parent_id = None if parent == "root" else parent
        if parent_id is not None and parent_id not in data["menus"]:
            states.pop(uid, None)
            await send(chat_id, "❌ Asosiy menyu topilmadi.")
            await admin_panel(chat_id)
            return

        name = text.strip()
        err = name_error(name, parent_id)
        if err:
            await send(chat_id, "❌ " + err)
            return

        mid = new_id()
        data["menus"][mid] = {"name": name, "parent": parent_id, "items": []}
        states.pop(uid, None)
        await admin_panel(chat_id)
        if save():
            await send(chat_id, "✅ Menyu saqlandi.")
        else:
            data["menus"].pop(mid, None)
            await send(chat_id, "❌ Saqlashda xatolik.")
        await manager_view(chat_id, parent)
        return

    if action == "rename":
        mid = state["menu_id"]
        if mid not in data["menus"]:
            states.pop(uid, None)
            await send(chat_id, "❌ Menyu topilmadi.")
            await admin_panel(chat_id)
            return

        name = text.strip()
        err = name_error(name, data["menus"][mid].get("parent"), exclude=mid)
        if err:
            await send(chat_id, "❌ " + err)
            return

        old = data["menus"][mid]["name"]
        data["menus"][mid]["name"] = name
        states.pop(uid, None)
        await admin_panel(chat_id)
        if save():
            await send(chat_id, "✅ Nom o‘zgartirildi.")
        else:
            data["menus"][mid]["name"] = old
            await send(chat_id, "❌ Saqlashda xatolik.")
        await manager_view(chat_id, mid)
        return

    if action == "add_item":
        mid = state["menu_id"]
        if mid not in data["menus"]:
            states.pop(uid, None)
            await send(chat_id, "❌ Menyu topilmadi.")
            await admin_panel(chat_id)
            return

        if text == BTN_DONE:
            states.pop(uid, None)
            await admin_panel(chat_id)
            await send(chat_id, f"✅ {state['count']} ta xabar saqlandi.")
            await manager_view(chat_id, mid)
            return

        kind, file_id = get_file_id(message)
        if message.text is not None:
            item = {"type": "text", "value": message.text}
        elif kind:
            item = {"type": kind, "value": file_id}
            if message.caption:
                item["caption"] = message.caption
        else:
            await send(chat_id, "❌ Bu xabar turi qo‘llab-quvvatlanmaydi.")
            return

        data["menus"][mid].setdefault("items", []).append(item)
        if save():
            state["count"] += 1
            await send(chat_id, f"✅ Saqlandi (jami: {state['count']}). "
                                "Yana yuboring yoki «✅ Tayyor» ni bosing.")
        else:
            data["menus"][mid]["items"].pop()
            await send(chat_id, "❌ Xabar saqlanmadi.")
        return

    if action == "add_channel":
        if not text.strip():
            await send(chat_id, "Kanal username yoki ID sini yuboring.")
            return

        channel = normalize_channel(text)
        if channel not in data["channels"]:
            data["channels"].append(channel)

        states.pop(uid, None)
        await admin_panel(chat_id)
        if save():
            await send(chat_id, f"✅ Kanal qo‘shildi: {channel}")
            try:
                await bot.get_chat(channel)
            except TelegramAPIError:
                await send(chat_id, "⚠️ Kanal topilmadi yoki bot unda admin emas. "
                                    "Aks holda obuna tekshirilmaydi.")
        else:
            await send(chat_id, "❌ Saqlashda xatolik.")
        await channels_view(chat_id)
        return

    if action == "add_admin":
        try:
            new_admin = int(text.strip())
        except ValueError:
            await send(chat_id, "❌ Telegram ID faqat raqamlardan iborat bo‘lishi kerak.")
            return

        if new_admin not in data["admins"]:
            data["admins"].append(new_admin)

        states.pop(uid, None)
        await admin_panel(chat_id)
        await send(chat_id, "✅ Admin qo‘shildi." if save() else "❌ Saqlashda xatolik.")
        await admins_view(chat_id)
        return

    if action == "broadcast":
        states.pop(uid, None)
        await admin_panel(chat_id)
        await send(chat_id, "⏳ Xabar yuborilmoqda...")
        ok_count, fail_count = await do_broadcast(chat_id, message.message_id)
        await send(chat_id, f"✅ Yuborildi: {ok_count}\n❌ Yuborilmadi: {fail_count}")
        return


# ==================================
# XABAR HANDLER
# ==================================


async def message_handler(message: Message):
    if message.from_user is None:
        return
    if message.chat.type != "private":
        return

    uid = message.from_user.id
    chat_id = message.chat.id
    text = message.text or ""

    add_user(uid)

    if text == "/start":
        await show_home(chat_id, uid)
        return

    if not await check_sub(uid):
        await ask_subscribe(chat_id)
        return

    # /admin kiritish holatini bekor qilib, panelni ochadi
    if text == "/admin" and uid in states and is_admin(uid):
        await admin_panel(chat_id, uid)
        return

    # Admin kiritish holati
    if uid in states:
        if is_admin(uid):
            await handle_state(uid, chat_id, message, text)
        else:
            states.pop(uid, None)
        return

    if text == BTN_HOME:
        await show_home(chat_id, uid)
        return

    if text == BTN_BACK:
        await go_back(chat_id, uid)
        return

    if text == BTN_CONTACT:
        await send(chat_id, "📞 Admin bilan bog‘lanish:\nTelegram: " + CONTACT)
        return

    if text in ("/admin", BTN_ADMIN):
        if is_admin(uid):
            await admin_panel(chat_id, uid)
        return

    # Admin panel tugmalari
    if is_admin(uid):
        if text == BTN_MENUS:
            await manager_view(chat_id, "root")
            return
        if text == BTN_CHANNELS:
            await channels_view(chat_id)
            return
        if text == BTN_ADMINS:
            await admins_view(chat_id)
            return
        if text == BTN_BROADCAST:
            await start_state(chat_id, uid, {"action": "broadcast"},
                              "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring:")
            return
        if text == BTN_STATS:
            await send(chat_id, stats_text())
            return

    # Foydalanuvchi menyusi bo'ylab yurish
    if text and await navigate(chat_id, uid, text):
        return

    if text:
        await send(chat_id, "Iltimos, pastdagi tugmalardan foydalaning 👇",
                   user_keyboard(uid, where.get(uid)))


# ==================================
# AIOGRAM ROUTERLARI
# ==================================


@router.message()
async def on_message(message: Message):
    try:
        await message_handler(message)
    except Exception as e:
        print("Update xatosi:", repr(e))


@router.callback_query()
async def on_callback(callback: CallbackQuery):
    try:
        await callback_handler(callback)
    except Exception as e:
        print("Update xatosi:", repr(e))


# ==================================
# BOTNI ISHGA TUSHIRISH
# ==================================


async def main():
    logging.basicConfig(level=logging.INFO)

    try:
        me = await bot.get_me()
    except Exception as e:
        print("XATO: Token noto‘g‘ri yoki internet yo‘q:", e)
        await bot.session.close()
        raise SystemExit(1)

    print("BOT ISHGA TUSHDI!  @" + (me.username or "?"))
    print("Ma'lumot fayli:", DATA_FILE)

    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
