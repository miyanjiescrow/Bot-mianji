from typing import Optional, Union, Dict, Any, List
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import config

def get_main_menu(is_admin: bool = False, is_verified: bool = False) -> ReplyKeyboardMarkup:
    """
    منوی اصلی ربات با پوشش کامل تمام قابلیت‌ها.
    """
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # ردیف ۱
    markup.row(
        KeyboardButton("🤝 ثبت معامله جدید"),
        KeyboardButton("📜 لیست معاملات من")
    )
    
    # دکمه پروفایل کاربری
    verify_label = "👤 پروفایل کاربری"
    
    # ردیف ۲
    markup.row(
        KeyboardButton(verify_label),
        KeyboardButton("💳 کیف پول و اعتبار")
    )
    

    # ردیف ۴ - تمام عرض
    markup.row(KeyboardButton("🎧 پشتیبانی و راهنمای استفاده 📖"))
    
    # ردیف ۴
    markup.row(KeyboardButton("💎 پنل سفیران"))
    
    if is_admin:
        markup.row(KeyboardButton("⚡ پنل مدیریت هوشمند"))
        
    return markup

def get_role_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد انتخاب نقش کاربر در معامله"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("💼 کارفرما (خریدار)"), KeyboardButton("🛠 مجری (پیمانکار)"))
    markup.add(KeyboardButton("🔙 انصراف و بازگشت"))
    return markup

def get_category_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد انتخاب نوع و دسته‌بندی قرارداد"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("💻 برنامه‌نویسی و IT"),
        KeyboardButton("🎨 طراحی و گرافیک"),
        KeyboardButton("🎓 دانشگاهی و پژوهشی"),
        KeyboardButton("📚 آموزشی و مشاوره"),
        KeyboardButton("📑 سایر خدمات و عمومی")
    )
    markup.add(KeyboardButton("🔙 انصراف و بازگشت"))
    return markup

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد انصراف کلی"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("🔙 انصراف و بازگشت"))
    return markup

def get_contract_preview_inline(draft_id: str = "draft") -> InlineKeyboardMarkup:
    """پنل شیشه‌ای پیش‌نمایش، ویرایش و تایید نهایی پیش از امضا"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید و مرحله امضا", callback_data=f"confirm_draft_{draft_id}"),
        InlineKeyboardButton("✏️ ویرایش پیش‌نویس", callback_data=f"edit_draft_{draft_id}")
    )
    markup.add(
        InlineKeyboardButton("❌ لغو پیش‌نویس", callback_data="cancel_draft")
    )
    return markup

def get_draft_edit_inline(draft_id: str = "draft") -> InlineKeyboardMarkup:
    """منوی شیشه‌ای انتخاب بخش برای ویرایش مجزا"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✏️ ویرایش عنوان", callback_data=f"edit_field_title_{draft_id}"),
        InlineKeyboardButton("💰 ویرایش مبلغ", callback_data=f"edit_field_amount_{draft_id}")
    )
    markup.add(
        InlineKeyboardButton("⏱ ویرایش مهلت تحویل", callback_data=f"edit_field_deadline_{draft_id}"),
        InlineKeyboardButton("📝 ویرایش شرح تعهدات", callback_data=f"edit_field_desc_{draft_id}")
    )
    markup.add(
        InlineKeyboardButton("🔄 تعداد ویرایش رایگان", callback_data=f"edit_field_freeedits_{draft_id}")
    )
    markup.add(
        InlineKeyboardButton("🔙 بازگشت به پیش‌نمایش قرارداد", callback_data=f"back_to_preview_{draft_id}")
    )
    return markup


def get_negotiation_edit_inline(contract_id: str) -> InlineKeyboardMarkup:
    """منوی شیشه‌ای انتخاب بخش برای پیشنهاد تغییر در جریان چانه‌زنی"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✏️ تغییر عنوان موضوع", callback_data=f"neg_edit_title_{contract_id}"),
        InlineKeyboardButton("💰 تغییر مبلغ معامله", callback_data=f"neg_edit_amount_{contract_id}")
    )
    markup.add(
        InlineKeyboardButton("⏱ تغییر مهلت تحویل", callback_data=f"neg_edit_deadline_{contract_id}"),
        InlineKeyboardButton("📝 تغییر شرح تعهدات", callback_data=f"neg_edit_desc_{contract_id}")
    )
    markup.add(
        InlineKeyboardButton("🔄 تغییر سهمیه ویرایش", callback_data=f"neg_edit_freeedits_{contract_id}"),
        InlineKeyboardButton("⚖️ تغییر پرداخت‌کننده کارمزد", callback_data=f"neg_edit_comm_payer_{contract_id}")
    )
    markup.add(
        InlineKeyboardButton("🔙 بازگشت به قرارداد", callback_data=f"view_contract_terms:{contract_id}")
    )
    return markup

def get_deposit_admin_keyboard(transaction_id: Union[int, str]) -> InlineKeyboardMarkup:
    """دکمه‌های تایید یا رد شارژ حساب برای ادمین"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید و شارژ", callback_data=f"approve_deposit:{transaction_id}"),
        InlineKeyboardButton("❌ رد فیش", callback_data=f"reject_deposit:{transaction_id}")
    )
    return markup

def get_free_edits_selection_inline() -> InlineKeyboardMarkup:
    """
    پنل شیشه‌ای انتخاب تعداد ویرایش رایگان توسط نفر اول قرارداد (ایجادکننده).
    بعد از اتمام سهمیه انتخابی، مجری برای هر ویرایش بعدی قیمت تعیین می‌کند.

    رفع باگ چیدمان (فاز ۱ - UI/UX): قبلاً همه دکمه‌ها (شامل متن بلند
    «بدون ویرایش رایگان») با یک row_width=3 ثابت در کنار دکمه‌های کوتاه
    عددی («۱ بار»، «۲ بار») چیده می‌شدند که در گوشی باعث بهم‌ریختگی/شکستگی
    ظاهری می‌شد. اکنون دکمه بلند «بدون ویرایش رایگان» تنها و تمام‌عرض در
    ردیف اول قرار می‌گیرد، گزینه‌های عددی کوتاه در ردیف‌های ۲ تایی مرتب
    می‌شوند و «عدد دلخواه» در انتها به‌صورت مجزا می‌آید.
    """
    markup = InlineKeyboardMarkup(row_width=2)
    options = getattr(config, "FREE_EDITS_OPTIONS", [0, 1, 2, 3, 5])

    # ردیف اول: گزینه «بدون ویرایش رایگان» (متن بلند) به‌تنهایی و تمام‌عرض
    if 0 in options:
        markup.add(InlineKeyboardButton("بدون ویرایش رایگان", callback_data="set_free_edits_0"))

    # گزینه‌های عددی کوتاه، ۲ تا در هر ردیف
    numeric_buttons = [
        InlineKeyboardButton(f"{n} بار", callback_data=f"set_free_edits_{n}")
        for n in options if n != 0
    ]
    for i in range(0, len(numeric_buttons), 2):
        markup.row(*numeric_buttons[i:i + 2])

    # ردیف آخر: عدد دلخواه، به‌تنهایی
    markup.add(InlineKeyboardButton("✍️ عدد دلخواه", callback_data="set_free_edits_custom"))
    return markup


# ====================================================
# ساخت معامله با «ویزارد گام‌به‌گام» (جایگزین فرم متنی یکجای قدیمی)
# هر مرحله فقط یک سوال ساده می‌پرسد؛ کاربر دیگر نیازی به کپی/پر کردن یک
# قالب متنی چندخطی و دقیق‌التایپ ندارد.
# ====================================================

