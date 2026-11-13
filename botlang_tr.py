"""Botlar uchun umumiy iboralar lug'ati: o'zbekcha (lotin) matn -> (ruscha, inglizcha).
Kalit botning kodidagi matn bilan AYNAN bir xil bo'lishi kerak (tugma yoki xabar bo'lagi).
Kirill o'zbekcha lotindan avtomatik o'giriladi, shuning uchun bu yerga yozilmaydi.
Lug'atda yo'q matn o'zbekcha (lotin) holida qoladi — yangi ibora qo'shish uchun pastga qator qo'shing."""

DICT = {
    # umumiy tugma va xabarlar
    "📊 Statistika": ("📊 Статистика", "📊 Statistics"),
    "✅ Tekshirish": ("✅ Проверить", "✅ Check"),
    "❌ Bekor": ("❌ Отмена", "❌ Cancel"),
    "✅ Yuborish": ("✅ Отправить", "✅ Send"),
    "Bekor qilindi.": ("Отменено.", "Cancelled."),
    "Tasdiqlang:": ("Подтвердите:", "Please confirm:"),
    "✅ Saqlandi.": ("✅ Сохранено.", "✅ Saved."),
    "Menyudan tanlang 👇": ("Выберите в меню 👇", "Choose from the menu 👇"),
    "👇 Uslubni tanlang:": ("👇 Выберите стиль:", "👇 Choose a style:"),
    "🚫 Siz bloklangansiz.": ("🚫 Вы заблокированы.", "🚫 You are blocked."),
    "Allaqachon yakunlangan.": ("Уже завершено.", "Already finished."),
    "Xabar topilmadi, qaytadan boshlang.": ("Сообщение не найдено, начните заново.", "Message not found, please start again."),
    "⚠️ Bot hozircha band, birozdan so'ng qayta urinib ko'ring.": (
        "⚠️ Бот сейчас занят, попробуйте позже.", "⚠️ The bot is busy right now, please try again later."),
    # majburiy obuna
    "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling, so'ng «Tekshirish» tugmasini bosing:": (
        "Чтобы пользоваться ботом, подпишитесь на каналы ниже, затем нажмите «Проверить»:",
        "To use the bot, subscribe to the channels below, then press “Check”:"),
    "Avval kanallarga obuna bo'ling.": ("Сначала подпишитесь на каналы.", "Please subscribe to the channels first."),
    "Hali hamma kanalga obuna bo'lmagansiz.": ("Вы подписаны не на все каналы.", "You haven't joined all the channels yet."),
    "📣 Majburiy obuna": ("📣 Обязательная подписка", "📣 Required subscription"),
    # xabar yuborish (admin)
    "📢 Xabar yuborish": ("📢 Рассылка", "📢 Broadcast"),
    "👆 Shu xabar hammaga yuboriladi. Tasdiqlaysizmi?": (
        "👆 Это сообщение будет отправлено всем. Подтверждаете?", "👆 This message will be sent to everyone. Confirm?"),
    "📤 Yuborish boshlandi, tugagach xabar beraman.": (
        "📤 Рассылка началась, сообщу, когда закончится.", "📤 Sending started, I'll tell you when it's done."),
    "✅ Yuborildi: ": ("✅ Отправлено: ", "✅ Sent: "),
    "\n❌ Yetib bormadi: ": ("\n❌ Не доставлено: ", "\n❌ Failed: "),
    # admin panel
    "🛠 Admin panel": ("🛠 Админ-панель", "🛠 Admin panel"),
    "⚙️ Admin panel": ("⚙️ Админ-панель", "⚙️ Admin panel"),
    "⚙️ Sozlamalar": ("⚙️ Настройки", "⚙️ Settings"),
    "➕ Admin qo'shish": ("➕ Добавить админа", "➕ Add admin"),
    "➕ Kanal qo'shish": ("➕ Добавить канал", "➕ Add channel"),
    "👋 Kirish matni": ("👋 Приветствие", "👋 Welcome text"),
    "👮 Adminlar": ("👮 Админы", "👮 Admins"),
    "👥 Foydalanuvchilar: ": ("👥 Пользователи: ", "👥 Users: "),
    "\n🛠 Adminlar: ": ("\n🛠 Админы: ", "\n🛠 Admins: "),
    # qidiruv / yuklash (video bot va yangi bot uchun umumiy)
    "⏳ Yuklanmoqda...": ("⏳ Загружаю...", "⏳ Downloading..."),
    "🔎 Qidirilmoqda...": ("🔎 Ищу...", "🔎 Searching..."),
    "Natija topilmadi. Boshqa so'z bilan urinib ko'ring.": (
        "Ничего не найдено. Попробуйте другие слова.", "No results. Try different words."),
    "Qidiruv eskirgan, qaytadan yozing.": ("Поиск устарел, напишите заново.", "This search has expired, please search again."),
    "Yuklanmoqda...": ("Загружаю...", "Downloading..."),
    "Yuklab bo'lmadi. Havola ochiq (public) ekanini tekshiring.": (
        "Не удалось загрузить. Проверьте, что ссылка открыта (public).", "Couldn't download. Check that the link is public."),
    "Audioni yuklab bo'lmadi (hajmi katta bo'lishi mumkin).": (
        "Не удалось загрузить аудио (возможно, файл слишком большой).", "Couldn't download the audio (it may be too large)."),
    "Qo'shiq nomini yozing, masalan: lofi chill": ("Напишите название песни, например: lofi chill", "Send a song name, e.g. lofi chill"),
    ("Salom! 👋\n\n"
     "🎬 Video yuklash: Instagram, YouTube, TikTok va boshqa havolani yuboring.\n"
     "🎵 Qo'shiq qidirish: shunchaki qo'shiq nomini yozing\n\n"
     "⚠️ Faqat o'zingizga tegishli yoki yuklashga ruxsat berilgan kontentni "
     "yuklang. Musiqa qidiruvi faqat Creative Commons litsenziyali "
     "audiolarni topadi."): (
        "Привет! 👋\n\n🎬 Скачать видео: отправьте ссылку Instagram, YouTube, TikTok и др.\n"
        "🎵 Поиск песен: просто напишите название\n\n"
        "⚠️ Скачивайте только свой контент или тот, который разрешено скачивать. "
        "Поиск музыки находит только аудио с лицензией Creative Commons.",
        "Hello! 👋\n\n🎬 Download videos: send an Instagram, YouTube, TikTok or other link.\n"
        "🎵 Find songs: just type the song name\n\n"
        "⚠️ Only download content that is yours or that you are allowed to download. "
        "Music search only finds Creative Commons licensed audio."),
    # kitob / kino / boshqalar (eng ko'p uchraydiganlar)
    "🔤 Fontlar katalogi": ("🔤 Каталог шрифтов", "🔤 Font catalogue"),
    "Kategoriyani tanlang:": ("Выберите категорию:", "Choose a category:"),
}
