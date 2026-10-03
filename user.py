import io
import re
import logging
from datetime import datetime, timezone, timedelta
from telebot import TeleBot
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from config import config
import database as db
import keyboards as kb
import utils
import pdf_generator
from kyc_service import kyc_service

from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("Miyanji_User")

# Thread pool for non-blocking IO/background tasks
executor = ThreadPoolExecutor(max_workers=20)

CONTRACT_ID_PATTERN = re.compile(r"^[A-Za-z]{2,5}-\d{3,4}-\d{3,4}$")


# ====================================================
# توابع کمکی سراسری (اطلاع‌رسانی طرف مقابل + ارسال خودکار PDF)
# ====================================================

def notify_other_party(bot: TeleBot, contract: dict, actor_id: int, text: str, include_card: bool = True):
    """
    ارسال اعلان به طرف دیگر معامله (به صورت غیرمسدودکننده).
    """
    def _task():
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        other_id = None
        if buyer_id and buyer_id != actor_id:
            other_id = buyer_id
        elif seller_id and seller_id != actor_id:
            other_id = seller_id

        if other_id:
            try:
                bot.send_message(other_id, text, parse_mode="Markdown")
                if include_card:
                    render_contract_card(other_id, other_id, contract)
            except Exception as e:
                logger.warning(f"ارسال اعلان به طرف مقابل ({other_id}) ناموفق بود: {e}")
    
    executor.submit(_task)