def get_wizard_deadline_inline() -> InlineKeyboardMarkup:
    """انتخاب سریع مهلت تحویل با دکمه‌های آماده رایج + گزینه عدد دلخواه"""
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("۱ روز", callback_data="wiz_deadline_1"),
        InlineKeyboardButton("۳ روز", callback_data="wiz_deadline_3"),
        InlineKeyboardButton("۷ روز", callback_data="wiz_deadline_7"),
    )
    markup.add(
        InlineKeyboardButton("۱۴ روز", callback_data="wiz_deadline_14"),
        InlineKeyboardButton("۳۰ روز", callback_data="wiz_deadline_30"),
        InlineKeyboardButton("✍️ عدد دلخواه", callback_data="wiz_deadline_custom"),
    )
    return markup

def get_wizard_milestones_ask_inline() -> InlineKeyboardMarkup:
    """آیا معامله به چند مرحله پرداخت تقسیم شود؟"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ بله، به چند مرحله تقسیم شود", callback_data="wiz_ms_yes"),
        InlineKeyboardButton("➡️ نه، یکجا پرداخت شود", callback_data="wiz_ms_no"),
    )
    return markup

def get_wizard_milestone_more_inline() -> InlineKeyboardMarkup:
    """بعد از ثبت هر مرحله پرداخت: افزودن مرحله دیگر یا اتمام"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("➕ افزودن مرحله دیگر", callback_data="wiz_ms_add_more"),
        InlineKeyboardButton("✅ اتمام و ادامه", callback_data="wiz_ms_done"),
    )
    return markup

def get_wizard_ms_template_inline() -> InlineKeyboardMarkup:
    """
    قالب‌های آماده تقسیم مبلغ معامله بین چند مرحله پرداخت (بر اساس درصد) تا
    نیاز به محاسبه دستی و تایپ مبلغ هر مرحله نباشد. گزینه «دستی» برای مواقعی
    که کاربر می‌خواهد مبلغ دقیق هر مرحله را خودش تعیین کند نگه داشته شده است.
    """
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("۵۰٪ پیش‌پرداخت / ۵۰٪ تسویه نهایی (۲ مرحله)", callback_data="wiz_ms_tpl_50_50"),
        InlineKeyboardButton("۳۰٪ پیش‌پرداخت / ۷۰٪ تسویه نهایی (۲ مرحله)", callback_data="wiz_ms_tpl_30_70"),
        InlineKeyboardButton("۳۰٪ / ۳۰٪ / ۴۰٪ (۳ مرحله)", callback_data="wiz_ms_tpl_30_30_40"),
        InlineKeyboardButton("تقسیم مساوی بین چند مرحله", callback_data="wiz_ms_tpl_equal"),
        InlineKeyboardButton("📐 درصد دلخواه (مثال: 20,30,50)", callback_data="wiz_ms_tpl_custom"),
        InlineKeyboardButton("✍️ دستی (عنوان و مبلغ هر مرحله)", callback_data="wiz_ms_tpl_manual"),
        InlineKeyboardButton("➡️ نه، یکجا پرداخت شود", callback_data="wiz_ms_no"),
    )
    return markup


def get_wizard_ms_equal_count_inline() -> InlineKeyboardMarkup:
    """انتخاب سریع تعداد مراحل برای تقسیم مساوی"""
    markup = InlineKeyboardMarkup(row_width=4)
    markup.add(
        InlineKeyboardButton("۲", callback_data="wiz_ms_equaln_2"),
        InlineKeyboardButton("۳", callback_data="wiz_ms_equaln_3"),
        InlineKeyboardButton("۴", callback_data="wiz_ms_equaln_4"),
        InlineKeyboardButton("۵", callback_data="wiz_ms_equaln_5"),
    )
    return markup


def get_wizard_commission_payer_inline() -> InlineKeyboardMarkup:
    """انتخاب پرداخت‌کننده کارمزد در ویزارد یا تغییر شرایط"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🛠 مجری (کسر از سهم مجری)", callback_data="wiz_comm_freelancer"),
        InlineKeyboardButton("💼 کارفرما (اضافه به مبلغ کارفرما)", callback_data="wiz_comm_employer"),
        InlineKeyboardButton("⚖️ مشترک (۵۰/۵۰)", callback_data="wiz_comm_shared"),
        InlineKeyboardButton("❌ انصراف", callback_data="cancel_wizard")
    )
    return markup


def get_ms_pay_keyboard(contract_id: str, idx: int) -> InlineKeyboardMarkup:
    """دکمه پرداخت وجه / ارسال فیش مخصوص یک مرحله از پرداخت مرحله‌ای"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(f"💳 پرداخت وجه / ارسال فیش مرحله {idx + 1}", callback_data=f"msp_pay_{contract_id}_{idx}"))
    return markup


def get_ms_receipt_admin_inline(contract_id: str, idx: int, buyer_id: int) -> InlineKeyboardMarkup:
    """تایید/رد فیش یک مرحله توسط ادمین"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید فیش", callback_data=f"adm:msreceipt:appr:{contract_id}:{idx}:{buyer_id}"),
        InlineKeyboardButton("❌ رد فیش", callback_data=f"adm:msreceipt:rej:{contract_id}:{idx}:{buyer_id}"),
    )
    markup.add(InlineKeyboardButton("📁 پرونده کامل معامله", callback_data=f"adm:case:{contract_id}"))
    markup.add(InlineKeyboardButton("🔙 بازگشت به فیش‌ها", callback_data="adm:pending_receipts"))
    return markup


def get_ms_deliver_keyboard(contract_id: str, idx: int) -> InlineKeyboardMarkup:
    """دکمه ارسال تحویلی یک مرحله توسط مجری"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(f"📦 ارسال تحویلی مرحله {idx + 1}", callback_data=f"msp_deliver_{contract_id}_{idx}"))
    return markup


def get_ms_delivery_review_keyboard(contract_id: str, idx: int) -> InlineKeyboardMarkup:
    """تایید/رد تحویلی یک مرحله توسط کارفرما"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ تایید و آزادسازی وجه این مرحله", callback_data=f"msp_dok_{contract_id}_{idx}"),
        InlineKeyboardButton("⚠️ نیاز به اصلاح دارد", callback_data=f"msp_dno_{contract_id}_{idx}"),
    )
    return markup


def get_wizard_description_interactive_inline() -> InlineKeyboardMarkup:
    """منوی تعاملی مرحله ۵ برای مدیریت تعهدات"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📋 مشاهده تعهدات قانونی پیش‌فرض", callback_data="wiz_desc_view_legal"),
        InlineKeyboardButton("✏️ افزودن تعهدات شخصی به متن قانونی", callback_data="wiz_desc_add_custom")
    )
    return markup


def get_wizard_description_legal_back_inline() -> InlineKeyboardMarkup:
    """کیبورد بازگشت از نمایش تعهدات قانونی به منوی مرحله ۵"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="wiz_desc_back"))
    return markup


def get_extra_edit_decision_inline(contract_id: str) -> InlineKeyboardMarkup:
    """پنل شیشه‌ای تصمیم کارفرما درباره هزینه ویرایش اضافه (پس از اتمام ویرایش رایگان)"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ پرداخت و موافقت با ویرایش اضافه", callback_data=f"extra_edit_approve_{contract_id}"),
        InlineKeyboardButton("❌ عدم موافقت با هزینه ویرایش", callback_data=f"extra_edit_reject_{contract_id}")
    )
    return markup

def get_kyc_phone_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد ارسال شماره جهت احراز هویت شاهکار"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("📱 ارسال شماره جهت احراز هویت", request_contact=True))
    markup.add(KeyboardButton("❌ انصراف و بازگشت به منو"))
    return markup

def get_phone_sign_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد تایید جهت امضای الکترونیک"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("✅ تایید می‌کنم"))
    markup.add(KeyboardButton("❌ انصراف و بازگشت به منو"))
    return markup

