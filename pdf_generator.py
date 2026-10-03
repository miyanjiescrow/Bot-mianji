import io
import os
import re
import logging
import sys
import traceback
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm

from config import config
import utils

logger = logging.getLogger("Miyanji_PDF")

# لاگ مشخصات سیستم برای عیب‌یابی در محیط رندر
logger.info(f"PDF System Info: Python {sys.version} | Platform: {sys.platform}")
try:
    import reportlab
    logger.info(f"ReportLab version: {reportlab.Version}")
except ImportError:
    logger.error("ReportLab is NOT installed!")

# ====================================================
# ۱. تنظیمات بصری و رنگ‌بندی (Modern Minimalist)
# ====================================================
COLORS = {
    "primary": colors.HexColor("#0F172A"),   # Deep Navy
    "secondary": colors.HexColor("#F8FAFC"), # Light Slate Background
    "bg_main": colors.HexColor("#F8FAFC"),   # Bone/Light Gray Page Background
    "bg_card": colors.HexColor("#F1F5F9"),   # Card Background
    "accent_cyan": colors.HexColor("#0EA5E9"),
    "accent_gold": colors.HexColor("#D97706"),
    "text_main": colors.HexColor("#334155"),
    "text_light": colors.HexColor("#64748b"),
    "border": colors.HexColor("#E2E8F0"),
    "white": colors.white,
    "success": colors.HexColor("#10B981"),
    "warning": colors.HexColor("#F59E0B"),
    "danger": colors.HexColor("#EF4444"),
}

# ====================================================
# ۲. بارگذاری فونت و ابزارهای RTL
# ====================================================
# مسیر فونت را به صورت مطلق و دقیق تنظیم می‌کنیم
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
Vazir_Regular = os.path.join(FONT_DIR, "Vazirmatn-Regular.ttf")
Vazir_Bold = os.path.join(FONT_DIR, "Vazirmatn-Bold.ttf")

# فونت‌های سیستمی به عنوان فال‌بک نهایی
PERSIAN_FONT = "Helvetica"
PERSIAN_FONT_BOLD = "Helvetica-Bold"

import httpx

