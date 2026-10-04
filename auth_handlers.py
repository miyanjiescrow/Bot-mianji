import logging
import unicodedata
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import database as db
import keyboards as kb
from config import config
from phone_utils import normalize_phone_number

logger = logging.getLogger("Miyanji_Auth")

def get_guest_keyboard():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔑 ورود / ثبت‌نام با شماره موبایل", callback_data="auth_login_register"),
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
        "برای استفاده از امکانات ربات، لطفاً با شماره موبایل خود وارد شوید یا ثبت‌نام کنید:\n\n"
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
                InlineKeyboardButton("🚪 خروج از حساب / Logout", callback_data="profile_logout"),
                InlineKeyboardButton("🔙 بازگشت به منو", callback_data="profile_back")
            )
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in profile_menu_handler: {e}", exc_info=True)

    @bot.callback_query_handler(func=lambda call: call.data in ["auth_login_register", "auth_cancel", "profile_logout", "profile_back"])
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
                
            elif call.data == "auth_login_register":
                db.set_user_state(user_id, "AUTH_PHONE", {})
                markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
                markup.add(KeyboardButton("📱 ارسال شماره موبایل من", request_contact=True))
                markup.add(KeyboardButton("❌ انصراف"))
                try:
                    bot.delete_message(chat_id, call.message.message_id)
                except:
                    pass
                bot.send_message(
                    chat_id, 
                    "🔐 **ورود / ثبت‌نام در میانجی**\n\nلطفاً شماره موبایل خود را با استفاده از دکمه زیر ارسال کنید یا به صورت دستی وارد نمایید (مثال: 09123456789):", 
                    reply_markup=markup, 
                    parse_mode="Markdown"
                )

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

    # --- AUTH PHONE STEP (Login or Register) ---
    @bot.message_handler(content_types=['text', 'contact'], func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AUTH_PHONE")
    def auth_phone_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, message.chat.id)
                return

            # Contact card verification
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

            # Check if phone exists in users
            existing_user = db.get_user_by_phone(phone)
            if existing_user:
                # Login existing user
                db.set_user_session(user_id, phone)
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, f"✅ خوش آمدید! شما با موفقیت وارد حساب خود شدید.", reply_markup=ReplyKeyboardRemove())
                show_main_dashboard(bot, message.chat.id, user_id)
            else:
                # New registration: ask for full name
                db.set_user_state(user_id, "AUTH_NAME", {"phone": phone})
                bot.send_message(message.chat.id, "👤 لطفاً **نام و نام خانوادگی** خود را برای تکمیل ثبت‌نام وارد کنید:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error in auth_phone_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی رخ داد. لطفاً دوباره تلاش کنید.")

    # --- AUTH NAME STEP (Registration completion) ---
    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id)[0] == "AUTH_NAME")
    def auth_name_step(message: Message):
        try:
            user_id = message.from_user.id
            if message.text in ["❌ انصراف", "/cancel", "🔙 انصراف و بازگشت"]:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
                show_guest_landing(bot, message.chat.id)
                return

            full_name = message.text.strip()
            if len(full_name) < 2:
                bot.send_message(message.chat.id, "⚠️ نام و نام خانوادگی معتبر نیست. لطفاً دوباره وارد کنید:")
                return

            _, data = db.get_user_state(user_id)
            phone = data.get("phone") if data else None

            if not phone:
                bot.send_message(message.chat.id, "⚠️ نشست شما منقضی شد. لطفاً دوباره از ابتدا تلاش کنید.", reply_markup=ReplyKeyboardRemove())
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            # Double check duplicate
            existing = db.get_user_by_phone(phone)
            if existing:
                bot.send_message(message.chat.id, "این شماره تلفن قبلاً در سیستم ثبت شده است.", reply_markup=ReplyKeyboardRemove())
                db.clear_user_state(user_id)
                show_guest_landing(bot, message.chat.id)
                return

            # Register user
            db.register_or_update_user_by_phone(
                phone_number=phone,
                full_name=full_name,
                telegram_id=user_id,
                username=message.from_user.username or "",
                is_verified=True
            )
            db.set_user_session(user_id, phone)
            db.clear_user_state(user_id)

            bot.send_message(message.chat.id, "✅ **ثبت‌نام شما با موفقیت انجام شد و وارد حساب شدید!**", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
            show_main_dashboard(bot, message.chat.id, user_id)

        except Exception as e:
            logger.error(f"Error in auth_name_step: {e}", exc_info=True)
            bot.send_message(message.chat.id, "❌ خطای سیستمی در ثبت‌نام. لطفاً دوباره تلاش کنید.")