def get_skip_work_phone_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد ثبت شماره کاری اختیاری"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("⏭ رد کردن و استفاده از شماره تلگرام"))
    markup.add(KeyboardButton("❌ انصراف و بازگشت به منو"))
    return markup

def get_wallet_inline(has_cards: bool = False) -> InlineKeyboardMarkup:
    """دکمه‌های شیشه‌ای مدیریت کیف پول"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💳 شارژ حساب", callback_data="deposit_wallet"),
        InlineKeyboardButton("🏧 درخواست برداشت", callback_data="withdraw_wallet")
    )
    markup.add(
        InlineKeyboardButton("📜 تاریخچه تراکنش‌ها", callback_data="show_transactions"),
        InlineKeyboardButton("💳 مدیریت کارت‌ها", callback_data="manage_cards")
    )
    return markup

def get_withdraw_cards_inline(cards: list) -> InlineKeyboardMarkup:
    """نمایش کارت‌های ذخیره‌شده برای انتخاب در زمان برداشت"""
    markup = InlineKeyboardMarkup(row_width=1)
    for idx, card in enumerate(cards):
        num = card.get("card_number", "")
        sheba = card.get("sheba", "")
        bank_emoji = card.get("bank_emoji", "💳")
        bank_name = card.get("bank_name", "")
        
        if num:
            masked = f"{num[:4]} **** **** {num[-4:]}"
            display = f"{bank_emoji} {bank_name}: {masked}"
        elif sheba:
            clean = sheba.replace("IR", "")
            masked = f"IR{clean[:2]}...{clean[-4:]}"
            display = f"🏦 {bank_name} (شبا): {masked}"
        else:
            display = f"💳 حساب #{idx + 1}"
            
        markup.add(InlineKeyboardButton(display, callback_data=f"withdraw_use_card_{idx}"))
    
    markup.add(InlineKeyboardButton("➕ افزودن حساب جدید", callback_data="card_add_new"))
    markup.add(InlineKeyboardButton("⌨️ وارد کردن دستی شبا (موقت)", callback_data="withdraw_new_sheba"))
    markup.add(InlineKeyboardButton("❌ انصراف", callback_data="show_wallet"))
    return markup


def get_cards_management_inline(cards: list) -> InlineKeyboardMarkup:
    """
    منوی شیشه‌ای مدیریت کارت‌های بانکی کاربر.
    هر کارت: دکمه انتخاب به‌عنوان پیش‌فرض + دکمه حذف.
    """
    markup = InlineKeyboardMarkup(row_width=2)
    for idx, card in enumerate(cards):
        num = card.get("card_number", "")
        sheba = card.get("sheba", "")
        bank_emoji = card.get("bank_emoji", "💳")
        bank_name = card.get("bank_name", "")
        
        if num:
            masked = f"{num[:4]} **** **** {num[-4:]}"
            display_num = masked
        elif sheba:
            clean = sheba.replace("IR", "")
            masked = f"IR{clean[:2]}...{clean[-4:]}"
            display_num = masked
        else:
            display_num = "حساب ثبت شده"
            
        default_mark = "✅ " if card.get("is_default") else ""
        bank_str = f"{bank_emoji} {bank_name}" if bank_name else "بانک"
        display = f"{default_mark}{bank_str}: {display_num}"
        
        # ردیف هر کارت: نمایش شماره + دکمه‌های عملیات
        markup.add(
            InlineKeyboardButton(f"⭐ پیش‌فرض: {display}", callback_data=f"card_setdefault_{idx}"),
        )
        markup.add(
            InlineKeyboardButton("🗑 حذف این کارت", callback_data=f"card_delete_{idx}")
        )
    markup.add(InlineKeyboardButton("➕ افزودن کارت جدید", callback_data="card_add_new"))
    return markup

def get_help_center_inline() -> InlineKeyboardMarkup:
    """پنل مرکزی راهنما، قوانین و پشتیبانی با چیدمان بهینه"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📖 راهنمای استفاده", callback_data="show_guide"),
        InlineKeyboardButton("⚖️ قوانین و امنیت", callback_data="show_rules")
    )
    markup.add(
        InlineKeyboardButton("❓ سوالات متداول", callback_data="faq_info"),
        InlineKeyboardButton("💬 پشتیبانی آنلاین", url="https://t.me/Mianji_Support")
    )
    return markup

def get_faq_keyboard() -> InlineKeyboardMarkup:
    """منوی شیشه‌ای سوالات متداول"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("💰 کارمزد سامانه چقدر است؟", callback_data="faq_q1"),
        InlineKeyboardButton("⏱ زمان واریز وجه چقدر است؟", callback_data="faq_q2"),
        InlineKeyboardButton("⚖️ در صورت بروز اختلاف چه می‌شود؟", callback_data="faq_q3"),
        InlineKeyboardButton("🛡 امنیت وجه من چگونه تضمین می‌شود؟", callback_data="faq_q4"),
        InlineKeyboardButton("❌ امکان لغو یکطرفه معامله وجود دارد؟", callback_data="faq_q5"),
        InlineKeyboardButton("⏳ اگر مجری پروژه را تحویل ندهد چه میشود؟", callback_data="faq_q6"),
        InlineKeyboardButton("✏️ مراحل ویرایش و اصلاح فایلها چگونه است؟", callback_data="faq_q7"),
        InlineKeyboardButton("🆔 آیا برای استفاده نیاز به احراز هویت است؟", callback_data="faq_q8"),
        InlineKeyboardButton("🔙 بازگشت به راهنما", callback_data="back_to_help"),
        InlineKeyboardButton("🏠 صفحه اصلی", callback_data="main_menu")
    )
    return markup

def get_faq_back_inline() -> InlineKeyboardMarkup:
    """دکمه بازگشت به لیست سوالات متداول"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔙 لیست سوالات", callback_data="faq_info"),
        InlineKeyboardButton("🏠 صفحه اصلی", callback_data="main_menu")
    )
    return markup

def get_wallet_amount_cancel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد لغو حین وارد کردن مبلغ شارژ/برداشت"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("❌ انصراف و بازگشت به منو"))
    return markup

