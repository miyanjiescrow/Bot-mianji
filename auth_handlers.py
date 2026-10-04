import logging
import hashlib
import unicodedata
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import keyboards as kb
from config import config
from phone_utils import normalize_phone_number

logger = logging.getLogger("Miyanji_Auth")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.strip().encode()).hexdigest()

def get_guest_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود به حساب کاربری", callback_data="auth_login"),
        InlineKeyboardButton("📝 ثبت‌نام در سامانه", callback_data="auth_register"),
        InlineKeyboardButton("🔄 بازیابی رمز عبور", callback_data="auth_forgot"),
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
        "برای استفاده از امکانات ربات، لطفاً وارد حساب خود شوید، ثبت‌نام کنید یا رمز عبور خود را بازیابی کنید:\n\n"
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

def register_auth_handlers(bot: TeleBot):

    @bot.message_handler(commands=['start'])
    def start_handler(message: Message):
        try:
            user_id = message.from_user.id
            db.clear_user_state(user_id)
            
            phone = db.get_current_user_phone(user_id)
            if phone:
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
            phone = db.get_current_user_phone(user_id)
            if not phone:
                show_guest_landing(bot, message.chat.id)
                return
            
            user = db.get_user_by_phone(phone) or {}
            text = (
                f"👤 **پروفایل کاربری شما**\n\n"
                f"▫️ نام و نام خانوادگی: {user.get('full_name', 'ثبت نشده')}\n"
                f"▫️ شماره موبایل: {phone}\n"
                f"▫️ شناسه تلگرام: `{user_id}`\n"
            )
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("🔑 تغییر رمز عبور", callback_data="profile_change_pwd"),
                InlineKeyboardButton("🚪 خروج از حساب / Logout", callback_data="profile_logout"),
                InlineKeyboardButton("🔙 بازگشت به منو", callback_data="profile_back")
            )
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in profile_menu_handler: {e}", exc_info=True)

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login", "auth_register", "auth_forgot", "auth_cancel", "profile_change_pwd", "profile_logout", "profile_back"])
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
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(
                    chat_id, 
                    "📝 **مرحله ۱ از ۳: ثبت‌نام**\n\nلطفاً شماره موبایل خود را با استفاده از دکمه زیر ارسال کنید یا به صورت دستی وارد نمایید (مثال: 09123456789):", 
                    reply_markup=markup, 
                    parse_mode="Markdown"
                )

            elif call.data == "auth_login":
                db.set_user_state(user_id, "LOGIN_PHONE", {})
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل من", request_contact=True))
                markup.add(KeyboardButton("❌ انصراف"))
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(
                    chat_id, 
                    "🔑 **ورود به حساب کاربری**\n\nلطفاً شماره موبایل خود را با استفاده از دکمه زیر ارسال کنید یا به صورت دستی وارد نمایید (مثال: 09123456789):", 
                    reply_markup=markup, 
                    parse_mode="Markdown"
                )

            elif call.data == "auth_forgot":
                db.set_user_state(user_id, "FORGOT_PHONE", {})
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل من", request_contact=True))
                markup.add(KeyboardButton("❌ انصراف"))
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(
                    chat_id, 
                    "🔄 **بازیابی رمز عبور**\n\nلطفاً شماره موبایل ثبت‌شده خود را ارسال کنید تا هویت شما تایید شود:", 
                    reply_markup=markup, 
                    parse_mode="Markdown"
                )

            elif call.data == "profile_change_pwd":
                db.set_user_state(user_id, "CHANGE_PWD_OLD", {})
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(chat_id, "🔑 لطفاً **رمز عبور فعلی** خود را وارد کنید:", reply_markup=kb.get_cancel_keyboard(), parse_mode="Markdown")

            elif call.data == "profile_logout":
                db.clear_user_state(user_id)
                db.clear_user_session(user_id)
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass

                bot.send_message(chat_id, "🚪 شما با موفقیت از حساب کاربری خود خارج شدید.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, chat_id)

        except Exception as e:
            logger.error(f"Error in auth callbacks: {e}", exc_info=True)

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

            if message.contact:
                if message.contact.user_id and message.contact.user_id != user_id:
                    bot.send_message(message.chat.id, "⚠️ شماره تماس ارسال شده متعلق به شما نیست! لطفاً شماره خود را ارسال کنید.")
                    return
                raw_phone = message.contact.phone_number
            else:
                raw_phone = message.text

            phone = normalize_phone_number(raw_phone)
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل وارد شده نامعتبر است. لطفاً فرمت صحیح را وارد کنید (مثال: 09123456789):")
                return

            existing = db.get_user_by_phone(phone)
            if existing:
                bot.send_message(message.chat.id, "این شماره تلفن قبلاً در سیستم ثبت شده است.", reply_markup=get_guest_keyboard())
                db.clear_user_state(user_id)
                return

            db.set_user_state(user_id, "REG_NAME", {"phone": phone})
            bot.send_message(message.chat.id, "👤 **مرحله ۲ از ۳: نام و نام خانوادگی**\n\nلطفاً نام و نام خانوادگی خود را وارد کنید:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in reg_phone_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "REG_NAME")
    def reg_name_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, message.chat.id)
                return

            full_name = message.text.strip()
            if len(full_name) < 2:
                bot.send_message(message.chat.id, "⚠️ نام و نام خانوادگی نامعتبر است. دوباره وارد کنید:")
                return

            _, data = db.get_user_state(user_id)
            phone = data.get("phone")

            db.set_user_state(user_id, "REG_PASSWORD", {"phone": phone, "full_name": full_name})
            bot.send_message(message.chat.id, "🔑 **مرحله ۳ از ۳: انتخاب رمز عبور**\n\nلطفاً یک رمز عبور امن (حداقل ۴ کاراکتر) برای حساب خود وارد کنید:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in reg_name_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "REG_PASSWORD")
    def reg_password_step(message: Message):
        try:
            user_id = message.from_user.id
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            password = message.text.strip()
            if len(password) < 4:
                bot.send_message(message.chat.id, "⚠️ رمز عبور باید حداقل ۴ کاراکتر باشد. دوباره وارد کنید:")
                return

            _, data = db.get_user_state(user_id)
            phone = data.get("phone")
            full_name = data.get("full_name")

            if not phone or not full_name:
                bot.send_message(message.chat.id, "❌ اطلاعات ثبت‌نام منقضی شد. لطفاً دوباره /start بزنید.")
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            pwd_hash = hash_password(password)
            db.register_or_update_user_by_phone(
                phone_number=phone,
                full_name=full_name,
                telegram_id=user_id,
                username=message.from_user.username or "",
                password_hash=pwd_hash,
                is_verified=True
            )
            db.set_user_session(user_id, phone)
            db.clear_user_state(user_id)

            bot.send_message(message.chat.id, "✅ **ثبت‌نام با موفقیت انجام شد و وارد حساب شدید!**", parse_mode="Markdown")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in reg_password_step: {e}", exc_info=True)
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

            if message.contact:
                if message.contact.user_id and message.contact.user_id != user_id:
                    bot.send_message(message.chat.id, "⚠️ شماره تماس ارسال شده متعلق به شما نیست!")
                    return
                raw_phone = message.contact.phone_number
            else:
                raw_phone = message.text

            phone = normalize_phone_number(raw_phone)
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل نامعتبر است. لطفاً فرمت صحیح را وارد کنید (مثال: 09123456789):")
                return

            user = db.get_user_by_phone(phone)
            if not user:
                bot.send_message(message.chat.id, "⚠️ حسابی با این شماره تلفن یافت نشد!\nلطفاً ابتدا ثبت‌نام کنید.", reply_markup=get_guest_keyboard())
                db.clear_user_state(user_id)
                return

            db.set_user_state(user_id, "LOGIN_PASSWORD", {"phone": phone})
            bot.send_message(message.chat.id, "🔑 لطفاً رمز عبور خود را وارد کنید:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
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
            phone = data.get("phone") if data else None

            if not phone:
                bot.send_message(message.chat.id, "⚠️ نشست شما منقضی شد. لطفاً دوباره تلاش کنید.", reply_markup=ReplyKeyboardRemove())
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            user = db.get_user_by_phone(phone)
            stored_hash = user.get("password_hash") if user else None

            # If user has no password set (legacy), allow login and prompt to set one or check hash
            if stored_hash and stored_hash != hash_password(password):
                bot.send_message(message.chat.id, "❌ رمز عبور اشتباه است! دوباره تلاش کنید:", reply_markup=kb.get_cancel_keyboard())
                return

            db.set_user_session(user_id, phone)
            db.clear_user_state(user_id)

            bot.send_message(message.chat.id, "✅ **ورود موفقیت‌آمیز بود!**", parse_mode="Markdown")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in login_password_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی در ورود.")

    # --- FORGOT PASSWORD / RECOVERY FLOW ---
    @bot.message_handler(content_types=['text', 'contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "FORGOT_PHONE")
    def forgot_phone_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, message.chat.id)
                return

            if message.contact:
                if message.contact.user_id and message.contact.user_id != user_id:
                    bot.send_message(message.chat.id, "⚠️ شماره تماس ارسال شده متعلق به شما نیست!")
                    return
                raw_phone = message.contact.phone_number
            else:
                raw_phone = message.text

            phone = normalize_phone_number(raw_phone)
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل نامعتبر است. فرمت صحیح (مثال: 09123456789):")
                return

            user = db.get_user_by_phone(phone)
            if not user:
                bot.send_message(message.chat.id, "⚠️ حسابی با این شماره تلفن در سامانه ثبت نشده است.", reply_markup=get_guest_keyboard())
                db.clear_user_state(user_id)
                return

            db.set_user_state(user_id, "FORGOT_NEW_PASSWORD", {"phone": phone})
            bot.send_message(message.chat.id, "✅ شماره شما تایید شد.\n\n🔑 لطفاً **رمز عبور جدید** خود را وارد کنید:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in forgot_phone_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "FORGOT_NEW_PASSWORD")
    def forgot_new_password_step(message: Message):
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

            _, data = db.get_user_state(user_id)
            phone = data.get("phone") if data else None

            if not phone:
                bot.send_message(message.chat.id, "⚠️ نشست منقضی شد. لطفاً دوباره تلاش کنید.", reply_markup=ReplyKeyboardRemove())
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            new_hash = hash_password(new_pwd)
            db.register_or_update_user_by_phone(phone_number=phone, full_name=None, password_hash=new_hash, telegram_id=user_id)
            db.set_user_session(user_id, phone)
            db.clear_user_state(user_id)

            bot.send_message(message.chat.id, "✅ **رمز عبور شما با موفقیت بازیابی و تغییر یافت!**\nاکنون وارد حساب خود شدید.", parse_mode="Markdown")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in forgot_new_password_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطا در بازیابی رمز عبور.")

    # --- CHANGE PASSWORD (PROFILE) ---
    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "CHANGE_PWD_OLD")
    def change_pwd_old_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_main_dashboard(bot, message.chat.id, user_id)
                return

            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            pwd = message.text.strip()
            phone = db.get_current_user_phone(user_id)
            user = db.get_user_by_phone(phone) if phone else None
            
            stored_hash = user.get("password_hash") if user else None
            if stored_hash and stored_hash != hash_password(pwd):
                bot.send_message(message.chat.id, "❌ رمز عبور فعلی اشتباه است. دوباره وارد کنید:", reply_markup=kb.get_cancel_keyboard())
                return

            db.set_user_state(user_id, "CHANGE_PWD_NEW", {})
            bot.send_message(message.chat.id, "🔑 لطفاً **رمز عبور جدید** خود را وارد کنید:", reply_markup=kb.get_cancel_keyboard(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in change_pwd_old_step: {e}", exc_info=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "CHANGE_PWD_NEW")
    def change_pwd_new_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_main_dashboard(bot, message.chat.id, user_id)
                return

            try:
                bot.delete_message(message.chat.id, message.message_id)
            except:
                pass

            new_pwd = message.text.strip()
            if len(new_pwd) < 4:
                bot.send_message(message.chat.id, "⚠️ رمز عبور جدید باید حداقل ۴ کاراکتر باشد:", reply_markup=kb.get_cancel_keyboard())
                return

            new_hash = hash_password(new_pwd)
            phone = db.get_current_user_phone(user_id)
            if phone:
                db.register_or_update_user_by_phone(phone_number=phone, full_name=None, password_hash=new_hash)
            
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "✅ رمز عبور شما با موفقیت تغییر یافت.")
            show_main_dashboard(bot, message.chat.id, user_id)
        except Exception as e:
            logger.error(f"Error in change_pwd_new_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطا در تغییر رمز عبور.")
