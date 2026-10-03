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

    # Register handlers
    logger.info("📥 Registering handlers...")
    
    try:
        import auth_handlers
        auth_handlers.register_auth_handlers(bot)
        logger.info("✅ Auth handlers registered.")
    except Exception as e:
        logger.error(f"💥 Failed to register auth_handlers: {e}", exc_info=True)
    
    try:
        admin.register_admin_handlers(bot)
        logger.info("✅ Admin handlers registered.")
    except Exception as e:
        logger.error(f"💥 Failed to register admin_handlers: {e}", exc_info=True)
        
    try:
        user.register_user_handlers(bot)
        logger.info("✅ User handlers registered.")
    except Exception as e:
        logger.error(f"💥 Failed to register user_handlers: {e}", exc_info=True)
        
    try:
        admin_settings.register_admin_settings_handlers(bot)
        logger.info("✅ Admin settings handlers registered.")
    except Exception as e:
        logger.error(f"💥 Failed to register admin_settings_handlers: {e}", exc_info=True)

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