def get_wallet_admin_approval_inline(request_type: str, user_id: int, amount: float, req_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای تایید/رد درخواست شارژ یا برداشت کیف پول برای ادمین"""
    markup = InlineKeyboardMarkup(row_width=2)
    amt_int = int(amount)
    req_suffix = f":{req_id}" if req_id else ""
    
    if request_type == "deposit":
        markup.add(
            InlineKeyboardButton("✅ تایید شارژ", callback_data=f"adm:wallet_req:ok:deposit:{user_id}:{amt_int}{req_suffix}"),
            InlineKeyboardButton("❌ رد درخواست", callback_data=f"adm:wallet_req:no:deposit:{user_id}:{amt_int}{req_suffix}")
        )
    else:
        # درخواست برداشت
        markup.add(
            InlineKeyboardButton("✅ واریز شد", callback_data=f"adm:wallet_req:ok:withdraw:{user_id}:{amt_int}{req_suffix}"),
            InlineKeyboardButton("⏳ سیکل شبا", callback_data=f"adm:wd:queue:{req_id}") if req_id else InlineKeyboardButton("⏳ سیکل شبا", callback_data="none")
        )
        markup.add(
            InlineKeyboardButton("❌ رد و بازگشت وجه", callback_data=f"adm:wallet_req:no:withdraw:{user_id}:{amt_int}{req_suffix}"),
            InlineKeyboardButton("🔙 لیست", callback_data="adm:withdrawals")
        )
    return markup

def get_payment_options_inline(contract_id: str, amount: float, wallet_balance: float) -> InlineKeyboardMarkup:
    """انتخاب روش پرداخت معامله (کارت به کارت یا کیف پول)"""
    markup = InlineKeyboardMarkup(row_width=1)
    
    # دکمه پرداخت از کیف پول (اگر موجودی کافی باشد)
    if wallet_balance >= amount:
        markup.add(InlineKeyboardButton(f"💰 پرداخت از کیف پول (موجودی: {int(wallet_balance):,} تومان)", callback_data=f"pay_with_wallet_{contract_id}"))
    else:
        markup.add(InlineKeyboardButton(f"❌ موجودی کیف پول کافی نیست ({int(wallet_balance):,} تومان)", callback_data=f"wallet_low_balance_{contract_id}"))
        
    markup.add(InlineKeyboardButton("💳 کارت به کارت (ارسال فیش)", callback_data=f"pay_with_receipt_{contract_id}"))
    return markup

def get_withdrawal_list_inline(requests: list) -> InlineKeyboardMarkup:
    """لیست دکمه‌ای درخواست‌های برداشت در حال انتظار، برای پنل ادمین"""
    markup = InlineKeyboardMarkup(row_width=1)
    for req in requests:
        rid = req.get("id")
        uid = req.get("user_id") or req.get("id")
        try:
            amount_display = f"{int(float(req.get('amount', 0))):,}"
        except Exception:
            amount_display = str(req.get("amount", 0))
        suspicious_icon = " 🚩" if req.get("suspicious_flag") else ""
        markup.add(InlineKeyboardButton(
            f"#{rid} | کاربر {uid} | {amount_display} تومان{suspicious_icon}",
            callback_data=f"adm:wd:view:{rid}"
        ))
    markup.add(InlineKeyboardButton("🔄 بروزرسانی لیست", callback_data="adm:withdrawals"))
    markup.add(InlineKeyboardButton("🔙 بازگشت به پنل ادمین", callback_data="adm:home"))
    return markup

def get_deposit_list_inline(requests: list) -> InlineKeyboardMarkup:
    """لیست دکمه‌ای درخواست‌های شارژ حساب (واریزی) در حال انتظار"""
    markup = InlineKeyboardMarkup(row_width=1)
    for req in requests:
        rid = req.get("related_id") or req.get("id") # transaction_id
        uid = req.get("user_id")
        try:
            amount_display = f"{int(float(req.get('amount', 0))):,}"
        except Exception:
            amount_display = str(req.get("amount", 0))
        markup.add(InlineKeyboardButton(
            f"💰 تراکنش {rid} | کاربر {uid} | {amount_display} تومان",
            callback_data=f"adm:wallet_receipt:view:{rid}"
        ))
    markup.add(InlineKeyboardButton("🔄 بروزرسانی لیست", callback_data="adm:pending_deposits"))
    markup.add(InlineKeyboardButton("🔙 بازگشت به پنل ادمین", callback_data="adm:home"))
    return markup

def get_deposit_action_inline(tx_id: str, user_id: int, amount: float) -> InlineKeyboardMarkup:
    """دکمه‌های تصمیم‌گیری روی یک درخواست شارژ حساب مشخص"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید و شارژ", callback_data=f"adm:wallet_req:ok:deposit:{user_id}:{int(amount)}:{tx_id}"),
        InlineKeyboardButton("❌ رد فیش", callback_data=f"adm:wallet_req:no:deposit:{user_id}:{int(amount)}:{tx_id}")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="adm:pending_deposits"))
    return markup


def get_withdrawal_action_inline(req_id) -> InlineKeyboardMarkup:
    """دکمه‌های تصمیم‌گیری روی یک درخواست برداشت مشخص"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تسویه شد (فوری)", callback_data=f"adm:wd:paid:{req_id}"),
        InlineKeyboardButton("⏳ در صف سیکل شبا", callback_data=f"adm:wd:queue:{req_id}")
    )
    markup.add(
        InlineKeyboardButton("❌ لغو و عودت وجه", callback_data=f"adm:wd:cancel:{req_id}"),
        InlineKeyboardButton("📎 افزودن مستندات", callback_data=f"adm:wd:add_docs:{req_id}")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="adm:withdrawals"))
    return markup


def get_contracts_categories_keyboard(stats: dict) -> InlineKeyboardMarkup:
    """منوی دسته‌بندی معاملات بر اساس وضعیت"""
    markup = InlineKeyboardMarkup(row_width=2)
    # 🟢 درحال انجام (۲) | ⏳ در انتظار اقدام (۱)
    # 🏁 تکمیل شده (۵) | ❌ لغوشده / مرجوعی
    markup.add(
        InlineKeyboardButton(f"🟢 درحال انجام ({stats.get('active', 0)})", callback_data="contracts_cat:active"),
        InlineKeyboardButton(f"⏳ در انتظار اقدام ({stats.get('pending', 0)})", callback_data="contracts_cat:pending")
    )
    markup.add(
        InlineKeyboardButton(f"🏁 تکمیل شده ({stats.get('completed', 0)})", callback_data="contracts_cat:completed"),
        InlineKeyboardButton(f"❌ لغوشده / مرجوعی ({stats.get('cancelled', 0)})", callback_data="contracts_cat:cancelled")
    )
    markup.add(
        InlineKeyboardButton("🔍 جست‌وجوی قرارداد با شناسه", callback_data="contracts_search"),
        InlineKeyboardButton("⬅️ بازگشت به منو", callback_data="back_to_menu")
    )
    return markup

def get_contract_action_keyboard(contract_id: str, user_role: str, status: str, has_milestones: bool = False, staged_payment: bool = False, show_terms_button: bool = True, contract: dict = None) -> InlineKeyboardMarkup:
    """
    دکمه‌های مدیریتی قرارداد بسته به وضعیت فعلی معامله و نقش کاربر.
    """
    markup = InlineKeyboardMarkup(row_width=2)
    
    buyer_signed = False
    seller_signed = False
    if contract:
        buyer_signed = bool(contract.get("buyer_signed_at") or contract.get("employer_signed_at"))
        seller_signed = bool(contract.get("seller_signed_at") or contract.get("freelancer_signed_at"))

    user_has_signed = buyer_signed if user_role == "employer" else seller_signed

    # دکمه‌های مربوط به پرداخت و تحویل
    if status in ["pending_payment", "awaiting_payment"]:
        if user_role == "employer":
            markup.add(InlineKeyboardButton("💳 پرداخت وجه / ارسال فیش", callback_data=f"pay_options_{contract_id}"))
        elif user_role == "freelancer":
            markup.add(InlineKeyboardButton("⏳ در انتظار پرداخت کارفرما", callback_data="none"))

    if status == "pending_approval":
        if not user_has_signed:
            markup.add(InlineKeyboardButton("✍️ امضا و تایید قرارداد", callback_data=f"sign_contract_{contract_id}"))
            markup.add(InlineKeyboardButton("🔄 پیشنهاد تغییر شرایط", callback_data=f"open_negotiation_{contract_id}"))
        else:
            other_signed = seller_signed if user_role == "employer" else buyer_signed
            if not other_signed:
                markup.add(InlineKeyboardButton("⏳ در انتظار امضای طرف مقابل", callback_data=f"show_invite_{contract_id}"))
            else:
                markup.add(InlineKeyboardButton("⏳ در انتظار واریز وجه", callback_data="none"))

    if status == "bargaining":
        if not user_has_signed:
            markup.add(InlineKeyboardButton("✍️ موافقم — امضا می‌کنم", callback_data=f"neg_accept_{contract_id}"))
            markup.add(InlineKeyboardButton("🔄 پیشنهاد متقابل می‌دهم", callback_data=f"open_negotiation_{contract_id}"))
        else:
            markup.add(InlineKeyboardButton("⏳ در انتظار تایید نهایی طرف مقابل", callback_data=f"show_invite_{contract_id}"))

    # تحویل پروژه توسط مجری
    if status in ["in_progress", "active", "paid"] and user_role == "freelancer":
        markup.add(InlineKeyboardButton("🚀 تحویل پروژه / ارسال فایل", callback_data=f"deliver_{contract_id}"))

    # دکمه داوری برای هر دو طرف در وضعیت فعال/درحال انجام
    if status in ["in_progress", "active", "paid"]:
        markup.add(InlineKeyboardButton("⚖️ ثبت اعتراض و درخواست داوری", callback_data=f"open_dispute_{contract_id}"))

    # تایید یا رد پروژه تحویلی توسط کارفرما
    if status in ["work_submitted", "delivered"] and user_role == "employer":
        markup.add(InlineKeyboardButton("✅ تایید نهایی پروژه و آزادسازی وجه", callback_data=f"final_confirm_{contract_id}"))
        markup.add(InlineKeyboardButton("⚠️ عدم تایید و درخواست اصلاح پروژه", callback_data=f"reject_project_{contract_id}"))
        markup.add(InlineKeyboardButton("⚖️ ثبت اعتراض و درخواست داوری", callback_data=f"open_dispute_{contract_id}"))
        markup.add(InlineKeyboardButton("👁 مشاهده فایل‌های تحویلی", callback_data=f"view_delivery_{contract_id}"))

    # دکمه داوری برای مجری در وضعیت تحویل‌شده
    if status in ["work_submitted", "delivered"] and user_role == "freelancer":
        markup.add(InlineKeyboardButton("⚖️ ثبت اعتراض و درخواست داوری", callback_data=f"open_dispute_{contract_id}"))
        markup.add(InlineKeyboardButton("👁 مشاهده فایل‌های ارسالی", callback_data=f"view_delivery_{contract_id}"))

    if status == "in_dispute":
        markup.add(InlineKeyboardButton("⚖️ پرونده در حال داوری ادمین", callback_data="none"))

    # در انتظار تصمیم کارفرما درباره هزینه ویرایش اضافه
    if status == "awaiting_edit_price":
        if user_role == "employer":
            markup.add(InlineKeyboardButton("💰 مشاهده و تصمیم درباره هزینه ویرایش اضافه", callback_data=f"view_extra_edit_{contract_id}"))
        else:
            markup.add(InlineKeyboardButton("💰 تعیین/تغییر هزینه ویرایش", callback_data=f"set_edit_price_{contract_id}"))
        markup.add(InlineKeyboardButton("⚖️ ثبت اعتراض و درخواست داوری", callback_data=f"open_dispute_{contract_id}"))

    # دکمه مدیریت مراحل برای همه قراردادهایی که مرحله دارند (چه پرداخت مستقل، چه عادی)
    if has_milestones:
        markup.add(InlineKeyboardButton("📊 مدیریت مراحل معامله", callback_data=f"manage_milestones_{contract_id}"))

    if status not in ["completed", "cancelled", "resolved_employer", "resolved_freelancer", "disputed", "in_dispute"]:
        markup.add(InlineKeyboardButton("❌ لغو معامله", callback_data=f"cancel_contract_{contract_id}"))

    if show_terms_button:
        markup.add(InlineKeyboardButton("📖 مشاهده متن کامل و تعهدات", callback_data=f"view_contract_terms:{contract_id}"))
    
    markup.add(InlineKeyboardButton("📑 پیش‌نمایش و دریافت PDF", callback_data=f"get_pdf_{contract_id}"))
    return markup

def get_read_contract_keyboard(contract_id: str) -> InlineKeyboardMarkup:
    """کیبورد مرحله اول برای دعوت به معامله (فقط دکمه مطالعه)"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📖 مطالعه متن کامل قرارداد", callback_data=f"view_contract_terms:{contract_id}"))
    return markup

