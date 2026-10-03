import time
import logging
import threading
from datetime import datetime, timezone, timedelta
import database as db
import utils

logger = logging.getLogger("Miyanji_Reminders")

def start_reminder_service(bot):
    """شروع سرویس یادآوری در یک ترد جداگانه"""
    reminder_thread = threading.Thread(target=_reminder_loop, args=(bot,), daemon=True)
    reminder_thread.start()
    logger.info("🔔 سرویس یادآوری‌های خودکار فعال شد.")

def _reminder_loop(bot):
    """حلقه اصلی بررسی یادآوری‌ها (هر ۳۰ دقیقه)"""
    while True:
        try:
            _check_payment_reminders(bot)
            _check_deadline_reminders(bot)
        except Exception as e:
            logger.error(f"Error in reminder loop: {e}")
        
        # هر ۳۰ دقیقه یکبار چک کن
        time.sleep(1800)

def _check_payment_reminders(bot):
    """
    بررسی معامله‌هایی که ۲۴ ساعت از امضای نفر دوم گذشته اما هنوز پرداخت نشده‌اند.
    """
    now = datetime.now(timezone.utc)
    try:
        # دریافت معامله‌های در انتظار پرداخت
        res = db.supabase.table("contracts").select("*").eq("status", "awaiting_payment").execute()
        contracts = res.data or []
        
        for c in contracts:
            try:
                cid = c.get("contract_id") or c.get("id")
                # بررسی اینکه آیا قبلاً یادآوری پرداخت فرستاده شده یا خیر
                history = c.get("history") or []
                if any("🔔 یادآوری خودکار پرداخت" in h.get("event", "") for h in history):
                    continue
                
                # پیدا کردن زمان آخرین امضا
                s1 = c.get("buyer_signed_at")
                s2 = c.get("seller_signed_at")
                
                if s1 and s2:
                    ts1 = datetime.fromisoformat(s1.replace('Z', '+00:00'))
                    ts2 = datetime.fromisoformat(s2.replace('Z', '+00:00'))
                    last_signed = max(ts1, ts2)
                    
                    if now - last_signed > timedelta(hours=24):
                        # ارسال یادآوری به کارفرما
                        buyer_id = c.get("buyer_id")
                        if buyer_id:
                            msg = (
                                f"🔔 **یادآوری پرداخت معامله**\n"
                                f"──────────────────\n"
                                f"📌 **معامله:** `{cid}`\n"
                                f"👤 **طرف مقابل معامله را امضا کرده است.**\n\n"
                                f"⚠️ بیش از ۲۴ ساعت از امضای نهایی قرارداد گذشته است. لطفاً جهت فعال‌سازی معامله و شروع کار مجری، نسبت به واریز وجه اقدام کنید.\n\n"
                                f"💡 در صورت عدم واریز تا ۲۴ ساعت آینده، قرارداد ممکن است به طور خودکار منقضی شود."
                            )
                            try:
                                bot.send_message(buyer_id, msg, parse_mode="Markdown")
                                db.append_contract_history(cid, "🔔 یادآوری خودکار پرداخت به کارفرما ارسال شد.")
                            except Exception as e:
                                logger.warning(f"Could not send payment reminder to {buyer_id}: {e}")
            except Exception as inner_e:
                logger.error(f"Error processing contract {c.get('id')} in payment reminders: {inner_e}")
                            
    except Exception as e:
        logger.error(f"Error in _check_payment_reminders: {e}")

def _check_deadline_reminders(bot):
    """
    بررسی معامله‌هایی که ۲۴ ساعت به مهلت تحویل آن‌ها باقی مانده است.
    """
    now = datetime.now(timezone.utc)
    try:
        res = db.supabase.table("contracts").select("*").in_("status", ["active", "in_progress"]).execute()
        contracts = res.data or []
        
        for c in contracts:
            try:
                cid = c.get("contract_id") or c.get("id")
                history = c.get("history") or []
                if any("🔔 یادآوری خودکار مهلت تحویل" in h.get("event", "") for h in history):
                    continue
                
                delivery_deadline = c.get("delivery_deadline")
                deadline_at = None
                
                if delivery_deadline:
                    deadline_at = datetime.fromisoformat(delivery_deadline.replace('Z', '+00:00'))
                elif c.get("paid_at") and c.get("deadline"):
                    # Fallback for old contracts
                    paid_at = datetime.fromisoformat(c.get("paid_at").replace('Z', '+00:00'))
                    deadline_days = int(c.get("deadline", 1))
                    deadline_at = paid_at + timedelta(days=deadline_days)
                
                if not deadline_at:
                    continue
                
                # اگر کمتر از ۲۴ ساعت مانده باشد و هنوز منقضی نشده باشد
                if now < deadline_at and deadline_at - now < timedelta(hours=24):
                    seller_id = c.get("seller_id")
                    if seller_id:
                        msg = (
                            f"🔔 **یادآوری مهلت تحویل پروژه**\n"
                            f"──────────────────\n"
                            f"📌 **معامله:** `{cid}`\n"
                            f"⏳ **کمتر از ۲۴ ساعت به پایان مهلت تحویل باقی مانده است.**\n\n"
                            f"⚠️ لطفاً پروژه را تا قبل از موعد مقرر تحویل دهید.\n\n"
                            f"🚨 **هشدار جریمه دیرکرد:**\n"
                            f"در صورت عدم تحویل به موقع، **۱۰٪ از مبلغ کل معامله** به عنوان جریمه کسر و به حساب کارفرما واریز خواهد شد."
                        )
                        try:
                            bot.send_message(seller_id, msg, parse_mode="Markdown")
                            db.append_contract_history(cid, "🔔 یادآوری خودکار مهلت تحویل به مجری ارسال شد.")
                        except Exception as e:
                            logger.warning(f"Could not send deadline reminder to {seller_id}: {e}")
            except Exception as inner_e:
                logger.error(f"Error processing contract {c.get('id')} in deadline reminders: {inner_e}")
                        
    except Exception as e:
        logger.error(f"Error in _check_deadline_reminders: {e}")
