# Bot konstruktor

## Ishga tushirish
    pip install -r requirements.txt
    export BUILDER_TOKEN="konstruktor bot tokeni"
    export SUPER_ADMINS="7843654005"        # to'lovlarni tasdiqlaydigan adminlar (vergul bilan)
    export CARD_INFO="💳 8600 ... \n👤 Ism"     # ixtiyoriy
    export SITE_URL="https://..."           # ixtiyoriy
    export CLICK_PROVIDER_TOKEN="..."       # ixtiyoriy: avto to'lov (Click)
    export PAYME_PROVIDER_TOKEN="..."       # ixtiyoriy: avto to'lov (Payme)
    export TRIAL_DAYS=7 WARN_HOURS=24       # ixtiyoriy: sinov muddati va ogohlantirish (soat)
    python main.py

## Tuzilma
- main.py — konstruktor (menyu, bot yaratish, botlarim, balans, referal, murojaat, admin), /addbot
- runner.py — har bir botni alohida jarayon sifatida ishga tushiradi
- i18n.py — 4 tildagi matnlar va tariflar matni
- bots/plan_limits.py — tarif limitlari (har bir bot ichida)
- config.py — bot turlari, tariflar (PLANS) (BOT_TYPES ichida narxni so'mda yozing)
- bots/ — 9 ta tayyor bot (token va adminlar BOT_TOKEN / ADMIN_IDS muhitidan olinadi)
- data/<bot_id>/ — har bir yaratilgan botning o'z bazasi va logi

## Menyularni o'zingiz qo'shish (admin)
/menus yoki pastdagi «🧩 Menyularni boshqarish» tugmasi → «➕ Menyu qo'shish» → nom yuboring → mazmunini yuboring (matn, rasm, video, fayl yoki faqat havola). Menyu barcha foydalanuvchilarning pastki tugmalariga qo'shiladi; o'chirish ham shu yerda. Eng ko'pi bilan 30 ta.

## Pul o'tkazish
Foydalanuvchilar «💸 Pul o'tkazish» orqali boshqa foydalanuvchi ID'siga balansdan pul yuboradi (tasdiqlash bilan). Minimal summa 2 000 so'm; admin `/mintransfer SUMMA` bilan o'zgartiradi. Admin `/setbalance ID SUMMA` bilan balansni aniq summaga qo'yadi. config.py → SEED_BALANCES: 7843654005 uchun birinchi ishga tushishda 45 000 000 so'm bir marta qo'yiladi.

## Yangi bot turini .py yuborib qo'shish
Admin konstruktorga .py faylni hujjat sifatida yuboradi (izoh: `Nomi | narxi`, ixtiyoriy) — fayl tekshiriladi, saqlanadi va «➕ Bot yaratish» ro'yxatiga qo'shiladi. Shu nomli fayl qayta yuborilsa, tur yangilanadi va ishlayotgan botlari qayta ishga tushadi. `/types` — ro'yxat, `/deltype KALIT` — o'chirish (botlari yo'q bo'lsa).
Fayl talablari: tokenni `os.getenv("BOT_TOKEN")`, adminlarni `os.getenv("ADMIN_IDS")` (vergul bilan) dan olsin, polling bilan ishlasin; tarif limiti uchun `import plan_limits; plan_limits.install(dp)` qo'shing. Faqat o'zingiz ishongan kodni yuklang — u serverda ishga tushadi.

## 🌐 Botlarda til tanlash (Pro va undan yuqori tariflar)
Pro, Premium va Unlimited tarifidagi HAR QANDAY botda (kino, kitob, video, konkurs, moderator, fon, stars, shrift, menyu, nomer, yuklovchi) foydalanuvchi /start bosganda til tanlaydi: O'zbekcha, Ўзбекча, Русский, English (konstruktordagidek). Istalgan vaqtda /lang bilan o'zgartiradi. Start/Lite/Standard tariflarida bot avvalgidek o'zbekcha (lotin).
- Yoqish: konstruktor botga `PLAN_LANG=1` beradi (config.py → PLANS → `lang=True`; admin panelda «dizayn» tumblerini yoqsa, til ham yoqiladi). Tarif pasaytirilsa bot qayta ishga tushadi va til tanlash o'chadi.
- Ishlashi: `bots/botlang.py` (plan_limits.install orqali o'zi ulanadi — botlar kodiga tegilmaydi). Foydalanuvchiga ketadigan xabar matni, tugmalar va callback javoblari o'giriladi; reply-tugma bosilganda asl matn handlerga uzatiladi.
- Ўзбекча — botning o'zbekcha matnlaridan avtomatik (lotin → kirill). Русский/English — `bots/botlang_tr.py` lug'atidan (kalit — kodidagi matn bilan aynan bir xil). Lug'atda yo'q matn o'zbekcha qoladi: yangi ibora qo'shish uchun `DICT` ga `"matn": ("ruscha", "inglizcha")` qatorini qo'shing.
- O'girilmaydi: admin yozgan matnlar (kirish matni, reklama), kino/qo'shiq nomlari kabi dinamik qismlar, media izohlari, ommaviy xabarlar.
- Super yuklovchi bot matnlari 4 tilda to'liq (bots/yuklovchi_tx.py).

## 📥 Super yuklovchi bot (yangi tur: «yuklovchi»)
Fayllar: bots/yuklovchi_bot.py, yuklovchi_core.py, yuklovchi_tx.py, yuklovchi_web.py. Video yuklovchidagi hamma narsa saqlangan (admin panel, xabar yuborish, kirish matni, video osti matni, majburiy obuna, /music, Local Bot API 2000 MB) va qo'shilgan:
- Instagram (video, reels, rasm, albom, hikoyalar), TikTok (suv belgisiz), Pinterest, Facebook, X, Likee, RuTube.
- YouTube: sifat tanlash (hajmi bilan), Telegramga sig'maydiganlari yashiriladi; MP3 — muqova, ijrochi va nom bilan.
- TikTok videosi ostida «🎵 Musiqasi» tugmasi (bepul).
- Qo'shiq tanish: ovozli xabar/video/audio/dumaloq video (shazamio), ijrochi, matn, muqova; MP3 yuklab olish.
- Qo'shiq nomi bo'yicha qidiruv («➕ Yana» bilan davomi), /top (O'zbekiston, bo'lmasa dunyo), inline qidiruv, guruhlarda 👀.
- Kesh: bir marta yuklangan fayl keyingi safar darrov va bepul (limitga hisoblanmaydi).
- ⭐ Plus (Telegram Stars): cheksiz va reklamasiz. Bepul foydalanuvchiga kunlik limit (standart 10, admin panelda o'zgaradi). /grant ID KUN — qo'lda Plus berish.
- 📺 Reklama: admin panel → «Reklamalar» (xabar + ixtiyoriy tugma), har N-yuklashdan so'ng Plus bo'lmaganlarga.
- 📱 Web admin panel: konstruktor muhitida `WEB_BASE_PORT=9000` (va ixtiyoriy `WEB_PUBLIC_URL=https://host:{port}`) bering — har bot o'z portida (9000+bot_id) ochiladi; havolani botda admin /web bilan oladi.
Sozlash: Instagram hikoyalari/yopiq postlar uchun `IG_COOKIES=/yo'l/cookies.txt`; inline uchun @BotFather → /setinline va /setinlinefeedback (Enabled). ffmpeg kerak (Dockerfile'da bor). Faqat ruxsat berilgan kontentni yuklash uchun foydalanuvchilarga ogohlantirish kirish matnida berilgan.

## 💰 Referal bot (yangi tur: «referal»)
Fayl: bots/referal_bot.py (baza: referal.db, har bot o'z papkasida). Tarif limitlari (kunlik aktiv, oylik so'rov, tezlik, til) boshqa turlardagidek plan_limits orqali ishlaydi — alohida tarif kerak emas, 7 kunlik sinov va tariflar ro'yxati hamma tur uchun umumiy.
- Foydalanuvchi: 💰 Pul ishlash (shaxsiy havola, 2-daraja), 🎁 Kunlik bonus (har 7-kuni ×2), 🎟 Promokod, 🏆 Reyting (hafta/hamma vaqt), 💵 Hisobim, 💳 Pul yechish (karta/hamyon), 🧾 To'lovlar tarixi, ☎️ Murojaat, 📱 Kabinet (Mini ilova).
- Himoya: obuna majburiy; telefon tekshiruvi (bitta raqam = bitta akkaunt, ruxsat etilgan kodlar, masalan +998); yechish uchun minimal referal soni; qurilma tekshiruvi 3 darajada (Hammaga ruxsat / 1 qurilma = 1 referal / qattiq blok).
- Egasi (admin panel /admin): sozlamalar, to'lov tizimlari, yechish so'rovlarini tasdiqlash/rad etish, to'lovlar kanali, statistika, foydalanuvchini boshqarish (balans, blok), promokodlar, murojaatlarga javob, telefonlar CSV, xabar yuborish, hamma matn va tugmalarni tahrirlash.
- Mini ilova va qurilma tekshiruvi uchun https manzil kerak: admin panel → Sozlamalar → «Web manzil» (yoki muhitda WEB_URL) va konstruktor muhitida WEB_BASE_PORT. Web manzil berilmasa Kabinet matn ko'rinishida chiqadi va qurilma tekshiruvi o'tkazib yuboriladi (telefon va obuna tekshiruvi ishlayveradi).
- Mukofot do'st barcha tekshiruvdan o'tgandan keyin BIR marta beriladi.

## 🟢/🔴 Tarif tizimini yoqish/o'chirish
Admin panel (/admin) → «Tarif tizimi» tugmasi (yoki 💰 Tariflar ichida). O'chiq bo'lsa: hamma botlar bepul, limitsiz (kunlik/oylik/tezlik), muddatsiz va dizayn/til imkoniyatlari bilan ishlaydi; muddat tugashi, ogohlantirishlar va tarif sotib olish to'xtaydi; botlar kartasida «🆓 Bepul» ko'rinadi. Qayta yoqilganda bot muddatlari o'chiq turgan vaqtga uzaytiriladi (hech kim yo'qotmaydi). Holat bazada saqlanadi (settings: tariffs_on).

## Til
/start bosilganda 4 tilda tanlov chiqadi: O'zbekcha, Ўзбекча, Русский, English. Tanlovdan so'ng xush kelibsiz xabari, menyu va tariflar ro'yxati ko'rsatiladi.
Matnlar i18n.py da (kirill o'zbekcha lotin matndan avtomatik o'giriladi).

## Tariflar (barcha bot turlari uchun bir xil — config.py → PLANS)
Start 48 000 · Lite 96 000 · Standard 144 000 · Pro 240 000 · Premium 288 000 · Unlimited 480 000 so'm / 30 kun.
Har tarifda: kunlik aktivlar, xabarnoma tezligi (ta/daqiqa), oylik so'rovlar limiti; Pro va yuqorida — bot dizaynini (nom, tavsif) o'zgartirish.
- Limitlar har bir bot ichida `bots/plan_limits.py` orqali majburlanadi (muhit: PLAN_DAILY, PLAN_MONTHLY, PLAN_RATE). Egasi (admin) limitga kirmaydi.
  Limit tugasa, yangi foydalanuvchilar/so'rovlar vaqtincha bloklanadi va egasiga kuniga bir marta xabar boradi.
- Xabarnoma tezligi: boshqa foydalanuvchilarga ketadigan xabarlar daqiqasiga tarif limitiga tushiriladi (javoblar kechikmaydi).
- «🤖 Botlarim» → bot → «📦 Tarifni tanlash» balansdan yechadi va botni yangi limitlar bilan qayta ishga tushiradi.
- Admin: /setplan BOT_ID TARIF, /extend BOT_ID KUN.

## Sinov muddati
- Har bir yangi bot {TRIAL_DAYS} (7) kun bepul, «Start» tarifi limitlari bilan ishlaydi. Tugashiga 24 soat qolganda egasiga avtomatik xabar boradi.
- Muddat tugagach bot avtomatik to'xtaydi (ma'lumotlar saqlanadi), egasiga xabar yuboriladi. Davom ettirish uchun tarif tanlanadi.
- Referal bonusi taklif qilingan odam birinchi marta hisobini to'ldirganda beriladi.
- Fon o'chirish boti: tayyor API o'rnatilmaydi. Bot egasi /admin → «🔑 remove.bg kaliti» (yoki /key) orqali remove.bg/api dan olgan kalitini botga yuboradi, bot uni tekshirib ulaydi (kalit shu botning bazasida saqlanadi, yuborilgan xabar o'chiriladi). Konstruktor muhitidagi REMOVEBG_KEY bolalarga o'tmaydi. Video bot uchun ffmpeg kerak.

## 📲 Nomer Bot (virtual raqamlar)
Fayllar: bots/nomer_bot.py, nomer_db.py, nomer_providers.py, nomer_texts.py. Bot egasi (admin) provayder kalitlarini botning o'zida kiritadi — kalitlar shu botning bazasida saqlanadi, boshqa botlarga ko'rinmaydi:
    /server 1 5sim TOKEN
    /server 2 activate URL KEY tg
    /server 1 off
So'ng /admin → Narxlar → «Server kursi», «Hozir yangilash», «Avto yangilash ON». Konstruktor muhitidagi SERVER*_ o'zgaruvchilari bolalarga o'tmaydi.

## 🏠 Bosh menyu (v3.7)
    ➕ Bot yaratish
    🤖 Botlarim        | 👤 Hisobim
    🗣 Referal         | 💳 Hisob to'ldirish
    📘 Qo'llanma       | ✉️ Murojaat
    🛠 Maxsus bot
    (admin qo'shgan menyular)
    👑 Admin panel      (faqat adminlarga)
- **Hisobim** ichida: 💸 Pul o'tkazish, 💼 Daromad, 📦 Tariflar, 🌐 Sayt (avval pastki menyuda edi).
- **Maxsus bot**: foydalanuvchi o'ziga kerakli botni tasvirlaydi, buyurtma adminlarga boradi, javob `/reply ID MATN`.
- **Admin panel** (`/admin` ham ochadi): 🧩 menyularni boshqarish, ✏️ tugma nomlari, 📝 matnlarni tahrirlash, 🌐 til tanlash
  (nom va matnlar tanlangan til uchun saqlanadi), 👤 adminlar (qo'shish/o'chirish — faqat .env dagi asosiy adminlar).
- Admin o'zgartirishlari bazada (`overrides`, `settings`) saqlanadi, qayta ishga tushganda yo'qolmaydi.

## v3.8 — yangi imkoniyatlar
- 📢 **Xabar yuborish** (Admin panel): hammaga / bot egalariga / to'lov qilganlarga / tanlangan til foydalanuvchilariga; matn, rasm, video, fayl; yakunda yetkazildi/yetmadi hisoboti.
- 🔒 **Majburiy obuna**: kanallar qo'shiladi (bot ularda admin bo'lishi shart); a'zo bo'lmaguncha foydalanuvchi botdan foydalana olmaydi, «✅ Tekshirish» tugmasi bor. Adminlar to'smaydi.
- 🎁 **Promokod**: admin `BONUS LIMIT [KOD]` bilan yaratadi (kod bo'lmasa avtomatik), karta ko'rinishi: kod / bonus / limit used/limit / «Bu promokod tugadi!». Foydalanuvchi: Hisobim → 🎁 Promokod yoki `/promo KOD`. Har kim bir kodni faqat bir marta ishlata oladi.
- 💰 **Tariflar**: Admin paneldan narx, kunlik/oylik limit, tezlik, dizayn o'zgartiriladi (bazada saqlanadi).
- `SEED_BALANCES` summasi o'zgartirilsa, shu summa bir marta qayta qo'yiladi.
