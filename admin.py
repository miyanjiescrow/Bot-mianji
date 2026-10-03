import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import config
import database as db
import keyboards as kb
import utils

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("Miyanji_Admin")

# Thread pool for background tasks (broadcasts, large exports)
executor = ThreadPoolExecutor(max_workers=25)


# دیکشنری برای نگهداری وضعیت موقت ادمین
# admin_states دیگر به صورت محلی استفاده نمی‌شود و به db منتقل شده است.


def is_admin(user_id: int) -> bool:
    """بررسی ادمین بودن کاربر"""
    return user_id == config.OWNER_ID or user_id == config.ADMIN_ID or user_id in getattr(config, 'ADMIN_IDS', [])


def is_owner(user_id: int) -> bool:
    """بررسی مالک بودن کاربر"""
    return user_id == config.OWNER_ID


def set_admin_state(chat_id: int, user_id: Optional[int], state: str, data: Optional[Dict[str, Any]] = None):
    """
    تنظیم وضعیت مدیریت هم برای کاربر (اگر در پیوی است) و هم برای چت (اگر کانال مدیریت است).
    """
    if user_id:
        db.set_user_state(user_id, state, data)
    
    # اگر در یک کانال یا گروه هستیم، وضعیت را برای خودِ چت هم ذخیره می‌کنیم تا ادمین‌ها بتوانند پاسخ دهند
    if chat_id and chat_id < 0:
        db.set_user_state(chat_id, state, data)

def get_admin_state(message: Message) -> tuple:
    """دریافت وضعیت ادمین با اولویت دیتای تزریق شده در چرخه جاری پیام"""
    st = getattr(message, 'user_state', None)
    dt = getattr(message, 'user_data', {})
    if st: return st, dt

    user_id = message.from_user.id if message.from_user else message.chat.id
    return db.get_user_state(user_id)


