import re
import random
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple, List
from telebot import TeleBot
from config import config

logger = logging.getLogger("Miyanji_Utils")

# منطقه زمانی تهران — مرجع تمام تاریخ/ساعت‌های نمایش‌داده‌شده به کاربر و PDF
TEHRAN_TZ = ZoneInfo("Asia/Tehran")

PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

# ====================================================
# ۰. برچسب‌های فارسی وضعیت هر مرحله در معاملات پرداخت مرحله‌ای (Staged Payment)
# این وضعیت‌ها مختص مراحلی هستند که هرکدام لینک پرداخت و تحویل مستقل خودشان
# را دارند (contract["staged_payment"] == True) و با وضعیت‌های قدیمی
# pending/released (مدل «پرداخت یکجا + آزادسازی تدریجی») متفاوتند.
# ====================================================
MS_STATUS_LABELS = {
    "locked": "🔒 در صف - هنوز شروع نشده",
    "awaiting_payment": "💳 در انتظار ارسال فیش واریزی",
    "receipt_submitted": "🕓 فیش ارسال شد - در انتظار تایید ادمین",
    "paid": "🛠 پرداخت‌شده - در حال انجام کار",
    "delivered": "📦 تحویل داده‌شده - در انتظار تایید کارفرما",
    "completed": "✅ تکمیل و تسویه‌شده",
    "pending": "🔒 بلوکه‌شده (پرداخت یکجا)",
    "released": "✅ آزاد شده",
}


def get_ms_status_label(status: str) -> str:
    return MS_STATUS_LABELS.get(str(status), str(status))


def split_amount_by_percentages(total_amount: float, percentages: List[float]) -> List[float]:
    """
    تقسیم مبلغ کل بین چند مرحله بر اساس درصد. جهت جلوگیری از خطای گرد کردن،
    مبلغ تمام مراحل به‌جز آخرین مرحله رند به ۱۰۰۰ تومان می‌شود و باقیمانده
    (برای اینکه جمع دقیقاً برابر مبلغ کل شود) به مرحله آخر اختصاص می‌یابد.
    """
    total_amount = float(total_amount)
    n = len(percentages)
    if n == 0 or total_amount <= 0:
        return []

    amounts = []
    running_total = 0.0
    for i, pct in enumerate(percentages):
        if i == n - 1:
            amounts.append(round(total_amount - running_total, 2))
        else:
            raw = total_amount * (float(pct) / 100.0)
            rounded = round(raw / 1000) * 1000
            amounts.append(float(rounded))
            running_total += rounded
    return amounts


def parse_custom_percentages(text: str) -> Optional[List[float]]:
    """پارس متن درصدهای دلخواه کاربر مثل '20,30,50' یا '20 30 50' → [20,30,50]"""
    clean = fa_to_en_digits(text or "").replace("٪", "").replace("%", "")
    parts = re.split(r"[,\s،]+", clean.strip())
    parts = [p for p in parts if p]
    if not parts:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if any(v <= 0 for v in values):
        return None
    if abs(sum(values) - 100.0) > 0.5:
        return None
    return values


# ====================================================
# ۱. جدول کدگذاری حوزه‌ها جهت بایگانی رسمی
# ====================================================
CATEGORY_CODES = {
    "DEV": "DEV",       # برنامه‌نویسی و IT
    "DESIGN": "DSG",    # طراحی و گرافیک
    "ACADEMIC": "RSH",  # دانشجویی و پژوهشی
    "TEACHING": "EDU",  # آموزشی و تدریس
    "GEN": "GEN"        # عمومی و خدمات
}

# ====================================================
# ۲. شروط و مفاد حقوقی تخصصی هر حوزه (بر اساس قوانین ایران)
# ====================================================
CATEGORY_LEGAL_CLAUSES = {
    "DESIGN": (
        "🏛 *ضوابط طراحی و گرافیک:*\n"
        "• تحویل طبق استاندارد فنی توافق شده \(رزولوشن/فرمت\)\.\n"
        "• انتقال کامل حقوق مادی به کارفرما پس از تسویه نهایی\.\n"
        "• تضمین اصالت اثر و مسئولیت کامل در قبال کپی‌برداری\.\n"
        "• تغییرات جزئی تا ۲ مرحله رایگان؛ تغییر کانسپت مشمول هزینه\."
    ),
    "DEV": (
        "🏛 *ضوابط فناوری اطلاعات:*\n"
        "• تحویل سورس‌کد منسجم و راهنمای استقرار الزامی است\.\n"
        "• تضمین عملکرد در محیط اعلامی و ۷ روز پشتیبانی رفع خطا\.\n"
        "• ممنوعیت استفاده از لایسنس‌های محدودکننده بدون اطلاع\.\n"
        "• تعهد کامل به حفظ محرمانگی داده‌ها و اسرار تجاری\."
    ),
    "ACADEMIC": (
        "🏛 *ضوابط پژوهشی و آموزشی:*\n"
        "• رعایت اصل عدم سرقت ادبی و ارائه گزارش سلامت متن\.\n"
        "• تحویل طبق جدول زمانی؛ تاخیر مشمول جریمه قراردادی\.\n"
        "• ارائه فایل‌های محاسباتی و داده‌های خام در صورت درخواست\."
    ),
    "TEACHING": (
        "🏛 *ضوابط تدریس آنلاین:*\n"
        "• پوشش کامل سرفصل‌ها در زمان‌بندی مشخص شده\.\n"
        "• ممنوعیت ضبط و انتشار بدون مجوز کتبی طرفین\."
    ),
    "GEN": (
        "🏛 *مفاد عمومی خدمات:*\n"
        "• انجام موضوع قرارداد با بالاترین کیفیت و حسن نیت\.\n"
        "• مهلت بررسی ۴۸ ساعته کارفرما پس از هر مرحله تحویل\."
    )
}

# ====================================================
# ۳.۱ اعتبارسنجی و مدیریت حساب‌های بانکی (کارت و شبا)
# ====================================================

