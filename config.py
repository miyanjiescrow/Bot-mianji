import os
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی از فایل .env در محیط توسعه محلی
load_dotenv()

class Config:
    # ----------------------------------------------------
    # تنظیمات اصلی ربات تلگرام
    # ----------------------------------------------------
    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
    BOT_USERNAME = os.getenv("BOT_USERNAME", "mianji_ir_bot").strip()
    
    def _safe_int(val, default=None):
        if not val: return default
        try:
            # Clean possible comments if any (e.g. if user added # comments in env)
            clean_val = str(val).split('#')[0].strip()
            if not clean_val: return default
            return int(clean_val)
        except (ValueError, TypeError):
            return default

    # شناسه ادمین ارشد و مالک سیستم
    _admin_env = os.getenv("ADMIN_ID")
    ADMIN_ID = _safe_int(_admin_env, 1802649782)
    ADMIN_IDS = [ADMIN_ID]
    
    # شناسه مالک اصلی (فقط مالک می‌تواند تنظیمات حساس را تغییر دهد)
    _owner_env = os.getenv("OWNER_ID")
    OWNER_ID = _safe_int(_owner_env, ADMIN_ID)

    # ----------------------------------------------------
    # کانال‌ها و شناسه بایگانی سیستم
    # ----------------------------------------------------
    _archive_env = os.getenv("ARCHIVE_CHANNEL_ID")
    ARCHIVE_CHANNEL_ID = _safe_int(_archive_env, -1003862335372)

    # کانال میانجی اصلی (پست شیشه‌ای)
    _mjchnl_env = os.getenv("MJCHNL")
    MJCHNL = _safe_int(_mjchnl_env)
    
    # کانال مدیریت جهت تایید واریزها و برداشت‌ها
    _mj_admin_env = os.getenv("MJ_ADMIN")
    MJ_ADMIN = _safe_int(_mj_admin_env)
    
    # دعم رو به عقب برای نسخه‌های قدیمی
    _channel_env = os.getenv("MIYANJI_CHANNEL_ID")
    MIYANJI_CHANNEL_ID = _safe_int(_channel_env, (MJCHNL or -1002738838047))

    # کانال بایگانی داخلی مدیریت (MJNOTE)
    # ------------------------------------------------------------------
    # آیدی عددی کانال خصوصی که ربات باید در آن ادمین باشد. از این پس تمام
    # فایل‌های قرارداد، فیش‌های واریزی (به‌صورت نسخه بایگانی)، تحویلی‌های
    # پروژه، درخواست‌های داوری و درخواست‌های کیف‌پول در همین کانال بایگانی
    # می‌شوند و دیگر به پیوی تک‌تک ادمین‌ها ارسال نخواهند شد — به‌جز پیام
    # «تایید واریز» (فیش پرداختی) که چون نیازمند اقدام فوری ادمین است همچنان
    # هم به پیوی و هم به این کانال ارسال می‌شود.
    # اگر این متغیر تنظیم نشود، ربات به‌صورت خودکار به حالت قبل (ارسال به
    # پیوی همه ادمین‌ها) بازمی‌گردد تا هیچ پیامی گم نشود.
    _mjnote_env = os.getenv("MJNOTE")
    MJNOTE = _safe_int(_mjnote_env)

    # ----------------------------------------------------
    # تنظیمات پایگاه داده Supabase
    # ----------------------------------------------------
    _supabase_url_raw = os.getenv("SUPABASE_URL", "")
    _supabase_key_raw = os.getenv("SUPABASE_KEY", "")

    SUPABASE_URL = _supabase_url_raw.strip(' "\'')
    SUPABASE_KEY = _supabase_key_raw.strip(' "\'')
    
    SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "miyanji-docs").strip()
    
    # آدرس اصلی اپلیکیشن برای لینک‌های خارجی
    APP_URL = os.getenv("APP_URL", "https://ais-dev-mbuzgdihdia3eyzieisxcm-497526016591.europe-west3.run.app").strip()

    # ----------------------------------------------------
    # تنظیمات سرور (Render/Heroku require dynamic port)
    # ----------------------------------------------------
    PORT = _safe_int(os.getenv("PORT"), 3000)
    WEB_SERVER_ALIVE_MSG = "Miyanji is running!"

    # ----------------------------------------------------
    # ثوابت مالی و کارمزدها
    # ----------------------------------------------------
    _comm_env = os.getenv("COMMISSION_PERCENT")
    COMMISSION_PERCENT = 10.0
    try:
        if _comm_env:
            COMMISSION_PERCENT = float(str(_comm_env).split('#')[0].strip())
    except:
        pass

    # حد مبلغ برای نیاز به احراز هویت (کد ملی و نام فامیل)
    _threshold_env = os.getenv("IDENTITY_VERIFICATION_THRESHOLD")
    IDENTITY_VERIFICATION_THRESHOLD = _safe_int(_threshold_env, 3000000)

    # شماره کارت میانجی برای شارژ حساب
    INTERMEDIARY_CARD = os.getenv("INTERMEDIARY_CARD", "6037-9975-7534-8211").strip()

    # ----------------------------------------------------
    # پنل سفیران (Ambassadors) — همکاری با کانال‌های مبادله اکانت / فریلنسری
    # ----------------------------------------------------
    # سهم سفیر از «کارمزد پلتفرم» (نه از کل مبلغ معامله) در هر معاملهٔ ای که
    # از طریق لینک اختصاصی او ثبت و تسویه شود.
    _aff_share_env = os.getenv("AFFILIATE_SHARE_PERCENT")
    AFFILIATE_SHARE_PERCENT = 50.0
    try:
        if _aff_share_env:
            AFFILIATE_SHARE_PERCENT = float(str(_aff_share_env).split('#')[0].strip())
    except:
        pass
    AMBASSADOR_COMMISSION_PERCENT = AFFILIATE_SHARE_PERCENT

    # ----------------------------------------------------
    # وضعیت‌هایی که در آن‌ها مبلغ معامله «قفل» شده و لغو یک‌طرفه امکان‌پذیر نیست
    # (لغو در این وضعیت‌ها فقط با تایید هر دو طرف انجام می‌شود)
    # ----------------------------------------------------
    LOCKED_DEAL_STATUSES = [
        "active", "in_progress", "work_submitted", "delivered", "awaiting_edit_price", "in_dispute"
    ]

    # ----------------------------------------------------
    # تنظیمات پیش‌فرض «ویرایش رایگان» (بخش جدید)
    # نفر اول قرارداد (ایجادکننده) هنگام ثبت معامله می‌تواند تعداد دفعات
    # ویرایش/اصلاح رایگان پروژه را از بین این گزینه‌ها انتخاب کند. بعد از
    # اتمام سهمیه، مجری برای هر اصلاح بعدی مبلغ دلخواه خودش را تعیین می‌کند.
    # ----------------------------------------------------
    _default_free_env = os.getenv("DEFAULT_FREE_EDITS")
    DEFAULT_FREE_EDITS = _safe_int(_default_free_env, 3)
    FREE_EDITS_OPTIONS = [0, 1, 2, 3, 5]

    # ----------------------------------------------------
    # کد دسته‌بندی موضوعات قرارداد و پیشوندها
    # ----------------------------------------------------
    STYLE_CODES = {
        "💻 برنامه‌نویسی و توسعه (DEV)": "DEV",
        "🎨 طراحی و گرافیک (DS)": "DS",
        "📝 تولید محتوا و سئو (CNT)": "CNT",
        "🎓 خدمات مشاوره و آموزش (CNS)": "CNS",
        "🌐 خدمات تجاری و عمومی (TRD)": "TRD",
        "📦 سایر موارد (GEN)": "GEN",
    }

    # نگاشت کلیدهای معامله جهت یکپارچه‌سازی با Supabase
    DEAL_KEYS = {
        "ID": "contract_id",
        "TITLE": "title",
        "AMOUNT": "amount",
        "BUYER": "buyer_id",
        "SELLER": "seller_id",
        "STATUS": "status",
        "CATEGORY": "category"
    }

    DEBUG = False

