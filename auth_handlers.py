import logging
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db

logger = logging.getLogger("Miyanji_Auth")

# --- UI Helpers ---
def get_guest_landing_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود با شماره و رمز عبور", callback_data="auth_login"),
        InlineKeyboardButton("📝 ثبت‌نام / حساب جدید", callback_data="auth_register"),
        InlineKeyboardButton("❓ پشتیبانی و راهنما", callback_data="auth_help")
    )
    return markup

def get_cancel_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ انصراف", callback_data="auth_cancel"))
    return markup

def show_main_dashboard(bot, chat_id, edit_id=None):
    text = "🏠 **پنل کاربری میانجی**"
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💰 لیست معاملات", callback_data="my_trades"),
               InlineKeyboardButton("👤 پروفایل", callback_data="profile"))
    if edit_id: bot.edit_message_text(text, chat_id, edit_id, reply_markup=markup, parse_mode="Markdown")
    else: bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

def show_guest_landing(bot, chat_id, edit_id=None):
    text = "به پلتفرم میانجی خوش آمدید."
    if edit_id: bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_guest_landing_keyboard())
    else: bot.send_message(chat_id, text, reply_markup=get_guest_landing_keyboard())

# --- Handlers ---
def register_auth_handlers(bot: TeleBot):

    @bot.message_handler(commands=['start'])
    def start_handler(message: Message):
        user_id = message.from_user.id
        db.clear_user_state(user_id)
        user = db.get_user(user_id)
        if user and user.get('is_verified'): show_main_dashboard(bot, message.chat.id)
        else: show_guest_landing(bot, message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_cancel"])
    def auth_callback(call: CallbackQuery):
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

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] in ["AWAITING_PHONE", "AWAITING_LOGIN_PHONE"])
    def phone_handler(message: Message):
        phone = message.contact.phone_number if message.contact else message.text
        norm_phone = db.normalize_phone_number(phone)
        
        if not norm_phone:
            bot.send_message(message.chat.id, "⚠️ شماره نامعتبر است!")
            return

        state, _ = db.get_user_state(message.from_user.id)
        if state == "AWAITING_PHONE" and db.check_phone_exists(norm_phone):
            bot.send_message(message.chat.id, "⚠️ این شماره قبلاً ثبت شده است! لطفا وارد شوید.")
            return

        db.set_user_state(message.from_user.id, state.replace("PHONE", "PASSWORD"), {"phone": norm_phone})
        bot.send_message(message.chat.id, "🔑 رمز عبور خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] in ["AWAITING_NEW_PASSWORD", "AWAITING_LOGIN_PASSWORD"])
    def password_handler(message: Message):
        user_id = message.from_user.id
        password = message.text.strip()
        state, data = db.get_user_state(user_id)
        phone = data.get("phone")
        
        if state == "AWAITING_LOGIN_PASSWORD":
            user = db.get_user_by_phone(phone)
            if not user:
                bot.send_message(message.chat.id, "⚠️ حسابی با این شماره یافت نشد!")
            elif db.hash_password(password) != user.get('password_hash'):
                bot.send_message(message.chat.id, "❌ رمز عبور اشتباه است!")
            else:
                db.link_telegram_id(phone, user_id)
                bot.send_message(message.chat.id, "✅ ورود موفق!")
                show_main_dashboard(bot, message.chat.id)
        else:
            db.set_user_state(user_id, "AWAITING_FULL_NAME", {"phone": phone, "password": db.hash_password(password)})
            bot.send_message(message.chat.id, "👤 نام و نام خانوادگی خود را وارد کنید:")

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AWAITING_FULL_NAME")
    def full_name_handler(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()
        _, data = db.get_user_state(user_id)
        
        # ثبت کاربر در دیتابیس (با استفاده از توابع موجود در database.py)
        db.register_or_update_user(
            user_id=user_id,
            full_name=full_name,
            phone_number=data['phone'],
            password_hash=data['password'],
            is_verified=True
        )
        db.clear_user_state(user_id)
        bot.send_message(message.chat.id, "✅ ثبت‌نام انجام شد.")
        show_main_dashboard(bot, message.chat.id)