IRAN_BANKS_BIN = {
    "603799": ("بانک ملی ایران", "🏛"),
    "589210": ("بانک سپه", "⚓️"),
    "627353": ("بانک تجارت", "💎"),
    "628023": ("بانک مسکن", "🏠"),
    "603770": ("بانک کشاورزی", "🌾"),
    "627412": ("بانک اقتصاد نوین", "🪙"),
    "627488": ("بانک کارآفرین", "💼"),
    "621986": ("بانک سامان", "🟦"),
    "639346": ("بانک پاسارگاد", "🛡"),
    "639607": ("بانک سرمایه", "💰"),
    "502229": ("بانک پاسارگاد", "🛡"),
    "502908": ("بانک توسعه تعاون", "🤝"),
    "627648": ("بانک توسعه صادرات", "🌍"),
    "627961": ("بانک صنعت و معدن", "🏭"),
    "639347": ("بانک پاسارگاد", "🛡"),
    "502806": ("بانک شهر", "🏙"),
    "502938": ("بانک دی", "☀️"),
    "606373": ("بانک قرض‌الحسنه مهر ایران", "☀️"),
    "639599": ("بانک قوامین", "🚔"),
    "504172": ("بانک رسالت", "⚜️"),
    "505410": ("بانک کوثر", "💎"),
    "505801": ("بانک کوثر", "💎"),
    "507677": ("موسسه ملل", "🤝"),
    "606256": ("موسسه ملل", "🤝"),
    "636214": ("بانک آینده", "🔮"),
    "636949": ("بانک حکمت ایرانیان", "🛡"),
    "585983": ("بانک تجارت", "💎"),
    "505957": ("بلو بانک", "💙"),
    "505232": ("بانک ایران زمین", "🌍"),
    "603769": ("بانک صادرات ایران", "💎"),
    "610433": ("بانک ملت", "🔴"),
    "991975": ("بانک ملی ایران", "🏛"),
}

def validate_card_luhn(card_number: str) -> bool:
    """الگوریتم لون برای اعتبارسنجی شماره کارت"""
    if not card_number or len(card_number) != 16 or not card_number.isdigit():
        return False
    
    digits = [int(d) for d in card_number]
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    
    total = sum(odd_digits)
    for d in even_digits:
        doubled = d * 2
        total += doubled if doubled < 10 else doubled - 9
        
    return total % 10 == 0

def get_bank_info(card_number: str) -> Tuple[str, str]:
    """تشخیص نام و ایموجی بانک از روی ۶ رقم اول کارت"""
    if not card_number or len(card_number) < 6:
        return "بانک نامشخص", "🏦"
    
    bin_code = card_number[:6]
    return IRAN_BANKS_BIN.get(bin_code, ("بانک نامشخص", "🏦"))

def validate_iranian_sheba(sheba: str) -> bool:
    """
    اعتبارسنجی ساختاری شماره شبا (ISO 13616)
    ورودی باید با IR شروع شود و ۲۶ کاراکتر باشد
    """
    sheba = sheba.strip().upper()
    if not re.fullmatch(r"IR\d{24}", sheba):
        return False
    
    # الگوریتم باقی‌مانده ۹۷
    # جابجایی ۴ کاراکتر اول به انتها
    rearranged = sheba[4:] + sheba[:4]
    
    # تبدیل حروف به اعداد (A=10, B=11, ..., R=27, I=18)
    numeric_str = ""
    for char in rearranged:
        if char.isdigit():
            numeric_str += char
        else:
            numeric_str += str(ord(char) - ord('A') + 10)
            
    return int(numeric_str) % 97 == 1

def sanitize_bank_input(text: str) -> str:
    """پاکسازی فوق‌حرفه‌ای ورودی بانک (تبدیل اعداد، حذف فواصل و کاراکترهای مزاحم)"""
    if not text:
        return ""
    # تبدیل اعداد فارسی/عربی به انگلیسی
    text = fa_to_en_digits(text).strip().upper()
    
    # اگر شبا باشد (حاوی IR یا طول ۲۴/۲۶ عددی)
    if "IR" in text or (len(re.sub(r'\D', '', text)) >= 24):
        # فقط حروف A-Z و اعداد را نگه دار
        clean = re.sub(r'[^A-Z0-9]', '', text)
        return clean
    
    # اگر شماره کارت باشد (یا فرض بر آن باشد)
    # فقط اعداد را استخراج کن
    digits_only = re.sub(r'\D', '', text)
    return digits_only

# ====================================================
# ۳. توابع کمکی تبدیل اعداد، زمان و فرمت‌دهی
# ====================================================

def fa_to_en_digits(text: str) -> str:
    """تبدیل اعداد فارسی و عربی به اعداد انگلیسی"""
    if not text:
        return ""
    fa_digits = "۰۱۲۳۴۵۶۷۸۹"
    ar_digits = "٠١٢٣٤٥٦٧٨٩"
    en_digits = "0123456789"
    
    translation_table = str.maketrans(fa_digits + ar_digits, en_digits * 2)
    return str(text).translate(translation_table)

def parse_amount_flexible(text: str) -> Optional[float]:
    """
    پارس مبلغ با هر فرمت ممکن:
    - اعداد فارسی/عربی → انگلیسی
    - کاما، فاصله، نقطه هزارگان (۱،۰۰۰،۰۰۰ یا 1,000,000 یا 1 000 000) حذف می‌شوند
    - کلمات «تومان»، «ریال»، «تومن» حذف می‌شوند
    - اگر عدد معتبر و مثبت بود برمی‌گردد وگرنه None
    """
    if not text:
        return None
    cleaned = fa_to_en_digits(str(text).strip())
    # حذف واحد پولی
    for unit in ["تومان", "ریال", "تومن", "Toman", "toman"]:
        cleaned = cleaned.replace(unit, "")
    # حذف جداکننده‌های هزارگان (کاما، نقطه به‌عنوان جداکننده، فاصله، زیرخط)
    # ابتدا باید نقطه‌ای که جداکننده هزارگان است را از نقطه اعشار تشخیص دهیم.
    cleaned = re.sub(r"[،\s_]", "", cleaned)  # کاما فارسی، فاصله، زیرخط
    cleaned = re.sub(r",", "", cleaned)         # کاما انگلیسی
    # نقطه:
    # - اگر عدد کامل به‌صورت گروه‌های ۳رقمی جدا‌شده با نقطه باشد (مثل 10.000.000
    #   یا 8.000) → همه نقطه‌ها جداکننده هزارگانند و حذف می‌شوند (پشتیبانی از
    #   چند نقطه پشت‌سرهم، نه فقط یک نقطه).
    # - در غیر این صورت، اگر فقط یک نقطه وجود دارد و دقیقاً ۳ رقم بعد از آن
    #   آمده، همچنان جداکننده هزارگان در نظر گرفته می‌شود (مبالغ تومانی اعشار
    #   ندارند)؛ وگرنه نقطه به‌عنوان اعشار حفظ می‌شود.
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    elif cleaned.count(".") == 1:
        left, right = cleaned.split(".")
        if left.isdigit() and right.isdigit() and len(right) == 3:
            cleaned = left + right
    cleaned = cleaned.strip()
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def gregorian_to_jalali(g_y: int, g_m: int, g_d: int) -> Tuple[int, int, int]:
    """تبدیل دقیق تاریخ میلادی به شمسی (روز/ماه/سال) بدون نیازمندی به پکیج خارجی"""
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if (g_y % 4 == 0 and g_y % 100 != 0) or (g_y % 400 == 0):
        g_days_in_month[1] = 29

    gy = g_y - 1600
    gm = g_m - 1
    gd = g_d - 1

    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm):
        g_day_no += g_days_in_month[i]
    g_day_no += gd

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    jm, jd = 1, j_day_no + 1
    for i in range(12):
        if jd <= j_days_in_month[i]:
            jm = i + 1
            break
        jd -= j_days_in_month[i]

    return jy, jm, jd