def download_font_if_missing(file_path: str, url: str):
    """دانلود فونت در صورت عدم وجود در مسیر مشخص شده"""
    if not os.path.exists(file_path):
        try:
            logger.info(f"Font {os.path.basename(file_path)} missing. Downloading...")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            response = httpx.get(url, follow_redirects=True)
            if response.status_code == 200:
                with open(file_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Font downloaded successfully: {file_path}")
            else:
                logger.error(f"Failed to download font. Status: {response.status_code}")
        except Exception as e:
            logger.error(f"Error downloading font: {e}")

# لینک‌های مستقیم فونت وزیر
VAZIR_REG_URL = "https://github.com/rastikerdar/vazirmatn/raw/v33.003/fonts/ttf/Vazirmatn-Regular.ttf"
VAZIR_BOLD_URL = "https://github.com/rastikerdar/vazirmatn/raw/v33.003/fonts/ttf/Vazirmatn-Bold.ttf"

# تلاش برای دانلود فونت‌ها در هنگام شروع ماژول
download_font_if_missing(Vazir_Regular, VAZIR_REG_URL)
download_font_if_missing(Vazir_Bold, VAZIR_BOLD_URL)

def register_fonts():
    global PERSIAN_FONT, PERSIAN_FONT_BOLD
    try:
        # ثبت فونت Regular
        if os.path.exists(Vazir_Regular):
            pdfmetrics.registerFont(TTFont("Vazir", Vazir_Regular))
            PERSIAN_FONT = "Vazir"
            logger.info("Vazir Regular font registered successfully.")
        else:
            logger.warning("Vazir Regular font file not found. Using Helvetica as fallback.")
            
        # ثبت فونت Bold
        if os.path.exists(Vazir_Bold):
            pdfmetrics.registerFont(TTFont("Vazir-Bold", Vazir_Bold))
            PERSIAN_FONT_BOLD = "Vazir-Bold"
            logger.info("Vazir Bold font registered successfully.")
        else:
            PERSIAN_FONT_BOLD = PERSIAN_FONT
    except Exception as e:
        logger.error(f"Error registering fonts: {e}")
        print("FONT REGISTRATION ERROR:", traceback.format_exc())
        # بازگشت به فونت‌های پیش‌فرض در صورت خطا
        PERSIAN_FONT = "Helvetica"
        PERSIAN_FONT_BOLD = "Helvetica-Bold"

register_fonts()

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    RESHAPE_AVAILABLE = True
except ImportError:
    try:
        from bidi import get_display
        RESHAPE_AVAILABLE = True
    except ImportError:
        RESHAPE_AVAILABLE = False
        logger.warning("arabic_reshaper or python-bidi not found.")

def reshape_text(text: str) -> str:
    """آماده‌سازی متن برای نمایش صحیح در ReportLab (RTL + Arabic Reshaping)"""
    if not text:
        return ""
    
    # تبدیل به استرینگ و پاکسازی کاراکترهای مخرب احتمالی
    text = str(text)
    
    if not RESHAPE_AVAILABLE:
        return text
    
    try:
        # شکل‌دهی حروف (اتصال حروف فارسی)
        reshaped = arabic_reshaper.reshape(text)
        # الگوریتم دوجهته (RTL)
        bidi_text = get_display(reshaped)
        
        return bidi_text
    except Exception as e:
        logger.error(f"Error in reshape_text for '{text[:20]}...': {e}")
        # در صورت خطا، متن اصلی را برمی‌گردانیم تا کل فرآیند متوقف نشود
        return text

import html

def clean_markdown(text: str) -> str:
    """پاکسازی تگ‌های مارک‌داون و اسکیپ کردن کاراکترهای XML برای جلوگیری از خطای Paragraph"""
    if not text: return ""
    # تبدیل به استرینگ
    text = str(text)
    
    # حذف کاراکترهای کنترلی و غیرمجاز XML که ممکن است باعث کرش ReportLab شوند
    # ما فقط کاراکترهای یونیکد معتبر و کاراکترهای متنی استاندارد را نگه می‌داریم
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\r\t")
    
    # اسکیپ کردن کاراکترهای رزرو شده XML
    text = html.escape(text)
    
    # حذف الگوهای مارک‌داون (بدون اضافه کردن تگ HTML به دلیل تداخل شدید با الگوریتم Bidi در ReportLab)
    # توجه: تگ‌های HTML در متن‌های راست‌به‌چپ (RTL) که توسط get_display ری‌شیپ می‌شوند،
    # باعث به هم ریختن ساختار تگ (مثل <b/> به جای <b>) و کرش Paragraph می‌شوند.
    text = re.sub(r"&lt;b&gt;(.*?)&lt;/b&gt;", r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"`(.*?)`", r"\1", text)
    
    return text

# ====================================================
# ۳. نگاشت وضعیت‌ها به فارسی
# ====================================================
STATUS_MAP = {
    "waiting_signature": "در انتظار امضا",
    "pending_approval": "در انتظار تایید",
    "awaiting_payment": "در انتظار پرداخت",
    "receipt_submitted": "بررسی فیش واریزی",
    "paid": "در حال اجرا",
    "working": "در حال اجرا",
    "delivered": "تحویل داده شده",
    "completed": "تکمیل و تسویه شده",
    "cancelled": "لغو شده",
    "disputed": "در حال داوری",
    "released": "تسویه شده",
}

def get_status_fa(status_en: str) -> str:
    return STATUS_MAP.get(status_en, status_en)

# ====================================================
# ۴. کلاس سفارشی کانواس برای فوتر و شماره صفحه
# ====================================================
class ContractCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self.pages)
        for page in self.pages:
            self.__dict__.update(page)
            self.draw_canvas_extras(page_count)
            super().showPage()
        super().save()

    def draw_canvas_extras(self, page_count):
        self.saveState()
        
        # Footer
        self.setFont(PERSIAN_FONT, 8)
        self.setFillColor(COLORS["text_light"])
        
        # خط فوتر
        self.setStrokeColor(COLORS["border"])
        self.setLineWidth(0.5)
        self.line(20*mm, 15*mm, 190*mm, 15*mm)
        
        # متن فوتر
        footer_left = reshape_text(f"صفحه {self._pageNumber} از {page_count}")
        footer_right = reshape_text("سند الکترونیکی معتبر سامانه میانجی - mianji.io")
        
        self.drawString(20*mm, 10*mm, footer_left)
        self.drawRightString(190*mm, 10*mm, footer_right)
        
        self.restoreState()

