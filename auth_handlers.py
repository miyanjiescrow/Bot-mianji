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
            bot.edit_message_text("لطفاً شماره تلفن خود را وارد کنید:", call.message.chat.id, call.message.message_id)
        else:
            db.set_user_state(user_id, "AUTH_LOGIN_PHONE")
            bot.edit_message_text("لطفاً شماره تلفن خود را وارد کنید:", call.message.chat.id, call.message.message_id)

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) in ["AUTH_REGISTER_PHONE", "AUTH_LOGIN_PHONE"])
    def process_phone(message: Message):
        user_id = message.from_user.id
        phone = message.text
        current_state, _ = db.get_user_state(user_id)
        
        # ذخیره موقت شماره
        if current_state == "AUTH_REGISTER_PHONE":
            db.set_user_state(user_id, "AUTH_REGISTER_PASS", {"phone": phone})
            bot.send_message(message.chat.id, "لطفاً رمز عبور خود را وارد کنید:")
        else:
            db.set_user_state(user_id, "AUTH_LOGIN_PASS", {"phone": phone})
            bot.send_message(message.chat.id, "لطفاً رمز عبور خود را وارد کنید:")

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) in ["AUTH_REGISTER_PASS", "AUTH_LOGIN_PASS"])
    def process_password(message: Message):
        user_id = message.from_user.id
        password = message.text
        state, data = db.get_user_state(user_id)
        phone = data.get("phone")
        
        if state == "AUTH_REGISTER_PASS":
            if auth.register_user_credentials(user_id, phone, password):
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "ثبت‌نام با موفقیت انجام شد!")
            else:
                bot.send_message(message.chat.id, "خطا در ثبت‌نام.")
        else:
            authorized_id = auth.check_credentials(phone, password)
            if authorized_id == user_id:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "خوش آمدید!")
            else:
                bot.send_message(message.chat.id, "شماره یا رمز اشتباه است.")