def get_jalali_year_month() -> str:
    """سال و ماه شمسی دو رقمی فعلی، بر اساس ساعت لحظه‌ای تهران"""
    now_tehran = datetime.now(TEHRAN_TZ)
    jy, jm, _ = gregorian_to_jalali(now_tehran.year, now_tehran.month, now_tehran.day)
    return f"{jy % 100:02d}{jm:02d}"

def generate_archive_contract_id(category: str) -> str:
    """تولید کد یکتا و حقوقی بایگانی (نمونه: DSG-0508-8492)"""
    prefix = CATEGORY_CODES.get(category, "GEN")
    date_code = get_jalali_year_month()
    rand_code = random.randint(1000, 9999)
    return f"{prefix}-{date_code}-{rand_code}"

def parse_single_message_contract(text: str) -> Optional[Dict[str, Any]]:
    """پارس کردن فرم متنی یکجا با پشتیبانی از اعداد فارسی و فرمت‌های مختلف"""
    try:
        if not text:
            return None

        clean_text = fa_to_en_digits(text)

        parsed_data = {
            "category": "GEN",
            "role": "employer",
            "title": "",
            "amount": 0.0,
            "deadline": 1,
            "milestones": [],
            "description": ""
        }

        # ۱. استخراج نقش
        if "مجری" in clean_text or "فروشنده" in clean_text or "پیمانکار" in clean_text:
            parsed_data["role"] = "freelancer"
        else:
            parsed_data["role"] = "employer"

        # ۲. استخراج عنوان
        title_match = re.search(r"عنوان:\s*(.+)", clean_text)
        if title_match:
            parsed_data["title"] = title_match.group(1).strip()

        # ۳. استخراج مبلغ کل — با پشتیبانی از هر فرمتی (کاما، فاصله، اعداد فارسی، واحد تومان و ...)
        amount_match = re.search(r"مبلغ کل.*?:\s*([۰-۹\d][۰-۹\d,،.\s]*)", clean_text)
        if amount_match:
            parsed_amount = parse_amount_flexible(amount_match.group(1))
            if parsed_amount:
                parsed_data["amount"] = parsed_amount

        # ۴. استخراج مهلت تحویل (روز)
        deadline_match = re.search(r"مهلت.*:\s*(\d+)", clean_text)
        if deadline_match:
            parsed_data["deadline"] = int(deadline_match.group(1))

        # ۵. استخراج مراحل پرداخت (Milestones)
        milestones_block = re.search(r"مراحل پرداخت:\s*\n((?:[\d]+\..+\n?)+)", clean_text)
        if milestones_block:
            m_lines = milestones_block.group(1).strip().split("\n")
            for line in m_lines:
                m_match = re.search(r"\d+\.\s*(.+):\s*([\d,]+)", line)
                if m_match:
                    m_title = m_match.group(1).strip()
                    m_amt = float(m_match.group(2).replace(",", "").strip())
                    parsed_data["milestones"].append({
                        "title": m_title,
                        "amount": m_amt,
                        "status": "pending"
                    })

        # ۶. استخراج توضیحات
        desc_match = re.search(r"شرح تعهدات:\s*(.+)", clean_text, re.DOTALL)
        if desc_match:
            parsed_data["description"] = desc_match.group(1).strip()

        if not parsed_data["title"] or parsed_data["amount"] <= 0:
            return None

        return parsed_data

    except Exception as e:
        logger.error(f"خطا در پارس کردن متن معامله: {e}")
        return None

def get_commission_percent() -> float:
    """
    درصد کارمزد فعلی سیستم. ابتدا مقدار «زنده» ثبت‌شده توسط ادمین در پنل
    تنظیمات (جدول bot_settings) خوانده می‌شود؛ اگر تنظیم نشده بود، مقدار
    پیش‌فرض از config.py (متغیر محیطی) استفاده می‌شود.
    """
    import database as db  # وارد کردن محلی جهت جلوگیری از وابستگی حلقوی ماژول‌ها
    try:
        val = db.get_bot_setting("commission_rate", None)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return getattr(config, 'COMMISSION_PERCENT', 2.5)


def get_ambassador_share_percent() -> float:
    """
    درصد سهم سفیران از کارمزد پلتفرم. ابتدا مقدار «زنده» ثبت‌شده توسط ادمین
    در پنل تنظیمات خوانده می‌شود؛ اگر تنظیم نشده بود، مقدار پیش‌فرض از
    config.py استفاده می‌شود.
    """
    import database as db  # وارد کردن محلی جهت جلوگیری از وابستگی حلقوی ماژول‌ها
    try:
        val = db.get_bot_setting("affiliate_rate", None)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return getattr(config, "AMBASSADOR_COMMISSION_PERCENT", getattr(config, "AFFILIATE_SHARE_PERCENT", 15.0))


def calculate_commission(amount: float, user_id: Optional[int] = None, payer: str = "freelancer") -> Tuple[float, float, float]:
    """
    محاسبه میزان کارمزد میانجی و مبالغ نهایی برای طرفین.
    payer: "freelancer", "employer", or "shared"
    
    Returns: (commission, freelancer_gets, employer_pays)
    """
    import database as db
    comm_pct = get_commission_percent()
    
    # بررسی تخفیف برای کاربر دعوت شده
    has_discount = False
    if user_id:
        user = db.get_user(user_id)
        if user and user.get("invited_by"):
            has_discount = True
            
    if has_discount:
        # اعمال ۱۰٪ تخفیف روی نرخ کارمزد (مثلاً ۵٪ می‌شود ۴.۵٪)
        comm_pct = comm_pct * 0.9
        
    commission = round((amount * comm_pct) / 100.0)
    
    if payer == "employer":
        # کارفرما کارمزد را می‌دهد: او مبلغ بیشتری می‌پردازد، مجری مبلغ کامل را می‌گیرد
        freelancer_gets = amount
        employer_pays = amount + commission
    elif payer == "shared":
        # ۵۰/۵۰: هر کدام نصف کارمزد را می‌دهند
        half_comm = round(commission / 2.0)
        freelancer_gets = amount - half_comm
        employer_pays = amount + (commission - half_comm) # باقیمانده کارمزد را کارفرما می‌دهد
    else:
        # مجری کارمزد را می‌دهد (پیش‌فرض): کارفرما مبلغ پایه را می‌دهد، از سهم مجری کسر می‌شود
        freelancer_gets = amount - commission
        employer_pays = amount
        
    return commission, freelancer_gets, employer_pays

