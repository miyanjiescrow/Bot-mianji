import logging
import hashlib
import unicodedata
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import keyboards as kb
from config import config

logger = logging.getLogger("Miyanji_Auth")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def normalize_phone(phone_str: str) -> str:
    if not phone_str: return ""
    normalized = unicodedata.normalize('NFKC', phone_str)
    digits = "".join([c for c in normalized if c.isdigit()])
    if digits.startswith(("98", "0098")):
        digits = "0" + digits[2:] if len(digits) > 10 else digits[2:]
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    return digits if len(digits) == 11 else ""

# --- UI Keyboards ---
def get_guest_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود به حساب کاربری", callback_data="auth_login"),
        InlineKeyboardButton("📝 ثبت‌نام در سامانه", callback_data="auth_register"),
        InlineKeyboardButton("❓ پشتیبانی", callback_data="auth_help")
    )
    return markup

def get_cancel_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ انصراف و بازگشت", callback_data="auth_cancel"))
    return markup

def show_guest_landing(bot, chat_id, edit_id=None):
    text = "🔒 **پلتفرم امن میانجی (Escrow)**\n\nبرای استفاده از امکانات ربات، لطفاً وارد حساب خود شوید یا ثبت‌نام کنید:"
    if edit_id:
        try:
            bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_guest_keyboard(), parse_mode="Markdown")
        except:
            bot.send_message(chat_id, text, reply_markup=get_guest_keyboard(), parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=get_guest_keyboard(), parse_mode="Markdown")

def show_main_dashboard(bot, chat_id, user_id=None):
    text = "🏠 **پنل کاربری میانجی**\n\nشما با موفقیت وارد سیستم شدید. از منوی زیر استفاده کنید:"
    is_admin = False
    if user_id:
        is_admin = (user_id == config.OWNER_ID or user_id in getattr(config, 'ADMIN_IDS', []))
    markup = kb.get_main_menu(is_admin=is_admin, is_verified=True)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

