"""Konstruktor matnlari 4 tilda: uz (lotin), uzc (kirill), ru, en.
Kirill alifbosidagi o'zbekcha matn lotin matnidan avtomatik o'giriladi (translit)."""
import re

from config import PLANS, PLAN_DAYS, SPEED

LANGS = {"uz": "🇺🇿 O'zbekcha", "uzc": "🇺🇿 Ўзбекча", "ru": "🇷🇺 Русский", "en": "🇺🇸 English"}

CHOOSE_LANG = ("O'zingizga qulay tilni tanlang 🇺🇿\n\nЎзингизга қулай тилни танланг 🇺🇿\n\n"
               "Выбери язык, который тебе нравится 🇷🇺\n\nChoose the language you like 🇺🇸")

# ---------------------------------------------------------------- lotin -> kirill
_PROTECT = re.compile(r"(\{[^}]*\}|<[^>]*>|https?://\S+|@\w+'?|/cancel|/start|BotFather'?|Click|Payme|Start|Lite|Standard|Pro|Premium|Unlimited|Gbit/s|ID)")
_APOS = "'‘’ʻʼ`"
_MAP = dict(zip("abdefghijklmnopqrstuvxyz", "абдефгҳижклмнопқрстувхйз"))
_MAP["c"] = "с"
_MAP["w"] = "в"