# ====================================================
# ۴. توابع ساخت لینک سریع، نمایش سند و پنل شیشه‌ای
# ====================================================

def generate_quick_contract_link(bot_username: str, contract_id: str) -> str:
    """تولید لینک اختصاصی امضای سریع قرارداد (Deep Link)"""
    return f"https://t.me/{bot_username}?start=contract_{contract_id}"

def generate_ambassador_link(bot_username: str, ambassador_id: int) -> str:
    """تولید لینک دعوت اختصاصی سفیر جهت انتشار در کانال (Deep Link ردیابی معرف)"""
    return f"https://t.me/{bot_username}?start=ref_{ambassador_id}"


# ====================================================
# ۴.۵ سطوح سفیران (Bronze / Silver / Gold) بر اساس تعداد معاملات موفق ماهانه
# ====================================================

AMBASSADOR_TIERS = [
    # (حداقل تعداد معامله موفق در ماه, کلید, عنوان فارسی, نشان, درصد بونوس ماهانه, توضیح تسویه)
    (51, "gold", "🥇 سفیر طلایی (تاییدشده)", 10.0, "تسویه آنی + پشتیبانی اختصاصی"),
    (16, "silver", "🥈 سفیر نقره‌ای", 5.0, "تسویه سریع (زیر ۶ ساعت)"),
    (0, "bronze", "🥉 سفیر برنزی", 0.0, "تسویه عادی (۲۴ ساعته)"),
]

def get_ambassador_tier(monthly_successful_deals: int) -> Dict[str, Any]:
    """
    تعیین سطح فعلی سفیر بر اساس تعداد معاملات موفقِ معرفی‌شدگانش در ماه جاری:
      برنزی: ۱ تا ۱۵ | نقره‌ای: ۱۶ تا ۵۰ | طلایی: بیش از ۵۰
    خروجی شامل عنوان، نشان و درصد بونوس ماهانه (روی کل درآمد کیف پول همکاری) است.
    """
    n = monthly_successful_deals or 0
    for threshold, key, label, bonus_pct, note in AMBASSADOR_TIERS:
        if n >= threshold:
            return {"key": key, "label": label, "bonus_percent": bonus_pct, "settlement_note": note, "monthly_deals": n}
    return {"key": "bronze", "label": "🥉 سفیر برنزی", "bonus_percent": 0.0, "settlement_note": "تسویه عادی (۲۴ ساعته)", "monthly_deals": n}



# ====================================================
# ۵. بایگانی متمرکز به کانال مدیریت (MJNOTE)
# ====================================================

