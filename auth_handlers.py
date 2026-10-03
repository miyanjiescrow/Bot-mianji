import logging
import hashlib
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db

logger = logging.getLogger("Miyanji_Auth")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# --- UI Helpers ---
def get_guest_landing_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود با شماره و رمز عبور", callback_data="auth_login"),
        InlineKeyboardButton("📝 ثبت‌نام", callback_data="auth_register"),
        InlineKeyboardButton("❓ پشتیبانی", callback_data="auth_help")
    )
    return markup

def get_cancel_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ انصراف", callback_data="auth_cancel"))
    return markup

def show_guest_landing(bot, chat_id, edit_id=None):
    text = "به پلتفرم میانجی خوش آمدید."
    if edit_id: bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_guest_landing_keyboard())
    else: bot.send_message(chat_id, text, reply_markup=get_guest_landing_keyboard())

# --- Handlers ---
def register_auth_handlers(bot: TeleBot):

    @bot.message_handler(commands=['start'])
    def start_handler(message: Message):
        try:
            user_id = message.from_user.id
            db.clear_user_state(user_id)
            user = db.get_user(user_id)
            if user and user.get('is_verified'):
                import user as user_module
                user_module.show_main_menu(bot, message.chat.id)
            else:
                show_guest_landing(bot, message.chat.id)
        except Exception as e:
            logger.error(f"Error in start_handler: {e}")
            bot.send_message(message.chat.id, "خطای سیستمی رخ داد.")

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_cancel"])
    def auth_callback(call: CallbackQuery):
        try:
            user_id = call.from_user.id
            bot.answer_callback_query(call.id)
            
            if call.data == "auth_register":
                db.set_user_state_safe(user_id, "AWAITING_PHONE", username=call.from_user.username)
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل", request_contact=True))
                bot.send_message(call.message.chat.id, "شماره موبایل را وارد یا ارسال کنید:", reply_markup=markup)
                
            elif call.data == "auth_login":
                db.set_user_state_safe(user_id, "AWAITING_LOGIN_PHONE", username=call.from_user.username)
                bot.edit_message_text("📱 شماره موبایل خود را وارد کنید:", call.message.chat.id, call.message.message_id, reply_markup=get_cancel_keyboard())

            elif call.data == "auth_cancel":
                db.clear_user_state(user_id)
                show_guest_landing(bot, call.message.chat.id, edit_id=call.message.message_id)
        except Exception as e:
            logger.error(f"Error in auth_callback: {e}")

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] in ["AWAITING_PHONE", "AWAITING_LOGIN_PHONE"])
    def phone_handler(message: Message):
        try:
            phone = message.contact.phone_number if message.contact else message.text
            norm_phone = db.normalize_phone_number(phone)
            if not norm_phone:
                bot.send_message(message.chat.id, "⚠️ شماره نامعتبر است!")
                return
            state, _ = db.get_user_state(message.from_user.id)
            if state == "AWAITING_PHONE" and db.check_phone_exists(norm_phone):
                bot.send_message(message.chat.id, "⚠️ این شماره قبلاً ثبت شده است!")
                return
            db.set_user_state(message.from_user.id, state.replace("PHONE", "PASSWORD"), {"phone": norm_phone})
            bot.send_message(message.chat.id, "🔑 رمز عبور را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        except Exception as e:
            logger.error(f"Error in phone_handler: {e}")

    def password_state_filter(message: Message):
        state, _ = db.get_user_state(message.from_user.id)
        logger.info(f"DEBUG: User {message.from_user.id} sending password. Current state: {state}")
        return state in ["AWAITING_NEW_PASSWORD", "AWAITING_LOGIN_PASSWORD"]

    @bot.message_handler(func=password_state_filter)
    def password_handler(message: Message):
        try:
            password = message.text.strip()
            state, data = db.get_user_state(message.from_user.id)
            phone = data.get("phone")
            
            if state == "AWAITING_LOGIN_PASSWORD":
                user = db.get_user_by_phone(phone)
                if not user or hash_password(password) != user.get('password_hash'):
                    bot.send_message(message.chat.id, "❌ شماره یا رمز اشتباه است!")
                    return
                db.link_telegram_id(phone, message.from_user.id)
                bot.send_message(message.chat.id, "✅ ورود موفق!")
                import user as user_module
                user_module.show_main_menu(bot, message.chat.id)
            else:
                db.set_user_state(message.from_user.id, "AWAITING_FULL_NAME", {"phone": phone, "password": hash_password(password)})
                bot.send_message(message.chat.id, "👤 نام و نام خانوادگی خود را وارد کنید:")
    def full_name_state_filter(message: Message):
        state, _ = db.get_user_state(message.from_user.id)
        return state == "AWAITING_FULL_NAME"

    @bot.message_handler(func=full_name_state_filter)
    def full_name_handler(message: Message):
        try:
            full_name = message.text.strip()
            user_id = message.from_user.id
            state, data = db.get_user_state(user_id)
            phone = data.get("phone")
            password_hash = data.get("password")
            
            if not phone or not password_hash:
                bot.send_message(message.chat.id, "❌ خطایی در اطلاعات ثبت‌نام رخ داد. لطفاً دوباره /start بزنید.")
                db.clear_user_state(user_id)
                return

            # ثبت نهایی کاربر در دیتابیس
            db.register_or_update_user(
                user_id=user_id,
                full_name=full_name,
                phone_number=phone,
                password_hash=password_hash,
                is_verified=True
            )
            
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "✅ ثبت‌نام با موفقیت کامل انجام شد و به حساب خود وارد شدید.")
            import user as user_module
            user_module.show_main_menu(bot, message.chat.id)
        except Exception as e:
            logger.error(f"Error in full_name_handler: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی در ثبت‌نام. لطفاً دوباره تلاش کنید.")
