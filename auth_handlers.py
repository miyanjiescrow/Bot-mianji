import logging
from telebot import TeleBot
from telebot.types import Message, CallbackQuery
import database as db
import auth_service as auth
import keyboards as kb

logger = logging.getLogger("Miyanji_Auth")

def register_auth_handlers(bot: TeleBot):
    
    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register"])
    def auth_start(call: CallbackQuery):
        user_id = call.from_user.id
        action = call.data
        
        if action == "auth_register":
            db.set_user_state(user_id, "AUTH_REGISTER_PHONE")
            bot.edit_message_text("📱 لطفاً شماره تلفن خود را وارد کنید:", call.message.chat.id, call.message.message_id)
        else:
            db.set_user_state(user_id, "AUTH_LOGIN_PHONE")
            bot.edit_message_text("📱 لطفاً شماره تلفن خود را وارد کنید:", call.message.chat.id, call.message.message_id)

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) in ["AUTH_REGISTER_PHONE", "AUTH_LOGIN_PHONE"])
    def process_phone(message: Message):
        user_id = message.from_user.id
        phone = message.text.strip()
        
        if not phone.isdigit() or len(phone) < 10:
            bot.send_message(message.chat.id, "❌ شماره تلفن نامعتبر است. لطفاً دوباره وارد کنید:")
            return

        state, _ = db.get_user_state(user_id)
        
        # ذخیره موقت شماره
        if state == "AUTH_REGISTER_PHONE":
            db.set_user_state(user_id, "AUTH_REGISTER_PASS", {"phone": phone})
            bot.send_message(message.chat.id, "🔑 لطفاً رمز عبور خود را تعیین کنید (حداقل ۶ رقم):")
        else:
            db.set_user_state(user_id, "AUTH_LOGIN_PASS", {"phone": phone})
            bot.send_message(message.chat.id, "🔑 لطفاً رمز عبور خود را وارد کنید:")

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) in ["AUTH_REGISTER_PASS", "AUTH_LOGIN_PASS"])
    def process_password(message: Message):
        user_id = message.from_user.id
        password = message.text.strip()
        state, data = db.get_user_state(user_id)
        phone = data.get("phone")
        
        if len(password) < 6:
            bot.send_message(message.chat.id, "⚠️ رمز عبور باید حداقل ۶ رقم باشد. دوباره وارد کنید:")
            return
            
        if state == "AUTH_REGISTER_PASS":
            db.set_user_state(user_id, "AUTH_REGISTER_FULLNAME", {"phone": phone, "password": password})
            bot.send_message(message.chat.id, "👤 لطفاً نام و نام خانوادگی خود را وارد کنید:")
        else:
            authorized_id = auth.check_credentials(phone, password)
            if authorized_id == user_id:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "✅ ورود با موفقیت انجام شد! به منوی اصلی خوش آمدید.")
                # Show main menu
            else:
                bot.send_message(message.chat.id, "❌ شماره یا رمز عبور اشتباه است.")

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "AUTH_REGISTER_FULLNAME")
    def process_fullname(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()
        
        if len(full_name.split()) < 2:
            bot.send_message(message.chat.id, "⚠️ لطفاً نام و نام خانوادگی را کامل وارد کنید (حداقل دو کلمه):")
            return
            
        _, data = db.get_user_state(user_id)
        phone = data.get("phone")
        password = data.get("password")
        
        if auth.register_user_credentials(user_id, phone, password, full_name):
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "✅ ثبت‌نام با موفقیت انجام شد! به میانجی خوش آمدید.")
        else:
            bot.send_message(message.chat.id, "❌ خطا در ثبت‌نام. دوباره تلاش کنید.")
