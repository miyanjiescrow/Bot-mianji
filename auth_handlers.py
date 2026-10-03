import logging
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardRemove
import database as db
import auth_service as auth
import keyboards as kb

logger = logging.getLogger("Miyanji_Auth")

def register_auth_handlers(bot: TeleBot):
    
    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_recover"])
    def auth_start(call: CallbackQuery):
        user_id = call.from_user.id
        action = call.data
        
        if action == "auth_recover":
            bot.answer_callback_query(call.id, "این قابلیت به‌زودی فعال می‌شود.", show_alert=True)
            return

        state = "AUTH_REGISTER_PHONE" if action == "auth_register" else "AUTH_LOGIN_PHONE"
        db.set_user_state(user_id, state)
        
        bot.edit_message_text(
            "📱 شماره تلفن خود را وارد کنید:\n(برای لغو، دستور /cancel را بفرستید)", 
            call.message.chat.id, 
            call.message.message_id
        )

    @bot.message_handler(commands=['cancel'])
    def cancel_auth(message: Message):
        db.clear_user_state(message.from_user.id)
        bot.send_message(message.chat.id, "عملیات لغو شد. انتخاب کنید:", reply_markup=kb.get_auth_keyboard())

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] in ["AUTH_REGISTER_PHONE", "AUTH_LOGIN_PHONE"])
    def process_phone(message: Message):
        user_id = message.from_user.id
        phone = message.text.strip()
        
        if not phone.isdigit() or len(phone) < 10:
            bot.send_message(message.chat.id, "❌ شماره نامعتبر است. مجدداً وارد کنید:")
            return

        state, _ = db.get_user_state(user_id)
        next_state = "AUTH_REGISTER_PASS" if state == "AUTH_REGISTER_PHONE" else "AUTH_LOGIN_PASS"
        db.set_user_state(user_id, next_state, {"phone": phone})
        
        bot.send_message(message.chat.id, "🔑 رمز عبور را وارد کنید:")

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] in ["AUTH_REGISTER_PASS", "AUTH_LOGIN_PASS"])
    def process_password(message: Message):
        user_id = message.from_user.id
        password = message.text.strip()
        state, data = db.get_user_state(user_id)
        phone = data.get("phone")
        
        if len(password) < 6:
            bot.send_message(message.chat.id, "⚠️ رمز عبور حداقل ۶ رقم باشد:")
            return
            
        if state == "AUTH_REGISTER_PASS":
            db.set_user_state(user_id, "AUTH_REGISTER_NAME", {"phone": phone, "password": password})
            bot.send_message(message.chat.id, "👤 نام و نام خانوادگی را وارد کنید:")
        else:
            authorized_id = auth.check_credentials(phone, password)
            if authorized_id == user_id:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "✅ با موفقیت وارد شدید.")
            else:
                bot.send_message(message.chat.id, "❌ شماره یا رمز عبور اشتباه است.")

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AUTH_REGISTER_NAME")
    def process_fullname(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()
        
        _, data = db.get_user_state(user_id)
        if auth.register_user_credentials(user_id, data.get("phone"), data.get("password"), full_name):
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "✅ ثبت‌نام موفق بود.")
        else:
            bot.send_message(message.chat.id, "❌ خطا در ثبت‌نام.")