def get_invite_overview_keyboard(contract_id: str) -> InlineKeyboardMarkup:
    """کیبورد پیام آگاهی اولیه برای نفر دوم"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("👁 مشاهده تعهدات و امضای قرارداد", callback_data=f"view_contract_terms:{contract_id}"))
    markup.add(InlineKeyboardButton("📑 پیش‌نمایش و دریافت PDF", callback_data=f"get_pdf_{contract_id}"))
    markup.add(InlineKeyboardButton("❌ لغو معامله", callback_data=f"cancel_contract_{contract_id}"))
    return markup

def get_cancel_confirmation_inline(contract_id: str) -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای تایید/رد درخواست لغوِ معاملهٔ قفل‌شده توسط طرف مقابل"""
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید لغو معامله", callback_data=f"confirm_cancel_{contract_id}"),
        InlineKeyboardButton("❌ رد درخواست لغو", callback_data=f"deny_cancel_{contract_id}")
    )
    return markup

def get_ambassador_intro_inline() -> InlineKeyboardMarkup:
    """پنل شیشه‌ای معرفی طرح سفیران برای کاربری که هنوز سفیر نشده"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📝 مطالعه شرایط و عضویت در طرح سفیران", callback_data="ambassador_terms"))
    return markup

def get_ambassador_terms_inline() -> InlineKeyboardMarkup:
    """پنل شیشه‌ای پذیرش شرایط و ضوابط سفیران"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ پذیرش و دریافت لینک دعوت", callback_data="accept_affiliate_terms"),
        InlineKeyboardButton("🔙 بازگشت به پنل اصلی", callback_data="back_to_main_menu")
    )
    return markup

def get_ambassador_dashboard_inline(stats: dict) -> InlineKeyboardMarkup:
    """
    پنل شیشه‌ای جامع سفیران (B2B Dashboard)
    شامل آمار، لینک دعوت، پکیج تبلیغاتی و تسویه حساب.
    """
    markup = InlineKeyboardMarkup(row_width=2)
    
    # ردیف اول: آمار (فقط نمایش، دکمه‌های غیرعملیاتی یا نمایشی)
    total_refs = stats.get("total_referrals", 0)
    balance = stats.get("withdrawable_balance", 0)
    tier = stats.get("tier_level", "Bronze")
    
    markup.add(
        InlineKeyboardButton(f"👥 زیرمجموعه: {total_refs} نفر", callback_data="amb_stats_refs"),
        InlineKeyboardButton(f"🏆 سطح: {tier}", callback_data="amb_stats_tier")
    )
    markup.add(
        InlineKeyboardButton(f"💰 موجودی: {int(balance):,} ت", callback_data="amb_stats_bal")
    )
    
    # ردیف دوم: عملیات اصلی
    markup.add(
        InlineKeyboardButton("🔗 لینک اختصاصی من", callback_data="amb_get_link"),
        InlineKeyboardButton("🎁 پکیج تبلیغاتی", callback_data="amb_promo_pack")
    )
    markup.add(
        InlineKeyboardButton("🏧 درخواست تسویه درآمد", callback_data="amb_cashout")
    )
    markup.add(
        InlineKeyboardButton("❓ راهنما و مزایای سطوح", callback_data="amb_tier_info")
    )
    
    # ردیف آخر: بازگشت
    markup.add(InlineKeyboardButton("⬅️ بازگشت به منوی اصلی", callback_data="back_to_menu"))
    return markup