# ====================================================
# ۵. تابع اصلی ساخت PDF
# ====================================================
def build_contract_pdf(contract_data: dict, buyer_user: dict = None, seller_user: dict = None) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15*mm,
        leftMargin=15*mm,
        topMargin=20*mm,
        bottomMargin=20*mm
    )

    # استایل‌ها
    styles = getSampleStyleSheet()
    
    def get_para_style(name, fontSize=10, color=COLORS["text_main"], bold=False, align=TA_RIGHT, leading=14):
        return ParagraphStyle(
            name,
            fontName=PERSIAN_FONT_BOLD if bold else PERSIAN_FONT,
            fontSize=fontSize,
            textColor=color,
            alignment=align,
            leading=leading,
        )

    style_title = get_para_style('Title', fontSize=18, bold=True, align=TA_CENTER, leading=22)
    style_h1 = get_para_style('H1', fontSize=12, bold=True, color=COLORS["primary"], leading=20)
    style_body = get_para_style('Body', fontSize=10, leading=16)
    style_label = get_para_style('Label', fontSize=9, color=COLORS["text_light"], leading=12)
    style_value = get_para_style('Value', fontSize=10, bold=True, leading=14)
    style_badge = get_para_style('Badge', fontSize=9, bold=True, color=COLORS["white"], align=TA_CENTER)
    style_audit = get_para_style('Audit', fontSize=8, color=COLORS["text_light"], leading=12)
    style_legal = get_para_style('Legal', fontSize=9, color=COLORS["text_main"], leading=16)

    elements = []

    # --- داده‌ها (با ایمن‌سازی کامل ورودی‌ها) ---
    cid = str(contract_data.get("contract_id") or contract_data.get("id", "---"))
    title = clean_markdown(str(contract_data.get("title") or "بدون عنوان"))
    
    try:
        amount = float(contract_data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0.0
        
    status_en = str(contract_data.get("status") or "waiting_signature")
    status_fa = get_status_fa(status_en)
    category = str(contract_data.get("category") or "GEN")
    
    try:
        deadline = int(contract_data.get("deadline", 1))
    except (ValueError, TypeError):
        deadline = 1
        
    description = clean_markdown(str(contract_data.get("description") or "توضیحی ثبت نشده است."))
    created_at = contract_data.get("created_at")
    date_str = utils.convert_to_jalali(created_at) if created_at else "نامشخص"
    
    comm_payer = str(contract_data.get("commission_payer") or "freelancer")
    comm_pct = utils.get_commission_percent()
    comm, net, emp_pays = utils.calculate_commission(amount, payer=comm_payer)
    
    roles = utils.get_role_titles(category)
    
    # تعیین متن پرداخت‌کننده کارمزد
    payer_map = {
        "freelancer": f"مجری ({roles['provider']})",
        "employer": f"کارفرما ({roles['client']})",
        "shared": "مشترک (۵۰/۵۰)"
    }
    payer_text = payer_map.get(comm_payer, comm_payer)
    
    # اطلاعات کاربران
    if buyer_user is None or seller_user is None:
        try:
            import database as db
            bid = contract_data.get("buyer_id")
            sid = contract_data.get("seller_id")
            if not buyer_user and bid: buyer_user = db.get_user(bid)
            if not seller_user and sid: seller_user = db.get_user(sid)
        except Exception as e:
            logger.error(f"Error fetching users for PDF: {e}")

    def get_party_display(role_type):
        """ترکیب اطلاعات لحظه امضا و اطلاعات جاری کاربر با لاگ دقیق جهت عیب‌یابی"""
        # اولویت با اطلاعات ثبت شده در خود قرارداد (Snapshot)
        c_name = contract_data.get(f"{role_type}_fullname")
        c_nid = contract_data.get(f"{role_type}_national_id")
        c_phone = contract_data.get(f"{role_type}_phone")
        
        u = buyer_user if role_type == "buyer" else seller_user
        
        logger.info(f"🔍 [PDF_USER_DEBUG] Role: {role_type}, Snapshot Name: {c_name}, User Object Present: {u is not None}")

        # اگر اطلاعات لحظه امضا نباشد، از پروفایل جاری استفاده می‌کنیم
        if not c_name and u:
            if u.get("first_name_real") and u.get("last_name_real"):
                c_name = f"{u['first_name_real']} {u['last_name_real']}"
            else:
                c_name = u.get("full_name") or u.get("first_name")
            logger.info(f"   -> Using name from profile: {c_name}")
        
        if not c_nid and u: c_nid = u.get("national_id")
        if not c_phone and u: c_phone = u.get("phone_number")
        
        uid = str(u.get("id") if u else contract_data.get(f"{role_type}_id", "---"))
        ip = str(u.get("registration_ip") if u else "---")

        return {
            "name": clean_markdown(str(c_name or "ناشناس")),
            "phone": str(c_phone or "ثبت نشده"),
            "id": str(c_nid or "---"),
            "uid": uid,
            "ip": ip
        }

    buyer_info = get_party_display("buyer")
    seller_info = get_party_display("seller")

    # --- ۱. هدر (Header) ---
    header_data = [
        [
            Table([
                [Paragraph(reshape_text(f"شناسه بایگانی: #{cid}"), style_label)],
                [Paragraph(reshape_text(date_str), style_label)]
            ], colWidths=[50*mm], style=[('ALIGN', (0,0), (-1,-1), 'LEFT')]),
            
            Paragraph(reshape_text("قرارداد رسمی میانجی"), style_title),
            
            # Badge Status
            Table([
                [Paragraph(reshape_text(status_fa), style_badge)]
            ], colWidths=[35*mm], style=[
                ('BACKGROUND', (0,0), (-1,-1), COLORS["accent_cyan"]),
                ('ROUNDEDCORNERS', [10, 10, 10, 10]),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ])
        ]
    ]
    header_table = Table(header_data, colWidths=[55*mm, 70*mm, 55*mm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (1,0), (1,0), 'CENTER'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 10*mm))

    # --- ۲. اطلاعات اصلی (Contract Title & ID Block) ---
    elements.append(Paragraph(reshape_text("موضوع و کلیات قرارداد"), style_h1))
    elements.append(Spacer(1, 3*mm))
    
    info_table_data = [
        [
            Paragraph(reshape_text(title), style_value),
            Paragraph(reshape_text("عنوان معامله:"), style_label)
        ],
        [
            Paragraph(reshape_text(f"{utils.get_role_titles(category)['provider']} / {utils.get_role_titles(category)['client']}"), style_value),
            Paragraph(reshape_text("طرفین معامله:"), style_label)
        ]
    ]
    info_table = Table(info_table_data, colWidths=[140*mm, 40*mm])
    info_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 8*mm))

    # --- ۳. جزئیات مالی (Financial Details) ---
    elements.append(Paragraph(reshape_text("جزئیات مالی و زمان‌بندی"), style_h1))
    elements.append(Spacer(1, 3*mm))
    
    finance_data = [
        [
            Paragraph(reshape_text(f"{deadline} روز"), style_value),
            Paragraph(reshape_text("مهلت تحویل:"), style_label),
            Paragraph(reshape_text(utils.format_currency(amount)), style_value),
            Paragraph(reshape_text("مبلغ پایه معامله:"), style_label),
        ],
        [
            Paragraph(reshape_text(utils.format_currency(emp_pays)), style_value),
            Paragraph(reshape_text("پرداختی کارفرما:"), style_label),
            Paragraph(reshape_text(f"{comm_pct:,.2g}%"), style_value),
            Paragraph(reshape_text("درصد کارمزد:"), style_label),
        ],
        [
            Paragraph(reshape_text(utils.format_currency(net)), style_value),
            Paragraph(reshape_text(f"خالص {roles['provider']}:"), style_label),
            Paragraph(reshape_text(utils.format_currency(comm)), style_value),
            Paragraph(reshape_text("مبلغ کارمزد:"), style_label),
        ],
        [
            Paragraph(reshape_text(payer_text), style_value),
            Paragraph(reshape_text("پرداخت‌کننده کارمزد:"), style_label),
            Paragraph(reshape_text("میانجی امن"), style_value),
            Paragraph(reshape_text("نوع واسطه‌گری:"), style_label),
        ]
    ]
    finance_table = Table(finance_data, colWidths=[40*mm, 35*mm, 70*mm, 35*mm])
    finance_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), COLORS["bg_card"]),
        ('BORDER', (0,0), (-1,-1), 0.5, COLORS["border"]),
        ('ROUNDEDCORNERS', [8, 8, 8, 8]),
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(finance_table)
    elements.append(Spacer(1, 10*mm))

    # --- ۴. اطلاعات طرفین (Parties Info) ---
    elements.append(Paragraph(reshape_text("مشخصات طرفین قرارداد"), style_h1))
    elements.append(Spacer(1, 3*mm))
    
    parties_data = [
        [
            Paragraph(reshape_text(f"ارائه‌دهنده خدمت ({roles['provider']})"), get_para_style('RHeader', bold=True, align=TA_CENTER)),
            Paragraph(reshape_text(f"گیرنده خدمت ({roles['client']})"), get_para_style('LHeader', bold=True, align=TA_CENTER)),
        ],
        [
            Table([
                [Paragraph(reshape_text("نام کامل:"), style_label), Paragraph(reshape_text(seller_info['name']), style_value)],
                [Paragraph(reshape_text("کد ملی:"), style_label), Paragraph(reshape_text(seller_info['id']), style_value)],
                [Paragraph(reshape_text("تلفن همراه:"), style_label), Paragraph(reshape_text(seller_info['phone']), style_value)],
                [Paragraph(reshape_text("شناسه/آدرس IP:"), style_label), Paragraph(reshape_text(f"{seller_info.get('uid', '---')} / {seller_info['ip']}"), style_value)],
            ], colWidths=[30*mm, 50*mm]),
            
            Table([
                [Paragraph(reshape_text("نام کامل:"), style_label), Paragraph(reshape_text(buyer_info['name']), style_value)],
                [Paragraph(reshape_text("کد ملی:"), style_label), Paragraph(reshape_text(buyer_info['id']), style_value)],
                [Paragraph(reshape_text("تلفن همراه:"), style_label), Paragraph(reshape_text(buyer_info['phone']), style_value)],
                [Paragraph(reshape_text("شناسه/آدرس IP:"), style_label), Paragraph(reshape_text(f"{buyer_info.get('uid', '---')} / {buyer_info['ip']}"), style_value)],
            ], colWidths=[30*mm, 50*mm]),
        ]
    ]
    parties_table = Table(parties_data, colWidths=[90*mm, 90*mm])
    parties_table.setStyle(TableStyle([
        ('BACKGROUND', (0,1), (-1,-1), COLORS["bg_card"]),
        ('GRID', (0,1), (-1,-1), 0.5, COLORS["border"]),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    elements.append(parties_table)
    elements.append(Spacer(1, 10*mm))

    # --- ۵. تعهدات (Obligations) ---
    elements.append(Paragraph(reshape_text("شرح تعهدات و توضیحات اختصاصی"), style_h1))
    elements.append(Spacer(1, 3*mm))
    
    # پاکسازی توضیحات از متون هرز و فرمت‌بندی
    clean_desc = description.replace("\r", "").strip()
    elements.append(Paragraph(reshape_text(clean_desc), style_body))
    elements.append(Spacer(1, 6*mm))

    # مفاد حقوقی
    cat_terms = utils.CATEGORY_LEGAL_CLAUSES.get(category, utils.CATEGORY_LEGAL_CLAUSES["GEN"])
    cat_terms = clean_markdown(cat_terms)
    for line in cat_terms.split("\n"):
        line = line.strip()
        if not line: continue
        # حذف شماره از ابتدای خط اگر باشد برای بولت کردن
        line_clean = re.sub(r"^\d+[\.\-]\s*", "", line)
        elements.append(Paragraph(reshape_text(f"• {line_clean}"), get_para_style('Term', fontSize=9, leading=14)))
    
    elements.append(Spacer(1, 10*mm))

    # --- ۶. داوری و قوانین (Legal) ---
    elements.append(Paragraph(reshape_text("داوری و ضمانت‌های اجرایی میانجی"), style_h1))
    elements.append(Spacer(1, 3*mm))
    
    legal_text = (
        "۱. این قرارداد طبق ماده ۱۰ قانون مدنی بین طرفین نافذ و لازم‌الاجرا است.\n"
        "۲. طرفین توافق نمودند که سامانه میانجی (و نماینده قانونی آن) به عنوان داور مرضی‌الطرفین جهت حل اختلاف انتخاب شود. رای داور برای طرفین قطعی، نهایی و لازم‌الاجرا بوده و حق هرگونه اعتراض از طرفین سلب می‌گردد.\n"
        "۳. ثبت تاییدیه از طریق رمز یکبارمصرف (OTP) و ثبت وقایع در سیاهه سیستم، طبق مواد ۱۲ و ۱۵ قانون تجارت الکترونیک، به منزله امضای الکترونیکی امن و قبول کامل مفاد این سند است.\n"
        "۴. این سند در یک نسخه الکترونیکی صادر شده و تمام نسخه‌های دانلودشده یا استعلام‌شده از سامانه دارای اعتبار یکسان و رسمی می‌باشند."
    )
    for line in legal_text.split("\n"):
        elements.append(Paragraph(reshape_text(line.strip()), style_legal))
    
    elements.append(Spacer(1, 12*mm))

    # --- ۷. تاریخچه و OTP (Audit Trail) ---
    elements.append(Paragraph(reshape_text("سیاهه وقایع و گواهی امنیتی (OTP)"), style_h1))
    elements.append(Spacer(1, 3*mm))
    
    audit_data = [
        [reshape_text("رویداد سیستم"), reshape_text("زمان ثبت (تهران)"), reshape_text("وضعیت تاییدیه")]
    ]
    
    def add_audit(event, time_raw, status):
        if not time_raw: return
        audit_data.append([
            Paragraph(reshape_text(event), style_audit),
            Paragraph(reshape_text(utils.convert_to_jalali(time_raw)), style_audit),
            Paragraph(reshape_text(status), style_audit)
        ])

    add_audit("ایجاد معامله", created_at, "ثبت سیستمی")
    if contract_data.get("buyer_signed_at"):
        add_audit(f"امضای {roles['client']}", contract_data.get("buyer_signed_at"), "تایید OTP ✅")
    if contract_data.get("seller_signed_at"):
        add_audit(f"امضای {roles['provider']}", contract_data.get("seller_signed_at"), "تایید OTP ✅")
    if contract_data.get("paid_at"):
        add_audit("فعالسازی (پرداخت)", contract_data.get("paid_at"), "تایید مالی")

    audit_table = Table(audit_data, colWidths=[80*mm, 60*mm, 40*mm])
    audit_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), COLORS["border"]),
        ('GRID', (0,0), (-1,-1), 0.5, COLORS["border"]),
        ('FONTNAME', (0,0), (-1,0), PERSIAN_FONT_BOLD),
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(audit_table)

    # Build PDF
    try:
        def draw_bg(canvas, doc):
            canvas.saveState()
            canvas.setFillColor(COLORS["bg_main"])
            canvas.rect(0, 0, 210*mm, 297*mm, fill=1, stroke=0)
            canvas.restoreState()

        logger.info(f"Building PDF for contract {cid}...")
        # استفاده از یک بلوک سعی-خطا برای ساخت نهایی جهت یافتن المان خطاکار
        try:
            doc.build(elements, canvasmaker=ContractCanvas, onFirstPage=draw_bg, onLaterPages=draw_bg)
        except Exception as build_err:
            logger.error(f"Error during doc.build for {cid}: {build_err}")
            # تلاش مجدد با حذف تمام المان‌های احتمالی خطاکار (Fallback به نسخه مینیمال)
            buffer.seek(0)
            buffer.truncate()
            minimal_elements = [Paragraph(reshape_text(f"خطا در تولید نسخه کامل قرارداد {cid}"), style_title)]
            doc.build(minimal_elements, canvasmaker=ContractCanvas)
        
        # بررسی حجم بافر تولید شده
        size = buffer.tell()
        logger.info(f"PDF built successfully. Size: {size} bytes")
        
        if size == 0:
            raise ValueError("Generated PDF is empty (0 bytes)")
            
        buffer.seek(0)
        return buffer
    except Exception as e:
        import traceback
        logger.error(f"Critical error building PDF for {cid}: {e}")
        logger.error(traceback.format_exc())
        # بازگرداندن بافر خالی یا ناتمام برای مدیریت در لایه بالاتر
        buffer.seek(0)
        return buffer