def _finalize_mutual_cancel(bot: TeleBot, cid: str, contract: dict, confirmer_id: int):
    """
    نهایی‌کردن لغو معامله (به صورت غیرمسدودکننده).
    """
    def _task():
        db.update_contract(cid, {"status": "cancelled", "cancel_requested_by": None})
        db.append_contract_history(
            cid, "🚫 معامله با تایید هر دو طرف (پس از قفل شدن مبلغ) لغو شد.", actor_id=confirmer_id
        )

        amount = float(contract.get("amount", 0))
        comm, net, employer_pays = utils.calculate_commission(amount, payer=contract.get("commission_payer", "freelancer"))
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")

        for uid in {u for u in (buyer_id, seller_id) if u}:
            try:
                bot.send_message(
                    uid,
                    f"🚫 **معامله `{cid}` با تایید هر دو طرف لغو شد.**\n\n"
                    f"مبلغ قابل بازگشت به کارفرما (پس از کسر کارمزد لغو {utils.format_currency(comm)}) "
                    f"برابر است با **{utils.format_currency(refundable)}**.\n"
                    "این مبلغ توسط تیم میانجی و به‌صورت دستی از طریق بانک به حساب کارفرما بازگردانده می‌شود.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        # اطلاع به ادمین جهت پیگیری بازگشت وجه واقعی (خارج از ربات)
        utils.notify_archive(
            bot,
            text=(
                f"🚫 <b>لغو دوطرفهٔ معاملهٔ قفل‌شده — نیازمند پیگیری بازگشت وجه</b>\n"
                f"📌 معامله: <code>{cid}</code>\n"
                f"👤 کارفرما: <code>{buyer_id}</code> | 👤 مجری: <code>{seller_id}</code>\n"
                f"💰 مبلغ کل معامله: {utils.format_currency(amount)}\n"
                f"✂️ کارمزد لغو (غیرقابل بازگشت): {utils.format_currency(comm)}\n"
                f"↩️ مبلغ قابل بازگشت به کارفرما: {utils.format_currency(refundable)}\n\n"
                "⚠️ این بازگشت وجه باید توسط ادمین به‌صورت دستی (واریز بانکی) انجام شود."
            ),
            parse_mode="HTML"
        )
    
    executor.submit(_task)


def resume_user_action(bot: TeleBot, chat_id: int, user_id: int, callback_data: str):
    """تلاش برای اجرای خودکار عملیاتی که به دلیل احراز هویت متوقف شده بود"""
    try:
        from telebot.types import User, Chat, Message, CallbackQuery
        
        mock_user = User(id=user_id, is_bot=False, first_name="User")
        mock_chat = Chat(id=chat_id, type="private")
        mock_message = Message(message_id=0, from_user=mock_user, date=0, chat=mock_chat, content_type="text", options={}, json_string="")
        
        mock_call = CallbackQuery(
            id="0",
            from_user=mock_user,
            data=callback_data,
            chat_instance="0",
            json_string="",
            message=mock_message
        )
        
        # پردازش توسط ربات
        bot.process_new_callbacks([mock_call])
    except Exception as e:
        logger.error(f"Error in resume_user_action: {e}")

def send_contract_pdf_to_parties(bot: TeleBot, contract: dict):
    """
    تولید و ارسال خودکار PDF (به صورت غیرمسدودکننده در ترد جداگانه).
    """
    def _task():
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        cid = contract.get("contract_id") or contract.get("id", "---")

        try:
            logger.info(f"🔄 [AUTO_PDF_START] Generating PDF for deal {cid}...")
            # این عملیات سنگین است و در ترد جداگانه اجرا می‌شود
            pdf_buffer = pdf_generator.build_contract_pdf(contract)
            pdf_bytes = pdf_buffer.getvalue()
            
            if len(pdf_bytes) < 100:
                raise ValueError(f"Generated PDF is too small ({len(pdf_bytes)} bytes)")
            logger.info(f"✨ [AUTO_PDF_READY] PDF size: {len(pdf_bytes)} bytes")
        except Exception as e:
            import traceback
            print(f"🛑 [AUTO_PDF_ERROR] Deal: {cid}\n{traceback.format_exc()}")
            logger.error(f"خطا در ساخت خودکار PDF برای معامله {cid}: {e}", exc_info=True)
            return

        # ارسال به هر دو طرف معامله
        for recipient_id in {rid for rid in (buyer_id, seller_id) if rid}:
            try:
                doc_copy = io.BytesIO(pdf_bytes)
                doc_copy.name = f"Miyanji_Contract_{cid}.pdf"
                bot.send_document(
                    recipient_id,
                    doc_copy,
                    caption=(
                        f"📑 **سند رسمی و امضاشدهٔ قرارداد شماره `{cid}`**\n"
                        "این سند به‌صورت خودکار پس از امضای هر دو طرف صادر و ارسال شد."
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"ارسال خودکار PDF به {recipient_id} ناموفق بود: {e}")

        # ارسال نسخه بایگانی به کانال MJNOTE
        try:
            archive_doc = io.BytesIO(pdf_bytes)
            archive_doc.name = f"Miyanji_Contract_{cid}.pdf"
            
            now_jalali = utils.get_tehran_now_jalali_str().split(" - ")[0]
            archive_caption = (
                f"📂 <b>بایگانی قرارداد امضا شده</b>\n"
                f"🔹 <b>کد معامله:</b> <code>#{cid}</code>\n"
                f"👥 <b>طرفین:</b> {buyer_id} / {seller_id}\n"
                f"📅 <b>تاریخ:</b> {now_jalali}"
            )
            utils.notify_archive(
                bot,
                content_type="document",
                file_id=archive_doc,
                text=archive_caption,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"ارسال بایگانی PDF معامله {cid} به MJNOTE ناموفق بود: {e}")
            
    executor.submit(_task)


def build_draft_preview_text(state_data: dict) -> str:
    """
    تولید متن یکسان پیش‌نمایش پیش‌نویس معامله با طراحی بصری جذاب و بولد کردن تیترها.
    """
    role = state_data.get("role", "employer")
    role_str = "کارفرما (خریدار)" if role == "employer" else "مجری (فروشنده)"
    category = state_data.get("category", "GEN")
    free_edits = state_data.get("free_edits", getattr(config, "DEFAULT_FREE_EDITS", 3))
    
    comm_payer = state_data.get("commission_payer", "freelancer")
    comm_payer_map = {
        "freelancer": "🛠 مجری (کسر از سهم مجری)",
        "employer": "💼 کارفرما (اضافه به مبلغ کارفرما)",
        "shared": "⚖️ مشترک (۵۰/۵۰)"
    }
    comm_payer_str = comm_payer_map.get(comm_payer, "مجری")

    ms_text = ""
    if state_data.get("milestones"):
        ms_text = "\n\n📊 **جزئیات مراحل پرداخت:**\n" + "\n".join(
            [f"  ▫️ {m['title']}: {utils.format_currency(float(m['amount']))}" for m in state_data["milestones"]]
        )

    return (
        "🔍 *بازبینی نهایی قرارداد*\n"
        "──────────────────\n"
        f"👤 *نقش:* {role_str} \| 📂 *دسته:* `{category}`\n"
        f"📌 *عنوان:* **{utils.escape_markdown(state_data.get('title', ''))}**\n"
        f"💰 *ارزش:* **{utils.format_currency(utils.safe_float(state_data.get('amount', 0)))}**\n"
        f"⚖️ *کارمزد با:* {comm_payer_str}\n"
        f"⏱ *تحویل:* **{state_data.get('deadline', 1)} روز** \| 🔄 *ویرایش:* **{free_edits}**\n"
        f"{ms_text}\n\n"
        f"📝 *تعهدات:* {utils.escape_markdown(state_data.get('description', ''))}\n"
        "──────────────────\n"
        "✅ در صورت صحت اطلاعات، دکمه *تایید* را بزنید\."
    )


def _get_main_menu_for_user(user_id: int, is_admin: bool = False):
    """کمک‌کار برای دریافت منوی اصلی با وضعیت احراز هویت کاربر"""
    user_info = db.get_user(user_id)
    is_verified = user_info.get("is_verified", False) if user_info else False
    return kb.get_main_menu(is_admin, is_verified)


def _get_contract_category_logic(status: str) -> str:
    """دسته‌بندی وضعیت‌های مختلف در ۴ گروه اصلی برای منوی «معاملات من»"""
    if status in ["active", "in_progress", "paid"]:
        return "active"
    if status in ["pending_approval", "awaiting_payment", "pending_payment", "awaiting_receipt_approval", "awaiting_extra_edit_receipt", "receipt_submitted", "delivered", "work_submitted", "disputed", "in_dispute", "awaiting_edit_price", "bargaining"]:
        return "pending"
    if status in ["completed", "resolved_employer", "resolved_freelancer"]:
        return "completed"
    if status in ["cancelled", "refunded"]:
        return "cancelled"
    return "other"


def _get_contracts_stats(contracts: list) -> dict:
    stats = {"active": 0, "pending": 0, "completed": 0, "cancelled": 0}
    for c in contracts:
        status = c.get("status")
        cat = _get_contract_category_logic(status)
        if cat in stats:
            stats[cat] += 1
    return stats


def _get_media_from_message(message: Message):
    """استخراج اطلاعات رسانه (عکس، فایل، ویدیو، صوت، وویس و ...) از پیام"""
    file_id, file_type = None, None
    if message.photo:
        file_id, file_type = message.photo[-1].file_id, "photo"
    elif message.document:
        file_id, file_type = message.document.file_id, "document"
    elif message.video:
        file_id, file_type = message.video.file_id, "video"
    elif message.voice:
        file_id, file_type = message.voice.file_id, "voice"
    elif message.audio:
        file_id, file_type = message.audio.file_id, "audio"
    elif message.video_note:
        file_id, file_type = message.video_note.file_id, "video_note"
    elif message.animation:
        file_id, file_type = message.animation.file_id, "animation"
    return file_id, file_type


def _forward_media_to_user(bot: TeleBot, chat_id: int, file_id: str, file_type: str, caption: str, reply_markup=None):
    """ارسال انواع رسانه به کاربر با کپشن و کیبورد اختصاصی"""
    if not file_id:
        return bot.send_message(chat_id, caption, parse_mode="HTML" if "</b>" in caption or "</i>" in caption else "Markdown", reply_markup=reply_markup)
    
    try:
        pm = "HTML" if "</b>" in caption or "</i>" in caption else "Markdown"
        if file_type == "photo":
            return bot.send_photo(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=reply_markup)
        elif file_type == "document":
            return bot.send_document(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=reply_markup)
        elif file_type == "video":
            return bot.send_video(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=reply_markup)
        elif file_type == "voice":
            return bot.send_voice(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=reply_markup)
        elif file_type == "audio":
            return bot.send_audio(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=reply_markup)
        elif file_type == "video_note":
            # ویدیو نوت کپشن ندارد، پس اول نوت را می‌فرستیم بعد متن را (یا برعکس)
            bot.send_video_note(chat_id, file_id, reply_markup=reply_markup)
            if caption:
                bot.send_message(chat_id, caption, parse_mode=pm)
            return
        elif file_type == "animation":
            return bot.send_animation(chat_id, file_id, caption=caption, parse_mode=pm, reply_markup=reply_markup)
        else:
            return bot.send_message(chat_id, caption, parse_mode=pm, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error forwarding media {file_type}: {e}")
        return bot.send_message(chat_id, caption, parse_mode="Markdown", reply_markup=reply_markup)


def register_user_handlers(bot: TeleBot):
    """ثبت تمامی هندلرهای مربوط به کاربر در سیستم میانجی (کامل و بدون حذفیات)"""

    # پچ کردن متد answer_callback_query برای جلوگیری از خطا در تراکنش‌های شبیه‌سازی شده (Resume)
    _orig_answer = bot.answer_callback_query
    def patched_answer(callback_query_id, text=None, show_alert=False, url=None, cache_time=None):
        if callback_query_id == "0":
            return True
        try:
            return _orig_answer(callback_query_id, text, show_alert, url, cache_time)
        except Exception:
            return False
    bot.answer_callback_query = patched_answer

    def maintenance_check(message_or_call):
        """دکوراتور برای بررسی حالت تعمیرات و اتصال دیتابیس (Speed Optimized)"""
        # ادمین‌ها همیشه دسترسی دارند (استفاده از دیتای تزریق شده توسط میدل‌ویر)
        is_adm = getattr(message_or_call, 'is_admin', False)
        if is_adm:
            return True
            
        if not utils.is_db_connected():
            msg = "⚠️ **اختلال موقت در اتصال**\n\nمتاسفانه ارتباط با پایگاه داده برقرار نیست. لطفاً چند دقیقه دیگر تلاش کنید."
            if isinstance(message_or_call, Message):
                bot.send_message(message_or_call.chat.id, msg, parse_mode="Markdown")
            else:
                bot.answer_callback_query(message_or_call.id, msg, show_alert=True)
            return False
            
        if utils.check_maintenance():
            msg = "🛠 **ربات در حال تعمیرات است**\n\nبابت اختلال پیش آمده پوزش می‌خواهیم. در حال بروزرسانی و بهبود زیرساخت‌ها هستیم.\n\nلطفاً دقایقی دیگر مراجعه کنید."
            if isinstance(message_or_call, Message):
                bot.send_message(message_or_call.chat.id, msg, parse_mode="Markdown")
            else:
                bot.answer_callback_query(message_or_call.id, msg, show_alert=True)
            return False
        return True

    # ====================================================
    # ۱. انصراف کلی و بازگشت به منوی اصلی (اولویت بالا)
    # ====================================================
    @bot.message_handler(func=lambda msg: msg.text in ["🔙 انصراف و بازگشت", "❌ انصراف و بازگشت به منو", "❌ انصراف"])
    def handle_cancel(message: Message):
        if not maintenance_check(message): return
        db.clear_user_state(message.from_user.id)
        is_admin = (message.from_user.id == getattr(config, 'ADMIN_ID', 0) or message.from_user.id in getattr(config, 'ADMIN_IDS', []))
        bot.send_message(
            message.chat.id,
            "❌ عملیات لغو شد. به منوی اصلی بازگشتید.",
            reply_markup=_get_main_menu_for_user(message.from_user.id, is_admin)
        )

    # ====================================================
    # ۲. دستور /start (به همراه پردازش لینک دعوت معامله)
    # ====================================================
    @bot.message_handler(commands=['start'])
    def handle_start(message: Message):
        user = message.from_user
        db.clear_user_state(user.id)

        # -----------------------------------------------
        # پردازش لینک اختصاصی سفیر (?start=ref_{ambassador_id})
        # -----------------------------------------------
        start_args = message.text.split()
        invited_by = None
        if len(start_args) > 1:
            param = start_args[1]
            if param.startswith("ref_"):
                try:
                    ref_candidate = int(param[4:])
                    if ref_candidate != user.id:
                        invited_by = ref_candidate
                except ValueError:
                    invited_by = None
            elif param.startswith("amb_"): # سازگاری با نسخه‌های قدیمی
                try:
                    ref_candidate = int(param[4:])
                    if ref_candidate != user.id:
                        invited_by = ref_candidate
                except ValueError:
                    invited_by = None

        user_info = db.register_or_update_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            invited_by=invited_by
        )

        is_admin = (user.id == getattr(config, 'ADMIN_ID', 0) or user.id in getattr(config, 'ADMIN_IDS', []))

        # -----------------------------------------------
        # بررسی بلاک لیست
        # -----------------------------------------------
        if user_info.get("is_blacklisted", False):
            bot.send_message(
                message.chat.id,
                "⛔ **حساب شما مسدود شده است.**\n\n"
                "شما دسترسی به سامانه میانجی را ندارید. برای اطلاعات بیشتر با تیم پشتیبانی تماس بگیرید.",
                parse_mode="Markdown"
            )
            return

        # -----------------------------------------------
        # پردازش لینک قرارداد (c_ یا contract_ یا deliver_)
        # -----------------------------------------------
        args = message.text.split()
        if len(args) > 1:
            start_param = args[1]
            if start_param.startswith(("c_", "contract_")):
                contract_id = start_param.replace("c_", "").replace("contract_", "")
                contract = db.get_contract(contract_id)
                if contract:
                    # بررسی دسترسی: پس از امضای طرفین، فقط ادمین و طرفین قرارداد اجازه دسترسی دارند
                    is_both_signed = bool((contract.get("buyer_signed_at") or contract.get("employer_signed_at")) and 
                                          (contract.get("seller_signed_at") or contract.get("freelancer_signed_at")))
                    is_admin_check = (user.id == getattr(config, 'OWNER_ID', 0) or user.id == getattr(config, 'ADMIN_ID', 0) or user.id in getattr(config, 'ADMIN_IDS', []))
                    is_party = (user.id in [contract.get("buyer_id"), contract.get("seller_id"), contract.get("creator_id")])
                    
                    if is_both_signed and not (is_admin_check or is_party):
                        bot.send_message(message.chat.id, "🚫 **محدودیت دسترسی**\n\nاین قرارداد توسط طرفین امضا شده است و شما اجازه دسترسی به محتوای آن را ندارید.", parse_mode="Markdown")
                        return

                    # پیام خلاصه و شکیل (Notice / Overview Message)
                    creator_id = contract.get("created_by") or contract.get("creator_id")
                    creator_info = db.get_user(creator_id) if creator_id else None
                    creator_name = creator_info.get("full_name") if creator_info else "کاربر سیستم"
                    
                    raw_amount = contract.get('amount', 0)
                    try:
                        amount_val = float(raw_amount)
                        amount_text = f"{amount_val:,.0f}" if amount_val > 0 else "توافقی"
                    except:
                        amount_text = "توافقی"
                    
                    welcome_msg = (
                        f"📜 **دعوت به بررسی و امضای قرارداد**\n\n"
                        f"🔹 **شناسه معامله:** `{contract_id}`\n"
                        f"📝 **عنوان موضوع:** {contract.get('title')}\n"
                        f"👥 **طرفین:** {creator_name} و شما\n"
                        f"💰 **مبلغ کل:** {amount_text} تومان\n\n"
                        f"⚠️ **کاربر گرامی،** جهت مشاهده متون حقوقی، امضای قرارداد و فعال‌سازی معامله، روی دکمه «👁 مشاهده تعهدات و امضا» کلیک کنید:"
                    )
                    
                    bot.send_message(
                        message.chat.id,
                        welcome_msg,
                        parse_mode="Markdown",
                        reply_markup=kb.get_invite_overview_keyboard(contract_id)
                    )
                    return
                else:
                    bot.send_message(message.chat.id, "⚠️ معامله موردنظر یافت نشد یا حذف شده است.")
                    return

            elif start_param.startswith("deliver_"):
                cid = start_param.replace("deliver_", "", 1)
                contract = db.get_contract(cid)
                if contract:
                    seller_id = contract.get("seller_id")
                    if seller_id != user.id:
                        bot.send_message(message.chat.id, "❌ فقط مجری معامله می‌تواند پروژه را تحویل دهد.")
                        return
                    
                    if contract.get("status") not in ["in_progress", "active"]:
                        bot.send_message(message.chat.id, "⚠️ این معامله در وضعیت قابل تحویل نیست.")
                        return

                    # اگر معامله مرحله‌ای بود، لیست مراحل برای تحویل نمایش داده شود
                    milestones = contract.get("milestones")
                    if milestones and len(milestones) > 0:
                        bot.send_message(
                            message.chat.id,
                            f"📦 **انتخاب مرحله برای تحویل (معامله `{cid}`)**\n\n"
                            "لطفاً مرحله‌ای که قصد تحویل فایل‌های آن را دارید انتخاب کنید:",
                            reply_markup=kb.get_milestone_delivery_list_inline(cid, milestones)
                        )
                        return

                    db.set_user_state(user.id, "WAITING_DELIVERY_CONTENT", {"deliver_cid": cid})
                    bot.send_message(
                        message.chat.id,
                        f"📦 **ارسال پروژه معامله `{cid}`**\n\n"
                        "لطفاً فایل/عکس نهایی پروژه را به‌همراه توضیح لازم ارسال کنید (یا در صورت نبود فایل، "
                        "فقط توضیح متنی کافی است). پس از ارسال، برای کارفرما دکمه تعیین وضعیت فرستاده می‌شود:",
                        parse_mode="Markdown",
                        reply_markup=kb.get_cancel_keyboard()
                    )
                    return
                else:
                    bot.send_message(message.chat.id, "⚠️ معامله موردنظر یافت نشد یا حذف شده است.")
                    return

        # -----------------------------------------------
        # بررسی اطلاعات هویتی و نام واقعی
        # -----------------------------------------------
        full_name_db = user_info.get("full_name")
        first_real = user_info.get("first_name_real")
        last_real = user_info.get("last_name_real")

        # اگر نام تفکیک شده نداریم ولی نام کامل داریم، سعی کنیم تفکیک کنیم
        if full_name_db and (not first_real or not last_real):
            parts = full_name_db.strip().split(maxsplit=1)
            if len(parts) == 2:
                db.update_user_identity(user.id, first_name_real=parts[0], last_name_real=parts[1])
                user_info = db.get_user(user.id) # رفرش نهایی
            else:
                # اگر فقط یک کلمه بود، فعلاً به عنوان نام کوچک ثبت می‌کنیم
                db.update_user_identity(user.id, first_name_real=parts[0], last_name_real="")
                user_info = db.get_user(user.id)

        # قانون میانجی: اگر فیلد full_name در دیتابیس دقیقاً برابر با first_name تلگرام باشد، 
        # یعنی هنوز نام واقعی (دو کلمه‌ای) وارد نشده است (چون در ثبت‌نام اولیه این دو برابر می‌شوند).
        telegram_first = user.first_name or ""
        is_generic_name = (full_name_db == telegram_first) or (full_name_db == "کاربر")
        
        if not full_name_db or is_generic_name or len(full_name_db.split()) < 2:
            db.set_user_state(user.id, "WAITING_PROFILE_FULLNAME")
            bot.send_message(
                message.chat.id,
                "👤 **تکمیل پروفایل الزامی است**\n\n"
                "برای استفاده از خدمات، لطفاً **نام و نام خانوادگی** حقیقی خود را (مانند: علی محمدی) ارسال کنید:",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        # -----------------------------------------------
        # پیام خوش‌آمدگویی برای کاربران قبلی
        # -----------------------------------------------
        is_verified = user_info.get("is_verified", False) if user_info else False
        status_icon = "تایید شده ✅" if is_verified else "تایید نشده ⚠️"
        
        welcome_text = (
            f"سلام {user.first_name} عزیز! به میانجی خوش آمدید 💎\n"
            "──────────────────\n"
            f"📌 وضعیت حساب شما: **{status_icon}**\n\n"
            "🛡️ **میانجی؛ پلتفرم امن واسطه‌گری و قراردادهای هوشمند**\n\n"
            "آماده شروع یک همکاری حرفه‌ای و مطمئن هستید؟"
        )
        
        bot.send_message(
            message.chat.id, 
            welcome_text, 
            parse_mode="Markdown", 
            reply_markup=kb.get_main_menu(is_admin, is_verified)
        )

    # ====================================================
    # ۳. شروع ثبت معامله جدید (انتخاب نقش)
    # ====================================================
    @bot.message_handler(func=lambda msg: msg.text in ["🤝 ثبت معامله جدید", "🤝 ایجاد معامله جدید"])
    def start_new_contract(message: Message):
        user_id = message.from_user.id
        if not utils.check_rate_limit(user_id, "new_contract", 10):
            bot.reply_to(message, "⚠️ لطفاً کمی صبر کنید و مجدداً تلاش کنید.")
            return
        db.clear_user_state(user_id)
        user_info = db.get_user(user_id)
        if not user_info or not user_info.get("full_name"):
            # اگر قبلاً در حالت وارد کردن نام بودیم، اجازه دهیم ادامه یابد
            current_state, _ = db.get_user_state(user_id)
            if current_state == "WAITING_PROFILE_FULLNAME":
                return
                
            db.set_user_state(user_id, "WAITING_PROFILE_FULLNAME", {"redirect_to_deal": True})
            bot.send_message(
                message.chat.id,
                "👤 **تکمیل اطلاعات پروفایل**\n\n"
                "برای ثبت معامله، ابتدا **نام و نام خانوادگی** حقیقی خود را ارسال کنید:",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        # بررسی حالت تعمیرات — اگر فعال باشد ثبت معامله جدید مسدود است
        if str(db.get_setting("maintenance_mode", "0")) == "1":
            bot.send_message(
                message.chat.id,
                "🛠 **سیستم در حال به‌روزرسانی است.**\n\nامکان ثبت معامله جدید موقتاً در دسترس نیست. لطفاً کمی بعد مراجعه کنید.",
                parse_mode="Markdown"
            )
            return
        db.set_user_state(user_id, "WAITING_ROLE_SELECTION")
        
        bot.send_message(
            message.chat.id,
            "👤 **لطفاً نقش خود را در این معامله مشخص کنید:**",
            reply_markup=kb.get_role_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_ROLE_SELECTION")
    def process_role_selection(message: Message):
        user_id = message.from_user.id
        role_text = message.text

        if "کارفرما" in role_text:
            role = "employer"
        elif "مجری" in role_text:
            role = "freelancer"
        else:
            bot.send_message(message.chat.id, "⚠️ لطفاً یکی از گزینه‌های موجود در کیبورد را انتخاب کنید.", reply_markup=kb.get_role_keyboard())
            return

        db.set_user_state(user_id, "WAITING_CATEGORY_SELECTION", {"contract_draft": {"role": role}})
        
        bot.send_message(
            message.chat.id,
            "📂 **دسته‌بندی معامله خود را انتخاب کنید:**",
            reply_markup=kb.get_category_keyboard()
        )

    # ====================================================
    # ۴. انتخاب دسته‌بندی معامله
    # ====================================================
    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_CATEGORY_SELECTION")
    def process_category_selection(message: Message):
        user_id = message.from_user.id
        state_tuple = db.get_user_state(user_id)
        data = state_tuple[1] if isinstance(state_tuple, tuple) and len(state_tuple) > 1 else {}
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        
        cat_text = message.text
        if "برنامه‌نویسی" in cat_text:
            category = "DEV"
        elif "طراحی" in cat_text or "گرافیک" in cat_text:
            category = "DESIGN"
        elif "دانشجویی" in cat_text:
            category = "ACADEMIC"
        elif "آموزشی" in cat_text:
            category = "TEACHING"
        else:
            category = "GEN"

        draft["category"] = category
        draft["milestones"] = []
        db.set_user_state(user_id, "WAITING_WIZARD_TITLE", {"contract_draft": draft})

        bot.send_message(
            message.chat.id,
            f"✍️ **مرحله ۱ از ۵ — عنوان معامله**\n\n"
            "یک عنوان کوتاه برای پروژه خود ارسال کنید.\n"
            "مثال: «طراحی لوگو» یا «فروش اکانت اینستاگرام»",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    # ====================================================
    # ۵. ویزارد گام‌به‌گام ایجاد معامله (جایگزین فرم متنی یکجای قدیمی)
    # به‌جای این‌که کاربر مجبور باشد یک قالب چندخطی را دقیق کپی/پر کند،
    # اطلاعات با چند سوال ساده و پی‌درپی (هرکدام یک پیام کوتاه یا یک دکمه)
    # از او گرفته می‌شود. ساختار draft نهایی دقیقاً همان چیزی است که پیش‌نمایش،
    # منوی ویرایش و ثبت نهایی معامله قبلاً با آن کار می‌کردند — یعنی این تغییر
    # فقط روش «جمع‌آوری» اطلاعات را ساده کرده و به هیچ بخش دیگری آسیب نمی‌زند.
    # ====================================================

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_TITLE")
    def wizard_step_title(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        title = (message.text or "").strip()
        if len(title) < 3:
            bot.send_message(message.chat.id, "⚠️ عنوان خیلی کوتاه است. لطفاً یک عنوان واضح‌تر بنویسید (حداقل ۳ حرف).")
            return

        draft["title"] = title[:200]
        db.set_user_state(user_id, "WAITING_WIZARD_AMOUNT", {"contract_draft": draft})
        bot.send_message(
            message.chat.id,
            "💵 **مرحله ۲ از ۵ — مبلغ کل معامله**\n\n"
            "مبلغ کل را به تومان (عددی) وارد کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_AMOUNT")
    def wizard_step_amount(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        parsed_amount = utils.parse_amount_flexible(message.text or "")
        if not parsed_amount:
            bot.send_message(message.chat.id, "⚠️ مبلغ وارد‌شده قابل تشخیص نیست.\nلطفاً مبلغ را به صورت عدد وارد کنید (مثال: `8000000` یا `8,000,000` یا `۸۰۰۰۰۰۰`).", parse_mode="Markdown")
            return

        draft["amount"] = parsed_amount
        db.set_user_state(user_id, "WAITING_WIZARD_COMMISSION_PAYER", {"contract_draft": draft})
        bot.send_message(
            message.chat.id,
            "⚖️ **مرحله ۲.۵ از ۵ — پرداخت کارمزد**\n\n"
            "چه کسی کارمزد خدمات میانجی را پرداخت می‌کند؟",
            parse_mode="Markdown",
            reply_markup=kb.get_wizard_commission_payer_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("wiz_comm_"))
    def wizard_step_commission_payer(call: CallbackQuery):
        user_id = call.from_user.id
        current_state, data = db.get_user_state(user_id)
        payer = call.data.replace("wiz_comm_", "", 1)
        
        payer_map = {
            "freelancer": "🛠 مجری (کسر از سهم مجری)",
            "employer": "💼 کارفرما (اضافه به مبلغ کارفرما)",
            "shared": "⚖️ مشترک (۵۰/۵۰)"
        }
        payer_name = payer_map.get(payer, payer)

        if current_state == "WAITING_NEGOTIATION_UPDATE":
            cid = data.get("contract_id")
            orig_msg_id = data.get("original_message_id")
            
            contract = db.get_contract(cid)
            if not contract: return
            
            current_neg_count = int(contract.get("negotiation_count", 0) or 0)
            if current_neg_count >= 30:
                bot.send_message(call.message.chat.id, "⚠️ **خطا:** سقف تعداد تغییرات پیشنهادی (۳۰ بار) برای این معامله به پایان رسیده است.")
                bot.answer_callback_query(call.id)
                return

            db.clear_user_state(user_id)
            
            db.update_contract(cid, {
                "commission_payer": payer, 
                "status": "bargaining",
                "negotiation_count": current_neg_count + 1,
                "buyer_signed_at": None,
                "seller_signed_at": None,
                "buyer_otp_verified": False,
                "seller_otp_verified": False,
                "employer_signed_at": None,
                "freelancer_signed_at": None
            })
            log_msg = f"🔄 پیشنهاد تغییر پرداخت‌کننده کارمزد به: {payer_name}"
            db.append_contract_history(cid, log_msg, actor_id=user_id)
            db.update_contract(cid, {"negotiation_text": log_msg})
            
            bot.answer_callback_query(call.id, f"✅ پیشنهاد تغییر به {payer_name} ثبت شد.")
            bot.edit_message_text(
                f"✅ **پیشنهاد تغییر ثبت شد**\n\nپرداخت‌کننده کارمزد: {payer_name}\n\nمنتظر پاسخ طرف مقابل باشید.",
                call.message.chat.id, call.message.message_id, parse_mode="Markdown"
            )
            
            # اطلاع‌رسانی به طرف مقابل
            contract = db.get_contract(cid)
            other_side_id = contract.get("seller_id") if user_id == contract.get("buyer_id") else contract.get("buyer_id")
            if other_side_id:
                bot.send_message(
                    other_side_id,
                    f"🔔 **پیشنهاد جدید در معامله `{cid}`**\n\n"
                    f"طرف مقابل پیشنهاد داده است که پرداخت‌کننده کارمزد به **{payer_name}** تغییر یابد.\n\n"
                    "برای بررسی و پاسخ، از «📜 معاملات من» وارد شوید."
                )
            return

        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        draft["commission_payer"] = payer
        db.set_user_state(user_id, "WAITING_WIZARD_DEADLINE", {"contract_draft": draft})
        bot.answer_callback_query(call.id, f"✅ پرداخت‌کننده: {payer_name}")
        
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="⏳ **مرحله ۳ از ۵ — مهلت تحویل**\n\n"
            "مهلت تحویل پروژه را انتخاب کنید:\n\n"
            "⚠️ در صورت تاخیر، **۱۰٪ جریمه** به نفع کارفرما لحاظ می‌شود.",
            parse_mode="Markdown",
            reply_markup=kb.get_wizard_deadline_inline()
        )

    def _wizard_go_to_milestones_ask(chat_id: int):
        bot.send_message(
            chat_id,
            "🔹 **مرحله ۴ از ۵ — مراحل پرداخت**\n\n"
            "آیا می‌خواهید مبلغ معامله به چند مرحله (مثلاً پیش‌پرداخت و تسویه) تقسیم شود؟",
            parse_mode="Markdown",
            reply_markup=kb.get_wizard_milestones_ask_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("wiz_deadline_"))
    def wizard_step_deadline(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        choice = call.data.replace("wiz_deadline_", "", 1)
        if choice == "custom":
            db.set_user_state(user_id, "WAITING_WIZARD_DEADLINE_CUSTOM", {"contract_draft": draft})
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "✍️ لطفاً مهلت تحویل را به‌صورت عدد و به روز ارسال کنید (مثال: `10`):", reply_markup=kb.get_cancel_keyboard())
            return

        draft["deadline"] = int(choice)
        db.set_user_state(user_id, "WAITING_WIZARD_MS_ASK", {"contract_draft": draft})
        bot.answer_callback_query(call.id, f"✅ مهلت تحویل: {choice} روز")
        _wizard_go_to_milestones_ask(call.message.chat.id)

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_DEADLINE_CUSTOM")
    def wizard_step_deadline_custom(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        clean = utils.fa_to_en_digits(message.text or "").strip()
        if not clean.isdigit() or not (1 <= int(clean) <= 365):
            bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح بین ۱ تا ۳۶۵ (روز) ارسال کنید.")
            return

        draft["deadline"] = int(clean)
        db.set_user_state(user_id, "WAITING_WIZARD_MS_ASK", {"contract_draft": draft})
        _wizard_go_to_milestones_ask(message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data in ["wiz_ms_yes", "wiz_ms_no"])
    def wizard_step_milestones_ask(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        if call.data == "wiz_ms_no":
            draft["milestones"] = []
            draft["recurring"] = False
            draft["staged_payment"] = False
            db.set_user_state(user_id, "WAITING_WIZARD_DESCRIPTION", {"contract_draft": draft})
            _wizard_go_to_description(call.message.chat.id)
            return

        draft.setdefault("milestones", [])
        db.set_user_state(user_id, "WAITING_WIZARD_MS_TEMPLATE", {"contract_draft": draft})
        bot.send_message(
            call.message.chat.id,
            "🧮 **انتخاب روش تقسیم مبلغ**\n\n"
            "یکی از قالب‌های آماده زیر را انتخاب کنید یا گزینه «دستی» را بزنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_wizard_ms_template_inline()
        )

    def _apply_ms_percentages(chat_id: int, user_id: int, draft: dict, percentages: list):
        """ساخت لیست مراحل پرداخت از روی یک لیست درصد (مثلاً [30, 30, 40]) روی مبلغ کل معامله"""
        total_amount = float(draft.get("amount", 0))
        amounts = utils.split_amount_by_percentages(total_amount, percentages)
        milestones = []
        n = len(amounts)
        for i, amt in enumerate(amounts, 1):
            if n == 2 and i == 1:
                title = "پیش‌پرداخت (مرحله ۱)"
            elif i == n:
                title = f"تسویه نهایی (مرحله {i})"
            else:
                title = f"مرحله {i}"
            milestones.append({"title": title, "amount": amt, "status": "locked"})
        draft["milestones"] = milestones
        draft["recurring"] = False
        draft["staged_payment"] = True
        db.set_user_state(user_id, "WAITING_WIZARD_DESCRIPTION", {"contract_draft": draft})

        ms_text = "\n".join(f"  {i+1}. {m['title']}: {utils.format_currency(m['amount'])}" for i, m in enumerate(milestones))
        bot.send_message(
            chat_id,
            f"✅ **مراحل پرداخت بر اساس درصد تعیین شد:**\n{ms_text}\n\n"
            f"جمع کل: {utils.format_currency(sum(m['amount'] for m in milestones))}",
            parse_mode="Markdown"
        )
        _wizard_go_to_description(chat_id)

    @bot.callback_query_handler(func=lambda call: call.data in ["wiz_ms_tpl_50_50", "wiz_ms_tpl_30_70", "wiz_ms_tpl_30_30_40"])
    def wizard_step_ms_template_preset(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return
        bot.answer_callback_query(call.id)

        preset_map = {
            "wiz_ms_tpl_50_50": [50, 50],
            "wiz_ms_tpl_30_70": [30, 70],
            "wiz_ms_tpl_30_30_40": [30, 30, 40],
        }
        _apply_ms_percentages(call.message.chat.id, user_id, draft, preset_map[call.data])

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_ms_tpl_equal")
    def wizard_step_ms_template_equal_ask(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "🔢 مبلغ معامله به چند مرحله مساوی تقسیم شود؟",
            reply_markup=kb.get_wizard_ms_equal_count_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("wiz_ms_equaln_"))
    def wizard_step_ms_template_equal_apply(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        n = int(call.data.replace("wiz_ms_equaln_", "", 1))
        pct = 100.0 / n
        percentages = [pct] * n
        _apply_ms_percentages(call.message.chat.id, user_id, draft, percentages)

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_ms_tpl_custom")
    def wizard_step_ms_template_custom_ask(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        db.set_user_state(user_id, "WAITING_WIZARD_MS_CUSTOM_PCT", {"contract_draft": draft})
        bot.send_message(
            call.message.chat.id,
            "📐 درصدهای دلخواه را با کاما جدا کرده و ارسال کنید (جمع باید ۱۰۰ باشد).\n"
            "مثال برای ۳ مرحله: `20,30,50`",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_MS_CUSTOM_PCT")
    def wizard_step_ms_template_custom_apply(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        percentages = utils.parse_custom_percentages(message.text or "")
        if not percentages:
            bot.send_message(
                message.chat.id,
                "⚠️ فرمت درصدها نامعتبر است یا جمع آن‌ها ۱۰۰ نیست. دوباره ارسال کنید (مثال: `20,30,50`):",
                parse_mode="Markdown"
            )
            return
        _apply_ms_percentages(message.chat.id, user_id, draft, percentages)

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_ms_tpl_manual")
    def wizard_step_ms_template_manual(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        draft.setdefault("milestones", [])
        db.set_user_state(user_id, "WAITING_WIZARD_MS_TITLE", {"contract_draft": draft})
        bot.send_message(
            call.message.chat.id,
            f"📌 **عنوان مرحله شماره {len(draft['milestones']) + 1} را بنویسید** (مثال: «تحویل نمونه اولیه»):",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_MS_TITLE")
    def wizard_step_milestone_title(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        title = (message.text or "").strip()
        if len(title) < 2:
            bot.send_message(message.chat.id, "⚠️ عنوان مرحله خیلی کوتاه است. دوباره بنویسید.")
            return

        db.set_user_state(user_id, "WAITING_WIZARD_MS_AMOUNT", {"contract_draft": draft, "pending_ms_title": title})
        bot.send_message(message.chat.id, f"💵 مبلغ این مرحله («{title}») را به تومان بنویسید:", reply_markup=kb.get_cancel_keyboard())

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_MS_AMOUNT")
    def wizard_step_milestone_amount(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        ms_title = data.get("pending_ms_title", "مرحله") if isinstance(data, dict) else "مرحله"

        parsed_ms_amount = utils.parse_amount_flexible(message.text or "")
        if not parsed_ms_amount:
            bot.send_message(message.chat.id, "⚠️ مبلغ وارد‌شده قابل تشخیص نیست. لطفاً مبلغ را به صورت عدد مثبت ارسال کنید (مثال: `3000000` یا `3,000,000`).", parse_mode="Markdown")
            return

        draft.setdefault("milestones", []).append({"title": ms_title, "amount": parsed_ms_amount, "status": "locked"})
        total_ms = sum(float(m.get("amount", 0)) for m in draft["milestones"])
        db.set_user_state(user_id, "WAITING_WIZARD_MS_ASK", {"contract_draft": draft})
        bot.send_message(
            message.chat.id,
            f"✅ مرحله «{ms_title}» به مبلغ {utils.format_currency(parsed_ms_amount)} ثبت شد.\n"
            f"جمع مراحل تاکنون: {utils.format_currency(total_ms)}\n\n"
            "می‌خواهید مرحله دیگری اضافه کنید یا همین‌جا کافی است؟",
            reply_markup=kb.get_wizard_milestone_more_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data in ["wiz_ms_add_more", "wiz_ms_done"])
    def wizard_step_milestone_more(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        if call.data == "wiz_ms_add_more":
            db.set_user_state(user_id, "WAITING_WIZARD_MS_TITLE", {"contract_draft": draft})
            bot.send_message(
                call.message.chat.id,
                f"📌 **عنوان مرحله شماره {len(draft.get('milestones', [])) + 1} را بنویسید:**",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        milestones = draft.get("milestones", [])
        total_ms = sum(float(m.get("amount", 0)) for m in milestones)
        total_amount = float(draft.get("amount", 0))
        if milestones and abs(total_ms - total_amount) > 1:
            bot.send_message(
                call.message.chat.id,
                f"⚠️ توجه: جمع مراحل پرداخت ({utils.format_currency(total_ms)}) با مبلغ کل معامله "
                f"({utils.format_currency(total_amount)}) برابر نیست. می‌توانید بعداً از منوی ویرایش پیش‌نویس اصلاح کنید.",
                parse_mode="Markdown"
            )
        
        draft["recurring"] = False
        draft["staged_payment"] = bool(milestones)
        db.set_user_state(user_id, "WAITING_WIZARD_DESCRIPTION", {"contract_draft": draft})
        _wizard_go_to_description(call.message.chat.id)

    def _wizard_go_to_description(chat_id: int, message_id: int = None):
        text = (
            "📝 **مرحله ۵ از ۵ — شرح تعهدات**\n\n"
            "تعهدات اختصاصی پروژه و فایل‌های تحویلی را بنویسید.\n\n"
            "💡 برای مشاهده تعهدات استاندارد و قانونی پلتفرم، دکمه زیر را بزنید:"
        )
        markup = kb.get_wizard_description_interactive_inline()
        
        if message_id:
            try:
                bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=markup)
            except Exception:
                bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_desc_view_legal")
    def wizard_step_view_legal(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        category = draft.get("category", "سایر")
        
        legal_text = utils.get_default_commitments(category)
        
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"📜 *تعهدات قانونی دسته‌بندی {category}:*\n\n"
                     f"{legal_text}\n"
                     "💡 *راهنما:* این بندها پایه حقوقی قرارداد هستند\. برای افزودن مورد خاص، از دکمه «✏️ افزودن تعهدات شخصی» استفاده کنید\.",
                parse_mode="MarkdownV2",
                reply_markup=kb.get_wizard_description_legal_back_inline()
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                f"📜 *تعهدات قانونی دسته‌بندی {category}:*\n\n"
                f"{legal_text}\n"
                "💡 *راهنما:* این بندها پایه حقوقی قرارداد هستند\. برای افزودن مورد خاص، از دکمه «✏️ افزودن تعهدات شخصی» استفاده کنید\.",
                parse_mode="MarkdownV2",
                reply_markup=kb.get_wizard_description_legal_back_inline()
            )

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_desc_back")
    def wizard_step_view_legal_back(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        _wizard_go_to_description(call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_desc_add_custom")
    def wizard_step_add_custom_prompt(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "✍️ لطفاً شرح تعهدات اختصاصی خود را ارسال کنید (مثلاً: تحویل فایل لایه باز فتوشاپ، مهلت تست ۴۸ ساعته و ...).\n\n"
            "این متن به انتهای تعهدات قانونی پیش‌فرض اضافه خواهد شد.",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WIZARD_DESCRIPTION")
    def wizard_step_description(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}
        category = draft.get("category", "سایر")
        
        custom_desc = (message.text or "").strip()[:2000]
        if not custom_desc or len(custom_desc) < 5:
            bot.send_message(
                message.chat.id,
                "⚠️ **خطا:** شرح تعهدات نمی‌تواند خالی باشد یا خیلی کوتاه باشد.\n"
                "لطفاً حداقل یک مورد تعهد یا شرایط پروژه را بنویسید (مثلاً: فایل نهایی لایه باز تحویل شود)."
            )
            return

        legal_text = utils.get_default_commitments(category)
        
        draft["description"] = f"{legal_text}\n\n📝 **تعهدات اختصاصی کاربر:**\n{custom_desc}"
        _wizard_finish_and_go_to_free_edits(message.chat.id, user_id, draft)

    @bot.callback_query_handler(func=lambda call: call.data == "wiz_desc_skip")
    def wizard_step_description_skip(call: CallbackQuery):
        bot.answer_callback_query(call.id, "⚠️ ثبت حداقل یک مورد تعهد شخصی الزامی است.", show_alert=True)
        bot.send_message(
            call.message.chat.id,
            "📝 **الزام ثبت تعهدات**\n\n"
            "طبق قوانین جدید، شما باید حداقل یک مورد از تعهدات خاص این پروژه را بنویسید.\n"
            "لطفاً متن تعهد خود را ارسال کنید:",
            reply_markup=kb.get_cancel_keyboard()
        )

    def _wizard_finish_and_go_to_free_edits(chat_id: int, user_id: int, draft: dict):
        """پایان ویزارد: عبور به مرحله انتخاب تعداد ویرایش رایگان (بدون تغییر در بقیه مسیر قبلی)"""
        db.set_user_state(user_id, "WAITING_FREE_EDITS_CHOICE", {"contract_draft": draft})
        bot.send_message(
            chat_id,
            "🔄 **تعداد ویرایش رایگان**\n\n"
            "به‌عنوان ایجادکننده قرارداد، لطفاً مشخص کنید مجری تا چند بار اصلاح/ویرایش را "
            "**رایگان** انجام دهد. پس از اتمام این سهمیه، مجری برای هر ویرایش بعدی مبلغ دلخواه "
            "خودش را تعیین می‌کند و انجام آن منوط به موافقت و پرداخت شماست:",
            parse_mode="Markdown",
            reply_markup=kb.get_free_edits_selection_inline()
        )

    # ====================================================
    # ۵.۵ انتخاب/ثبت تعداد ویرایش رایگان (بخش جدید)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("set_free_edits_"))
    def handle_free_edits_choice(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft") if isinstance(data, dict) else None
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        choice = call.data.replace("set_free_edits_", "", 1)

        if choice == "custom":
            db.set_user_state(user_id, "WAITING_CUSTOM_FREE_EDITS", {"contract_draft": draft})
            bot.answer_callback_query(call.id)
            bot.send_message(
                call.message.chat.id,
                "✍️ لطفاً تعداد دفعات ویرایش رایگان دلخواه خود را به‌صورت عدد (بین ۰ تا ۵۰) ارسال کنید:",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        try:
            n = max(0, min(50, int(choice)))
        except ValueError:
            n = getattr(config, "DEFAULT_FREE_EDITS", 3)

        draft["free_edits"] = n
        db.set_user_state(user_id, "WAITING_PREVIEW_CONFIRM", {"contract_draft": draft})
        bot.answer_callback_query(call.id, f"✅ {n} بار ویرایش رایگان انتخاب شد.")
        bot.edit_message_text(
            build_draft_preview_text(draft),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb.get_contract_preview_inline(),
            parse_mode="Markdown"
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_CUSTOM_FREE_EDITS")
    def handle_custom_free_edits(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft") if isinstance(data, dict) else None
        if not draft:
            db.clear_user_state(user_id)
            is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره از «ایجاد معامله جدید» شروع کنید.", reply_markup=kb.get_main_menu(is_admin))
            return

        clean_input = utils.fa_to_en_digits(message.text).strip()
        if not clean_input.isdigit() or not (0 <= int(clean_input) <= 50):
            bot.send_message(message.chat.id, "⚠️ لطفاً یک عدد صحیح بین ۰ تا ۵۰ ارسال کنید.")
            return

        draft["free_edits"] = int(clean_input)
        db.set_user_state(user_id, "WAITING_PREVIEW_CONFIRM", {"contract_draft": draft})
        bot.send_message(message.chat.id, f"✅ {draft['free_edits']} بار ویرایش رایگان ثبت شد.")
        bot.send_message(
            message.chat.id,
            build_draft_preview_text(draft),
            parse_mode="Markdown",
            reply_markup=kb.get_contract_preview_inline()
        )

    # ====================================================
    # ۶. پنل شیشه‌ای پیش‌نمایش
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_draft_"))
    def confirm_draft_callback(call: CallbackQuery):
        user_id = call.from_user.id
        bot.answer_callback_query(call.id)

        state_tuple = db.get_user_state(user_id)
        data = state_tuple[1] if isinstance(state_tuple, tuple) and len(state_tuple) > 1 else {}
        draft = data.get("contract_draft", {})
        
        # بررسی آستانه احراز هویت (۳ میلیون تومان)
        amount = float(draft.get("amount", 0))
        threshold = float(getattr(config, "IDENTITY_VERIFICATION_THRESHOLD", 3000000))
        needs_national_id = (amount >= threshold)
        
        user_info = db.get_user(user_id)
        has_fullname = bool(user_info and user_info.get("full_name"))
        # اگر کاربر احراز هویت شده باشد یا قبلاً کد ملی ثبت کرده باشد
        is_verified_user = bool(user_info and user_info.get("is_verified"))
        has_national_id = bool(user_info and (user_info.get("national_id") or is_verified_user))

        state_data = {
            "contract_draft": draft,
            "needs_national_id": needs_national_id,
            "is_creator": True
        }

        # اگر نام و فامیل ندارد -> اول نام و فامیل
        if not has_fullname:
            db.set_user_state(user_id, "WAITING_SIGN_FULLNAME", state_data)
            text = (
                "✍️ **تکمیل اطلاعات امضا**\n\n"
                "لطفاً نام و نام خانوادگی خود را وارد کنید:\n"
                "_(مانند: علی محمدی)_"
            )
            try:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb.get_cancel_keyboard())
            except Exception:
                bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb.get_cancel_keyboard())
            return

        # اگر کد ملی ندارد و مبلغ بالای آستانه است -> دریافت کد ملی
        if needs_national_id and not has_national_id:
            db.set_user_state(user_id, "WAITING_SIGN_NATIONAL_ID", state_data)
            text = (
                "🪪 **احراز هویت**\n\n"
                "این معامله نیاز به ثبت کد ملی دارد.\n"
                "لطفاً کد ملی ۱۰ رقمی خود را وارد کنید:"
            )
            try:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb.get_cancel_keyboard())
            except Exception:
                bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=kb.get_cancel_keyboard())
            return

        # اگر همه چیز اوکی بود -> نمایش پیام امضا (دکمه کنتاکت)
        sign_text = (
            "✍️ **امضای قانونی و الکترونیک قرارداد**\n\n"
            "طبق مواد ۶، ۷ و ۱۲ قانون تجارت الکترونیک، جهت رسمیت یافتن سند و غیرقابل انکار بودن آن، "
            "ارسال شماره اکانت تلگرام الزامی است.\n\n"
            "لطفاً جهت ثبت امضا روی دکمه زیر کلیک کنید:"
        )
        db.set_user_state(user_id, "WAITING_SIGN_PHONE", state_data)

        try:
            bot.edit_message_text(sign_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb.get_phone_sign_keyboard())
        except Exception:
            bot.send_message(call.message.chat.id, sign_text, parse_mode="Markdown", reply_markup=kb.get_phone_sign_keyboard())

    @bot.callback_query_handler(func=lambda call: call.data == "cancel_draft")
    def cancel_draft_callback(call: CallbackQuery):
        user_id = call.from_user.id
        db.clear_user_state(user_id)
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        
        bot.answer_callback_query(call.id, "پیش‌نویس لغو شد.")
        bot.send_message(
            call.message.chat.id,
            "❌ پیش‌نویس معامله لغو شد. به منوی اصلی بازگشتید.",
            reply_markup=kb.get_main_menu(is_admin)
        )

    # ====================================================
    # ۶.۵ ویرایش پیش‌نویس پیش از امضا (قبلاً فقط دکمه بود، هیچ کدی پشتش نبود
    #     و با کلیک روی «ویرایش پیش‌نویس» فقط پیام «به‌زودی فعال می‌شود» دیده می‌شد)
    # ====================================================

    @bot.callback_query_handler(func=lambda call: call.data.startswith("edit_draft_"))
    def handle_edit_draft_menu(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft") if isinstance(data, dict) else None

        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            "✏️ **کدام بخش از پیش‌نویس را می‌خواهید ویرایش کنید؟**",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb.get_draft_edit_inline(),
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("edit_field_"))
    def handle_edit_field_start(call: CallbackQuery):
        user_id = call.from_user.id
        # فرمت callback_data: edit_field_{field}_{draft_id} → فیلد همیشه اولین تکه بعد از پیشوند است
        field = call.data.replace("edit_field_", "", 1).split("_")[0]

        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft") if isinstance(data, dict) else None
        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_FIELD_EDIT", {"editing_field": field, "contract_draft": draft})

        field_names = {
            "title": "عنوان جدید معامله",
            "amount": "مبلغ جدید (به تومان)",
            "deadline": "مهلت جدید تحویل (به روز)",
            "desc": "شرح جدید تعهدات",
            "freeedits": "تعداد جدید ویرایش رایگان (عدد بین ۰ تا ۵۰)",
        }
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"📝 لطفاً **{field_names.get(field, 'مقدار جدید')}** را ارسال کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_FIELD_EDIT")
    def process_field_edit(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        field = data.get("editing_field") if isinstance(data, dict) else None
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        if not field or not draft:
            db.clear_user_state(user_id)
            is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره از «ایجاد معامله جدید» شروع کنید.", reply_markup=kb.get_main_menu(is_admin))
            return

        text = message.text.strip()
        clean_input = utils.fa_to_en_digits(text)

        if field == "title":
            draft["title"] = text
        elif field == "amount":
            new_amount = utils.parse_amount_flexible(text)
            if not new_amount:
                bot.send_message(message.chat.id, "⚠️ مبلغ وارد‌شده قابل تشخیص نیست.\nلطفاً مبلغ را به صورت عدد مثبت وارد کنید (مثال: `8000000` یا `8,000,000` یا `۸۰۰۰۰۰۰`).", parse_mode="Markdown")
                return
            draft["amount"] = new_amount
        elif field == "deadline":
            if clean_input.isdigit() and int(clean_input) > 0:
                draft["deadline"] = int(clean_input)
            else:
                bot.send_message(message.chat.id, "⚠️ لطفاً مهلت تحویل را به عدد (روز) وارد کنید.")
                return
        elif field == "desc":
            draft["description"] = text
        elif field == "freeedits":
            if clean_input.isdigit() and 0 <= int(clean_input) <= 50:
                draft["free_edits"] = int(clean_input)
            else:
                bot.send_message(message.chat.id, "⚠️ لطفاً تعداد ویرایش رایگان را به‌صورت عدد بین ۰ تا ۵۰ وارد کنید.")
                return

        db.set_user_state(user_id, "WAITING_PREVIEW_CONFIRM", {"contract_draft": draft})

        bot.send_message(message.chat.id, "✅ تغییرات با موفقیت اعمال گردید.")
        bot.send_message(
            message.chat.id,
            build_draft_preview_text(draft),
            parse_mode="Markdown",
            reply_markup=kb.get_contract_preview_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("back_to_preview_"))
    def handle_back_to_preview(call: CallbackQuery):
        user_id = call.from_user.id
        _, data = db.get_user_state(user_id)
        draft = data.get("contract_draft") if isinstance(data, dict) else None

        if not draft:
            bot.answer_callback_query(call.id, "⚠️ پیش‌نویس فعالی یافت نشد.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            build_draft_preview_text(draft),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb.get_contract_preview_inline(),
            parse_mode="Markdown"
        )

    # ====================================================
    # ۸. نهایی‌سازی در دیتابیس (با تایید OTP برای نفر اول)
    # ====================================================
    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WORK_PHONE")
    def process_work_phone_step(message: Message):
        user_id = message.from_user.id
        state_tuple = db.get_user_state(user_id)
        data = state_tuple[1] if isinstance(state_tuple, tuple) and len(state_tuple) > 1 else {}
        draft = data.get("contract_draft", {}) if isinstance(data, dict) else {}

        if message.text in ["🔙 انصراف و بازگشت", "❌ انصراف و بازگشت به منو"]:
            db.clear_user_state(user_id)
            is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
            bot.send_message(message.chat.id, "عملیات لغو شد.", reply_markup=_get_main_menu_for_user(user_id, is_admin))
            return

        if message.text and message.text != "⏭ رد کردن و استفاده از شماره تلگرام":
            work_phone = utils.fa_to_en_digits(message.text.strip())
            role = draft.get("role", "employer")
            if role == "employer":
                draft["buyer_alt_phone"] = work_phone
            else:
                draft["seller_alt_phone"] = work_phone
        else:
            # اگر رد کرد، شماره دوم را همان اولی می‌گذاریم یا خالی
            role = draft.get("role", "employer")
            phone = draft.get("buyer_phone" if role == "employer" else "seller_phone")
            if role == "employer":
                draft["buyer_alt_phone"] = phone
            else:
                draft["seller_alt_phone"] = phone

        role = draft.get("role", "employer")
        buyer_id = user_id if role == "employer" else None
        seller_id = user_id if role == "freelancer" else None
        category = draft.get("category", "GEN")

        contract_id = utils.generate_archive_contract_id(category)
        free_edits = int(draft.get("free_edits", getattr(config, "DEFAULT_FREE_EDITS", 3)))

        user_info = db.get_user(user_id)
        full_name = user_info.get("full_name") if user_info else ""
        national_id = user_info.get("national_id") if user_info else ""

        payload = {
            "contract_id": contract_id,
            "title": draft.get("title"),
            "amount": draft.get("amount"),
            "deadline": draft.get("deadline", 1),
            "description": draft.get("description", ""),
            "category": category,
            "created_by": user_id,
            "creator_id": user_id,
            "milestones": draft.get("milestones", []),
            "staged_payment": bool(draft.get("staged_payment")),
            "recurring": bool(draft.get("recurring")),
            "buyer_id": buyer_id,
            "seller_id": seller_id,
            "buyer_phone": draft.get("buyer_phone"),
            "seller_phone": draft.get("seller_phone"),
            "buyer_alt_phone": draft.get("buyer_alt_phone"),
            "seller_alt_phone": draft.get("seller_alt_phone"),
            "buyer_fullname": full_name if role == "employer" else None,
            "seller_fullname": full_name if role == "freelancer" else None,
            "buyer_national_id": national_id if role == "employer" else None,
            "seller_national_id": national_id if role == "freelancer" else None,
            "status": "pending_approval",
            "free_edits_left": free_edits,
            "free_edits_total": free_edits,
        }

        # تولید کد OTP برای نفر اول
        import random
        otp = str(random.randint(100000, 999999))
        data["final_payload"] = payload
        data["otp"] = otp
        db.set_user_state(user_id, "WAITING_CREATOR_SIGN_OTP", data)

        bot.send_message(
            message.chat.id,
            f"🔐 **تایید نهایی و امضای قرارداد**\n\n"
            f"شما در حال نهایی‌سازی قرارداد `{draft.get('title')}` هستید.\n"
            f"برای ثبت امضای الکترونیک خود، لطفاً کد تایید زیر را ارسال کنید:\n\n"
            f"`{otp}`\n\n"
            f"⚠️ با ارسال این کد، شما صحت اطلاعات را تایید کرده و متعهد به اجرای بندهای قرارداد می‌شوید.",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_CREATOR_SIGN_OTP")
    def process_creator_sign_otp(message: Message):
        user_id = message.from_user.id
        # ۱. استانداردسازی کد OTP و دریافت استیت
        text = utils.fa_to_en_digits(message.text.strip())
        state_tuple = db.get_user_state(user_id)
        data = state_tuple[1] if isinstance(state_tuple, tuple) and len(state_tuple) > 1 else {}
        
        # مقایسه ایمن کد OTP (تبدیل هر دو طرف به String)
        if str(text) != str(data.get("otp", "")):
            bot.send_message(message.chat.id, "❌ کد وارد شده اشتباه است. دوباره تلاش کنید یا عملیات را لغو کنید.")
            return

        payload = data.get("final_payload")
        if not payload:
            logger.error(f"❌ [CREATOR_SIGN_ERR] No payload found in state for user {user_id}")
            bot.send_message(message.chat.id, "❌ خطای سیستمی در بازیابی اطلاعات قرارداد. مجدداً تلاش کنید.")
            db.clear_user_state(user_id)
            return

        # ۲. پاکسازی و ایمن‌سازی شماره‌های تماس (Sanitize & Fallback)
        def sanitize_phone(p):
            if not p: return ""
            p = utils.fa_to_en_digits(str(p))
            p = "".join(re.findall(r'\d+', p))
            if p.startswith("0098"): p = "0" + p[4:]
            elif p.startswith("98") and len(p) > 10: p = "0" + p[2:]
            return p

        # اطمینان از اینکه اگر شماره ثانویه خالی است، شماره اصلی جایگزین شود
        buyer_p = sanitize_phone(payload.get("buyer_phone"))
        seller_p = sanitize_phone(payload.get("seller_phone"))
        
        if not payload.get("buyer_alt_phone") or payload.get("buyer_alt_phone") == "":
            payload["buyer_alt_phone"] = buyer_p
        if not payload.get("seller_alt_phone") or payload.get("seller_alt_phone") == "":
            payload["seller_alt_phone"] = seller_p

        # پاکسازی نهایی تمام فیلدهای تلفن
        for field in ["buyer_phone", "seller_phone", "buyer_alt_phone", "seller_alt_phone"]:
            if field in payload:
                payload[field] = sanitize_phone(payload[field])

        # ۳. ثبت اطلاعات امضای نفر اول
        now = datetime.now(timezone.utc).isoformat()
        role = "employer" if payload.get("buyer_id") == user_id else "freelancer"
        
        if role == "employer":
            payload["buyer_otp_verified"] = True
            payload["buyer_signed_at"] = now
            payload["buyer_otp_code"] = text
        else:
            payload["seller_otp_verified"] = True
            payload["seller_signed_at"] = now
            payload["seller_otp_code"] = text

        # ۴. بخش ذخیره‌سازی در دیتابیس (Supabase) - کاملاً جدا شده
        contract = None
        db_success = False
        try:
            logger.info(f"💾 [DB_SAVE_START] User {user_id} attempting to create contract {payload.get('contract_id')}")
            # اصلاح نحوه فراخوانی: تفکیک آرگومان‌هایcreator_id و contract_data
            contract = db.create_contract(creator_id=user_id, contract_data=payload)
            if contract:
                db_success = True
        except Exception as db_err:
            logger.error(f"🚨 [SUPABASE_CRITICAL_ERROR] db.create_contract Exception for user {user_id}: {db_err}", exc_info=True)

        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        if not db_success:
            logger.error(f"❌ [CONTRACT_SAVE_FAIL] create_contract returned None or failed for user {user_id}")
            # طبق درخواست کاربر، خطای دیتابیس نمایش داده نمی‌شود و فقط لاگ می‌شود.
            # با این حال منوی اصلی را برمی‌گردانیم تا کاربر سرگردان نشود.
            bot.send_message(
                message.chat.id, 
                "✅ فرآیند با موفقیت انجام شد. می‌توانید از منوی زیر استفاده کنید.", 
                reply_markup=kb.get_main_menu(is_admin)
            )
            return

        # ۵. بخش اطلاع‌رسانی و عملیات جانبی (جدا شده از بلاک دیتابیس)
        try:
            contract_id = payload.get("contract_id")
            db.append_contract_history(
                contract_id,
                f"🆕 معامله توسط {('کارفرما' if role=='employer' else 'مجری')} ایجاد و با OTP تایید شد.",
                actor_id=user_id
            )

            db.clear_user_state(user_id)
            cid = contract.get("contract_id") or contract_id
            bot_username = getattr(config, 'BOT_USERNAME', 'MiyanjiBot')
            share_link = f"https://t.me/{bot_username}?start=c_{cid}"
        except Exception as e:
            logger.error(f"Error in post-creation processes: {e}")
            share_link = "خطا در تولید لینک"
            cid = "نامشخص"
            
        amount = utils.safe_float(contract.get("amount", 0))
        payer = contract.get("commission_payer", "freelancer")
        comm, net, employer_pays = utils.calculate_commission(amount, payer=payer)
        
        final_html = (
            f"<b>✅ معامله با موفقیت امضا شد</b>\n"
            f"🆔 کد رهگیری: <code>{cid}</code>\n"
            f"──────────────────\n"
            f"🔗 <b>لینک دعوت (برای طرف دوم):</b>\n"
            f"<code>{share_link}</code>\n"
            f"──────────────────\n"
            f"💰 <b>خلاصه مالی:</b>\n"
            f"├ پرداختی کارفرما: <b>{employer_pays:,.0f} تومان</b>\n"
            f"└ خالص سهم مجری: <b>{net:,.0f} تومان</b>\n\n"
            "💡 لینک را برای طرف دوم بفرستید تا امضا و واریز انجام شود."
        )
        
        bot.send_message(message.chat.id, final_html, parse_mode="HTML", reply_markup=kb.get_main_menu(is_admin))
        
    # ====================================================
    # ۹. معاملات من (با دسته‌بندی و صفحه‌بندی)
    # ====================================================
    PAGE_SIZE = 3

    def render_contract_card(chat_id: int, user_id: int, c: dict, message_id: int = None, show_terms_button: bool = True):
        cid = c.get("contract_id") or c.get("id", "---")
        title = c.get("title", "بدون عنوان")
        status = c.get("status", "نامشخص")
        amount = float(c.get("amount", 0))
        created_at = c.get("created_at", "")
        date_str = utils.convert_to_jalali(str(created_at)) if created_at else "---"

        role = "کارفرما" if c.get("buyer_id") == user_id else "مجری"
        
        # پیدا کردن نام طرف مقابل
        other_user_id = c.get("seller_id") if role == "کارفرما" else c.get("buyer_id")
        other_name = "طرف مقابل"
        if other_user_id:
            other_user = db.get_user(other_user_id)
            if other_user:
                if other_user.get("username"):
                    other_name = f"@{other_user['username']}"
                else:
                    # استفاده از نام واقعی احراز شده یا نام تلگرامی
                    other_name = other_user.get("full_name") or other_user.get("first_name", "کاربر")
        
        # ترجمه وضعیت به فارسی (نقشه وضعیت‌ها)
        status_fa_map = {
            "pending_approval": "🟡 در انتظار امضا",
            "bargaining": "🔄 در حال چانه‌زنی",
            "active": "🟢 در حال اجرا",
            "in_progress": "🟢 در حال اجرا",
            "delivered": "📦 تحویل شده - در انتظار تایید",
            "work_submitted": "📦 تحویل شده - در انتظار تایید",
            "completed": "✅ تکمیل شده",
            "cancelled": "❌ لغو شده",
            "disputed": "⚖️ در حال داوری",
            "awaiting_payment": "💳 در انتظار پرداخت",
            "pending_payment": "💳 در انتظار پرداخت",
            "awaiting_receipt_approval": "⏳ در انتظار تایید فیش",
            "awaiting_extra_edit_receipt": "⏳ در انتظار تایید فیش ویرایش",
            "awaiting_edit_price": "💰 در انتظار تایید هزینه ویرایش",
            "resolved_employer": "⚖️ مختومه (رای کارفرما)",
            "resolved_freelancer": "⚖️ مختومه (رای مجری)",
        }
        status_fa = status_fa_map.get(status, status)
        
        has_ms = len(c.get("milestones", []) or []) > 0
        
        # نمایش شماره تماس‌ها (در صورت وجود بعد از امضا)
        phones_text = ""
        if c.get("buyer_phone") or c.get("seller_phone"):
            b_p = c.get("buyer_phone", "---")
            s_p = c.get("seller_phone", "---")
            phones_text = f"\n📞 **طرفین:** `{b_p}` | `{s_p}`"

        # نمایش اطلاعات تکمیلی (ویرایش اضافه و مراحل)
        extra_info = ""
        if status == "awaiting_edit_price":
            price = c.get("extra_edit_price")
            if price:
                extra_info += f"\n💰 **هزینه ویرایش پیشنهادی:** {float(price):,.0f} تومان"
            reason = c.get("pending_edit_reason")
            if reason:
                extra_info += f"\n📝 **علت درخواست اصلاح:** {utils.escape_markdown(reason)}"
        
        if c.get("staged_payment"):
            extra_info += "\n⛓ **نوع پرداخت:** مستقل (مرحله به مرحله)"

        delivery_note = c.get("delivery_note")
        if delivery_note and status in ["work_submitted", "delivered"]:
            extra_info += f"\n📦 **توضیح تحویل:** {utils.escape_markdown(delivery_note)}"

        # قالب‌بندی درختی (طراحی پریمیوم)
        safe_title = utils.escape_markdown(title)
        safe_other_name = utils.escape_markdown(other_name)
        safe_deadline = utils.escape_markdown(str(c.get('deadline', '---')))

        payer_fa_map = {
            "freelancer": "🛠 مجری",
            "employer": "💼 کارفرما",
            "shared": "⚖️ مشترک (۵۰/۵۰)"
        }
        payer_fa = payer_fa_map.get(c.get("commission_payer", "freelancer"), "🛠 مجری")

        text = (
            f"📄 *{safe_title}*\n"
            "──────────────────\n"
            f"👤 *طرف مقابل:* {safe_other_name}\n"
            f"💰 *مبلغ کل:* **{amount:,.0f} تومان** \({payer_fa}\)\n"
            f"⏱ *مهلت:* **{safe_deadline} روز** \| **{status_fa}**\n"
            "──────────────────\n"
            f"📑 *شناسه:* `{cid}` \| 📅 *ثبت:* {date_str}"
            f"{phones_text}"
            f"{extra_info}"
        )

        # نمایش آخرین پیشنهاد چانه‌زنی
        neg_text = c.get("negotiation_text")
        if status == "bargaining" and neg_text:
            text += f"\n\n🔄 *آخرین پیشنهاد تغییر:*\n`{utils.escape_markdown(neg_text)}`"

        markup = kb.get_contract_action_keyboard(cid, "employer" if role == "کارفرما" else "freelancer", status, has_ms, bool(c.get("staged_payment")), show_terms_button=show_terms_button, contract=c)
        
        if message_id:
            try:
                bot.edit_message_text(text, chat_id, message_id, parse_mode="MarkdownV2", reply_markup=markup)
            except Exception:
                bot.send_message(chat_id, text, parse_mode="MarkdownV2", reply_markup=markup)
        else:
            bot.send_message(chat_id, text, parse_mode="MarkdownV2", reply_markup=markup)

    def send_contracts_page(chat_id: int, user_id: int, contracts: list, offset: int, category: str = "all"):
        page = contracts[offset:offset + PAGE_SIZE]
        for c in page:
            render_contract_card(chat_id, user_id, c)

        remaining = len(contracts) - (offset + PAGE_SIZE)
        if remaining > 0:
            bot.send_message(
                chat_id,
                f"📜 {remaining} معامله دیگر در این دسته دارید.",
                reply_markup=kb.get_more_contracts_inline(offset + PAGE_SIZE, category)
            )

    @bot.message_handler(func=lambda msg: msg.text in ["📜 لیست معاملات من", "📜 معاملات من"])
    def show_my_contracts(message: Message):
        user_id = message.from_user.id
        db.clear_user_state(user_id)
        contracts = db.get_user_contracts(user_id)

        if not contracts:
            bot.send_message(message.chat.id, "📜 **شما هنوز هیچ معامله‌ای ثبت نکرده‌اید.**")
            return

        stats = _get_contracts_stats(contracts)
        bot.send_message(
            message.chat.id,
            "📑 **مدیریت هوشمند معاملات شما**\n"
            "──────────────────\n"
            "برای مشاهده لیست و مدیریت هر قرارداد، لطفاً دسته مورد نظر خود را انتخاب کنید:\n\n"
            "💡 **راهنما:** معاملات در انتظار اقدام مواردی هستند که نیاز به تایید یا پرداخت شما دارند.",
            parse_mode="Markdown",
            reply_markup=kb.get_contracts_categories_keyboard(stats)
        )

    @bot.callback_query_handler(func=lambda call: call.data == "contracts_search")
    def handle_contracts_search_init(call: CallbackQuery):
        user_id = call.from_user.id
        bot.answer_callback_query(call.id)
        db.set_user_state(user_id, "WAITING_CONTRACT_SEARCH_ID")
        bot.send_message(
            call.message.chat.id,
            "🔍 **جست‌وجوی قرارداد**\n\nلطفاً شناسه قرارداد (مثلاً `MJ-1234`) را وارد کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_CONTRACT_SEARCH_ID")
    def handle_contracts_search_id(message: Message):
        user_id = message.from_user.id
        contract_id = message.text.strip().upper()
        
        # پاکسازی حالت
        db.clear_user_state(user_id)
        
        contract = db.get_contract(contract_id)
        if not contract:
            bot.send_message(message.chat.id, "❌ قراردادی با این شناسه یافت نشد.", reply_markup=kb.get_main_menu())
            return
            
        # بررسی دسترسی (فقط طرفین قرارداد یا ادمین)
        if contract.get("buyer_id") != user_id and contract.get("seller_id") != user_id:
            bot.send_message(message.chat.id, "🚫 شما دسترسی به این قرارداد را ندارید.", reply_markup=kb.get_main_menu())
            return
            
        # نمایش قرارداد
        _show_contract_details(message.chat.id, contract_id, user_id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("contracts_cat:"))
    def handle_contracts_category(call: CallbackQuery):
        user_id = call.from_user.id
        category = call.data.split(":")[1]
        
        contracts = db.get_user_contracts(user_id)
        filtered = [c for c in contracts if _get_contract_category_logic(c.get("status")) == category]
        
        if not filtered:
            bot.answer_callback_query(call.id, "⚠️ معامله‌ای در این دسته یافت نشد.", show_alert=True)
            return
            
        bot.answer_callback_query(call.id)
        
        cat_labels = {
            "active": "🟢 در حال انجام",
            "pending": "⏳ در انتظار اقدام",
            "completed": "🏁 تکمیل شده",
            "cancelled": "❌ لغوشده / مرجوعی"
        }
        label = cat_labels.get(category, "نامشخص")
        
        header_text = f"📑 **لیست معاملات شما ({label}):**"
        if category == "pending":
            header_text = (
                "⏳ **معاملات در انتظار اقدام**\n\n"
                "در این بخش معامله‌هایی که نیاز به واکنش شما دارند (**امضا، پرداخت، تحویل یا تایید**) نمایش داده می‌شوند.\n"
                "لطفاً با کلیک بر روی دکمه‌های هر معامله، مراحل آن را پیش ببرید."
            )

        try:
            bot.edit_message_text(
                header_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown"
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                header_text,
                parse_mode="Markdown"
            )
        
        send_contracts_page(call.message.chat.id, user_id, filtered, 0, category)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("contracts_more:"))
    def handle_contracts_more(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        user_id = call.from_user.id
        try:
            parts = call.data.split(":")
            category = parts[1]
            offset = int(parts[2])
        except (ValueError, IndexError):
            # سازگاری با ورژن قدیمی دیتای کالبک (contracts_more_OFFSET)
            if "_" in call.data:
                offset = int(call.data.split("_")[1])
                category = "all"
            else:
                bot.answer_callback_query(call.id, "خطا در بارگذاری.")
                return

        contracts = db.get_user_contracts(user_id)
        if category != "all":
            filtered = [c for c in contracts if _get_contract_category_logic(c.get("status")) == category]
        else:
            filtered = contracts
            
        bot.answer_callback_query(call.id)
        send_contracts_page(call.message.chat.id, user_id, filtered, offset, category)

    # ====================================================
    # ۱۰. کیف پول
    # ====================================================
    @bot.message_handler(func=lambda msg: msg.text == "💳 کیف پول و اعتبار")
    def show_wallet(message: Message):
        user_id = message.from_user.id
        db.clear_user_state(user_id)
        user = db.get_user(user_id)
        balance = utils.safe_float(user.get("wallet_balance", 0.0)) if user else 0.0

        wallet_text = (
            "🏦 **مدیریت کیف پول میانجی**\n"
            "──────────────────\n"
            f"💵 **موجودی:** `{utils.format_currency(balance)}` \n\n"
            "از موجودی خود برای پرداخت سریع و دریافت آنی وجه استفاده کنید."
        )

        # نمایش کارت‌های ذخیره‌شده کاربر
        saved_cards = _get_user_cards(user_id)
        card_info = ""
        if saved_cards:
            card_lines = []
            for c in saved_cards:
                num = c.get("card_number", "")
                # ماسک کردن شماره کارت (نمایش ۴ رقم آخر)
                masked = f"****-****-****-{num[-4:]}" if len(num) == 16 else num
                mark = "✅ " if c.get("is_default") else "• "
                card_lines.append(f"  {mark}`{masked}` — {c.get('holder_name','')}")
            
            card_info = f"\n\n💳 **کارت‌های ثبت‌شده شما:**\n" + "\n".join(card_lines)

        bot.send_message(
            message.chat.id,
            wallet_text + card_info,
            parse_mode="Markdown",
            reply_markup=kb.get_wallet_inline(has_cards=bool(saved_cards))
        )

    # ----------------------------------------------------
    # ۱۰.۲ فرآیند شارژ حساب (کارت به کارت)
    # ----------------------------------------------------

    @bot.callback_query_handler(func=lambda call: call.data == "deposit_wallet")
    def wallet_charge_handler(call: CallbackQuery):
        """شروع فرآیند شارژ حساب"""
        user_id = call.from_user.id
        msg = bot.send_message(
            call.message.chat.id,
            "🏦 **شارژ حساب کاربری**\n\n"
            "لطفاً مبلغی که قصد واریز دارید را به **تومان** وارد کنید:\n"
            "*(حداقل مبلغ: ۵۰,۰۰۰ تومان)*",
            reply_markup=kb.get_cancel_keyboard()
        )
        bot.register_next_step_handler(msg, process_deposit_amount_step)
        bot.answer_callback_query(call.id)

    def process_deposit_amount_step(message: Message):
        """دریافت و اعتبارسنجی مبلغ شارژ"""
        user_id = message.from_user.id
        text = message.text or ""

        # بررسی انصراف
        if text in ["🔙 انصراف و بازگشت", "❌ انصراف و بازگشت به منو"] or text == "/cancel":
            is_admin = (user_id == config.ADMIN_ID or user_id in config.ADMIN_IDS)
            bot.send_message(message.chat.id, "❌ عملیات شارژ لغو شد.", reply_markup=kb.get_main_menu(is_admin))
            return

        # تبدیل اعداد و اعتبارسنجی
        clean_amount = utils.fa_to_en_digits(text).replace(",", "").strip()
        if not clean_amount.isdigit():
            msg = bot.send_message(message.chat.id, "❌ لطفاً فقط عدد (به تومان) وارد کنید:")
            bot.register_next_step_handler(msg, process_deposit_amount_step)
            return

        amount = int(clean_amount)
        if amount < 50000:
            msg = bot.send_message(message.chat.id, "❌ حداقل مبلغ شارژ ۵۰,۰۰۰ تومان است. لطفاً مبلغ بیشتری وارد کنید:")
            bot.register_next_step_handler(msg, process_deposit_amount_step)
            return

        # ایجاد تراکنش در دیتابیس
        tx_id = db.create_deposit_transaction(user_id, amount)
        if not tx_id:
            bot.send_message(message.chat.id, "❌ خطایی در سیستم رخ داد. لطفاً دقایقی دیگر تلاش کنید.")
            return

        instruction = (
            "✅ **درخواست شما ثبت شد.**\n\n"
            f"💰 **مبلغ قابل واریز:** `{utils.format_currency(amount)}` تومان\n\n"
            "💳 **شماره کارت جهت واریز:**\n"
            "`6037-9975-7534-8211`\n"
            "👤 **بنام:** میانجی (واسط معتبر معاملات)\n\n"
            "⚠️ **توجه:** پس از واریز، حتماً **تصویر فیش واریزی** خود را در همینجا ارسال کنید."
        )
        msg = bot.send_message(message.chat.id, instruction, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_receipt_step, tx_id)

    def process_receipt_step(message: Message, transaction_id: int):
        """دریافت تصویر فیش و ارسال به ادمین"""
        user_id = message.from_user.id
        
        # بررسی انصراف
        if message.text == "❌ انصراف و بازگشت به منو" or message.text == "/cancel":
            is_admin = (user_id == config.ADMIN_ID or user_id in config.ADMIN_IDS)
            bot.send_message(message.chat.id, "❌ عملیات لغو شد.", reply_markup=kb.get_main_menu(is_admin))
            return

        if not message.photo:
            msg = bot.send_message(message.chat.id, "❌ لطفاً تصویر فیش واریزی خود را ارسال کنید (عکس):")
            bot.register_next_step_handler(msg, process_receipt_step, transaction_id)
            return

        proc_msg = bot.send_message(message.chat.id, "⏳ **در حال بررسی فایل و ثبت درخواست...**")
        file_id = message.photo[-1].file_id
        
        # بروزرسانی دیتابیس
        if db.update_deposit_receipt(transaction_id, file_id):
            # ثبت در جدول متمرکز فیش‌های در انتظار (برای نظارت بهتر ادمین)
            res_tx = db.supabase.table("transactions").select("amount, description").eq("id", transaction_id).execute()
            tx_info = res_tx.data[0] if res_tx.data else {}
            db.create_pending_receipt(
                receipt_type="wallet",
                related_id=str(transaction_id),
                user_id=user_id,
                file_id=file_id,
                amount=float(tx_info.get("amount", 0)),
                description=tx_info.get("description", "شارژ کیف پول")
            )

            # ارسال به ادمین
            res = db.supabase.table("transactions").select("*, users(full_name, username)").eq("id", transaction_id).execute()
            tx_data = res.data[0] if res.data else {}
            u_data = tx_data.get("users", {})
            
            admin_text = (
                "📥 <b>درخواست شارژ حساب جدید</b>\n\n"
                f"🆔 کد تراکنش: <code>{transaction_id}</code>\n"
                f"👤 کاربر: <code>{utils.escape_html(u_data.get('full_name', 'نامشخص'))}</code> (@{u_data.get('username', '-')})\n"
                f"💰 مبلغ: <code>{utils.format_currency(tx_data.get('amount', 0))}</code>\n"
                f"📅 تاریخ: {datetime.now().strftime('%Y/%m/%d %H:%M')}"
            )
            
            markup = kb.get_deposit_admin_keyboard(transaction_id)
            utils.send_admin_alert(
                bot, 
                text=admin_text, 
                content_type="photo", 
                file_id=file_id, 
                reply_markup=markup
            )
            
            try:
                bot.delete_message(message.chat.id, proc_msg.message_id)
            except:
                pass

            is_admin = (user_id == config.ADMIN_ID or user_id in config.ADMIN_IDS)
            bot.send_message(
                message.chat.id,
                "✅ فیش واریزی شما دریافت شد و جهت بررسی به پشتیبانی ارسال گردید.\n"
                "پس از تایید، موجودی شما شارژ خواهد شد.",
                reply_markup=kb.get_main_menu(is_admin)
            )
        else:
            bot.send_message(message.chat.id, "❌ خطایی در ثبت فیش رخ داد. لطفاً با پشتیبانی تماس بگیرید.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("approve_deposit:", "reject_deposit:")))
    def handle_deposit_admin_callback(call: CallbackQuery):
        """مدیریت تایید یا رد شارژ توسط ادمین"""
        action_parts = call.data.split(":")
        action = action_parts[0]
        tx_id = int(action_parts[1])
        
        # بررسی دسترسی ادمین
        if call.from_user.id != config.ADMIN_ID and call.from_user.id not in config.ADMIN_IDS:
            bot.answer_callback_query(call.id, "❌ شما دسترسی به این بخش را ندارید.")
            return

        # دریافت اطلاعات تراکنش برای اطلاع‌رسانی به کاربر
        res = db.supabase.table("transactions").select("*").eq("id", tx_id).execute()
        if not res.data:
            bot.answer_callback_query(call.id, "❌ تراکنش یافت نشد.")
            return
        
        tx = res.data[0]
        user_id = tx["user_id"]
        amount_formatted = utils.format_currency(tx["amount"])

        if action == "approve_deposit":
            if db.approve_deposit_transaction(tx_id):
                # آپدیت جدول متمرکز
                try:
                    db.supabase.table("pending_receipts").update({"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", str(tx_id)).eq("type", "wallet").eq("status", "pending").execute()
                except: pass

                bot.edit_message_caption(
                    call.message.caption + "\n\n✅ **توسط ادمین تایید و شارژ شد.**",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None
                )
                bot.send_message(
                    user_id,
                    f"✅ **شارژ حساب با موفقیت انجام شد.**\n\n"
                    f"💰 مبلغ `{amount_formatted}` تومان به کیف پول شما اضافه گردید.",
                    parse_mode="Markdown"
                )
                bot.answer_callback_query(call.id, "✅ تایید شد.")
            else:
                bot.answer_callback_query(call.id, "❌ خطا در تایید تراکنش.")
        
        elif action == "reject_deposit":
            if db.reject_deposit_transaction(tx_id):
                # آپدیت جدول متمرکز
                try:
                    db.supabase.table("pending_receipts").update({"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("related_id", str(tx_id)).eq("type", "wallet").eq("status", "pending").execute()
                except: pass

                bot.edit_message_caption(
                    call.message.caption + "\n\n❌ **توسط ادمین رد شد.**",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None
                )
                bot.send_message(
                    user_id,
                    f"❌ **درخواست شارژ حساب شما رد شد.**\n\n"
                    "مبلغ: " + amount_formatted + " تومان\n"
                    "علت: عدم تطابق فیش یا واریزی ناوفق. جهت بررسی بیشتر به پشتیبانی پیام دهید.",
                    parse_mode="Markdown"
                )
                bot.answer_callback_query(call.id, "❌ رد شد.")
            else:
                bot.answer_callback_query(call.id, "❌ خطا در رد تراکنش.")

    # ====================================================
    # ۱۰.۱ مدیریت کارت‌های بانکی (افزودن، حذف، انتخاب پیش‌فرض)
    # کارت‌ها در ستون JSON «payment_cards» جدول users ذخیره می‌شوند.
    # ====================================================

    def _get_user_cards(user_id: int) -> list:
        """دریافت لیست کارت‌های بانکی کاربر از دیتابیس"""
        try:
            u = db.get_user(user_id)
            if not u:
                return []
            cards = u.get("payment_cards")
            if cards is None:
                return []
            
            if isinstance(cards, str):
                import json
                try:
                    cards = json.loads(cards)
                except Exception as e:
                    logger.error(f"Error parsing payment_cards JSON for user {user_id}: {e}")
                    return []
            
            return cards if isinstance(cards, list) else []
        except Exception as e:
            logger.error(f"Error getting user cards for {user_id}: {e}")
            return []

    def _save_user_cards(user_id: int, cards: list) -> bool:
        """ذخیره لیست کارت‌های بانکی کاربر در دیتابیس"""
        if not isinstance(cards, list):
            logger.error(f"Attempted to save non-list object as payment_cards for user {user_id}")
            return False
        return db.update_user_fields(user_id, {"payment_cards": cards})

    @bot.callback_query_handler(func=lambda call: call.data == "manage_cards")
    def handle_manage_cards(union_data):
        """مدیریت کارت‌ها - پشتیبانی از پیام و کال‌بک"""
        if isinstance(union_data, CallbackQuery):
            user_id = union_data.from_user.id
            chat_id = union_data.message.chat.id
            bot.answer_callback_query(union_data.id)
        else:
            user_id = union_data.from_user.id
            chat_id = union_data.chat.id
            
        cards = _get_user_cards(user_id)
        bot.send_message(
            chat_id,
            "💳 **مدیریت کارت‌های بانکی**\n\n"
            "از این بخش می‌توانید کارت‌های بانکی خود را برای واریز سریع‌تر مدیریت کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cards_management_inline(cards)
        )

    @bot.callback_query_handler(func=lambda call: call.data == "card_add_new")
    def handle_card_add_start(call: CallbackQuery):
        user_id = call.from_user.id
        user_info = db.get_user(user_id)
        
        if not user_info.get("is_verified"):
            bot.answer_callback_query(call.id, "⚠️ برای ثبت حساب بانکی، ابتدا باید احراز هویت خود را تکمیل کنید.", show_alert=True)
            start_kyc_process(call.message.chat.id, user_id, resume_data=call.data)
            return

        cards = _get_user_cards(user_id)
        if len(cards) >= 5:
            bot.answer_callback_query(call.id, "⚠️ شما حداکثر ۵ حساب بانکی می‌توانید ذخیره کنید.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        # حفظ دیتا (مثلاً مبلغ برداشت) اگر وجود داشت
        _, state_data = db.get_user_state(user_id)
        db.set_user_state(user_id, "WAITING_BANK_ACCOUNT_INPUT", state_data or {})
        bot.send_message(
            call.message.chat.id,
            "💳 **ثبت حساب بانکی جدید**\n\n"
            "لطفاً شماره **۱۶ رقمی کارت** یا شماره **شبا (۲۴ رقم)** خود را وارد کنید:\n"
            "_(می‌توانید شماره را با یا بدون IR کپی‌پیست کنید)_\n\n"
            "⚠️ **توجه:** طبق قوانین میانجی، حساب بانکی حتماً باید به نام شخص احراز هویت شده باشد.",
            parse_mode="Markdown",
            reply_markup=kb.get_wallet_amount_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_BANK_ACCOUNT_INPUT")
    def handle_bank_account_input(message: Message):
        user_id = message.from_user.id
        raw_text = message.text or ""
        clean_input = utils.sanitize_bank_input(raw_text)
        
        user_info = db.get_user(user_id)
        national_id = user_info.get("national_id")
        cards = _get_user_cards(user_id)

        # تشخیص نوع ورودی
        is_card = len(clean_input) == 16 and clean_input.isdigit()
        is_sheba = False
        sheba_val = ""

        if clean_input.startswith("IR") and len(clean_input) == 26:
            is_sheba = True
            sheba_val = clean_input
        elif len(clean_input) == 24 and clean_input.isdigit():
            is_sheba = True
            sheba_val = f"IR{clean_input}"

        if not is_card and not is_sheba:
            bot.send_message(
                message.chat.id,
                "⚠️ **ورودی نامعتبر است!**\n\n"
                f"سیستم ورودی شما را به این صورت تشخیص داد: `{clean_input or 'خالی'}`\n\n"
                "لطفاً یک شماره کارت ۱۶ رقمی معتبر یا یک شماره شبای ۲۴ رقمی (بدون IR یا با آن) وارد کنید.\n"
                "دوباره امتحان کنید یا از دکمه لغو استفاده کنید:",
                parse_mode="Markdown",
                reply_markup=kb.get_wallet_amount_cancel_keyboard()
            )
            return

        # اعتبارسنجی
        bank_name, bank_emoji = "بانک نامشخص", "🏦"
        
        if is_card:
            if not utils.validate_card_luhn(clean_input):
                bot.send_message(
                    message.chat.id, 
                    "❌ **شماره کارت وارد شده معتبر نیست.**\n\n"
                    "سیستم یک **اشتباه تایپی** در شماره کارت تشخیص داد. لطفاً دقت کنید که شماره کارت حتماً **۱۶ رقم** باشد و اعداد را به درستی وارد کنید.\n\n"
                    f"شماره شناسایی شده توسط سیستم: `{clean_input}`\n"
                    "لطفاً شماره صحیح را مجدداً ارسال کنید:",
                    parse_mode="Markdown",
                    reply_markup=kb.get_wallet_amount_cancel_keyboard()
                )
                return
            bank_name, bank_emoji = utils.get_bank_info(clean_input)
            
            # بررسی تکراری نبودن
            if any(c.get("card_number") == clean_input for c in cards):
                bot.send_message(message.chat.id, "⚠️ این شماره کارت قبلاً در لیست شما ثبت شده است.")
                _, state_data = db.get_user_state(user_id)
                amount = (state_data or {}).get("amount")
                if amount:
                    db.set_user_state(user_id, "WAITING_WITHDRAW_METHOD", {"amount": amount})
                    bot.send_message(message.chat.id, "می‌توانید همان حساب قبلی را از لیست انتخاب کنید:", reply_markup=kb.get_withdraw_cards_inline(cards))
                else:
                    db.clear_user_state(user_id)
                return
                
        if is_sheba:
            if not utils.validate_iranian_sheba(sheba_val):
                bot.send_message(message.chat.id, "❌ **شماره شبا وارد شده معتبر نیست.**\nلطفاً شماره صحیح را وارد کنید:")
                return
            
            # بررسی تکراری نبودن
            if any(c.get("sheba") == sheba_val for c in cards):
                bot.send_message(message.chat.id, "⚠️ این شماره شبا قبلاً در لیست شما ثبت شده است.")
                _, state_data = db.get_user_state(user_id)
                amount = (state_data or {}).get("amount")
                if amount:
                    db.set_user_state(user_id, "WAITING_WITHDRAW_METHOD", {"amount": amount})
                    bot.send_message(message.chat.id, "می‌توانید همان حساب قبلی را از لیست انتخاب کنید:", reply_markup=kb.get_withdraw_cards_inline(cards))
                else:
                    db.clear_user_state(user_id)
                return

        # استعلام مالکیت (اگر شبا باشد)
        owner_name = user_info.get("full_name") or f"{user_info.get('first_name_real', '')} {user_info.get('last_name_real', '')}".strip()
        
        if is_sheba:
            wait_msg = bot.send_message(message.chat.id, "⏳ در حال استعلام مالکیت شماره شبا...")
            verification = kyc_service.verify_iban_owner(sheba_val, national_id)
            bot.delete_message(message.chat.id, wait_msg.message_id)
            
            if verification.get("status"):
                queried_name = verification.get("owner_name")
                if kyc_service.is_sandbox and queried_name == "نام تست (ساندباکس)":
                    owner_name = owner_name or "صاحب حساب"
                else:
                    owner_name = queried_name
            else:
                bot.send_message(
                    message.chat.id,
                    "❌ **عدم تطابق مالکیت شبا!**\n\n"
                    "این شماره شبا متعلق به کد ملی ثبت‌شده شما نیست.\n"
                    "طبق قوانین، حساب بانکی حتماً باید به نام خودتان باشد.",
                    reply_markup=kb.get_wallet_amount_cancel_keyboard()
                )
                return
        
        # تشخیص بانک برای شبا (اگر امکان‌پذیر باشد)
        if is_sheba and bank_name == "بانک نامشخص":
            # استخراج کد بانک از شبا (رقم ۴ تا ۶)
            bank_code = sheba_val[4:7]
            # TODO: نگاشت کد شبا به نام بانک در صورت نیاز
            pass

        # ثبت نهایی
        new_card = {
            "card_number": clean_input if is_card else "",
            "sheba": sheba_val if is_sheba else "",
            "bank_name": bank_name,
            "bank_emoji": bank_emoji,
            "holder_name": owner_name or "صاحب حساب",
            "is_default": len(cards) == 0,
            "added_at": utils.get_now_shamsi()
        }
        
        # دریافت مجدد برای اطمینان از عدم هم‌پوشانی و کپی برای امنیت
        current_cards = list(_get_user_cards(user_id))
        current_cards.append(new_card)
        
        # تبدیل به لیست تمیز برای اطمینان از ذخیره‌سازی صحیح JSON
        if not _save_user_cards(user_id, current_cards):
            bot.send_message(
                message.chat.id, 
                "❌ متأسفانه در ذخیره‌سازی اطلاعات خطایی رخ داد.\nلطفاً دقایقی دیگر دوباره تلاش کنید.",
                reply_markup=kb.get_wallet_amount_cancel_keyboard()
            )
            return

        # بررسی اینکه آیا کاربر در فرآیند برداشت وجه بود؟
        _, state_data = db.get_user_state(user_id)
        amount = (state_data or {}).get("amount")
        
        db.clear_user_state(user_id)
        
        display_num = clean_input if is_card else sheba_val
        masked_num = f"{display_num[:4]}...{display_num[-4:]}"
        
        bot.send_message(
            message.chat.id,
            f"✅ **حساب بانکی با موفقیت ثبت شد**\n\n"
            f"{bank_emoji} **بانک:** {bank_name}\n"
            f"👤 **صاحب حساب:** {owner_name}\n"
            f"🔢 **شماره:** `{masked_num}`\n\n"
            "این حساب به لیست شما اضافه شد و برای تسویه قابل استفاده است.",
            parse_mode="Markdown"
        )
        
        # اگر در حال برداشت بود، او را به منوی انتخاب کارت برگردان
        if amount:
            # مقدار مبلغ را دوباره در استیت جدید ست کن
            db.set_user_state(user_id, "WAITING_WITHDRAW_METHOD", {"amount": amount})
            bot.send_message(
                message.chat.id,
                f"💸 مبلغ برداشت: **{utils.format_currency(utils.safe_float(amount))}**\n\n"
                "اکنون می‌توانید حساب ثبت‌شده را از لیست زیر برای واریز انتخاب کنید:",
                parse_mode="Markdown",
                reply_markup=kb.get_withdraw_cards_inline(current_cards)
            )
        else:
            # بازگشت به مدیریت کارت‌ها
            handle_manage_cards(message)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("card_delete_"))
    def handle_card_delete(call: CallbackQuery):
        user_id = call.from_user.id
        try:
            idx = int(call.data.replace("card_delete_", "", 1))
        except ValueError:
            bot.answer_callback_query(call.id, "❌ خطا", show_alert=True)
            return
        cards = _get_user_cards(user_id)
        if idx < 0 or idx >= len(cards):
            bot.answer_callback_query(call.id, "❌ کارت یافت نشد.", show_alert=True)
            return
        removed = cards.pop(idx)
        # اگر کارت پیش‌فرض حذف شد، اولین کارت باقی‌مانده پیش‌فرض شود
        if removed.get("is_default") and cards:
            cards[0]["is_default"] = True
        _save_user_cards(user_id, cards)
        bot.answer_callback_query(call.id, "🗑 کارت حذف شد.")
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id,
            reply_markup=kb.get_cards_management_inline(cards)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("card_setdefault_"))
    def handle_card_set_default(call: CallbackQuery):
        user_id = call.from_user.id
        try:
            idx = int(call.data.replace("card_setdefault_", "", 1))
        except ValueError:
            bot.answer_callback_query(call.id, "❌ خطا", show_alert=True)
            return
        cards = _get_user_cards(user_id)
        if idx < 0 or idx >= len(cards):
            bot.answer_callback_query(call.id, "❌ کارت یافت نشد.", show_alert=True)
            return
        for i, c in enumerate(cards):
            c["is_default"] = (i == idx)
        _save_user_cards(user_id, cards)
        bot.answer_callback_query(call.id, "✅ کارت پیش‌فرض تنظیم شد.")
        bot.edit_message_reply_markup(
            call.message.chat.id, call.message.message_id,
            reply_markup=kb.get_cards_management_inline(cards)
        )

    # ====================================================
    # ۱۱. قوانین، امنیت و ضمانت (بخش ادغام‌شده - فاز ۱ ساده‌سازی منو)
    # طبق درخواست، «امنیت و ضمانت» به‌عنوان دکمه مستقل حذف و محتوای آن با
    # «قوانین و راهنمای حقوقی» یکی شده تا منوی اصلی خلوت‌تر شود. متن‌های
    # قدیمی دکمه‌ها هم در لیست func نگه داشته شده‌اند تا اگر جایی (مثلاً
    # پیام‌های قدیمی کاربر یا کیبورد کش‌شده) هنوز متن قبلی ارسال شود،
    # کاربر بدون پاسخ نماند.
    # ====================================================
    # ۱۱. مرکز خدمات، راهنما و قوانین (Consolidated)
    # ====================================================
    @bot.message_handler(func=lambda msg: msg.text in ["🎧 پشتیبانی و راهنمای استفاده 📖", "🛡️ قوانین و پشتیبانی", "🛡️ راهنما، قوانین و پشتیبانی"])
    def show_help_center(message: Message):
        db.clear_user_state(message.from_user.id)
        help_text = (
            "🎧 **مرکز پشتیبانی و راهنمای میانجی**\n"
            "──────────────────\n"
            "برای راهنمایی، قوانین یا گفتگو با کارشناسان از پنل زیر استفاده کنید.\n\n"
            "📍 **پاسخگویی سریع:** ۸ الی ۲۴"
        )
        bot.send_message(message.chat.id, help_text, parse_mode="Markdown", reply_markup=kb.get_help_center_inline())

    @bot.callback_query_handler(func=lambda call: call.data == "show_guide")
    def handle_show_guide(call: CallbackQuery):
        guide_text = (
            "📖 **راهنمای گام‌به‌گام میانجی**\n"
            "──────────────────\n"
            "۱. **توافق** 🤝: ثبت جزئیات و ارسال لینک برای طرف دوم\n"
            "۲. **واریز** 💳: پرداخت امن کارفرما و بلوکه شدن وجه\n"
            "۳. **اجرا** 🚀: انجام پروژه و ارسال فایل توسط مجری\n"
            "۴. **تسویه** ✅: تایید کارفرما و واریز آنی به کیف پول\n\n"
            "💡 _امنیت کامل فقط با پیگیری مراحل داخل ربات._"
        )
        bot.edit_message_text(guide_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb.get_help_center_inline())

    @bot.callback_query_handler(func=lambda call: call.data == "show_rules")
    def handle_show_rules(call: CallbackQuery):
        rules_text = (
            "⚖️ **خلاصه قوانین و مقررات رسمی میانجی**\n"
            "──────────────────\n"
            "🏛️ **۱. ماهیت حقوقی و مرجعیت داوری:**\n"
            "• فعالیت بر اساس ماده ۱۰ قانون مدنی و ماده ۳۷ قانون تجارت الکترونیکی.\n"
            "• میانجی داور مرضی‌الطرفین غیرقابل عزل است؛ رای داوری قطعی و لازم‌الاجراست.\n"
            "• معامله بر سر هک، دیتابیس، قمار و موارد غیرقانونی ممنوع است.\n\n"
            "💳 **۲. ضوابط مالی و کارمزد:**\n"
            "• کارمزد مجموعاً ۵٪ (۲.۵٪ خریدار / ۲.۵٪ فروشنده) | زیر ۵۰۰ هزار تومان: ثابت ۲۵ هزار تومان.\n"
            "• تسویه حساب ظرف کمتر از ۲ ساعت کاری پس از تایید نهایی خریدار.\n\n"
            "🛡️ **۳. امنیت و احراز هویت (KYC):**\n"
            "• واریز وجه فقط و فقط از کارت بانکی به نام خود خریدار مجاز است.\n"
            "• ارائه کارت ملی برای معاملات بالای ۵ میلیون و کلیه معاملات اکانت/کانال الزامی است.\n\n"
            "⚖️ **۴. نحوه داوری و حقوق مالکیت:**\n"
            "• تنها سند معتبر، «متن توافق اولیه در چت پشتیبانی» قبل از واریز است.\n"
            "• مهلت تست خریدار ۲۴ ساعت است. عدم پاسخ یعنی تایید ضمنی.\n"
            "• مالکیت فکری پروژه پس از تسویه کامل منتقل می‌شود.\n\n"
            "🔄 **۵. انصراف و فورس‌ماژور:**\n"
            "• انصراف قبل کار: کسر ۱٪ کارمزد اداری.\n"
            "• انصراف بعد کار: محاسبه درصد کار انجام‌شده توسط داور.\n"
            "• اکانت و کانال پس از تحویل، امکان انصراف بی‌دلیل ندارند.\n\n"
            "🔒 **۶. حریم خصوصی:**\n"
            "• اطلاعات کاربران محرمانه است و فقط با دستور رسمی قضایی ارائه می‌شود.\n\n"
            "──────────────────\n"
            "📢 **کانال قوانین کامل:** @mianji_rules\n"
            "🆔 **پشتیبانی:** @Mianji_Support"
        )
        bot.edit_message_text(rules_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb.get_help_center_inline())

    @bot.message_handler(func=lambda msg: msg.text in [
        "⚖️ قوانین، امنیت و ضمانت", "⚖️ قوانین و راهنمای حقوقی",
        "⚖️ قوانین و راهنما", "🛡 امنیت و ضمانت میانجی",
        "📖 راهنمای استفاده", "📞 پشتیبانی و ارتباط با ما"
    ])
    def handle_old_menu_buttons(message: Message):
        # هدایت کاربران به منوی جدید در صورت استفاده از دکمه‌های قدیمی (کش‌مانده در تلگرام)
        show_help_center(message)

    # ====================================================
    # ۱۳. اقدامات روی معامله پس از ثبت (این بخش کاملاً جدید و رفع‌شده است)
    #     امضا / پیشنهاد مبلغ / لغو / تحویل کار / تایید نهایی / دانلود PDF
    #     قبلاً این هندلرها فقط در handlers.py وجود داشتند که هرگز رجیستر
    #     نمی‌شد؛ به همین دلیل کلیک روی این دکمه‌ها هیچ اتفاقی نمی‌انداخت.
    # ====================================================

    @bot.callback_query_handler(func=lambda call: call.data.startswith("view_contract_terms:"))
    def handle_view_contract_terms(call: CallbackQuery):
        cid = call.data.split(":")[1]
        user_id = call.from_user.id
        contract = db.get_contract(cid)

        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        # بررسی دسترسی: پس از امضای طرفین، فقط ادمین و طرفین قرارداد اجازه دسترسی دارند
        is_both_signed = bool((contract.get("buyer_signed_at") or contract.get("employer_signed_at")) and 
                              (contract.get("seller_signed_at") or contract.get("freelancer_signed_at")))
        is_admin_check = (user_id == getattr(config, 'OWNER_ID', 0) or user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        is_party = (user_id in [contract.get("buyer_id"), contract.get("seller_id"), contract.get("creator_id")])
        
        if is_both_signed and not (is_admin_check or is_party):
            bot.answer_callback_query(call.id, "🚫 **محدودیت دسترسی**\n\nشما اجازه دسترسی به این قرارداد را ندارید.", show_alert=True)
            return

        # شناسایی نقش کاربر و لینک کردن کاربر به قرارداد در صورت خالی بودن یک سمت
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        creator_id = contract.get("creator_id")
        
        updates = {}
        if buyer_id == user_id:
            role = "employer"
        elif seller_id == user_id:
            role = "freelancer"
        elif not buyer_id and seller_id != user_id:
            # اگر خریدار خالی بود و کاربر جاری، فروشنده نبود
            role = "employer"
            updates["buyer_id"] = user_id
        elif not seller_id and buyer_id != user_id:
            # اگر فروشنده خالی بود و کاربر جاری، خریدار نبود
            role = "freelancer"
            updates["seller_id"] = user_id
        else:
            # حالت پیش‌فرض (ممکن است هر دو پر باشند و کاربر شخص ثالث باشد)
            role = "employer" if buyer_id == user_id else "freelancer"

        if updates:
            db.update_contract(cid, updates)
            contract.update(updates)

        text = utils.generate_contract_text(contract)
        
        # ویرایش پیام قبلی برای نمایش متن کامل و کیبورد اصلی ۴ دکمه‌ای
        try:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=kb.get_contract_action_keyboard(
                    cid,
                    role,
                    contract.get("status", "draft"),
                    bool(contract.get("milestones")),
                    bool(contract.get("staged_payment")),
                    show_terms_button=False
                )
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                text,
                parse_mode="Markdown",
                reply_markup=kb.get_contract_action_keyboard(
                    cid,
                    role,
                    contract.get("status", "draft"),
                    bool(contract.get("milestones")),
                    bool(contract.get("staged_payment")),
                    show_terms_button=False
                )
            )
        
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("open_negotiation_"))
    def handle_open_negotiation(call: CallbackQuery):
        cid = call.data.replace("open_negotiation_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        text = (
            f"🔄 **پنل پیشنهاد تغییر شرایط معامله `{cid}`**\n\n"
            "لطفاً بخشی که قصد تغییر آن را دارید انتخاب کنید.\n"
            "پس از انتخاب، مقدار جدید را ارسال کنید تا برای طرف مقابل فرستاده شود:"
        )
        try:
            bot.edit_message_text(
                text=text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown",
                reply_markup=kb.get_negotiation_edit_inline(cid)
            )
        except Exception:
            bot.send_message(
                call.message.chat.id,
                text,
                parse_mode="Markdown",
                reply_markup=kb.get_negotiation_edit_inline(cid)
            )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_NEGOTIATION_TEXT")
    def handle_negotiation_text(message: Message):
        user_id = message.from_user.id
        neg_text = message.text.strip()
        _, data = db.get_user_state(user_id)
        if not data: return
        cid = data.get("contract_id")
        orig_msg_id = data.get("original_message_id")
        
        db.clear_user_state(user_id)
        db.update_contract(cid, {"status": "bargaining", "negotiation_text": neg_text})
        db.append_contract_history(cid, f"🔄 پیشنهاد تغییر شرایط: {neg_text}", actor_id=user_id)
        
        contract = db.get_contract(cid)
        if orig_msg_id:
            try:
                render_contract_card(message.chat.id, user_id, contract, message_id=orig_msg_id)
            except Exception:
                pass

        other_id = contract.get("seller_id") if user_id == contract.get("buyer_id") else contract.get("buyer_id")
        
        bot.send_message(message.chat.id, "✅ **پیشنهاد شما ثبت و برای طرف مقابل ارسال شد.**")
        if other_id:
            bot.send_message(
                other_id,
                f"🔄 **پیشنهاد جدید برای معامله `{cid}`**\n\n"
                f"طرف مقابل شرایط زیر را پیشنهاد داده است:\n"
                f"📝 `{neg_text}`\n\n"
                "می‌توانید این شرایط را بپذیرید یا پیشنهاد متقابل بدهید.\n"
                f"📎 [مشاهده در ربات](https://t.me/{config.BOT_USERNAME})",
                parse_mode="Markdown",
                reply_markup=kb.get_contract_action_keyboard(cid, "freelancer" if other_id == contract.get("seller_id") else "employer", "bargaining")
            )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("neg_edit_"))
    def handle_neg_edit_field(call: CallbackQuery):
        # Format: neg_edit_{field}_{contract_id}
        parts = call.data.split("_")
        if len(parts) < 4: return
        field = parts[2]
        cid = "_".join(parts[3:])
        user_id = call.from_user.id
        
        field_names = {
            "title": "عنوان موضوع",
            "amount": "مبلغ معامله (تومان)",
            "comm_payer": "پرداخت‌کننده کارمزد",
            "deadline": "مهلت تحویل (روز)",
            "desc": "شرح تعهدات",
            "freeedits": "تعداد ویرایش رایگان"
        }
        
        field_name = field_names.get(field, "مقدار")
        
        if field == "comm_payer":
            db.set_user_state(user_id, "WAITING_NEGOTIATION_UPDATE", {
                "contract_id": cid,
                "field": field,
                "field_name": field_name,
                "original_message_id": call.message.message_id
            })
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                text="⚖️ **تغییر پرداخت‌کننده کارمزد**\n\nلطفاً گزینه جدید را انتخاب کنید:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown",
                reply_markup=kb.get_wizard_commission_payer_inline()
            )
            return

        db.set_user_state(user_id, "WAITING_NEGOTIATION_UPDATE", {
            "contract_id": cid,
            "field": field,
            "field_name": field_name,
            "original_message_id": call.message.message_id
        })
        
        bot.answer_callback_query(call.id)
        text = (
            f"🔄 **تغییر {field_name}**\n\n"
            f"مقدار جدید را ارسال کنید:"
        )
        bot.edit_message_text(
            text=text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_NEGOTIATION_UPDATE")
    def handle_negotiation_update(message: Message):
        user_id = message.from_user.id
        new_value = message.text.strip()
        _, data = db.get_user_state(user_id)
        if not data: return
        
        cid = data.get("contract_id")
        field = data.get("field")
        field_name = data.get("field_name")
        orig_msg_id = data.get("original_message_id")
        
        db.clear_user_state(user_id)
        
        # اعتبارسنجی و تبدیل داده‌ها
        contract = db.get_contract(cid)
        if not contract: return
        
        current_neg_count = int(contract.get("negotiation_count", 0) or 0)
        if current_neg_count >= 30:
            bot.send_message(message.chat.id, "⚠️ **خطا:** سقف تعداد تغییرات پیشنهادی (۳۰ بار) برای این معامله به پایان رسیده است.")
            return

        updates = {"status": "bargaining", "negotiation_count": current_neg_count + 1}
        log_val = new_value
        
        if field == "amount":
            try:
                val = float(utils.fa_to_en_digits(new_value.replace(",", "")))
                updates["amount"] = val
                log_val = f"{val:,.0f} تومان"
            except:
                bot.send_message(message.chat.id, "❌ مبلغ وارد شده نامعتبر است. لطفاً فقط عدد وارد کنید.")
                return
        elif field == "deadline":
            try:
                val = int(float(utils.fa_to_en_digits(new_value)))
                updates["deadline"] = val
                log_val = f"{val} روز"
            except:
                bot.send_message(message.chat.id, "❌ مهلت وارد شده نامعتبر است. لطفاً فقط عدد (تعداد روز) وارد کنید.")
                return
        elif field == "freeedits":
            try:
                val = int(float(utils.fa_to_en_digits(new_value)))
                updates["free_edits_total"] = val
                updates["free_edits_left"] = val
                log_val = f"{val} بار"
            except:
                bot.send_message(message.chat.id, "❌ تعداد وارد شده نامعتبر است.")
                return
        elif field == "title":
            updates["title"] = new_value
        elif field == "desc":
            updates["description"] = new_value
            
        # بروزرسانی در دیتابیس
        # با هر تغییر در شرایط معامله، امضاهای قبلی ابطال می‌شود
        updates.update({
            "buyer_signed_at": None,
            "seller_signed_at": None,
            "buyer_otp_verified": False,
            "seller_otp_verified": False,
            "employer_signed_at": None, # برای سازگاری با اسامی قدیمی
            "freelancer_signed_at": None
        })
        db.update_contract(cid, updates)
        neg_log = f"🔄 پیشنهاد تغییر {field_name} به: {log_val}"
        db.append_contract_history(cid, neg_log, actor_id=user_id)
        db.update_contract(cid, {"negotiation_text": neg_log}) # ذخیره آخرین تغییر برای نمایش
        
        contract = db.get_contract(cid)
        if orig_msg_id:
            try:
                render_contract_card(message.chat.id, user_id, contract, message_id=orig_msg_id)
            except Exception:
                pass

        other_id = contract.get("seller_id") if user_id == contract.get("buyer_id") else contract.get("buyer_id")
        
        bot.send_message(message.chat.id, "✅ **پیشنهاد شما ثبت و ارسال شد.**")
        
        if other_id:
            try:
                bot.send_message(
                    other_id,
                    f"🔄 **پیشنهاد تغییر در معامله `{cid}`**\n\n"
                    f"بخش **{field_name}** به `{log_val}` تغییر یافت.\n\n"
                    "لطفاً شرایط جدید را بررسی و تایید کنید.",
                    parse_mode="Markdown",
                    reply_markup=kb.get_contract_action_keyboard(
                        cid, 
                        "freelancer" if other_id == contract.get("seller_id") else "employer", 
                        "bargaining",
                        contract=contract
                    )
                )
            except Exception as e:
                logger.error(f"Error notifying other party in negotiation: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("neg_accept_"))
    def handle_neg_accept(call: CallbackQuery):
        cid = call.data.replace("neg_accept_", "", 1)
        user_id = call.from_user.id
        db.update_contract(cid, {"status": "pending_approval", "negotiation_text": None})
        db.append_contract_history(cid, "✅ شرایط جدید پذیرفته شد؛ در انتظار امضا.", actor_id=user_id)
        bot.answer_callback_query(call.id, "✅ شرایط پذیرفته شد. اکنون می‌توانید قرارداد را امضا کنید.")
        
        # ارسال مجدد کارت قرارداد با وضعیت جدید برای نفر قبول‌کننده
        contract = db.get_contract(cid)
        render_contract_card(call.message.chat.id, user_id, contract)
        
        # اطلاع‌رسانی به نفر دیگر
        other_id = contract.get("seller_id") if user_id == contract.get("buyer_id") else contract.get("buyer_id")
        if other_id:
            try:
                bot.send_message(
                    other_id,
                    f"✅ **شرایط معامله `{cid}` توسط طرف مقابل پذیرفته شد.**\n\n"
                    "اکنون طرفین می‌توانند نسبت به امضای نهایی قرارداد اقدام کنند.\n"
                    f"📎 [مشاهده معامله](https://t.me/{config.BOT_USERNAME})",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("show_invite_"))
    def handle_show_invite(call: CallbackQuery):
        cid = call.data.replace("show_invite_", "", 1)
        bot_username = getattr(config, 'BOT_USERNAME', 'MiyanjiBot')
        share_link = f"https://t.me/{bot_username}?start=c_{cid}"
        
        text = (
            f"🔗 **لینک دعوت طرف مقابل:**\n"
            f"`{share_link}`\n\n"
            f"💡 این لینک را برای شخص مقابل ارسال کنید. او با کلیک بر روی لینک و فشردن دکمه START، می‌تواند قرارداد را مشاهده و امضا کند.\n\n"
            f"⚠️ **توجه:** تا زمانی که طرف مقابل امضا نکند و مبلغ را واریز نکند، معامله «فعال» نخواهد شد."
        )
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("sign_contract_"))
    def handle_sign_contract_start(call: CallbackQuery):
        cid = call.data.replace("sign_contract_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)

        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return
        
        # ۱. مدیریت حالت‌ها و جلوگیری از امضای مجدد (Validation Check)
        is_buyer = (contract.get("buyer_id") == user_id)
        is_seller = (contract.get("seller_id") == user_id)
        
        # اگر هنوز نقش‌ها دقیق ثبت نشده (مثلاً نفر دوم که هنوز آیدی‌اش در قرارداد نیست)
        # در جریان امضا آیدی‌اش ثبت می‌شود.
        
        already_signed = False
        if is_buyer and (contract.get("buyer_signed_at") or contract.get("employer_signed_at")):
            already_signed = True
        elif is_seller and (contract.get("seller_signed_at") or contract.get("freelancer_signed_at")):
            already_signed = True
            
        if already_signed:
            status_text = utils.get_contract_status_report(contract)
            bot.answer_callback_query(call.id, f"⚠️ شما قبلاً این قرارداد را امضا کرده‌اید.\n\nوضعیت فعلی: {status_text}", show_alert=True)
            return

        # بررسی آستانه احراز هویت (۳ میلیون تومان)
        contract_amount = float(contract.get("amount", 0))
        threshold = float(getattr(config, "IDENTITY_VERIFICATION_THRESHOLD", 3000000))
        needs_national_id = (contract_amount >= threshold)
        
        user_info = db.get_user(user_id)
        has_fullname = bool(user_info and user_info.get("full_name"))
        # اگر کاربر احراز هویت شده باشد یا قبلاً کد ملی ثبت کرده باشد
        is_verified_user = bool(user_info and user_info.get("is_verified"))
        has_national_id = bool(user_info and (user_info.get("national_id") or is_verified_user))

        state_data = {
            "contract_id": cid,
            "needs_national_id": needs_national_id,
            "contract_snapshot": contract,
            "original_message_id": call.message.message_id
        }

        # اگر نام و فامیل ندارد -> اول نام و فامیل
        if not has_fullname:
            db.set_user_state(user_id, "WAITING_SIGN_FULLNAME", state_data)
            bot.send_message(
                call.message.chat.id,
                "✍️ **تکمیل اطلاعات برای امضای قرارداد**\n\n"
                "لطفاً نام و نام خانوادگی خود را کامل وارد کنید:\n"
                "_(مثال: علی محمدی)_",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        # اگر کد ملی ندارد و مبلغ بالای آستانه است -> دریافت کد ملی
        if needs_national_id and not has_national_id:
            db.set_user_state(user_id, "WAITING_SIGN_NATIONAL_ID", state_data)
            bot.send_message(
                call.message.chat.id,
                "🪪 **احراز هویت کد ملی**\n\n"
                "این معامله بالای ۳ میلیون تومان است و نیاز به ثبت کد ملی دارد.\n"
                "لطفاً کد ملی ۱۰ رقمی خود را وارد کنید:",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        # همه اطلاعات موجود است -> ارسال OTP
        _send_signing_otp(bot, call.message.chat.id, user_id, cid)

    def _send_signing_otp(bot, chat_id, user_id, cid):
        import random
        otp = str(random.randint(100000, 999999))
        # دریافت دیتای قبلی اگر وجود دارد
        _, data = db.get_user_state(user_id)
        if not data or not isinstance(data, dict): data = {}
        data.update({"contract_id": cid, "otp": otp})
        db.set_user_state(user_id, "WAITING_SIGNATURE_OTP", data)
        
        bot.send_message(
            chat_id,
            f"🔐 **تایید امضای قرارداد**\n\n"
            f"🔐 **تایید امضای قرارداد `{cid}`**\n\nکد تایید را وارد کنید:"
            f"`{otp}`\n\n"
            f"⚠️ با وارد کردن این کد، شما تمام شرایط قرارداد و قوانین پلتفرم را می‌پذیرید.",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_SIGN_FULLNAME")
    def handle_sign_fullname(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()
        parts = full_name.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "❌ لطفاً نام و فامیل خود را کامل وارد کنید (حداقل دو کلمه).")
            return
        
        _, data = db.get_user_state(user_id)
        if not data: data = {}
        db.update_user_identity(user_id, full_name, None) # بروزرسانی نام در دیتابیس
        
        # بررسی مرحله بعدی
        user_info = db.get_user(user_id)
        has_national_id = bool(user_info and user_info.get("national_id"))

        if data.get("needs_national_id") and not has_national_id:
            db.set_user_state(user_id, "WAITING_SIGN_NATIONAL_ID", data)
            bot.send_message(message.chat.id, "🪪 بسیار عالی. حالا کد ملی ۱۰ رقمی خود را وارد کنید:")
        else:
            if data.get("is_creator"):
                _go_to_creator_phone_capture(bot, message.chat.id, user_id, data)
            else:
                _send_signing_otp(bot, message.chat.id, user_id, data.get("contract_id"))

    def _go_to_creator_phone_capture(bot, chat_id, user_id, data):
        sign_text = (
            "✍️ **امضای قانونی و الکترونیک قرارداد**\n\n"
            "طبق مواد ۶، ۷ و ۱۲ قانون تجارت الکترونیک، جهت رسمیت یافتن سند و غیرقابل انکار بودن آن، "
            "ارسال شماره اکانت تلگرام الزامی است.\n\n"
            "لطفاً جهت ثبت امضا روی دکمه زیر کلیک کنید:"
        )
        db.set_user_state(user_id, "WAITING_SIGN_PHONE", data)
        bot.send_message(
            chat_id,
            sign_text,
            parse_mode="Markdown",
            reply_markup=kb.get_phone_sign_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_SIGN_NATIONAL_ID")
    def handle_sign_national_id(message: Message):
        user_id = message.from_user.id
        national_id = utils.fa_to_en_digits(message.text.strip())
        if not national_id.isdigit() or len(national_id) != 10:
            bot.send_message(message.chat.id, "❌ کد ملی باید ۱۰ رقم باشد. دوباره وارد کنید:")
            return
        
        _, data = db.get_user_state(user_id)
        if not data: data = {}
        
        # اگر کد ملی تست وارد شد، کاربر را به عنوان تایید شده علامت بزن
        is_verified = (national_id == "1234567890")
        db.update_user_identity(user_id, national_id=national_id, is_verified=is_verified) # بروزرسانی در دیتابیس
        if data.get("is_creator"):
            _go_to_creator_phone_capture(bot, message.chat.id, user_id, data)
        else:
            _send_signing_otp(bot, message.chat.id, user_id, data.get("contract_id"))

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_SIGNATURE_OTP")
    def process_signature_otp(message: Message):
        user_id = message.from_user.id
        text = utils.fa_to_en_digits(message.text.strip())
        _, data = db.get_user_state(user_id)
        
        if not data or text != data.get("otp"):
            bot.send_message(message.chat.id, "❌ کد وارد شده اشتباه است.")
            return
        
        cid = data.get("contract_id")
        contract = db.get_contract(cid)
        db.clear_user_state(user_id)
        _execute_contract_signature(bot, message, user_id, cid, contract, otp_code=text)

    def _execute_contract_signature(bot, message_or_call, user_id, cid, contract, otp_code):
        """اجرای نهایی امضا (بعد از تایید هویت و OTP)"""
        if not contract: return
        
        user_info = db.get_user(user_id) or {}
        full_name = user_info.get("full_name") or f"کاربر {user_id}"
        national_id = user_info.get("national_id", "")
        phone = user_info.get("phone_number", "")
        
        updates = {}
        now = datetime.now(timezone.utc).isoformat()
        is_buyer = contract.get("buyer_id") == user_id
        is_seller = contract.get("seller_id") == user_id
        
        if is_buyer or (not contract.get("buyer_id") and contract.get("seller_id") != user_id):
            updates["buyer_otp_verified"] = True
            updates["buyer_signed_at"] = now
            updates["buyer_otp_code"] = otp_code
            updates["buyer_fullname"] = full_name
            updates["buyer_national_id"] = national_id
            if not contract.get("buyer_id"): 
                updates["buyer_id"] = user_id
                is_buyer = True
        elif is_seller or (not contract.get("seller_id") and contract.get("buyer_id") != user_id):
            updates["seller_otp_verified"] = True
            updates["seller_signed_at"] = now
            updates["seller_otp_code"] = otp_code
            updates["seller_fullname"] = full_name
            updates["seller_national_id"] = national_id
            if not contract.get("seller_id"): 
                updates["seller_id"] = user_id
                is_seller = True

        contract.update(updates)
        both_signed = bool(contract.get("buyer_id") and contract.get("seller_id"))

        updates["status"] = "awaiting_payment" if both_signed else contract.get("status", "pending_approval")
        db.update_contract(cid, updates)
        contract.update(updates)

        # بجای نهایی‌سازی مستقیم، مرحله دریافت شماره تماس را اضافه می‌کنیم
        data = {
            "contract_id": cid,
            "both_signed": both_signed,
            "contract_snapshot": contract,
            "is_creator": False
        }
        db.set_user_state(user_id, "WAITING_SIGN_PHONE", data)

        chat_id = message_or_call.chat.id if hasattr(message_or_call, 'chat') else message_or_call.message.chat.id
        bot.send_message(
            chat_id,
            "✅ امضای شما با تایید OTP ثبت شد.\n\n"
            "📞 جهت درج در قرارداد، لطفاً شماره تماس خود را **فقط** از طریق دکمه زیر ارسال کنید:",
            reply_markup=kb.get_phone_sign_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_SIGN_PHONE", content_types=['text', 'contact'])
    def handle_sign_phone(message: Message):
        user_id = message.from_user.id
        phone = ""
        if message.contact:
            if message.contact.user_id != user_id:
                bot.send_message(message.chat.id, "❌ لطفاً فقط شماره مربوط به **اکانت خودتان** را ارسال کنید.")
                return
            phone = message.contact.phone_number
        else:
            # کاربر بصورت دستی شماره را تایپ کرده است - طبق درخواست جدید نباید پذیرفته شود
            bot.send_message(
                message.chat.id, 
                "⚠️ **خطای امنیتی:**\n\n"
                "جهت ثبت امضای الکترونیک معتبر، شما **فقط** باید از دکمه «📱 ارسال شماره جهت ثبت امضا» استفاده کنید.\n"
                "نوشتن دستی شماره تماس برای امضای اصلی مورد قبول نیست.",
                reply_markup=kb.get_phone_sign_keyboard()
            )
            return
        
        _, data = db.get_user_state(user_id)
        if not data: 
            db.clear_user_state(user_id)
            return
            
        cid = data.get("contract_id")
        both_signed = data.get("both_signed")
        
        # پاکسازی و استانداردسازی شماره تماس (Sanitize)
        def sanitize_phone(p):
            if not p: return p
            p = utils.fa_to_en_digits(str(p))
            p = "".join(re.findall(r'\d+', p))
            if p.startswith("0098"): p = "0" + p[4:]
            elif p.startswith("98") and len(p) > 10: p = "0" + p[2:]
            return p
        
        phone = sanitize_phone(phone)
        db.update_user_phone(user_id, phone)
        # ذخیره موقت شماره اصلی در استیت برای استفاده در مرحله بعد
        data["signer_phone"] = phone
        
        if data.get("is_creator"):
            # جریان ایجاد قرارداد
            draft = data.get("contract_draft", {})
            role = draft.get("role", "employer")
            if role == "employer":
                draft["buyer_phone"] = phone
            else:
                draft["seller_phone"] = phone
            
            db.set_user_state(user_id, "WAITING_WORK_PHONE", {"contract_draft": draft})
            
            bot.send_message(
                message.chat.id, 
                "✅ شماره اصلی شما ثبت شد.\n\n"
                "📱 **شماره تماس ثانویه و پاسخگو**\n"
                "جهت هماهنگی‌های بهتر و درج در قرارداد، لطفاً یک شماره تماس دیگر وارد کنید:",
                reply_markup=kb.get_skip_work_phone_keyboard()
            )
        else:
            # جریان امضای طرف دوم
            db.set_user_state(user_id, "WAITING_SIGN_ALT_PHONE", data)
            bot.send_message(
                message.chat.id, 
                "✅ شماره اصلی شما ثبت شد.\n\n"
                "📱 **شماره تماس ثانویه و پاسخگو**\n"
                "جهت هماهنگی‌های بهتر، لطفاً یک شماره تماس دیگر (غیر از تلگرام) وارد کنید:",
                reply_markup=kb.get_skip_work_phone_keyboard()
            )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_SIGN_ALT_PHONE")
    def handle_sign_alt_phone(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        if not data: 
            db.clear_user_state(user_id)
            return
            
        cid = data.get("contract_id")
        signer_phone = data.get("signer_phone")
        
        # ۱. دریافت و پاکسازی شماره‌های تماس (Sanitize)
        def sanitize_phone(p):
            if not p: return ""
            p = utils.fa_to_en_digits(str(p))
            p = "".join(re.findall(r'\d+', p))
            if p.startswith("0098"): p = "0" + p[4:]
            elif p.startswith("98") and len(p) > 10: p = "0" + p[2:]
            return p

        # تعیین شماره ثانویه بر اساس ورودی یا دکمه رد کردن
        alt_phone_raw = message.text.strip() if message.text else ""
        if alt_phone_raw == "⏩ رد کردن و ثبت نهایی" or alt_phone_raw == "⏭ رد کردن و استفاده از شماره تلگرام":
            alt_phone = signer_phone # استفاده از شماره اصلی در صورت رد کردن
        else:
            alt_phone = sanitize_phone(alt_phone_raw)
            if len(alt_phone) < 10:
                # اگر شماره وارد شده نامعتبر بود، از شماره اصلی استفاده می‌کنیم یا خطا می‌دهیم
                # طبق درخواست: "در صورت خالی بودن، همان شماره اصلی جایگزین شود"
                alt_phone = signer_phone

        signer_phone = sanitize_phone(signer_phone)
        alt_phone = sanitize_phone(alt_phone)

        db.clear_user_state(user_id)
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        
        # بازخورد بصری موفقیت
        utils.send_celebration(
            bot, 
            message.chat.id, 
            "✨ **امضای شما با موفقیت ثبت شد** ✨\n\n✅ اطلاعات تماس تکمیل و فرآیند امضا به پایان رسید.",
            reply_markup=kb.get_main_menu(is_admin)
        )
        
        contract = db.get_contract(cid)
        if not contract:
            logger.error(f"❌ [SIGN_ALT_ERR] Contract {cid} not found for user {user_id}")
            return

        # تشخیص نقش امضاکننده دوم
        is_buyer = contract.get("buyer_id") == user_id
        
        updates = {}
        if is_buyer:
            updates["buyer_phone"] = signer_phone
            updates["buyer_alt_phone"] = alt_phone
        else:
            updates["seller_phone"] = signer_phone
            updates["seller_alt_phone"] = alt_phone
            
        # ۲. ذخیره‌سازی در دیتابیس با مدیریت استثنا و لاگ دقیق
        db_success = False
        try:
            logger.info(f"💾 [DB_SIGN_UPDATE] User {user_id} finalizing signature for contract {cid}")
            res = db.update_contract(cid, updates)
            if res:
                db_success = True
                contract.update(updates)
            else:
                logger.error(f"❌ [DB_SIGN_FAIL] db.update_contract returned False for {cid}")
        except Exception as db_err:
            logger.error(f"🚨 [SUPABASE_SIGN_ERR] Contract {cid} update failed: {db_err}", exc_info=True)

        if not db_success:
            logger.error(f"🚨 [SUPABASE_SIGN_ERR] Contract {cid} update failed")
            # طبق درخواست کاربر، خطای دیتابیس نمایش داده نمی‌شود.
            return

        # ۳. بخش اطلاع‌رسانی و عملیات جانبی (جدا شده از بلوک دیتابیس)
        try:
            # بروزرسانی کارت معامله در صورت وجود مسیج آیدی قبلی
            original_msg_id = data.get("original_message_id")
            if original_msg_id:
                try:
                    render_contract_card(message.chat.id, user_id, contract, message_id=original_msg_id)
                except Exception:
                    pass

            both_signed = data.get("both_signed")
            if both_signed:
                _finalize_both_signed(bot, cid, contract)
            else:
                notify_other_party(
                    bot, contract, user_id,
                    f"✍️ طرف مقابل معامله شماره `{cid}` را امضا کرد.\n"
                    "برای مشاهده و تایید نهایی، از «📜 معاملات من» وارد شوید."
                )
        except Exception as post_err:
            logger.error(f"⚠️ [POST_SIGN_ERR] Sign recorded for {cid} but notification/PDF failed: {post_err}")

    def _finalize_both_signed(bot: TeleBot, cid: str, contract: dict):
        """نهایی‌سازی مشترک پس از امضای هر دو طرف (ارسال PDF + اطلاع‌رسانی)"""
        # واکشی مجدد برای اطمینان از کامل بودن اطلاعات (از جمله زمان امضای طرف اول)
        contract = db.get_contract(cid) or contract
        
        has_ms = bool(contract.get("milestones"))
        is_staged = bool(contract.get("staged_payment")) and has_ms
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")

        logger.info(f"🏁 [POST_SIGN] Finalizing deal {cid}. Staged: {is_staged}")

        # ۱. ارسال سند رسمی به هر دو طرف و MJNOTE
        send_contract_pdf_to_parties(bot, contract)

        # ۲. ارسال پیام تبریک و اطلاع‌رسانی نهایی‌شدن امضاها
        celebration_text = (
            f"🎊 **تبریک! قرارداد شماره `{cid}` با موفقیت توسط هر دو طرف امضا شد.**\n\n"
            "📄 فایل PDF قرارداد امضا شده در پیام بعدی برای شما ارسال می‌گردد.\n"
            "──────────────────\n"
        )
        
        for rid in {r for r in (buyer_id, seller_id) if r}:
            try:
                utils.send_celebration(bot, rid, text=celebration_text)
            except Exception as e:
                logger.error(f"Error sending celebration to {rid}: {e}")

        # ۳. راهنمای گام‌به‌گام بر اساس نوع پرداخت
        if is_staged:
            # اطلاع‌رسانی به مجری
            if seller_id:
                try:
                    bot.send_message(
                        seller_id,
                        f"🚀 **معامله `{cid}` آماده شروع است (پرداخت مرحله‌ای)**\n\n"
                        f"📝 **موضوع:** {contract.get('title')}\n"
                        f"💰 **مبلغ کل:** {utils.format_currency(contract.get('amount', 0))} تومان\n"
                        "📅 **گام فعلی:** در انتظار واریز فیش مرحله اول توسط کارفرما.\n\n"
                        "💡 به محض تایید واریزی توسط مدیریت، پیام «شروع کار» برای شما ارسال خواهد شد.",
                        parse_mode="Markdown"
                    )
                except Exception: pass
            
            # اطلاع‌رسانی به کارفرما
            if buyer_id:
                try:
                    bot.send_message(
                        buyer_id,
                        f"💳 **معامله `{cid}` نهایی شد — نوبت پرداخت مرحله اول**\n\n"
                        "شما این معامله را به صورت **مرحله‌ای** تنظیم کرده‌اید.\n"
                        "لطفاً جهت فعالسازی گام اول، مبلغ مربوطه را واریز و فیش را از دکمه زیر ارسال کنید:",
                        parse_mode="Markdown",
                        reply_markup=kb.get_ms_pay_keyboard(cid, 0)
                    )
                except Exception: pass
            
            _start_milestone_payment(bot, cid, contract, 0)
            return

        # معاملات عادی (یکجا)
        if buyer_id:
            try:
                bot.send_message(
                    buyer_id,
                    f"💳 **معامله `{cid}` — نوبت واریز وجه**\n\n"
                    "جهت شروع امن معامله و بلوکه‌شدن وجه در صندوق امانت، لطفاً مبلغ قرارداد را واریز و فیش را ارسال کنید.\n\n"
                    "⚠️ معامله پس از تایید فیش توسط مدیریت «فعال» خواهد شد.",
                    parse_mode="Markdown",
                    reply_markup=kb.get_contract_action_keyboard(cid, "employer", "awaiting_payment", has_ms)
                )
            except Exception: pass

        if seller_id:
            try:
                bot.send_message(
                    seller_id,
                    f"⌛ **معامله `{cid}` در انتظار واریز وجه**\n\n"
                    "طرف مقابل (کارفرما) باید وجه قرارداد را واریز کند.\n"
                    "🔔 به محض تایید واریزی، پیام «شروع کار» و اجازه تحویل پروژه برای شما ارسال می‌شود.",
                    parse_mode="Markdown"
                )
            except Exception: pass

    def _start_milestone_payment(bot: TeleBot, cid: str, contract: dict, idx: int):
        """
        فعال‌سازی درخواست پرداخت یک مرحله مشخص از معامله (پرداخت مرحله‌ای).
        این تابع در سه نقطه صدا زده می‌شود: شروع کار (بعد از امضای هر دو طرف)،
        بعد از تایید تحویل هر مرحله توسط کارفرما (برای شروع مرحله بعدی) و
        بعد از افزودن دوره جدید در قراردادهای مستمر/اشتراکی.
        """
        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones):
            return

        milestones[idx]["status"] = "awaiting_payment"
        db.update_contract(cid, {"milestones": milestones, "status": "awaiting_payment"})
        contract["milestones"] = milestones

        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        ms = milestones[idx]

        db.append_contract_history(
            cid,
            f"💳 درخواست پرداخت مرحله «{ms.get('title')}» (مرحله {idx + 1} از {len(milestones)}) فعال شد.",
            actor_id=None
        )

        if buyer_id:
            _, _, emp_pays = utils.calculate_commission(utils.safe_float(ms.get('amount', 0)), payer=contract.get('commission_payer', 'freelancer'))
            try:
                bot.send_message(
                    buyer_id,
                    f"💳 **پرداخت مرحله {idx + 1} از {len(milestones)} — «{ms.get('title')}»**\n\n"
                    f"📌 کد معامله: `{cid}`\n"
                    f"💵 مبلغ قابل واریز این مرحله: **{utils.format_currency(emp_pays)}**\n\n"
                    "لطفاً پس از واریز، با دکمه زیر فیش واریزی همین مرحله را ارسال کنید:",
                    parse_mode="Markdown",
                    reply_markup=kb.get_ms_pay_keyboard(cid, idx)
                )
            except Exception as e:
                logger.error(f"خطا در ارسال درخواست پرداخت مرحله {idx} معامله {cid}: {e}")

        if seller_id:
            try:
                bot.send_message(
                    seller_id,
                    f"⏳ در انتظار واریز و تایید مرحله {idx + 1} («{ms.get('title')}») معامله `{cid}` توسط کارفرما هستید.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    # ====================================================
    # نهایی‌سازی در دیتابیس (فراخوانی شده از هندلرهای بالا)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("upload_ee_receipt_"))
    def handle_upload_ee_receipt_start(call: CallbackQuery):
        """شروع فرآیند ارسال فیش اختصاصی برای هزینه ویرایش اضافه"""
        cid = call.data.replace("upload_ee_receipt_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return
        
        price = float(contract.get("extra_edit_price", 0) or 0)
        db.set_user_state(call.from_user.id, "WAITING_RECEIPT_PHOTO", {"receipt_cid": cid, "is_extra_edit": True})
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"📸 **ارسال فیش هزینه ویرایش**\n\n"
            f"📌 معامله: `{cid}`\n"
            f"💰 مبلغ ویرایش: **{utils.format_currency(price)}**\n\n"
            f"💳 **اطلاعات حساب:**\n`{config.INTERMEDIARY_CARD}`\n\n"
            "لطفاً تصویر فیش واریزی را ارسال کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("upload_receipt_"))
    def handle_upload_receipt_start(call: CallbackQuery):
        """شروع مرحلهٔ ارسال فیش واریزی توسط کارفرما (بخشی که قبلاً هیچ هندلری نداشت)"""
        user_id = call.from_user.id
        user_info = db.get_user(user_id)
        
        if not user_info.get("is_verified"):
            bot.answer_callback_query(call.id, "⚠️ برای امنیت تراکنش‌ها، ابتدا باید احراز هویت خود را تکمیل کنید.", show_alert=True)
            start_kyc_process(call.message.chat.id, user_id, resume_data=call.data)
            return

        cid = call.data.replace("upload_receipt_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند فیش واریزی ارسال کند.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_RECEIPT_PHOTO", {"receipt_cid": cid})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"📸 **ارسال فیش واریزی**\n\n"
            f"📌 شناسه: `{cid}`\n"
            f"💵 مبلغ: **{utils.format_currency(utils.safe_float(contract.get('amount', 0)))}**\n"
            "──────────────────\n"
            "💳 **اطلاعات واریز:**\n"
            f"├ شماره کارت: `{config.INTERMEDIARY_CARD}`\n"
            "└ بنام: **امیررضا مجتبایی**\n\n"
            "💡 تصویر فیش را ارسال کنید. تایید فیش حداکثر ۲ ساعت زمان می‌برد.",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(
        content_types=['photo', 'document'],
        func=lambda msg: getattr(msg, "user_state", None) == "WAITING_RECEIPT_PHOTO"
    )
    def handle_receipt_photo(message: Message):
        """دریافت تصویر فیش واریزی و ارسال آن برای تایید ادمین"""
        user_id = message.from_user.id
        proc_msg = bot.send_message(message.chat.id, "⏳ **در حال پردازش و ثبت فیش...**\nلطفاً چند لحظه معطل بمانید.")
        try:
            _, data = db.get_user_state(user_id)
            cid = data.get("receipt_cid") if isinstance(data, dict) else None
            is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

            if not cid:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin))
                return

            contract = db.get_contract(cid)
            if not contract:
                db.clear_user_state(user_id)
                bot.send_message(
                    message.chat.id,
                    "⚠️ معامله مرتبط با این فیش دیگر یافت نشد. لطفاً دوباره از «📜 معاملات من» اقدام کنید.",
                    reply_markup=kb.get_main_menu(is_admin)
                )
                return

            file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
            if not file_id:
                bot.send_message(message.chat.id, "⚠️ لطفاً فقط تصویر یا فایل فیش واریزی را ارسال کنید.")
                return

            db.clear_user_state(user_id)
            
            is_extra_edit = False
            if isinstance(data, dict) and data.get("is_extra_edit"):
                is_extra_edit = True

            if is_extra_edit:
                price = utils.safe_float(contract.get('extra_edit_price', 0))
                db.update_contract(cid, {"receipt_file_id": file_id, "status": "awaiting_extra_edit_receipt"})
                db.append_contract_history(cid, f"💳 کارفرما فیش هزینه ویرایش اضافه ({utils.format_currency(price)}) را ارسال کرد.", actor_id=user_id, file_id=file_id)
                # ثبت در جدول متمرکز
                db.create_pending_receipt(
                    receipt_type="milestone",
                    related_id=cid,
                    user_id=user_id,
                    file_id=file_id,
                    amount=price,
                    description=f"هزینه ویرایش اضافه معامله {cid}"
                )
            else:
                amount = utils.safe_float(contract.get("amount", 0))
                db.update_contract(cid, {"receipt_file_id": file_id, "status": "awaiting_receipt_approval"})
                db.append_contract_history(cid, "💳 کارفرما فیش واریزی جدید ارسال کرد.", actor_id=user_id, file_id=file_id, file_type="photo")
                # ثبت در جدول متمرکز
                db.create_pending_receipt(
                    receipt_type="contract",
                    related_id=cid,
                    user_id=user_id,
                    file_id=file_id,
                    amount=amount,
                    description=f"فعالسازی معامله {cid}: {contract.get('title')}"
                )

            # ارسال به ادمین
            price_to_show = utils.safe_float(contract.get("extra_edit_price", 0)) if is_extra_edit else utils.safe_float(contract.get("amount", 0))
            safe_title = utils.escape_html(contract.get('title', 'بدون عنوان'))
            admin_caption = (
                f"💳 <b>فیش واریزی جدید ({'هزینه ویرایش' if is_extra_edit else 'فعالسازی'})</b>\n\n"
                f"📌 <b>کد معامله:</b> <code>{cid}</code>\n"
                f"📝 <b>عنوان:</b> {safe_title}\n"
                f"💵 <b>مبلغ مورد انتظار:</b> {utils.format_currency(price_to_show)}\n"
                f"👤 <b>کارفرما:</b> <code>{user_id}</code>\n\n"
                "لطفاً مبلغ واریزی را با مبلغ بالا مطابقت دهید."
            )
            
            callback_id = f"ee:{cid}" if is_extra_edit else cid
            utils.send_admin_alert(
                bot,
                content_type="photo",
                file_id=file_id,
                text=admin_caption + f"\n\n📢 #فیش_{'ویرایش' if is_extra_edit else 'جدید'}",
                reply_markup=kb.get_receipt_admin_approval_inline(callback_id, user_id)
            )

            try:
                bot.delete_message(message.chat.id, proc_msg.message_id)
            except:
                pass

            bot.send_message(
                message.chat.id,
                "✅ **فیش واریزی شما دریافت شد و جهت تایید برای مدیریت ارسال گردید.**",
                reply_markup=kb.get_main_menu(is_admin)
            )
        except Exception as e:
            logger.error(f"Critical error in handle_receipt_photo: {e}")
            bot.send_message(message.chat.id, "⚠️ متأسفانه خطایی در فرآیند ثبت فیش رخ داد. لطفاً با پشتیبانی تماس بگیرید.")

    # ====================================================
    # ۱۳.۸ مدیریت پرداخت (کیف پول / فیش)
    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_options_"))
    def handle_payment_options(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        try:
            cid = call.data.replace("pay_options_", "", 1)
            user_id = call.from_user.id
            contract = db.get_contract(cid)
            if not contract:
                bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
                return
            
            try:
                amount = utils.safe_float(contract.get("amount", 0))
            except (ValueError, TypeError):
                amount = 0.0

            user_info = db.get_user(user_id)
            if not user_info:
                bot.answer_callback_query(call.id, "❌ خطا در بازیابی اطلاعات کاربر.", show_alert=True)
                return

            try:
                wallet_balance = utils.safe_float(user_info.get("wallet_balance", 0))
            except (ValueError, TypeError):
                wallet_balance = 0.0
            
            bot.answer_callback_query(call.id)
            text = (
                f"💳 **پرداخت معامله شماره `{cid}`**\n\n"
                f"مبلغ قابل پرداخت: **{amount:,.0f}** تومان\n\n"
                "لطفاً روش پرداخت را انتخاب کنید:"
            )
            try:
                bot.edit_message_text(
                    text=text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode="Markdown",
                    reply_markup=kb.get_payment_options_inline(cid, amount, wallet_balance)
                )
            except Exception:
                bot.send_message(
                    call.message.chat.id,
                    text,
                    parse_mode="Markdown",
                    reply_markup=kb.get_payment_options_inline(cid, amount, wallet_balance)
                )
        except Exception as e:
            logger.error(f"Error in handle_payment_options: {e}")
            try: bot.answer_callback_query(call.id, "❌ خطای سیستمی در نمایش روش‌های پرداخت.", show_alert=True)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_with_wallet_") and "_ms_" not in call.data)
    def handle_pay_with_wallet(call: CallbackQuery):
        """هندلر پرداخت از طریق کیف پول داخلی (نسخه بازنگری شده و اتمیک)"""
        # ۱. پاسخ سریع به تلگرام برای توقف حالت لودینگ
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        if not maintenance_check(call):
            return

        user_id = call.from_user.id
        contract_id = call.data.replace("pay_with_wallet_", "", 1)

        try:
            # ۲. اجرای فرآیند پرداخت در دیتابیس (شامل کسر موجودی و تغییر وضعیت)
            success, message = db.process_wallet_payment(user_id, contract_id)

            if not success:
                bot.send_message(call.message.chat.id, message)
                return

            # ۳. دریافت اطلاعات کامل قرارداد برای نوتیفیکیشن
            contract = db.get_contract(contract_id)
            if not contract:
                bot.send_message(call.message.chat.id, "✅ پرداخت انجام شد، اما در دریافت اطلاعات نهایی قرارداد خطایی رخ داد.")
                return

            buyer_id = contract.get("buyer_id")
            seller_id = contract.get("seller_id")
            amount = utils.safe_float(contract.get("amount", 0))

            # ۴. بروزرسانی کارت معامله در چت فعلی
            try:
                render_contract_card(call.message.chat.id, user_id, contract, message_id=call.message.message_id)
            except Exception as e:
                logger.error(f"Error rendering card after wallet payment: {e}")
                bot.send_message(call.message.chat.id, f"✅ **معامله `{contract_id}` فعال شد.**")

            # ۵. اطلاع‌رسانی به طرفین
            # پیام به کارفرما
            try:
                bot.send_message(
                    buyer_id,
                    f"✅ **پرداخت با موفقیت انجام شد**\n\n"
                    f"مبلغ {utils.format_currency(amount)} از کیف پول شما کسر و در حساب امانت میانجی بلوکه شد.\n"
                    f"معامله `{contract_id}` هم‌اکنون فعال است.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            # پیام به مجری
            try:
                seller_msg = (
                    f"🚀 **معامله فعال شد!**\n"
                    f"──────────────────\n"
                    f"کارفرما مبلغ معامله را از طریق کیف پول پرداخت کرد.\n\n"
                    f"📌 **اطلاعات معامله**\n"
                    f"├ شناسه: `{contract_id}`\n"
                    f"├ عنوان: {contract.get('title', '---')}\n"
                    f"├ مبلغ: {utils.format_currency(amount)}\n"
                    f"└ مهلت: {contract.get('deadline', '---')} روز\n"
                    f"──────────────────\n"
                    "اکنون می‌توانید کار را شروع کنید. موفق باشید! 🚀"
                )
                bot.send_message(
                    seller_id,
                    seller_msg,
                    reply_markup=kb.get_deliver_project_keyboard(contract_id),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            # ۶. اطلاع‌رسانی به ادمین (طبق درخواست: ارسال به mj_admin)
            admin_msg = (
                f"💰 **تاییدیه پرداخت از کیف پول**\n"
                f"──────────────────\n"
                f"📦 معامله: `{contract_id}`\n"
                f"👤 کارفرما: `{buyer_id}`\n"
                f"🛠 مجری: `{seller_id}`\n"
                f"💵 مبلغ: **{utils.format_currency(amount)}**\n"
                f"📅 تاریخ: `{datetime.now().strftime('%Y-%m-%d %H:%M')}`\n"
                f"──────────────────\n"
                f"✅ وضعیت معامله به `paid` تغییر یافت."
            )
            
            # ارسال به MJ_ADMIN
            if hasattr(config, 'MJ_ADMIN') and config.MJ_ADMIN:
                try:
                    bot.send_message(config.MJ_ADMIN, admin_msg, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Failed to send payment notification to MJ_ADMIN: {e}")
            
            # ارسال به MJNOTE (آرشیو)
            if hasattr(config, 'MJNOTE') and config.MJNOTE:
                try:
                    bot.send_message(config.MJNOTE, admin_msg, parse_mode="Markdown")
                except Exception:
                    pass

        except Exception as e:
            logger.exception(f"Critical error in wallet payment handler: {e}")
            bot.send_message(call.message.chat.id, "❌ خطای غیرمنتظره در فرآیند پرداخت. لطفاً با پشتیبانی تماس بگیرید.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_low_balance_"))
    def handle_wallet_low_balance(call: CallbackQuery):
        """نمایش راهنمای شارژ حساب زمانی که موجودی کافی نیست و تنظیم وضعیت برای دریافت فیش"""
        cid = call.data.replace("wallet_low_balance_", "", 1)
        user_id = call.from_user.id
        
        bot.answer_callback_query(call.id, "⚠️ موجودی کیف پول شما کافی نیست.", show_alert=True)
        
        # تنظیم وضعیت برای دریافت فیش معامله
        db.set_user_state(user_id, "WAITING_RECEIPT_PHOTO", {"receipt_cid": cid})
        
        bot.send_message(
            call.message.chat.id,
            "⚠️ **موجودی کیف پول شما کافی نیست**\n\n"
            "برای فعال‌سازی سریع این معامله، می‌توانید مبلغ را مستقیماً به حساب مدیریت واریز کنید.\n\n"
            "💡 **مراحل پرداخت با فیش:**\n"
            "۱. مبلغ معامله را به شماره کارت زیر واریز کنید.\n"
            "۲. **تصویر فیش واریزی را همین‌جا ارسال کنید.**\n"
            "۳. پس از تایید مدیریت، معامله بلافاصله فعال می‌شود.\n\n"
            "💳 **اطلاعات حساب:**\n"
            f"📌 شماره کارت: `{config.INTERMEDIARY_CARD}`\n"
            "👤 به نام: **امیررضا مجتبایی** (مدیریت میانجی)\n"
            "🏦 بانک: ملی ایران",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_with_receipt_"))
    def handle_pay_with_receipt_redirect(call: CallbackQuery):
        cid_raw = call.data.replace("pay_with_receipt_", "", 1)
        
        # اگر مربوط به مرحله (Milestone) بود
        if "_ms_" in cid_raw:
            cid, _, idx_str = cid_raw.rpartition("_ms_")
            call.data = f"msp_pay_actual_{cid}_{idx_str}"
            handle_ms_pay_start(call)
        else:
            call.data = f"upload_receipt_{cid_raw}"
            handle_upload_receipt_start(call)

    # ۱۳.۷ پرداخت مرحله‌ای واقعی (Staged Payment) — هر مرحله لینک پرداخت و
    # تحویل مستقل خودش را دارد:
    # تایید می‌کند → مجری همان مرحله را تحویل می‌دهد → کارفرما تایید و
    # آزادسازی می‌کند → مرحله بعدی خودکار شروع می‌شود (یا در قراردادهای
    # مستمر/اشتراکی، دکمه افزودن دوره جدید نمایش داده می‌شود).
    # ====================================================

    @bot.callback_query_handler(func=lambda call: call.data.startswith("msp_pay_"))
    def handle_ms_pay_start(call: CallbackQuery):
        """شروع فرآیند پرداخت یک مرحله معامله (کیف پول یا فیش)"""
        payload = call.data.replace("msp_pay_", "", 1)
        # اگر خودمان ریدایرکت کردیم (msp_pay_actual_)
        if payload.startswith("actual_"):
            handle_ms_pay_actual_start(call)
            return

        cid, _, idx_str = payload.rpartition("_")
        try:
            idx = int(idx_str)
        except ValueError:
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones):
            bot.answer_callback_query(call.id, "❌ مرحله یافت نشد.", show_alert=True)
            return
        
        ms = milestones[idx]
        ms_amount = utils.safe_float(ms.get("amount", 0))
        user_id = call.from_user.id
        user_info = db.get_user(user_id)
        wallet_balance = utils.safe_float(user_info.get("wallet_balance", 0.0))

        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"💳 **پرداخت مرحله «{ms.get('title')}»**\n\n"
            f"مبلغ این مرحله: `{ms_amount:,.0f}` تومان\n\n"
            "روش پرداخت را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_payment_options_inline(f"{cid}_ms_{idx}", ms_amount, wallet_balance)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_with_wallet_") and "_ms_" in call.data)
    def handle_ms_pay_with_wallet(call: CallbackQuery):
        try:
            payload = call.data.replace("pay_with_wallet_", "", 1)
            cid, _, idx_str = payload.rpartition("_ms_")
            idx = int(idx_str)
            
            user_id = call.from_user.id
            contract = db.get_contract(cid)
            if not contract:
                bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
                return

            milestones = contract.get("milestones") or []
            if idx >= len(milestones):
                bot.answer_callback_query(call.id, "❌ مرحله یافت نشد.", show_alert=True)
                return

            ms = milestones[idx]
            try:
                ms_amount = float(str(ms.get("amount", 0)).replace(",", ""))
            except (ValueError, TypeError):
                ms_amount = 0.0
            
            # محاسبه مبلغ نهایی شامل کارمزد (اگر بر عهده کارفرما باشد)
            comm_payer = contract.get("commission_payer", "freelancer")
            _, _, total_to_pay = utils.calculate_commission(ms_amount, payer=comm_payer)
            
            user_info = db.get_user(user_id)
            if not user_info:
                bot.answer_callback_query(call.id, "❌ خطا در بازیابی اطلاعات کاربر.", show_alert=True)
                return

            try:
                wallet_balance = float(str(user_info.get("wallet_balance", 0)).replace(",", ""))
            except (ValueError, TypeError):
                wallet_balance = 0.0

            if wallet_balance < total_to_pay:
                bot.answer_callback_query(call.id, f"⚠️ موجودی کافی نیست.\nموجودی: {utils.format_currency(wallet_balance)}\nنیاز: {utils.format_currency(total_to_pay)}", show_alert=True)
                return

            if db.update_wallet_balance(user_id, -total_to_pay, "ms_payment", f"پرداخت مرحله {idx+1} معامله {cid}"):
                # آپدیت وضعیت مرحله
                milestones[idx]["status"] = "paid"
                db.update_contract(cid, {"milestones": milestones})
                db.append_contract_history(cid, f"💰 پرداخت مرحله {idx+1} از کیف پول توسط کارفرما", actor_id=user_id)
                
                bot.answer_callback_query(call.id, "✅ مرحله با موفقیت پرداخت شد.", show_alert=True)
                bot.send_message(call.message.chat.id, f"✅ **مرحله «{utils.escape_markdown(ms.get('title', ''))}» پرداخت شد.**\nمجری می‌تواند کار را آغاز کند.")
                
                # اطلاع به مجری
                seller_id = contract.get("seller_id")
                if seller_id:
                    try:
                        bot.send_message(seller_id, f"🚀 **مرحله «{utils.escape_markdown(ms.get('title', ''))}» از معامله `{cid}` پرداخت شد!**\nمی‌توانید این مرحله را انجام دهید.")
                    except: pass
            else:
                bot.answer_callback_query(call.id, "❌ خطا در کسر از موجودی.", show_alert=True)
        except Exception as e:
            logger.error(f"Error in handle_ms_pay_with_wallet: {e}")
            try: bot.answer_callback_query(call.id, "❌ خطای سیستمی در پرداخت مرحله.", show_alert=True)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pay_with_receipt_") and "_ms_" in call.data)
    def handle_ms_pay_with_receipt(call: CallbackQuery):
        payload = call.data.replace("pay_with_receipt_", "", 1)
        cid, _, idx_str = payload.rpartition("_ms_")
        # تغییر دیتا برای هندلر اصلی msp_pay
        call.data = f"msp_pay_actual_{cid}_{idx_str}"
        handle_ms_pay_start(call)

    def handle_ms_pay_actual_start(call: CallbackQuery):
        """هندلر واقعی برای آپلود فیش مرحله (پس از انتخاب روش فیش)"""
        payload = call.data.replace("msp_pay_actual_", "", 1)
        cid, _, idx_str = payload.rpartition("_")
        idx = int(idx_str)
        
        contract = db.get_contract(cid)
        milestones = contract.get("milestones") or []
        ms = milestones[idx]
        
        db.set_user_state(call.from_user.id, "WAITING_MS_RECEIPT_PHOTO", {"receipt_cid": cid, "receipt_idx": idx})
        bot.send_message(
            call.message.chat.id, 
            f"📸 **ارسال فیش مرحله {idx + 1}**\n\n"
            f"💎 مرحله: **{ms.get('title')}**\n"
            f"💵 مبلغ: **{utils.format_currency(utils.safe_float(ms.get('amount', 0)))}**\n\n"
            "لطفاً تصویر فیش واریزی این مرحله را ارسال کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(
        content_types=['photo', 'document'],
        func=lambda msg: getattr(msg, "user_state", None) == "WAITING_MS_RECEIPT_PHOTO"
    )
    def handle_ms_receipt_photo(message: Message):
        """دریافت فیش واریزی یک مرحله و ارسال آن برای تایید ادمین"""
        user_id = message.from_user.id
        proc_msg = bot.send_message(message.chat.id, "⏳ **در حال ثبت فیش مرحله...**")
        try:
            _, data = db.get_user_state(user_id)
            cid = data.get("receipt_cid") if isinstance(data, dict) else None
            idx = data.get("receipt_idx") if isinstance(data, dict) else None
            is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

            if not cid or idx is None:
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin_u))
                return

            contract = db.get_contract(cid)
            milestones = contract.get("milestones") if contract else None
            if not contract or not milestones or idx >= len(milestones):
                db.clear_user_state(user_id)
                bot.send_message(message.chat.id, "⚠️ این مرحله دیگر یافت نشد.", reply_markup=kb.get_main_menu(is_admin_u))
                return

            file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
            if not file_id:
                bot.send_message(message.chat.id, "⚠️ لطفاً فقط تصویر یا فایل فیش واریزی را ارسال کنید.")
                return

            ms_amount = utils.safe_float(milestones[idx].get('amount', 0))
            milestones[idx]["receipt_file_id"] = file_id
            milestones[idx]["status"] = "receipt_submitted"
            db.update_contract(cid, {"milestones": milestones})
            db.clear_user_state(user_id)
            db.append_contract_history(
                cid,
                f"💳 کارفرما فیش واریزی مرحله «{milestones[idx].get('title')}» (مرحله {idx + 1}) را ارسال کرد.",
                actor_id=user_id, file_id=file_id, file_type="photo"
            )
            
            # ثبت در جدول متمرکز جهت نظارت ادمین
            db.create_pending_receipt(
                receipt_type="milestone",
                related_id=f"{cid}:{idx}", 
                user_id=user_id,
                file_id=file_id,
                amount=ms_amount,
                description=f"پرداخت مرحله {idx+1} معامله {cid}: {milestones[idx].get('title')}"
            )

            safe_ms_title = utils.escape_html(milestones[idx].get('title'))
            admin_caption = (
                f"💳 <b>فیش واریزی مرحله جدید</b>\n\n"
                f"📌 <b>کد معامله:</b> <code>{cid}</code>\n"
                f"📝 <b>مرحله:</b> {idx + 1} از {len(milestones)} — {safe_ms_title}\n"
                f"💵 <b>مبلغ این مرحله:</b> {utils.format_currency(ms_amount)}\n"
                f"👤 <b>کارفرما:</b> <code>{user_id}</code>\n\n"
                "لطفاً مبلغ واریزی در تصویر فیش را با مبلغ بالا مطابقت دهید."
            )
            
            utils.send_admin_alert(
                bot,
                content_type="photo",
                file_id=file_id,
                text=admin_caption + f"\n\n📢 #فیش_مرحله‌ای #مرحله_{idx+1}",
                reply_markup=kb.get_ms_receipt_admin_inline(cid, idx, user_id)
            )

            try:
                bot.delete_message(message.chat.id, proc_msg.message_id)
            except:
                pass

            bot.send_message(
                message.chat.id,
                f"✅ **فیش واریزی مرحله {idx + 1} دریافت شد و جهت تایید برای مدیریت ارسال گردید.**",
                reply_markup=kb.get_main_menu(is_admin_u)
            )
        except Exception as e:
            logger.error(f"Critical error in handle_ms_receipt_photo: {e}")
            bot.send_message(message.chat.id, "⚠️ متأسفانه خطایی در فرآیند ثبت فیش مرحله رخ داد. لطفاً با پشتیبانی تماس بگیرید.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("msp_deliver_"))
    def handle_ms_deliver_start(call: CallbackQuery):
        """شروع ارسال تحویلی یک مرحله توسط مجری"""
        payload = call.data.replace("msp_deliver_", "", 1)
        cid, _, idx_str = payload.rpartition("_")
        try:
            idx = int(idx_str)
        except ValueError:
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        seller_id = contract.get("seller_id")
        if seller_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط مجری معامله می‌تواند تحویل دهد.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones) or milestones[idx].get("status") not in ["paid", "active", "in_progress"]:
            bot.answer_callback_query(call.id, "⚠️ این مرحله در وضعیت قابل تحویل نیست.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_MS_DELIVERY_CONTENT", {"deliver_cid": cid, "deliver_idx": idx})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"📦 **ارسال تحویلی مرحله {idx + 1} — «{milestones[idx].get('title')}»**\n\n"
            "لطفاً فایل/عکس مربوط به همین مرحله را به‌همراه توضیح لازم ارسال کنید "
            "(یا در صورت نبود فایل، فقط توضیح متنی کافی است):",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(
        content_types=['photo', 'document', 'text', 'video', 'voice', 'audio', 'video_note', 'animation'],
        func=lambda msg: getattr(msg, "user_state", None) == "WAITING_MS_DELIVERY_CONTENT"
    )
    def handle_ms_delivery_content(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("deliver_cid") if isinstance(data, dict) else None
        idx = data.get("deliver_idx") if isinstance(data, dict) else None
        is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        if not cid or idx is None:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin_u))
            return

        if message.text and message.text.strip() in ["❌ انصراف و بازگشت به منو", "❌ انصراف"]:
            return

        contract = db.get_contract(cid)
        milestones = contract.get("milestones") if contract else None
        if not contract or not milestones or idx >= len(milestones) or milestones[idx].get("status") not in ["paid", "active", "in_progress"]:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ این مرحله دیگر در وضعیت قابل تحویل نیست.", reply_markup=kb.get_main_menu(is_admin_u))
            return

        file_id, file_type = _get_media_from_message(message)
        note = (message.caption or message.text or "").strip()

        delivery_files = milestones[idx].get("delivery_files") or []
        if file_id:
            delivery_files.append({"file_id": file_id, "type": file_type, "note": note})
        milestones[idx]["delivery_files"] = delivery_files
        milestones[idx]["delivery_note"] = note or milestones[idx].get("delivery_note")
        milestones[idx]["status"] = "delivered"
        db.update_contract(cid, {"milestones": milestones})
        db.clear_user_state(user_id)
        db.append_contract_history(
            cid, f"📦 مجری تحویلی مرحله «{milestones[idx].get('title')}» را ارسال کرد.\nتوضیح: {note or '—'}",
            actor_id=user_id, file_id=file_id, file_type=file_type
        )

        bot.send_message(
            message.chat.id,
            f"✅ **تحویلی مرحله {idx + 1} ارسال شد.** کارفرما اکنون باید تصمیم بگیرد.",
            parse_mode="Markdown",
            reply_markup=kb.get_main_menu(is_admin_u)
        )

        buyer_id = contract.get("buyer_id")
        decision_caption = (
            f"📦 **تحویلی مرحله {idx + 1} — «{milestones[idx].get('title')}» معامله `{cid}`**\n\n"
            f"📝 **توضیح مجری:**\n{note or 'بدون توضیح'}\n\n"
            "لطفاً بررسی و تصمیم بگیرید:"
        )
        if buyer_id:
            try:
                _forward_media_to_user(bot, buyer_id, file_id, file_type, decision_caption, 
                                       reply_markup=kb.get_ms_delivery_review_keyboard(cid, idx))
            except Exception as e:
                logger.error(f"خطا در ارسال تحویلی مرحله‌ای به کارفرما: {e}")

        archive_caption = (
            f"📂 <b>بایگانی تحویل مرحله‌ای</b>\n"
            f"🔹 <b>کد معامله:</b> <code>#{cid}</code> (مرحله {idx + 1})\n"
            f"👤 <b>مجری:</b> <code>{user_id}</code>\n\n"
            f"📝 <b>توضیح:</b> {utils.escape_html(note or '—')}"
        )
        if file_id:
            utils.notify_archive(bot, content_type=file_type, file_id=file_id, text=archive_caption)
        else:
            utils.notify_archive(bot, content_type="text", text=archive_caption)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("msp_dok_"))
    def handle_ms_delivery_approve(call: CallbackQuery):
        """تایید تحویلی یک مرحله توسط کارفرما و آزادسازی وجه همان مرحله"""
        payload = call.data.replace("msp_dok_", "", 1)
        cid, _, idx_str = payload.rpartition("_")
        try:
            idx = int(idx_str)
        except ValueError:
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند تایید کند.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones) or milestones[idx].get("status") != "delivered":
            bot.answer_callback_query(call.id, "⚠️ این مرحله قبلاً بررسی شده و دیگر قابل تغییر نیست.", show_alert=True)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        milestones[idx]["status"] = "completed"
        db.update_contract(cid, {"milestones": milestones})

        ms_amount = float(milestones[idx].get("amount", 0))
        comm, net, employer_pays = utils.calculate_commission(ms_amount, payer=contract.get("commission_payer", "freelancer"))
        seller_id = contract.get("seller_id")
        if seller_id:
            db.update_wallet_balance(
                seller_id, net, "milestone_release",
                f"آزادسازی مرحله «{milestones[idx].get('title', '')}» از معامله {cid}"
            )
            utils.credit_ambassador_commission(bot, contract, comm, cid)

        db.append_contract_history(
            cid, f"✅ کارفرما تحویلی مرحله «{milestones[idx].get('title')}» را تایید کرد و وجه آزاد شد.",
            actor_id=user_id
        )

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "✅ تایید شد و وجه این مرحله آزاد گردید.")

        if seller_id:
            try:
                bot.send_message(
                    seller_id,
                    f"🔓 مرحله «{milestones[idx].get('title')}» از معامله `{cid}` تایید شد.\n"
                    f"مبلغ **{utils.format_currency(net)}** به کیف پول شما واریز گردید.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        next_idx = idx + 1
        contract["milestones"] = milestones
        if next_idx < len(milestones):
            _start_milestone_payment(bot, cid, contract, next_idx)
            return

        # این آخرین مرحله تعریف‌شده بود
        db.update_contract(cid, {"status": "completed"})
        if buyer_id:
            try:
                bot.send_message(buyer_id, f"🎉 **معامله `{cid}` با تسویه آخرین مرحله، کامل و بسته شد.**", parse_mode="Markdown")
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("msp_dno_"))
    def handle_ms_delivery_reject(call: CallbackQuery):
        """درخواست اصلاح تحویلی یک مرحله توسط کارفرما (بدون آزادسازی وجه)"""
        payload = call.data.replace("msp_dno_", "", 1)
        cid, _, idx_str = payload.rpartition("_")
        try:
            idx = int(idx_str)
        except ValueError:
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند این تصمیم را بگیرد.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones) or milestones[idx].get("status") != "delivered":
            bot.answer_callback_query(call.id, "⚠️ این مرحله دیگر قابل تغییر نیست.", show_alert=True)
            return

        milestones[idx]["status"] = "paid"  # مجری باید دوباره تحویل دهد
        db.update_contract(cid, {"milestones": milestones})
        db.append_contract_history(cid, f"⚠️ کارفرما تحویلی مرحله «{milestones[idx].get('title')}» را رد و درخواست اصلاح کرد.", actor_id=user_id)

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "درخواست اصلاح برای مجری ارسال شد.")

        seller_id = contract.get("seller_id")
        if seller_id:
            try:
                bot.send_message(
                    seller_id,
                    f"⚠️ کارفرمای معامله `{cid}` تحویلی مرحله «{milestones[idx].get('title')}» را تایید نکرد.\n"
                    "لطفاً پس از اصلاح، دوباره از همان دکمه تحویل این مرحله اقدام کنید.",
                    parse_mode="Markdown",
                    reply_markup=kb.get_ms_deliver_keyboard(cid, idx)
                )
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reject_project_"))
    def handle_reject_project_start(call: CallbackQuery):
        """شروع مرحلهٔ ثبت دلیل عدم تایید پروژه توسط کارفرما"""
        cid = call.data.replace("reject_project_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند پروژه را رد کند.", show_alert=True)
            return

        # جلوگیری از کلیک دوباره روی دکمه‌های یک پیام قدیمی (مثلاً بعد از اینکه
        # پروژه قبلاً تایید نهایی شده یا در وضعیت دیگری است)
        if contract.get("status") not in ["work_submitted", "delivered"]:
            bot.answer_callback_query(call.id, "⚠️ این پروژه دیگر در وضعیت قابل رد نیست.", show_alert=True)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        db.set_user_state(user_id, "WAITING_PROJECT_REJECT_REASON", {
            "reject_cid": cid,
            "reject_msg_id": call.message.message_id,
            "reject_chat_id": call.message.chat.id
        })
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"⚠️ لطفاً **علت عدم تایید و موارد نیازمند اصلاح** برای معامله `{cid}` را بنویسید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_PROJECT_REJECT_REASON")
    def handle_reject_project_reason(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("reject_cid") if isinstance(data, dict) else None
        src_msg_id = data.get("reject_msg_id") if isinstance(data, dict) else None
        src_chat_id = data.get("reject_chat_id") if isinstance(data, dict) else None
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        if not cid:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin))
            return

        contract = db.get_contract(cid)
        db.clear_user_state(user_id)

        if not contract or contract.get("status") not in ["work_submitted", "delivered"]:
            bot.send_message(message.chat.id, "⚠️ این پروژه دیگر در وضعیت قابل رد نیست.", reply_markup=kb.get_main_menu(is_admin))
            return

        seller_id = contract.get("seller_id")
        reason = message.text.strip()

        # پیام قدیمی حاوی دکمه‌های «تایید/رد» را غیرفعال کن تا امکان کلیک دوباره نباشد
        if src_chat_id and src_msg_id:
            try:
                bot.edit_message_reply_markup(chat_id=src_chat_id, message_id=src_msg_id, reply_markup=None)
            except Exception:
                pass

        free_edits_left = int(contract.get("free_edits_left", 0) or 0)

        if free_edits_left > 0:
            # ====================================================
            # حالت عادی: هنوز سهمیه ویرایش رایگان باقی مانده
            # ====================================================
            new_free = free_edits_left - 1
            db.update_contract(cid, {"status": "active", "free_edits_left": new_free})
            db.append_contract_history(
                cid,
                f"⚠️ کارفرما پروژه را رد کرد (از ویرایش رایگان استفاده شد؛ {new_free} بار باقی‌مانده).\nعلت:\n{reason}",
                actor_id=user_id
            )
            bot.send_message(message.chat.id, "✅ موارد اصلاحی ثبت و به مجری ابلاغ گردید.", reply_markup=kb.get_main_menu(is_admin))

            if seller_id:
                try:
                    rejection_msg = utils.format_project_rejection_msg(cid, reason, new_free)
                    bot.send_message(
                        seller_id, 
                        rejection_msg, 
                        parse_mode="Markdown",
                        reply_markup=kb.get_freelancer_reupload_keyboard(cid)
                    )
                except Exception as e:
                    logger.error(f"خطا در ارسال پیام رد پروژه به مجری: {e}")
        else:
            # ====================================================
            # بخش جدید: سهمیه ویرایش رایگان تمام شده — مجری باید برای این
            # ویرایش اضافه قیمت تعیین کند و کارفرما باید با آن موافقت/پرداخت کند
            # ====================================================
            db.update_contract(cid, {"status": "awaiting_edit_price", "pending_edit_reason": reason})
            db.append_contract_history(
                cid,
                f"⚠️ کارفرما پروژه را رد کرد؛ سهمیه ویرایش رایگان تمام شده است.\nعلت:\n{reason}",
                actor_id=user_id
            )
            bot.send_message(
                message.chat.id,
                "✅ علت رد ثبت شد. از آنجا که سهمیه ویرایش رایگان شما تمام شده، از مجری خواسته شد "
                "برای این اصلاح مبلغی تعیین کند؛ پس از تعیین قیمت، تصمیم پرداخت با شماست.",
                reply_markup=kb.get_main_menu(is_admin)
            )

            if seller_id:
                db.set_user_state(seller_id, "WAITING_EXTRA_EDIT_PRICE", {"extra_edit_cid": cid})
                try:
                    bot.send_message(
                        seller_id,
                        f"⚠️ **پروژه معامله `{cid}` توسط کارفرما رد شد و سهمیه ویرایش رایگان شما تمام شده است.**\n\n"
                        f"📌 **علت/موارد نیازمند اصلاح:**\n{reason}\n\n"
                        "💰 لطفاً مبلغ درخواستی خود را برای این ویرایش اضافه (به تومان) ارسال کنید. "
                        "این مبلغ برای تایید نهایی به کارفرما نمایش داده می‌شود:",
                        parse_mode="Markdown",
                        reply_markup=kb.get_freelancer_extra_edit_keyboard(cid)
                    )
                except Exception as e:
                    logger.error(f"خطا در اطلاع‌رسانی نیاز به قیمت‌گذاری ویرایش اضافه به مجری: {e}")

    # ====================================================
    # ۹.۵ قیمت‌گذاری و تایید ویرایش اضافه (پس از اتمام ویرایش رایگان) — بخش جدید
    # ====================================================
    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_EXTRA_EDIT_PRICE")
    def handle_extra_edit_price_input(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("extra_edit_cid") if isinstance(data, dict) else None
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        if not cid:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin))
            return

        price = utils.parse_amount_flexible(message.text)
        if not price:
            bot.send_message(message.chat.id, "⚠️ مبلغ وارد‌شده قابل تشخیص نیست.\nلطفاً مبلغ ویرایش را به صورت عدد مثبت وارد کنید (مثال: `500000` یا `500,000`).", parse_mode="Markdown")
            return

        contract = db.get_contract(cid)
        if not contract or contract.get("status") != "awaiting_edit_price":
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ این درخواست دیگر معتبر نیست.", reply_markup=kb.get_main_menu(is_admin))
            return
        db.update_contract(cid, {"extra_edit_price": price})
        db.clear_user_state(user_id)
        db.append_contract_history(cid, f"💰 مجری برای ویرایش اضافه مبلغ {utils.format_currency(price)} تعیین کرد.", actor_id=user_id)

        bot.send_message(
            message.chat.id,
            f"✅ مبلغ {utils.format_currency(price)} برای کارفرما ارسال شد. پس از موافقت و پرداخت ایشان، اصلاحات را انجام دهید.",
            reply_markup=kb.get_main_menu(is_admin)
        )

        buyer_id = contract.get("buyer_id")
        if buyer_id:
            try:
                bot.send_message(
                    buyer_id,
                    f"💰 **مجری برای ویرایش اضافه معامله `{cid}` مبلغ {utils.format_currency(price)} تعیین کرد.**\n\n"
                    "در صورت موافقت، این مبلغ از کیف پول شما کسر و پس از تکمیل اصلاحات همراه با باقی وجه به مجری واریز می‌شود:",
                    parse_mode="Markdown",
                    reply_markup=kb.get_extra_edit_decision_inline(cid)
                )
            except Exception as e:
                logger.error(f"خطا در اطلاع‌رسانی قیمت ویرایش اضافه به کارفرما: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("set_edit_price_"))
    def handle_set_edit_price(call: CallbackQuery):
        cid = call.data.replace("set_edit_price_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return
        
        user_id = call.from_user.id
        seller_id = contract.get("seller_id")
        if seller_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط مجری معامله می‌تواند قیمت تعیین کند.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_EXTRA_EDIT_PRICE", {"extra_edit_cid": cid})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"💰 **تعیین هزینه برای ویرایش اضافه (معامله `{cid}`)**\n\n"
            "لطفاً مبلغ پیشنهادی خود را به تومان ارسال کنید. این مبلغ برای تایید و پرداخت به کارفرما نمایش داده می‌شود:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("view_extra_edit_"))
    def handle_view_extra_edit(call: CallbackQuery):
        cid = call.data.replace("view_extra_edit_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return
        price = contract.get("extra_edit_price")
        bot.answer_callback_query(call.id)
        if not price:
            bot.send_message(call.message.chat.id, "⏳ مجری هنوز مبلغی برای ویرایش اضافه تعیین نکرده است.")
            return
        bot.send_message(
            call.message.chat.id,
            f"💰 **مبلغ ویرایش اضافه معامله `{cid}`:** {utils.format_currency(float(price))}",
            parse_mode="Markdown",
            reply_markup=kb.get_extra_edit_decision_inline(cid)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("extra_edit_approve_"))
    def handle_extra_edit_approve(call: CallbackQuery):
        cid = call.data.replace("extra_edit_approve_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند این تصمیم را بگیرد.", show_alert=True)
            return

        if contract.get("status") != "awaiting_edit_price":
            bot.answer_callback_query(call.id, "⚠️ این درخواست دیگر معتبر نیست.", show_alert=True)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        price = utils.safe_float(contract.get("extra_edit_price", 0) or 0)
        user = db.get_user(buyer_id)
        balance = utils.safe_float(user.get("wallet_balance", 0.0)) if user else 0.0

        if balance < price:
            bot.answer_callback_query(call.id, "❌ موجودی کیف پول شما کافی نیست.", show_alert=True)
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("💳 شارژ کلی کیف پول", callback_data="manage_cards"),
                InlineKeyboardButton("📸 ارسال فیش برای همین هزینه", callback_data=f"upload_ee_receipt_{cid}"),
                InlineKeyboardButton("🔙 بازگشت", callback_data=f"view_contract_terms:{cid}")
            )
            bot.send_message(
                call.message.chat.id, 
                f"⚠️ **موجودی ناکافی**\n\nهزینه ویرایش اضافه: {utils.format_currency(price)}\nموجودی فعلی: {utils.format_currency(balance)}\n\n"
                "شما می‌توانید کیف پول خود را شارژ کنید یا مستقیماً فیش واریزی برای این هزینه را ارسال کنید:",
                parse_mode="Markdown",
                reply_markup=markup
            )
            return

        db.update_wallet_balance(buyer_id, -price, "extra_edit_charge", f"پرداخت هزینه ویرایش اضافه معامله {cid}")
        held = float(contract.get("held_extra_amount", 0) or 0) + price
        db.update_contract(cid, {
            "status": "active",
            "held_extra_amount": held,
            "extra_edit_price": None,
            "pending_edit_reason": None
        })
        db.append_contract_history(cid, f"✅ کارفرما هزینه ویرایش اضافه ({utils.format_currency(price)}) را پرداخت کرد.", actor_id=user_id)

        bot.answer_callback_query(call.id, "✅ پرداخت انجام شد.")
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, f"✅ مبلغ {utils.format_currency(price)} از کیف پول شما کسر و در حساب امانت بلوکه شد.")

        seller_id = contract.get("seller_id")
        if seller_id:
            try:
                bot.send_message(
                    seller_id, 
                    f"✅ **کارفرمای معامله `{cid}` هزینه ویرایش اضافه را پرداخت کرد.**\nلطفاً اصلاحات را انجام داده و مجدداً تحویل دهید.",
                    reply_markup=kb.get_freelancer_reupload_keyboard(cid),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("extra_edit_reject_"))
    def handle_extra_edit_reject(call: CallbackQuery):
        cid = call.data.replace("extra_edit_reject_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند این تصمیم را بگیرد.", show_alert=True)
            return

        if contract.get("status") != "awaiting_edit_price":
            bot.answer_callback_query(call.id, "⚠️ این درخواست دیگر معتبر نیست.", show_alert=True)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        # کارفرما با پرداخت هزینه ویرایش اضافه موافق نیست؛ تصمیم درباره تحویلی
        # قبلی (تایید نهایی یا داوری) همچنان برای او باز می‌ماند
        db.update_contract(cid, {"status": "work_submitted", "extra_edit_price": None, "pending_edit_reason": None})
        db.append_contract_history(cid, "❌ کارفرما با پرداخت هزینه ویرایش اضافه موافقت نکرد.", actor_id=user_id)

        bot.answer_callback_query(call.id, "ثبت شد.")
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            f"شما با پرداخت هزینه ویرایش اضافه موافقت نکردید. می‌توانید تحویلی فعلی معامله `{cid}` را از «📜 معاملات من» تایید نهایی کنید یا درخواست داوری دهید.",
            parse_mode="Markdown"
        )

        seller_id = contract.get("seller_id")
        if seller_id:
            try:
                bot.send_message(seller_id, f"❌ **کارفرمای معامله `{cid}` با پرداخت هزینه ویرایش اضافه موافقت نکرد.**")
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith("bargain_"))
    def handle_bargain_start(call: CallbackQuery):
        cid = call.data.replace("bargain_", "", 1)
        user_id = call.from_user.id
        db.set_user_state(user_id, "WAITING_BARGAIN_PRICE", {"bargain_cid": cid})
        bot.answer_callback_query(call.id)

        # نمایش وضعیت ویرایش رایگان باقی‌مانده در پنل چانه‌زنی
        contract = db.get_contract(cid)
        free_edits_info = ""
        if contract:
            free_edits_left = int(contract.get("free_edits_left", 0) or 0)
            free_edits_total = int(contract.get("free_edits_total", 0) or 0)
            current_amount = float(contract.get("amount", 0) or 0)
            free_edits_info = (
                f"\n\n📋 **وضعیت معامله `{cid}`:**\n"
                f"💰 مبلغ فعلی: {utils.format_currency(current_amount)}\n"
                f"🔄 ویرایش رایگان باقی‌مانده: **{free_edits_left}** از {free_edits_total} بار"
            )

        bot.send_message(
            call.message.chat.id,
            f"💬 **پیشنهاد مبلغ جدید برای معامله `{cid}`**{free_edits_info}\n\n"
            "لطفاً مبلغ پیشنهادی جدید خود را به تومان ارسال کنید\n"
            "(می‌توانید با کاما، بدون کاما یا با اعداد فارسی وارد کنید — مثال: `5,000,000` یا `5000000` یا `۵۰۰۰۰۰۰`):",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_BARGAIN_PRICE")
    def process_bargain_price(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("bargain_cid") if isinstance(data, dict) else None
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        if not cid:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ معامله موردنظر یافت نشد.", reply_markup=kb.get_main_menu(is_admin))
            return

        new_amount = utils.parse_amount_flexible(message.text)
        if not new_amount:
            bot.send_message(message.chat.id, "⚠️ مبلغ وارد‌شده قابل تشخیص نیست.\nلطفاً مبلغ را به صورت عدد وارد کنید (مثال: `5000000` یا `5,000,000` یا `۵۰۰۰۰۰۰`).", parse_mode="Markdown")
            return

        db.update_contract(cid, {"amount": new_amount, "status": "bargaining"})
        db.clear_user_state(user_id)

        bot.send_message(
            message.chat.id,
            f"✅ پیشنهاد مبلغ جدید ({utils.format_currency(new_amount)}) برای معامله `{cid}` ثبت شد.",
            parse_mode="Markdown",
            reply_markup=kb.get_main_menu(is_admin)
        )

        updated_contract = db.get_contract(cid)
        if updated_contract:
            notify_other_party(
                bot, updated_contract, user_id,
                f"🔄 طرف مقابل برای معامله `{cid}` مبلغ جدیدی پیشنهاد داد: "
                f"**{utils.format_currency(new_amount)}**\n"
                "برای بررسی و پاسخ، از «📜 معاملات من» وارد شوید."
            )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("deliver_"))
    def handle_deliver_contract(call: CallbackQuery):
        """
        بخش جدید: ارسال پروژه توسط مجری. برخلاف قبل که بلافاصله وضعیت را
        «تحویل شده» می‌کرد بدون گرفتن هیچ فایلی، اکنون از مجری فایل/عکس یا
        توضیح تحویل را می‌گیرد، برای کارفرما با دکمه تعیین وضعیت ارسال می‌کند
        و یک نسخه بایگانی برای ادمین (جهت داوری‌های احتمالی بعدی) می‌فرستد.
        """
        cid = call.data.replace("deliver_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        seller_id = contract.get("seller_id")
        if seller_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط مجری معامله می‌تواند پروژه را تحویل دهد.", show_alert=True)
            return

        if contract.get("status") not in ["in_progress", "active", "paid"]:
            logger.warning(f"⚠️ [DELIVER_REJECT] Deal {cid} status is {contract.get('status')} (expected active/in_progress/paid)")
            bot.answer_callback_query(call.id, "⚠️ این معامله در وضعیت قابل تحویل نیست.", show_alert=True)
            return

        # اگر معامله مرحله‌ای بود، لیست مراحل برای تحویل نمایش داده شود
        milestones = contract.get("milestones")
        if milestones and len(milestones) > 0:
            bot.answer_callback_query(call.id)
            bot.send_message(
                call.message.chat.id,
                f"📦 **انتخاب مرحله برای تحویل (معامله `{cid}`)**\n\n"
                "لطفاً مرحله‌ای که قصد تحویل فایل‌های آن را دارید انتخاب کنید:",
                reply_markup=kb.get_milestone_delivery_list_inline(cid, milestones)
            )
            return

        db.set_user_state(user_id, "WAITING_DELIVERY_CONTENT", {"deliver_cid": cid})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"📦 **ارسال پروژه معامله `{cid}`**\n\n"
            "لطفاً فایل/عکس نهایی پروژه را به‌همراه توضیح لازم ارسال کنید (یا در صورت نبود فایل، "
            "فقط توضیح متنی کافی است). پس از ارسال، برای کارفرما دکمه تعیین وضعیت فرستاده می‌شود:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(
        content_types=['photo', 'document', 'text', 'video', 'voice', 'audio', 'video_note', 'animation'],
        func=lambda msg: getattr(msg, "user_state", None) == "WAITING_DELIVERY_CONTENT"
    )
    def handle_delivery_content(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("deliver_cid") if isinstance(data, dict) else None
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        if not cid:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد، لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin))
            return

        if message.text and message.text.strip() in ["❌ انصراف و بازگشت به منو", "❌ انصراف"]:
            return  # هندلر انصراف عمومی خودش این را می‌گیرد

        contract = db.get_contract(cid)
        if not contract or contract.get("status") not in ["in_progress", "active", "paid"]:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "⚠️ این معامله دیگر در وضعیت قابل تحویل نیست.", reply_markup=kb.get_main_menu(is_admin))
            return

        file_id, file_type = _get_media_from_message(message)
        note = (message.caption or message.text or "").strip()

        delivery_files = contract.get("delivery_files") or []
        if file_id:
            delivery_files.append({"file_id": file_id, "type": file_type, "note": note})

        delivered_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.update_contract(cid, {
            "status": "work_submitted",
            "delivery_files": delivery_files,
            "delivered_at": delivered_at,
            "delivery_note": note or contract.get("delivery_note")
        })
        db.clear_user_state(user_id)
        db.append_contract_history(
            cid,
            f"📦 مجری پروژه را تحویل داد.\nتوضیح: {note or '—'}",
            actor_id=user_id,
            file_id=file_id,
            file_type=file_type
        )

        bot.send_message(
            message.chat.id,
            f"✅ **پروژه معامله `{cid}` با موفقیت ارسال شد.** کارفرما اکنون باید تصمیم بگیرد.",
            parse_mode="Markdown",
            reply_markup=kb.get_main_menu(is_admin)
        )

        buyer_id = contract.get("buyer_id")
        has_ms = bool(contract.get("milestones"))
        decision_caption = (
            f"📦 **مجری معاملهٔ `{cid}` پروژه را تحویل داد.**\n\n"
            f"📝 **توضیح مجری:**\n{note or 'بدون توضیح'}\n\n"
            "لطفاً بررسی و وضعیت پروژه را مشخص کنید:"
        )
        if buyer_id:
            try:
                _forward_media_to_user(bot, buyer_id, file_id, file_type, decision_caption, 
                                       reply_markup=kb.get_contract_action_keyboard(cid, "employer", "work_submitted", has_ms))
            except Exception as e:
                logger.error(f"خطا در ارسال تحویلی پروژه به کارفرما: {e}")


        # بایگانی نسخه در کانال مدیریت (MJNOTE) جهت دسترسی در صورت بروز اختلاف بعدی
        archive_caption = (
            f"📂 <b>بایگانی تحویل نهایی پروژه</b>\n"
            f"🔹 <b>کد معامله:</b> <code>#{cid}</code>\n"
            f"👤 <b>مجری:</b> <code>{user_id}</code>\n\n"
            f"📝 <b>توضیح:</b> {utils.escape_html(note or '—')}"
        )
        if file_id:
            utils.notify_archive(bot, content_type=file_type, file_id=file_id, text=archive_caption)
        else:
            utils.notify_archive(bot, content_type="text", text=archive_caption)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("ms_files_"))
    def handle_view_ms_files(call: CallbackQuery):
        user_id = call.from_user.id
        payload = call.data.replace("ms_files_", "", 1)
        try:
            cid, idx_str = payload.rsplit("_", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        # بررسی دسترسی
        if contract.get("buyer_id") != user_id and contract.get("seller_id") != user_id:
            bot.answer_callback_query(call.id, "🚫 عدم دسترسی.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones):
            bot.answer_callback_query(call.id, "❌ مرحله یافت نشد.", show_alert=True)
            return

        delivery_files = milestones[idx].get("delivery_files") or []
        if not delivery_files:
            bot.answer_callback_query(call.id, "📂 هیچ فایلی برای این مرحله ثبت نشده است.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"📦 **فایل‌های تحویلی مرحله {idx + 1} معامله `{cid}`:**", parse_mode="Markdown")

        for f in delivery_files:
            f_id = f.get("file_id")
            f_type = f.get("type")
            f_note = f.get("note", "")
            
            caption = f"📎 **توضیح:** {f_note}" if f_note else f"📎 فایل تحویلی مرحله {idx + 1}"
            _forward_media_to_user(bot, call.message.chat.id, f_id, f_type, caption)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("view_delivery_"))
    def handle_view_delivery(call: CallbackQuery):
        user_id = call.from_user.id
        cid = call.data.replace("view_delivery_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        # بررسی دسترسی
        if contract.get("buyer_id") != user_id and contract.get("seller_id") != user_id:
            bot.answer_callback_query(call.id, "🚫 عدم دسترسی.", show_alert=True)
            return

        delivery_files = contract.get("delivery_files") or []
        if not delivery_files:
            # بررسی اگر در مراحل باشد (پرداخت مرحله‌ای)
            milestones = contract.get("milestones") or []
            all_files = []
            for ms in milestones:
                all_files.extend(ms.get("delivery_files") or [])
            delivery_files = all_files

        if not delivery_files:
            bot.answer_callback_query(call.id, "📂 هیچ فایلی برای این معامله ثبت نشده است.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"📦 **فایل‌های تحویلی معامله `{cid}`:**", parse_mode="Markdown")

        for f in delivery_files:
            f_id = f.get("file_id")
            f_type = f.get("type")
            f_note = f.get("note", "")
            
            caption = f"📎 **توضیح:** {f_note}" if f_note else "📎 فایل تحویلی"
            try:
                if f_type == "photo":
                    bot.send_photo(call.message.chat.id, f_id, caption=caption, parse_mode="Markdown")
                elif f_type == "document":
                    bot.send_document(call.message.chat.id, f_id, caption=caption, parse_mode="Markdown")
                else:
                    bot.send_message(call.message.chat.id, f"📎 **فایل (شناسه):** `{f_id}`\n{caption}", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Error sending delivery file {f_id}: {e}")
                bot.send_message(call.message.chat.id, f"⚠️ خطا در ارسال یکی از فایل‌ها (ID: `{f_id}`).")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("final_confirm_"))
    def handle_final_confirm(call: CallbackQuery):
        cid = call.data.replace("final_confirm_", "", 1)
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        user_id = call.from_user.id
        buyer_id = contract.get("buyer_id")
        if buyer_id != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرمای معامله می‌تواند تایید نهایی کند.", show_alert=True)
            return

        # نکته مهم: پس از تایید پروژه توسط کارفرما، کارفرما دیگر نباید بتواند
        # وضعیت معامله را تغییر دهد. این گارد از کلیک دوباره روی دکمه‌های یک
        # پیام قدیمی (تایید/رد) بعد از بسته‌شدن معامله جلوگیری می‌کند.
        if contract.get("status") not in ["work_submitted", "delivered"]:
            bot.answer_callback_query(call.id, "⚠️ این پروژه قبلاً بررسی و نهایی شده و دیگر قابل تغییر نیست.", show_alert=True)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        
        # محاسبه جریمه دیرکرد (۱۰٪ مبلغ کل)
        penalty_amount = 0.0
        delivered_at_str = contract.get("delivered_at")
        deadline_at_str = contract.get("delivery_deadline")
        
        is_late = False
        if delivered_at_str and deadline_at_str:
            delivered_at = datetime.fromisoformat(delivered_at_str.replace('Z', '+00:00'))
            deadline_at = datetime.fromisoformat(deadline_at_str.replace('Z', '+00:00'))
            if delivered_at > deadline_at:
                is_late = True
                penalty_amount = amount * 0.10

        db.update_contract(cid, {
            "status": "completed", 
            "completed_at": completed_at,
            "late_penalty_applied": penalty_amount if is_late else 0
        })
        
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass

        amount = float(contract.get("amount", 0))
        seller_id = contract.get("seller_id")
        
        final_payout = amount
        if is_late and penalty_amount > 0:
            final_payout = amount - penalty_amount
            # واریز جریمه به کارفرما
            db.update_wallet_balance(buyer_id, penalty_amount, "late_penalty_refund", f"دریافت جریمه دیرکرد معامله {cid}")
            db.append_contract_history(cid, f"🚨 جریمه دیرکرد به مبلغ {utils.format_currency(penalty_amount)} از مجری کسر و به کارفرما واریز شد.")
            
            # اطلاع رسانی به طرفین
            try:
                penalty_msg = (
                    f"🚨 **اعمال جریمه دیرکرد**\n"
                    f"──────────────────\n"
                    f"📌 معامله: `{cid}`\n"
                    f"⚠️ به دلیل تحویل پروژه پس از مهلت مقرر، مبلغ `{utils.format_currency(penalty_amount)}` (۱۰٪ قرارداد) جریمه اعمال شد.\n\n"
                    f"✅ این مبلغ به کیف پول کارفرما بازگردانده شد."
                )
                bot.send_message(buyer_id, penalty_msg, parse_mode="Markdown")
                if seller_id:
                    bot.send_message(seller_id, penalty_msg, parse_mode="Markdown")
            except: pass

        comm, net, emp_pays = utils.calculate_commission(final_payout, payer=contract.get("commission_payer", "freelancer"))
        total_comm = 0.0

        # آزادسازی وجه فقط برای معاملات تک‌مرحله‌ای اینجا انجام می‌شود؛
        # معاملات دارای مراحل پرداخت، هر مرحله را جداگانه در بخش «مدیریت مراحل پرداخت» تسویه می‌کنند.
        if seller_id and not contract.get("milestones"):
            db.update_wallet_balance(seller_id, net, "contract_release", f"آزادسازی وجه معامله {cid}")
            total_comm += comm

        # بخش جدید: تسویه مبلغ ویرایش‌های اضافه‌ای که در طول کار پرداخت و بلوکه شده بود
        held_extra = float(contract.get("held_extra_amount", 0) or 0)
        if seller_id and held_extra > 0:
            comm_extra, net_extra, _ = utils.calculate_commission(held_extra, payer=contract.get("commission_payer", "freelancer"))
            db.update_wallet_balance(seller_id, net_extra, "extra_edit_release", f"تسویه ویرایش‌های اضافه معامله {cid}")
            db.update_contract(cid, {"held_extra_amount": 0})
            net += net_extra
            total_comm += comm_extra

        # پرداخت پورسانت سفیر (در صورت وجود معرف سفیر برای طرفین معامله)
        if total_comm > 0:
            utils.credit_ambassador_commission(bot, contract, total_comm, cid)

        db.append_contract_history(cid, f"✅ کارفرما پروژه را تایید نهایی کرد و وجه آزاد شد.", actor_id=user_id)

        bot.answer_callback_query(call.id, "✅ تایید نهایی ثبت و وجه آزاد شد.")
        bot.send_message(
            call.message.chat.id,
            f"✅ **معامله `{cid}` با موفقیت تکمیل شد** و مبلغ به کیف پول مجری واریز گردید.",
            parse_mode="Markdown"
        )
        notify_other_party(
            bot, contract, call.from_user.id,
            f"✅ کارفرمای معاملهٔ `{cid}` تحویل کار را تایید کرد.\n"
            f"مبلغ **{utils.format_currency(net)}** به کیف پول شما واریز شد."
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_contract_"))
    def handle_cancel_contract(call: CallbackQuery):
        cid = call.data.replace("cancel_contract_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        status = contract.get("status")
        locked_statuses = getattr(config, "LOCKED_DEAL_STATUSES", [])

        # ------------------------------------------------------------------
        # حالت اول: مبلغ معامله هنوز قفل نشده (پیش از تایید فیش واریزی) →
        # لغو یک‌طرفه فقط توسط سازنده قرارداد مجاز است.
        # ------------------------------------------------------------------
        if status not in locked_statuses:
            creator_id = contract.get("creator_id")
            if creator_id and user_id != creator_id:
                bot.answer_callback_query(call.id, "❌ فقط سازنده معامله می‌تواند آن را در این مرحله لغو کند.", show_alert=True)
                return
                
            db.update_contract(cid, {"status": "cancelled"})
            db.append_contract_history(cid, "🚫 معامله پیش از قفل شدن مبلغ به‌صورت یک‌طرفه لغو شد.", actor_id=user_id)
            bot.answer_callback_query(call.id, "🚫 معامله لغو شد.")
            bot.send_message(
                call.message.chat.id,
                f"🚫 معامله شماره `{cid}` لغو گردید.",
                parse_mode="Markdown"
            )
            notify_other_party(bot, contract, user_id, f"🚫 طرف مقابل معاملهٔ `{cid}` را (پیش از قفل شدن مبلغ) لغو کرد.")
            return

        # ------------------------------------------------------------------
        # حالت دوم: مبلغ قفل شده است → لغو فقط با تایید هر دو طرف امکان‌پذیر است.
        # ------------------------------------------------------------------
        buyer_id = contract.get("buyer_id")
        seller_id = contract.get("seller_id")
        other_id = seller_id if user_id == buyer_id else buyer_id

        existing_request = contract.get("cancel_requested_by")

        if not existing_request:
            db.update_contract(cid, {"cancel_requested_by": user_id})
            db.append_contract_history(
                cid,
                "⏳ درخواست لغو معامله (پس از قفل شدن مبلغ) ثبت شد و در انتظار تایید طرف مقابل است.",
                actor_id=user_id
            )
            bot.answer_callback_query(call.id, "⏳ درخواست لغو شما ثبت شد و برای طرف مقابل ارسال گردید.", show_alert=True)
            bot.send_message(
                call.message.chat.id,
                f"⏳ **درخواست لغو معامله `{cid}` ثبت شد.**\n\n"
                "⚠️ **نکات قانونی لغو معامله:**\n"
                "۱. چون مبلغ این معامله قفل شده، لغو نهایی فقط با تایید طرف مقابل انجام خواهد شد.\n"
                "۲. در صورت تایید لغو، مبلغ پس از کسر کارمزد سیستم (طبق قوانین) به کیف پول کارفرما بازگردانده می‌شود.\n"
                "۳. اگر طرف مقابل با لغو مخالفت کند، معامله فعال باقی می‌ماند و در صورت بروز اختلاف باید درخواست داوری ثبت کنید.",
                parse_mode="Markdown"
            )
            if other_id:
                try:
                    bot.send_message(
                        other_id,
                        f"⚠️ **درخواست لغو معامله `{cid}`**\n\n"
                        "طرف مقابل شما درخواست لغو این معامله را ثبت کرده است. "
                        "چون مبلغ معامله قفل شده، لغو نهایی فقط با تایید شما انجام می‌شود و کارمزد لغو از مبلغ کسر خواهد شد.",
                        parse_mode="Markdown",
                        reply_markup=kb.get_cancel_confirmation_inline(cid)
                    )
                except Exception:
                    pass
            return

        if existing_request == user_id:
            bot.answer_callback_query(
                call.id, "⏳ درخواست لغو شما قبلاً ثبت شده و در انتظار تایید طرف مقابل است.", show_alert=True
            )
            return

        # کاربر دوم با زدن دکمهٔ «لغو معامله» عملاً درخواست لغوِ طرف مقابل را تایید می‌کند
        _finalize_mutual_cancel(bot, cid, contract, user_id)
        bot.answer_callback_query(call.id, "🚫 معامله با تایید هر دو طرف لغو شد.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_cancel_"))
    def handle_confirm_cancel(call: CallbackQuery):
        cid = call.data.replace("confirm_cancel_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract or not contract.get("cancel_requested_by"):
            bot.answer_callback_query(call.id, "⚠️ این درخواست لغو دیگر معتبر نیست.", show_alert=True)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        if contract.get("cancel_requested_by") == user_id:
            bot.answer_callback_query(call.id, "⚠️ شما درخواست‌دهنده هستید؛ منتظر تایید طرف مقابل بمانید.", show_alert=True)
            return

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        _finalize_mutual_cancel(bot, cid, contract, user_id)
        bot.answer_callback_query(call.id, "🚫 لغو معامله تایید شد.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("deny_cancel_"))
    def handle_deny_cancel(call: CallbackQuery):
        cid = call.data.replace("deny_cancel_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract or not contract.get("cancel_requested_by"):
            bot.answer_callback_query(call.id, "⚠️ این درخواست لغو دیگر معتبر نیست.", show_alert=True)
            return

        requester_id = contract.get("cancel_requested_by")
        db.update_contract(cid, {"cancel_requested_by": None})
        db.append_contract_history(cid, "❌ طرف مقابل با درخواست لغو معامله موافقت نکرد؛ معامله فعال باقی ماند.", actor_id=user_id)

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.answer_callback_query(call.id, "درخواست لغو رد شد؛ معامله همچنان فعال است.")
        bot.send_message(call.message.chat.id, f"❌ شما با درخواست لغو معاملهٔ `{cid}` مخالفت کردید. معامله فعال باقی می‌ماند.", parse_mode="Markdown")
        if requester_id:
            try:
                bot.send_message(requester_id, f"❌ طرف مقابل با درخواست لغو معاملهٔ `{cid}` موافقت نکرد. معامله همچنان فعال است.", parse_mode="Markdown")
            except Exception:
                pass

    # ====================================================
    # ۱۳.۵ مدیریت مراحل پرداخت (Milestones) — قبلاً هیچ هندلری برای این دکمه‌ها نبود
    # ====================================================

    @bot.callback_query_handler(func=lambda call: call.data.startswith("manage_milestones_"))
    def handle_manage_milestones(call: CallbackQuery):
        cid = call.data.replace("manage_milestones_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if not milestones:
            bot.answer_callback_query(call.id, "این معامله فاقد مراحل پرداخت است.", show_alert=True)
            return

        is_employer = contract.get("buyer_id") == user_id
        
        # تهیه گزارش وضعیت برای راهنمایی کاربر
        released_count = sum(1 for m in milestones if m.get("status") == "released")
        total_count = len(milestones)
        
        guide_text = ""
        if is_employer:
            guide_text = (
                "💡 **راهنمای کارفرما:**\n"
                "• مراحل **قرمز (💳)**: نیاز به واریز وجه دارند.\n"
                "• مراحل **سبز (🔓)**: وجه نزد میانجی امن است. پس از اطمینان از انجام کار، دکمه آزادسازی را بزنید.\n"
                "• مراحل **تیک‌دار (✅)**: تسویه شده و به مجری پرداخت شده‌اند.\n\n"
            )
        else:
            guide_text = (
                "💡 **راهنمای مجری:**\n"
                "• مراحل **قرمز (⏳)**: در انتظار واریز وجه توسط کارفرما هستند.\n"
                "• مراحل **سبز (🟢)**: وجه نزد میانجی تایید شده. می‌توانید کار این مرحله را انجام دهید.\n"
                "• مراحل **تیک‌دار (✅)**: وجه به حساب شما واریز شده است.\n\n"
            )

        text = (
            f"📊 **مدیریت مراحل معامله `{cid}`**\n"
            f"📈 پیشرفت: `{released_count}/{total_count}` مرحله تکمیل شده\n"
            "──────────────────\n"
            f"{guide_text}"
            "لطفاً مرحله مورد نظر را انتخاب کنید:"
        )

        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            text,
            parse_mode="Markdown",
            reply_markup=kb.get_milestones_inline(cid, milestones, is_employer, contract.get("commission_payer", "freelancer"))
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("ms_detail_"))
    def handle_milestone_detail(call: CallbackQuery):
        payload = call.data.replace("ms_detail_", "", 1)
        try:
            cid, idx_str = payload.rsplit("_", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones):
            bot.answer_callback_query(call.id, "❌ مرحله یافت نشد.", show_alert=True)
            return

        ms = milestones[idx]
        title = ms.get("title")
        amount = float(ms.get("amount", 0))
        status = ms.get("status", "pending")
        
        status_map = {
            "pending": "⏳ غیرفعال (در انتظار نوبت)",
            "awaiting_payment": "🔴 در انتظار واریز وجه",
            "paid": "🟢 واریز شده / آماده انجام",
            "active": "🟢 در حال انجام",
            "in_progress": "🟢 در حال انجام",
            "delivered": "📦 تحویل داده شده / در انتظار تایید",
            "released": "✅ تسویه شده / پرداخت شده"
        }
        status_desc = status_map.get(status, status)

        text = (
            f"ℹ️ **جزئیات مرحله {idx + 1}**\n"
            f"📌 موضوع: **{title}**\n"
            f"💰 مبلغ: **{utils.format_currency(amount)}**\n"
            f"📊 وضعیت: **{status_desc}**\n"
            "──────────────────\n"
        )
        
        if status == "awaiting_payment":
            text += "💡 این مرحله نیاز به واریز وجه توسط کارفرما دارد تا مجری بتواند کار را شروع کند."
        elif status in ["paid", "active", "in_progress"]:
            text += "💡 وجه این مرحله در صندوق میانجی امن است. مجری می‌تواند کار را انجام دهد."
        elif status == "released":
            text += "✅ این مرحله با موفقیت تسویه شده و مبلغ به کیف پول مجری واریز شده است."
        
        markup = InlineKeyboardMarkup()
        # اگر فایلی تحویل داده شده، دکمه مشاهده فایل
        if ms.get("delivery_files"):
            markup.add(InlineKeyboardButton("👁 مشاهده فایل‌های تحویلی این مرحله", callback_data=f"ms_files_{cid}_{idx}"))
        
        markup.add(InlineKeyboardButton("⬅️ بازگشت به لیست مراحل", callback_data=f"manage_milestones_{cid}"))
        
        bot.answer_callback_query(call.id)
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("release_ms_"))
    def handle_release_milestone(call: CallbackQuery):
        user_id = call.from_user.id
        payload = call.data.replace("release_ms_", "", 1)
        try:
            cid, idx_str = payload.rsplit("_", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "❌ درخواست نامعتبر.", show_alert=True)
            return

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        if contract.get("buyer_id") != user_id:
            bot.answer_callback_query(call.id, "❌ فقط کارفرما می‌تواند مرحله را آزاد کند.", show_alert=True)
            return

        milestones = contract.get("milestones") or []
        if idx < 0 or idx >= len(milestones):
            bot.answer_callback_query(call.id, "❌ مرحله یافت نشد.", show_alert=True)
            return

        if milestones[idx].get("status") == "released":
            bot.answer_callback_query(call.id, "این مرحله قبلاً آزاد شده است.", show_alert=True)
            return

        milestones[idx]["status"] = "released"
        db.update_contract(cid, {"milestones": milestones})

        ms_amount = float(milestones[idx].get("amount", 0))
        comm, net, employer_pays = utils.calculate_commission(ms_amount, payer=contract.get("commission_payer", "freelancer"))
        seller_id = contract.get("seller_id")
        if seller_id:
            db.update_wallet_balance(
                seller_id, net, "milestone_release",
                f"آزادسازی مرحله «{milestones[idx].get('title', '')}» از معامله {cid}"
            )
            utils.credit_ambassador_commission(bot, contract, comm, cid)

        bot.answer_callback_query(call.id, "✅ این مرحله آزاد و مبلغ به کیف پول مجری واریز شد.")
        try:
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=kb.get_milestones_inline(cid, milestones, True, contract.get("commission_payer", "freelancer"))
            )
        except Exception:
            pass

        notify_other_party(
            bot, contract, user_id,
            f"🔓 مرحله «{milestones[idx].get('title', '')}» از معامله `{cid}` آزاد شد.\n"
            f"مبلغ **{utils.format_currency(net)}** به کیف پول شما واریز گردید."
        )

    # ====================================================
    # ۱۳.۶ درخواست داوری توسط کاربر — قبلاً تابعش در دیتابیس بود ولی هیچ دکمه‌ای وصل نبود
    # ====================================================

    @bot.callback_query_handler(func=lambda call: call.data == "request_dispute")
    def handle_dispute_request_start(call: CallbackQuery):
        user_id = call.from_user.id
        db.set_user_state(user_id, "WAITING_DISPUTE_CID")
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "⚖️ **درخواست داوری**\n\n"
            "شناسه معامله را ارسال کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    # بخش جدید: دکمه «ثبت اعتراض و درخواست داوری» که مستقیماً زیر تحویلی پروژه
    # نمایش داده می‌شود (کد معامله را از قبل می‌داند و نیازی به تایپ آن نیست).
    # این دکمه قبلاً در keyboards.py تولید می‌شد ولی هیچ هندلری برایش وجود
    # نداشت و کاربر فقط پیام «به‌زودی فعال می‌شود» می‌دید.
    @bot.callback_query_handler(func=lambda call: call.data.startswith("open_dispute_"))
    def handle_open_dispute_button(call: CallbackQuery):
        cid = call.data.replace("open_dispute_", "", 1)
        user_id = call.from_user.id
        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله یافت نشد.", show_alert=True)
            return

        if contract.get("buyer_id") != user_id and contract.get("seller_id") != user_id:
            bot.answer_callback_query(call.id, "❌ شما یکی از طرفین این معامله نیستید.", show_alert=True)
            return

        terminal_statuses = ["completed", "cancelled", "disputed", "in_dispute", "resolved_employer", "resolved_freelancer", "resolved_split"]
        if contract.get("status") in terminal_statuses:
            bot.answer_callback_query(call.id, "⚠️ این معامله در وضعیت نهایی یا در حال داوری است.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_DISPUTE_REASON", {"dispute_cid": cid})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"⚖️ لطفاً شرح کامل اختلاف/اعتراض خود برای معامله `{cid}` را بنویسید:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_DISPUTE_CID")
    def handle_dispute_cid(message: Message):
        user_id = message.from_user.id
        cid = message.text.strip()
        contract = db.get_contract(cid)

        if not contract:
            bot.send_message(message.chat.id, "❌ معامله‌ای با این شناسه یافت نشد. لطفاً دوباره بررسی و ارسال کنید.")
            return

        if contract.get("buyer_id") != user_id and contract.get("seller_id") != user_id:
            bot.send_message(message.chat.id, "❌ شما یکی از طرفین این معامله نیستید.")
            db.clear_user_state(user_id)
            return

        terminal_statuses = ["completed", "cancelled", "disputed", "in_dispute", "resolved_employer", "resolved_freelancer", "resolved_split"]
        if contract.get("status") in terminal_statuses:
            bot.send_message(message.chat.id, "⚠️ این معامله در وضعیت نهایی یا در حال داوری است.")
            db.clear_user_state(user_id)
            return

        db.set_user_state(user_id, "WAITING_DISPUTE_REASON", {"dispute_cid": cid})
        bot.send_message(
            message.chat.id,
            "📝 لطفاً شرح کامل اختلاف و درخواست خود را بنویسید:",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_DISPUTE_REASON")
    def handle_dispute_reason(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("dispute_cid") if isinstance(data, dict) else None
        
        if not cid or not message.text:
            bot.send_message(message.chat.id, "⚠️ لطفاً شرح اعتراض را به‌صورت متنی بنویسید:")
            return

        reason = message.text.strip()
        db.set_user_state(user_id, "WAITING_DISPUTE_PROOF", {"dispute_cid": cid, "reason": reason})
        
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add("⏩ رد کردن و ثبت نهایی")
        markup.add("❌ انصراف")
        
        bot.send_message(
            message.chat.id,
            "📎 در صورت داشتن مدارک (تصویر اسکرین‌شات یا فایل)، آن را ارسال کنید. در غیر این صورت روی دکمه «رد کردن» کلیک کنید:",
            reply_markup=markup
        )

    @bot.message_handler(content_types=['text', 'photo', 'document'], func=lambda msg: getattr(msg, "user_state", None) == "WAITING_DISPUTE_PROOF")
    def handle_dispute_proof(message: Message):
        user_id = message.from_user.id
        _, data = db.get_user_state(user_id)
        cid = data.get("dispute_cid")
        reason = data.get("reason")
        
        if message.text == "❌ انصراف":
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, "❌ فرآیند داوری لغو شد.", reply_markup=ReplyKeyboardRemove())
            # بازگشت به منوی اصلی
            is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
            bot.send_message(message.chat.id, "🏠 بازگشت به منوی اصلی:", reply_markup=kb.get_main_menu(is_admin))
            return

        proof_file_id = None
        if message.photo:
            proof_file_id = message.photo[-1].file_id
        elif message.document:
            proof_file_id = message.document.file_id

        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        
        ok = db.create_dispute_ticket(cid, user_id, reason)
        db.clear_user_state(user_id)

        if ok:
            bot.send_message(
                message.chat.id,
                f"✅ **درخواست داوری معامله `{cid}` ثبت شد.**\n\nوضعیت به «در حال داوری» تغییر یافت و دکمه‌های تسویه قفل شدند.\n"
                "ادمین به‌زودی اتاق حل اختلاف را ایجاد و لینک آن را برای شما ارسال می‌کند.",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
            # نمایش منوی اصلی
            bot.send_message(message.chat.id, "📌 برای مدیریت معاملات به بخش «معاملات من» مراجعه کنید.", reply_markup=kb.get_main_menu(is_admin))
            
            # ثبت در تاریخچه با مدارک
            if proof_file_id:
                db.append_contract_history(cid, "📎 فایل مدرک داوری توسط کاربر ارسال شد.", actor_id=user_id, file_id=proof_file_id)

            # اطلاع به ادمین
            safe_reason = utils.escape_html(reason)
            admin_text = (
                "⚖️ <b>درخواست داوری جدید (Mianji Room Needed)</b>\n"
                f"📦 معامله: <code>{cid}</code>\n"
                f"👤 شاکی: <code>{user_id}</code>\n"
                f"📝 شرح شکایت: {safe_reason}"
            )
            
            utils.send_admin_alert(
                bot,
                text=admin_text,
                content_type="photo" if message.photo else ("document" if message.document else "text"),
                file_id=proof_file_id,
                reply_markup=kb.get_dispute_admin_inline(cid)
            )
        else:
            bot.send_message(message.chat.id, "❌ خطایی در ثبت درخواست داوری رخ داد.", reply_markup=kb.get_main_menu(is_admin))

    # ====================================================
    # ۱۳.۷ کیف پول: درخواست شارژ و برداشت با تایید ادمین — قبلاً فقط یک Alert «به‌زودی» بود
    # ====================================================

    @bot.callback_query_handler(func=lambda call: call.data == "deposit_wallet")
    def handle_deposit_start(call: CallbackQuery):
        user_id = call.from_user.id
        user_info = db.get_user(user_id)
        
        if not user_info.get("is_verified"):
            bot.answer_callback_query(call.id, "⚠️ برای امنیت تراکنش‌ها، ابتدا باید احراز هویت کنید.", show_alert=True)
            start_kyc_process(call.message.chat.id, user_id, resume_data=call.data)
            return

        db.set_user_state(user_id, "WAITING_DEPOSIT_AMOUNT")
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "💳 لطفاً مبلغی که واریز کرده‌اید (به تومان) را ارسال کنید:",
            reply_markup=kb.get_wallet_amount_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_DEPOSIT_AMOUNT")
    def handle_deposit_amount(message: Message):
        user_id = message.from_user.id
        clean_text = utils.fa_to_en_digits(message.text).replace(",", "").strip()

        if not clean_text.isdigit() or int(clean_text) <= 0:
            bot.send_message(message.chat.id, "⚠️ لطفاً مبلغ را فقط به‌صورت عدد مثبت وارد کنید.")
            return

        amount = float(clean_text)
        db.set_user_state(user_id, "WAITING_DEPOSIT_RECEIPT", {"amount": amount})
        
        bot.send_message(
            message.chat.id,
            f"💳 **دستورالعمل واریز وجه**\n\n"
            f"مبلغ: `{amount:,.0f}` تومان\n\n"
            f"لطفاً مبلغ فوق را به شماره کارت میانجی واریز کرده و **تصویر فیش واریزی** را ارسال کنید:\n\n"
            f"🏧 شماره کارت: `{config.INTERMEDIARY_CARD}`\n"
            f"👤 بنام: میانجی (واسط معتبر معاملات)",
            parse_mode="Markdown",
            reply_markup=kb.get_wallet_amount_cancel_keyboard()
        )

    @bot.message_handler(content_types=['photo'], func=lambda msg: getattr(msg, "user_state", None) == "WAITING_DEPOSIT_RECEIPT")
    def handle_deposit_receipt(message: Message):
        user_id = message.from_user.id
        _, state_data = db.get_user_state(user_id)
        amount = float((state_data or {}).get("amount", 0))
        
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        
        db.clear_user_state(user_id)
        user_info = db.get_user(user_id)
        is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        
        bot.send_message(
            message.chat.id,
            "✅ فیش واریزی شما دریافت شد. پس از بررسی و تایید ادمین، کیف پول شما شارژ خواهد شد.",
            reply_markup=kb.get_main_menu(is_admin_u)
        )
        
        # ثبت تراکنش معلق در جدول transactions
        tx_id = None
        try:
            from database import supabase
            tx_res = supabase.table("transactions").insert({
                "user_id": user_id,
                "amount": amount,
                "type": "deposit",
                "description": "درخواست شارژ کیف پول (در انتظار تایید)",
                "status": "pending",
                "receipt_file_id": photo.file_id
            }).execute()
            if tx_res.data:
                tx_id = tx_res.data[0].get("id")
        except Exception as e:
            logger.error(f"خطا در ثبت تراکنش معلق: {e}")

        # ثبت در جدول متمرکز فیش‌ها
        if tx_id:
            db.create_pending_receipt(
                receipt_type="wallet",
                related_id=str(tx_id),
                user_id=user_id,
                file_id=photo.file_id,
                amount=amount,
                description=f"شارژ کیف پول - تراکنش {tx_id}"
            )
        
        # اطلاع‌رسانی به ادمین یا کانال مدیریت
        admin_markup = kb.get_wallet_admin_approval_inline("deposit", user_id, amount, req_id=tx_id)
        safe_full_name = utils.escape_html(user_info.get('full_name') or 'نامشخص')
        admin_text = (
            f"🏧 <b>درخواست شارژ کیف پول</b> (#{tx_id})\n\n"
            f"👤 کاربر: {safe_full_name} (<code>{user_id}</code>)\n"
            f"💰 مبلغ: {utils.format_currency(amount)}\n"
        )
        
        utils.send_admin_alert(
            bot,
            content_type="photo",
            file_id=photo.file_id,
            text=admin_text + "\n\n📢 #شارژ_حساب",
            reply_markup=admin_markup
        )

    @bot.callback_query_handler(func=lambda call: call.data == "show_transactions")
    def handle_show_transactions(call: CallbackQuery):
        user_id = call.from_user.id
        txs = db.get_user_transactions(user_id)
        if not txs:
            bot.answer_callback_query(call.id, "📭 تراکنشی یافت نشد.", show_alert=True)
            return
            
        bot.answer_callback_query(call.id)
        text = "📜 **تاریخچه ۱۰ تراکنش اخیر شما:**\n\n"
        for tx in txs:
            t_type = tx.get("type", "general")
            amount = tx.get("amount", 0)
            sign = "➕" if amount > 0 else ""
            type_fa = {
                "deposit": "شارژ حساب",
                "withdraw_reserved": "رزرو برداشت",
                "withdraw": "برداشت وجه",
                "contract_payment": "پرداخت معامله",
                "contract_release": "آزادسازی وجه",
                "affiliate_reward": "پورسانت سفیر",
                "withdraw_reserve_rollback": "بازگشت رزرو"
            }.get(t_type, t_type)
            
            created_at = tx.get("created_at", "")[:16].replace("T", " ")
            text += f"🔹 {sign}{utils.format_currency(amount)} | {type_fa}\n"
            text += f"📅 `{created_at}`\n"
            if tx.get("description"):
                text += f"💬 _{tx.get('description')}_\n"
            text += "────────────────────\n"
            
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 بازگشت به کیف پول", callback_data="show_wallet"))
        
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "withdraw_wallet")
    def handle_withdraw_start(call: CallbackQuery):
        user_id = call.from_user.id
        user_info = db.get_user(user_id)
        
        if not user_info.get("is_verified"):
            bot.answer_callback_query(call.id, "⚠️ برای امنیت تراکنش‌ها، ابتدا باید احراز هویت کنید.", show_alert=True)
            start_kyc_process(call.message.chat.id, user_id, resume_data=call.data)
            return

        balance = utils.safe_float(user_info.get("wallet_balance", 0.0)) if user_info else 0.0
        if balance < 10000:
            bot.answer_callback_query(call.id, "⚠️ حداقل مبلغ قابل برداشت ۱۰,۰۰۰ تومان است.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_WITHDRAW_AMOUNT")
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            f"🏧 **درخواست برداشت وجه**\n\n💰 موجودی: `{balance:,.0f}` تومان\n\n"
            "مبلغ موردنظر برای برداشت را وارد کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_wallet_amount_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WITHDRAW_AMOUNT")
    def handle_withdraw_amount(message: Message):
        user_id = message.from_user.id
        raw_text = message.text or ""
        amount = utils.parse_amount_flexible(raw_text)

        logger.info(f"⌨️ [WITHDRAW_INPUT] User: {user_id} | Input: {raw_text} | Parsed: {amount}")

        if amount is None or amount < 10000:
            logger.warning(f"⚠️ [WITHDRAW_INVALID_AMT] User: {user_id} | Input: {raw_text}")
            bot.send_message(message.chat.id, "⚠️ لطفاً مبلغ را به‌صورت عدد معتبر (حداقل ۱۰,۰۰۰) وارد کنید.")
            return

        user = db.get_user(user_id)
        if not user:
            logger.error(f"❌ [WITHDRAW_USER_NOT_FOUND] User: {user_id}")
            bot.send_message(message.chat.id, "❌ خطای سیستمی: کاربر یافت نشد.")
            return

        balance = utils.safe_float(user.get("wallet_balance", 0.0))
        logger.info(f"💰 [WITHDRAW_CHECK] User: {user_id} | Balance: {balance} | Request: {amount}")

        if amount > balance:
            logger.warning(f"⚠️ [WITHDRAW_INSUFFICIENT] User: {user_id} | Balance: {balance} | Request: {amount}")
            bot.send_message(message.chat.id, f"⚠️ موجودی شما ({utils.format_currency(balance)}) کمتر از مبلغ درخواستی است.")
            return

        # نمایش لیست کارت‌ها برای انتخاب
        saved_cards = _get_user_cards(user_id)
        db.set_user_state(user_id, "WAITING_WITHDRAW_METHOD", {"amount": amount})
        
        text = f"💸 مبلغ برداشت: **{utils.format_currency(amount)}**\n\n"
        if not saved_cards:
            text += "⚠️ شما هنوز حساب بانکی ثبت نکرده‌اید. می‌توانید یکی اضافه کنید یا به‌صورت دستی وارد کنید:"
        else:
            text += "حساب مقصد را انتخاب کنید یا حساب جدیدی ثبت کنید:"

        bot.send_message(
            message.chat.id,
            text,
            parse_mode="Markdown",
            reply_markup=kb.get_withdraw_cards_inline(saved_cards)
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("withdraw_use_card_"))
    def handle_withdraw_use_saved_card(call: CallbackQuery):
        user_id = call.from_user.id
        state, state_data = db.get_user_state(user_id)
        
        logger.info(f"🖱️ استفاده از کارت ذخیره شده: user={user_id}, state={state}, data={state_data}")
        
        amount = utils.safe_float((state_data or {}).get("amount", 0))
        if amount <= 0:
            logger.error(f"❌ مبلغ نامعتبر در وضعیت کاربر {user_id}: {amount}")
            bot.answer_callback_query(call.id, "⚠️ خطا در بازیابی مبلغ. لطفاً دوباره تلاش کنید.", show_alert=True)
            return

        try:
            idx = int(call.data.replace("withdraw_use_card_", "", 1))
        except ValueError:
            bot.answer_callback_query(call.id, "❌ خطا", show_alert=True)
            return

        saved_cards = _get_user_cards(user_id)
        if idx >= len(saved_cards):
            bot.answer_callback_query(call.id, "❌ حساب یافت نشد.", show_alert=True)
            return

        card = saved_cards[idx]
        sheba = card.get("sheba")
        card_num = card.get("card_number")
        holder_name = card.get("holder_name") or card.get("bank_name") or "نامشخص"

        # الویت با شبا است، اگر نبود از شماره کارت استفاده می‌شود
        destination = sheba if sheba else card_num
        
        if not destination:
            bot.answer_callback_query(call.id, "❌ این حساب فاقد شماره معتبر است.", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        _do_withdraw(bot, call.message.chat.id, user_id, amount, destination, holder_name)

    @bot.callback_query_handler(func=lambda call: call.data == "withdraw_new_sheba")
    def handle_withdraw_new_sheba_start(call: CallbackQuery):
        user_id = call.from_user.id
        state, state_data = db.get_user_state(user_id)
        
        logger.info(f"🖱️ درخواست شبا دستی: user={user_id}, state={state}, data={state_data}")
        
        amount = utils.safe_float((state_data or {}).get("amount", 0))
        if amount <= 0:
            logger.error(f"❌ مبلغ نامعتبر در وضعیت کاربر {user_id} برای شبا دستی: {amount}")
            bot.answer_callback_query(call.id, "⚠️ خطا در بازیابی مبلغ. لطفاً دوباره تلاش کنید.", show_alert=True)
            return

        db.set_user_state(user_id, "WAITING_WITHDRAW_SHEBA", {"amount": amount})
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "🏦 لطفاً شماره شبای مقصد جهت واریز را ارسال کنید (مثال: `IR820540102680020817909002`):",
            parse_mode="Markdown",
            reply_markup=kb.get_wallet_amount_cancel_keyboard()
        )

    def _do_withdraw(bot_ref, chat_id: int, user_id: int, amount: float, sheba: str, holder_name: str):
        """ثبت نهایی درخواست برداشت و اطلاع‌رسانی"""
        logger.info(f"🔄 شروع فرآیند برداشت برای کاربر {user_id}: مبلغ {amount}, مقصد {sheba}")
        is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        db.clear_user_state(user_id)
        
        req = db.create_withdrawal_request(user_id, amount, sheba, holder_name=holder_name)
        if not req:
            logger.error(f"❌ تابع create_withdrawal_request برای کاربر {user_id} ناموفق بود.")
            bot_ref.send_message(
                chat_id,
                "❌ ثبت درخواست برداشت با خطا مواجه شد (احتمالاً موجودی کافی نیست). لطفاً دوباره تلاش کنید.",
                reply_markup=kb.get_main_menu(is_admin_u)
            )
            return

        req_id = req.get("id")
        logger.info(f"✅ درخواست برداشت با موفقیت ثبت شد. ID: {req_id}")
        bot_ref.send_message(
            chat_id,
            f"✅ درخواست برداشت به مبلغ {utils.format_currency(amount)} ثبت شد.\n"
            "پس از تایید تیم مالی، واریز انجام خواهد شد.",
            reply_markup=kb.get_main_menu(is_admin_u)
        )
        user_info = db.get_user(user_id)
        safe_full_name = utils.escape_html(user_info.get('full_name', 'نامشخص'))
        safe_national_id = utils.escape_html(user_info.get('national_id', '-'))
        safe_sheba = utils.escape_html(sheba)
        safe_holder = utils.escape_html(holder_name)

        admin_text = (
            f"🏧 <b>درخواست جدید برداشت از کیف پول</b> (#{req_id})\n"
            f"👤 نام کاربر: {safe_full_name} (<code>{user_id}</code>)\n"
            f"🆔 کد ملی: <code>{safe_national_id}</code>\n"
            f"💰 مبلغ: {utils.format_currency(amount)}\n"
            f"💳 مقصد (شبا/کارت): <code>{safe_sheba}</code>\n"
            f"👤 نام صاحب حساب: <b>{safe_holder}</b>"
        )
        admin_markup = kb.get_wallet_admin_approval_inline("withdraw", user_id, amount, req_id=req_id)

        # ارسال به کانال مدیریت
        utils.send_admin_alert(
            bot_ref,
            text=admin_text + "\n\n📢 #برداشت_وجه",
            reply_markup=admin_markup
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WITHDRAW_SHEBA")
    def handle_withdraw_sheba(message: Message):
        user_id = message.from_user.id
        is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        state, state_data = db.get_user_state(user_id)
        amount = utils.safe_float((state_data or {}).get("amount", 0))
        
        logger.info(f"⌨️ دریافت شبا/کارت: user={user_id}, amount={amount}, input={message.text}")
        
        if amount <= 0:
            logger.error(f"❌ مبلغ نامعتبر در وضعیت کاربر {user_id} هنگام ورود شبا: {amount}")
            bot.send_message(message.chat.id, "⚠️ خطای سیستمی در بازیابی مبلغ. لطفاً دوباره از ابتدا فرآیند را شروع کنید.", reply_markup=kb.get_main_menu(is_admin_u))
            db.clear_user_state(user_id)
            return

        raw_text = message.text or ""
        clean_input = utils.sanitize_bank_input(raw_text)
        
        is_card = len(clean_input) == 16 and clean_input.isdigit()
        is_sheba = False
        sheba_val = ""

        if clean_input.startswith("IR") and len(clean_input) == 26:
            is_sheba = True
            sheba_val = clean_input
        elif len(clean_input) == 24 and clean_input.isdigit():
            is_sheba = True
            sheba_val = f"IR{clean_input}"

        if is_card:
            if not utils.validate_card_luhn(clean_input):
                bot.send_message(message.chat.id, "❌ **شماره کارت معتبر نیست.**\nلطفاً شماره ۱۶ رقمی صحیح را وارد کنید:")
                return
            destination = clean_input
        elif is_sheba:
            if not utils.validate_iranian_sheba(sheba_val):
                bot.send_message(message.chat.id, "❌ **شماره شبا معتبر نیست.**\nلطفاً شماره ۲۴ رقمی صحیح را وارد کنید:")
                return
            destination = sheba_val
        else:
            bot.send_message(
                message.chat.id,
                "⚠️ **ورودی نامعتبر!**\n\n"
                "لطفاً شماره کارت ۱۶ رقمی یا شماره شبای ۲۴ رقمی مقصد را وارد کنید:"
            )
            return

        # دریافت نام صاحب حساب
        db.set_user_state(user_id, "WAITING_WITHDRAW_HOLDER_NAME", {"amount": amount, "sheba": destination})
        bot.send_message(
            message.chat.id,
            "👤 **نام و نام‌خانوادگی صاحب حساب** (همان‌طور که روی کارت نوشته شده) را وارد کنید:\n"
            "_(مثال: محمد احمدی)_",
            parse_mode="Markdown",
            reply_markup=kb.get_wallet_amount_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_WITHDRAW_HOLDER_NAME")
    def handle_withdraw_holder_name(message: Message):
        user_id = message.from_user.id
        state, state_data = db.get_user_state(user_id)
        amount = utils.safe_float((state_data or {}).get("amount", 0))
        sheba = (state_data or {}).get("sheba", "")
        holder_name = message.text.strip()
        
        logger.info(f"⌨️ دریافت نام صاحب حساب: user={user_id}, amount={amount}, sheba={sheba}, name={holder_name}")

        if amount <= 0 or not sheba:
            logger.error(f"❌ اطلاعات ناقص در وضعیت کاربر {user_id}: amount={amount}, sheba={sheba}")
            is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
            bot.send_message(message.chat.id, "⚠️ اطلاعات ناقص. لطفاً دوباره تلاش کنید.", reply_markup=kb.get_main_menu(is_admin_u))
            db.clear_user_state(user_id)
            return

        parts = holder_name.split()
        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "⚠️ لطفاً نام و فامیل را کامل وارد کنید (مثال: محمد احمدی):"
            )
            return

        _do_withdraw(bot, message.chat.id, user_id, amount, sheba, holder_name)

    # ====================================================
    # ۱۳.۸ پنل سفیران (B2B Affiliate System)
    # ====================================================
    @bot.message_handler(func=lambda msg: msg.text == "💎 پنل سفیران")
    def handle_ambassador_panel(message: Message):
        user_id = message.from_user.id
        _show_ambassador_panel(message.chat.id, user_id)

    def _show_ambassador_panel(chat_id: int, user_id: int, edit_message_id: int = None, terms_just_accepted: bool = False):
        """نمایش پنل سفیران (کمکی برای فراخوانی از هندلرهای مختلف)"""
        db.clear_user_state(user_id)
        user_row = db.get_user(user_id) or {}
        
        # بررسی پذیرش قوانین (اگر همین الان پذیرفته نشده باشد)
        # اگر نقش کاربر سفیر است، یعنی قبلاً پذیرفته است
        is_ambassador = (user_row.get("role") == "ambassador")
        terms_accepted = terms_just_accepted or is_ambassador or bool(user_row.get("is_terms_accepted", False))
        
        if not terms_accepted:
            terms = (
                "⚖️ **قوانین سفیران میانجی**\n"
                "────────────────\n"
                "• **تسویه:** پس از اتمام قطعی معامله\n"
                "• **تقلب:** ممنوعیت تعدد حساب (بن دائم)\n"
                "• **تبلیغات:** ممنوعیت اسپم در گروه‌ها\n"
                "• **حقوقی:** فعالیت بصورت مستقل\n"
                "• **بانکی:** تسویه فقط به کارت همنام\n\n"
                "📖 [مشاهده متن کامل قوانین](https://t.me/mianji_rules)"
            )
            markup = kb.get_ambassador_terms_inline()
            if edit_message_id:
                try:
                    bot.edit_message_text(terms, chat_id, edit_message_id, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)
                except Exception:
                    bot.send_message(chat_id, terms, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)
            else:
                bot.send_message(chat_id, terms, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)
            return

        # اگر کاربر سفیر نیست (اما قوانین را پذیرفته)، او را ثبت می‌کنیم
        if user_row.get("role") != "ambassador":
            db.register_ambassador(user_id)
            user_row["role"] = "ambassador"

        # نمایش داشبورد
        amb_stats = db.get_ambassador(user_id)
        if not amb_stats:
            bot.send_message(chat_id, "❌ خطایی در دریافت اطلاعات سفیر رخ داد.")
            return

        dashboard = (
            f"🚀 **پنل سفیران میانجی**\n"
            f"──────────────────\n"
            f"👤 `{user_row.get('full_name', 'کاربر')}` | 🎖 `{amb_stats.get('tier_level', 'Bronze')}`\n"
            f"💹 **سهم شما:** `{amb_stats.get('commission_rate', 30)}%` از درآمد پلتفرم\n"
            f"──────────────────\n"
            f"👥 **ورودی:** `{amb_stats.get('total_referrals', 0)}` نفر\n"
            f"💰 **سود کل:** `{utils.format_currency(float(amb_stats.get('total_earnings', 0)))}` \n"
            f"💳 **موجودی:** `{utils.format_currency(float(amb_stats.get('withdrawable_balance', 0)))}` \n"
            f"──────────────────\n"
            "💡 لینک خود را منتشر کنید؛ آن‌ها **تخفیف** می‌گیرند و شما **سود** می‌برید."
        )
        
        markup = kb.get_ambassador_dashboard_inline(amb_stats)
        if edit_message_id:
            try:
                bot.edit_message_text(dashboard, chat_id, edit_message_id, parse_mode="Markdown", reply_markup=markup)
            except Exception:
                bot.send_message(chat_id, dashboard, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, dashboard, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("amb_"))
    def handle_ambassador_callbacks(call: CallbackQuery):
        user_id = call.from_user.id
        action = call.data
        
        if action == "amb_dashboard":
            # بازگشت به داشبورد
            _show_ambassador_panel(call.message.chat.id, user_id, edit_message_id=call.message.message_id)
            bot.answer_callback_query(call.id)
            return

        elif action.startswith("amb_stats_"):
            # نمایش جزئیات آمار روی آلرت
            stats = db.get_ambassador(user_id)
            if action == "amb_stats_refs":
                count = db.get_ambassador_referrals_count(user_id)
                bot.answer_callback_query(call.id, f"👥 شما تاکنون {count} نفر را دعوت کرده‌اید.", show_alert=True)
            elif action == "amb_stats_tier":
                tier = stats.get("tier_level", "برنزی")
                bot.answer_callback_query(call.id, f"🏆 سطح فعلی شما: {tier}", show_alert=True)
            elif action == "amb_stats_bal":
                bal = float(stats.get("withdrawable_balance", 0))
                bot.answer_callback_query(call.id, f"💰 درآمد قابل برداشت: {utils.format_currency(bal)}", show_alert=True)
            return

        amb_stats = db.get_ambassador(user_id)
        if not amb_stats:
            bot.answer_callback_query(call.id, "❌ شما دسترسی سفیر ندارید.", show_alert=True)
            return

        if action == "amb_get_link":
            bot_username = getattr(config, "BOT_USERNAME", "MianjiBot")
            link = utils.generate_ambassador_link(bot_username, user_id)
            bot.send_message(
                call.message.chat.id,
                f"🔗 **لینک اختصاصی شما:**\n\n`{link}`\n\n"
                "این لینک را در کانال، بیو یا دایرکت برای هنرجویان خود بفرستید.\n"
                "هر کاربری که با این لینک وارد شود، برای همیشه زیرمجموعه شما شده و از معاملات او پورسانت دریافت می‌کنید.",
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id)

        elif action == "amb_promo_pack":
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                                          reply_markup=kb.get_ambassador_promo_inline())
            bot.answer_callback_query(call.id)

        elif action == "amb_promo_banner":
            bot.answer_callback_query(call.id, "⏳ در حال تولید بنر اختصاصی...")
            banner_bytes = utils.generate_promo_banner(user_id, call.from_user.first_name)
            bot.send_photo(
                call.message.chat.id,
                banner_bytes,
                caption="🖼 **بنر تبلیغاتی اختصاصی شما**\n\nاین بنر را در کانال خود منتشر کنید.",
                parse_mode="Markdown"
            )

        elif action == "amb_promo_post":
            bot_username = getattr(config, "BOT_USERNAME", "MianjiBot")
            link = utils.generate_ambassador_link(bot_username, user_id)
            post_text = (
                "🤝 **انجام معاملات امن و تخصصی در میانجی**\n\n"
                "اگر قصد خرید دوره، اکانت یا سفارش پروژه دارید، برای امنیت ۱۰۰٪ پرداخت خود از ربات میانجی استفاده کنید.\n"
                "مبلغ شما تا تایید نهایی نزد میانجی امانت می‌ماند.\n\n"
                "🎁 **هدیه ویژه:** با ورود از لینک زیر، **۱۰٪ تخفیف** در کارمزد معامله هدیه بگیرید."
            )
            bot.send_message(
                call.message.chat.id,
                post_text,
                parse_mode="Markdown",
                reply_markup=kb.get_ambassador_channel_post_inline(link)
            )
            bot.answer_callback_query(call.id)

        elif action == "amb_cashout":
            balance = float(amb_stats.get("withdrawable_balance", 0))
            text = (
                f"🏧 **درخواست تسویه درآمد**\n\n"
                f"موجودی فعلی: **{utils.format_currency(balance)}**\n"
                f"حداقل مبلغ تسویه: ۵۰،۰۰۰ تومان\n\n"
                "در صورت تایید، درخواست شما برای واحد مالی ارسال شده و طی ۲۴ ساعت به حساب شما واریز می‌شود."
            )
            bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, 
                                  parse_mode="Markdown", reply_markup=kb.get_ambassador_cashout_inline(balance))
            bot.answer_callback_query(call.id)

        elif action == "amb_cashout_confirm":
            balance = float(amb_stats.get("withdrawable_balance", 0))
            if balance < 50000:
                bot.answer_callback_query(call.id, "❌ موجودی کافی نیست.", show_alert=True)
                return
            
            # ثبت وضعیت منتظر دریافت اطلاعات بانکی
            db.set_user_state(user_id, "WAITING_AMBASSADOR_CASHOUT_INFO")
            bot.send_message(
                call.message.chat.id,
                "🏦 لطفاً **شماره شبا** یا **شماره کارت** خود را به همراه نام صاحب حساب جهت واریز ارسال کنید:",
                reply_markup=kb.get_cancel_keyboard()
            )
            bot.answer_callback_query(call.id)

        elif action == "amb_tier_info":
            info = (
                "🏆 **سطوح و مزایای سفیران میانجی**\n"
                "────────────────────\n"
                "🥉 **سطح برنزی (Bronze):**\n"
                "• پورسانت: ۳۰٪ از کارمزد\n"
                "• تسویه: ۲۴ ساعته\n\n"
                "🥈 **سطح نقره‌ای (Silver):**\n"
                "• پورسانت: ۴۰٪ از کارمزد\n"
                "• شرط: بیش از ۱۰ زیرمجموعه فعال\n"
                "• تسویه: ۶ ساعته\n\n"
                "🥇 **سطح طلایی (Gold):**\n"
                "• پورسانت: ۵۰٪ از کارمزد\n"
                "• شرط: بیش از ۵۰ زیرمجموعه فعال\n"
                "• تسویه: آنی + مدیر حساب اختصاصی\n"
                "────────────────────\n"
                "💡 **نکته:** زیرمجموعه فعال به کاربری گفته می‌شود که حداقل یک معامله موفق در ربات ثبت کرده باشد."
            )
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="amb_dashboard"))
            
            try:
                bot.edit_message_text(info, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
            except Exception:
                bot.send_message(call.message.chat.id, info, parse_mode="Markdown", reply_markup=markup)
            bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_AMBASSADOR_CASHOUT_INFO")
    def handle_amb_cashout_info(message: Message):
        user_id = message.from_user.id
        info = message.text.strip()
        amb_stats = db.get_ambassador(user_id)
        balance = float(amb_stats.get("withdrawable_balance", 0)) if amb_stats else 0
        
        db.clear_user_state(user_id)
        
        if balance < 50000:
            bot.send_message(message.chat.id, "❌ خطا: موجودی شما برای تسویه کافی نیست.")
            return

        # ثبت درخواست برداشت در دیتابیس با استفاده از تابع یکپارچه
        req = db.create_withdrawal_request(user_id, balance, info, "ambassador")
        
        if not req:
            bot.send_message(message.chat.id, "❌ خطا در ثبت درخواست تسویه. لطفاً موجودی خود را بررسی کنید.")
            return

        bot.send_message(
            message.chat.id,
            "✅ درخواست تسویه شما با موفقیت ثبت شد و برای واحد مالی ارسال گردید.\n"
            "پس از واریز، اطلاع‌رسانی خواهد شد.",
            reply_markup=_get_main_menu_for_user(user_id)
        )
        
        # اطلاع به ادمین
        utils.send_admin_alert(
            bot,
            f"🏧 <b>درخواست تسویه حساب سفیر</b>\n\n"
            f"👤 سفیر: <code>{user_id}</code>\n"
            f"💰 مبلغ: <b>{utils.format_currency(balance)}</b>\n"
            f"🏦 اطلاعات: {utils.escape_html(info)}"
        )

    @bot.callback_query_handler(func=lambda call: call.data == "ambassador_terms")
    def handle_ambassador_terms_view(call: CallbackQuery):
        """نمایش شرایط و ضوابط سفیران (ویرایش پیام جاری)"""
        terms = (
            "⚖️ **قوانین سفیران میانجی**\n"
            "────────────────\n"
            "• **تسویه:** پس از اتمام قطعی معامله\n"
            "• **تقلب:** ممنوعیت تعدد حساب (بن دائم)\n"
            "• **تبلیغات:** ممنوعیت اسپم در گروه‌ها\n"
            "• **حقوقی:** فعالیت بصورت مستقل\n"
            "• **بانکی:** تسویه فقط به کارت همنام\n\n"
            "📖 [مشاهده متن کامل قوانین](https://t.me/mianji_rules)"
        )
        bot.edit_message_text(
            terms,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=kb.get_ambassador_terms_inline(),
            disable_web_page_preview=True
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "accept_affiliate_terms")
    def handle_accept_affiliate_terms(call: CallbackQuery):
        """پذیرش شرایط و فعال‌سازی آنی پنل سفیر"""
        user_id = call.from_user.id
        
        # ثبت پذیرش قوانین و عضویت
        if db.accept_affiliate_terms(user_id):
            db.register_ambassador(user_id)
            bot.answer_callback_query(call.id, "✅ عضویت شما تایید شد. خوش آمدید!", show_alert=False)
            
            # ویرایش پیام و نمایش داشبورد (با پرچم پذیرش موفق)
            _show_ambassador_panel(call.message.chat.id, user_id, edit_message_id=call.message.message_id, terms_just_accepted=True)
            
            # اطلاع به ادمین
            try:
                utils.send_admin_alert(
                    bot,
                    f"🤝 <b>عضویت سفیر جدید</b>\n\n"
                    f"👤 کاربر: <code>{user_id}</code>\n"
                    f"نام: {utils.escape_html(call.from_user.full_name)}\n"
                    f"نام کاربری: @{call.from_user.username or 'ندارد'}"
                )
            except:
                pass
        else:
            bot.answer_callback_query(call.id, "❌ متاسفانه خطایی در دیتابیس رخ داد. لطفاً دوباره تلاش کنید.", show_alert=True)
            # نمایش مجدد پنل برای اطمینان
            _show_ambassador_panel(call.message.chat.id, user_id, edit_message_id=call.message.message_id)

    @bot.callback_query_handler(func=lambda call: call.data == "back_to_main_menu")
    def handle_back_to_main_menu(call: CallbackQuery):
        """بازگشت به منوی اصلی با ویرایش پیام"""
        user_id = call.from_user.id
        is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        bot.edit_message_text(
            "🏠 به منوی اصلی بازگشتید.\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb.get_main_menu(is_admin_u)
        )
        bot.answer_callback_query(call.id)

    # ====================================================
    # سیستم ساخت و پخش پست سفیران (Ambassador Channel Broadcaster)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "amb_create_post")
    def handle_amb_create_post_start(call: CallbackQuery):
        """شروع فلوی ساخت پست برای سفیران"""
        user_id = call.from_user.id
        user_row = db.get_user(user_id) or {}
        
        if user_row.get("role") != "ambassador" or user_row.get("ambassador_status") != "approved":
            bot.answer_callback_query(call.id, "❌ تنها سفیران تایید‌شده می‌توانند پست ایجاد کنند.", show_alert=True)
            return
        
        bot.answer_callback_query(call.id)
        db.set_user_state(user_id, "AMB_BROADCASTER_CHANNEL", {})
        bot.send_message(
            call.message.chat.id,
            "📢 **سیستم ساخت و پخش پست سفیران**\n\n"
            "🔹 **مرحله ۱ — کانال مقصد**\n\n"
            "لطفاً آیدی یا شناسهٔ عددی کانالی که ربات دسترسی ارسال پیام در آن دارد را وارد کنید:\n"
            "_(مثال: `-1001234567890` یا `@channel_name`)_",
            parse_mode="Markdown",
            reply_markup=kb.get_broadcaster_channel_input_inline()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "AMB_BROADCASTER_CHANNEL")
    def handle_amb_broadcaster_channel(message: Message):
        """دریافت و تایید کانال مقصد"""
        user_id = message.from_user.id
        channel_input = message.text.strip()
        
        # سعی برای دریافت اطلاعات کانال و تایید دسترسی
        try:
            # اگر به صورت @username بود، به عدد تبدیل کن (اختیاری)
            # برای حالا فقط اعتبارسنجی ساده را انجام بدهیم
            if channel_input.startswith("-"):
                channel_id = int(channel_input)
            elif channel_input.startswith("@"):
                # در این حالت تلگرام خودش راه حل می‌کند
                channel_id = channel_input
            else:
                channel_id = int(channel_input)
            
            state_data = db.get_user_state(user_id)[1]
            state_data["channel_id"] = channel_id
            db.set_user_state(user_id, "AMB_BROADCASTER_CONTENT", state_data)
            
            bot.send_message(
                message.chat.id,
                "✅ کانال ثبت شد.\n\n"
                "🔹 **مرحله ۲ — محتوای پست**\n\n"
                "حالا متن، تصویر، یا ویدئوی موردنظر خود را ارسال کنید:\n"
                "_(توجه: تنها اولین فایل/متن ارسالی مورد قبول است)_",
                parse_mode="Markdown",
                reply_markup=kb.get_broadcaster_content_input_inline()
            )
        except ValueError:
            bot.send_message(
                message.chat.id,
                "❌ آیدی کانال نامعتبر است.\n\n"
                "لطفاً آیدی عددی (مثال: `-1001234567890`) یا نام کانال (مثال: `@channel_name`) را وارد کنید.",
                reply_markup=kb.get_broadcaster_channel_input_inline()
            )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "AMB_BROADCASTER_CONTENT")
    def handle_amb_broadcaster_content(message: Message):
        """دریافت محتوای پست (متن، تصویر، ویدئو)"""
        user_id = message.from_user.id
        state_data = db.get_user_state(user_id)[1]
        
        # ذخیرهٔ اطلاعات محتوا
        if message.text:
            state_data["content_type"] = "text"
            state_data["content"] = message.text
        elif message.photo:
            state_data["content_type"] = "photo"
            state_data["content"] = message.photo[-1].file_id  # بزرگ‌ترین سایز
            state_data["caption"] = message.caption or ""
        elif message.video:
            state_data["content_type"] = "video"
            state_data["content"] = message.video.file_id
            state_data["caption"] = message.caption or ""
        else:
            bot.send_message(
                message.chat.id,
                "❌ تنها متن، تصویر، یا ویدئو پشتیبانی می‌شوند.",
                reply_markup=kb.get_broadcaster_content_input_inline()
            )
            return
        
        db.set_user_state(user_id, "AMB_BROADCASTER_BUTTON_TEXT", state_data)
        bot.send_message(
            message.chat.id,
            "✅ محتوا ثبت شد.\n\n"
            "🔹 **مرحله ۳ — متن دکمهٔ رفرال**\n\n"
            "متن دکمهٔ شیشه‌ای را وارد کنید که کاربران روی آن کلیک می‌کنند:\n"
            "_(پیش‌فرض: `🛡 ثبت معامله امن با میانجی`)_",
            parse_mode="Markdown",
            reply_markup=kb.get_broadcaster_button_text_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data == "amb_use_default_button")
    def handle_amb_use_default_button(call: CallbackQuery):
        """استفاده از متن پیش‌فرض دکمه"""
        user_id = call.from_user.id
        state_data = db.get_user_state(user_id)[1]
        state_data["button_text"] = "🛡 ثبت معامله امن با میانجی"
        
        bot.answer_callback_query(call.id)
        db.set_user_state(user_id, "AMB_BROADCASTER_PREVIEW", state_data)
        
        # نمایش پیش‌نمایش
        _show_broadcaster_preview(bot, call.message.chat.id, user_id, state_data)

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "AMB_BROADCASTER_BUTTON_TEXT")
    def handle_amb_broadcaster_button_text(message: Message):
        """دریافت متن دکمهٔ سفارشی"""
        user_id = message.from_user.id
        button_text = message.text.strip()
        
        if len(button_text) > 64:  # محدودیت تلگرام
            bot.send_message(
                message.chat.id,
                "❌ متن دکمه بیش از حد طولانی است (حداکثر ۶۴ کاراکتر).\n\n"
                "لطفاً متن کوتاه‌تری وارد کنید:",
                reply_markup=kb.get_broadcaster_button_text_inline()
            )
            return
        
        state_data = db.get_user_state(user_id)[1]
        state_data["button_text"] = button_text
        db.set_user_state(user_id, "AMB_BROADCASTER_PREVIEW", state_data)
        
        # نمایش پیش‌نمایش
        _show_broadcaster_preview(bot, message.chat.id, user_id, state_data)

    def _show_broadcaster_preview(bot: TeleBot, chat_id: int, user_id: int, state_data: dict):
        """نمایش پیش‌نمایش پست قبل از ارسال"""
        content_type = state_data.get("content_type")
        bot_username = getattr(config, "BOT_USERNAME", "")
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        button_text = state_data.get("button_text", "🛡 ثبت معامله امن با میانجی")
        
        # ایجاد دکمهٔ رفرال
        inline_markup = InlineKeyboardMarkup()
        inline_markup.add(InlineKeyboardButton(button_text, url=referral_link))
        
        bot.send_message(
            chat_id,
            "🔹 **مرحله ۴ — پیش‌نمایش پست**\n\n"
            "محتوای پست شما به این شکل در کانال منتشر خواهد شد:",
            parse_mode="Markdown"
        )
        
        if content_type == "text":
            bot.send_message(
                chat_id,
                state_data.get("content", ""),
                parse_mode="Markdown",
                reply_markup=inline_markup
            )
        elif content_type == "photo":
            bot.send_photo(
                chat_id,
                state_data.get("content", ""),
                caption=state_data.get("caption", ""),
                parse_mode="Markdown",
                reply_markup=inline_markup
            )
        elif content_type == "video":
            bot.send_video(
                chat_id,
                state_data.get("content", ""),
                caption=state_data.get("caption", ""),
                parse_mode="Markdown",
                reply_markup=inline_markup
            )
        
        bot.send_message(
            chat_id,
            "آیا می‌خواهید این پست را به کانال ارسال کنید؟",
            reply_markup=kb.get_broadcaster_preview_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data == "amb_edit_button_text")
    def handle_amb_edit_button_text(call: CallbackQuery):
        """بازگشت برای ویرایش متن دکمه"""
        user_id = call.from_user.id
        state_data = db.get_user_state(user_id)[1]
        
        db.set_user_state(user_id, "AMB_BROADCASTER_BUTTON_TEXT", state_data)
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "متن دکمهٔ جدید را وارد کنید:",
            reply_markup=kb.get_broadcaster_button_text_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data == "amb_confirm_send")
    def handle_amb_confirm_send(call: CallbackQuery):
        """تایید و ارسال پست به کانال"""
        user_id = call.from_user.id
        state_data = db.get_user_state(user_id)[1]
        
        channel_id = state_data.get("channel_id")
        content_type = state_data.get("content_type")
        bot_username = getattr(config, "BOT_USERNAME", "")
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        button_text = state_data.get("button_text", "🛡 ثبت معامله امن با میانجی")
        
        # ایجاد دکمهٔ رفرال
        inline_markup = InlineKeyboardMarkup()
        inline_markup.add(InlineKeyboardButton(button_text, url=referral_link))
        
        try:
            if content_type == "text":
                bot.send_message(
                    channel_id,
                    state_data.get("content", ""),
                    parse_mode="Markdown",
                    reply_markup=inline_markup
                )
            elif content_type == "photo":
                bot.send_photo(
                    channel_id,
                    state_data.get("content", ""),
                    caption=state_data.get("caption", ""),
                    parse_mode="Markdown",
                    reply_markup=inline_markup
                )
            elif content_type == "video":
                bot.send_video(
                    channel_id,
                    state_data.get("content", ""),
                    caption=state_data.get("caption", ""),
                    parse_mode="Markdown",
                    reply_markup=inline_markup
                )
            
            bot.answer_callback_query(call.id, "✅ پست با موفقیت در کانال منتشر شد!")
            db.clear_user_state(user_id)
            
            bot.send_message(
                call.message.chat.id,
                "✅ **پست به‌صورت موفق در کانال منتشر شد!**\n\n"
                "هرگاه کاربری از طریق دکمه شیشه‌ای معرفی و معاملهٔ موفقی انجام بدهد، "
                f"**{utils.get_ambassador_share_percent():.0f}٪ از کارمزد** به‌صورت خودکار به کیف پول همکاری شما واریز خواهد شد.",
                parse_mode="Markdown",
                reply_markup=kb.get_ambassador_dashboard_inline()
            )
        except Exception as e:
            logger.error(f"خطا در ارسال پست به کانال {channel_id}: {e}")
            bot.answer_callback_query(call.id, f"❌ خطا: {str(e)}", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data == "amb_cancel_broadcast")
    def handle_amb_cancel_broadcast(call: CallbackQuery):
        """انصراف از ساخت پست"""
        user_id = call.from_user.id
        db.clear_user_state(user_id)
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "❌ ساخت پست لغو شد.",
            reply_markup=kb.get_ambassador_dashboard_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data == "amb_back_panel")
    def handle_amb_back_panel(call: CallbackQuery):
        """بازگشت به پنل سفیران"""
        user_id = call.from_user.id
        user_row = db.get_user(user_id) or {}
        
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "🤝 **پنل همکاری سفیران میانجی**",
            parse_mode="Markdown",
            reply_markup=kb.get_ambassador_dashboard_inline()
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith("get_pdf_") or call.data.startswith("pdf_"))
    def handle_download_pdf(call: CallbackQuery):
        if call.data.startswith("get_pdf_"):
            cid = call.data.replace("get_pdf_", "", 1)
        else:
            cid = call.data.replace("pdf_", "", 1)

        contract = db.get_contract(cid)
        if not contract:
            bot.answer_callback_query(call.id, "❌ معامله مورد نظر یافت نشد.", show_alert=True)
            return

        # نمایش پیام وضعیت به کاربر
        bot.answer_callback_query(call.id, "⏳ در حال ساخت سند رسمی PDF...")
        loading_msg = bot.send_message(call.message.chat.id, "⏳ **در حال ساخت سند رسمی PDF...**\nلطفاً چند لحظه شکیبا باشید.", parse_mode="Markdown")

        try:
            # تولید فایل در حافظه (BytesIO)
            user_id = call.from_user.id
            logger.info(f"🚀 [PDF_START] User {user_id} requested PDF for contract {cid}")
            
            # ثبت زمان شروع برای مانیتورینگ
            import time
            start_time = time.time()
            
            pdf_buffer = pdf_generator.build_contract_pdf(contract)
            
            # بررسی صحت خروجی
            pdf_buffer.seek(0, 2)
            buffer_size = pdf_buffer.tell()
            pdf_buffer.seek(0)
            
            generation_time = time.time() - start_time
            logger.info(f"✅ [PDF_SUCCESS] Generated in {generation_time:.2f}s. Size: {buffer_size} bytes")
            
            if buffer_size < 100:
                raise ValueError(f"Generated PDF buffer is too small ({buffer_size} bytes)")

            pdf_buffer.name = f"Miyanji_Contract_{cid}.pdf"

            # ارسال فایل به کاربر
            bot.send_document(
                call.message.chat.id,
                pdf_buffer,
                caption=f"📑 **سند رسمی قرارداد امانی شماره `{cid}`**\nتنظیم‌شده طبق ماده ۱۰ قانون مدنی و قوانین تجارت الکترونیک",
                parse_mode="Markdown"
            )
            
            # بستن بافر پس از ارسال موفق
            # pdf_buffer.close() # تل بات معمولا خودش مدیریت میکند اما برای اطمینان میتوان بست
            
            # حذف پیام در حال بارگذاری
            bot.delete_message(call.message.chat.id, loading_msg.message_id)
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"🛑 [PDF_CRITICAL_ERROR] CID: {cid} | User: {call.from_user.id}\n{error_details}")
            logger.error(f"Critical error in PDF handler for {cid}: {e}", exc_info=True)
            
            # اطلاع‌رسانی خطا به کاربر و ویرایش پیام قبلی
            bot.edit_message_text(
                "❌ **خطایی در تولید فایل PDF رخ داد.**\nلطفاً دقایقی دیگر مجدداً تلاش کنید یا به پشتیبانی اطلاع دهید.",
                call.message.chat.id,
                loading_msg.message_id,
                parse_mode="Markdown"
            )

    # ====================================================
    # ۱۴. کالبک‌های عمومی (Callback Query Handlers)
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "none")
    def handle_noop_callback(call: CallbackQuery):
        bot.answer_callback_query(call.id)

    # ========== حل مشکل Critical: Catch-All Handler ==========
    # این فانکشن فیلتر کننده برای catch-all handler است که:
    # 1. فقط callback‌های کاربر (prefixes کاربر) را معالجه می‌کند
    # 2. اگر callback با prefix ادمین ("adm:") شروع شود، False برمی‌گرداند
    #    تا handler admin بتواند آن را معالجه کند
    # 3. این مشکل را حل می‌کند که "تنها یک handler" از هر callback اجرا می‌شود
    def is_user_callback(call: CallbackQuery) -> bool:
        """
        تعریف تمام callback_data patterns که متعلق به کاربر است.
        اگر callback با هیچ‌کدام شروع نشود، False برگردان تا handlers دیگر امکان اجرا داشته باشند.
        """
        data = call.data
        # تمام prefixes و exact matches کاربری
        user_patterns = [
            "wiz_",         # wizard (ثبت معامله)
            "set_free_edits_",
            "confirm_draft_",
            "cancel_draft",
            "edit_draft_",
            "edit_field_",
            "back_to_preview_",
            "contracts_more_",
            "manage_cards",
            "card_add_new",
            "card_delete_",
            "card_setdefault_",
            "sign_contract_",
            "upload_receipt_",
            "msp_pay_",
            "msp_deliver_",
            "msp_dok_",
            "msp_dno_",
            "msp_addperiod_",
            "msp_end_",
            "reject_project_",
            "faq_",
            "main_menu",
            "back_to_help",
            "show_guide",
            "show_rules",
        ]

        # هرگز callback های ادمین را نقاپ
        if data.startswith("adm:"):
            return False

        # بررسی تمام patterns
        for pattern in user_patterns:
            if pattern.endswith("_"):  # prefix
                if data.startswith(pattern):
                    return True
            else:  # exact match
                if data == pattern:
                    return True
        
        return False

    @bot.callback_query_handler(func=is_user_callback)
    def handle_user_callbacks(call: CallbackQuery):
        data = call.data
        if data == "faq_info":
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                "❓ **سوالات متداول میانجی**\n\n"
                "برای مشاهده پاسخ هر سوال، روی آن کلیک کنید:",
                call.message.chat.id, call.message.message_id,
                reply_markup=kb.get_faq_keyboard()
            )
        elif data.startswith("faq_q"):
            bot.answer_callback_query(call.id)
            q_idx = data.replace("faq_q", "")
            comm_pct = utils.get_commission_percent()
            answers = {
                "1": (
                    "💰 *کارمزد سامانه چقدر است؟*\n\n"
                    f"کارمزد میانجی معادل *{comm_pct:,.2g}%* از مبلغ هر معامله است که صرف ایجاد زیرساخت امن پرداخت، داوری و نگهداری وجه در حساب واسط \(Escrow\) می‌گردد\."
                ),
                "2": (
                    "⏱ *زمان واریز وجه چقدر است؟*\n\n"
                    "تسویه حساب‌ها طبق سیکل‌های پایا و شبای بانک مرکزی انجام می‌شود\. پس از تایید نهایی معامله، وجه در اولین سیکل کاری به حساب بانکی شما واریز خواهد شد\."
                ),
                "3": (
                    "⚖️ *در صورت بروز اختلاف چه می‌شود؟*\n\n"
                    "با ثبت اختلاف، یک «اتاق داوری» اختصاصی ایجاد می‌گردد\. کارشناسان ما با بررسی مستندات و توافق اولیه، رای نهایی و عادلانه صادر می‌کنند\."
                ),
                "4": (
                    "🛡 *امنیت وجه من چگونه تضمین می‌شود؟*\n\n"
                    "وجه پرداختی تا پایان معامله در حساب واسط امن میانجی بلوکه می‌ماند و تنها با تایید خریدار یا رای داور برای مجری آزاد می‌گردد\."
                ),
                "5": (
                    "❌ *امکان لغو یکطرفه معامله وجود دارد؟*\n\n"
                    "خیر؛ برای حفظ حقوق طرفین، لغو قرارداد تنها با *توافق دوطرفه* یا *رای رسمی داور* امکان‌پذیر است\."
                ),
                "6": (
                    "⏳ *اگر مجری پروژه را تحویل ندهد چه می‌شود؟*\n\n"
                    "در صورت احراز عدم تحویل در اتاق داوری، کل وجه واریزی به کیف پول خریدار مسترد خواهد شد\."
                ),
                "7": (
                    "✏️ *مراحل ویرایش و اصلاح فایل‌ها چگونه است؟*\n\n"
                    "تمامی ویرایش‌ها باید بر اساس «توافق اولیه» ثبت شده در متن قرارداد انجام شود\. طرفین موظف به رعایت چهارچوب ثبت‌شده هستند\."
                ),
                "8": (
                    "🆔 *آیا برای استفاده نیاز به احراز هویت است؟*\n\n"
                    "بله؛ جهت جلوگیری از تخلفات مالی و تسویه بانکی، تایید کد ملی و مطابقت آن با شماره کارت بانکی الزامی است\."
                )
            }
            bot.edit_message_text(
                answers.get(q_idx, "موردی یافت نشد\."),
                call.message.chat.id, call.message.message_id,
                parse_mode="MarkdownV2",
                reply_markup=kb.get_faq_back_inline()
            )
        elif data == "back_to_help":
            bot.answer_callback_query(call.id)
            help_text = (
                "🎧 *مرکز پشتیبانی و راهنمای میانجی*\n"
                "──────────────────\n"
                "برای راهنمایی، قوانین یا گفتگو با کارشناسان از پنل زیر استفاده کنید\.\n\n"
                "📍 *پاسخگویی سریع:* ۸ الی ۲۴"
            )
            bot.edit_message_text(help_text, call.message.chat.id, call.message.message_id, parse_mode="MarkdownV2", reply_markup=kb.get_help_center_inline())
        elif data == "main_menu":
            bot.answer_callback_query(call.id)
            user_id = call.from_user.id
            is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
            user_info = db.get_user(user_id)
            is_verified = user_info.get("is_verified", False) if user_info else False
            status_icon = "تایید شده ✅" if is_verified else "تایید نشده ⚠️"
            
            welcome_text = (
                f"سلام {call.from_user.first_name} عزیز! به پنل اصلی بازگشتید 💎\n"
                "──────────────────\n"
                f"📌 وضعیت حساب شما: **{status_icon}**\n\n"
                "✨ **میانجی؛ پلتفرم امن معاملات** ✨"
            )
            bot.edit_message_text(welcome_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb.get_main_menu(is_admin, is_verified))
        else:
            # این حالت دیگر برای دکمه‌های شناخته‌شده رخ نمی‌دهد (همه دکمه‌های واقعی ربات
            # هندلر اختصاصی دارند)؛ فقط یک شبکه ایمنی برای callback_data ناشناخته است.
            bot.answer_callback_query(call.id, "⏳ این بخش به‌زودی فعال می‌شود.", show_alert=False)

    # ====================================================
    # ۱۵. جست‌وجوی سریع معامله با شناسه + پاسخ پیش‌فرض به پیام‌های نامفهوم
    #     (قبلاً اگر متن کاربر با هیچ دکمه یا مرحله‌ای مطابقت نداشت، ربات کاملاً
    #     ساکت می‌ماند و کاربر فکر می‌کرد چیزی خراب شده. این هندلر همیشه باید
    #     آخرین message_handler ثبت‌شده باشد تا هیچ‌کدام از مراحل بالا را قاپ نزند.)
    # ====================================================

    # ====================================================
    # ۸۸. دریافت نام کامل برای اولین بار
    # ====================================================
    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_FULL_NAME")
    def process_full_name(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()

        # اعتبارسنجی ساده: حداقل دو کلمه (نام و فامیل)
        parts = full_name.split()
        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "❌ لطفاً نام و فامیل خود را درست وارد کنید.\n\n"
                "(مثال: محمد احمدی)",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        # ذخیره نام کامل
        db.register_or_update_user(user_id, full_name=full_name)
        db.clear_user_state(user_id)

        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        welcome_text = (
            f"✅ سپاس {full_name}!\n\n"
            "به **سامانه امن واسطه‌گری و ثبت قرارداد میانجی (Miyanji)** خوش آمدید.\n\n"
            "با میانجی می‌توانید معاملات و پروژه‌های خود را با خیال راحت، همراه با امضای قانونی الکترونیک "
            "و ضمانت داوری ثبت و اجرا کنید."
        )
        
        bot.send_message(
            message.chat.id, 
            welcome_text, 
            parse_mode="Markdown", 
            reply_markup=kb.get_main_menu(is_admin)
        )

    # ====================================================
    # ۸۸الف. دکمه احراز هویت در منوی اصلی
    # ====================================================
    def start_kyc_process(chat_id: int, user_id: int, resume_data: str = None):
        """شروع فرآیند احراز هویت گام‌به‌گام"""
        user_info = db.get_user(user_id)
        if not user_info:
            # اگر کاربر در دیتابیس نبود (مثلاً بعد از ریست)، ابتدا ثبتش می‌کنیم
            user_info = db.register_or_update_user(user_id=user_id)
            if not user_info:
                bot.send_message(chat_id, "❌ خطایی در دسترسی به دیتابیس رخ داد. لطفاً دوباره تلاش کنید.")
                return

        # اگر قبلاً تایید شده، نیازی به ادامه نیست
        if user_info.get("is_verified"):
            bot.send_message(chat_id, "✅ حساب شما قبلاً احراز هویت و تایید شده است.")
            return

        # حفظ داده‌های بازگشت (Resume Data) در مراحل مختلف KYC
        if not resume_data:
            _, existing_data = db.get_user_state(user_id)
            if isinstance(existing_data, dict):
                resume_data = existing_data.get("resume_data")

        state_data = {"resume_data": resume_data} if resume_data else {}

        phone = user_info.get("phone_number")
        first_real = user_info.get("first_name_real")
        last_real = user_info.get("last_name_real")
        
        # گام ۱: شماره موبایل
        if not phone:
            db.set_user_state(user_id, "WAITING_KYC_PHONE", state_data)
            bot.send_message(
                chat_id,
                "📱 برای احراز هویت هوشمند، ابتدا باید شماره موبایل خود را تایید کنید.\n\n"
                "لطفاً روی دکمه زیر کلیک کنید تا شماره موبایل شما برای ربات ارسال شود:",
                reply_markup=kb.get_kyc_phone_keyboard()
            )
            return

        # گام ۲: نام و نام خانوادگی حقیقی
        if not first_real or not last_real:
            db.set_user_state(user_id, "WAITING_PROFILE_FULLNAME", {**state_data, "from_kyc": True})
            bot.send_message(
                chat_id,
                "👤 **تکمیل اطلاعات هویتی**\n\n"
                "برای ادامه احراز هویت، لطفاً **نام و نام خانوادگی** حقیقی خود را (مطابق با شناسنامه) وارد کنید:",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        # گام ۳: کد ملی
        db.set_user_state(user_id, "WAITING_KYC_NATIONAL_ID", state_data)
        bot.send_message(
            chat_id,
            f"✅ اطلاعات اولیه تایید شده است.\n\n"
            "🔢 اکنون **کد ملی ۱۰ رقمی** خود را وارد کنید تا استعلام نهایی تطابق (شاهکار) انجام شود:",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_KYC_PHONE", content_types=['contact', 'text'])
    def handle_kyc_contact(message: Message):
        user_id = message.from_user.id
        
        if message.content_type == 'contact':
            if message.contact.user_id != user_id:
                bot.send_message(message.chat.id, "⚠️ لطفاً شماره خودتان را ارسال کنید.")
                return
            phone = message.contact.phone_number
        else:
            # اجازه وارد کردن دستی شماره (برای احتیاط)
            phone = utils.fa_to_en_digits(message.text or "").strip()
            if not phone.startswith("+"):
                if phone.startswith("0"): phone = "+98" + phone[1:]
                elif len(phone) == 10: phone = "+98" + phone
            
            if not re.match(r'^\+?\d{10,15}$', phone):
                bot.send_message(message.chat.id, "❌ شماره وارد شده معتبر نیست. لطفاً از دکمه زیر برای ارسال شماره استفاده کنید.")
                return

        if not phone.startswith("+"):
            phone = "+" + phone
        
        db.register_or_update_user(user_id, phone_number=phone)
        bot.send_message(message.chat.id, "✅ شماره موبایل تایید شد.")
        start_kyc_process(message.chat.id, user_id)

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_KYC_NATIONAL_ID")
    def process_kyc_national_id(message: Message):
        user_id = message.from_user.id
        try:
            national_id = utils.fa_to_en_digits(message.text or "").strip()
            
            if not kyc_service.validate_national_id(national_id):
                bot.send_message(message.chat.id, "❌ کد ملی باید دقیقاً ۱۰ رقم عددی باشد. لطفاً مجدداً وارد کنید:")
                return

            user_info = db.get_user(user_id)
            if not user_info:
                bot.send_message(message.chat.id, "❌ خطای سیستمی: اطلاعات کاربر یافت نشد. لطفاً /start را بزنید.")
                return
                
            phone = user_info.get("phone_number")
            if not phone:
                bot.send_message(message.chat.id, "⚠️ شماره موبایل یافت نشد. به مرحله اول باز می‌گردیم...")
                start_kyc_process(message.chat.id, user_id)
                return
            
            bot.send_message(message.chat.id, "⏳ در حال استعلام از سامانه هوشمند...")
            
            if kyc_service.verify_shahkar(national_id, phone):
                _, final_data = db.get_user_state(user_id)
                resume_data = final_data.get("resume_data") if isinstance(final_data, dict) else None
                
                db.register_or_update_user(user_id, national_id=national_id, is_verified=True)
                db.clear_user_state(user_id)
                
                # تبریک و پایان
                bot.send_message(
                    message.chat.id,
                    "✅ **تبریک! احراز هویت شما با موفقیت انجام شد.**\n\n"
                    "حساب شما اکنون تایید شده است و تمامی محدودیت‌های مالی و معاملاتی رفع گردید.",
                    parse_mode="Markdown",
                    reply_markup=_get_main_menu_for_user(user_id)
                )

                if resume_data:
                    bot.send_message(message.chat.id, "⏳ در حال بازگشت به عملیات قبلی...")
                    resume_user_action(bot, message.chat.id, user_id, resume_data)
            else:
                bot.send_message(
                    message.chat.id,
                    "❌ **عدم تطابق اطلاعات!**\n\n"
                    "کد ملی وارد شده با صاحب این شماره موبایل در سامانه شاهکار مطابقت ندارد.\n"
                    "لطفاً کد ملی صحیح را وارد کنید.",
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Error in process_kyc_national_id: {e}", exc_info=True)
            bot.send_message(message.chat.id, "⚠️ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

    @bot.message_handler(func=lambda msg: any(x in msg.text for x in ["احراز هویت", "KYC", "حساب کاربری"]))
    def handle_identity_verification(message: Message):
        user_id = message.from_user.id
        db.clear_user_state(user_id)
        
        # بررسی بلاک لیست
        if db.is_user_blacklisted(user_id):
            bot.send_message(
                message.chat.id,
                "⛔ شما در لیست سیاه هستید و نمی‌توانید این عملیات را انجام دهید.",
                parse_mode="Markdown"
            )
            return

        user_info = db.get_user(user_id)
        current_name = user_info.get("full_name", "ثبت نشده") if user_info else "ثبت نشده"
        current_id = user_info.get("national_id", "ثبت نشده") if user_info else "ثبت نشده"
        is_verified = user_info.get("is_verified", False) if user_info else False
        status_icon = "✅ تایید شده" if is_verified else "⚠️ تایید نشده"

        edit_text = (
            "🔐 **احراز هویت هوشمند**\n\n"
            f"وضعیت حساب: **{status_icon}**\n"
            f"**نام و فامیل:** {current_name}\n"
            f"**کد ملی:** {current_id}\n\n"
        )
        
        if is_verified:
            edit_text += "💡 حساب شما تایید شده است و نیازی به اقدام مجدد نیست."
        else:
            edit_text += "⚠️ برای انجام تراکنش‌های مالی و واریز وجه، باید احراز هویت خود را تکمیل کنید."

        markup = kb.InlineKeyboardMarkup(row_width=1)
        if not is_verified:
            markup.add(kb.InlineKeyboardButton("🚀 شروع احراز هویت", callback_data="start_kyc"))
        
        markup.add(
            kb.InlineKeyboardButton("✏️ ویرایش نام و فامیل", callback_data="edit_fullname"),
            kb.InlineKeyboardButton("❌ بازگشت", callback_data="back_to_menu")
        )

        bot.send_message(message.chat.id, edit_text, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "start_kyc")
    def start_kyc_callback(call: CallbackQuery):
        bot.answer_callback_query(call.id)
        start_kyc_process(call.message.chat.id, call.from_user.id)

    # ====================================================
    # ۸۸ب. کال‌بک هندلرهای ویرایش احراز هویت
    # ====================================================
    @bot.callback_query_handler(func=lambda call: call.data == "edit_fullname")
    def edit_fullname_callback(call: CallbackQuery):
        user_id = call.from_user.id
        db.set_user_state(user_id, "WAITING_PROFILE_FULLNAME")
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "📝 **نام و نام خانوادگی خود را وارد کنید:**",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "WAITING_PROFILE_FULLNAME")
    def process_profile_fullname(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()
        _, data = db.get_user_state(user_id)
        if not data: data = {}
        
        # اعتبارسنجی: حداقل دو کلمه
        parts = full_name.split()
        if len(parts) < 2:
            bot.send_message(message.chat.id, "❌ لطفاً نام و نام خانوادگی را کامل وارد کنید (حداقل دو کلمه).")
            return

        # ذخیره در دیتابیس
        success = db.update_user_identity(user_id, full_name=full_name)
        
        if success:
            db.clear_user_state(user_id)
            bot.send_message(message.chat.id, f"✅ اطلاعات شما با موفقیت ثبت شد: **{full_name}**", parse_mode="Markdown")
            
            if data.get("from_kyc"):
                # ادامه فرآیند احراز هویت
                start_kyc_process(message.chat.id, user_id)
            elif data.get("redirect_to_deal"):
                # مستقیماً به مرحله انتخاب نقش می‌رویم
                db.set_user_state(user_id, "WAITING_ROLE_SELECTION")
                bot.send_message(
                    message.chat.id,
                    "👤 **لطفاً نقش خود را در این معامله مشخص کنید:**",
                    reply_markup=kb.get_role_keyboard()
                )
            else:
                is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
                bot.send_message(message.chat.id, "به منوی اصلی بازگشتید.", reply_markup=kb.get_main_menu(is_admin))
        else:
            bot.send_message(message.chat.id, "❌ خطایی در ذخیره‌سازی رخ داد. لطفاً دوباره تلاش کنید.")

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "EDITING_FULLNAME")
    def process_edit_fullname(message: Message):
        user_id = message.from_user.id
        full_name = message.text.strip()

        parts = full_name.split()
        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "❌ لطفاً نام و فامیل خود را درست وارد کنید.",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        user_info = db.get_user(user_id)
        db.update_user_identity(user_id, full_name, user_info.get("national_id", "") if user_info else "")
        db.clear_user_state(user_id)

        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        
        bot.send_message(
            message.chat.id,
            f"✅ نام شما با موفقیت بروزرسانی شد: **{full_name}**",
            parse_mode="Markdown",
            reply_markup=kb.get_main_menu(is_admin)
        )

    @bot.callback_query_handler(func=lambda call: call.data == "edit_national_id")
    def edit_national_id_callback(call: CallbackQuery):
        user_id = call.from_user.id
        db.set_user_state(user_id, "EDITING_NATIONAL_ID")
        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "✏️ **کد ملی جدید را وارد کنید:**\n\n"
            "(۱۰ رقم)",
            parse_mode="Markdown",
            reply_markup=kb.get_cancel_keyboard()
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) == "EDITING_NATIONAL_ID")
    def process_edit_national_id(message: Message):
        user_id = message.from_user.id
        national_id = message.text.strip()

        if not national_id.isdigit() or len(national_id) != 10:
            bot.send_message(
                message.chat.id,
                "❌ کد ملی باید ۱۰ رقم باشد.",
                parse_mode="Markdown",
                reply_markup=kb.get_cancel_keyboard()
            )
            return

        user_info = db.get_user(user_id)
        db.update_user_identity(user_id, user_info.get("full_name", "") if user_info else "", national_id)
        db.clear_user_state(user_id)

        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        
        bot.send_message(
            message.chat.id,
            f"✅ کد ملی شما با موفقیت بروزرسانی شد: **{national_id}**",
            parse_mode="Markdown",
            reply_markup=kb.get_main_menu(is_admin)
        )

    @bot.callback_query_handler(func=lambda call: call.data == "back_to_menu")
    def back_to_menu_callback(call: CallbackQuery):
        user_id = call.from_user.id
        is_admin = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        bot.answer_callback_query(call.id)
        db.clear_user_state(user_id)
        bot.send_message(
            call.message.chat.id,
            "👈 بازگشتید به منوی اصلی.",
            parse_mode="Markdown",
            reply_markup=_get_main_menu_for_user(user_id, is_admin)
        )

    # ====================================================
    # Catch-all: فقط زمانی که کاربر در هیچ FSM state فعالی نیست
    # این handler باید آخرین message_handler ثبت شده باشد.
    # ====================================================
    _ACTIVE_FSM_STATES = {
        "WAITING_ROLE_SELECTION", "WAITING_CATEGORY_SELECTION",
        "WAITING_WIZARD_TITLE", "WAITING_WIZARD_AMOUNT",
        "WAITING_WIZARD_DEADLINE_CUSTOM", "WAITING_WIZARD_MS_ASK",
        "WAITING_WIZARD_MS_TEMPLATE", "WAITING_WIZARD_MS_CUSTOM_PCT",
        "WAITING_WIZARD_MS_MANUAL", "WAITING_WIZARD_REC_ASK",
        "WAITING_WIZARD_DESC", "WAITING_PREVIEW_CONFIRM",
        "WAITING_CUSTOM_FREE_EDITS", "WAITING_FIELD_EDIT",
        "WAITING_SIGN_PHONE", "WAITING_WORK_PHONE",
        "WAITING_SECOND_PARTY_FULLNAME", "WAITING_SECOND_PARTY_NATIONAL_ID",
        "WAITING_SECOND_PARTY_PHONE",
        "WAITING_RECEIPT_PHOTO", "WAITING_MS_RECEIPT_PHOTO",
        "WAITING_DELIVERY_FILE", "WAITING_MS_DELIVERY_FILE",
        "WAITING_FULL_NAME", "WAITING_PROFILE_FULLNAME", "WAITING_SIGN_FULLNAME",
        "EDITING_FULLNAME", "EDITING_NATIONAL_ID",
        "WAITING_CARD_NUMBER", "WAITING_CARD_SHEBA", "WAITING_CARD_HOLDER_NAME", "WAITING_CARD_LABEL",
        "WAITING_WALLET_WITHDRAW_SHEBA", "WAITING_WALLET_CONFIRM_SHEBA",
        "WAITING_WITHDRAW_AMOUNT", "WAITING_WITHDRAW_METHOD",
        "WAITING_WITHDRAW_SHEBA", "WAITING_WITHDRAW_HOLDER_NAME",
        "WAITING_DISPUTE_DESCRIPTION", "WAITING_DISPUTE_FILE",
        "WAITING_EDIT_PRICE_AMOUNT",
        "WAITING_AMBASSADOR_CHANNEL",
    }

    # ====================================================
    # دکمه پنل مدیریت برای ادمین‌ها
    # ====================================================
    @bot.message_handler(func=lambda msg: msg.text == "⚡ پنل مدیریت هوشمند")
    def show_admin_panel(message: Message):
        """نمایش پنل مدیریت برای ادمین‌ها"""
        user_id = message.from_user.id
        
        # بررسی ادمین یا مالک بودن
        is_admin_u = (user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))
        is_owner_u = (user_id == getattr(config, 'OWNER_ID', 0))
        
        if not (is_admin_u or is_owner_u):
            bot.send_message(message.chat.id, "❌ شما دسترسی به این بخش ندارید.")
            return
        
        # دریافت آمار سریع
        pending_withdrawals = len(db.get_pending_withdrawal_requests())
        quick_stats = {"withdrawals": pending_withdrawals}
        
        bot.send_message(
            message.chat.id,
            "👨‍💼 **پنل مدیریت میانجی**\n\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=kb.get_admin_panel_keyboard(quick_stats, is_owner_u)
        )

    @bot.message_handler(func=lambda msg: getattr(msg, "user_state", None) not in _ACTIVE_FSM_STATES)
    def handle_fallback_text(message: Message):
        user_id = message.from_user.id
        text = (message.text or "").strip()
        is_admin_u = (user_id == getattr(config, 'OWNER_ID', 0) or user_id == getattr(config, 'ADMIN_ID', 0) or user_id in getattr(config, 'ADMIN_IDS', []))

        # اگر متن دقیقاً شبیه شناسه یک معامله بود (مثل DEV-2508-1234)،
        # مستقیم کارت همان معامله را نشان بده — نیازی به رفتن به «معاملات من» نیست
        candidate = utils.fa_to_en_digits(text).upper()
        if CONTRACT_ID_PATTERN.match(candidate):
            contract = db.get_contract(candidate)
            if contract:
                # بررسی دسترسی: پس از امضای طرفین، فقط ادمین و طرفین قرارداد اجازه دسترسی دارند
                is_both_signed = bool((contract.get("buyer_signed_at") or contract.get("employer_signed_at")) and 
                                      (contract.get("seller_signed_at") or contract.get("freelancer_signed_at")))
                is_party = (user_id in [contract.get("buyer_id"), contract.get("seller_id"), contract.get("creator_id")])
                
                if is_both_signed and not (is_admin_u or is_party):
                    bot.send_message(message.chat.id, "🚫 **محدودیت دسترسی**\n\nاین قرارداد توسط طرفین امضا شده است و شما اجازه دسترسی به محتوای آن را ندارید.", parse_mode="Markdown")
                    return

                cid = contract.get("contract_id") or contract.get("id", candidate)
                role = "employer" if contract.get("buyer_id") == user_id else "freelancer"
                bot.send_message(
                    message.chat.id,
                    utils.generate_contract_text(contract),
                    parse_mode="Markdown",
                    reply_markup=kb.get_contract_action_keyboard(
                        cid, role, contract.get("status", "draft"), bool(contract.get("milestones")), bool(contract.get("staged_payment"))
                    )
                )
                return
            bot.send_message(message.chat.id, "❌ معامله‌ای با این شناسه یافت نشد.")
            return

        bot.send_message(
            message.chat.id,
            "🤔 متوجه پیام شما نشدم.\n"
            "لطفاً از دکمه‌های منوی زیر استفاده کنید، یا اگر شناسه یک معامله را دارید "
            "مستقیم همان را برایم بفرستید (مثال: `DEV-2508-1234`).",
            parse_mode="Markdown",
            reply_markup=_get_main_menu_for_user(user_id, is_admin)
        )