def get_ambassador_promo_inline() -> InlineKeyboardMarkup:
    """انتخاب نوع محتوای تبلیغاتی"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🖼 دریافت بنر تصویری اختصاصی", callback_data="amb_promo_banner"),
        InlineKeyboardButton("📝 دریافت پست آماده کانال (متن+دکمه)", callback_data="amb_promo_post"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="amb_dashboard")
    )
    return markup

def get_ambassador_cashout_inline(balance: float) -> InlineKeyboardMarkup:
    """تایید نهایی درخواست تسویه"""
    markup = InlineKeyboardMarkup(row_width=1)
    if balance >= 50000: # حداقل مبلغ برداشت
        markup.add(InlineKeyboardButton(f"✅ تایید و ارسال درخواست ({int(balance):,} تومان)", callback_data="amb_cashout_confirm"))
    else:
        markup.add(InlineKeyboardButton("❌ موجودی کافی نیست (حداقل ۵۰،۰۰۰)", callback_data="amb_cashout_low"))
    markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="amb_dashboard"))
    return markup

def get_ambassador_channel_post_inline(referral_link: str) -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای آماده (URL Button) حاوی لینک اختصاصی سفیر، جهت الصاق به پست کانال او"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🤝 معامله امن با میانجی", url=referral_link))
    return markup

def get_receipt_admin_approval_inline(contract_id: str, buyer_id: int) -> InlineKeyboardMarkup:
    """
    پنل مدیریت جهت تایید یا رد فیش واریزی توسط ادمین.
    callback_data ها در فضای‌نام adm: هستند تا روتر مرکزی admin.py آن‌ها را
    مدیریت کند و دیگر با هندلرهای user.py تداخل نداشته باشند.
    """
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید فیش و فعالسازی معامله", callback_data=f"adm:receipt:appr:{contract_id}"),
        InlineKeyboardButton("❌ رد فیش واریزی", callback_data=f"adm:receipt:rej:{contract_id}")
    )
    markup.add(
        InlineKeyboardButton("📁 پرونده کامل معامله", callback_data=f"adm:case:{contract_id}")
    )
    return markup

def get_dispute_admin_inline(contract_id: str, room_link: str = None) -> InlineKeyboardMarkup:
    """
    پنل شیشه‌ای صدور رای و حل اختلاف داوری توسط ادمین.
    """
    markup = InlineKeyboardMarkup(row_width=1)
    
    if not room_link:
        markup.add(InlineKeyboardButton("🔗 ثبت و ارسال لینک گروه داوری", callback_data=f"adm:dispute:set_room:{contract_id}"))
    else:
        markup.add(InlineKeyboardButton("🚪 ورود به اتاق داوری", url=room_link))
        
    markup.add(
        InlineKeyboardButton("📁 مشاهده پرونده کامل (سابقه + فایل‌ها)", callback_data=f"adm:case:{contract_id}")
    )
    markup.add(
        InlineKeyboardButton("🟢 رای به نفع کارفرما (بازگشت وجه)", callback_data=f"adm:resolve:decide:employer:{contract_id}"),
        InlineKeyboardButton("🔵 رای به نفع مجری (آزادسازی وجه)", callback_data=f"adm:resolve:decide:freelancer:{contract_id}"),
        InlineKeyboardButton("⚖️ تقسیم توافقی درصد (Split)", callback_data=f"adm:resolve:split:{contract_id}")
    )
    markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="adm:disputes"))
    return markup

def get_dispute_room_link_keyboard(room_link: str) -> InlineKeyboardMarkup:
    """کیبورد حاوی لینک اتاق داوری برای طرفین"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⚖️ ورود به اتاق حل اختلاف (Mianji Room)", url=room_link))
    return markup

def get_project_admin_review_inline(contract_id: str, seller_id: int) -> InlineKeyboardMarkup:
    """
    پنل مدیریت جهت بررسی فایل‌های پروژه تحویلی توسط ادمین.
    رفع باگ اساسی: نسخهٔ قبلی این دکمه‌ها با پیشوند project_approve_ و
    project_reject_reason_ ساخته می‌شدند اما در admin.py هیچ هندلری برای
    همین پیشوندها وجود نداشت (هندلر admin_approve_project_/admin_reject_project_
    ثبت شده بود). حالا هر دو دکمه در فضای‌نام adm: هستند و روتر مرکزی آن‌ها را
    مستقیم به _handle_project_approval می‌فرستد.
    """
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ تایید کیفی پروژه", callback_data=f"adm:project:appr:{contract_id}"),
        InlineKeyboardButton("⚠️ رد پروژه و اعلام علت", callback_data=f"adm:project:rej:{contract_id}")
    )
    markup.add(
        InlineKeyboardButton("📁 پرونده کامل معامله", callback_data=f"adm:case:{contract_id}")
    )
    return markup

def get_channel_post_draft_inline(has_buttons: bool = False) -> InlineKeyboardMarkup:
    """
    کیبورد مرحلهٔ ساخت پست شیشه‌ای کانال، بعد از دریافت محتوای اصلی (متن/عکس/ویدیو).
    ادمین می‌تواند دکمهٔ شیشه‌ای (متن + لینک) اضافه کند یا پست را همان‌طور که هست
    (با یا بدون دکمه‌های اضافه‌شده) برای کانال میانجی ارسال کند.
    """
    markup = InlineKeyboardMarkup(row_width=1)
    finish_label = "🚀 اتمام و ارسال به کانال" if has_buttons else "🚀 ارسال بدون دکمه"
    markup.add(InlineKeyboardButton("➕ افزودن دکمه شیشه‌ای", callback_data="adm:chpost:addmore"))
    markup.add(InlineKeyboardButton("👁 پیش‌نمایش پست", callback_data="adm:chpost:preview"))
    markup.add(InlineKeyboardButton(finish_label, callback_data="adm:chpost:finish"))
    markup.add(InlineKeyboardButton("❌ لغو", callback_data="adm:chpost:cancel"))
    return markup


def build_channel_post_markup(buttons: list) -> InlineKeyboardMarkup:
    """ساخت کیبورد شیشه‌ای نهایی پست کانال از روی لیست دکمه‌های [{'text':..., 'url':...}, ...]"""
    markup = InlineKeyboardMarkup(row_width=1)
    for btn in buttons or []:
        url = btn.get("url", "").strip()
        if not url:
            continue
        # اطمینان از معتبر بودن URL
        if not (url.startswith("https://") or url.startswith("http://") or url.startswith("tg://")):
            url = "https://" + url
        markup.add(InlineKeyboardButton(btn.get("text", "🔗 لینک"), url=url))
    return markup


def get_raw_contract_category_inline() -> InlineKeyboardMarkup:
    """انتخاب حوزه قرارداد خام برای ادمین"""
    markup = InlineKeyboardMarkup(row_width=2)
    for label, code in [
        ("💻 برنامه‌نویسی (DEV)", "DEV"),
        ("🎨 طراحی و گرافیک (DS)", "DS"),
        ("📝 تولید محتوا (CNT)", "CNT"),
        ("🎓 مشاوره و آموزش (CNS)", "CNS"),
        ("🌐 خدمات تجاری (TRD)", "TRD"),
        ("📦 سایر (GEN)", "GEN"),
    ]:
        markup.add(InlineKeyboardButton(label, callback_data=f"adm:raw_cat:{code}"))
    markup.add(InlineKeyboardButton("❌ لغو", callback_data="adm:raw_cancel"))
    return markup


def get_blacklist_action_inline(target_user_id: int, is_blacklisted: bool) -> InlineKeyboardMarkup:
    """دکمه‌های افزودن/حذف از لیست سیاه + مدیریت موجودی کیف‌پول"""
    markup = InlineKeyboardMarkup(row_width=2)
    if is_blacklisted:
        markup.add(InlineKeyboardButton("✅ حذف از لیست سیاه", callback_data=f"adm:bl:remove:{target_user_id}"))
    else:
        markup.add(InlineKeyboardButton("🚫 افزودن به لیست سیاه", callback_data=f"adm:bl:add:{target_user_id}"))
    markup.add(
        InlineKeyboardButton("➕ افزایش موجودی", callback_data=f"adm:wallet_adj:add:{target_user_id}"),
        InlineKeyboardButton("➖ کاهش موجودی", callback_data=f"adm:wallet_adj:sub:{target_user_id}")
    )
    markup.add(InlineKeyboardButton("📋 تاریخچه معاملات", callback_data=f"user_deals_{target_user_id}_0"))
    return markup