def _translit_plain(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        ch, low = s[i], s[i].lower()
        nxt = s[i + 1] if i + 1 < n else ""
        pair = (low + nxt.lower()) if nxt else low
        up = ch.isupper()
        res = None
        if low in "og" and nxt and nxt in _APOS:
            res, step = ("ў" if low == "o" else "ғ"), 2
        elif pair in ("sh", "ch"):
            res, step = ("ш" if pair == "sh" else "ч"), 2
        elif pair in ("yo", "yu", "ya", "ye"):
            res, step = {"yo": "ё", "yu": "ю", "ya": "я", "ye": "е"}[pair], 2
        elif ch in _APOS:
            res, step = "ъ", 1
        elif low == "e" and (i == 0 or not s[i - 1].isalpha()):
            res, step = "э", 1
        elif low in _MAP:
            res, step = _MAP[low], 1
        else:
            out.append(ch); i += 1
            continue
        out.append(res.upper() if up else res)
        i += step
    return "".join(out)


def translit(text: str) -> str:
    return "".join(part if _PROTECT.fullmatch(part) else _translit_plain(part) for part in _PROTECT.split(text))


# ---------------------------------------------------------------- matnlar
UZ = {
    # tugmalar
    "b_create": "➕ Bot yaratish", "b_my": "🤖 Botlarim", "b_income": "💼 Daromad", "b_cab": "👤 Hisobim",
    "b_topup": "💳 Hisob to'ldirish", "b_ref": "🗣 Referal", "b_site": "🌐 Saytga kirish", "b_guide": "📘 Qo'llanma",
    "b_help": "✉️ Murojaat", "b_tariffs": "📦 Tariflar", "b_transfer": "💸 Pul o'tkazish",
    # bot turlari
    "type_kino": "🎬 Kino Bot", "type_kitob": "📚 Kitob Bot", "type_video": "📥 Video yuklovchi", "type_konkurs": "🏆 Konkurs Bot",
    "type_moderator": "🛡 Guruh moderator", "type_fon": "🖼 Fon o'chirish", "type_stars": "⭐ Stars Bot", "type_shrift": "🔤 Shrift Bot",
    "type_menyu": "🍽 Menyu Bot", "type_nomer": "📲 Nomer Bot", "type_yuklovchi": "📥 Super yuklovchi", "type_referal": "💰 Referal Bot",
    # birliklar
    "u_som": "so'm", "u_day": "kun", "u_pcs": "ta", "u_min": "daqiqa",
    # tariflar
    "tar_title": "📦 Tariflar — barcha botlar uchun:", "tar_month": "Oylik to'lov", "tar_daily": "Kunlik aktivlar",
    "tar_rate": "Xabarnoma tezligi", "tar_monthly": "Oylik so'rovlar limiti", "tar_speed": "Server tezligi",
    "tar_extra": "Qo'shimcha: bot dizaynini o'zgartirish va 4 tilda til tanlash",
    # umumiy
    "welcome": "👋 Xush kelibsiz! Bu yerda o'z Telegram botingizni bir necha soniyada yaratishingiz mumkin.\n\n🎁 Har bir yangi bot {days} kun bepul sinov bilan beriladi.\n📦 Tariflar quyida 👇",
    "cab": "📱 <b>Shaxsiy kabinet</b>\n\n🆔 ID: <code>{uid}</code>\n💰 Balans: {bal}\n🤖 Botlar: {n} ta",
    "site_title": "🌐 Sayt:", "site_btn": "Saytga kirish",
    "guide": "📘 <b>Qo'llanma</b>\n\n1. @BotFather'da yangi bot yarating va tokenni oling.\n2. «➕ Bot yaratish» → bot turini tanlang.\n3. Tokenni yuboring — bot darrov ishga tushadi va {days} kun bepul sinov muddati beriladi.\n4. Yangi botingizga kirib /start bosing: siz uning adminisiz.\n5. «🤖 Botlarim» bo'limidan to'xtatish, o'chirish, tarifni tanlash yoki dizaynni o'zgartirish mumkin.",
    "ref": "🗣 <b>Referal dastur</b>\n\nDo'stingiz sizning havolangiz orqali kirib, birinchi marta hisobini to'ldirsa, sizga {bonus} beriladi.\n\n🔗 {link}\n👥 Takliflar: {cnt} ta",
    "income": "💼 <b>Daromad</b>\n\n👥 Takliflar: {cnt} ta\n✅ Hisob to'ldirganlar: {paid} ta\n💰 Jami topilgan: {earned}\nDaromad avtomatik balansingizga qo'shiladi.",
    "pick_type": "🤖 Quyidagi bot turlaridan birini tanlang:",
    "need_balance": "Balans yetarli emas. Narxi: {price}",
    "type_chosen": "{name} tanlandi.\n\n@BotFather'dan olingan <b>tokenni</b> yuboring (bekor qilish: /cancel).",
    "cancelled": "Bekor qilindi.",
    "token_dup": "❌ Bu token allaqachon ulangan.", "token_bad": "❌ Token noto'g'ri. Qaytadan yuboring yoki /cancel.",
    "no_balance": "❌ Balans yetarli emas.", "starting": "⏳ Bot ishga tushirilmoqda...",
    "start_fail": "❌ Bot ishga tushmadi. Tokenni tekshirib qayta urinib ko'ring.",
    "created": "✅ Tayyor! @{username} ishga tushdi.\nUnga kirib /start bosing — siz uning adminisiz.\n\n🎁 Sinov muddati: {days} kun. Tugashiga {hours} soat qolganda ogohlantiramiz.",
    "no_bots": "Sizda hali bot yo'q. «➕ Bot yaratish» ni bosing.", "my_list": "🤖 Botlaringiz:",
    "lbl_type": "Turi", "lbl_status": "Holat", "lbl_expiry": "Muddat", "lbl_plan": "Tarif", "lbl_usage": "Foydalanish",
    "lbl_today": "bugun", "lbl_month": "oy",
    "st_run": "🟢 ishlayapti", "st_stop": "🔴 to'xtagan", "st_exp": "⏳ muddati tugagan", "forever": "cheksiz",
    "btn_stop": "⏹ To'xtatish", "btn_start": "▶️ Ishga tushirish", "btn_plan": "📦 Tarifni tanlash / uzaytirish",
    "btn_design": "🎨 Dizayn", "btn_design_lock": "🎨 Dizayn (Pro va yuqori)", "btn_delete": "🗑 O'chirish", "btn_back": "⬅️ Orqaga",
    "expired_alert": "Muddati tugagan. Avval tarifni tanlang.", "start_fail_alert": "Ishga tushmadi, tokenni tekshiring.",
    "deleted": "🗑 Bot o'chirildi.", "done": "Bajarildi",
    "plan_pick": "📦 Tarifni tanlang (har biri {days} kunga). Balans: {bal}\n\n{tariffs}",
    "plan_done": "✅ @{username} uchun «{plan}» tarifi yoqildi. Amal qilish muddati: {left}.",
    "design_menu": "🎨 @{username} dizayni. Nimani o'zgartirmoqchisiz?",
    "d_name": "✏️ Bot nomi", "d_desc": "📝 Tavsif", "d_short": "💬 Qisqa tavsif",
    "d_ask": "Yangi qiymatni yuboring (bekor qilish: /cancel). Limit: {limit} belgi.",
    "d_ok": "✅ Yangilandi.", "d_fail": "❌ O'zgartirib bo'lmadi: {err}", "d_toolong": "❌ Juda uzun. Eng ko'pi bilan {limit} belgi.",
    "topup_ask": "💳 Necha so'm to'ldirmoqchisiz? (kamida {min}). Bekor qilish: /cancel",
    "topup_bad": "Kamida {min} raqam bilan yozing.", "pay_pick": "💰 Summa: {amt}\nTo'lov usulini tanlang:",
    "pay_auto": "💳 {name} (avtomatik)", "pay_cheque_btn": "🧾 Karta + chek (qo'lda)",
    "pay_cheque": "To'lovni quyidagi kartaga o'tkazing:\n\n{card}\n\nSumma: {amt}\n\nSo'ng <b>chek skrinshotini</b> yuboring.",
    "pay_off": "Bu usul yoqilmagan.", "inv_title": "Hisobni to'ldirish", "inv_desc": "Balansni {amt}ga to'ldirish",
    "inv_bad": "Xatolik, qaytadan urinib ko'ring.", "inv_label": "Balans",
    "paid_ok": "✅ To'lov qabul qilindi! Balansingizga {amt} qo'shildi.\n💰 Balans: {bal}",
    "receipt_sent": "✅ Chek yuborildi. Admin tasdiqlagach balansingizga tushadi.",
    "receipt_bad": "🧾 Iltimos, chekning <b>skrinshotini rasm</b> sifatida yuboring (bekor qilish: /cancel).",
    "pay_approved": "✅ Hisobingiz {amt}ga to'ldirildi.", "pay_rejected": "❌ To'lovingiz tasdiqlanmadi. Murojaat bo'limiga yozing.",
    "ref_bonus": "🎉 Referalingiz hisobini to'ldirdi! Balansingizga {amt} qo'shildi.",
    "help_ask": "✉️ Xabaringizni yozing (bekor qilish: /cancel):", "help_sent": "✅ Yuborildi. Javobni shu bot orqali olasiz.",
    "lt_ended": "tugagan", "lt_dh": "{d} kun {h} soat", "lt_hm": "{h} soat {m} daqiqa",
    "exp_end": "⏳ @{username} botining muddati tugadi va bot to'xtatildi.\n«🤖 Botlarim» → botni tanlang → «📦 Tarifni tanlash» orqali davom ettiring. Ma'lumotlaringiz saqlanadi.",
    "exp_warn": "⚠️ @{username} botining muddati tugashiga {left} qoldi!\nUzilib qolmasligi uchun «🤖 Botlarim» → botni tanlang → «📦 Tarifni tanlash» ni bosing (eng arzon tarif: {price} / {days} kun). Balans: {bal}.",
    "bot_dead": "❌ @{username} ishga tushmayapti (token bekor qilingan bo'lishi mumkin) va to'xtatildi.",
    "gift": "🎁 Siz uchun @{username} boti yaratildi ({type}).\nUnga kirib /start bosing, siz uning adminisiz. «🤖 Botlarim» bo'limida ko'rasiz.\n🎁 Sinov muddati: {days} kun.",
}

RU = {
    "b_create": "➕ Создать бота", "b_my": "🤖 Мои боты", "b_income": "💼 Доход", "b_cab": "👤 Мой счёт",
    "b_topup": "💳 Пополнить счёт", "b_ref": "🗣 Рефералы", "b_site": "🌐 Перейти на сайт", "b_guide": "📘 Инструкция",
    "b_help": "✉️ Обращение", "b_tariffs": "📦 Тарифы", "b_transfer": "💸 Перевод денег",
    "type_kino": "🎬 Кино-бот", "type_kitob": "📚 Книжный бот", "type_video": "📥 Загрузчик видео", "type_konkurs": "🏆 Бот для конкурсов",
    "type_moderator": "🛡 Модератор группы", "type_fon": "🖼 Удаление фона", "type_stars": "⭐ Stars-бот", "type_shrift": "🔤 Бот шрифтов",
    "type_menyu": "🍽 Бот-меню", "type_nomer": "📲 Бот номеров", "type_yuklovchi": "📥 Супер-загрузчик", "type_referal": "💰 Реферальный бот",
    "u_som": "сум", "u_day": "день", "u_pcs": "шт.", "u_min": "мин",
    "tar_title": "📦 Тарифы — для всех ботов:", "tar_month": "Оплата в месяц", "tar_daily": "Активных в день",
    "tar_rate": "Скорость рассылки", "tar_monthly": "Лимит запросов в месяц", "tar_speed": "Скорость сервера",
    "tar_extra": "Дополнительно: изменение дизайна бота и выбор языка (4 языка)",
    "welcome": "👋 Добро пожаловать! Здесь вы можете создать своего Telegram-бота за несколько секунд.\n\n🎁 Каждый новый бот получает {days} дн. бесплатного пробного периода.\n📦 Тарифы ниже 👇",
    "cab": "📱 <b>Личный кабинет</b>\n\n🆔 ID: <code>{uid}</code>\n💰 Баланс: {bal}\n🤖 Боты: {n} шт.",
    "site_title": "🌐 Сайт:", "site_btn": "Перейти на сайт",
    "guide": "📘 <b>Инструкция</b>\n\n1. Создайте бота в @BotFather и получите токен.\n2. «➕ Создать бота» → выберите тип бота.\n3. Отправьте токен — бот сразу запустится, и вы получите {days} дн. бесплатного пробного периода.\n4. Зайдите в нового бота и нажмите /start: вы его администратор.\n5. В разделе «🤖 Мои боты» можно остановить, удалить бота, выбрать тариф или изменить дизайн.",
    "ref": "🗣 <b>Реферальная программа</b>\n\nЕсли друг перейдёт по вашей ссылке и впервые пополнит счёт, вы получите {bonus}.\n\n🔗 {link}\n👥 Приглашено: {cnt}",
    "income": "💼 <b>Доход</b>\n\n👥 Приглашено: {cnt}\n✅ Пополнили счёт: {paid}\n💰 Всего заработано: {earned}\nДоход автоматически зачисляется на баланс.",
    "pick_type": "🤖 Выберите тип бота:",
    "need_balance": "Недостаточно средств. Цена: {price}",
    "type_chosen": "Выбрано: {name}.\n\nОтправьте <b>токен</b> от @BotFather (отмена: /cancel).",
    "cancelled": "Отменено.",
    "token_dup": "❌ Этот токен уже подключён.", "token_bad": "❌ Неверный токен. Отправьте ещё раз или /cancel.",
    "no_balance": "❌ Недостаточно средств.", "starting": "⏳ Бот запускается...",
    "start_fail": "❌ Бот не запустился. Проверьте токен и попробуйте снова.",
    "created": "✅ Готово! @{username} запущен.\nЗайдите в него и нажмите /start — вы его администратор.\n\n🎁 Пробный период: {days} дн. За {hours} ч до окончания мы предупредим.",
    "no_bots": "У вас пока нет ботов. Нажмите «➕ Создать бота».", "my_list": "🤖 Ваши боты:",
    "lbl_type": "Тип", "lbl_status": "Статус", "lbl_expiry": "Срок", "lbl_plan": "Тариф", "lbl_usage": "Использование",
    "lbl_today": "сегодня", "lbl_month": "мес.",
    "st_run": "🟢 работает", "st_stop": "🔴 остановлен", "st_exp": "⏳ срок истёк", "forever": "бессрочно",
    "btn_stop": "⏹ Остановить", "btn_start": "▶️ Запустить", "btn_plan": "📦 Выбрать тариф / продлить",
    "btn_design": "🎨 Дизайн", "btn_design_lock": "🎨 Дизайн (Pro и выше)", "btn_delete": "🗑 Удалить", "btn_back": "⬅️ Назад",
    "expired_alert": "Срок истёк. Сначала выберите тариф.", "start_fail_alert": "Не запустился, проверьте токен.",
    "deleted": "🗑 Бот удалён.", "done": "Готово",
    "plan_pick": "📦 Выберите тариф (каждый на {days} дн.). Баланс: {bal}\n\n{tariffs}",
    "plan_done": "✅ Для @{username} включён тариф «{plan}». Срок действия: {left}.",
    "design_menu": "🎨 Дизайн @{username}. Что изменить?",
    "d_name": "✏️ Имя бота", "d_desc": "📝 Описание", "d_short": "💬 Краткое описание",
    "d_ask": "Отправьте новое значение (отмена: /cancel). Лимит: {limit} символов.",
    "d_ok": "✅ Обновлено.", "d_fail": "❌ Не удалось изменить: {err}", "d_toolong": "❌ Слишком длинно. Максимум {limit} символов.",
    "topup_ask": "💳 На сколько сум пополнить? (минимум {min}). Отмена: /cancel",
    "topup_bad": "Напишите число не менее {min}.", "pay_pick": "💰 Сумма: {amt}\nВыберите способ оплаты:",
    "pay_auto": "💳 {name} (автоматически)", "pay_cheque_btn": "🧾 Карта + чек (вручную)",
    "pay_cheque": "Переведите оплату на карту:\n\n{card}\n\nСумма: {amt}\n\nЗатем отправьте <b>скриншот чека</b>.",
    "pay_off": "Этот способ не включён.", "inv_title": "Пополнение счёта", "inv_desc": "Пополнение баланса на {amt}",
    "inv_bad": "Ошибка, попробуйте снова.", "inv_label": "Баланс",
    "paid_ok": "✅ Оплата получена! На баланс зачислено {amt}.\n💰 Баланс: {bal}",
    "receipt_sent": "✅ Чек отправлен. После подтверждения админом сумма поступит на баланс.",
    "receipt_bad": "🧾 Пожалуйста, отправьте <b>скриншот чека</b> как фото (отмена: /cancel).",
    "pay_approved": "✅ Ваш счёт пополнен на {amt}.", "pay_rejected": "❌ Ваш платёж не подтверждён. Напишите в раздел «Обращение».",
    "ref_bonus": "🎉 Ваш реферал пополнил счёт! На баланс зачислено {amt}.",
    "help_ask": "✉️ Напишите ваше сообщение (отмена: /cancel):", "help_sent": "✅ Отправлено. Ответ придёт в этого бота.",
    "lt_ended": "истёк", "lt_dh": "{d} дн. {h} ч.", "lt_hm": "{h} ч. {m} мин.",
    "exp_end": "⏳ Срок бота @{username} истёк, бот остановлен.\nПродолжите через «🤖 Мои боты» → выберите бота → «📦 Выбрать тариф». Ваши данные сохранены.",
    "exp_warn": "⚠️ До окончания срока бота @{username} осталось {left}!\nЧтобы не было перерыва, нажмите «🤖 Мои боты» → выберите бота → «📦 Выбрать тариф» (самый дешёвый тариф: {price} / {days} дн.). Баланс: {bal}.",
    "bot_dead": "❌ Бот @{username} не запускается (возможно, токен отозван) и остановлен.",
    "gift": "🎁 Для вас создан бот @{username} ({type}).\nЗайдите в него и нажмите /start — вы его администратор. Он в разделе «🤖 Мои боты».\n🎁 Пробный период: {days} дн.",
}

EN = {
    "b_create": "➕ Create bot", "b_my": "🤖 My bots", "b_income": "💼 Income", "b_cab": "👤 My account",
    "b_topup": "💳 Top up balance", "b_ref": "🗣 Referrals", "b_site": "🌐 Open website", "b_guide": "📘 Guide",
    "b_help": "✉️ Support", "b_tariffs": "📦 Plans", "b_transfer": "💸 Send money",
    "type_kino": "🎬 Movie Bot", "type_kitob": "📚 Book Bot", "type_video": "📥 Video Downloader", "type_konkurs": "🏆 Contest Bot",
    "type_moderator": "🛡 Group Moderator", "type_fon": "🖼 Background Remover", "type_stars": "⭐ Stars Bot", "type_shrift": "🔤 Font Bot",
    "type_menyu": "🍽 Menu Bot", "type_nomer": "📲 Number Bot", "type_yuklovchi": "📥 Super Downloader", "type_referal": "💰 Referral Bot",
    "u_som": "UZS", "u_day": "day", "u_pcs": "pcs", "u_min": "min",
    "tar_title": "📦 Plans — for all bots:", "tar_month": "Monthly fee", "tar_daily": "Daily active users",
    "tar_rate": "Broadcast speed", "tar_monthly": "Monthly request limit", "tar_speed": "Server speed",
    "tar_extra": "Extra: change the bot's design and language selection (4 languages)",
    "welcome": "👋 Welcome! Here you can create your own Telegram bot in a few seconds.\n\n🎁 Every new bot comes with a {days}-day free trial.\n📦 Plans below 👇",
    "cab": "📱 <b>My account</b>\n\n🆔 ID: <code>{uid}</code>\n💰 Balance: {bal}\n🤖 Bots: {n}",
    "site_title": "🌐 Website:", "site_btn": "Open website",
    "guide": "📘 <b>Guide</b>\n\n1. Create a new bot in @BotFather and get the token.\n2. «➕ Create bot» → choose a bot type.\n3. Send the token — the bot starts right away with a {days}-day free trial.\n4. Open your new bot and press /start: you are its admin.\n5. In «🤖 My bots» you can stop or delete a bot, choose a plan or change its design.",
    "ref": "🗣 <b>Referral program</b>\n\nIf a friend joins via your link and tops up their balance for the first time, you get {bonus}.\n\n🔗 {link}\n👥 Invited: {cnt}",
    "income": "💼 <b>Income</b>\n\n👥 Invited: {cnt}\n✅ Topped up: {paid}\n💰 Total earned: {earned}\nIncome is added to your balance automatically.",
    "pick_type": "🤖 Choose a bot type:",
    "need_balance": "Not enough balance. Price: {price}",
    "type_chosen": "{name} selected.\n\nSend the <b>token</b> from @BotFather (cancel: /cancel).",
    "cancelled": "Cancelled.",
    "token_dup": "❌ This token is already connected.", "token_bad": "❌ Invalid token. Send it again or /cancel.",
    "no_balance": "❌ Not enough balance.", "starting": "⏳ Starting the bot...",
    "start_fail": "❌ The bot failed to start. Check the token and try again.",
    "created": "✅ Done! @{username} is running.\nOpen it and press /start — you are its admin.\n\n🎁 Trial: {days} days. We will warn you {hours} h before it ends.",
    "no_bots": "You have no bots yet. Tap «➕ Create bot».", "my_list": "🤖 Your bots:",
    "lbl_type": "Type", "lbl_status": "Status", "lbl_expiry": "Expires in", "lbl_plan": "Plan", "lbl_usage": "Usage",
    "lbl_today": "today", "lbl_month": "month",
    "st_run": "🟢 running", "st_stop": "🔴 stopped", "st_exp": "⏳ expired", "forever": "unlimited",
    "btn_stop": "⏹ Stop", "btn_start": "▶️ Start", "btn_plan": "📦 Choose plan / renew",
    "btn_design": "🎨 Design", "btn_design_lock": "🎨 Design (Pro and above)", "btn_delete": "🗑 Delete", "btn_back": "⬅️ Back",
    "expired_alert": "Expired. Choose a plan first.", "start_fail_alert": "Failed to start, check the token.",
    "deleted": "🗑 Bot deleted.", "done": "Done",
    "plan_pick": "📦 Choose a plan ({days} days each). Balance: {bal}\n\n{tariffs}",
    "plan_done": "✅ The «{plan}» plan is active for @{username}. Valid for: {left}.",
    "design_menu": "🎨 Design of @{username}. What do you want to change?",
    "d_name": "✏️ Bot name", "d_desc": "📝 Description", "d_short": "💬 Short description",
    "d_ask": "Send the new value (cancel: /cancel). Limit: {limit} characters.",
    "d_ok": "✅ Updated.", "d_fail": "❌ Could not change: {err}", "d_toolong": "❌ Too long. At most {limit} characters.",
    "topup_ask": "💳 How many UZS do you want to add? (minimum {min}). Cancel: /cancel",
    "topup_bad": "Send a number of at least {min}.", "pay_pick": "💰 Amount: {amt}\nChoose a payment method:",
    "pay_auto": "💳 {name} (automatic)", "pay_cheque_btn": "🧾 Card + receipt (manual)",
    "pay_cheque": "Transfer the payment to this card:\n\n{card}\n\nAmount: {amt}\n\nThen send a <b>screenshot of the receipt</b>.",
    "pay_off": "This method is not enabled.", "inv_title": "Top up balance", "inv_desc": "Top up balance by {amt}",
    "inv_bad": "Error, please try again.", "inv_label": "Balance",
    "paid_ok": "✅ Payment received! {amt} added to your balance.\n💰 Balance: {bal}",
    "receipt_sent": "✅ Receipt sent. The amount will be credited once an admin confirms it.",
    "receipt_bad": "🧾 Please send a <b>screenshot of the receipt</b> as a photo (cancel: /cancel).",
    "pay_approved": "✅ Your balance was topped up by {amt}.", "pay_rejected": "❌ Your payment was not confirmed. Contact support.",
    "ref_bonus": "🎉 Your referral topped up their balance! {amt} added to your balance.",
    "help_ask": "✉️ Write your message (cancel: /cancel):", "help_sent": "✅ Sent. You will get the reply in this bot.",
    "lt_ended": "expired", "lt_dh": "{d} d {h} h", "lt_hm": "{h} h {m} min",
    "exp_end": "⏳ The trial/plan of @{username} has ended and the bot was stopped.\nContinue via «🤖 My bots» → pick the bot → «📦 Choose plan». Your data is kept.",
    "exp_warn": "⚠️ @{username} expires in {left}!\nTo avoid downtime, open «🤖 My bots» → pick the bot → «📦 Choose plan» (cheapest plan: {price} / {days} days). Balance: {bal}.",
    "bot_dead": "❌ @{username} fails to start (the token may have been revoked) and was stopped.",
    "gift": "🎁 A bot @{username} ({type}) was created for you.\nOpen it and press /start — you are its admin. Find it in «🤖 My bots».\n🎁 Trial: {days} days.",
}

UZ.update({
    "tr_ask_id": "💸 Pul o'tkazish.\nQabul qiluvchining ID raqamini yuboring (u konstruktorda /start bosgan bo'lishi kerak). Minimal summa: {min}. Bekor qilish: /cancel",
    "tr_bad_id": "ID faqat raqamlardan iborat bo'lishi kerak.", "tr_self": "O'zingizga pul o'tkaza olmaysiz.",
    "tr_nouser": "Bunday foydalanuvchi topilmadi. U konstruktorda /start bosgan bo'lishi kerak.",
    "tr_ask_amt": "👤 Qabul qiluvchi: {name} ({id})\n💰 Sizning balansingiz: {bal}\n\nNecha so'm o'tkazasiz? (kamida {min})",
    "tr_min": "Kamida {min} yozing (raqam bilan).", "tr_nofunds": "❌ Balans yetarli emas.",
    "tr_confirm": "Tasdiqlang:\n👤 {name} ({id})\n💸 Summa: {amt}", "tr_yes": "✅ Yuborish", "tr_no": "❌ Bekor",
    "tr_ok": "✅ {amt} {name} ga o'tkazildi.\n💰 Balans: {bal}", "tr_got": "💸 {name} sizga {amt} o'tkazdi.",
})
RU.update({
    "tr_ask_id": "💸 Перевод денег.\nОтправьте ID получателя (он должен нажать /start в конструкторе). Минимальная сумма: {min}. Отмена: /cancel",
    "tr_bad_id": "ID должен состоять только из цифр.", "tr_self": "Нельзя переводить самому себе.",
    "tr_nouser": "Пользователь не найден. Он должен нажать /start в конструкторе.",
    "tr_ask_amt": "👤 Получатель: {name} ({id})\n💰 Ваш баланс: {bal}\n\nСколько сум перевести? (минимум {min})",
    "tr_min": "Напишите не менее {min} (числом).", "tr_nofunds": "❌ Недостаточно средств.",
    "tr_confirm": "Подтвердите:\n👤 {name} ({id})\n💸 Сумма: {amt}", "tr_yes": "✅ Отправить", "tr_no": "❌ Отмена",
    "tr_ok": "✅ {amt} переведено пользователю {name}.\n💰 Баланс: {bal}", "tr_got": "💸 {name} перевёл вам {amt}.",
})
EN.update({
    "tr_ask_id": "💸 Send money.\nSend the recipient's ID (they must have pressed /start in the constructor). Minimum amount: {min}. Cancel: /cancel",
    "tr_bad_id": "The ID must contain digits only.", "tr_self": "You can't send money to yourself.",
    "tr_nouser": "User not found. They must have pressed /start in the constructor.",
    "tr_ask_amt": "👤 Recipient: {name} ({id})\n💰 Your balance: {bal}\n\nHow much do you want to send? (minimum {min})",
    "tr_min": "Send at least {min} (as a number).", "tr_nofunds": "❌ Not enough balance.",
    "tr_confirm": "Confirm:\n👤 {name} ({id})\n💸 Amount: {amt}", "tr_yes": "✅ Send", "tr_no": "❌ Cancel",
    "tr_ok": "✅ {amt} sent to {name}.\n💰 Balance: {bal}", "tr_got": "💸 {name} sent you {amt}.",
})


UZ.update({
    "b_special": "🛠 Maxsus bot", "b_admin": "👑 Admin panel",
    "special_ask": "🛠 Maxsus bot buyurtmasi.\n\nQanday bot kerakligini batafsil yozing: vazifalari, kerakli tugmalar, tillar, to'lov va hokazo. Admin siz bilan shu bot orqali bog'lanadi.\n\nBekor qilish: /cancel",
    "special_sent": "✅ Buyurtmangiz adminga yuborildi. Javobni shu bot orqali olasiz.",
})
RU.update({
    "b_special": "🛠 Особый бот", "b_admin": "👑 Админ-панель",
    "special_ask": "🛠 Заказ особого бота.\n\nПодробно опишите, какой бот вам нужен: задачи, кнопки, языки, оплата и т.д. Админ свяжется с вами через этого бота.\n\nОтмена: /cancel",
    "special_sent": "✅ Заказ отправлен админу. Ответ придёт в этом боте.",
})
EN.update({
    "b_special": "🛠 Custom bot", "b_admin": "👑 Admin panel",
    "special_ask": "🛠 Custom bot order.\n\nDescribe in detail the bot you need: tasks, buttons, languages, payment, etc. An admin will contact you via this bot.\n\nCancel: /cancel",
    "special_sent": "✅ Your order was sent to the admin. You will get the reply in this bot.",
})


UZ.update({
    "b_promo": "🎁 Promokod", "promo_ask": "🎁 Promokodni yuboring (bekor qilish: /cancel):",
    "promo_ok": "✅ Promokod faollashtirildi!\n💵 +{bonus}\n💰 Balans: {bal}",
    "promo_none": "❌ Bunday promokod topilmadi.", "promo_end": "⛔ Bu promokod tugadi!",
    "promo_used": "ℹ️ Siz bu promokoddan allaqachon foydalangansiz.",
    "sub_need": "🔒 Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling, so'ng «✅ Tekshirish» tugmasini bosing:",
    "sub_check": "✅ Tekshirish", "sub_ok": "✅ Rahmat! Endi botdan foydalanishingiz mumkin.",
    "sub_fail": "❌ Hali hammasiga a'zo bo'lmagansiz.",
})
RU.update({
    "b_promo": "🎁 Промокод", "promo_ask": "🎁 Отправьте промокод (отмена: /cancel):",
    "promo_ok": "✅ Промокод активирован!\n💵 +{bonus}\n💰 Баланс: {bal}",
    "promo_none": "❌ Такой промокод не найден.", "promo_end": "⛔ Этот промокод закончился!",
    "promo_used": "ℹ️ Вы уже использовали этот промокод.",
    "sub_need": "🔒 Чтобы пользоваться ботом, подпишитесь на каналы ниже и нажмите «✅ Проверить»:",
    "sub_check": "✅ Проверить", "sub_ok": "✅ Спасибо! Теперь вы можете пользоваться ботом.",
    "sub_fail": "❌ Вы подписались ещё не на все каналы.",
})
EN.update({
    "b_promo": "🎁 Promo code", "promo_ask": "🎁 Send the promo code (cancel: /cancel):",
    "promo_ok": "✅ Promo code activated!\n💵 +{bonus}\n💰 Balance: {bal}",
    "promo_none": "❌ Promo code not found.", "promo_end": "⛔ This promo code has run out!",
    "promo_used": "ℹ️ You have already used this promo code.",
    "sub_need": "🔒 To use the bot, join the channels below, then press «✅ Check»:",
    "sub_check": "✅ Check", "sub_ok": "✅ Thanks! You can now use the bot.",
    "sub_fail": "❌ You haven't joined all channels yet.",
})

S = {"uz": UZ, "ru": RU, "en": EN, "uzc": {k: translit(v) for k, v in UZ.items()}}
# bot turlari nomlari va tugma nomlarida emoji va lotin so'zlar translitda to'g'ri o'tadi; "Kino Bot" kabi brendlar ham o'giriladi.


LEGACY = {"b_cab": {'📱 Shaxsiy kabinet', '📱 Личный кабинет', '📱 My account', translit('📱 Shaxsiy kabinet')}}   # eski nomlar: telefonlarda eski tugma turgan bo'lsa ham ishlaydi
OVR = {}   # (til, kalit) -> admin o'zgartirgan matn


def register_type_title(key: str, title: str):
    """Admin yuklagan yangi bot turi nomini barcha tillarga qo'shadi."""
    for d in S.values():
        d["type_" + key] = title


def t(lang: str, key: str, **kw) -> str:
    text = OVR.get((lang, key)) or S.get(lang, UZ).get(key) or UZ[key]
    return text.format(**kw) if kw else text


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def money(n: int, lang: str = "uz") -> str:
    return f"{num(n)} {t(lang, 'u_som')}"


def all_texts(key: str) -> set:
    out = {S[l][key] for l in S} | LEGACY.get(key, set())
    out |= {v for (_, k), v in OVR.items() if k == key}          # admin nomini o'zgartirgan bo'lsa
    return out


def tariffs_text(lang: str) -> str:
    som, day, pcs, mn = (t(lang, k) for k in ("u_som", "u_day", "u_pcs", "u_min"))
    blocks = [t(lang, "tar_title")]
    for p in PLANS.values():
        lines = [f"▫️ • «{p['name']}» •",
                 f"💰 {t(lang, 'tar_month')}: {num(p['price'])} {som} ({num(p['price'] // PLAN_DAYS)} {som}/{day})",
                 f"🚹 {t(lang, 'tar_daily')}: {num(p['daily'])} {pcs}",
                 f"📩 {t(lang, 'tar_rate')}: {num(p['rate'])} {pcs}/{mn}",
                 f"💠 {t(lang, 'tar_monthly')}: {num(p['monthly'])} {pcs}",
                 f"🚀 {t(lang, 'tar_speed')}: {SPEED}"]
        if p["design"]:
            lines.append(f"⭐ {t(lang, 'tar_extra')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
