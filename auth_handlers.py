import logging
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import hashlib

logger = logging.getLogger("Miyanji_Auth")

# --- UI Components ---
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

def get_main_dashboard_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💰 لیست معاملات من", callback_data="my_trades"))
    markup.add(InlineKeyboardButton("👤 پروفایل کاربری", callback_data="profile"))
    return markup

# --- Auth Helpers ---
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# --- Auth Handlers ---
def register_auth_handlers(bot: TeleBot):

    @bot.message_handler(commands=['start'])
    def start_handler(message: Message):
        user_id = message.from_user.id
        db.clear_user_state(user_id)
        
        user = db.get_user(user_id)
        if user and user.get('is_verified'):
            show_main_dashboard(bot, message.chat.id)
        else:
            show_guest_landing(bot, message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_help", "auth_cancel"])
    def auth_callback(call: CallbackQuery):
        user_id = call.from_user.id
        bot.answer_callback_query(call.id)
        
        if call.data == "auth_register":
            db.set_user_state(user_id, "AWAITING_PHONE")
            markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
            markup.add(KeyboardButton("📱 ارسال شماره موبایل", request_contact=True))
            bot.send_message(call.message.chat.id, "لطفاً برای ثبت‌نام، شماره موبایل خود را تایید کنید:", reply_markup=markup)
            
        elif call.data == "auth_login":
            db.set_user_state(user_id, "AWAITING_LOGIN_PHONE")
            bot.edit_message_text("📱 شماره موبایل خود را وارد کنید:", call.message.chat.id, call.message.message_id, reply_markup=get_cancel_keyboard())

        elif call.data == "auth_cancel":
            db.clear_user_state(user_id)
            show_guest_landing(bot, call.message.chat.id, edit_id=call.message.message_id)

    @bot.message_handler(content_types=['contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AWAITING_PHONE")
    def contact_handler(message: Message):
        user_id = message.from_user.id
        phone = message.contact.phone_number
        if db.check_phone_exists(phone):
            bot.send_message(message.chat.id, "⚠️ این شماره موبایل قبلاً ثبت شده است! لطفا وارد شوید.", reply_markup=get_guest_landing_keyboard())
            return
        
        db.set_user_state(user_id, "AWAITING_NEW_PASSWORD", {"phone": phone})
        bot.send_message(message.chat.id, "🔑 لطفاً یک رمز عبور انتخاب کنید:", reply_markup=ReplyKeyboardRemove())

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AWAITING_NEW_PASSWORD")
    def new_password_handler(message: Message):
        user_id = message.from_user.id
        password = message.text.strip()
        _, data = db.get_user_state(user_id)
        
        db.set_user_state(user_id, "AWAITING_FULL_NAME", {"phone": data['phone'], "password": hash_password(password)})
        bot.send_message(message.chat.id, "👤 نام و نام خانوادگی خود را وارد کنید:")

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AWAITING_FULL_NAME")
    def full_name_handler(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()
        _, data = db.get_user_state(user_id)
        
        # Save to DB
        db.register_or_update_user(
            user_id=user_id,
            full_name=full_name,
            phone_number=data['phone'],
            password_hash=data['password'],
            is_verified=True
        )
        
        db.clear_user_state(user_id)
        bot.send_message(message.chat.id, "✅ ثبت‌نام با موفقیت انجام شد.")
        show_main_dashboard(bot, message.chat.id)

    # ... (Login flow similarly implemented with AWAITING_LOGIN_PHONE and AWAITING_LOGIN_PASSWORD)

# --- UI Helpers ---
def show_guest_landing(bot, chat_id, edit_id=None):
    text = "به پلتفرم میانجی خوش آمدید."
    if edit_id:
        bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_guest_landing_keyboard())
    else:
        bot.send_message(chat_id, text, reply_markup=get_guest_landing_keyboard())

def show_main_dashboard(bot, chat_id, edit_id=None):
    text = "🏠 **پنل کاربری میانجی**"
    if edit_id:
        bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_main_dashboard_keyboard(), parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=get_main_dashboard_keyboard(), parse_mode="Markdown")