def get_blacklist_panel_inline() -> InlineKeyboardMarkup:
    """
    منوی اصلی بخش لیست سیاه — با دکمه «افزودن مستقیم»
    که ادمین می‌تواند بدون جستجوی کاربر، مستقیماً آیدی تلگرام را وارد کند.
    """
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔍 مشاهده لیست کاربران مسدود", callback_data="adm:bl_list:0"))
    markup.add(InlineKeyboardButton("➕ افزودن مستقیم به لیست سیاه", callback_data="adm:bl_add_new"))
    markup.add(InlineKeyboardButton("🏠 بازگشت به پنل مدیریت", callback_data="adm:home"))
    return markup


def get_settings_inline(settings: dict) -> InlineKeyboardMarkup:
    """
    کیبورد تنظیمات زنده سیستم با نمایش مقادیر فعلی روی دکمه‌ها.
    هر دکمه با کلیک، ورود مقدار جدید را از ادمین می‌خواهد.
    """
    commission = settings.get("commission_percent", "؟")
    affiliate = settings.get("affiliate_share_percent", "؟")
    maintenance = settings.get("maintenance_mode", "0")
    maintenance_fa = "🔴 فعال" if str(maintenance) == "1" else "🟢 غیرفعال"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(
        f"💹 کارمزد معاملات: {commission}٪  (ویرایش)",
        callback_data="adm:setting:edit:commission_percent"
    ))
    markup.add(InlineKeyboardButton(
        f"🤝 سهم سفیران از کارمزد: {affiliate}٪  (ویرایش)",
        callback_data="adm:setting:edit:affiliate_share_percent"
    ))
    markup.add(InlineKeyboardButton(
        f"🛠 حالت تعمیرات: {maintenance_fa}  (سوئیچ)",
        callback_data="adm:setting:toggle_maint"
    ))
    markup.add(InlineKeyboardButton("🔄 بروزرسانی", callback_data="adm:settings"))
    markup.add(InlineKeyboardButton("🏠 بازگشت به پنل مدیریت", callback_data="adm:home"))
    return markup

def get_more_contracts_inline(next_offset: int, category: str = "all") -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای برای بارگذاری معاملات بیشتر در همان دسته‌بندی"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📜 نمایش معاملات بیشتر", callback_data=f"contracts_more:{category}:{next_offset}"))
    return markup

def get_milestones_inline(contract_id: str, milestones: list, is_employer: bool) -> InlineKeyboardMarkup:
    """دکمه‌های هوشمند مدیریت مراحل پرداخت (پرداخت، آزادسازی، مشاهده وضعیت)"""
    markup = InlineKeyboardMarkup(row_width=1)
    for idx, ms in enumerate(milestones):
        title = ms.get("title", f"مرحله {idx+1}")
        amt = float(ms.get("amount", 0))
        st = ms.get("status", "pending")
        
        if st == "released":
            btn_text = f"✅ {title} ({amt:,.0f} تومان) - تسویه شد"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"ms_detail_{contract_id}_{idx}"))
        elif st == "awaiting_payment":
            if is_employer:
                btn_text = f"💳 پرداخت {title} ({amt:,.0f} تومان)"
                markup.add(InlineKeyboardButton(btn_text, callback_data=f"msp_pay_{contract_id}_{idx}"))
            else:
                btn_text = f"🔴 {title} ({amt:,.0f} تومان) - در انتظار واریز"
                markup.add(InlineKeyboardButton(btn_text, callback_data=f"ms_detail_{contract_id}_{idx}"))
        elif st in ["paid", "active", "in_progress"]:
            if is_employer:
                btn_text = f"🔓 آزادسازی {title} ({amt:,.0f} تومان)"
                markup.add(InlineKeyboardButton(btn_text, callback_data=f"release_ms_{contract_id}_{idx}"))
            else:
                btn_text = f"🟢 {title} ({amt:,.0f} تومان) - آماده انجام"
                markup.add(InlineKeyboardButton(btn_text, callback_data=f"ms_detail_{contract_id}_{idx}"))
        else:
            btn_text = f"🔒 {title} ({amt:,.0f} تومان) - غیرفعال"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"ms_detail_{contract_id}_{idx}"))
            
    markup.add(InlineKeyboardButton("⬅️ بازگشت به کارت معامله", callback_data=f"view_contract_{contract_id}"))
    return markup
# بخش‌های تغییر یافتهٔ keyboards.py

# ====================================================
# تغییر: get_admin_panel_keyboard - حذف دکمه‌های غیرموثر
# ====================================================

def get_admin_panel_keyboard(quick_stats: dict = None, is_owner: bool = False) -> InlineKeyboardMarkup:
    """
    پنل شیشه‌ای اصلی مدیریت (نسخه بازطراحی شده).
    """
    qs = quick_stats or {}
    disputes_badge = f" 🔴{qs.get('disputes', 0)}" if qs.get("disputes") else ""
    withdrawals_badge = f" 🔴{qs.get('withdrawals', 0)}" if qs.get("withdrawals") else ""
    deal_receipts_badge = f" 🔴{qs.get('deal_receipts', 0)}" if qs.get("deal_receipts") else ""
    deposit_receipts_badge = f" 🔴{qs.get('deposit_receipts', 0)}" if qs.get("deposit_receipts") else ""

    markup = InlineKeyboardMarkup(row_width=2)
    
    # ردیف ۱: آمار و داوری
    markup.add(
        InlineKeyboardButton("📊 آمار کلی", callback_data="adm:stats"),
        InlineKeyboardButton(f"⚖️ پرونده‌های داوری{disputes_badge}", callback_data="adm:disputes")
    )
    
    # ردیف ۲: فیش‌های معامله و شارژ حساب (تراکنش‌های معلق)
    markup.add(
        InlineKeyboardButton(f"🧾 فیش‌های معامله{deal_receipts_badge}", callback_data="adm:pending_receipts"),
        InlineKeyboardButton(f"💰 تراکنش‌های معلق{deposit_receipts_badge}", callback_data="adm:pending_deposits")
    )
    
    # ردیف ۳: برداشت و نمودار تراکنش‌ها
    markup.add(
        InlineKeyboardButton(f"💸 درخواست‌های برداشت{withdrawals_badge}", callback_data="adm:withdrawals"),
        InlineKeyboardButton("📊 نمودار تراکنش‌ها", callback_data="adm:stats_chart")
    )
    
    # ردیف ۴: لیست سیاه و پیام همگانی
    markup.add(
        InlineKeyboardButton("🚫 لیست سیاه", callback_data="adm:blacklist"),
        InlineKeyboardButton("📢 پیام همگانی", callback_data="adm:broadcast")
    )
    
    # ردیف ۵: پیام مستقیم و مدیریت سفیران
    markup.add(
        InlineKeyboardButton("✉️ پیام مستقیم", callback_data="adm:direct_msg"),
        InlineKeyboardButton("🤝 مدیریت سفیران", callback_data="adm:ambassadors")
    )
    
    # ردیف ۶: بونوس و گزارش مالی
    markup.add(
        InlineKeyboardButton("🎁 بونوس ماهانه", callback_data="adm:pay_amb_bonus"),
        InlineKeyboardButton("📊 گزارش مالی (CSV)", callback_data="adm:finance_report")
    )

    # ردیف ۷: تنظیمات سیستم (فقط برای مالک) و پست کانال
    if is_owner:
        markup.add(
            InlineKeyboardButton("⚙️ تنظیمات سیستم", callback_data="adm:settings"),
            InlineKeyboardButton("📮 پست شیشه‌ای کانال", callback_data="adm:channel_post")
        )
    else:
        markup.add(
            InlineKeyboardButton("📮 پست شیشه‌ای کانال", callback_data="adm:channel_post")
        )
    
    return markup


# ====================================================
# اضافه: کیبورد برای ویرایش احراز هویت (در user.py)
# ====================================================