def escape_html(text: str) -> str:
    """فرار دادن کاراکترهای خاص برای HTML تلگرام"""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def archive_document(bot, file_id, content_type: str = "document", caption: str = "", parse_mode: str = "HTML"):
    """
    بایگانی بی‌صدا و صرفاً سندی در کانال MJNOTE.
    مطابق با منطق جدید: بدون پیام‌های متنی طولانی، فقط فایل.
    """
    import io as _io
    mjnote_id = getattr(config, "MJNOTE", 0)
    if not mjnote_id:
        return False
    try:
        # آرشیو بی‌صدا: فقط فایل با کپشن کوتاه (حداکثر ۴۰۰ کاراکتر از کپشن اصلی)
        clean_caption = caption[:400] if caption else ""
        
        # مدیریت BytesIO (بروزرسانی شده برای پایداری بیشتر)
        actual_file = file_id
        if isinstance(file_id, _io.BytesIO):
            file_id.seek(0)
            actual_file = _io.BytesIO(file_id.read())
            actual_file.name = getattr(file_id, "name", "document.pdf")
            
        if content_type == "photo":
            bot.send_photo(mjnote_id, actual_file, caption=clean_caption, parse_mode=parse_mode)
        elif content_type == "document":
            bot.send_document(mjnote_id, actual_file, caption=clean_caption, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.error(f"❌ Error in archive_document: {e}")
        return False

def notify_archive(
    bot,
    content_type: str = "text",
    text: str = "",
    file_id=None,
    parse_mode: str = "HTML",
    reply_markup=None,
    admin_ids: Optional[List[int]] = None,
) -> None:
    """
    بایگانی در MJNOTE (فقط فایل‌ها و اسناد).
    اگر محتوا متنی باشد، طبق قانون جدید نباید به MJNOTE ارسال شود (به پیوی ادمین‌ها روت می‌شود).
    """
    import io as _io
    mjnote_id = getattr(config, "MJNOTE", 0)
    
    # اگر فایل باشد، به MJNOTE می‌فرستیم
    if mjnote_id and file_id:
        try:
            raw_bytes: Optional[bytes] = None
            file_name: str = "file"
            if isinstance(file_id, _io.BytesIO):
                file_id.seek(0)
                raw_bytes = file_id.read()
                file_name = getattr(file_id, "name", "document.pdf")

            # آرشیو بی‌صدا (فقط فایل با کپشن کوتاه)
            caption = text[:400] if text else ""
            if content_type == "photo":
                if raw_bytes:
                    bot.send_photo(mjnote_id, _io.BytesIO(raw_bytes), caption=caption, parse_mode=parse_mode)
                else:
                    bot.send_photo(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
            elif content_type == "document":
                if raw_bytes:
                    buf = _io.BytesIO(raw_bytes)
                    buf.name = file_name
                    bot.send_document(mjnote_id, buf, caption=caption, parse_mode=parse_mode)
                else:
                    bot.send_document(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
            elif content_type == "video":
                bot.send_video(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
            elif content_type == "voice":
                bot.send_voice(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
            elif content_type == "audio":
                bot.send_audio(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
            elif content_type == "animation":
                bot.send_animation(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
            elif content_type == "video_note":
                bot.send_video_note(mjnote_id, file_id)
            else:
                # اگر نوع دیگری بود به عنوان داکیومنت می‌فرستیم
                try: bot.send_document(mjnote_id, file_id, caption=caption, parse_mode=parse_mode)
                except: pass
        except Exception as e:
            logger.error(f"❌ Error archiving document to MJNOTE: {e}")

    # برای پیام‌های متنی یا اطلاع‌رسانی‌ها، آن‌ها را به پیوی ادمین‌ها می‌فرستیم (نه کانال آرشیو)
    if not file_id or content_type == "text":
        targets = list(admin_ids or getattr(config, "ADMIN_IDS", []))
        for chat_id in targets:
            try:
                bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"❌ Error sending log to admin {chat_id}: {e}")


def credit_ambassador_commission(bot, contract: Dict[str, Any], commission_amount: float, cid: str) -> None:
    """
    در صورتی که خریدار یا فروشنده این معامله توسط سفیری دعوت شده باشند،
    سهم سفیر بر اساس نرخ اختصاصی او محاسبه و واریز می‌شود.
    """
    import database as db

    try:
        if not commission_amount or commission_amount <= 0:
            return

        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")

        ambassador_id = None
        for uid in (buyer_id, seller_id):
            if not uid: continue
            u = db.get_user(uid)
            if u and u.get("invited_by"):
                amb_info = db.get_ambassador(u["invited_by"])
                if amb_info and amb_info.get("is_active"):
                    ambassador_id = u["invited_by"]
                    break

        if not ambassador_id:
            return

        amb_info = db.get_ambassador(ambassador_id)
        share_pct = float(amb_info.get("commission_rate", 30))
        share_amount = round((commission_amount * share_pct) / 100.0)
        
        if share_amount <= 0:
            return

        # واریز به موجودی سفیر و بروزرسانی آمار
        ok = db.update_ambassador_stats(ambassador_id, earnings_delta=share_amount)
        if not ok:
            return

        # ثبت لاگ پورسانت
        db.create_commission_log(
            ambassador_id, 
            cid, 
            float(contract.get("amount", 0)), 
            commission_amount, 
            share_amount
        )

        try:
            bot.send_message(
                ambassador_id,
                f"🎉 **پورسانت جدید!**\n\n"
                f"💰 مبلغ **{format_currency(share_amount)}** تومان بابت معامله هنرجوی شما (`{cid}`) به کیف‌پول سفیران واریز شد.\n"
                f"📈 سطح فعلی شما: **{amb_info.get('tier_level', 'Bronze')}**",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"خطا در پرداخت پورسانت سفیر برای معامله {cid}: {e}")

def generate_promo_banner(ambassador_id: int, channel_name: str = "کانال شما") -> bytes:
    """تولید بنر تبلیغاتی اختصاصی سفیر (استفاده از ReportLab)"""
    from io import BytesIO
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    
    # این یک نمونه ساده است، در واقعیت می‌توان یک عکس زیبا با PIL تولید کرد
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=(600, 400))
    
    p.setFillColorRGB(0.1, 0.1, 0.3)
    p.rect(0, 0, 600, 400, fill=1)
    
    p.setFillColorRGB(1, 1, 1)
    p.setFont("Helvetica-Bold", 24)
    p.drawCentredString(300, 300, "Mianji Escrow Bot")
    
    p.setFont("Helvetica", 16)
    p.drawCentredString(300, 250, f"Special Promotion for: {channel_name}")
    
    bot_username = "MianjiBot" # باید از کانفیگ بیاید
    link = f"t.me/{bot_username}?start=ref_{ambassador_id}"
    p.setFont("Helvetica-Bold", 14)
    p.drawCentredString(300, 200, "Join for Secure Trades:")
    p.setFillColorRGB(0.9, 0.7, 0.1)
    p.drawCentredString(300, 170, link)
    
    p.showPage()
    p.save()
    
    buffer.seek(0)
    return buffer.getvalue()

def generate_draft_preview_text(draft_data: Dict[str, Any]) -> str:
    """تولید متن پیش‌نمایش پیش‌نویس معامله جهت نمایش و ویرایش"""
    title = draft_data.get("title", "ثبت نشده")
    amount = float(draft_data.get("amount", 0))
    deadline = draft_data.get("deadline", 1)
    desc = draft_data.get("description", "ثبت نشده")
    role = draft_data.get("role", "employer")
    role_str = "کارفرما (خریدار)" if role == "employer" else "مجری (فروشنده)"

    payer = draft_data.get("commission_payer", "freelancer")
    comm, net, emp_pays = calculate_commission(amount, payer=payer)

    text = (
        "📋 **پیش‌نویس قرارداد شما:**\n"
        "───────────────────────\n"
        f"👤 **نقش شما:** {role_str}\n"
        f"📌 **عنوان معامله:** {title}\n"
        f"💰 **مبلغ کل:** {amount:,.0f} تومان\n"
        f"⏱ **مهلت تحویل:** {deadline} روز\n"
        f"💳 **کارمزد سامانه (۲.۵٪):** {comm:,.0f} تومان\n"
        f"🎯 **مبلغ خالص مجری:** {net:,.0f} تومان\n"
        f"📝 **شرح تعهدات:**\n{desc}\n"
        "───────────────────────\n"
        "جهت تایید، ویرایش هر بخش یا لغو معامله، از دکمه‌های زیر استفاده کنید:"
    )
    return text

def generate_contract_text(contract: Dict[str, Any], buyer_user: Dict[str, Any] = None, seller_user: Dict[str, Any] = None) -> str:
    """تولید متن رسمی قرارداد همراه با وضعیت امضاها، شماره تماس پاسخگو، سقف ویرایش رایگان و پنل شیشه‌ای"""
    cid = contract.get("contract_id") or contract.get("id", "---")
    category = contract.get("category", "GEN")
    title = contract.get("title", "بدون عنوان")
    
    raw_amount = contract.get("amount")
    try:
        amount = float(raw_amount) if raw_amount is not None else 0.0
    except:
        amount = 0.0

    deadline = contract.get("deadline", 1)
    desc = contract.get("description", "بدون توضیحات تکمیلی")
    milestones = contract.get("milestones", [])
    
    status = contract.get("status", "pending_approval")
    buyer_signed = bool(contract.get("buyer_signed_at") or contract.get("employer_signed_at"))
    seller_signed = bool(contract.get("seller_signed_at") or contract.get("freelancer_signed_at"))
    is_both_signed = buyer_signed and seller_signed
    free_edits = contract.get("free_edits_left", 3)

    # ۱. منطق نگاشت status_badge (وضعیت معامله)
    status_badge = "نامشخص"
    if status in ["pending_approval", "bargaining"]:
        if not buyer_signed and not seller_signed:
            status_badge = "✍️ در انتظار امضای طرفین"
        elif not buyer_signed:
            status_badge = "⏳ در انتظار امضای کارفرما"
        elif not seller_signed:
            status_badge = "⏳ در انتظار امضای مجری"
        else:
            status_badge = "💳 در انتظار واریز وجه توسط کارفرما"
    elif status in ["awaiting_payment", "pending_payment", "receipt_submitted", "awaiting_receipt_approval"]:
        status_badge = "💳 در انتظار واریز وجه توسط کارفرما"
    elif status in ["active", "in_progress"]:
        status_badge = "🛠 در حال انجام پروژه توسط مجری"
    elif status in ["work_submitted", "delivered", "awaiting_edit_price"]:
        status_badge = "📦 فایل تحویل داده شد (در انتظار تأیید/ویرایش)"
    elif status == "in_dispute":
        status_badge = "⚖️ پرونده در حال بررسی در اتاق داوری (میانجی‌روم)"
    elif status == "completed":
        status_badge = "✅ معامله با موفقیت تسویه و تکمیل شد"
    elif status == "cancelled":
        status_badge = "❌ معامله لغو شد"

    # ۲. وضعیت امضا (signature_status)
    if is_both_signed:
        signature_status = "✅ تکمیل شده و معتبر"
    elif buyer_signed:
        signature_status = "⏳ فقط کارفرما امضا کرده است"
    elif seller_signed:
        signature_status = "⏳ فقط مجری امضا کرده است"
    else:
        signature_status = "❌ هیچ‌کدام امضا نکرده‌اند"

    payer = contract.get("commission_payer", "freelancer")
    comm, net, emp_pays = calculate_commission(amount, payer=payer)
    comm_pct = get_commission_percent()
    
    payer_map_fa = {
        "freelancer": "مجری",
        "employer": "کارفرما",
        "shared": "۵۰/۵۰ مشترک"
    }
    payer_fa = payer_map_fa.get(payer, "مجری")

    # استخراج هوشمند شماره تماس پاسخگوی طرفین
    buyer_phone = contract.get("buyer_phone") or (buyer_user.get("phone_number") if buyer_user else None) or "ثبت نشده"
    seller_phone = contract.get("seller_phone") or (seller_user.get("phone_number") if seller_user else None) or "ثبت نشده"

    text = (
        f"🏛 *سند رسمی معامله میانجی*\n"
        f"📑 شناسه: `{cid}`\n\n"
        f"📌 *وضعیت:* {status_badge}\n"
        f"✍️ *امضا:* {signature_status}\n"
        f"───────────────────────\n"
        f"✒️ *ویرایش:* {free_edits} مرحله باقی‌مانده\n\n"
    )

    # بخش جدید: تعاریف و عناوین طرفین (Dynamic Role Titles)
    roles = get_role_titles(category)
    p_role = roles["provider"]
    c_role = roles["client"]

    text += (
        f"📌 *موضوع:* {escape_markdown(title)}\n\n"
        f"💰 *تراکنش مالی*\n"
        f"├ مبلغ کل: {emp_pays:,.0f} تومان\n"
        f"└ سهم مجری: **{net:,.0f} تومان** \({payer_fa}\)\n\n"
        f"👥 *طرفین و زمان‌بندی*\n"
        f"├ {c_role}: `{buyer_phone}`\n"
        f"├ {p_role}: `{seller_phone}`\n"
        f"└ مهلت: {deadline} روز\n"
        "───────────────────────\n"
    )

    if milestones:
        text += "📊 *مراحل پرداخت*\n"
        for idx, m in enumerate(milestones, 1):
            st = get_ms_status_label(m.get("status", "pending"))
            branch = "├" if idx < len(milestones) else "└"
            text += f"{branch} {escape_markdown(m.get('title'))}: {float(m.get('amount', 0)):,.0f} \[{st}\]\n"
        text += "───────────────────────\n\n"

    specific_clauses = CATEGORY_LEGAL_CLAUSES.get(category, CATEGORY_LEGAL_CLAUSES["GEN"])
    
    text += (
        f"📝 *تعهدات:* {escape_markdown(desc)}\n\n"
        f"⚖️ *قوانین اختصاصی:*\n"
        f"{specific_clauses}\n\n"
        "🛡 *شرایط داوری و امنیتی میانجی:*\n"
        "• *تضمین وجوه \(Escrow\):* وجوه تا تایید نهایی در حساب امانت واسط *بلوکه* می‌ماند\.\n"
        "• *استناد قانونی:* این سند طبق ماده ۱۰ قانون مدنی و قوانین تجارت الکترونیک معتبر است\.\n"
        "• *داوری مرضی‌الطرفین:* پلتفرم میانجی به عنوان داور نهایی در صورت بروز اختلاف تعیین شده است\."
    )

    return text

def format_receipt_rejection_msg(contract_id: str, reason: str) -> str:
    """قالب‌بندی پیام اعلام رد فیش واریزی برای کارفرما"""
    return (
        f"❌ **فیش واریزی شما برای قرارداد `{contract_id}` تایید نشد.**\n\n"
        f"📌 **علت رد فیش:**\n{reason}\n\n"
        "💡 لطفاً فیش صحیح را از طریق پنل شیشه‌ای قرارداد مجدداً ارسال کنید."
    )

def format_project_rejection_msg(contract_id: str, reason: str, free_edits_left: int) -> str:
    """قالب‌بندی پیام رد پروژه/پایان کار توسط ادمین به همراه تعداد ویرایش مجانی باقی‌مانده"""
    return (
        f"⚠️ **پروژه تحویلی برای قرارداد `{contract_id}` رد شد.**\n\n"
        f"📌 **دلیل رد/نیاز به اصلاح:**\n{reason}\n\n"
        f"🔄 **تعداد ویرایش رایگان باقی‌مانده:** {free_edits_left} بار\n"
        "لطفاً اصلاحات لازم را انجام داده و مجدداً فایل/پروژه را ارسال کنید."
    )

def convert_to_jalali(date_str: str) -> str:
    """
    تبدیل یک تاریخ/ساعت میلادی (خروجی created_at دیتابیس، معمولاً UTC) به
    تاریخ و ساعت شمسی دقیق به‌وقت تهران، جهت نمایش روی سند/PDF.
    اگر ورودی قابل تفسیر نباشد، لحظه فعلی (اکنون) به‌وقت تهران برگردانده می‌شود.
    """
    dt = None
    if date_str:
        try:
            s = str(date_str).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        # مقادیر بدون منطقه‌زمانی (مثلاً CURRENT_TIMESTAMP در دیتابیس) به‌صورت UTC فرض می‌شوند
        dt = dt.replace(tzinfo=timezone.utc)

    dt_tehran = dt.astimezone(TEHRAN_TZ)
    jy, jm, jd = gregorian_to_jalali(dt_tehran.year, dt_tehran.month, dt_tehran.day)
    month_name = PERSIAN_MONTHS[jm - 1]
    return f"{jd} {month_name} {jy} - ساعت {dt_tehran.strftime('%H:%M:%S')}"


def send_celebration(bot: TeleBot, chat_id: int, text: str = None, reply_markup=None):
    """
    ارسال بازخورد بصری و جشن برای موفقیت در عملیات.
    """
    try:
        # ارسال استیکر/ایموجی متحرک (📝 یا 🎉)
        # اگر متن برای امضای نهایی است از 📝 استفاده می‌کنیم
        icon = "📝" if "امضا" in (text or "") else "🎉"
        
        bot.send_message(chat_id, icon, parse_mode="Markdown")
        if text:
            # ایجاد یک کادر زیبا با نمادها
            fancy_text = (
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"{text}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            bot.send_message(chat_id, fancy_text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"خطا در ارسال پیام جشن به {chat_id}: {e}")


def get_tehran_now_jalali_str() -> str:
    """تاریخ و ساعت لحظه‌ای و دقیق تهران به تقویم شمسی (برای مهر زمان دقیق روی PDF)"""
    return convert_to_jalali(datetime.now(timezone.utc).isoformat())

def format_currency(amount: float) -> str:
    """فرمت‌دهی مبلغ به تومان"""
    return f"{amount:,.0f} تومان"

def escape_markdown(text: str) -> str:
    """فرار کاراکترهای خاص مارک‌داون برای جلوگیری از خطای تلگرام"""
    if not text: return ""
    # لیست کاراکترهایی که در MarkdownV1 (پیش‌فرض telebot) ممکن است مشکل ایجاد کنند
    # توجه: telebot به‌صورت پیش‌فرض از Markdown (نسخه ۱) استفاده می‌کند.
    # کاراکترهای خطرناک: _ * [ `
    return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

def safe_float(val, default=0.0) -> float:
    """تبدیل ایمن انواع ورودی به عدد اعشاری (حذف کاما، فاصله و مدیریت نقاط)"""
    if val is None: return default
    if isinstance(val, (int, float)): return float(val)
    try:
        # پاکسازی رشته
        s = str(val).strip()
        # حذف کاما (جداکننده هزارگان متداول)
        s = s.replace(",", "")
        # حذف فواصل
        s = s.replace(" ", "")
        
        if not s: return default
        
        # مدیریت نقطه (اگر بیش از یک نقطه دارد، احتمالاً جداکننده هزارگان است)
        if s.count(".") > 1:
            s = s.replace(".", "")
            
        return float(s)
    except (ValueError, TypeError):
        return default

def send_admin_alert(bot, text: str, content_type: str = "text", file_id=None, reply_markup=None, parse_mode="HTML"):
    """
    ارسال متمرکز اعلان‌های حساس ادمین.
    ۱. موارد اکشن‌دار (واریز، برداشت، داوری) -> MJ_ADMIN (کانال مدیریت)
    ۲. اسناد و فیش‌ها -> MJNOTE (بایگانی بی‌صدا)
    ۳. سایر موارد -> پیوی ادمین‌ها
    """
    mjnote_id = getattr(config, "MJNOTE", 0)
    admin_channel = getattr(config, "MJ_ADMIN", 0)
    admin_ids = getattr(config, "ADMIN_IDS", [])

    # تشخیص نوع درخواست برای فیلتر کردن کانال مدیریت (Actionable ONLY)
    allowed_keywords = [
        "درخواست واریز", "فیش واریزی", "درخواست برداشت", "درخواست داوری", 
        "ثبت اختلاف", "شارژ کیف پول", "شارژ حساب", "واریز مرحله جدید"
    ]
    is_actionable = any(kw in text for kw in allowed_keywords)

    # ۱. بایگانی در MJNOTE (فقط اگر فایل باشد - بدون متن طولانی)
    if mjnote_id and file_id:
        archive_document(bot, file_id, content_type, caption=text[:400], parse_mode=parse_mode)

    # ۲. تعیین اهداف عملیاتی
    targets = []
    if is_actionable:
        if admin_channel and admin_channel != 0:
            targets.append(admin_channel)
        else:
            targets.extend(admin_ids)
    else:
        # اگر اکشن‌دار نباشد (مثلاً اعلان تکمیل یا لاگ)، فقط به پیوی ادمین‌ها می‌رود
        targets.extend(admin_ids)

    for target in targets:
        try:
            # دکمه‌ها فقط در پیوی (chat_id > 0) ارسال می‌شوند
            actual_markup = reply_markup if target > 0 else None
            # برای کانال متن را کوتاه می‌کنیم
            actual_text = text[:1024] if target < 0 else text

            if file_id and content_type in ["photo", "document"]:
                if content_type == "photo":
                    bot.send_photo(target, file_id, caption=actual_text[:1024], parse_mode=parse_mode, reply_markup=actual_markup)
                else:
                    bot.send_document(target, file_id, caption=actual_text[:1024], parse_mode=parse_mode, reply_markup=actual_markup)
            else:
                bot.send_message(target, actual_text[:4096], parse_mode=parse_mode, reply_markup=actual_markup)
        except Exception as e:
            logger.error(f"❌ Error sending alert to {target}: {e}")

    return True

def check_maintenance() -> bool:
    """بررسی فعال بودن حالت تعمیرات (استفاده از کش سریع)"""
    try:
        import database as db
        # متد get_bot_setting قبلا در database.py بهینه شده و از کش رم استفاده میکند
        mode = db.get_bot_setting("maintenance_mode", "0")
        return str(mode) == "1"
    except Exception:
        return False

import hmac
import hashlib
from urllib.parse import parse_qsl

def verify_telegram_webapp_data(init_data: str, bot_token: str) -> dict:
    """تایید اعتبار داده‌های ارسالی از Telegram Mini App"""
    try:
        vals = dict(parse_qsl(init_data))
        hash_val = vals.pop('hash', None)
        if not hash_val: return None
        
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(vals.items())])
        
        secret_key = hmac.new("WebAppData".encode(), bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash == hash_val:
            import json
            return json.loads(vals.get('user', '{}'))
        return None
    except Exception:
        return None

def is_db_connected() -> bool:
    """بررسی اتصال به دیتابیس"""
    try:
        import database as db
        return db.supabase is not None
    except Exception:
        return False


def get_now_shamsi() -> str:
    """دریافت زمان فعلی به فرمت شمسی (رشته)"""
    return get_tehran_now_jalali_str()


# ====================================================
# ابزارهای مدیریت حساب بانکی و اعتبار سنجی (Banking Utils)
# ====================================================

IRAN_BANKS_BIN = {
    "603799": ("بانک ملی ایران", "🔴"),
    "589210": ("بانک سپه", "🟢"),
    "627648": ("بانک توسعه صادرات", "🔵"),
    "627961": ("بانک صنعت و معدن", "⚪️"),
    "603770": ("بانک کشاورزی", "🌾"),
    "628023": ("بانک مسکن", "🏠"),
    "627760": ("پست بانک ایران", "📮"),
    "621986": ("بانک سامان", "🟦"),
    "639346": ("بانک سینا", "🎨"),
    "639607": ("بانک سرمایه", "💎"),
    "636214": ("بانک آینده", "🟠"),
    "502229": ("بانک پاسارگاد", "🟡"),
    "502908": ("بانک توسعه تعاون", "🤝"),
    "502938": ("بانک دی", "⚪️"),
    "505410": ("بانک کوثر", "💠"),
    "505785": ("بانک ایران زمین", "🌍"),
    "505801": ("بانک پارسیان", "🔱"),
    "606373": ("بانک قرض‌الحسنه مهر ایران", "☀️"),
    "622106": ("بانک پارسیان", "🔱"),
    "627353": ("بانک تجارت", "🔵"),
    "627381": ("بانک سپه (سابق انصار)", "🟢"),
    "627412": ("بانک اقتصاد نوین", "🟣"),
    "627488": ("بانک کارآفرین", "🟢"),
    "628157": ("بانک موسسه اعتباری ملل", "💠"),
    "636949": ("بانک حکمت ایرانیان", "⚪️"),
    "639347": ("بانک پاسارگاد", "🟡"),
    "639599": ("بانک قوامین", "🔵"),
    "991975": ("بانک ملت", "🔴"),
    "610433": ("بانک ملت", "🔴"),
    "585983": ("بانک تجارت", "🔵"),
    "627353": ("بانک تجارت", "🔵"),
    "589463": ("بانک رفاه کارگران", "🟣"),
    "627884": ("بانک پارسیان", "🔱"),
    "639370": ("بانک مهر اقتصاد", "🔵"),
}

def sanitize_bank_input(text: str) -> str:
    """پاکسازی ورودی‌های بانکی (حذف فضا، خط تیره و تبدیل اعداد)"""
    if not text: return ""
    text = fa_to_en_digits(text)
    # حذف هر چیزی بجز اعداد و حروف IR
    return re.sub(r'[^0-9A-Z]', '', text.upper())

def validate_card_luhn(card_number: str) -> bool:
    """اعتبارسنجی شماره کارت ۱۶ رقمی با الگوریتم Luhn"""
    if not card_number or len(card_number) != 16 or not card_number.isdigit():
        return False
    
    res = 0
    for i, digit in enumerate(card_number):
        d = int(digit)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        res += d
    return res % 10 == 0

def validate_iranian_sheba(sheba: str) -> bool:
    """اعتبارسنجی شماره شبا ۲۴ رقمی (ISO 7064 - Mod 97-10)"""
    sheba = sanitize_bank_input(sheba)
    if not sheba.startswith("IR") or len(sheba) != 26:
        return False
    
    # انتقال IR و دو رقم بعد به انتها و تبدیل حروف به عدد (I=18, R=27)
    # IR -> 1827
    reformatted = sheba[4:] + "1827" + sheba[2:4]
    try:
        return int(reformatted) % 97 == 1
    except ValueError:
        return False

def get_bank_info(card_number: str) -> Tuple[str, str]:
    """تشخیص نام بانک و ایموجی از روی ۶ رقم اول کارت (BIN)"""
    bin_6 = card_number[:6]
    return IRAN_BANKS_BIN.get(bin_6, ("بانک نامشخص", "💳"))

def get_contract_status_report(contract: Dict[str, Any]) -> str:
    """ارائه گزارش وضعیت متنی و دقیق معامله بر اساس دیتابیس"""
    status = contract.get("status")
    buyer_signed = bool(contract.get("buyer_signed_at") or contract.get("employer_signed_at"))
    seller_signed = bool(contract.get("seller_signed_at") or contract.get("freelancer_signed_at"))
    
    if status == "pending_approval" or status == "bargaining":
        if not buyer_signed:
            return "⏳ در انتظار امضای کارفرما"
        if not seller_signed:
            return "⏳ در انتظار امضای مجری"
        return "⏳ در انتظار تایید نهایی طرفین"
    
    if status in ["awaiting_payment", "pending_payment", "receipt_submitted", "awaiting_receipt_approval"]:
        return "💳 در انتظار واریز وجه و تایید مالی"
    
    if status in ["active", "in_progress"]:
        return "🚀 در انتظار انجام و تحویل پروژه توسط مجری"
    
    if status in ["work_submitted", "delivered", "awaiting_edit_price"]:
        return "🔍 در انتظار بررسی و تأیید/تسویه توسط کارفرما"
    
    if status == "in_dispute":
        return "⚖️ در حال بررسی در اتاق داوری ادمین"
    
    if status == "completed":
        return "🏁 معامله با موفقیت تکمیل شده است"
    
    if status == "cancelled":
        return "❌ معامله لغو شده است"
    
    return "سایر وضعیت‌ها"

# دیکشنری برای نگهداری آخرین زمان فعالیت کاربران جهت محدودیت نرخ (Rate Limiting)
_user_last_action = {}

def check_rate_limit(user_id: int, action: str, limit_seconds: int = 5) -> bool:
    """بررسی محدودیت نرخ برای جلوگیری از اسپم (پیش‌فرض ۵ ثانیه)"""
    key = f"{user_id}:{action}"
    now = datetime.now().timestamp()
    last_time = _user_last_action.get(key, 0)
    
    if now - last_time < limit_seconds:
        return False
    
    _user_last_action[key] = now
    return True

def get_role_titles(category: str) -> Dict[str, str]:
    """تعیین عناوین حقوقی طرفین بر اساس دسته‌بندی معامله"""
    if "آموزشی" in category or "مشاوره" in category:
        return {"provider": "مدرس/مشاور", "client": "فراگیر/مراجع"}
    elif "دانشگاهی" in category or "پژوهشی" in category:
        return {"provider": "پژوهشگر/ویراستار", "client": "متقاضی"}
    elif "برنامه‌نویسی" in category or "طراحی" in category:
        return {"provider": "پیمانکار/مجری", "client": "سفارش‌دهنده/کارفرما"}
    else:
        return {"provider": "ارائه‌دهنده", "client": "گیرنده خدمت"}

def get_default_commitments(category: str) -> str:
    """دریافت تعهدات قانونی پیش‌فرض بر اساس دسته‌بندی معامله"""
    roles = get_role_titles(category)
    p = roles["provider"]
    c = roles["client"]
    
    base = (
        f"۱. {p} متعهد می‌گردد خدمات موضوع قرارداد را با کیفیت مطلوب و در مهلت مقرر تحویل {c} نماید.\n"
        f"۲. {c} متعهد است حق‌الزحمه را مطابق مراحل پرداخت در سامانه میانجی تودیع نماید.\n"
        f"۳. مالکیت معنوی و حقوق مادی اثر پس از تسویه کامل متعلق به {c} خواهد بود.\n"
        f"۴. سامانه میانجی به‌عنوان داور مرضی‌الطرفین در صورت بروز اختلاف، رای قطعی صادر خواهد کرد.\n"
    )
    
    if "برنامه‌نویسی" in category:
        base += "۵. تحویل سورس‌کد کامل و راهنمای نصب الزامی است.\n"
    elif "طراحی" in category:
        base += "۵. تحویل فایل‌های لایه باز (Source) در انتهای پروژه الزامی است.\n"
    elif "آموزشی" in category:
        base += "۵. حفظ محرمانگی محتوای آموزشی و عدم بازنشر آن توسط فراگیر الزامی است.\n"
        
    return base
