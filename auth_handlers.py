import logging
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db

logger = logging.getLogger("Miyanji_Auth")

# --- UI Components ---
def get_guest_landing_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود به حساب کاربری", callback_data="auth_login"),
        InlineKeyboardButton("📝 ثبت‌نام / حساب جدید", callback_data="auth_register"),
        InlineKeyboardButton("❓ راهنما و پشتیبانی", callback_data="auth_help")
    )
    return markup

def get_main_dashboard_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💰 لیست معاملات من", callback_data="my_trades"))
    markup.add(InlineKeyboardButton("👤 پروفایل کاربری", callback_data="profile"))
    return markup

# --- Auth Handlers ---
def register_auth_handlers(bot: TeleBot):

    @bot.message_handler(commands=['start'])
    def start_handler(message: Message):
        user_id = message.from_user.id
        db.set_user_state(user_id, "IDLE") # پاکسازی وضعیت
        
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
            markup.add(KeyboardButton("📱 ارسال و تایید شماره موبایل", request_contact=True))
            markup.add(KeyboardButton("❌ انصراف"))
            bot.send_message(call.message.chat.id, "لطفاً برای ثبت‌نام، شماره موبایل خود را تایید کنید:", reply_markup=markup)
            
        elif call.data == "auth_login":
            user = db.get_user(user_id)
            if user and user.get('is_verified'):
                show_main_dashboard(bot, call.message.chat.id, edit_id=call.message.message_id)
            else:
                bot.edit_message_text("حسابی با این شناسه یافت نشد. لطفاً ثبت‌نام کنید.", call.message.chat.id, call.message.message_id, reply_markup=get_guest_landing_keyboard())

        elif call.data == "auth_cancel":
            db.set_user_state(user_id, "IDLE")
            show_guest_landing(bot, call.message.chat.id, edit_id=call.message.message_id)

    @bot.message_handler(content_types=['contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AWAITING_PHONE")
    def contact_handler(message: Message):
        user_id = message.from_user.id
        if message.contact.user_id != user_id:
            bot.send_message(message.chat.id, "❌ این شماره متعلق به شما نیست!")
            return
            
        # ثبت در دیتابیس
        db.register_or_update_user(
            user_id=user_id,
            username=message.from_user.username,
            full_name=f"{message.from_user.first_name} {message.from_user.last_name or ''}",
            phone_number=message.contact.phone_number,
            is_verified=True
        )
        
        db.set_user_state(user_id, "IDLE")
        bot.send_message(message.chat.id, "✅ ثبت‌نام با موفقیت انجام شد.", reply_markup=ReplyKeyboardRemove())
        show_main_dashboard(bot, message.chat.id)

# --- UI Helpers ---
def show_guest_landing(bot, chat_id, edit_id=None):
    text = "به پلتفرم میانجی خوش آمدید. برای ادامه یکی از گزینه‌های زیر را انتخاب کنید:"
    if edit_id:
        bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_guest_landing_keyboard())
    else:
        bot.send_message(chat_id, text, reply_markup=get_guest_landing_keyboard())

def show_main_dashboard(bot, chat_id, edit_id=None):
    text = "🏠 **پنل کاربری میانجی**\nخوش آمدید، از منوی زیر استفاده کنید:"
    if edit_id:
        bot.edit_message_text(text, chat_id, edit_id, reply_markup=get_main_dashboard_keyboard(), parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=get_main_dashboard_keyboard(), parse_mode="Markdown")