def get_identity_verification_keyboard() -> InlineKeyboardMarkup:
    """کیبورد برای بخش احراز هویت"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✏️ تغییر نام و فامیل", callback_data="edit_fullname"),
        InlineKeyboardButton("✏️ تغییر کد ملی", callback_data="edit_national_id"),
        InlineKeyboardButton("❌ بازگشت", callback_data="back_to_menu")
    )
    return markup


# ====================================================
# پنل مذاکره نفر دوم (Multi-field Negotiation Panel)
# ====================================================

def get_negotiation_panel_inline(contract_id: str, selected: list = None) -> InlineKeyboardMarkup:
    """
    پنل شیشه‌ای انتخاب فیلدهای قابل تغییر توسط نفر دوم قرارداد.
    فیلدهای انتخاب‌شده با ✅ نمایش داده می‌شوند.
    """
    selected = selected or []
    markup = InlineKeyboardMarkup(row_width=1)

    fields = [
        ("amount",      "💰 مبلغ معامله"),
        ("deadline",    "⏳ مهلت تحویل"),
        ("free_edits",  "🔄 تعداد ویرایش رایگان"),
        ("description", "📝 شرح تعهدات / توضیحات"),
    ]

    for key, label in fields:
        check = "✅ " if key in selected else "◻️ "
        markup.add(InlineKeyboardButton(
            f"{check}{label}",
            callback_data=f"neg_toggle_{contract_id}_{key}"
        ))

    markup.add(Spacer := InlineKeyboardButton("─────────────────", callback_data="none"))

    if selected:
        markup.add(InlineKeyboardButton(
            "➡️ ادامه و وارد کردن مقادیر پیشنهادی",
            callback_data=f"neg_proceed_{contract_id}"
        ))

    markup.add(InlineKeyboardButton(
        "✍️ بدون تغییر — امضا می‌کنم",
        callback_data=f"sign_contract_{contract_id}"
    ))
    markup.add(InlineKeyboardButton(
        "❌ رد قرارداد",
        callback_data=f"reject_contract_{contract_id}"
    ))
    return markup


def get_neg_confirm_inline(contract_id: str) -> InlineKeyboardMarkup:
    """پنل تایید پیشنهاد مذاکره + امضای همزمان"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(
            "✍️ تایید پیشنهاد و امضای قرارداد",
            callback_data=f"neg_sign_{contract_id}"
        ),
        InlineKeyboardButton(
            "✏️ ویرایش مجدد پیشنهاد",
            callback_data=f"neg_redo_{contract_id}"
        ),
        InlineKeyboardButton(
            "❌ انصراف",
            callback_data=f"neg_cancel_{contract_id}"
        )
    )
    return markup


def get_neg_first_party_inline(contract_id: str) -> InlineKeyboardMarkup:
    """دکمه‌های نمایش داده‌شده به نفر اول پس از دریافت پیشنهاد مذاکره"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(
            "✅ موافقم — امضا می‌کنم",
            callback_data=f"neg_accept_{contract_id}"
        ),
        InlineKeyboardButton(
            "🔄 پیشنهاد متقابل می‌دهم",
            callback_data=f"bargain_{contract_id}"
        ),
        InlineKeyboardButton(
            "❌ رد و لغو معامله",
            callback_data=f"reject_contract_{contract_id}"
        )
    )
    return markup

# ====================================================
# کیبوردهای پخش پست سفیران (Ambassador Channel Broadcaster)
# ====================================================

def get_ambassador_broadcaster_inline() -> InlineKeyboardMarkup:
    """دکمه‌های داشبورد ساخت پست سفیران"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📢 ساخت و ارسال پست آماده", callback_data="amb_create_post"),
        InlineKeyboardButton("🔙 بازگشت به پنل سفیران", callback_data="amb_back_panel")
    )
    return markup


def get_broadcaster_channel_input_inline() -> InlineKeyboardMarkup:
    """کیبورد برای صفحهٔ دریافت آیدی کانال"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("❌ انصراف", callback_data="amb_cancel_broadcast")
    )
    return markup


def get_broadcaster_content_input_inline() -> InlineKeyboardMarkup:
    """کیبورد برای صفحهٔ دریافت محتوا"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("❌ انصراف", callback_data="amb_cancel_broadcast")
    )
    return markup


def get_broadcaster_button_text_inline() -> InlineKeyboardMarkup:
    """کیبورد برای صفحهٔ دریافت متن دکمه"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ استفاده از عنوان پیش‌فرض", callback_data="amb_use_default_button"),
        InlineKeyboardButton("❌ انصراف", callback_data="amb_cancel_broadcast")
    )
    return markup


def get_broadcaster_preview_inline() -> InlineKeyboardMarkup:
    """کیبورد پیش‌نمایش پست با دکمهٔ تایید و انصراف"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ تایید و ارسال به کانال", callback_data="amb_confirm_send"),
        InlineKeyboardButton("✏️ ویرایش متن دکمه", callback_data="amb_edit_button_text"),
        InlineKeyboardButton("❌ انصراف", callback_data="amb_cancel_broadcast")
    )
    return markup

def get_deliver_project_keyboard(contract_id: str) -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای تحویل پروژه برای پیام تایید واریز"""
    markup = InlineKeyboardMarkup(row_width=1)
    url = f"https://t.me/{config.BOT_USERNAME}?start=deliver_{contract_id}"
    markup.add(
        InlineKeyboardButton("🚀 تحویل پروژه / ارسال فایل", url=url),
        InlineKeyboardButton("📖 مشاهده تعهدات قرارداد", callback_data=f"view_contract_terms:{contract_id}")
    )
    return markup

def get_milestone_delivery_list_inline(contract_id: str, milestones: list) -> InlineKeyboardMarkup:
    """لیست مراحل برای تحویل توسط مجری"""
    markup = InlineKeyboardMarkup(row_width=1)
    for i, ms in enumerate(milestones):
        status = ms.get("status", "pending")
        if status in ["paid", "active", "in_progress"]:
            btn_text = f"🚀 تحویل مرحله {i+1}: {ms.get('title')}"
            markup.add(InlineKeyboardButton(btn_text, callback_data=f"msp_deliver_{contract_id}_{i}"))
    markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="main_menu"))
    return markup

def get_freelancer_reupload_keyboard(contract_id: str) -> InlineKeyboardMarkup:
    """دکمه شیشه‌ای ارسال مجدد پروژه برای زمانی که پروژه رد شده و ویرایش رایگان باقی مانده است"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📤 ارسال مجدد پروژه / اصلاحات", callback_data=f"deliver_{contract_id}"))
    markup.add(InlineKeyboardButton("⚖️ ثبت اعتراض و درخواست داوری", callback_data=f"open_dispute_{contract_id}"))
    return markup

def get_freelancer_extra_edit_keyboard(contract_id: str) -> InlineKeyboardMarkup:
    """کیبورد برای زمانی که ویرایش رایگان تمام شده و مجری باید قیمت بدهد یا داوری بخواهد"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⚖️ ثبت اعتراض و درخواست داوری", callback_data=f"open_dispute_{contract_id}"))
    markup.add(InlineKeyboardButton("❌ انصراف", callback_data="back_to_menu"))
    return markup
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

def get_auth_keyboard() -> InlineKeyboardMarkup:
    """کیبورد ورود و ثبت‌نام"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("ورود به حساب کاربری", callback_data="auth_login"),
        InlineKeyboardButton("ایجاد اکانت جدید", callback_data="auth_register"),
        InlineKeyboardButton("🔄 رمزم را فراموش کرده‌ام", callback_data="auth_recover"),
        InlineKeyboardButton("💬 پشتیبانی آنلاین", url="https://t.me/Mianji_Support")
    )
    return markup

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد انصراف"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("❌ انصراف"))
    return markup