def register_admin_handlers(bot: TeleBot):
    """ثبت تمام handlers مربوط به توابع ادمینی"""

    # ====================================================
    # ۰. دستور ادمین و بازگشت به پنل مدیریت (adm:home)
    # ====================================================
    @bot.message_handler(commands=['admin'])
    @bot.channel_post_handler(commands=['admin'])
    def admin_command(message: Message):
        user_id = message.from_user.id if message.from_user else message.chat.id
        if not is_admin(user_id):
            return
        
        try:
            qs = db.get_admin_quick_stats()
        except Exception:
            qs = {}
            
        bot.send_message(
            message.chat.id,
            "👨‍💼 **پنل مدیریت میانجی**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_admin_panel_keyboard(qs, is_owner(user_id))
        )

    @bot.callback_query_handler(func=lambda call: call.data == "adm:home")
    def admin_home(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        
        # پاک‌سازی وضعیت ادمین هنگام بازگشت به خانه
        db.clear_user_state(call.from_user.id)
        if call.message.chat.id < 0:
            db.clear_user_state(call.message.chat.id)
            
        try:
            qs = db.get_admin_quick_stats()
        except Exception:
            qs = {}
        bot.edit_message_text(
            "👨‍💼 **پنل مدیریت میانجی**\n"
            "──────────────────\n"
            "به بخش مدیریت خوش آمدید. لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_admin_panel_keyboard(qs, is_owner(call.from_user.id))
        )

    # ====================================================
    # ۱. آمار کلی (adm:stats)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:stats")
    def show_stats(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            pub = db.get_public_transparency_stats()
            qs = db.get_admin_quick_stats()
            text = (
                "📊 **آمار کلی سامانه**\n\n"
                f"✅ معاملات موفق: **{pub.get('completed_count', 0)}** عدد\n"
                f"💰 حجم تسویه: **{utils.format_currency(pub.get('completed_volume', 0))}**\n"
                f"🔄 معاملات فعال: **{pub.get('active_count', 0)}** عدد\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⚖️ داوری‌های باز: **{qs.get('disputes', 0)}**\n"
                f"💸 برداشت‌های در انتظار: **{qs.get('withdrawals', 0)}**\n"
                f"🧾 فیش‌های معامله: **{qs.get('deal_receipts', 0)}**\n"
                f"💰 تراکنش‌های معلق (واریزی): **{qs.get('deposit_receipts', 0)}**\n"
            )
        except Exception as e:
            logger.error(f"خطا در دریافت آمار: {e}")
            text = "❌ خطا در دریافت آمار."
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=markup)

    # ====================================================
    # ۲. پرونده‌های داوری (adm:disputes)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:disputes")
    def show_disputes(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            from database import supabase
            res = supabase.table("contracts").select(
                "contract_id,title,buyer_id,seller_id,amount,status"
            ).in_("status", ["disputed", "in_dispute"]).order("updated_at", desc=True).limit(20).execute()
            disputes = res.data or []
        except Exception as e:
            logger.error(f"خطا در دریافت داوری‌ها: {e}")
            disputes = []

        if not disputes:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
            bot.edit_message_text("⚖️ پرونده‌ای در انتظار داوری وجود ندارد.",
                                  call.message.chat.id, call.message.message_id, reply_markup=markup)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for d in disputes:
            cid = d.get("contract_id", "---")
            title = (d.get("title") or "بدون عنوان")[:25]
            markup.add(InlineKeyboardButton(
                f"⚖️ {cid} — {title}",
                callback_data=f"adm:dispute:view:{cid}"
            ))
        markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
        bot.edit_message_text(
            f"⚖️ **پرونده‌های داوری باز ({len(disputes)})**",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=markup
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:dispute:view:"))
    def view_dispute(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        cid = call.data.replace("adm:dispute:view:", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
            return

        text = utils.generate_contract_text(contract)
        # نمایش علت شکایت در پیام
        reason = contract.get("dispute_reason", "ذکر نشده")
        text += f"\n\n⚖️ **علت شکایت:**\n{reason}"
        
        # استفاده از کیبورد استاندارد داوری
        markup = kb.get_dispute_admin_inline(cid, room_link=contract.get("dispute_room_link"))
        
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:dispute:set_room:"))
    def handle_set_dispute_room(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        cid = call.data.replace("adm:dispute:set_room:", "", 1)
        
        bot.answer_callback_query(call.id)
        set_admin_state(call.message.chat.id, admin_id, "WAITING_DISPUTE_ROOM_LINK", {"cid": cid})
        
        bot.send_message(
            call.message.chat.id,
            f"🔗 **ثبت لینک اتاق داوری (Mianji Room)**\n\n"
            f"لطفاً لینک گروه داوری ایجاد شده برای معامله `{cid}` را ارسال کنید:\n"
            "(این لینک برای طرفین معامله ارسال خواهد شد)",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "WAITING_DISPUTE_ROOM_LINK")
    def handle_dispute_room_link_input(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, data = get_admin_state(message)
        cid = data.get("cid")
        link = (message.text or "").strip()
        
        if not (link.startswith("https://t.me/") or link.startswith("http://t.me/") or link.startswith("tg://")):
             bot.reply_to(message, "⚠️ لینک نامعتبر است. لطفاً لینک صحیح تلگرام را ارسال کنید:")
             return
             
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        
        contract = db.get_contract(cid)
        if not contract:
            bot.send_message(message.chat.id, "❌ قرارداد یافت نشد.")
            return

        db.update_contract(cid, {"dispute_room_link": link})
        db.append_contract_history(cid, f"🔗 لینک اتاق داوری ثبت شد: {link}", actor_id=admin_id)
        
        # اطلاع‌رسانی به طرفین
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        
        markup = kb.get_dispute_room_link_keyboard(link)
        msg_text = (
            f"⚖️ **اتاق حل اختلاف (Mianji Room) ایجاد شد**\n\n"
            f"برای حل اختلاف معامله `{cid}`، ادمین اتاق ویژه‌ای ایجاد کرده است. لطفاً وارد شوید:\n"
            "⚠️ تیم داوری در این گروه حضور دارد و مستندات را بررسی می‌کند."
        )
        
        for uid in [buyer_id, seller_id]:
            try:
                bot.send_message(uid, msg_text, parse_mode="Markdown", reply_markup=markup)
            except Exception: pass
            
        bot.send_message(message.chat.id, f"✅ لینک اتاق داوری ثبت و برای طرفین ارسال شد.\n\n{link}", reply_markup=kb.get_admin_panel_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:dispute:buyer:") or call.data.startswith("adm:dispute:seller:") or call.data.startswith("adm:resolve:decide:") or call.data.startswith("adm:resolve:split:"))
    def resolve_dispute_callback(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return

        # یکسان‌سازی کال‌بک‌های مختلف داوری
        data = call.data
        if data.startswith("adm:dispute:buyer:"):
            cid = data.replace("adm:dispute:buyer:", "", 1)
            winner = "buyer"
        elif data.startswith("adm:dispute:seller:"):
            cid = data.replace("adm:dispute:seller:", "", 1)
            winner = "seller"
        elif data.startswith("adm:resolve:decide:employer:"):
            cid = data.replace("adm:resolve:decide:employer:", "", 1)
            winner = "buyer"
        elif data.startswith("adm:resolve:decide:freelancer:"):
            cid = data.replace("adm:resolve:decide:freelancer:", "", 1)
            winner = "seller"
        elif data.startswith("adm:resolve:split:"):
            cid = data.replace("adm:resolve:split:", "", 1)
            # رفتن به مرحله دریافت درصد تقسیم
            state_data = {"cid": cid, "msg_id": call.message.message_id}
            set_admin_state(call.message.chat.id, admin_id, "WAITING_DISPUTE_SPLIT_PERCENT", state_data)
            
            bot.answer_callback_query(call.id, "📊 لطفاً درصد سهم «مجری» را وارد کنید (مثلاً 40):", show_alert=True)
            bot.send_message(call.message.chat.id, f"⚖️ <b>تقسیم وجه معامله <code>{cid}</code></b>\n\nلطفاً درصد سهم <b>مجری</b> (از 1 تا 99) را وارد کنید.\nباقیمانده به کارفرما عودت داده می‌شود.", parse_mode="HTML", reply_markup=kb.get_cancel_keyboard())
            return
        else:
            return

        try:
            contract = db.get_contract(cid)
            if not contract:
                bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            amount = float(contract.get("amount", 0))

            if winner == "buyer":
                # بازگشت وجه به کارفرما
                db.update_contract(cid, {"status": "cancelled"})
                db.update_wallet_balance(buyer_id, amount, "dispute_refund", f"بازگشت وجه داوری {cid}")
                winner_id, loser_id = buyer_id, seller_id
                winner_text = "حکم به نفع **کارفرما** صادر شد. وجه به کیف‌پول ایشان برگشت داده شد."
            else:
                # آزادسازی وجه به مجری
                payer = contract.get("commission_payer", "freelancer")
                comm, net, _ = utils.calculate_commission(amount, payer=payer)
                db.update_contract(cid, {"status": "completed"})
                db.update_wallet_balance(seller_id, net, "dispute_release", f"آزادسازی وجه داوری {cid}")
                winner_id, loser_id = seller_id, buyer_id
                winner_text = f"حکم به نفع **مجری** صادر شد. مبلغ {utils.format_currency(net)} به کیف‌پول ایشان واریز شد."

            db.append_contract_history(cid, f"⚖️ رأی داوری ادمین: {winner_text}", actor_id=admin_id)
            db.log_admin_action(admin_id, "resolve_dispute", f"cid={cid} winner={winner}", target_id=winner_id)

            for uid, msg in [(winner_id, f"✅ **رأی داوری**\n\n{winner_text}"),
                             (loser_id, f"⚖️ **رأی داوری قرارداد {cid}**\n\nداوری توسط تیم میانجی انجام شد.\n{winner_text}")]:
                try:
                    bot.send_message(uid, msg, parse_mode="Markdown")
                except Exception:
                    pass

            bot.answer_callback_query(call.id, "✅ رأی داوری ثبت شد.", show_alert=True)
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 بازگشت به داوری‌ها", callback_data="adm:disputes"))
            markup.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm:home"))
            try:
                bot.edit_message_text(f"✅ **داوری {cid} بسته شد.**\n\n{winner_text}",
                                      call.message.chat.id, call.message.message_id,
                                      parse_mode="Markdown", reply_markup=markup)
            except Exception:
                bot.send_message(call.message.chat.id, f"✅ **داوری {cid} بسته شد.**\n\n{winner_text}", reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در ثبت رأی داوری: {e}")
            bot.answer_callback_query(call.id, "❌ خطا در ثبت رأی.", show_alert=True)

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "WAITING_DISPUTE_SPLIT_PERCENT")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "WAITING_DISPUTE_SPLIT_PERCENT")
    def handle_dispute_split_percent(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, data = get_admin_state(message)
        
        raw_text = utils.fa_to_en_digits(message.text or "").strip()
        try:
            seller_pct = float(raw_text)
            if not (0 < seller_pct < 100):
                raise ValueError()
        except ValueError:
            bot.reply_to(message, "❌ عدد نامعتبر. لطفاً یک عدد بین 1 تا 99 وارد کنید:")
            return

        cid = data.get("cid")
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)

        try:
            contract = db.get_contract(cid)
            if not contract:
                bot.send_message(message.chat.id, "❌ معامله یافت نشد.")
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            amount = float(contract.get("amount", 0))
            
            # محاسبه سهم‌ها
            seller_share = round((amount * seller_pct) / 100.0)
            buyer_share = amount - seller_share
            
            db.update_contract(cid, {"status": "completed", "dispute_verdict": f"split:{seller_pct}"})
            db.update_wallet_balance(seller_id, seller_share, "dispute_split", f"سهم {seller_pct}% از داوری {cid}")
            db.update_wallet_balance(buyer_id, buyer_share, "dispute_split", f"سهم {100-seller_pct}% از داوری {cid}")
            
            msg_text = f"⚖️ **رأی داوری: تقسیم وجه (Split)**\n\nسهم مجری: **{utils.format_currency(seller_share)}** ({seller_pct}%)\nسهم کارفرما: **{utils.format_currency(buyer_share)}** ({100-seller_pct}%)"
            
            db.append_contract_history(cid, f"⚖️ {msg_text}", actor_id=admin_id)
            db.log_admin_action(admin_id, "resolve_dispute_split", f"cid={cid} seller_pct={seller_pct}")

            for uid, m in [(seller_id, f"✅ **نتیجه داوری معامله `{cid}`**\n\n{msg_text}"),
                             (buyer_id, f"✅ **نتیجه داوری معامله `{cid}`**\n\n{msg_text}")]:
                try:
                    bot.send_message(uid, m, parse_mode="Markdown")
                except Exception:
                    pass

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 بازگشت به داوری‌ها", callback_data="adm:disputes"))
            bot.send_message(message.chat.id, f"✅ رأی تقسیم وجه برای معامله `{cid}` با موفقیت ثبت و اجرا شد.\n\n{msg_text}", parse_mode="Markdown", reply_markup=markup)

        except Exception as e:
            logger.error(f"خطا در اجرای تقسیم وجه داوری: {e}")
            bot.send_message(message.chat.id, "❌ خطای سیستمی در اجرای تقسیم وجه.")

    # ====================================================
    # ۳. فیش‌های در انتظار (adm:pending_receipts)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:pending_receipts")
    def show_pending_receipts(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            from database import supabase
            # واکشی فیش‌های مربوط به معاملات از جدول متمرکز
            res = supabase.table("pending_receipts").select("*").eq("status", "pending").in_("type", ["contract", "milestone"]).order("created_at", desc=True).execute()
            pending_receipts = res.data or []
        except Exception as e:
            logger.error(f"خطا در دریافت فیش‌ها: {e}")
            pending_receipts = []

        if not pending_receipts:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
            bot.edit_message_text("🧾 فیش معامله‌ای در انتظار تایید وجود ندارد.",
                                   call.message.chat.id, call.message.message_id, reply_markup=markup)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for pr in pending_receipts:
            rtype = pr.get("type")
            rel_id = pr.get("related_id")
            amt = utils.format_currency(utils.safe_float(pr.get("amount", 0)))
            label = f"📜 {rtype}: {rel_id} ({amt})"
            markup.add(InlineKeyboardButton(label, callback_data=f"adm:receipt:view:{rel_id}"))
            
        markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
        bot.edit_message_text(
            f"🧾 **فیش‌های معامله در انتظار ({len(pending_receipts)})**",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=markup
        )

    # ۳.۱ تراکنش‌های معلق (واریزی‌های کیف پول)
    @bot.callback_query_handler(func=lambda call: call.data == "adm:pending_deposits")
    def show_pending_deposits(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        
        try:
            from database import supabase
            # واکشی فیش‌های شارژ کیف پول از جدول متمرکز (الگوی مشابه برداشت)
            res = supabase.table("pending_receipts").select("*").eq("status", "pending").eq("type", "wallet").order("created_at", desc=True).execute()
            pending_deposits = res.data or []
        except Exception as e:
            logger.error(f"خطا در دریافت تراکنش‌های معلق: {e}")
            pending_deposits = []

        if not pending_deposits:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل ادمین", callback_data="adm:home"))
            bot.edit_message_text("💰 تراکنش معلقی در انتظار تایید وجود ندارد.",
                                   call.message.chat.id, call.message.message_id, reply_markup=markup)
            return

        bot.edit_message_text(
            f"💰 **تراکنش‌های معلق (واریزی) ({len(pending_deposits)})**",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", 
            reply_markup=kb.get_deposit_list_inline(pending_deposits)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wallet_receipt:view:"))
    def view_wallet_receipt(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        tx_id = call.data.replace("adm:wallet_receipt:view:", "", 1)
        
        from database import supabase
        # دریافت جزئیات از جدول تراکنش‌ها
        res = supabase.table("transactions").select("*, users(full_name, username)").eq("id", tx_id).execute()
        if not res.data:
            bot.answer_callback_query(call.id, "❌ تراکنش یافت نشد.", show_alert=True)
            return
        
        tx = res.data[0]
        user_info = tx.get("users", {})
        user_id = tx.get("user_id")
        amount = tx.get("amount", 0)
        file_id = tx.get("receipt_file_id")
        
        text = (
            f"💰 **بررسی فیش شارژ حساب**\n"
            f"──────────────────\n"
            f"👤 کاربر: `{user_id}` ({user_info.get('full_name', 'نامشخص')})\n"
            f"💵 مبلغ: `{utils.format_currency(amount)}` تومان\n"
            f"📅 تاریخ درخواست: `{tx.get('created_at')}`\n"
            f"📝 شرح: `{tx.get('description', 'شارژ کیف پول')}`\n"
        )
        
        markup = kb.get_deposit_action_inline(tx_id, user_id, amount)
        
        if file_id:
            try:
                bot.send_photo(call.message.chat.id, file_id, caption=text, parse_mode="Markdown", reply_markup=markup)
            except Exception:
                try:
                    bot.send_document(call.message.chat.id, file_id, caption=text, parse_mode="Markdown", reply_markup=markup)
                except Exception:
                    bot.send_message(call.message.chat.id, text + "\n\n⚠️ خطا در بارگذاری فایل فیش.", parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:receipt:view:"))
    def view_receipt(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        
        raw_id = call.data.replace("adm:receipt:view:", "", 1)
        # تشخیص معامله یا مرحله
        if ":" in raw_id:
            cid, idx_str = raw_id.split(":", 1)
            idx = int(idx_str)
            is_milestone = True
        else:
            cid = raw_id
            idx = None
            is_milestone = False
            
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
            return

        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        
        if is_milestone:
            milestones = contract.get("milestones") or []
            if idx >= len(milestones):
                bot.answer_callback_query(call.id, "❌ این مرحله یافت نشد.", show_alert=True)
                return
            ms = milestones[idx]
            amount = utils.safe_float(ms.get("amount", 0))
            receipt_file_id = ms.get("receipt_file_id")
            text = (
                f"🧾 **بررسی فیش مرحله {idx + 1}**\n\n"
                f"🤝 قرارداد: {contract.get('title', 'بدون عنوان')}\n"
                f"📌 مرحله: {ms.get('title', 'بدون عنوان')}\n"
                f"💰 مبلغ مرحله: {utils.format_currency(amount)} تومان\n"
                f"👤 کارفرما (ID): `{buyer_id}`\n"
            )
            markup = kb.get_ms_receipt_admin_inline(cid, idx, buyer_id)
        else:
            amount = utils.safe_float(contract.get("amount", 0))
            receipt_file_id = contract.get("receipt_file_id")
            text = (
                f"🧾 **بررسی فیش کل معامله**\n\n"
                f"🤝 عنوان: {contract.get('title', 'بدون عنوان')}\n"
                f"💰 مبلغ کل: {utils.format_currency(amount)} تومان\n"
                f"🆔 کد معامله: `{cid}`\n"
                f"👤 کارفرما (ID): `{buyer_id}`\n"
                f"🛠 مجری (ID): `{seller_id}`\n"
                f"📊 وضعیت: {contract.get('status', 'نامشخص')}\n"
            )
            markup = kb.get_receipt_admin_approval_inline(cid, buyer_id)
            
        bot.answer_callback_query(call.id)
        
        # نمایش فیش (عکس یا فایل)
        if receipt_file_id:
            try:
                bot.send_photo(call.message.chat.id, receipt_file_id, caption=text, parse_mode="Markdown", reply_markup=markup)
                return
            except Exception:
                try:
                    bot.send_document(call.message.chat.id, receipt_file_id, caption=text, parse_mode="Markdown", reply_markup=markup)
                    return
                except Exception as e:
                    logger.warning(f"Error showing receipt: {e}")
        
        bot.send_message(call.message.chat.id, text + "\n\n⚠️ فیش قابل نمایش نیست یا حذف شده است.", parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:receipt:appr:") or call.data.startswith("adm:receipt:rej:"))
    def handle_receipt_decision(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return

        if call.data.startswith("adm:receipt:appr:"):
            cid = call.data.replace("adm:receipt:appr:", "", 1)
            approve = True
        else:
            cid = call.data.replace("adm:receipt:rej:", "", 1)
            approve = False

        is_extra_edit = False
        if cid.startswith("ee:"):
            is_extra_edit = True
            cid = cid.replace("ee:", "", 1)

        try:
            contract = db.get_contract(cid)
            if not contract:
                bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
                return

            # بررسی وضعیت
            current_status = contract.get("status", "")
            valid_statuses = ("awaiting_receipt_approval", "awaiting_payment", "pending_payment", "awaiting_extra_edit_receipt", "active")
            if current_status not in valid_statuses:
                bot.answer_callback_query(call.id,
                                           f"⚠️ این فیش قبلاً پردازش شده (وضعیت: {current_status})",
                                           show_alert=True)
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            
            if approve:
                if is_extra_edit:
                    price = utils.safe_float(contract.get("extra_edit_price", 0))
                    held = utils.safe_float(contract.get("held_extra_amount", 0)) + price
                    db.update_contract(cid, {
                        "status": "active",
                        "held_extra_amount": held,
                        "extra_edit_price": None,
                        "pending_edit_reason": None
                    })
                    db.append_contract_history(cid, f"✅ فیش هزینه ویرایش ({utils.format_currency(price)}) توسط ادمین تایید شد.", actor_id=admin_id)
                    
                    # آپدیت وضعیت در جدول متمرکز
                    try:
                        db.supabase.table("pending_receipts").update({"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", cid).eq("type", "milestone").eq("status", "pending").execute()
                    except Exception as e:
                        logger.error(f"Error updating pending_receipts on extra edit approve: {e}")
                    
                    # اطلاع‌رسانی
                    try:
                        bot.send_message(buyer_id, f"✅ **فیش هزینه ویرایش معامله `{cid}` تایید شد.**\nمبلغ {utils.format_currency(price)} در حساب امانت بلوکه شد.", parse_mode="Markdown")
                        
                        seller_msg = (
                            f"🚀 **معامله فعال شد (هزینه ویرایش)**\n"
                            f"──────────────────\n"
                            f"📌 **اطلاعات معامله**\n"
                            f"├ شناسه: `{cid}`\n"
                            f"├ کارفرما: {contract.get('buyer_username') or 'کارفرما'}\n"
                            f"├ مبلغ کل: {utils.format_currency(utils.safe_float(contract.get('amount', 0)))}\n"
                            f"└ مهلت: {contract.get('deadline', '---')} روز\n"
                            f"──────────────────\n"
                            "لطفاً اصلاحات را انجام داده و مجدداً تحویل دهید. ✨"
                        )
                        bot.send_message(seller_id, seller_msg, parse_mode="Markdown", reply_markup=kb.get_deliver_project_keyboard(cid))
                    except Exception: pass
                    
                    result_text = f"✅ فیش ویرایش معامله `{cid}` تایید شد."
                else:
                    amount = utils.safe_float(contract.get("amount", 0))
                    # تایید فیش عادی
                    paid_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    db.update_contract(cid, {"status": "active", "paid_at": paid_at})
                    db.append_contract_history(cid, "✅ فیش واریزی توسط ادمین تایید شد.", actor_id=admin_id)
                    db.log_admin_action(admin_id, "approve_receipt", f"cid={cid} amount={amount}")
                    
                    # آپدیت وضعیت در جدول متمرکز
                    try:
                        res_pr = db.supabase.table("pending_receipts").update({"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", cid).eq("status", "pending").execute()
                    except Exception as e:
                        logger.error(f"Error updating pending_receipts on contract approve: {e}")
                    
                    # ارسال پیام به کارفرما
                    try:
                        bot.send_message(
                            buyer_id,
                            f"✅ **فیش واریزی شما تایید شد**\n\n"
                            f"📌 معامله: `{cid}`\n"
                            f"💰 مبلغ: {utils.format_currency(amount)}\n\n"
                            "معامله فعال شد. منتظر تحویل مجری باشید.",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

                    # ارسال پیام به مجری
                    try:
                        seller_msg = (
                            f"🚀 **معامله فعال شد!**\n"
                            f"──────────────────\n"
                            f"📌 **اطلاعات معامله**\n"
                            f"├ شناسه: `{cid}`\n"
                            f"├ کارفرما: {contract.get('buyer_username') or 'کارفرما'}\n"
                            f"├ مبلغ: {utils.format_currency(amount)}\n"
                            f"└ مهلت: {contract.get('deadline', '---')} روز\n"
                            f"──────────────────\n"
                            "اکنون می‌توانید کار را شروع کنید. موفق باشید! 🚀"
                        )
                        bot.send_message(
                            seller_id,
                            seller_msg,
                            reply_markup=kb.get_deliver_project_keyboard(cid),
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                    
                    result_text = f"✅ فیش معامله `{cid}` تایید شد."
            else:
                # رد فیش
                prefix = "ee:" if is_extra_edit else ""
                state_data = {"cid": prefix + cid, "msg_id": call.message.message_id, "chat_id": call.message.chat.id}
                set_admin_state(call.message.chat.id, admin_id, "WAITING_REJECT_RECEIPT_REASON", state_data)
                
                bot.answer_callback_query(call.id, "📝 لطفاً دلیل رد فیش را بنویسید.", show_alert=True)
                bot.send_message(call.message.chat.id, f"📝 <b>دلیل رد فیش معامله <code>{cid}</code> را بنویسید:</b>", 
                                 reply_to_message_id=call.message.message_id,
                                 parse_mode="HTML",
                                 reply_markup=kb.get_cancel_keyboard())
                return

            bot.answer_callback_query(call.id, result_text[:200], show_alert=True)

            # آپدیت پیام ادمین
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 بازگشت به فیش‌ها", callback_data="adm:pending_receipts"))
            markup.add(InlineKeyboardButton("🏠 پنل مدیریت", callback_data="adm:home"))
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                              reply_markup=markup)
            except Exception:
                bot.send_message(call.message.chat.id, result_text, reply_markup=markup)

        except Exception as e:
            logger.error(f"خطا در تصمیم‌گیری فیش: {e}")
            bot.answer_callback_query(call.id, "❌ خطایی رخ داد.", show_alert=True)

    # ====================================================
    # ۸. مدیریت سفیران (Ambassador Approvals)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:amb:"))
    def handle_ambassador_approval(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        parts = call.data.split(":")
        action = parts[2] # appr or rej
        target_id = int(parts[3])
        
        if action == "appr":
            # تایید سفیر
            db.register_ambassador(target_id)
            db.update_user_fields(target_id, {"role": "ambassador", "ambassador_status": "approved"})
            
            bot.answer_callback_query(call.id, "✅ سفیر با موفقیت تایید شد.")
            bot.edit_message_text(
                f"✅ درخواست کاربر `{target_id}` برای سفیر شدن **تایید شد**.",
                call.message.chat.id,
                call.message.message_id
            )
            
            # اطلاع به کاربر
            try:
                bot.send_message(
                    target_id,
                    "🎉 **تبریک! درخواست شما برای همکاری تایید شد.**\n\n"
                    "اکنون می‌توانید از منوی اصلی وارد «📣 پنل سفیران» شوید و فعالیت خود را شروع کنید.",
                    parse_mode="Markdown"
                )
            except: pass
            
        elif action == "rej":
            db.update_user_fields(target_id, {"ambassador_status": "rejected"})
            bot.answer_callback_query(call.id, "❌ درخواست رد شد.")
            bot.edit_message_text(
                f"❌ درخواست کاربر `{target_id}` برای سفیر شدن **رد شد**.",
                call.message.chat.id,
                call.message.message_id
            )
            
            # اطلاع به کاربر
            try:
                bot.send_message(
                    target_id,
                    "⚠️ **نتیجه درخواست همکاری**\n\nمتاسفانه درخواست شما برای همکاری در حال حاضر تایید نشد.\nبرای اطلاعات بیشتر می‌توانید به پشتیبانی پیام دهید."
                )
            except: pass

    # ====================================================
    # ۴. درخواست‌های برداشت (adm:withdrawals)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:withdrawals")
    def show_withdrawal_requests(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        requests = db.get_pending_withdrawal_requests()
        if not requests:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
            bot.edit_message_text("📭 درخواست برداشتی در حال انتظار نیست.",
                                  call.message.chat.id, call.message.message_id, reply_markup=markup)
            return
        bot.edit_message_text(
            f"💸 **درخواست‌های برداشت در حال انتظار ({len(requests)})**",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_withdrawal_list_inline(requests)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wd:view:"))
    def view_withdrawal_request(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        req_id = call.data.replace("adm:wd:view:", "", 1)
        req = db.get_withdrawal_request(req_id)
        if not req:
            bot.answer_callback_query(call.id, "❌ درخواست یافت نشد.", show_alert=True)
            return
        req_id_val = req.get("id", "---")
        user_id_req = req.get("user_id", "---")
        amount = req.get("amount", 0)
        sheba = req.get("sheba") or req.get("sheba_or_card") or "---"
        status_raw = req.get("status", "pending")
        status_map = {
            "pending": "⏳ در انتظار اقدام",
            "queued": "🕒 در صف سیکل شبا",
            "paid": "✅ تسویه شده",
            "cancelled": "❌ لغو شده"
        }
        status = status_map.get(status_raw, status_raw)
        
        created_at = req.get("created_at", "---")
        documents = req.get("documents", None)
        document_note = req.get("document_note", None)
        suspicious_text = ""
        if req.get("suspicious_flag"):
            suspicious_text = "🚨 **هشدار فعالیت مشکوک:** این کاربر بیش از ۲ درخواست برداشت معلق همزمان دارد.\n\n"
            
        text = (
            f"💳 **جزئیات درخواست برداشت #{req_id_val}**\n\n"
            f"{suspicious_text}"
            f"👤 **کاربر:** `{user_id_req}`\n"
            f"💰 **مبلغ:** {utils.format_currency(amount)}\n"
            f"🏦 **شماره شبا:** `{sheba}`\n"
            f"📅 **تاریخ درخواست:** `{created_at}`\n"
            f"🔶 **وضعیت:** {status}\n"
        )
        if documents:
            text += f"\n📎 **مستندات ارسال شده:** بله\n"
            if document_note:
                text += f"📝 **یادداشت:** {document_note}\n"
        else:
            text += f"\n📎 **مستندات:** ارسال نشده\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown",
                              reply_markup=kb.get_withdrawal_action_inline(req_id_val))
        if documents and len(documents) > 0:
            try:
                for doc_file_id in documents:
                    bot.send_document(call.message.chat.id, doc_file_id)
            except Exception as e:
                logger.warning(f"خطا در ارسال مستندات: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wd:paid:"))
    def approve_withdrawal(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید.", show_alert=True)
            return
        req_id = int(call.data.replace("adm:wd:paid:", "", 1))
        req = db.get_withdrawal_request(req_id)
        if not req:
            bot.answer_callback_query(call.id, "❌ درخواست یافت نشد.", show_alert=True)
            return
            
        bot.answer_callback_query(call.id)
        target_uid = req.get("user_id")
        amount = req.get("amount")
        destination = req.get("sheba") or req.get("sheba_or_card") or "ثبت نشده"
        
        state_data = {
            "target_uid": target_uid, 
            "amount": amount, 
            "call_msg_id": call.message.message_id,
            "req_id": req_id,
            "mode": "approve_and_pay"
        }
        set_admin_state(call.message.chat.id, admin_id, "WAITING_WITHDRAW_TRACKING_CODE", state_data)
        
        user_info = db.get_user(target_uid)
        real_name = f"{user_info.get('first_name_real', '---')} {user_info.get('last_name_real', '---')}" if user_info else "نامشخص"
        
        bot.send_message(
            call.message.chat.id, 
            f"📝 **تایید واریز وجه (تسویه فوری)**\n\n"
            f"👤 کاربر: {utils.escape_html(real_name)} (<code>{target_uid}</code>)\n"
            f"💰 مبلغ: {utils.format_currency(amount)}\n"
            f"💳 شبا/کارت: `{destination}`\n\n"
            f"لطفاً **فیش واریزی (عکس یا فایل)** را ارسال کنید تا وضعیت درخواست به «تسویه شده» تغییر یابد:", 
            parse_mode="HTML",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wd:queue:"))
    def queue_withdrawal(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        req_id = int(call.data.replace("adm:wd:queue:", "", 1))
        req = db.get_withdrawal_request(req_id)
        if not req:
            bot.answer_callback_query(call.id, "❌ درخواست یافت نشد.", show_alert=True)
            return
            
        bot.answer_callback_query(call.id)
        target_uid = req.get("user_id")
        amount = req.get("amount")
        destination = req.get("sheba") or req.get("sheba_or_card") or "ثبت نشده"

        state_data = {
            "target_uid": target_uid, 
            "amount": amount, 
            "call_msg_id": call.message.message_id,
            "req_id": req_id,
            "mode": "queue"
        }
        set_admin_state(call.message.chat.id, admin_id, "WAITING_WITHDRAW_TRACKING_CODE", state_data)
        
        user_info = db.get_user(target_uid)
        real_name = f"{user_info.get('first_name_real', '---')} {user_info.get('last_name_real', '---')}" if user_info else "نامشخص"
        
        bot.send_message(
            call.message.chat.id, 
            f"📝 **تایید انتقال به سیکل شبا/پایا**\n\n"
            f"👤 کاربر: {utils.escape_html(real_name)} (<code>{target_uid}</code>)\n"
            f"💰 مبلغ: {utils.format_currency(amount)}\n"
            f"🏦 شبا: `{destination}`\n\n"
            f"لطفاً **فیش ثبت درخواست پایا (عکس یا فایل)** را ارسال کنید تا وضعیت درخواست به «در صف سیکل شبا» تغییر یابد:", 
            parse_mode="HTML",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wd:cancel:"))
    def reject_withdrawal(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید.", show_alert=True)
            return
        req_id = call.data.replace("adm:wd:cancel:", "", 1)
        req = db.get_withdrawal_request(req_id)
        if not req:
            bot.answer_callback_query(call.id, "❌ درخواست یافت نشد.", show_alert=True)
            return
        success = db.resolve_withdrawal_request(req_id, approve=False, admin_id=admin_id)
        if success:
            bot.answer_callback_query(call.id, "✅ درخواست برداشت لغو شد.", show_alert=True)
            user_id_req = req.get("user_id")
            amount = req.get("amount")
            try:
                bot.send_message(user_id_req,
                                 f"❌ **درخواست برداشت شما رد شد**\n\n"
                                 f"💰 **مبلغ:** {utils.format_currency(amount)}\n"
                                 "مبلغ مذکور به کیف پول شما بازگردانده شده است.",
                                 parse_mode="Markdown")
            except Exception:
                pass
            requests = db.get_pending_withdrawal_requests()
            if requests:
                bot.edit_message_text(f"💸 **درخواست‌های برداشت ({len(requests)})**",
                                      call.message.chat.id, call.message.message_id,
                                      parse_mode="Markdown",
                                      reply_markup=kb.get_withdrawal_list_inline(requests))
            else:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
                bot.edit_message_text("📭 درخواست برداشتی در حال انتظار نیست.",
                                      call.message.chat.id, call.message.message_id, reply_markup=markup)
        else:
            bot.answer_callback_query(call.id, "❌ خطایی در لغو رخ داد.", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wd:add_docs:"))
    def start_add_documents(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ شما دسترسی ندارید.", show_alert=True)
            return
        
        req_id = int(call.data.replace("adm:wd:add_docs:", "", 1))
        req = db.get_withdrawal_request(req_id)
        if not req:
            bot.send_message(call.message.chat.id, "❌ درخواست یافت نشد.")
            return

        state_data = {
            "target_uid": req.get("user_id"), 
            "amount": req.get("amount"), 
            "req_id": req_id,
            "mode": "only_docs"
        }
        set_admin_state(call.message.chat.id, admin_id, "WAITING_WITHDRAW_TRACKING_CODE", state_data)
        
        bot.send_message(
            call.message.chat.id,
            f"📎 **افزودن مستندات به درخواست #{req_id}**\n\n"
            "لطفاً تصویر فیش یا فایل مربوطه را ارسال کنید:\n"
            "(این فایل در تاریخچه درخواست ثبت و برای کاربر ارسال می‌شود)",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(content_types=['photo', 'document', 'text'],
                         func=lambda msg: get_admin_state(msg)[0] == "ADM_ADDING_DOCUMENTS")
    def handle_admin_documents(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        try:
            if message.content_type == 'photo':
                if "documents" not in state_data: state_data["documents"] = []
                state_data["documents"].append(message.photo[-1].file_id)
                set_admin_state(message.chat.id, admin_id, state_name, state_data)
                bot.send_message(message.chat.id, "✅ عکس دریافت شد.")
            elif message.content_type == 'document':
                if "documents" not in state_data: state_data["documents"] = []
                state_data["documents"].append(message.document.file_id)
                set_admin_state(message.chat.id, admin_id, state_name, state_data)
                bot.send_message(message.chat.id, "✅ فایل دریافت شد.")
            elif message.content_type == 'text':
                state_data["document_note"] = message.text
                set_admin_state(message.chat.id, admin_id, state_name, state_data)
                bot.send_message(message.chat.id, "✅ توضیحات ثبت شد.")
        except Exception as e:
            logger.error(f"خطا در دریافت مستندات: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wd:confirm_docs:"))
    def confirm_documents(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        admin_id = call.from_user.id
        state_name, state_data = get_admin_state(call.message)
        
        if state_name != "ADM_ADDING_DOCUMENTS":
            bot.answer_callback_query(call.id, "❌ وضعیت نامعتبر.", show_alert=True)
            return
        
        req_id = call.data.replace("adm:wd:confirm_docs:", "", 1)
        success = db.update_withdrawal_documents(req_id, state_data.get("documents", []), state_data.get("document_note", ""))
        if success:
            bot.send_message(call.message.chat.id, "✅ مستندات ذخیره شدند.",
                             reply_markup=kb.get_withdrawal_action_inline(req_id))
        else:
            bot.send_message(call.message.chat.id, "❌ خطا در ذخیره مستندات.")
        db.clear_user_state(admin_id)
        if call.message.chat.id < 0: db.clear_user_state(call.message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data == "adm:wd:cancel_docs")
    def cancel_documents(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "❌ عملیات لغو شد.")
        db.clear_user_state(call.from_user.id)

    # ====================================================
    # ۵. جست‌وجوی پرونده (adm:case_search) - غیرفعال شده
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:case_search")
    def case_search_disabled(call: CallbackQuery):
        bot.answer_callback_query(call.id, "📁 این بخش از پنل ادمین حذف شده است.", show_alert=False)

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_CASE_SEARCH")
    def handle_case_search_input(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        query = message.text.strip()
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)

        # جست‌وجو بر اساس شناسه قرارداد
        contract = db.get_contract(query.upper())
        if contract:
            text = utils.generate_contract_text(contract)
            cid = contract.get("contract_id") or contract.get("id", query)
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)
            return

        # جست‌وجو بر اساس آیدی کاربر
        try:
            user_id_q = int(query)
            user = db.get_user(user_id_q)
            if user:
                contracts = db.get_user_contracts(user_id_q)
                full_name = user.get("full_name", "نامشخص")
                username = user.get("username", "")
                wallet = float(user.get("wallet_balance", 0) or 0)
                is_bl = bool(user.get("is_blacklisted", False))
                text = (
                    f"👤 **کاربر: {full_name}**\n"
                    f"🆔 آیدی: `{user_id_q}`\n"
                    f"👤 یوزرنیم: @{username}\n"
                    f"💰 موجودی کیف پول: {utils.format_currency(wallet)}\n"
                    f"📋 تعداد معاملات: {len(contracts)}\n"
                    f"🚫 لیست سیاه: {'بله' if is_bl else 'خیر'}\n"
                )
                markup = kb.get_blacklist_action_inline(user_id_q, is_bl)
                markup.add(
                    InlineKeyboardButton("➕ شارژ / دریافت وجه", callback_data=f"adm:wallet_adj:add:{user_id_q}"),
                    InlineKeyboardButton("➖ کاهش / واریز به کاربر", callback_data=f"adm:wallet_adj:sub:{user_id_q}")
                )
                markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
                bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)
                return
        except (ValueError, TypeError):
            pass

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.send_message(message.chat.id, "❌ پرونده‌ای با این مشخصات یافت نشد.", reply_markup=markup)

    # ====================================================
    # ۵.۵. نمودار تراکنش‌ها (adm:stats_chart)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:stats_chart")
    def show_stats_chart(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        
        # استفاده از آدرس اپلیکیشن برای نمایش نمودار
        chart_url = f"{config.APP_URL}/admin/stats-chart"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🌐 مشاهده نمودار (D3)", url=chart_url))
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        
        bot.edit_message_text(
            "📊 **تحلیل تراکنش‌های موفق**\n"
            "──────────────────\n"
            "برای مشاهده نمودار ستونی و جزئیات تراکنش‌های ۳۰ روز اخیر که به صورت هوشمند و تعاملی با استفاده از کتابخانه D3 طراحی شده است، روی دکمه زیر کلیک کنید.\n\n"
            "📍 **نکته:** نمودار شامل واریزی‌ها و تسویه‌های موفق می‌باشد.",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )

    # ====================================================
    # ۶. قرارداد خام (adm:raw_contract)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:raw_contract")
    def raw_contract_start(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "📝 **قرارداد خام**\n\nدسته‌بندی قرارداد را انتخاب کنید:",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_raw_contract_category_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:raw_cat:"))
    def raw_contract_category(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        cat = call.data.replace("adm:raw_cat:", "", 1)
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_RAW_CONTRACT", {"category": cat})
        bot.send_message(
            call.message.chat.id,
            f"📝 دسته‌بندی `{cat}` انتخاب شد.\n\nمتن کامل قرارداد را ارسال کنید:",
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data == "adm:raw_cancel")
    def raw_contract_cancel(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        db.clear_user_state(call.from_user.id)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text("❌ لغو شد.", call.message.chat.id, call.message.message_id, reply_markup=markup)

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_RAW_CONTRACT")
    def handle_raw_contract_text(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        cat = state_data.get("category", "GEN")
        raw_text = message.text or ""

        # ارسال به کانال بایگانی
        try:
            utils.notify_archive(
                bot,
                text=f"📝 **قرارداد خام (دسته: {cat})**\n\nتوسط ادمین `{admin_id}` ثبت شد:\n\n{raw_text[:3000]}"
            )
        except Exception as e:
            logger.warning(f"خطا در ارسال قرارداد خام به آرشیو: {e}")

        db.log_admin_action(admin_id, "raw_contract", f"cat={cat} len={len(raw_text)}")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.send_message(message.chat.id, "✅ قرارداد خام ثبت و در آرشیو ذخیره شد.", reply_markup=markup)

    # ====================================================
    # ۷. لیست سیاه (adm:blacklist)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:blacklist")
    def show_blacklist_panel(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "🚫 **مدیریت لیست سیاه**\n\nیک گزینه انتخاب کنید:",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_blacklist_panel_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:bl_list:"))
    def show_blacklist(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            from database import supabase
            offset = int(call.data.replace("adm:bl_list:", "", 1))
            res = supabase.table("users").select("id,full_name,username").eq("is_blacklisted", True).range(offset, offset + 9).execute()
            users = res.data or []
        except Exception as e:
            logger.error(f"خطا در دریافت لیست سیاه: {e}")
            users = []

        if not users:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="adm:blacklist"))
            bot.edit_message_text("🚫 لیست سیاه خالی است.",
                                  call.message.chat.id, call.message.message_id, reply_markup=markup)
            return

        markup = InlineKeyboardMarkup(row_width=1)
        for u in users:
            uid = u.get("id", "---")
            name = u.get("full_name", "نامشخص")
            markup.add(InlineKeyboardButton(f"🚫 {name} ({uid})", callback_data=f"adm:bl:info:{uid}"))
        markup.add(InlineKeyboardButton("🔙 بازگشت", callback_data="adm:blacklist"))
        bot.edit_message_text(f"🚫 **کاربران مسدود ({len(users)})**",
                              call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm:bl_add_new")
    def bl_add_new(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        db.set_user_state(call.from_user.id, "ADM_BL_ADD_DIRECT", {})
        bot.send_message(call.message.chat.id,
                         "🆔 آیدی تلگرام کاربر را وارد کنید (عدد):",
                         parse_mode="Markdown")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_BL_ADD_DIRECT")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_BL_ADD_DIRECT")
    def handle_bl_add_direct(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        
        try:
            target_id = int(utils.fa_to_en_digits(message.text or "").strip())
            from database import supabase, update_user_fields
            update_user_fields(target_id, {"is_blacklisted": True})
            db.log_admin_action(admin_id, "blacklist_add", target_id=target_id)
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 بازگشت به لیست سیاه", callback_data="adm:blacklist"))
            bot.send_message(message.chat.id, f"✅ کاربر `{target_id}` به لیست سیاه اضافه شد.",
                             parse_mode="Markdown", reply_markup=markup)
            try:
                bot.send_message(target_id, "⛔ حساب شما در سامانه میانجی مسدود شده است.")
            except Exception:
                pass
        except (ValueError, TypeError):
            bot.send_message(message.chat.id, "❌ آیدی نامعتبر.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:bl:info:"))
    def bl_user_info(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        target_id = int(call.data.replace("adm:bl:info:", "", 1))
        user = db.get_user(target_id)
        if not user:
            bot.answer_callback_query(call.id, "❌ کاربر یافت نشد.", show_alert=True)
            return
        text = (
            f"👤 **{user.get('full_name', 'نامشخص')}**\n"
            f"🆔 `{target_id}`\n"
            f"💰 موجودی: {utils.format_currency(float(user.get('wallet_balance', 0) or 0))}\n"
        )
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown",
                         reply_markup=kb.get_blacklist_action_inline(target_id, True))

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:bl:add:") or call.data.startswith("adm:bl:remove:"))
    def toggle_blacklist(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        if call.data.startswith("adm:bl:add:"):
            target_id = int(call.data.replace("adm:bl:add:", "", 1))
            blacklisted = True
            action = "blacklist_add"
            label = "✅ کاربر به لیست سیاه اضافه شد."
        else:
            target_id = int(call.data.replace("adm:bl:remove:", "", 1))
            blacklisted = False
            action = "blacklist_remove"
            label = "✅ کاربر از لیست سیاه حذف شد."
        try:
            from database import update_user_fields
            update_user_fields(target_id, {"is_blacklisted": blacklisted})
            db.log_admin_action(admin_id, action, target_id=target_id)
            bot.answer_callback_query(call.id, label, show_alert=True)
        except Exception as e:
            logger.error(f"خطا در تغییر لیست سیاه: {e}")
            bot.answer_callback_query(call.id, "❌ خطا.", show_alert=True)

    # ====================================================
    # ۸. تنظیمات سیستم (adm:settings)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:settings")
    def show_settings(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            settings = db.get_all_settings()
        except Exception:
            settings = {}
        bot.edit_message_text(
            "⚙️ **تنظیمات سیستم**",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_settings_inline(settings)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:setting:edit:"))
    def setting_edit_start(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        key = call.data.replace("adm:setting:edit:", "", 1)
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_SETTING_EDIT", {"key": key})
        key_fa = {"commission_percent": "درصد کارمزد", "affiliate_share_percent": "سهم سفیران"}.get(key, key)
        bot.send_message(call.message.chat.id,
                         f"✏️ مقدار جدید **{key_fa}** را وارد کنید (عدد):",
                         parse_mode="Markdown")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_SETTING_EDIT")
    def handle_setting_edit(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        key = state_data.get("key", "")
        try:
            value = float(message.text.strip().replace(",", "."))
            db.set_setting(key, value, updated_by=admin_id)
            db.log_admin_action(admin_id, "setting_change", f"{key}={value}")
            settings = db.get_all_settings()
            markup = kb.get_settings_inline(settings)
            bot.send_message(message.chat.id, f"✅ تنظیم `{key}` به **{value}** بروزرسانی شد.",
                             parse_mode="Markdown", reply_markup=markup)
        except (ValueError, TypeError):
            bot.send_message(message.chat.id, "❌ مقدار نامعتبر. لطفاً یک عدد وارد کنید.")

    @bot.callback_query_handler(func=lambda call: call.data == "adm:setting:toggle_maint")
    def toggle_maintenance(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        current = str(db.get_setting("maintenance_mode", "0"))
        new_val = "0" if current == "1" else "1"
        db.set_setting("maintenance_mode", new_val, updated_by=admin_id)
        db.log_admin_action(admin_id, "toggle_maintenance", f"new={new_val}")
        settings = db.get_all_settings()
        label = "فعال" if new_val == "1" else "غیرفعال"
        bot.answer_callback_query(call.id, f"✅ حالت تعمیرات {label} شد.", show_alert=True)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=kb.get_settings_inline(settings))

    # ====================================================
    # ۹. لاگ ادمین‌ها (adm:audit_log)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:audit_log")
    def show_audit_log(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            logs = db.get_admin_audit_logs(limit=15)
        except Exception:
            logs = []
        if not logs:
            text = "📋 لاگی ثبت نشده است."
        else:
            lines = []
            for log in logs:
                t = str(log.get("created_at", ""))[:16]
                a = log.get("admin_id", "?")
                act = log.get("action", "")
                det = str(log.get("detail", ""))[:50]
                lines.append(f"🕐 `{t}` | 👤`{a}` | `{act}` — {det}")
            text = "📋 **لاگ آخرین فعالیت‌های ادمین:**\n\n" + "\n".join(lines)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text(text[:4000], call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=markup)

    # ====================================================
    # ۱۰. مدیریت کاربران (adm:users)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:users")
    def manage_users(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_USER_SEARCH", {})
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text(
            "👥 **مدیریت کاربران**\n\nآیدی تلگرام یا یوزرنیم کاربر را وارد کنید:",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=markup
        )

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_USER_SEARCH")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_USER_SEARCH")
    def handle_user_search_input(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        query = message.text.strip().lstrip("@")

        # جست‌وجو بر اساس آیدی عددی
        try:
            user_id_q = int(query)
            user = db.get_user(user_id_q)
        except ValueError:
            # جست‌وجو بر اساس یوزرنیم
            user = None
            try:
                from database import supabase
                res = supabase.table("users").select("*").ilike("username", query).limit(1).execute()
                if res.data:
                    user = res.data[0]
                    user_id_q = user.get("user_id")
            except Exception as e:
                logger.error(f"خطا در جست‌وجوی کاربر: {e}")
                user_id_q = None

        if not user:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            bot.send_message(message.chat.id, "❌ کاربری با این مشخصات یافت نشد.", reply_markup=markup)
            return

        full_name = user.get("full_name", "نامشخص")
        username = user.get("username", "")
        wallet = float(user.get("wallet_balance", 0) or 0)
        is_bl = bool(user.get("is_blacklisted", False))
        contracts = db.get_user_contracts(user_id_q) if user_id_q else []

        text = (
            f"👤 **{full_name}**\n"
            f"🆔 آیدی: `{user_id_q}`\n"
            f"👤 یوزرنیم: @{username}\n"
            f"💰 موجودی: {utils.format_currency(wallet)}\n"
            f"📋 معاملات: {len(contracts)}\n"
            f"🚫 لیست سیاه: {'بله' if is_bl else 'خیر'}\n"
        )
        markup = InlineKeyboardMarkup(row_width=2)
        if is_bl:
            markup.add(InlineKeyboardButton("✅ حذف از لیست سیاه", callback_data=f"adm:bl:remove:{user_id_q}"))
        else:
            markup.add(InlineKeyboardButton("🚫 افزودن به لیست سیاه", callback_data=f"adm:bl:add:{user_id_q}"))
        markup.add(
            InlineKeyboardButton("➕ شارژ / دریافت وجه", callback_data=f"adm:wallet_adj:add:{user_id_q}"),
            InlineKeyboardButton("➖ کاهش / واریز به کاربر", callback_data=f"adm:wallet_adj:sub:{user_id_q}")
        )
        markup.add(InlineKeyboardButton("✉️ پیام مستقیم", callback_data=f"adm:direct_to:{user_id_q}"))
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:direct_to:"))
    def direct_msg_to_user(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        target_id = int(call.data.replace("adm:direct_to:", "", 1))
        set_admin_state(call.message.chat.id, admin_id, "ADM_DIRECT_MSG_TEXT", {"target_id": target_id})
        bot.send_message(call.message.chat.id,
                         f"✉️ متن پیام برای کاربر `{target_id}` را بنویسید:",
                         parse_mode="Markdown")

    # ====================================================
    # ۱۱. تنظیم موجودی کیف‌پول کاربر (adm:wallet_adj:add/sub)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wallet_adj:"))
    def wallet_adjustment_start(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        parts = call.data.split(":")  # adm:wallet_adj:add:TARGET_ID
        if len(parts) < 4:
            return
        op = parts[2]  # add or sub
        target_id = int(parts[3])
        set_admin_state(call.message.chat.id, admin_id, "ADM_WALLET_ADJ", {"op": op, "target_id": target_id})
        op_fa = "افزایش" if op == "add" else "کاهش"
        bot.send_message(call.message.chat.id,
                         f"💰 مبلغ {op_fa} موجودی کاربر `{target_id}` را به تومان وارد کنید:",
                         parse_mode="Markdown")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_WALLET_ADJ")
    def handle_wallet_adjustment_amount(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        try:
            amount = float(message.text.strip().replace(",", ""))
            state_data["amount"] = amount
            set_admin_state(message.chat.id, admin_id, "ADM_WALLET_ADJ_DOCS", state_data)
            
            op_fa = "افزایش" if state_data.get("op") == "add" else "کاهش"
            bot.send_message(message.chat.id,
                             f"📎 لطفاً **مستندات (عکس فیش یا فایل)** مربوط به این {op_fa} موجودی را ارسال کنید:\n\n"
                             f"مبلغ: {utils.format_currency(amount)} تومان\n"
                             f"کاربر: `{state_data.get('target_id')}`",
                             parse_mode="Markdown")
        except (ValueError, TypeError):
            bot.send_message(message.chat.id, "❌ مبلغ نامعتبر. لطفاً عدد وارد کنید:")

    @bot.message_handler(content_types=['text', 'photo', 'document'], func=lambda msg: get_admin_state(msg)[0] == "ADM_WALLET_ADJ_DOCS")
    def handle_wallet_adjustment_docs(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        if not state_data: return
        
        op = state_data.get("op", "add")
        target_id = state_data.get("target_id")
        amount = state_data.get("amount", 0)
        
        file_id = None
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document:
            file_id = message.document.file_id
            
        if not file_id:
            bot.reply_to(message, "❌ **ارسال مستندات (عکس فیش یا فایل) الزامی است.**\nلطفاً فیش مربوطه را ارسال کنید.")
            return

        db.clear_user_state(admin_id)
        final_amount = amount if op == "add" else -amount
        desc = (message.caption or f"تنظیم دستی توسط ادمین {admin_id}").strip()
        
        if db.update_wallet_balance(target_id, final_amount, "admin_adjustment", desc, admin_document_id=file_id):
            db.log_admin_action(admin_id, f"wallet_adj_{op}", f"amount={amount}", target_id=target_id)
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            op_fa = "افزایش" if op == "add" else "کاهش"
            bot.send_message(message.chat.id,
                             f"✅ موجودی کاربر `{target_id}` با موفقیت {op_fa} یافت.\n"
                             f"مبلغ: {utils.format_currency(amount)}",
                             parse_mode="Markdown", reply_markup=markup)
            try:
                msg_to_user = (
                    f"💰 موجودی کیف‌پول شما توسط تیم مدیریت میانجی "
                    f"{'افزایش' if op == 'add' else 'کاهش'} یافت.\n\n"
                    f"💵 مبلغ: {utils.format_currency(amount)} تومان\n"
                    f"📝 توضیحات: {desc}"
                )
                if message.photo:
                    bot.send_photo(target_id, file_id, caption=msg_to_user)
                else:
                    bot.send_document(target_id, file_id, caption=msg_to_user)
            except Exception: pass
        else:
            bot.send_message(message.chat.id, "❌ خطا در بروزرسانی کیف پول (احتمالاً موجودی کافی نیست).")

    # ====================================================
    # گزارش مالی (CSV)
    @bot.callback_query_handler(func=lambda call: call.data == "adm:finance_report")
    def handle_finance_report(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "⏳ در حال آماده‌سازی گزارش...")
        
        try:
            from database import supabase
            # واکشی آخرین ۱۰۰۰ تراکنش
            res = supabase.table("transactions").select("*").order("created_at", desc=True).limit(1000).execute()
            txs = res.data or []
            
            if not txs:
                bot.send_message(call.message.chat.id, "⚠️ هیچ تراکنشی یافت نشد.")
                return
                
            import io
            import csv
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Header
            writer.writerow(["ID", "User ID", "Amount", "Type", "Status", "Description", "Created At"])
            
            for t in txs:
                writer.writerow([
                    t.get("id"),
                    t.get("user_id"),
                    t.get("amount"),
                    t.get("type"),
                    t.get("status"),
                    t.get("description"),
                    t.get("created_at")
                ])
                
            # ارسال فایل
            output.seek(0)
            file_data = io.BytesIO(output.getvalue().encode('utf-8-sig'))
            file_data.name = f"Finance_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            
            bot.send_document(
                call.message.chat.id,
                file_data,
                caption=f"📊 گزارش آخرین ۱۰۰۰ تراکنش سیستم\n📅 تاریخ: {utils.get_now_shamsi()}"
            )
            
        except Exception as e:
            logger.error(f"خطا در تولید گزارش مالی: {e}")
            bot.send_message(call.message.chat.id, "❌ خطا در تولید گزارش مالی.")

    # ۱۲. پیام همگانی (adm:broadcast)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:broadcast")
    def broadcast_start(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_BROADCAST", {})
        bot.send_message(
            call.message.chat.id,
            "📢 **پیام همگانی**\n\n"
            "متن پیامی که می‌خواهید برای همه کاربران ارسال شود را بنویسید:\n\n"
            "⚠️ این پیام برای تمام کاربران ربات ارسال می‌شود.",
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_BROADCAST")
    def handle_broadcast_text(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        text = message.text or ""
        
        if not text:
            bot.send_message(message.chat.id, "❌ متن پیام نمی‌تواند خالی باشد.")
            return

        # تایید قبل از ارسال
        set_admin_state(message.chat.id, admin_id, "ADM_BROADCAST_CONFIRM", {"text": text})
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("✅ بله، ارسال کن", callback_data="adm:broadcast_confirm"),
            InlineKeyboardButton("❌ انصراف", callback_data="adm:broadcast_cancel")
        )
        bot.send_message(message.chat.id,
                         f"📢 **پیش‌نمایش پیام همگانی:**\n\n{text}\n\n"
                         "آیا مطمئنید؟",
                         parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "adm:broadcast_confirm")
    def do_broadcast(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        # وقتی دکمه تایید کلیک می‌شود، باید استیت ادمین را بر اساس آیدی خودش بگیریم، نه آیدی پیام
        _, state_data = db.get_user_state(admin_id)
        db.clear_user_state(admin_id)
        if call.message.chat.id < 0: db.clear_user_state(call.message.chat.id)
        
        text = state_data.get("text", "") if isinstance(state_data, dict) else ""
        if not text:
            bot.send_message(call.message.chat.id, "❌ متنی برای ارسال وجود ندارد. مجدداً از بخش پیام همگانی شروع کنید.")
            return

        bot.edit_message_text("⏳ فرآیند ارسال پیام همگانی در پس‌زمینه آغاز شد. پس از اتمام، گزارش نهایی ارسال می‌شود.",
                               call.message.chat.id, call.message.message_id)
        
        def _broadcast_task():
            try:
                from database import supabase
                res = supabase.table("users").select("id").execute()
                users = res.data or []
            except Exception:
                users = []

            sent, failed = 0, 0
            for u in users:
                uid = u.get("id")
                if not uid:
                    continue
                try:
                    bot.send_message(uid, text, parse_mode="Markdown")
                    sent += 1
                except Exception:
                    failed += 1
                
                # برای جلوگیری از فلود شدن تلگرام، وقفه کوتاه اضافه می‌کنیم
                if sent % 20 == 0:
                    import time
                    time.sleep(1)

            db.log_admin_action(admin_id, "broadcast", f"sent={sent} failed={failed}")
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            bot.send_message(call.message.chat.id,
                             f"✅ **پایان فرآیند ارسال پیام همگانی**\n\n"
                             f"📤 ارسال موفق: {sent}\n❌ ناموفق: {failed}",
                             parse_mode="Markdown",
                             reply_markup=markup)
        
        executor.submit(_broadcast_task)

    @bot.callback_query_handler(func=lambda call: call.data == "adm:broadcast_cancel")
    def cancel_broadcast(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        db.clear_user_state(call.from_user.id)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text("❌ پیام همگانی لغو شد.",
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    # ====================================================
    # ۱۳. پیام مستقیم (adm:direct_msg)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:direct_msg")
    def direct_msg_start(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_DIRECT_MSG_TARGET", {})
        bot.send_message(call.message.chat.id,
                         "✉️ **پیام مستقیم**\n\nآیدی تلگرام گیرنده را وارد کنید:",
                         parse_mode="Markdown")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_DIRECT_MSG_TARGET")
    def handle_direct_msg_target(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        try:
            target_id = int(message.text.strip())
            set_admin_state(message.chat.id, admin_id, "ADM_DIRECT_MSG_TEXT", {"target_id": target_id})
            bot.send_message(message.chat.id,
                             f"✉️ متن پیامی که می‌خواهید برای کاربر `{target_id}` ارسال شود را بنویسید:",
                             parse_mode="Markdown")
        except (ValueError, TypeError):
            db.clear_user_state(admin_id)
            if message.chat.id < 0: db.clear_user_state(message.chat.id)
            bot.send_message(message.chat.id, "❌ آیدی نامعتبر.")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_DIRECT_MSG_TEXT")
    def handle_direct_msg_text(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        target_id = state_data.get("target_id")
        text = message.text or ""
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        try:
            bot.send_message(target_id, f"📩 **پیام از تیم میانجی:**\n\n{text}", parse_mode="Markdown")
            db.log_admin_action(admin_id, "direct_msg", f"to={target_id}")
            bot.send_message(message.chat.id, f"✅ پیام به کاربر `{target_id}` ارسال شد.",
                             parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ خطا در ارسال پیام: {e}", reply_markup=markup)

    # ====================================================
    # ۱۴. مدیریت سفیران (adm:ambassadors)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:ambassadors")
    def show_ambassadors(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        try:
            from database import supabase
            # واکشی اطلاعات سفیران به همراه اطلاعات پایه کاربر از طریق Join
            res = supabase.table("ambassadors").select(
                "telegram_id, withdrawable_balance, total_earnings, users(full_name, username)"
            ).eq("is_active", True).order("total_earnings", desc=True).limit(20).execute()
            ambassadors = res.data or []
        except Exception as e:
            logger.error(f"خطا در دریافت سفیران: {e}")
            ambassadors = []

        if not ambassadors:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت", callback_data="adm:home"))
            bot.edit_message_text("🤝 سفیری ثبت نشده است.",
                                  call.message.chat.id, call.message.message_id, reply_markup=markup)
            return

        lines = []
        for a in ambassadors:
            uid = a.get("telegram_id", "?")
            user_data = a.get("users", {}) or {}
            name = user_data.get("full_name", "نامشخص")
            earned = float(a.get("total_earnings", 0) or 0)
            wallet = float(a.get("withdrawable_balance", 0) or 0)
            lines.append(
                f"👤 {name} (`{uid}`)\n"
                f"   💰 کیف‌پول: {utils.format_currency(wallet)} | کل درآمد: {utils.format_currency(earned)}"
            )

        text = f"🤝 **سفیران فعال ({len(ambassadors)})**\n\n" + "\n\n".join(lines)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text(text[:4000], call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=markup)

    # ====================================================
    # ۱۵. بونوس ماهانه سفیران (adm:pay_amb_bonus)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:pay_amb_bonus")
    def pay_ambassador_bonus_start(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_AMB_BONUS", {})
        bot.send_message(
            call.message.chat.id,
            "🎁 **بونوس ماهانه سفیران**\n\n"
            "مبلغ بونوس (به تومان) برای هر سفیر را وارد کنید:\n"
            "این مبلغ به کیف‌پول همکاری تمام سفیران فعال اضافه می‌شود.",
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_AMB_BONUS")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_AMB_BONUS")
    def handle_amb_bonus(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        
        try:
            amount = float(utils.fa_to_en_digits(message.text or "").strip().replace(",", ""))
        except (ValueError, TypeError):
            bot.send_message(message.chat.id, "❌ مبلغ نامعتبر. لطفاً فقط عدد وارد کنید.")
            return

        try:
            from database import supabase
            # واکشی تمام سفیران فعال
            res = supabase.table("ambassadors").select("telegram_id, withdrawable_balance").eq("is_active", True).execute()
            ambassadors = res.data or []
            
            if not ambassadors:
                bot.send_message(message.chat.id, "⚠️ سفیری برای واریز بونوس یافت نشد.")
                return
                
            bot.send_message(message.chat.id, f"⏳ در حال واریز بونوس به {len(ambassadors)} سفیر...")
            
            success_count = 0
            for a in ambassadors:
                uid = a.get("telegram_id")
                new_bal = float(a.get("withdrawable_balance", 0) or 0) + amount
                try:
                    supabase.table("ambassadors").update({"withdrawable_balance": new_bal}).eq("telegram_id", uid).execute()
                    # اطلاع به سفیر
                    bot.send_message(uid, f"🎁 **هدیه تیم مدیریت میانجی**\n\nمبلغ {utils.format_currency(amount)} بونوس ماهانه به کیف‌پول همکاری شما اضافه شد. ✨", parse_mode="Markdown")
                    success_count += 1
                except: pass
            
            db.log_admin_action(admin_id, "amb_bonus_pay", f"amt={amount} count={success_count}")
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            bot.send_message(message.chat.id, 
                             f"✅ عملیات با موفقیت پایان یافت.\n\n💰 مبلغ: {utils.format_currency(amount)}\n👤 تعداد واریزی موفق: {success_count}",
                             reply_markup=markup)
            
        except Exception as e:
            logger.error(f"Error in handle_amb_bonus: {e}")
            bot.send_message(message.chat.id, "❌ خطای سیستمی در واریز بونوس.")

    # ====================================================
    # ۱۶. پست شیشه‌ای کانال (adm:channel_post)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:channel_post")
    def channel_post_start(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_CHPOST_CONTENT", {"buttons": []})
        bot.send_message(
            call.message.chat.id,
            "📮 **پست شیشه‌ای کانال**\n\n"
            "متن یا عکس پست را ارسال کنید:\n"
            "(برای عکس همراه کپشن، عکس را با کپشن ارسال کنید)",
            parse_mode="Markdown"
        )

    @bot.message_handler(content_types=['text', 'photo'],
                         func=lambda msg: get_admin_state(msg)[0] == "ADM_CHPOST_CONTENT")
    def handle_chpost_content(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        if message.content_type == 'photo':
            state_data["photo"] = message.photo[-1].file_id
            state_data["caption"] = message.caption or ""
            state_data["type"] = "photo"
        else:
            state_data["text"] = message.text
            state_data["type"] = "text"
        
        set_admin_state(message.chat.id, admin_id, "ADM_CHPOST_READY", state_data)
        has_buttons = len(state_data.get("buttons", [])) > 0
        bot.send_message(message.chat.id, "✅ محتوا دریافت شد.",
                         reply_markup=kb.get_channel_post_draft_inline(has_buttons))

    @bot.callback_query_handler(func=lambda call: call.data == "adm:chpost:addmore")
    def chpost_add_button(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        state_name, state_data = get_admin_state(call.message)
        set_admin_state(call.message.chat.id, call.from_user.id, "ADM_CHPOST_BTN_TEXT", state_data)
        bot.send_message(call.message.chat.id,
                         "📎 متن دکمه را وارد کنید (مثلاً: 🔗 بازدید از سایت):")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_CHPOST_BTN_TEXT")
    def handle_chpost_btn_text(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        state_data["btn_text_tmp"] = message.text
        set_admin_state(message.chat.id, admin_id, "ADM_CHPOST_BTN_URL", state_data)
        bot.send_message(message.chat.id, "🔗 لینک دکمه را وارد کنید:")

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_CHPOST_BTN_URL")
    def handle_chpost_btn_url(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        url = message.text.strip()

        # اعتبارسنجی URL
        if not url:
            bot.send_message(message.chat.id, "❌ لینک نمی‌تواند خالی باشد. مجدداً وارد کنید:")
            return
        if not (url.startswith("https://") or url.startswith("http://") or url.startswith("tg://")):
            url = "https://" + url

        # بررسی اینکه URL حاوی حروف فارسی یا کاراکترهای نامعتبر نباشد
        import re as _re
        if _re.search(r'[\u0600-\u06FF\u200c]', url):
            bot.send_message(message.chat.id,
                             "❌ لینک نامعتبر است. لطفاً یک آدرس اینترنتی صحیح (مثلاً https://example.com) وارد کنید:")
            return

        btn_text = state_data.pop("btn_text_tmp", "🔗 لینک")
        state_data.setdefault("buttons", []).append({"text": btn_text, "url": url})
        set_admin_state(message.chat.id, admin_id, "ADM_CHPOST_READY", state_data)
        has_buttons = len(state_data["buttons"]) > 0
        bot.send_message(message.chat.id,
                         f"✅ دکمه «{btn_text}» با لینک `{url}` اضافه شد.",
                         parse_mode="Markdown",
                         reply_markup=kb.get_channel_post_draft_inline(has_buttons))

    @bot.callback_query_handler(func=lambda call: call.data == "adm:chpost:preview")
    def chpost_preview(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        
        state_name, state_data = db.get_user_state(admin_id)
        buttons = state_data.get("buttons", [])
        reply_markup = kb.build_channel_post_markup(buttons) if buttons else None

        try:
            if state_data.get("type") == "photo":
                bot.send_photo(call.message.chat.id, state_data["photo"],
                               caption=f"👁 پیش‌نمایش:\n\n{state_data.get('caption', '')}",
                               reply_markup=reply_markup)
            else:
                bot.send_message(call.message.chat.id,
                                 f"👁 **پیش‌نمایش پست:**\n\n{state_data.get('text', '')}",
                                 parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ خطا در پیش‌نمایش: {e}")

    @bot.callback_query_handler(func=lambda call: call.data == "adm:chpost:finish")
    def chpost_finish(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        state_name, state_data = db.get_user_state(admin_id)

        # بررسی وجود محتوا
        if not state_data.get("type"):
            bot.send_message(call.message.chat.id, "❌ هیچ محتوایی برای ارسال وجود ندارد. دوباره شروع کنید.")
            db.clear_user_state(admin_id)
            return

        buttons = state_data.get("buttons", [])

        # اعتبارسنجی نهایی همه URLها قبل از ارسال
        valid_buttons = []
        import re as _re
        for btn in buttons:
            url = btn.get("url", "").strip()
            if not url:
                continue
            if not (url.startswith("https://") or url.startswith("http://") or url.startswith("tg://")):
                url = "https://" + url
            if _re.search(r'[\u0600-\u06FF\u200c]', url):
                bot.send_message(call.message.chat.id,
                                 f"❌ دکمه «{btn.get('text')}» دارای لینک نامعتبر است. پست لغو شد.")
                db.clear_user_state(admin_id)
                return
            valid_buttons.append({"text": btn.get("text", "🔗 لینک"), "url": url})

        reply_markup = kb.build_channel_post_markup(valid_buttons) if valid_buttons else None

        channel_id = getattr(config, 'MIYANJI_CHANNEL_ID', None) or getattr(config, 'MJCHNL', None)
        if not channel_id:
            bot.send_message(call.message.chat.id,
                             "❌ شناسه کانال تنظیم نشده است.\n"
                             "متغیر محیطی `MJCHNL` یا `MIYANJI_CHANNEL_ID` را تنظیم کنید.",
                             parse_mode="Markdown")
            return

        db.clear_user_state(admin_id)

        try:
            if state_data.get("type") == "photo":
                bot.send_photo(channel_id, state_data["photo"],
                               caption=state_data.get("caption", ""),
                               reply_markup=reply_markup)
            else:
                text_content = state_data.get("text", "")
                if not text_content:
                    bot.send_message(call.message.chat.id, "❌ متن پست خالی است.")
                    return
                bot.send_message(channel_id, text_content,
                                 parse_mode="Markdown", reply_markup=reply_markup)
            db.log_admin_action(admin_id, "channel_post", f"channel={channel_id} buttons={len(valid_buttons)}")
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            try:
                bot.edit_message_text("✅ پست با موفقیت در کانال منتشر شد.",
                                      call.message.chat.id, call.message.message_id, reply_markup=markup)
            except Exception:
                bot.send_message(call.message.chat.id, "✅ پست با موفقیت در کانال منتشر شد.",
                                 reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در ارسال پست کانال: {e}")
            error_msg = str(e)
            if "inline keyboard button URL" in error_msg or "Wrong HTTP URL" in error_msg:
                bot.send_message(call.message.chat.id,
                                 "❌ یکی از لینک‌های دکمه نامعتبر است.\n"
                                 "لطفاً مطمئن شوید همه لینک‌ها با https:// شروع می‌شوند.")
            else:
                bot.send_message(call.message.chat.id, f"❌ خطا در ارسال پست: {e}")

    @bot.callback_query_handler(func=lambda call: call.data == "adm:chpost:cancel")
    def chpost_cancel(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        db.clear_user_state(call.from_user.id)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text("❌ پست شیشه‌ای لغو شد.",
                              call.message.chat.id, call.message.message_id, reply_markup=markup)

    # ====================================================
    # ۱۷. مدیریت ادمین‌ها (adm:manage_admins) — نسخه ساده
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "adm:manage_admins")
    def manage_admins(call: CallbackQuery):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        admin_list = getattr(config, 'ADMIN_IDS', [config.ADMIN_ID])
        text = "👥 **لیست ادمین‌های فعال:**\n\n"
        for aid in admin_list:
            text += f"• `{aid}`\n"
        text += "\n⚙️ برای تغییر ادمین‌ها، متغیر محیطی ADMIN_ID را ویرایش کنید."
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=markup)

    # ====================================================
    # ====================================================
    # ۲۰. تایید فیش چندمرحله‌ای (adm:msreceipt:appr/rej)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:msreceipt:appr:") or call.data.startswith("adm:msreceipt:rej:"))
    def handle_ms_receipt(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return

        approve = call.data.startswith("adm:msreceipt:appr:")
        parts = call.data.split(":")
        # فرمت: adm:msreceipt:appr:CONTRACT_ID:IDX:BUYER_ID
        try:
            contract_id = parts[3]
            buyer_id = int(parts[5]) if len(parts) > 5 else None
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id, "❌ داده نامعتبر.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        try:
            contract = db.get_contract(contract_id)
            if not contract:
                bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
                return

            seller_id = contract.get("seller_id")
            amount = float(contract.get("amount", 0))

            if approve:
                # تایید فیش مرحله‌ای
                idx = int(parts[4])
                milestones = contract.get("milestones") or []
                if 0 <= idx < len(milestones):
                    milestones[idx]["status"] = "paid"
                    db.update_contract(contract_id, {"milestones": milestones, "status": "active"})
                else:
                    db.update_contract(contract_id, {"status": "active"})

                db.append_contract_history(contract_id, f"✅ فیش مرحله {idx+1} تایید شد.", actor_id=admin_id)
                db.log_admin_action(admin_id, "msreceipt_approve", f"cid={contract_id} idx={idx}")
                
                # آپدیت جدول متمرکز
                try:
                    db.supabase.table("pending_receipts").update({"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", f"{contract_id}:{idx}").eq("type", "milestone").eq("status", "pending").execute()
                except: pass
                
                if buyer_id:
                    try:
                        bot.send_message(buyer_id,
                                         f"✅ **فیش واریزی شما تایید شد**\n\nمبلغ مرحله {idx+1} معامله `{contract_id}` تایید گردید.",
                                         parse_mode="Markdown")
                    except Exception:
                        pass
                if seller_id:
                    try:
                        bot.send_message(seller_id,
                                         f"✅ **فیش واریزی کارفرما تایید شد**\n\nمبلغ مرحله {idx+1} معامله `{contract_id}` تایید شد. می‌توانید این مرحله را تحویل دهید.",
                                         reply_markup=kb.get_deliver_project_keyboard(contract_id),
                                         parse_mode="Markdown")
                    except Exception:
                        pass
                result = f"✅ فیش مرحله {idx+1} معامله `{contract_id}` تایید شد."
            else:
                # رد با دلیل
                state_data = {
                    "cid": contract_id, "idx": parts[4], "bid": buyer_id,
                    "msg_id": call.message.message_id, "chat_id": call.message.chat.id
                }
                set_admin_state(call.message.chat.id, admin_id, "WAITING_REJECT_MS_RECEIPT_REASON", state_data)
                
                bot.answer_callback_query(call.id, "📝 لطفاً دلیل رد فیش را بنویسید:", show_alert=True)
                bot.send_message(call.message.chat.id, f"📝 <b>دلیل رد فیش مرحله‌ای معامله <code>{contract_id}</code> را بنویسید:</b>",
                                 reply_to_message_id=call.message.message_id,
                                 parse_mode="HTML",
                                 reply_markup=kb.get_cancel_keyboard())
                return

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, result, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در پردازش فیش چندمرحله‌ای: {e}")
            bot.answer_callback_query(call.id, "❌ خطایی رخ داد.", show_alert=True)

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "WAITING_REJECT_RECEIPT_REASON")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "WAITING_REJECT_RECEIPT_REASON")
    def handle_reject_receipt_reason(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, data = get_admin_state(message)
        cid = data.get("cid", "")
        reason = message.text or "نامشخص"
        db.clear_user_state(admin_id)
        if message.chat.id < 0:
            db.clear_user_state(message.chat.id)

        is_extra_edit = cid.startswith("ee:")
        actual_cid = cid.replace("ee:", "", 1) if is_extra_edit else cid

        contract = db.get_contract(actual_cid)
        if not contract:
            bot.send_message(message.chat.id, "❌ قرارداد یافت نشد.")
            return

        buyer_id = contract.get("buyer_id")
        
        if is_extra_edit:
            db.update_contract(actual_cid, {"status": "active", "extra_edit_price": None})
            db.append_contract_history(actual_cid, f"❌ فیش هزینه ویرایش رد شد. دلیل: {reason}", actor_id=admin_id)
            # آپدیت جدول متمرکز
            try:
                db.supabase.table("pending_receipts").update({"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", actual_cid).eq("type", "milestone").eq("status", "pending").execute()
            except: pass
        else:
            db.update_contract(actual_cid, {"status": "awaiting_payment"})
            db.append_contract_history(actual_cid, f"❌ فیش واریزی رد شد. دلیل: {reason}", actor_id=admin_id)
            # آپدیت جدول متمرکز
            try:
                db.supabase.table("pending_receipts").update({"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", actual_cid).eq("type", "contract").eq("status", "pending").execute()
            except: pass

        # اطلاع‌رسانی به کارفرما
        try:
            msg_type = "هزینه ویرایش" if is_extra_edit else "واریزی"
            bot.send_message(buyer_id, f"❌ **فیش {msg_type} معامله `{actual_cid}` رد شد**\n\n🚫 **دلیل رد:** {reason}\n\nلطفاً فیش صحیح را مجدداً ارسال کنید.", parse_mode="Markdown")
        except: pass

        bot.send_message(message.chat.id, f"✅ فیش معامله `{actual_cid}` با موفقیت رد شد.")

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id if msg.from_user else msg.chat.id)[0] == "WAITING_REJECT_MS_RECEIPT_REASON")
    @bot.channel_post_handler(func=lambda msg: db.get_user_state(msg.chat.id)[0] == "WAITING_REJECT_MS_RECEIPT_REASON")
    def handle_reject_ms_receipt_reason(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        if message.from_user and not is_admin(admin_id): return
        
        _, data = db.get_user_state(admin_id)
        cid = data.get("cid")
        idx = data.get("idx")
        bid = data.get("bid")
        reason = message.text
        db.clear_user_state(admin_id)
        if message.chat.type == 'channel':
            db.clear_user_state(message.chat.id)

        db.update_contract(cid, {"status": "awaiting_payment"})
        db.append_contract_history(cid, f"❌ فیش مرحله {idx} رد شد. دلیل: {reason}", actor_id=admin_id)
        db.log_admin_action(admin_id, "msreceipt_reject_with_reason", f"cid={cid} idx={idx} reason={reason}")

        # آپدیت جدول متمرکز
        try:
            db.supabase.table("pending_receipts").update({"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", f"{cid}:{idx}").eq("type", "milestone").eq("status", "pending").execute()
        except: pass

        if bid:
            try:
                bot.send_message(bid, f"❌ **فیش واریزی مرحله {idx} شما رد شد**\n\n🚫 **دلیل رد:** {reason}\n\nلطفاً فیش صحیح را مجدداً ارسال کنید.", parse_mode="Markdown")
            except: pass

        bot.send_message(message.chat.id, f"✅ فیش مرحله‌ای معامله `{cid}` رد شد.")

    # ====================================================
    # ۲۱. تایید/رد درخواست کیف‌پول (adm:wallet_req:ok/no)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:wallet_req:ok:") or call.data.startswith("adm:wallet_req:no:"))
    def handle_wallet_request(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return

        approve = call.data.startswith("adm:wallet_req:ok:")
        # فرمت: adm:wallet_req:ok:REQUEST_TYPE:USER_ID:AMOUNT[:REQ_ID]
        parts = call.data.split(":")
        try:
            request_type = parts[3]
            target_uid = int(parts[4])
            amount = float(parts[5])
            req_id = int(parts[6]) if len(parts) > 6 else None
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id, "❌ داده نامعتبر.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        try:
            if approve:
                # تایید درخواست
                if request_type == "deposit":
                    # شارژ خودکار کیف پول کاربر
                    if db.update_wallet_balance(target_uid, amount, "admin_charge", "شارژ تایید شده توسط ادمین"):
                        # آپدیت وضعیت تراکنش
                        if req_id:
                            try:
                                db.supabase.table("transactions").update({"status": "completed"}).eq("id", req_id).execute()
                                db.supabase.table("pending_receipts").update({"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", str(req_id)).eq("type", "wallet").execute()
                            except: pass
                            
                        msg = f"✅ **کیف‌پول شما شارژ شد**\n\nمبلغ {utils.format_currency(amount)} به کیف‌پول شما اضافه شد."
                        result = f"✅ درخواست شارژ کاربر `{target_uid}` تایید و کیف پول شارژ شد."
                    else:
                        result = "❌ خطا در شارژ کیف پول کاربر."
                        bot.answer_callback_query(call.id, result, show_alert=True)
                        return
                else:
                    # درخواست برداشت: ابتدا کد پیگیری/مستندات بپرس
                    state_data = {
                        "target_uid": target_uid, 
                        "amount": amount, 
                        "call_msg_id": call.message.message_id,
                        "req_id": req_id
                    }
                    set_admin_state(call.message.chat.id, admin_id, "WAITING_WITHDRAW_TRACKING_CODE", state_data)
                    bot.send_message(
                        call.message.chat.id, 
                        f"📝 لطفاً کد پیگیری (شماره ارجاع) واریز مبلغ {utils.format_currency(amount)} به کاربر <code>{target_uid}</code> را وارد کنید.\n\n"
                        f"💡 **نکته:** می‌توانید به جای متن، **تصویر فیش یا فایل** را ارسال کنید تا برای کاربر ارسال و در سیستم ثبت شود.", 
                        parse_mode="HTML"
                    )
                    return
                
                db.log_admin_action(admin_id, f"wallet_req_approve_{request_type}", f"uid={target_uid} amt={amount}", target_id=target_uid)
            else:
                # درخواست رد با دلیل
                state_data = {
                    "type": request_type, "uid": target_uid, "amt": amount,
                    "msg_id": call.message.message_id, "chat_id": call.message.chat.id,
                    "req_id": req_id
                }
                set_admin_state(call.message.chat.id, admin_id, "WAITING_WALLET_REJECT_REASON", state_data)
                
                bot.answer_callback_query(call.id, "📝 لطفاً دلیل رد این درخواست را بنویسید:", show_alert=True)
                bot.send_message(call.message.chat.id, f"📝 <b>دلیل رد درخواست {request_type} کاربر <code>{target_uid}</code> را بنویسید:</b>",
                                 reply_to_message_id=call.message.message_id,
                                 parse_mode="HTML",
                                 reply_markup=kb.get_cancel_keyboard())
                return

            try:
                bot.send_message(target_uid, msg, parse_mode="Markdown")
            except Exception:
                pass

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, result, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در پردازش درخواست کیف‌پول: {e}")
            bot.answer_callback_query(call.id, "❌ خطایی رخ داد.", show_alert=True)

    @bot.message_handler(func=lambda msg: db.get_user_state(msg.from_user.id if msg.from_user else msg.chat.id)[0] == "WAITING_WALLET_REJECT_REASON")
    @bot.channel_post_handler(func=lambda msg: db.get_user_state(msg.chat.id)[0] == "WAITING_WALLET_REJECT_REASON")
    def handle_wallet_reject_reason(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        if message.from_user and not is_admin(admin_id): return
        
        _, data = db.get_user_state(admin_id)
        rtype = data.get("type")
        uid = data.get("uid")
        amt = data.get("amt")
        reason = message.text
        db.clear_user_state(admin_id)
        if message.chat.type == 'channel':
            db.clear_user_state(message.chat.id)
            
        req_id = data.get("req_id")
            
        # آپدیت وضعیت در جداول
        try:
            from database import supabase
            if req_id and rtype == "deposit":
                supabase.table("transactions").update({"status": "rejected"}).eq("id", req_id).execute()
                supabase.table("pending_receipts").update({"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", str(req_id)).eq("type", "wallet").execute()
            elif rtype == "withdraw":
                # برای برداشت فعلاً وضعیت را در pending_receipts (اگر وجود داشته باشد) آپدیت می‌کنیم
                supabase.table("pending_receipts").update({"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("user_id", uid).eq("type", "wallet").eq("status", "pending").execute()
        except: pass

        msg = f"❌ **درخواست {rtype} شما رد شد**\n\n🚫 **دلیل رد:** {reason}\n\nدر صورت نیاز با پشتیبانی تماس بگیرید."
        try:
            bot.send_message(uid, msg, parse_mode="Markdown")
        except: pass

        db.log_admin_action(admin_id, f"wallet_req_reject_{rtype}", f"uid={uid} amt={amt} reason={reason}", target_id=uid)
        bot.send_message(message.chat.id, f"✅ درخواست {rtype} کاربر `{uid}` با موفقیت رد شد.")

    @bot.message_handler(content_types=['text', 'photo', 'document'], func=lambda msg: get_admin_state(msg)[0] == "WAITING_WITHDRAW_TRACKING_CODE")
    @bot.channel_post_handler(content_types=['text', 'photo', 'document'], func=lambda msg: get_admin_state(msg)[0] == "WAITING_WITHDRAW_TRACKING_CODE")
    def handle_withdraw_tracking_code(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        try:
            state_name, data = get_admin_state(message)
            
            if not data:
                logger.error(f"❌ [ADMIN_WITHDRAW] State data missing for admin {admin_id}")
                bot.send_message(message.chat.id, "❌ نشست شما منقضی شده است. لطفاً دوباره از پنل مدیریت اقدام کنید.")
                db.clear_user_state(admin_id)
                if message.chat.id < 0: db.clear_user_state(message.chat.id)
                return

            if message.from_user and not is_admin(admin_id): return
            
            # استخراج متغیرها با اسامی دقیق و هماهنگ
            target_uid = data.get("target_uid")
            amount = data.get("amount")
            req_id = data.get("req_id")
            mode = data.get("mode", "approve_and_pay")
            call_msg_id = data.get("call_msg_id")
            
            if not req_id:
                logger.error(f"❌ [ADMIN_WITHDRAW] Request ID missing in state for admin {admin_id}")
                bot.send_message(message.chat.id, "❌ خطای سیستمی: شناسه درخواست یافت نشد.")
                return

            # مدیریت دقیق فیش (عکس یا مستند)
            file_id = None
            if message.photo:
                file_id = message.photo[-1].file_id
            elif message.document:
                file_id = message.document.file_id
                
            if not file_id:
                bot.reply_to(message, "❌ **ارسال مستندات (عکس فیش یا فایل) الزامی است.**\nلطفاً فیش واریزی را ارسال کنید.")
                return

            tracking_code_raw = message.caption or message.text or ""
            tracking_code = utils.fa_to_en_digits(tracking_code_raw).strip() if tracking_code_raw else ""
            
            # بررسی وجود درخواست در دیتابیس
            req = db.get_withdrawal_request(req_id)
            if not req:
                logger.error(f"❌ [ADMIN_WITHDRAW] Request #{req_id} not found in database.")
                bot.send_message(message.chat.id, "❌ درخواست یافت نشد.")
                db.clear_user_state(admin_id)
                if message.chat.id < 0: db.clear_user_state(message.chat.id)
                return

            # اگر حالت فقط افزودن مستندات باشد
            if mode == "only_docs":
                if db.update_withdrawal_documents(req_id, [file_id], tracking_code):
                    db.clear_user_state(admin_id)
                    if message.chat.id < 0: db.clear_user_state(message.chat.id)
                    bot.send_message(message.chat.id, f"✅ مستندات جدید با موفقیت به درخواست #{req_id} اضافه شد.")
                    
                    # اطلاع به کاربر
                    try:
                        msg_to_user = f"📎 **مستندات جدیدی به درخواست برداشت #{req_id} اضافه شد:**\n\n📝 توضیحات: {tracking_code}"
                        if message.photo:
                            bot.send_photo(target_uid, file_id, caption=msg_to_user, parse_mode="Markdown")
                        else:
                            bot.send_document(target_uid, file_id, caption=msg_to_user, parse_mode="Markdown")
                    except Exception as e:
                        logger.warning(f"⚠️ [ADMIN_WITHDRAW] Failed to notify user {target_uid}: {e}")
                else:
                    bot.send_message(message.chat.id, "❌ خطا در بروزرسانی مستندات در دیتابیس.")
                return

            # بررسی وضعیت برای جلوگیری از تداخل
            status = req.get("status")
            if status == "paid":
                bot.send_message(message.chat.id, "✅ این درخواست قبلاً با موفقیت تسویه شده است.")
                db.clear_user_state(admin_id)
                if message.chat.id < 0: db.clear_user_state(message.chat.id)
                return

            if status == "rejected":
                bot.send_message(message.chat.id, "❌ این درخواست قبلاً رد شده است.")
                db.clear_user_state(admin_id)
                if message.chat.id < 0: db.clear_user_state(message.chat.id)
                return

            # اجرای عملیات تسویه
            new_status = "queued" if mode == "queue" else "paid"
            if db.resolve_withdrawal_request(req_id, True, admin_id, status=new_status):
                # ثبت مستندات بلافاصله پس از تسویه
                db.update_withdrawal_documents(req_id, [file_id], tracking_code)
                
                db.clear_user_state(admin_id)
                if message.chat.id < 0: db.clear_user_state(message.chat.id)
                
                # ارسال به بایگانی MJNOTE
                try:
                    user_info = db.get_user(target_uid)
                    safe_name = utils.escape_html(f"{user_info.get('first_name_real', '')} {user_info.get('last_name_real', '')}".strip() or "نامشخص")
                    
                    archive_text = (
                        f"📂 <b>بایگانی مستندات تسویه</b>\n"
                        f"🔹 <b>کد درخواست:</b> <code>#{req_id}</code>\n"
                        f"👤 <b>کاربر:</b> {safe_name} (<code>{target_uid}</code>)\n"
                        f"💰 <b>مبلغ:</b> {utils.format_currency(amount)}"
                    )
                    if tracking_code:
                        archive_text += f"\n🧾 <b>کد پیگیری:</b> <code>{utils.escape_html(tracking_code)}</code>"
                    
                    utils.send_admin_alert(bot, archive_text, content_type="photo" if message.photo else "document", file_id=file_id)
                except Exception as e:
                    logger.warning(f"⚠️ [ADMIN_WITHDRAW] Failed to archive: {e}")

                # اطلاع رسانی به کاربر
                if mode == "queue":
                    msg_to_user = (
                        f"⏳ **درخواست برداشت شما در صف واریز قرار گرفت**\n\n"
                        f"💰 مبلغ: `{utils.format_currency(amount)}` \n"
                        f"📌 وضعیت: **سیکل شبا/پایا**\n"
                        f"🧾 کد پیگیری ثبت: `{tracking_code}`\n\n"
                        f"واریز نهایی طی چرخه‌های بانکی انجام خواهد شد."
                    )
                else:
                    msg_to_user = (
                        f"✅ **درخواست برداشت شما تایید و واریز شد**\n\n"
                        f"💰 مبلغ: `{utils.format_currency(amount)}` \n"
                        f"🧾 کد پیگیری واریز: `{tracking_code}`"
                    )
                
                try:
                    if message.photo:
                        bot.send_photo(target_uid, file_id, caption=msg_to_user, parse_mode="Markdown")
                    else:
                        bot.send_document(target_uid, file_id, caption=msg_to_user, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"⚠️ [ADMIN_WITHDRAW] Failed to notify user of payment: {e}")
                
                success_msg = "در صف سیکل شبا قرار گرفت" if mode == "queue" else "تایید و واریز شد"
                bot.send_message(message.chat.id, f"✅ درخواست برداشت {success_msg} و مستندات برای کاربر ارسال شد.")
                db.log_admin_action(admin_id, "withdraw_confirm", f"uid={target_uid} amt={amount} code={tracking_code} mode={mode}", target_id=target_uid)
                
                # حذف کیبورد پیام قبلی
                try:
                    if call_msg_id:
                        bot.edit_message_reply_markup(message.chat.id, call_msg_id, reply_markup=None)
                except: pass
            else:
                bot.send_message(message.chat.id, "❌ خطا در بروزرسانی وضعیت درخواست در دیتابیس.")

        except Exception as e:
            logger.error(f"❌ [CRITICAL_ADMIN_WITHDRAW] Error in receipt handler: {e}", exc_info=True)
            bot.send_message(message.chat.id, f"❌ یک خطای غیرمنتظره رخ داد: {str(e)}")

    # ====================================================
    # ۲۲. مشاهده پرونده کامل معامله (adm:case:CONTRACT_ID)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:case:"))
    def view_full_case(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        cid = call.data.replace("adm:case:", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
            return

        text = utils.generate_contract_text(contract)

        # تاریخچه
        try:
            history = db.get_contract_history(cid)
            if history:
                text += "\n\n📋 **تاریخچه:**\n"
                for h in history[-5:]:
                    text += f"• {h.get('description', '')[:80]}\n"
        except Exception:
            pass

        markup = InlineKeyboardMarkup(row_width=1)
        status = contract.get("status", "")
        if status == "disputed":
            markup.add(
                InlineKeyboardButton("✅ حکم به نفع کارفرما", callback_data=f"adm:dispute:buyer:{cid}"),
                InlineKeyboardButton("✅ حکم به نفع مجری", callback_data=f"adm:dispute:seller:{cid}")
            )
        markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
        bot.send_message(call.message.chat.id, text[:4000], parse_mode="Markdown", reply_markup=markup)

    # ====================================================
    # ۲۳. رای داوری با تقسیم (adm:resolve:decide / adm:resolve:split)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:resolve:decide:") or call.data.startswith("adm:resolve:split:"))
    def handle_resolve_advanced(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return

        bot.answer_callback_query(call.id)

        if call.data.startswith("adm:resolve:split:"):
            cid = call.data.replace("adm:resolve:split:", "", 1)
            set_admin_state(call.message.chat.id, admin_id, "ADM_RESOLVE_SPLIT", {"contract_id": cid})
            bot.send_message(call.message.chat.id,
                             f"⚖️ **تقسیم توافقی — معامله `{cid}`**\n\n"
                             "درصد سهم کارفرما را وارد کنید (۰ تا ۱۰۰):\n"
                             "مثال: `40` یعنی ۴۰٪ به کارفرما و ۶۰٪ به مجری",
                             parse_mode="Markdown")
            return

        # adm:resolve:decide:employer/freelancer:CONTRACT_ID
        parts = call.data.split(":")
        try:
            winner_role = parts[3]  # employer یا freelancer
            cid = parts[4]
        except IndexError:
            bot.answer_callback_query(call.id, "❌ داده نامعتبر.", show_alert=True)
            return

        winner = "buyer" if winner_role == "employer" else "seller"
        # استفاده از همان منطق resolve_dispute_callback
        try:
            contract = db.get_contract(cid)
            if not contract:
                bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            amount = float(contract.get("amount", 0))

            if winner == "buyer":
                db.update_contract(cid, {"status": "cancelled"})
                db.update_wallet_balance(buyer_id, amount, "dispute_refund", f"بازگشت وجه داوری {cid}")
                winner_id, loser_id = buyer_id, seller_id
                winner_text = "حکم به نفع **کارفرما** — وجه بازگشت داده شد."
            else:
                payer = contract.get("commission_payer", "freelancer")
                comm, net, _ = utils.calculate_commission(amount, payer=payer)
                db.update_contract(cid, {"status": "completed"})
                db.update_wallet_balance(seller_id, net, "dispute_release", f"آزادسازی وجه داوری {cid}")
                winner_id, loser_id = seller_id, buyer_id
                winner_text = f"حکم به نفع **مجری** — {utils.format_currency(net)} آزاد شد."

            db.append_contract_history(cid, f"⚖️ رأی داوری: {winner_text}", actor_id=admin_id)
            db.log_admin_action(admin_id, "resolve_decide", f"cid={cid} winner={winner}", target_id=winner_id)

            for uid, msg in [(winner_id, f"✅ **رأی داوری**\n\n{winner_text}"),
                             (loser_id, f"⚖️ **رأی داوری — {cid}**\n\n{winner_text}")]:
                try:
                    bot.send_message(uid, msg, parse_mode="Markdown")
                except Exception:
                    pass

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            try:
                bot.edit_message_text(f"✅ داوری `{cid}` بسته شد.\n\n{winner_text}",
                                      call.message.chat.id, call.message.message_id,
                                      parse_mode="Markdown", reply_markup=markup)
            except Exception:
                bot.send_message(call.message.chat.id, f"✅ داوری `{cid}` بسته شد.\n\n{winner_text}",
                                 parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در resolve_decide: {e}")
            bot.answer_callback_query(call.id, "❌ خطایی رخ داد.", show_alert=True)

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_RESOLVE_SPLIT")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_RESOLVE_SPLIT")
    def handle_resolve_split_input(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        cid = state_data.get("contract_id")
        try:
            buyer_percent = float(message.text.strip().replace(",", ""))
            if not 0 <= buyer_percent <= 100:
                raise ValueError("out of range")
        except ValueError:
            bot.send_message(message.chat.id, "❌ لطفاً عددی بین ۰ و ۱۰۰ وارد کنید.")
            return

        seller_percent = 100 - buyer_percent
        try:
            contract = db.get_contract(cid)
            if not contract:
                bot.send_message(message.chat.id, "❌ قرارداد یافت نشد.")
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            amount = float(contract.get("amount", 0))

            buyer_share = amount * buyer_percent / 100
            seller_share_raw = amount * seller_percent / 100
            payer = contract.get("commission_payer", "freelancer")
            _, seller_net, _ = utils.calculate_commission(seller_share_raw, payer=payer)

            if buyer_share > 0 and buyer_id:
                db.update_wallet_balance(buyer_id, buyer_share, "dispute_split", f"تقسیم داوری {cid} ({buyer_percent}٪)")
            if seller_share_raw > 0 and seller_id:
                db.update_wallet_balance(seller_id, seller_net, "dispute_split", f"تقسیم داوری {cid} ({seller_percent}٪)")

            db.update_contract(cid, {"status": "completed"})
            db.append_contract_history(cid,
                                       f"⚖️ تقسیم داوری: {buyer_percent}٪ کارفرما / {seller_percent}٪ مجری",
                                       actor_id=admin_id)
            db.log_admin_action(admin_id, "resolve_split", f"cid={cid} buyer={buyer_percent}%")

            split_text = (
                f"⚖️ **رأی داوری — تقسیم توافقی**\n\n"
                f"📌 معامله: `{cid}`\n"
                f"👤 کارفرما: {utils.format_currency(buyer_share)} ({buyer_percent}٪)\n"
                f"🛠 مجری: {utils.format_currency(seller_net)} ({seller_percent}٪ منهای کارمزد)"
            )
            for uid in [buyer_id, seller_id]:
                if uid:
                    try:
                        bot.send_message(uid, split_text, parse_mode="Markdown")
                    except Exception:
                        pass

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            bot.send_message(message.chat.id, f"✅ تقسیم داوری `{cid}` ثبت شد.", reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در تقسیم داوری: {e}")
            bot.send_message(message.chat.id, f"❌ خطا: {e}")

    # ====================================================
    # ۲۴. تایید/رد پروژه توسط ادمین (adm:project:appr/rej)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:project:appr:") or call.data.startswith("adm:project:rej:"))
    def handle_project_review(call: CallbackQuery):
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return

        approve = call.data.startswith("adm:project:appr:")
        cid = call.data.replace("adm:project:appr:" if approve else "adm:project:rej:", "", 1)

        bot.answer_callback_query(call.id)
        try:
            contract = db.get_contract(cid)
            if not contract:
                bot.answer_callback_query(call.id, "❌ قرارداد یافت نشد.", show_alert=True)
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            amount = float(contract.get("amount", 0))

            if approve:
                payer = contract.get("commission_payer", "freelancer")
                comm, net, _ = utils.calculate_commission(amount, payer=payer)
                db.update_contract(cid, {"status": "completed"})
                if seller_id:
                    db.update_wallet_balance(seller_id, net, "project_approved", f"تسویه پروژه {cid}")
                db.append_contract_history(cid, "✅ پروژه توسط ادمین تایید و تسویه شد.", actor_id=admin_id)
                db.log_admin_action(admin_id, "project_approve", f"cid={cid} net={net}")
                msg_buyer = f"✅ **پروژه معامله `{cid}` تایید شد.**\n\nتسویه انجام گردید."
                msg_seller = f"✅ **پروژه شما تایید شد**\n\nمبلغ {utils.format_currency(net)} به کیف‌پول شما واریز شد."
                result = f"✅ پروژه `{cid}` تایید و تسویه شد."
            else:
                state_data = {"contract_id": cid,
                              "buyer_id": buyer_id, "seller_id": seller_id}
                set_admin_state(call.message.chat.id, admin_id, "ADM_PROJECT_REJECT_REASON", state_data)

                bot.send_message(call.message.chat.id,
                                 f"⚠️ علت رد پروژه <code>{cid}</code> را بنویسید:",
                                 parse_mode="HTML")
                return

            for uid, msg_text in [(buyer_id, msg_buyer), (seller_id, msg_seller)]:
                if uid and msg_text:
                    try:
                        bot.send_message(uid, msg_text, parse_mode="Markdown")
                    except Exception:
                        pass

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
            except Exception:
                pass
            bot.send_message(call.message.chat.id, result, reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در بررسی پروژه: {e}")
            bot.answer_callback_query(call.id, "❌ خطایی رخ داد.", show_alert=True)

    @bot.message_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_PROJECT_REJECT_REASON")
    @bot.channel_post_handler(func=lambda msg: get_admin_state(msg)[0] == "ADM_PROJECT_REJECT_REASON")
    def handle_project_reject_reason(message: Message):
        admin_id = message.from_user.id if message.from_user else message.chat.id
        state_name, state_data = get_admin_state(message)
        db.clear_user_state(admin_id)
        if message.chat.id < 0: db.clear_user_state(message.chat.id)
        cid = state_data.get("contract_id")
        buyer_id = state_data.get("buyer_id")
        seller_id = state_data.get("seller_id")
        reason = message.text or ""

        try:
            db.update_contract(cid, {"status": "disputed"})
            db.append_contract_history(cid, f"⚠️ پروژه توسط ادمین رد شد: {reason}", actor_id=admin_id)
            db.log_admin_action(admin_id, "project_reject", f"cid={cid}")

            for uid, msg_text in [
                (buyer_id, f"⚠️ **پروژه معامله `{cid}` رد شد**\n\nعلت: {reason}"),
                (seller_id, f"⚠️ **پروژه شما رد شد**\n\nعلت: {reason}\n\nلطفاً اصلاحات لازم را انجام دهید.")
            ]:
                if uid:
                    try:
                        bot.send_message(uid, msg_text, parse_mode="Markdown")
                    except Exception:
                        pass

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🏠 بازگشت به پنل", callback_data="adm:home"))
            bot.send_message(message.chat.id, f"✅ رد پروژه `{cid}` ثبت شد.", reply_markup=markup)
        except Exception as e:
            logger.error(f"خطا در ثبت رد پروژه: {e}")
            bot.send_message(message.chat.id, f"❌ خطا: {e}")

    # ====================================================
    # ۲۶. Catch-all برای callback‌های ادمین ناشناخته
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("adm:"))
    def admin_unknown_callback(call: CallbackQuery):
        """پاسخ به callback‌های adm: که handler اختصاصی ندارند"""
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ دسترسی ندارید.", show_alert=True)
            return
        logger.warning(f"Unhandled admin callback: {call.data}")
        bot.answer_callback_query(call.id, "⚠️ این بخش هنوز در حال توسعه است.", show_alert=True)
