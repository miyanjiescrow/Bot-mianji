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
    elif digits.startswith("09") and len(digits) == 11:
        pass
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    return digits if len(digits) == 11 else ""

# --- UI Keyboards ---
def get_guest_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود به حساب کاربری", callback_data="auth_login"),
        InlineKeyboardButton("📝 ثبت‌نام در سامانه", callback_data="auth_register"),
        InlineKeyboardButton("🎧 پشتیبانی (@mianji_support)", url="https://t.me/mianji_support")
    )
    return markup

def get_cancel_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ انصراف و بازگشت", callback_data="auth_cancel"))
    return markup

def show_guest_landing(bot, chat_id, edit_id=None):
    text = (
        "🔒 **پلتفرم امن میانجی (Escrow)**\n\n"
        "برای استفاده از امکانات ربات، لطفاً وارد حساب خود شوید یا ثبت‌نام کنید:\n\n"
        "💬 **پشتیبانی تلگرام:** `@mianji_support`"
    )
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

    @bot.message_handler(func=lambda msg: msg.text == "👤 پروفایل کاربری")
    def profile_menu_handler(message: Message):
        try:
            user_id = message.from_user.id
            user = db.get_user(user_id)
            if not user or not user.get('is_verified'):
                show_guest_landing(bot, message.chat.id)
                return
            
            text = (
                f"👤 **پروفایل کاربری شما**\n\n"
                f"▫️ نام و نام خانوادگی: {user.get('full_name', 'ثبت نشده')}\n"
                f"▫️ شماره موبایل: {user.get('phone_number', 'ثبت نشده')}\n"
                f"▫️ شناسه کاربری: `{user_id}`\n"
            )
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("🔑 تغییر رمز عبور", callback_data="profile_change_pwd"),
                InlineKeyboardButton("📱 تغییر شماره تماس", callback_data="profile_change_phone"),
                InlineKeyboardButton("🚪 خروج از اکانت", callback_data="profile_logout"),
                InlineKeyboardButton("🔙 بازگشت به منو", callback_data="profile_back")
            )
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in profile_menu_handler: {e}", exc_info=True)

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_help", "auth_cancel", "profile_change_pwd", "profile_change_phone", "profile_logout", "profile_back"])
    def auth_and_profile_callbacks(call: CallbackQuery):
        try:
            user_id = call.from_user.id
            bot.answer_callback_query(call.id)
            chat_id = call.message.chat.id
            
            if call.data == "auth_cancel" or call.data == "profile_back":
                db.clear_user_state(user_id)
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(chat_id, "🔙 بازگشت به منوی اصلی.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, chat_id)
                
            elif call.data == "auth_register":
                db.set_user_state(user_id, "REG_PHONE", {})
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل من", request_contact=True))
                markup.add(KeyboardButton("❌ انصراف"))
                bot.send_message(chat_id, "📝 **مرحله ۱ از ۳: ثبت‌نام**\n\nلطفاً شماره موبایل خود را با استفاده از دکمه زیر ارسال کنید یا به صورت دستی وارد نمایید (مثال: 09123456789):", reply_markup=markup, parse_mode="Markdown")
                
            elif call.data == "auth_login":
                db.set_user_state(user_id, "LOGIN_PHONE", {})
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل من", request_contact=True))
                markup.add(KeyboardButton("❌ انصراف"))
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(chat_id, "🔑 **ورود به حساب کاربری**\n\nلطفاً شماره موبایل خود را با استفاده از دکمه زیر ارسال کنید یا به صورت دستی وارد نمایید (مثال: 09123456789):", reply_markup=markup, parse_mode="Markdown")
                
            elif call.data == "auth_help":
                bot.answer_callback_query(call.id, "برای راهنمایی با پشتیبانی میانجی در ارتباط باشید: @mianji_support", show_alert=True)

            elif call.data == "profile_logout":
                db.clear_user_state(user_id)
                try:
                    if db.supabase:
                        db.supabase.table("users").update({"telegram_id": None, "is_verified": False}).eq("id", user_id).execute()
                    db.register_or_update_user(user_id=user_id, is_verified=False)
                except Exception as e:
                    logger.error(f"Error during logout for user {user_id}: {e}")
                
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass

                bot.send_message(chat_id, "🚪 شما با موفقیت از حساب کاربری خود خارج شدید.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, chat_id)

            elif call.data == "profile_change_pwd":
                db.set_user_state(user_id, "CHANGE_PWD_OLD", {})
                bot.edit_message_text("🔑 لطفاً **رمز عبور فعلی** خود را وارد کنید:", chat_id, call.message.message_id, parse_mode="Markdown")

            elif call.data == "profile_change_phone":
                db.set_user_state(user_id, "CHANGE_PHONE_NEW", {})
                bot.edit_message_text("📱 لطفاً **شماره موبایل جدید** خود را وارد کنید:", chat_id, call.message.message_id, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error in callbacks: {e}", exc_info=True)

    # --- REGISTRATION FLOW ---
    @bot.message_handler(content_types=['text', 'contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "REG_PHONE")
    def reg_phone_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
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
            # حذف پیام حاوی رمز عبور برای حفظ امنیت و عدم نمایش متن رمزی در چت
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            password = message.text.strip()
            if len(password) < 4:
                bot.send_message(message.chat.id, "⚠️ رمز عبور باید حداقل ۴ کاراکتر باشد. دوباره وارد کنید:")
                return

            bot.send_message(message.chat.id, "✅ رمز عبور با موفقیت دریافت شد.")

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
    @bot.message_handler(content_types=['text', 'contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "LOGIN_PHONE")
    def login_phone_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, message.chat.id)
                return

            raw_phone = message.contact.phone_number if message.contact else message.text
            phone = normalize_phone(raw_phone)
            
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل نامعتبر است. لطفاً شماره خود را ارسال کنید یا به صورت صحیح وارد کنید (مثال: 09123456789):")
                return

            user = db.get_user_by_phone(phone)
            if not user:
                bot.send_message(message.chat.id, "⚠️ حسابی با این شماره تلفن یافت نشد!\nلطفاً ابتدا ثبت‌نام کنید.", reply_markup=get_guest_keyboard())
                db.clear_user_state(user_id)
                return

            db.set_user_state(user_id, "LOGIN_PASSWORD", {"phone": phone})
            bot.send_message(message.chat.id, "🔑 لطفاً رمز عبور حساب خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        except Exception as e:
            logger.error(f"Error in login_phone_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "LOGIN_PASSWORD")
    def login_password_step(message: Message):
        try:
            user_id = message.from_user.id
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

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

    # --- PROFILE MANAGEMENT (CHANGE PASSWORD & PHONE) ---
    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "CHANGE_PWD_OLD")
    def change_pwd_old_step(message: Message):
        try:
            user_id = message.from_user.id
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            pwd = message.text.strip()
            user = db.get_user(user_id)
            
            if not user or user.get("password_hash") != hash_password(pwd):
                bot.send_message(message.chat.id, "❌ رمز عبور فعلی اشتباه است. دوباره وارد کنید:")
                return

            db.set_user_state(user_id, "CHANGE_PWD_NEW", {})
            bot.send_message(message.chat.id, "🔑 لطفاً **رمز عبور جدید** خود را وارد کنید:")
        except Exception as e:
            logger.error(f"Error in change_pwd_old_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "CHANGE_PWD_NEW")
    def change_pwd_new_step(message: Message):
        try:
            user_id = message.from_user.id
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            new_pwd = message.text.strip()
            if len(new_pwd) < 4:
                bot.send_message(message.chat.id, "⚠️ رمز عبور جدید باید حداقل ۴ کاراکتر باشد:")
                return

            new_hash = hash_password(new_pwd)
            db.register_or_update_user(user_id=user_id, password_hash=new_hash)
            db.clear_user_state(user_id)
            
            bot.send_message(message.chat.id, "✅ رمز عبور شما با موفقیت تغییر یافت.")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in change_pwd_new_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطا در تغییر رمز عبور.")

    @bot.message_handler(content_types=['text', 'contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "CHANGE_PHONE_NEW")
    def change_phone_new_step(message: Message):
        try:
            user_id = message.from_user.id
            raw_phone = message.contact.phone_number if message.contact else message.text
            phone = normalize_phone(raw_phone)
            
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل نامعتبر است. لطفاً فرمت صحیح را وارد کنید:")
                return

            existing = db.get_user_by_phone(phone)
            if existing and existing.get("id") != user_id:
                bot.send_message(message.chat.id, "⚠️ این شماره تلفن متعلق به کاربر دیگری است!")
                return

            db.register_or_update_user(user_id=user_id, phone_number=phone)
            db.clear_user_state(user_id)
            
            bot.send_message(message.chat.id, "✅ شماره تماس شما با موفقیت تغییر یافت.")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in change_phone_new_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطا در تغییر شماره تماس.")