# نمونه‌سازی از کانفیگ
config = Config()

# Export variables for backward compatibility
BOT_TOKEN = config.BOT_TOKEN
BOT_USERNAME = config.BOT_USERNAME
ADMIN_ID = config.ADMIN_ID
ADMIN_IDS = config.ADMIN_IDS
OWNER_ID = config.OWNER_ID
ARCHIVE_CHANNEL_ID = config.ARCHIVE_CHANNEL_ID
MJCHNL = config.MJCHNL
MIYANJI_CHANNEL_ID = config.MIYANJI_CHANNEL_ID
MJNOTE = config.MJNOTE
SUPABASE_URL = config.SUPABASE_URL
SUPABASE_KEY = config.SUPABASE_KEY
SUPABASE_STORAGE_BUCKET = config.SUPABASE_STORAGE_BUCKET
PORT = config.PORT
COMMISSION_PERCENT = config.COMMISSION_PERCENT
IDENTITY_VERIFICATION_THRESHOLD = config.IDENTITY_VERIFICATION_THRESHOLD
AFFILIATE_SHARE_PERCENT = config.AFFILIATE_SHARE_PERCENT
AMBASSADOR_COMMISSION_PERCENT = config.AMBASSADOR_COMMISSION_PERCENT
LOCKED_DEAL_STATUSES = config.LOCKED_DEAL_STATUSES
DEFAULT_FREE_EDITS = config.DEFAULT_FREE_EDITS
FREE_EDITS_OPTIONS = config.FREE_EDITS_OPTIONS
STYLE_CODES = config.STYLE_CODES
DEAL_KEYS = config.DEAL_KEYS
DEBUG = config.DEBUG
MJ_ADMIN = config.MJ_ADMIN
