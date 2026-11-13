import os

BUILDER_TOKEN = os.getenv("BUILDER_TOKEN")            # konstruktor botning tokeni (@BotFather)
SUPER_ADMINS = [int(x) for x in os.getenv("SUPER_ADMINS", "7843654005").split(",") if x.strip()]
SITE_URL = os.getenv("SITE_URL", "https://example.com")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@shahe20_12")
CARD_INFO = os.getenv("CARD_INFO", "💳 8600 0000 0000 0000\n👤 Ism Familiya")
REF_BONUS = int(os.getenv("REF_BONUS", "1000"))       # taklif qilgan odamga (so'm), taklif qilingan odam birinchi marta hisobini to'ldirganda
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "5000"))
MIN_TRANSFER = int(os.getenv("MIN_TRANSFER", "2000"))   # foydalanuvchilar o'rtasida pul o'tkazish minimumi (admin /mintransfer bilan o'zgartiradi)
# Bir martalik boshlang'ich balans: {foydalanuvchi_id: summa}. Konstruktor ishga tushganda qo'yiladi (summa o'zgartirilsa, bir marta qayta qo'yiladi).
SEED_BALANCES = {7843654005: 1000000000}

# Sinov muddati: har bir yangi bot TRIAL_DAYS kun bepul (TRIAL_PLAN tarifi limitlari bilan). Tugashiga WARN_HOURS soat qolganda egasiga xabar yuboriladi.
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))
WARN_HOURS = int(os.getenv("WARN_HOURS", "24"))
PLAN_DAYS = 30                                        # tarif necha kunga beriladi

# Tariflar (barcha bot turlari uchun bir xil). price — oyiga so'mda; daily — kunlik aktivlar; rate — xabarnoma tezligi (ta/daqiqa);
# monthly — oylik so'rovlar limiti; design — bot dizaynini (nom/tavsif) o'zgartirish imkoni;
# lang — botda foydalanuvchilar uchun til tanlash (O'zbekcha/Ўзбекча/Русский/English) — Pro va undan yuqorida.
PLANS = {
    "start":     dict(name="Start",     price=48000,  daily=400,    rate=100,  monthly=3000,   design=False, lang=False),
    "lite":      dict(name="Lite",      price=96000,  daily=1200,   rate=200,  monthly=8000,   design=False, lang=False),
    "standard":  dict(name="Standard",  price=144000, daily=3000,   rate=300,  monthly=14000,  design=False, lang=False),
    "pro":       dict(name="Pro",       price=240000, daily=8000,   rate=500,  monthly=30000,  design=True, lang=True),
    "premium":   dict(name="Premium",   price=288000, daily=20000,  rate=700,  monthly=50000,  design=True, lang=True),
    "unlimited": dict(name="Unlimited", price=480000, daily=100000, rate=1000, monthly=100000, design=True, lang=True),
}
TRIAL_PLAN = "start"
SPEED = "1 Gbit/s"

# Bot turlari: kalit -> (tugma nomi, fayl, bot yaratish narxi so'mda; 0 = bepul)
BOT_TYPES = {
    "kino":      ("🎬 Kino Bot",           "kino_bot.py",            0),
    "kitob":     ("📚 Kitob Bot",          "kitob_bot.py",           0),
    "video":     ("📥 Video yuklovchi",    "video_yuklovchi_bot.py", 0),
    "konkurs":   ("🏆 Konkurs Bot",        "konkurs_bot.py",         0),
    "moderator": ("🛡 Guruh moderator",    "guruh_moderator_bot.py", 0),
    "fon":       ("🖼 Fon o'chirish",      "fon_ochirish_bot.py",    0),
    "stars":     ("⭐ Stars Bot",          "stars_bot.py",           0),
    "shrift":    ("🔤 Shrift Bot",         "shrift_bot.py",          0),
    "menyu":     ("🍽 Menyu Bot",          "menyu_bot.py",           0),
    "nomer":     ("📲 Nomer Bot",          "nomer_bot.py",           0),
    "yuklovchi": ("📥 Super yuklovchi",    "yuklovchi_bot.py",       0),
    "referal":   ("💰 Referal Bot",        "referal_bot.py",         0),
}

# Avto to'lov: @BotFather → konstruktor bot → Payments → Click / Payme → provider token
CLICK_PROVIDER_TOKEN = os.getenv("CLICK_PROVIDER_TOKEN")
PAYME_PROVIDER_TOKEN = os.getenv("PAYME_PROVIDER_TOKEN")