# --- Main Handlers Registration ---
def register_auth_handlers(bot: TeleBot):

    @bot.message_handler(commands=['start'])
    def start_handler(message: Message):
        try:
            user_id = message.from_user.id
            db.clear_user_state(user_id)
            
            user = db.get_user(user_id)
            if user and user.get('is_verified'):
                show_main_dashboard(bot, message.chat.id, user_id)
            else:
                show_guest_landing(bot, message.chat.id)
        except Exception as e:
            logger.error(f"Error in start_handler: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی رخ داد. لطفاً دوباره تلاش کنید.")

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_help", "auth_cancel"])
    def auth_callbacks(call: CallbackQuery):
        try:
            user_id = call.from_user.id
            bot.answer_callback_query(call.id)
            chat_id = call.message.chat.id
            
            if call.data == "auth_cancel":
                db.clear_user_state(user_id)
                show_guest_landing(bot, chat_id, edit_id=call.message.message_id)
                
            elif call.data == "auth_register":
                db.set_user_state(user_id, "REG_PHONE", {})
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل من", request_contact=True))
                markup.add(KeyboardButton("❌ انصراف"))
                bot.send_message(chat_id, "📝 **مرحله ۱ از ۳: ثبت‌نام**\n\nلطفاً شماره موبایل خود را با استفاده از دکمه زیر ارسال کنید یا به صورت دستی به شکل متن وارد نمایید (مثال: 09123456789):", reply_markup=markup, parse_mode="Markdown")
                
            elif call.data == "auth_login":
                db.set_user_state(user_id, "LOGIN_PHONE", {})
                bot.edit_message_text("🔑 **ورود به حساب کاربری**\n\nلطفاً شماره موبایل ثبت‌شده خود را وارد کنید:", chat_id, call.message.message_id, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
                
            elif call.data == "auth_help":
                bot.answer_callback_query(call.id, "برای راهنمایی با پشتیبانی میانجی در ارتباط باشید.", show_alert=True)
        except Exception as e:
            logger.error(f"Error in auth_callbacks: {e}", exc_info=True)

    # --- REGISTRATION FLOW ---
    @bot.message_handler(content_types=['text', 'contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "REG_PHONE")
    def reg_phone_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            raw_phone = message.contact.phone_number if message.contact else message.text
            phone = normalize_phone(raw_phone)
            
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل وارد شده نامعتبر است. لطفاً فرمت صحیح را وارد کنید (مثال: 09123456789):")
                return

            existing = db.get_user_by_phone(phone)
            if existing:
                bot.send_message(message.chat.id, "⚠️ این شماره موبایل قبلاً در سیستم ثبت‌نام کرده است!\nلطفاً از بخش ورود وارد حساب خود شوید.", reply_markup=get_guest_keyboard())
                db.clear_user_state(user_id)
                return

            db.set_user_state(user_id, "REG_PASSWORD", {"phone": phone})
            bot.send_message(message.chat.id, "🔑 **مرحله ۲ از ۳: انتخاب رمز عبور**\n\nلطفاً یک رمز عبور امن برای حساب خود وارد کنید:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in reg_phone_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "REG_PASSWORD")
    def reg_password_step(message: Message):
        try:
            user_id = message.from_user.id
            password = message.text.strip()
            if len(password) < 4:
                bot.send_message(message.chat.id, "⚠️ رمز عبور باید حداقل ۴ کاراکتر باشد. دوباره وارد کنید:")
                return

            _, data = db.get_user_state(user_id)
            data["password_hash"] = hash_password(password)
            
            db.set_user_state(user_id, "REG_NAME", data)
            bot.send_message(message.chat.id, "👤 **مرحله ۳ از ۳: نام و نام خانوادگی**\n\nلطفاً نام و نام خانوادگی خود را وارد کنید:", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in reg_password_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "REG_NAME")
    def reg_name_step(message: Message):
        try:
            user_id = message.from_user.id
            full_name = message.text.strip()
            _, data = db.get_user_state(user_id)
            
            phone = data.get("phone")
            pwd_hash = data.get("password_hash")

            if not phone or not pwd_hash:
                bot.send_message(message.chat.id, "❌ خطایی در اطلاعات رخ داد. لطفاً دوباره /start بزنید.")
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            db.register_or_update_user(
                user_id=user_id,
                username=message.from_user.username,
                full_name=full_name,
                phone_number=phone,
                password_hash=pwd_hash,
                is_verified=True
            )

            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "✅ **ثبت‌نام با موفقیت کامل انجام شد!**", parse_mode="Markdown")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in reg_name_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی در ثبت‌نام. لطفاً دوباره تلاش کنید.")

    # --- LOGIN FLOW ---
    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "LOGIN_PHONE")
    def login_phone_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            phone = normalize_phone(message.text)
            
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل نامعتبر است. لطفاً به صورت صحیح وارد کنید:")
                return

            user = db.get_user_by_phone(phone)
            if not user:
                bot.send_message(message.chat.id, "⚠️ حسابی با این شماره تلفن یافت نشد!\nلطفاً ابتدا ثبت‌نام کنید.", reply_markup=get_guest_keyboard())
                db.clear_user_state(user_id)
                return

            db.set_user_state(user_id, "LOGIN_PASSWORD", {"phone": phone})
            bot.send_message(message.chat.id, "🔑 لطفاً رمز عبور حساب خود را وارد کنید:")
        except Exception as e:
            logger.error(f"Error in login_phone_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "LOGIN_PASSWORD")
    def login_password_step(message: Message):
        try:
            user_id = message.from_user.id
            password = message.text.strip()
            _, data = db.get_user_state(user_id)
            phone = data.get("phone")

            user = db.get_user_by_phone(phone)
            if not user or user.get("password_hash") != hash_password(password):
                bot.send_message(message.chat.id, "❌ رمز عبور اشتباه است! لطفاً دوباره تلاش کنید:")
                return

            db.link_telegram_id(phone, user_id)
            db.clear_user_state(user_id)
            
            bot.send_message(message.chat.id, "✅ **ورود موفقیت‌آمیز بود!**", parse_mode="Markdown")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in login_password_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی در ورود. لطفاً دوباره تلاش کنید.")
