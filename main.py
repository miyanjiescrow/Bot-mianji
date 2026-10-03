import os
import sys
import threading
import logging
import time
from flask import Flask, jsonify
import telebot
from config import config
import user
import admin
import admin_settings
from telebot import custom_filters
import database as db
import reminders
import auth_service as auth
import keyboards as kb

# Logging to stdout for Render
logging.basicConfig(
    stream=sys.stdout,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask API & Web App ---
from main_web_api import app

def run_health_check():
    port = config.PORT
    logger.info(f"🚀 Flask Web Server listening on port {port}")
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"❌ Flask server failed: {e}")

def main():
    logger.info("🎬 Starting Mianji Bot application...")
    
    # Start health check
    threading.Thread(target=run_health_check, daemon=True).start()
    
    # Database Test
    if db.supabase:
        logger.info("🔗 Testing Supabase connection...")
        try:
            # Try to fetch a simple setting to verify connection
            db.get_bot_setting("commission_rate", 10)
            logger.info("✅ Database connection verified.")
        except Exception as e:
            logger.error(f"❌ Database test failed: {e}")
    else:
        logger.error("❌ Supabase client is not initialized.")

    # Initialize Bot with optimized thread pool
    bot_token = config.BOT_TOKEN
    if not bot_token:
        logger.error("❌ BOT_TOKEN missing! Bot will not start. Please set BOT_TOKEN in Settings.")
        # Keep the process alive so health check server keeps running
        while True:
            time.sleep(60)

    # Enable middleware support
    from telebot import apihelper
    apihelper.ENABLE_MIDDLEWARE = True

    # Increase num_threads for better concurrency
    bot = telebot.TeleBot(bot_token, threaded=True, num_threads=40)

    # --- Middleware for High Speed Processing ---
    @bot.middleware_handler(update_types=['message', 'callback_query'])
    def inject_user_data(bot_instance, update):
        """تزریق وضعیت کاربر به پیام جهت حذف کوئری‌های تکراری در فیلترها (Speed Hack)"""
        try:
            user_id = None
            target = None
            
            if hasattr(update, 'message') and update.message:
                user_id = update.message.from_user.id
                target = update.message
            elif hasattr(update, 'from_user') and update.from_user:
                user_id = update.from_user.id
                target = update
            
            if user_id and target:
                # دریافت وضعیت فقط یک بار در شروع پردازش هر پیام
                state, data = db.get_user_state(user_id)
                setattr(target, 'user_state', state)
                setattr(target, 'user_data', data)
                # بررسی وضعیت لاگین
                is_authenticated = auth.is_authenticated(user_id)
                state = db.get_user_state(user_id)[0]
                is_auth_flow = state and state.startswith("AUTH")

                if not is_authenticated and not is_auth_flow:
                    bot_instance.send_message(target.chat.id, "👋 خوش آمدید! برای شروع کار با میانجی، وارد حساب خود شوید یا اکانت بسازید:", reply_markup=kb.get_auth_keyboard())
                    return # متوقف کردن پردازش تمام هندلرها

                # همچنین چک کردن پروفایل کاربر (کش شده) برای ادمین بودن
                user_info = db.get_user(user_id)
                is_admin = (user_id == config.OWNER_ID or user_id == config.ADMIN_ID or user_id in getattr(config, 'ADMIN_IDS', []))
                setattr(target, 'is_admin', is_admin)
                setattr(target, 'user_info', user_info)
        except Exception as e:
            logging.error(f"Middleware Error: {e}")

    bot.add_custom_filter(custom_filters.StateFilter(bot))
    
    # Register handlers
    logger.info("📥 Registering handlers...")
    import auth_handlers
    auth_handlers.register_auth_handlers(bot)
    admin.register_admin_handlers(bot)
    user.register_user_handlers(bot)
    admin_settings.register_admin_settings_handlers(bot)

    # Start reminder service
    reminders.start_reminder_service(bot)
    
    logger.info("🤖 Bot is ready to poll.")
    try:
        logger.info("🧹 Removing any existing webhooks or stale connections...")
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        logger.warning(f"⚠️ Could not remove webhook: {e}")

    while True:
        try:
            logger.info("🚀 Starting bot polling (infinity_polling)...")
            # Optimized polling parameters for faster response and stability
            bot.infinity_polling(timeout=90, long_polling_timeout=20, skip_pending=True, logger_level=logging.WARNING)
        except Exception as e:
            logger.error(f"💥 Polling error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"💥 Application crashed during startup: {e}", exc_info=True)
        sys.exit(1)
