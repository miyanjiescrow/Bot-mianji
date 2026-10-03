import logging
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from telebot.handler_backends import State, StatesGroup

import database as db
import utils

logger = logging.getLogger("AdminSettings")

# 1. تعریف حالات (States) - در این نسخه از register_next_step_handler استفاده می‌شود
# اما کلاس را برای مراجعات احتمالی آینده نگه می‌داریم
class AdminSettingsStates(StatesGroup):
    waiting_for_value = State()

# مقادیر پیش‌فرض در صورتی که دیتابیس خالی باشد
DEFAULT_SETTINGS = {
    "commission_rate": 10.0,      # درصد کارمزد سیستم
    "affiliate_rate": 15.0,       # درصد پورسانت سفیر از کارمزد
    "arbitration_fee": 50000,     # هزینه ثابت داوری (تومان)
    "min_withdrawal": 100000      # حداقل مبلغ برداشت (تومان)
}

def is_admin(user_id: int) -> bool:
    from config import config
    return user_id == config.OWNER_ID or user_id == config.ADMIN_ID or user_id in getattr(config, 'ADMIN_IDS', [])

def is_owner(user_id: int) -> bool:
    from config import config
    return user_id == config.OWNER_ID

def get_setting_value(key: str):
    """دریافت مقدار از دیتابیس یا مقدار پیش‌فرض"""
    return db.get_bot_setting(key, DEFAULT_SETTINGS.get(key))

def get_settings_keyboard():
    """تولید کیبورد منوی تنظیمات با مقادیر فعلی"""
    markup = InlineKeyboardMarkup(row_width=1)
    
    comm = get_setting_value("commission_rate")
    aff = get_setting_value("affiliate_rate")
    arb = get_setting_value("arbitration_fee")
    min_w = get_setting_value("min_withdrawal")

    markup.add(
        InlineKeyboardButton(f"📊 کارمزد سیستم: {comm}%", callback_data="set:commission_rate"),
        InlineKeyboardButton(f"🤝 پورسانت سفیر: {aff}%", callback_data="set:affiliate_rate"),
        InlineKeyboardButton(f"⚖️ هزینه داوری: {utils.format_currency(arb)}", callback_data="set:arbitration_fee"),
        InlineKeyboardButton(f"💸 حداقل برداشت: {utils.format_currency(min_w)}", callback_data="set:min_withdrawal"),
        InlineKeyboardButton("🏠 بازگشت به پنل مدیریت", callback_data="adm:home")
    )
    return markup

def register_admin_settings_handlers(bot: TeleBot):
    """ثبت هندلرهای مربوط به تنظیمات ادمین"""

    @bot.callback_query_handler(func=lambda call: call.data == "adm:settings")
    def show_settings_main(call: CallbackQuery):
        if not is_owner(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ این بخش فقط برای مالک اصلی در دسترس است.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "⚙️ **تنظیمات سیستمی میانجی (فقط مالک)**\n\n"
            "در این بخش می‌توانید پارامترهای اصلی محاسباتی ربات را تغییر دهید.\n"
            "برای ویرایش هر مورد، روی دکمه مربوطه کلیک کنید:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=get_settings_keyboard()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("set:"))
    def start_edit_setting(call: CallbackQuery):
        if not is_owner(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ عدم دسترسی!", show_alert=True)
            return
        
        key = call.data.replace("set:", "")
        bot.answer_callback_query(call.id)
        
        prompts = {
            "commission_rate": "لطفاً **درصد کارمزد سیستم** را وارد کنید (0 تا 100):",
            "affiliate_rate": "لطفاً **درصد سهم سفیر** از کارمزد را وارد کنید (0 تا 100):",
            "arbitration_fee": "لطفاً **مبلغ ثابت هزینه داوری** را به تومان وارد کنید:",
            "min_withdrawal": "لطفاً **حداقل مبلغ قابل برداشت** را به تومان وارد کنید:"
        }
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 انصراف و بازگشت", callback_data="adm:settings"))
        
        sent_msg = bot.edit_message_text(
            prompts.get(key, "لطفاً مقدار جدید را وارد کنید:"),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
        # استفاده از register_next_step_handler برای دریافت پاسخ قطعی
        bot.register_next_step_handler(sent_msg, process_setting_input, bot, key)

    def process_setting_input(message: Message, bot: TeleBot, key: str):
        admin_id = message.from_user.id
        
        # اگر کاربر دکمه‌ای زد یا دستور دیگری فرستاد، این مرحله لغو شود
        if not message.text or message.text.startswith('/'):
            # اگر دکمه‌های اینلاین فشرده شوند، هندلرهای خودشان اجرا می‌شوند
            # اینجا فقط اگر پیام متنی نباشد لغو می‌کنیم
            return

        raw_val = utils.fa_to_en_digits(message.text).strip()
        
        # ۱. اعتبارسنجی ورودی
        try:
            # حذف کاما و جداکننده‌ها برای مبالغ مالی
            clean_raw = raw_val.replace(",", "").replace("،", "")
            
            if key in ["commission_rate", "affiliate_rate"]:
                val = float(clean_raw)
                if not (0 <= val <= 100):
                    raise ValueError("خارج از محدوده 0-100")
            else:
                val = int(float(clean_raw)) # تبدیل به float و سپس int برای پشتیبانی از فرمت‌های علمی یا اعشاری اشتباه
                if val < 0:
                    raise ValueError("مقدار منفی مجاز نیست")
        except ValueError:
            sent_msg = bot.reply_to(message, "⚠️ **ورودی نامعتبر!**\nلطفاً یک عدد صحیح یا اعشاری معتبر وارد کنید (مثلاً 10 یا 5.5):")
            # تکرار مرحله در صورت ورود اشتباه
            bot.register_next_step_handler(sent_msg, process_setting_input, bot, key)
            return

        # ۲. ذخیره در دیتابیس
        db.set_bot_setting(key, val)
        # ثبت در لاگ فعالیت‌های ادمین (اگر تابعش وجود دارد)
        try:
            db.log_admin_action(admin_id, "change_setting", f"{key}={val}")
        except:
            pass
        
        # ۳. اطلاع‌رسانی موفقیت
        bot.send_message(
            message.chat.id,
            f"✅ تنظیمات **{key}** با موفقیت به مقدار `{val}` تغییر یافت.",
            parse_mode="Markdown",
            reply_markup=get_settings_keyboard()
        )

