import logging
import re
import threading
import uuid
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Union, Tuple
from functools import lru_cache
from supabase import create_client, Client
from config import config
import utils
from phone_utils import normalize_phone_number

logger = logging.getLogger("Miyanji_Database")


# ====================================================
# اعتبارسنجی شماره شبا (IBAN ایرانی)
# ====================================================

def validate_iranian_sheba(sheba: str) -> bool:
    """
    اعتبارسنجی کامل شماره شبای ایرانی:
    - فرمت: IR + ۲۴ رقم (مجموعاً ۲۶ کاراکتر)
    - چک‌سام استاندارد IBAN (MOD-97)
    """
    sheba = sheba.strip().upper()
    if not re.fullmatch(r"IR\d{24}", sheba):
        return False
    # محاسبه چک‌سام IBAN: انتقال ۴ کاراکتر اول به آخر و تبدیل حروف به اعداد
    rearranged = sheba[4:] + sheba[:4]
    numeric_str = "".join(
        str(ord(ch) - ord("A") + 10) if ch.isalpha() else ch
        for ch in rearranged
    )
    return int(numeric_str) % 97 == 1

# قفل Thread جهت جلوگیری از Race Condition (فقط برای تغییرات حساس حافظه)
db_lock = threading.Lock()

# Thread pool for non-blocking DB operations (like persisting state)
db_executor = threading.Thread(target=lambda: None) # placeholder to keep imports clean if needed
from concurrent.futures import ThreadPoolExecutor
db_pool = ThreadPoolExecutor(max_workers=30)

# ایجاد کلاینت اتصال به Supabase
supabase: Optional[Client] = None
try:
    if config.SUPABASE_URL and config.SUPABASE_KEY:
        supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        logger.info("اتصال به Supabase با موفقیت برقرار شد.")
    else:
        logger.warning("اطلاعات SUPABASE_URL یا SUPABASE_KEY تنظیم نشده است.")
except Exception as e:
    logger.error(f"خطا در اتصال به Supabase: {e}")
    supabase = None

# حافظه موقت برای مدیریت وضعیت کاربران (FSM State Management)
_user_states: Dict[int, Dict[str, Any]] = {}


@lru_cache(maxsize=1024)
def is_user_blacklisted(user_id: int) -> bool:
    """
    بررسی کنندهٔ بلاک لیست برای یک کاربر (به صورت بهینه و کش شده)
    """
    if not supabase:
        return False
    try:
        # Cache this if possible in the future, but for now just query
        res = supabase.table("users").select("is_blacklisted").eq("id", user_id).execute()
        if res.data and len(res.data) > 0:
            return bool(res.data[0].get("is_blacklisted", False))
        return False
    except Exception as e:
        logger.error(f"خطا در بررسی وضعیت بلاک لیست کاربر {user_id}: {e}")
        return False

# ====================================================
# ۱. مدیریت وضعیت کاربران (FSM State Management)
# ====================================================

def set_user_state(user_id: int, current_state: str, state_data: Optional[Dict[str, Any]] = None) -> None:
    """
    تنظیم وضعیت و داده‌های موقت کاربر (با ذخیره‌سازی غیرهمزمان در دیتابیس).
    """
    data_to_store = dict(state_data) if state_data else {}
    
    # آپدیت سریع حافظه موقت (بدون قفل طولانی)
    with db_lock:
        _user_states[user_id] = {
            "current_state": current_state,
            "state_data": data_to_store
        }
    
    # ذخیره‌سازی در دیتابیس در پس‌زمینه برای جلوگیری از تاخیر در پاسخدهی
    if supabase:
        def _persist():
            try:
                supabase.table("user_states").upsert({
                    "id": user_id,
                    "state": current_state,
                    "data": data_to_store,
                    "updated_at": "now()"
                }).execute()
            except Exception as e:
                logger.error(f"Error persisting user state for {user_id}: {e}")
        
        db_pool.submit(_persist)


def get_user_state(user_id: int) -> tuple:
    """دریافت وضعیت و داده‌های جاری کاربر (با اولویت حافظه موقت)"""
    # خواندن فوق‌سریع از کش بدون نیاز به لاک سنگین در عملیات خواندن
    user_info = _user_states.get(user_id)
    if user_info:
        return user_info.get("current_state"), user_info.get("state_data", {})
    
    # اگر در حافظه نبود، از دیتابیس می‌خوانیم
    if supabase:
        try:
            res = supabase.table("user_states").select("*").eq("id", user_id).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                with db_lock:
                    _user_states[user_id] = {
                        "current_state": row["state"],
                        "state_data": row["data"] or {}
                    }
                return row["state"], row["data"] or {}
            else:
                # ثبت وضعیت پیش‌فرض در کش برای جلوگیری از کوئری‌های مکرر در فیلترها
                with db_lock:
                    _user_states[user_id] = {
                        "current_state": "START",
                        "state_data": {}
                    }
                return "START", {}
        except Exception as e:
            logger.error(f"Error fetching user state for {user_id}: {e}")
            
    return "START", {}


def clear_user_state(user_id: int) -> None:
    """پاک‌سازی وضعیت کاربر (به صورت غیرهمزمان در دیتابیس)"""
    with db_lock:
        if user_id in _user_states:
            del _user_states[user_id]
    
    if supabase:
        def _delete():
            try:
                supabase.table("user_states").delete().eq("id", user_id).execute()
            except Exception as e:
                logger.error(f"Error deleting user state for {user_id}: {e}")
        db_pool.submit(_delete)


# ====================================================
# ۲. مدیریت کاربران، کیف پول و ادمین
# ====================================================

def register_or_update_user(
    user_id: int, 
    username: Optional[str] = "", 
    first_name: Optional[str] = "", 
    invited_by: Optional[int] = None, 
    phone_number: Optional[str] = None,
    full_name: Optional[str] = None,
    role: Optional[str] = "user",
    **kwargs
) -> Dict[str, Any]:
    if not supabase:
        logger.error("Supabase client is not initialized.")
        return {}

    try:
        # جستجو بر اساس آیدی
        res = supabase.table("users").select("*").eq("id", user_id).execute()
        existing_user = res.data[0] if res.data else None

        if existing_user:
            update_data = {}
            if first_name: update_data["full_name"] = first_name # Using full_name instead
            if username: update_data["username"] = username
            if phone_number: update_data["phone_number"] = phone_number
            if full_name: update_data["full_name"] = full_name
            
            # فیلدهای تکمیلی و احراز هویت
            for key in ["is_verified", "national_id", "is_blacklisted", "role", "wallet_balance", "payment_cards", "invited_by", "password_hash"]:
                if key in kwargs:
                    val = kwargs[key]
                    update_data[key] = val
            
            if update_data:
                # فقط اگر فیلدهای حساس تغییر کردند، کش را پاک کن
                sensitive_fields = ["is_verified", "national_id", "is_blacklisted", "role", "wallet_balance", "payment_cards"]
                should_clear_cache = any(field in update_data for field in sensitive_fields)
                
                # بروزرسانی آنی کش جهت جلوگیری از Race Condition
                if user_id in _user_info_cache:
                    _user_info_cache[user_id].update(update_data)
                    _user_info_last_update[user_id] = datetime.now()
                else:
                    # اگر در کش نبود، شیء کامل را با داده‌های جدید بساز و در کش بگذار
                    full_obj = dict(existing_user)
                    full_obj.update(update_data)
                    _user_info_cache[user_id] = full_obj
                    _user_info_last_update[user_id] = datetime.now()

                def _do_update_final():
                    try:
                        supabase.table("users").update(update_data).eq("id", user_id).execute()
                    except Exception as e:
                        logger.error(f"Async user update error: {e}")
                
                db_pool.submit(_do_update_final)
                # آپدیت موقت شیء در حافظه جهت بازگشت سریع
                existing_user.update(update_data)
            
            return existing_user
        else:
            new_user = {
                "id": user_id,
                "username": username or "",
                "full_name": full_name or f"{first_name or 'کاربر'}",
                "phone_number": phone_number,
                "password_hash": kwargs.get("password_hash"),
                "wallet_balance": kwargs.get("wallet_balance", 0.0),
                "invited_by": invited_by if (invited_by and invited_by != user_id) else None,
                "role": role,
                "impersonated_by": None,
                "is_verified": kwargs.get("is_verified", False),
                "national_id": kwargs.get("national_id"),
                "is_blacklisted": kwargs.get("is_blacklisted", False),
                "payment_cards": kwargs.get("payment_cards", "[]")
            }
            res_insert = supabase.table("users").insert(new_user).execute()
            
            if res_insert.data:
                user_data = res_insert.data[0]
                # آپدیت کش پس از درج موفق
                _user_info_cache[user_id] = user_data
                _user_info_last_update[user_id] = datetime.now()
                
                # اگر توسط کسی دعوت شده، آمار سفیر را بروزرسانی کنیم
                if invited_by:
                    update_ambassador_stats(invited_by, referrals_delta=1)
                
                return user_data
            
            if not res_insert.data:
                logger.error(f"Failed to insert user {user_id}. Response: {res_insert}")
            return new_user
    except Exception as e:
        logger.error(f"خطا در ثبت یا بروزرسانی کاربر {user_id}: {e}", exc_info=True)
        return {}

# ====================================================
#  Settings Management
# ====================================================

# حافظه موقت برای تنظیمات سیستمی
_cached_settings: Dict[str, Any] = {}
_last_settings_update = datetime.min

def get_bot_setting(key: str, default: Any = None) -> Any:
    """دریافت تنظیمات سیستمی با مکانیزم کشینگ هوشمند و پیش‌بارگذاری"""
    global _cached_settings, _last_settings_update
    
    # بروزرسانی کش تنظیمات هر ۵ دقیقه یکبار (یا اگر کش خالی بود)
    now = datetime.now()
    if not _cached_settings or (now - _last_settings_update).total_seconds() > 300:
        try:
            if supabase:
                res = supabase.table("bot_settings").select("*").execute()
                if res.data:
                    _cached_settings = {row["key"]: row["value"] for row in res.data}
                    _last_settings_update = now
        except Exception as e:
            logger.error(f"Error pre-loading settings: {e}")

    val = _cached_settings.get(key)
    if val is not None:
        return _smart_convert(val, default)
    return default

def set_bot_setting(key: str, value: Any, **kwargs) -> bool:
    """بروزرسانی یا ثبت تنظیمات سیستمی در Supabase"""
    if not supabase:
        return False
    try:
        # استفاده از upsert برای درج یا بروزرسانی بر اساس کلید اصلی (key)
        # حذف updated_at از پکت ارسالی تا از مقدار DEFAULT now() دیتابیس استفاده شود
        data = {"key": key, "value": value}
        # اگر در آینده فیلدهایی مثل updated_by به جدول اضافه شد، می‌توان اینجا هندل کرد
        supabase.table("bot_settings").upsert(data).execute()
        
        # بروزرسانی کش لوکال
        global _cached_settings
        _cached_settings[key] = value
        get_bot_setting.cache_clear() # پاکسازی کش
        return True
    except Exception as e:
        logger.error(f"Error setting bot setting {key}: {e}")
        return False

def _smart_convert(val: Any, default: Any) -> Any:
    """تبدیل هوشمند مقادیر دریافتی از دیتابیس به نوع داده مورد انتظار"""
    if val is None: return default
    if default is None: return val
    
    try:
        if isinstance(default, bool):
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes", "on")
            return bool(val)
        if isinstance(default, int):
            return int(float(val))
        if isinstance(default, float):
            return float(val)
        return val
    except:
        return val

# ====================================================
# ۲.۵ مدیریت سفیران (Ambassadors)
# ====================================================

def get_ambassador(user_id: int) -> Optional[Dict[str, Any]]:
    """دریافت اطلاعات سفیر از جدول ambassadors"""
    if not supabase: return None
    try:
        res = supabase.table("ambassadors").select("*").eq("telegram_id", user_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error fetching ambassador {user_id}: {e}")
        return None

def register_ambassador(user_id: int, commission_rate: float = 30.0) -> bool:
    """ثبت نام کاربر به عنوان سفیر"""
    if not supabase: return False
    try:
        # ابتدا نقش کاربر را در جدول users تغییر می‌دهیم
        set_user_role(user_id, "ambassador")
        
        # سپس در جدول سفیران ثبت می‌کنیم
        data = {
            "telegram_id": user_id,
            "commission_rate": commission_rate,
            "tier_level": "Bronze"
        }
        supabase.table("ambassadors").upsert(data).execute()
        return True
    except Exception as e:
        logger.error(f"Error registering ambassador {user_id}: {e}")
        return False

def check_affiliate_terms_accepted(user_id: int) -> bool:
    """بررسی پذیرش قوانین توسط کاربر"""
    if not supabase: return False
    try:
        res = supabase.table("users").select("is_terms_accepted").eq("id", user_id).execute()
        if res.data:
            return bool(res.data[0].get("is_terms_accepted", False))
        return False
    except Exception as e:
        logger.error(f"Error checking terms for user {user_id}: {e}")
        return False

def accept_affiliate_terms(user_id: int) -> bool:
    """ثبت پذیرش قوانین توسط کاربر"""
    if not supabase: return False
    try:
        # اطمینان از وجود ردیف کاربر
        ensure_user_exists(user_id)
        
        import datetime
        now = datetime.datetime.now().isoformat()
        res = supabase.table("users").update({
            "is_terms_accepted": True,
            "terms_accepted_at": now
        }).eq("id", user_id).execute()
        
        if res.data:
            return True
        else:
            # تلاش برای آپدیت با استفاده از UPSERT اگر UPDATE جواب نداد
            res = supabase.table("users").upsert({
                "id": user_id,
                "is_terms_accepted": True,
                "terms_accepted_at": now
            }).execute()
            return bool(res.data)
    except Exception as e:
        logger.error(f"Error accepting terms for user {user_id}: {e}")
        return False

def update_ambassador_stats(user_id: int, referrals_delta: int = 0, earnings_delta: float = 0.0, is_withdrawal: bool = False) -> bool:
    """بروزرسانی آمار و موجودی سفیر در جدول ambassadors"""
    if not supabase: return False
    try:
        amb = get_ambassador(user_id)
        if not amb: 
            # اگر کاربر وجود دارد ولی سفیر نیست، ابتدا ثبت‌نامش می‌کنیم
            register_ambassador(user_id)
            amb = get_ambassador(user_id)
            if not amb: return False
        
        new_referrals = int(amb.get("total_referrals", 0) or 0) + referrals_delta
        
        # در زمان برداشت، سود کل نباید کم شود (فقط موجودی قابل برداشت کم می‌شود)
        current_total_earnings = float(amb.get("total_earnings", 0.0) or 0.0)
        new_earnings = current_total_earnings + (earnings_delta if not is_withdrawal else 0.0)
        
        new_balance = float(amb.get("withdrawable_balance", 0.0) or 0.0) + earnings_delta
        
        # تعیین سطح (Tier Level) بر اساس تعداد زیرمجموعه
        tier = "Bronze"
        if new_referrals > 50: tier = "Gold"
        elif new_referrals > 10: tier = "Silver"
        
        updates = {
            "total_referrals": new_referrals,
            "total_earnings": new_earnings,
            "withdrawable_balance": new_balance,
            "tier_level": tier
        }
        
        supabase.table("ambassadors").update(updates).eq("telegram_id", user_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error updating ambassador stats {user_id}: {e}")
        return False

def get_ambassador_referrals_count(user_id: int) -> int:
    """تعداد کاربران دعوت شده توسط یک سفیر"""
    if not supabase: return 0
    try:
        res = supabase.table("users").select("id", count="exact").eq("invited_by", user_id).execute()
        return res.count if res.count is not None else 0
    except Exception as e:
        logger.error(f"Error counting referrals for {user_id}: {e}")
        return 0

def create_commission_log(user_id: int, contract_id: str, trade_amount: float, platform_fee: float, ambassador_share: float) -> bool:
    """ثبت لاگ پورسانت واریزی برای سفیر"""
    if not supabase: return False
    try:
        data = {
            "ambassador_id": user_id,
            "trade_id": contract_id,
            "trade_amount": trade_amount,
            "platform_fee": platform_fee,
            "ambassador_share": ambassador_share
        }
        supabase.table("commission_logs").insert(data).execute()
        return True
    except Exception as e:
        logger.error(f"Error creating commission log: {e}")
        return False



def update_user_phone(user_id: int, phone_number: str) -> bool:
    try:
        register_or_update_user(user_id, phone_number=phone_number)
        return True
    except Exception as e:
        logger.error(f"خطا در بروزرسانی شماره تلفن کاربر {user_id}: {e}")
        return False


def update_user_identity(user_id: int, full_name: str = None, national_id: str = None, first_name_real: str = None, last_name_real: str = None, is_verified: bool = None) -> bool:
    """
    بروزرسانی اطلاعات احراز هویت کاربر (نام کامل، کد ملی، وضعیت تایید).
    """
    if not supabase:
        return False
    try:
        update_data: Dict[str, Any] = {}
        if full_name and full_name.strip():
            update_data["full_name"] = full_name.strip()
        if national_id and national_id.strip():
            update_data["national_id"] = national_id.strip()
        if is_verified is not None:
            update_data["is_verified"] = is_verified

        if not update_data:
            return True

        res = supabase.table("users").update(update_data).eq("id", user_id).execute()
        _clear_user_cache(user_id)
        if not res.data:
            update_data["id"] = user_id
            res = supabase.table("users").upsert(update_data).execute()
            if not res.data:
                logger.warning(f"Identity upsert for user {user_id} returned no data.")
                return False
        return True
    except Exception as e:
        logger.error(f"خطا در بروزرسانی اطلاعات احراز هویت کاربر {user_id}: {e}", exc_info=True)
        return False


# حافظه موقت برای اطلاعات کاربران جهت افزایش سرعت آنی
_user_info_cache: Dict[int, Dict] = {}
_user_info_last_update: Dict[int, datetime] = {}

def _clear_user_cache(user_id: int):
    """پاکسازی حافظه موقت کاربر برای جلوگیری از نمایش اطلاعات قدیمی"""
    global _user_info_cache, _user_info_last_update
    _user_info_cache.pop(user_id, None)
    _user_info_last_update.pop(user_id, None)

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """دریافت اطلاعات کامل کاربر با سیستم کشینگ فوق‌سریع لایه ۲"""
    global _user_info_cache, _user_info_last_update
    
    now = datetime.now()
    if user_id in _user_info_cache:
        cached = _user_info_cache[user_id]
        last_upd = _user_info_last_update.get(user_id, datetime.min)
        if (now - last_upd).total_seconds() < 300:
            return cached

    if not supabase:
        return None
    try:
        res = supabase.table("users").select("*").eq("id", user_id).execute()
        if res.data:
            user_data = res.data[0]
            _user_info_cache[user_id] = user_data
            _user_info_last_update[user_id] = now
            return user_data
        return None
    except Exception as e:
        logger.error(f"Error fetching user {user_id}: {e}")
        return _user_info_cache.get(user_id)


def set_user_role(user_id: int, role: str) -> bool:
    if not supabase: return False
    try:
        supabase.table("users").update({"role": role}).eq("id", user_id).execute()
        _clear_user_cache(user_id)
        return True
    except Exception as e:
        logger.error(f"خطا در تغییر سطح دسترسی کاربر {user_id}: {e}")
        return False


def update_user_fields(user_id: int, fields: Dict[str, Any]) -> bool:
    """بروزرسانی مستقیم و عمومی هر ترکیبی از ستون‌های جدول users (برای فیلدهای پنل سفیران و مشابه)"""
    if not supabase or not fields:
        return False
    try:
        supabase.table("users").update(fields).eq("id", user_id).execute()
        _clear_user_cache(user_id)
        return True
    except Exception as e:
        logger.error(f"خطا در بروزرسانی فیلدهای کاربر {user_id} ({list(fields.keys())}): {e}")
        return False


def set_impersonation(admin_id: int, target_user_id: Optional[int]) -> bool:
    if not supabase: return False
    try:
        supabase.table("users").update({"impersonated_by": target_user_id}).eq("id", admin_id).execute()
        return True
    except Exception as e:
        logger.error(f"خطا در Impersonation برای ادمین {admin_id}: {e}")
        return False


def process_wallet_payment(user_id: int, contract_id: str) -> Tuple[bool, str]:
    """
    فرآیند اتمیک کسر از کیف پول و فعال‌سازی معامله.
    برمی‌گرداند: (موفقیت، پیام خطا یا موفقیت)
    """
    if not supabase:
        return False, "❌ اتصال به دیتابیس برقرار نیست."

    try:
        # ۱. دریافت اطلاعات معامله
        contract = get_contract(contract_id)
        if not contract:
            return False, "❌ معامله یافت نشد."
        
        status = contract.get("status")
        # وضعیت‌های مجاز برای پرداخت (شامل موارد در انتظار تایید یا امضا)
        if status in ["active", "paid", "in_progress", "completed"]:
            return False, "⚠️ این معامله قبلاً پرداخت شده یا در جریان است."

        # ۲. پارس کردن مبلغ معامله و محاسبه مبلغ نهایی با کارمزد
        raw_amount = contract.get("amount", 0)
        try:
            # پاکسازی و تبدیل به عدد صحیح (سانتی‌رایز مبالغ رشته‌ای با کاما)
            amount_str = str(raw_amount).replace(',', '').split('.')[0]
            base_amount = float(amount_str)
        except (ValueError, TypeError):
            return False, "❌ مبلغ معامله نامعتبر است."

        if base_amount <= 0:
            return False, "❌ مبلغ معامله نمی‌تواند صفر یا منفی باشد."

        # محاسبه کارمزد و مبلغ نهایی که کارفرما باید بپردازد
        comm_payer = contract.get("commission_payer", "freelancer")
        _, _, total_to_pay = utils.calculate_commission(base_amount, payer=comm_payer)

        # ۳. دریافت موجودی کاربر
        user = get_user(user_id)
        if not user:
            return False, "❌ کاربر یافت نشد."
        
        user_balance = utils.safe_float(user.get("wallet_balance"), 0.0)

        # ۴. چک کردن موجودی (مقایسه عددی دقیق با مبلغ نهایی شامل کارمزد)
        if user_balance < total_to_pay:
            return False, f"⚠️ موجودی کافی نیست.\nموجودی شما: {utils.format_currency(user_balance)}\nمبلغ مورد نیاز (با احتساب کارمزد): {utils.format_currency(total_to_pay)}"

        # ۵. کسر وجه از کیف پول
        new_balance = user_balance - total_to_pay
        update_wallet = {"wallet_balance": new_balance}
            
        res_wallet = supabase.table("users").update(update_wallet).eq("id", user_id).execute()
        _clear_user_cache(user_id)
        if not res_wallet.data:
            return False, "❌ خطا در کسر از موجودی کیف پول."

        # ۶. بروزرسانی وضعیت معامله به paid / in_escrow
        now = datetime.now(timezone.utc)
        deadline_days = int(contract.get("deadline", 1))
        delivery_deadline = now + timedelta(days=deadline_days)

        # طبق درخواست: وضعیت به paid تغییر می‌کند
        contract_updates = {
            "status": "paid",
            "paid_at": now.isoformat(),
            "delivery_deadline": delivery_deadline.isoformat()
        }
        
        res_contract = supabase.table("contracts").update(contract_updates).eq("contract_id", contract_id).execute()
        
        if not res_contract.data:
            # تلاش برای Rollback دستی موجودی در صورت شکست آپدیت قرارداد
            rollback_data = {"wallet_balance": user_balance}
            supabase.table("users").update(rollback_data).eq("id", user_id).execute()
            return False, "❌ خطا در بروزرسانی وضعیت قرارداد."

        # ۷. ثبت در جدول تراکنش‌ها
        try:
            supabase.table("transactions").insert({
                "user_id": user_id,
                "amount": -float(amount),
                "type": "wallet_payment",
                "description": f"پرداخت قرارداد {contract_id} از کیف پول",
                "status": "approved",
                "created_at": now.isoformat()
            }).execute()
        except Exception as tx_err:
            logger.warning(f"Transaction log failed: {tx_err}")

        # ۸. ثبت در تاریخچه معامله
        append_contract_history(contract_id, f"💰 پرداخت کل مبلغ ({utils.format_currency(amount)}) از کیف پول کارفرما", actor_id=user_id)

        return True, "✅ پرداخت با موفقیت انجام شد."

    except Exception as e:
        logger.exception(f"Critical error in process_wallet_payment: {e}")
        return False, f"❌ خطای بحرانی در فرآیند پرداخت: {str(e)}"


def update_wallet_balance(
    user_id: int, 
    amount_change: float, 
    transaction_type: str = "general", 
    description: str = "",
    admin_document_id: str = None
) -> bool:
    """
    بروزرسانی موجودی کیف پول کاربر با دقت بالا و لاگ‌گذاری شفاف.
    """
    if not supabase:
        logger.error("❌ [WALLET_ERR] supabase client is None")
        return False

    try:
        # اطمینان از عددی بودن مقادیر
        user_id = int(user_id)
        amount_change = round(float(amount_change), 2)
        
        # اطمینان از وجود کاربر
        ensure_user_exists(user_id)
        
        user = get_user(user_id)
        if not user:
            logger.error(f"❌ [WALLET_ERR] user {user_id} not found after ensure_user_exists")
            return False
        
        # دریافت موجودی فعلی
        current_balance = utils.safe_float(user.get("wallet_balance"), 0.0)
        
        # محاسبه موجودی جدید
        new_balance = round(current_balance + amount_change, 2)
        
        logger.info(f"💰 [WALLET_DEBUG] User: {user_id} | Curr: {current_balance} | Change: {amount_change} | New: {new_balance}")
        
        # چک کردن موجودی (با تلرانس اعشاری)
        if new_balance < -0.01:
            logger.warning(f"❌ [INSUFFICIENT_FUNDS] User: {user_id} | Avail: {current_balance} | Req: {amount_change}")
            return False

        # بروزرسانی در دیتابیس
        update_data = {"wallet_balance": new_balance}
            
        try:
            res = supabase.table("users").update(update_data).eq("id", user_id).execute()
            if not res.data:
                logger.error(f"❌ [WALLET_DB_ERR] Update returned no data for user {user_id}. Data sent: {update_data}")
                return False
        except Exception as db_exc:
            logger.error(f"❌ [WALLET_DB_EXC] Update failed for user {user_id}: {db_exc}")
            return False
        
        # ثبت در جدول تراکنش‌ها
        try:
            tx_data = {
                "user_id": user_id,
                "amount": amount_change,
                "type": transaction_type,
                "description": description,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            if admin_document_id:
                tx_data["admin_document_id"] = admin_document_id
                
            supabase.table("transactions").insert(tx_data).execute()
        except Exception as tx_err:
            logger.warning(f"⚠️ [TRANSACTION_LOG_ERR] User {user_id} balance updated but log failed: {tx_err}")

        logger.info(f"✅ [WALLET_OK] User: {user_id} | New Balance: {new_balance}")
        return True

    except Exception as e:
        logger.error(f"❌ [WALLET_CRITICAL_ERR] User {user_id}: {e}", exc_info=True)
        return False

# ====================================================
# ۹. مدیریت شارژ حساب (کارت به کارت)
# ====================================================

def create_deposit_transaction(user_id: int, amount: float) -> Optional[int]:
    """ایجاد یک تراکنش شارژ در وضعیت در انتظار (Pending)"""
    if not supabase: return None
    try:
        # اطمینان از وجود کاربر برای جلوگیری از خطای Foreign Key
        ensure_user_exists(user_id)
        
        data = {
            "user_id": user_id,
            "amount": amount,
            "type": "deposit",
            "status": "pending",
            "description": f"شارژ حساب به مبلغ {utils.format_currency(amount)} تومان"
        }
        res = supabase.table("transactions").insert(data).execute()
        if res.data:
            return res.data[0]["id"]
        return None
    except Exception as e:
        logger.error(f"Error creating deposit transaction for user {user_id}: {e}")
        return None

def update_deposit_receipt(transaction_id: int, file_id: str) -> bool:
    """بروزرسانی تراکنش با آیدی فایل فیش واریزی"""
    if not supabase: return False
    try:
        res = supabase.table("transactions").update({
            "receipt_file_id": file_id
        }).eq("id", transaction_id).execute()
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error updating deposit receipt for transaction {transaction_id}: {e}")
        return False

def approve_deposit_transaction(transaction_id: int) -> bool:
    """تایید تراکنش، افزایش موجودی کاربر و تغییر وضعیت به approved"""
    if not supabase: return False
    try:
        # ۱. دریافت اطلاعات تراکنش
        res = supabase.table("transactions").select("*").eq("id", transaction_id).execute()
        if not res.data: return False
        
        tx = res.data[0]
        if tx["status"] != "pending":
            return False # قبلاً تعیین تکلیف شده
            
        user_id = tx["user_id"]
        amount = float(tx["amount"])
        
        # ۲. افزایش موجودی کاربر
        success = update_wallet_balance(user_id, amount, "deposit_approved", f"تایید شارژ حساب (تراکنش {transaction_id})")
        
        if success:
            # ۳. بروزرسانی وضعیت تراکنش
            supabase.table("transactions").update({
                "status": "approved"
            }).eq("id", transaction_id).execute()
            return True
        return False
    except Exception as e:
        logger.error(f"Error approving deposit transaction {transaction_id}: {e}")
        return False

def reject_deposit_transaction(transaction_id: int, reason: str = None) -> bool:
    """رد تراکنش شارژ حساب"""
    if not supabase: return False
    try:
        update_data = {"status": "rejected"}
        if reason:
            update_data["description"] = f"رد شده: {reason}"
            
        res = supabase.table("transactions").update(update_data).eq("id", transaction_id).execute()
        return bool(res.data)
    except Exception as e:
        logger.error(f"Error rejecting deposit transaction {transaction_id}: {e}")
        return False


def update_affiliate_wallet_balance(
    user_id: int,
    amount_change: float,
    transaction_type: str = "affiliate_general",
    description: str = ""
) -> bool:
    """
    بروزرسانی «کیف پول همکاری» سفیر — متصل به جدول ambassadors.
    """
    return update_ambassador_stats(user_id, earnings_delta=amount_change)


def get_ambassador_dashboard_stats(user_id: int) -> Dict[str, Any]:
    """
    آمار کامل داشبورد سفیر: تعداد کل ورودی‌ها (معرفی‌شدگان)، معاملات فعال،
    معاملات موفق کل و معاملات موفق ماه جاری (برای محاسبه سطح/تیر).
    """
    stats = {
        "referred_count": 0,
        "active_deals": 0,
        "completed_deals_total": 0,
        "completed_deals_this_month": 0,
    }
    if not supabase:
        return stats

    try:
        res = supabase.table("users").select("id").eq("invited_by", user_id).execute()
        referred_ids = [r["id"] for r in (res.data or [])]
        stats["referred_count"] = len(referred_ids)
        if not referred_ids:
            return stats

        seen_contract_ids = set()
        contracts: List[Dict[str, Any]] = []
        for column in ("buyer_id", "seller_id"):
            try:
                res_c = supabase.table("contracts").select("*").in_(column, referred_ids).execute()
                for c in (res_c.data or []):
                    key = c.get("contract_id") or c.get("id")
                    if key not in seen_contract_ids:
                        seen_contract_ids.add(key)
                        contracts.append(c)
            except Exception as e:
                logger.warning(f"خطا در دریافت معاملات معرفی‌شدگان (ستون {column}): {e}")

        active_statuses = getattr(config, "LOCKED_DEAL_STATUSES", []) + [
            "pending_approval", "bargaining", "awaiting_payment", "awaiting_receipt_approval"
        ]
        now = datetime.now(timezone.utc)

        for c in contracts:
            status = c.get("status")
            if status == "completed":
                stats["completed_deals_total"] += 1
                # برای محاسبهٔ سطح ماهانه، رویداد «تایید نهایی» را از سابقه معامله می‌خوانیم
                for entry in (c.get("history") or []):
                    if "تایید نهایی" in str(entry.get("event", "")):
                        try:
                            ts = datetime.fromisoformat(entry["ts"])
                            if ts.year == now.year and ts.month == now.month:
                                stats["completed_deals_this_month"] += 1
                        except Exception:
                            pass
                        break
            elif status in active_statuses:
                stats["active_deals"] += 1

        return stats
    except Exception as e:
        logger.error(f"خطا در محاسبه آمار داشبورد سفیر {ambassador_id}: {e}")
        return stats


# ====================================================
# ۳. مدیریت معاملات و وضعیت‌ها (Transactions / Contracts)
# ====================================================
# وضعیت‌های معتبر پروژه:
# pending_payment, receipt_submitted, in_progress, work_submitted, completed, disputed, cancelled, draft

_BASE_COLUMNS_CONTRACTS = [
    "id", "contract_id", "title", "amount", "description", "deadline", "category", "status",
    "buyer_id", "seller_id", "creator_id", "buyer_phone", "seller_phone", "buyer_alt_phone", "seller_alt_phone",
    "buyer_fullname", "seller_fullname", "buyer_national_id", "seller_national_id",
    "buyer_signed_at", "seller_signed_at", "buyer_otp_verified", "seller_otp_verified",
    "buyer_otp_code", "seller_otp_code", "buyer_ip", "seller_ip", "milestones", "staged_payment", "recurring", "history", "created_at"
]

def _enrich_contract(c: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not c: return c
    c.setdefault("deadline", 1)
    c.setdefault("description", c.get("title", ""))
    c.setdefault("milestones", [])
    c.setdefault("history", [])
    c.setdefault("delivery_files", [])
    c.setdefault("buyer_otp_verified", False)
    c.setdefault("seller_otp_verified", False)
    c.setdefault("staged_payment", False)
    c.setdefault("recurring", False)
    c.setdefault("commission_payer", "freelancer")
    return c

def create_contract(
    creator_id: Union[int, Dict[str, Any]], 
    role: Optional[str] = None, 
    title: Optional[str] = None, 
    amount: Optional[float] = None, 
    description: Optional[str] = None,
    contract_data: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    if not supabase:
        logger.error("کلاینت Supabase مقداردهی نشده است.")
        return None

    try:
        if isinstance(creator_id, dict):
            contract_data = creator_id
            creator_id = contract_data.get("creator_id") or contract_data.get("created_by")

        if contract_data:
            contract_data = contract_data.copy()
        else:
            contract_data = {
                "title": title,
                "amount": amount,
                "description": description,
                "buyer_id": creator_id if role == "employer" else None,
                "seller_id": creator_id if role == "freelancer" else None,
                "status": "pending_payment",
                "creator_id": creator_id
            }

        title_val = str(contract_data.get("title", "بدون عنوان"))
        category = str(contract_data.get("category", "GEN"))
        cid = str(contract_data.get("contract_id") or contract_data.get("id") or utils.generate_archive_contract_id(category))
        system_id = str(uuid.uuid4())

        raw_deadline = contract_data.get("deadline", 1)
        try:
            deadline_val = int(float(utils.fa_to_en_digits(str(raw_deadline))))
        except:
            deadline_val = 1

        raw_amount = contract_data.get("amount")
        amt = 0.0
        if raw_amount is not None:
            try:
                if isinstance(raw_amount, (int, float)):
                    amt = float(raw_amount)
                else:
                    amt_str = str(raw_amount).replace(",", "").strip()
                    amt = float(utils.fa_to_en_digits(amt_str))
            except:
                amt = 0.0

        buyer_val = contract_data.get("buyer_id")
        seller_val = contract_data.get("seller_id")
        creator_val = contract_data.get("creator_id")

        for uid in [buyer_val, seller_val, creator_val]:
            if uid:
                try:
                    ensure_user_exists(int(uid))
                except:
                    pass

        full_payload = {
            "id": system_id,
            "contract_id": cid,
            "title": title_val,
            "amount": amt,
            "description": str(contract_data.get("description", "")),
            "deadline": deadline_val,
            "category": category,
            "status": str(contract_data.get("status", "pending_payment")),
            "buyer_id": int(buyer_val) if buyer_val else None,
            "seller_id": int(seller_val) if seller_val else None,
            "creator_id": int(creator_val) if creator_val else None
        }
        
        for f in _BASE_COLUMNS_CONTRACTS:
            if f in contract_data and f not in full_payload:
                full_payload[f] = contract_data[f]

        # Try insert full payload; if columns don't exist, fallback to core columns only
        try:
            res = supabase.table("contracts").insert(full_payload).execute()
            if res.data: return _enrich_contract(res.data[0])
        except Exception as e1:
            err_msg = str(e1).lower()
            if "duplicate" in err_msg or "23505" in err_msg:
                existing = get_contract(cid)
                if existing: return _enrich_contract(existing)
            
            logger.warning(f"⚠️ [DB_CREATE_FALLBACK] Full insert failed ({e1}), retrying with core columns...")
            try:
                core_payload = {
                    "id": system_id,
                    "contract_id": cid,
                    "title": title_val,
                    "amount": amt,
                    "status": str(contract_data.get("status", "pending_payment")),
                    "category": category,
                    "buyer_id": int(buyer_val) if buyer_val else None,
                    "seller_id": int(seller_val) if seller_val else None,
                    "milestones": contract_data.get("milestones", []),
                    "history": contract_data.get("history", [])
                }
                res = supabase.table("contracts").insert(core_payload).execute()
                if res.data: return _enrich_contract(res.data[0])
            except Exception as e2:
                logger.error(f"🚨 [DB_CREATE_ERR_FINAL] Contract {cid} failed: {e2}", exc_info=True)
                raise e2

        return None

    except Exception as e:
        logger.error(f"خطای بحرانی در create_contract: {e}", exc_info=True)
        return None

def ensure_user_exists(user_id: int) -> bool:
    """اطمینان از وجود کاربر در جدول users (اگر نباشد، یک رکورد خام ایجاد می‌کند)"""
    if not supabase: return False
    try:
        res = supabase.table("users").select("id").eq("id", user_id).execute()
        if res.data and len(res.data) > 0:
            return True
            
        fallback_phone = f"98900{abs(user_id) % 100000000:08d}"
        supabase.table("users").upsert({
            "id": user_id,
            "phone_number": fallback_phone,
            "full_name": "کاربر میانجی",
            "role": "user"
        }, on_conflict="id").execute()
        return True
    except Exception as e:
        logger.warning(f"Error in ensure_user_exists for {user_id}: {e}")
        return False


def get_contract(contract_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    try:
        contract = None
        try:
            res = supabase.table("contracts").select("*").eq("contract_id", contract_id).execute()
            if res.data:
                contract = res.data[0]
        except Exception:
            pass

        if not contract:
            res_alt = supabase.table("contracts").select("*").eq("id", contract_id).execute()
            contract = res_alt.data[0] if res_alt.data else None
            
        return _enrich_contract(contract)

    except Exception as e:
        logger.error(f"خطا در دریافت معامله {contract_id}: {e}")
        return None


# لیست مرجع ستون‌های معتبر جدول قراردادها جهت جلوگیری از خطا در تراکنش‌ها
_BASE_COLUMNS_CONTRACTS = {
    "id", "contract_id", "title", "amount", "description", "deadline", "category", "status",
    "buyer_id", "seller_id", "creator_id", "staged_payment", "milestones", "history", 
    "delivery_files", "receipt_file_id", "payment_verified", "late_penalty_per_day", 
    "delivery_deadline", "recurring", "free_edits_total", "free_edits_left", 
    "buyer_otp_verified", "seller_otp_verified", "buyer_signed_at", "seller_signed_at", 
    "buyer_otp_code", "seller_otp_code",
    "buyer_fullname", "seller_fullname", "buyer_national_id", "seller_national_id",
    "buyer_phone", "seller_phone", "buyer_alt_phone", "seller_alt_phone",
    "paid_at", "delivered_at", "completed_at",
    "signed_by_second_party", "project_file_id", "created_by_admin", 
    "dispute_reason", "dispute_opened_by", "cancel_requested_by",
    "dispute_room_link", "dispute_verdict", "negotiation_text", "commission_payer"
}

def update_contract(
    contract_id: str, 
    updates: Union[Dict[str, Any], str], 
    extra_data: Optional[Dict[str, Any]] = None
) -> bool:
    if not supabase:
        return False

    if isinstance(updates, dict):
        payload = updates.copy()
    else:
        payload = {"status": updates}
        if extra_data:
            payload.update(extra_data)

    def _apply(pl: Dict[str, Any]) -> bool:
        try:
            clean_pl = {k: v for k, v in pl.items() if v is not None}
            res = supabase.table("contracts").update(clean_pl).eq("contract_id", contract_id).execute()
            if res.data: return True
        except Exception as e:
            logger.warning(f"⚠️ [DB_UPDATE_FALLBACK] Update with all columns failed ({e}), retrying with core columns...")
            try:
                core_keys = {"contract_id", "title", "amount", "buyer_id", "seller_id", "status", "category", "milestones", "history", "delivery_files", "paid_at"}
                core_pl = {k: v for k, v in clean_pl.items() if k in core_keys}
                if core_pl:
                    res = supabase.table("contracts").update(core_pl).eq("contract_id", contract_id).execute()
                    if res.data: return True
            except Exception as e_core:
                logger.error(f"⚠️ [DB_UPDATE_ERR_CORE] Contract {contract_id} core update failed: {e_core}")

        try:
            res = supabase.table("contracts").update(clean_pl).eq("id", contract_id).execute()
            if res.data:
                return True
            else:
                return False
        except Exception as e:
            logger.error(f"⚠️ [DB_UPDATE_ERR_FINAL] Contract {contract_id} update failed: {e}")
            return False

    try:
        safe_payload = {k: v for k, v in payload.items() if k in _BASE_COLUMNS_CONTRACTS}
        if not safe_payload:
            safe_payload = payload
            
        success = _apply(safe_payload)
        return success

    except Exception as e:
        logger.error(f"خطای بحرانی در بروزرسانی قرارداد {contract_id}: {e}", exc_info=True)
        return False

update_contract_status = update_contract


@lru_cache(maxsize=128)
def get_user_contracts(user_id: int) -> List[Dict[str, Any]]:
    if not supabase:
        return []
    try:
        res_buyer = supabase.table("contracts").select("*").eq("buyer_id", user_id).order("created_at", desc=True).execute()
        res_seller = supabase.table("contracts").select("*").eq("seller_id", user_id).order("created_at", desc=True).execute()
        res_creator = supabase.table("contracts").select("*").eq("creator_id", user_id).order("created_at", desc=True).execute()
        
        contracts = (res_buyer.data or []) + (res_seller.data or []) + (res_creator.data or [])
        
        now_utc = datetime.now(timezone.utc)
        valid_contracts = []
        seen_ids = set()
        for c in contracts:
            if c["id"] in seen_ids:
                continue
            seen_ids.add(c["id"])
            
            # Auto delete drafts older than 30 days (1 month)
            if c.get("status") == "draft":
                created_str = c.get("created_at")
                if created_str:
                    try:
                        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        if (now_utc - created_dt).total_seconds() > 30 * 24 * 3600:
                            supabase.table("contracts").delete().eq("id", c["id"]).execute()
                            continue
                    except Exception:
                        pass
            valid_contracts.append(_enrich_contract(c))
            
        valid_contracts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return valid_contracts
    except Exception as e:
        logger.error(f"خطا در دریافت معاملات کاربر {user_id}: {e}")
        return []

def save_user_draft(user_id: int, draft_data: dict, contract_id: str = None) -> Optional[Dict[str, Any]]:
    if not supabase: return None
    try:
        category = draft_data.get("category", "GEN")
        cid = contract_id or draft_data.get("contract_id") or utils.generate_archive_contract_id(category)
        system_id = draft_data.get("system_id") or str(uuid.uuid4())
        
        payload = {
            "id": system_id,
            "contract_id": cid,
            "title": draft_data.get("title", "پیش‌نویس بدون عنوان"),
            "amount": utils.safe_float(draft_data.get("amount", 0)),
            "description": draft_data.get("description", ""),
            "deadline": int(float(draft_data.get("deadline", 1))),
            "category": category,
            "status": "draft",
            "creator_id": user_id,
            "buyer_id": user_id if draft_data.get("role") == "employer" else None,
            "seller_id": user_id if draft_data.get("role") == "freelancer" else None,
            "milestones": draft_data.get("milestones", []),
            "staged_payment": bool(draft_data.get("staged_payment")),
            "recurring": bool(draft_data.get("recurring")),
            "commission_payer": draft_data.get("commission_payer", "freelancer"),
            "free_edits_total": draft_data.get("free_edits", 3)
        }
        res = supabase.table("contracts").upsert(payload, on_conflict="contract_id").execute()
        if res.data:
            return res.data[0]
        return payload
    except Exception as e:
        logger.error(f"Error saving user draft: {e}")
        return None

def delete_user_draft(contract_id: str, user_id: int) -> bool:
    if not supabase: return False
    try:
        supabase.table("contracts").delete().eq("contract_id", contract_id).eq("creator_id", user_id).eq("status", "draft").execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting user draft {contract_id}: {e}")
        return False

def get_user_transactions(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """دریافت لیست تراکنش‌های اخیر کاربر"""
    if not supabase: return []
    try:
        res = supabase.table("transactions").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error getting transactions for {user_id}: {e}")
        return []

def get_ambassador_referrals_count(user_id: int) -> int:
    """تعداد زیرمجموعه‌های یک سفیر"""
    if not supabase: return 0
    try:
        res = supabase.table("users").select("id", count="exact").eq("referred_by", user_id).execute()
        return getattr(res, 'count', 0) or 0
    except Exception:
        return 0


# ====================================================
# ۳.۵ سابقه رویدادها و پرونده کامل معامله (Audit Log / Case File)
# ====================================================
# این بخش برای هر معامله یک سیاهه زمانی از رویدادها (امضا، پرداخت، تحویل،
# رد/تایید پروژه، ویرایش‌های رایگان و اضافه، فیش‌ها، داوری و ...) نگه می‌دارد
# تا ادمین هنگام داوری/حل اختلاف به «سابقه کامل پیام‌ها، وضعیت‌ها و فایل‌ها»
# دسترسی داشته باشد، بدون نیاز به جدول جداگانه یا زیرساخت اضافه در Supabase.

def append_contract_history(
    contract_id: str,
    event: str,
    actor_id: Optional[int] = None,
    file_id: Optional[str] = None,
    file_type: Optional[str] = None
) -> bool:
    """افزودن یک رویداد جدید به سابقه (تایم‌لاین) معامله برای پرونده داوری ادمین"""
    try:
        contract = get_contract(contract_id)
        if not contract:
            return False

        history: List[Dict[str, Any]] = contract.get("history") or []
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor_id,
            "event": event,
        }
        if file_id:
            entry["file_id"] = file_id
            entry["file_type"] = file_type or "document"

        history.append(entry)
        return update_contract(contract_id, {"history": history})
    except Exception as e:
        logger.warning(f"خطا در ثبت تاریخچه برای قرارداد {contract_id}: {e}")
        return False


def get_contract_history(contract_id: str) -> List[Dict[str, Any]]:
    """دریافت کامل سیاهه رویدادهای یک معامله"""
    contract = get_contract(contract_id)
    if not contract:
        return []
    return contract.get("history") or []


def get_contract_file_attachments(contract_id: str) -> List[Dict[str, Any]]:
    """استخراج تمام فایل‌ها/عکس‌های ثبت‌شده روی یک معامله (فیش، تحویلی‌های پروژه و ...)
    جهت نمایش کامل به ادمین در زمان داوری"""
    contract = get_contract(contract_id)
    if not contract:
        return []

    attachments: List[Dict[str, Any]] = []

    receipt_file = contract.get("receipt_file_id")
    if receipt_file:
        attachments.append({"label": "💳 فیش واریزی فعلی", "file_id": receipt_file, "type": "photo"})

    for idx, f in enumerate(contract.get("delivery_files") or [], 1):
        if isinstance(f, dict) and f.get("file_id"):
            attachments.append({
                "label": f"📦 فایل تحویلی پروژه #{idx}",
                "file_id": f["file_id"],
                "type": f.get("type", "document")
            })

    for entry in contract.get("history") or []:
        if entry.get("file_id"):
            attachments.append({
                "label": f"📎 پیوست رویداد: {entry.get('event', '')}",
                "file_id": entry["file_id"],
                "type": entry.get("file_type", "document")
            })

    return attachments


def format_contract_history_text(contract_id: str) -> str:
    """قالب‌بندی متنی خوانا از سابقه معامله جهت ارسال به ادمین (پرونده کامل)"""
    history = get_contract_history(contract_id)
    if not history:
        return "📭 سابقه‌ای برای این معامله ثبت نشده است."

    lines = []
    for entry in history:
        ts = entry.get("ts", "")
        actor = entry.get("actor")
        actor_str = f"(کاربر `{actor}`)" if actor else ""
        lines.append(f"🕒 `{ts}` {actor_str}\n{entry.get('event', '')}")
    return "\n\n".join(lines)


# ====================================================
# ۴. ماژول متمرکز فیش‌های مالی (Pending Receipts)
# ====================================================

def create_pending_receipt(
    receipt_type: str, 
    related_id: str, 
    user_id: int, 
    file_id: str, 
    amount: float, 
    description: str = ""
) -> Optional[Dict[str, Any]]:
    """ایجاد رکورد فیش واریزی در جدول متمرکز جهت نظارت ادمین"""
    if not supabase: return None
    try:
        payload = {
            "type": receipt_type, # contract, wallet, milestone
            "related_id": str(related_id),
            "user_id": user_id,
            "file_id": file_id,
            "amount": utils.safe_float(amount),
            "description": description,
            "status": "pending"
        }
        res = supabase.table("pending_receipts").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"خطا در ثبت فیش در pending_receipts: {e}")
        return None

def get_pending_receipt(receipt_id: Union[str, int]) -> Optional[Dict[str, Any]]:
    """دریافت فیش بر اساس آیدی از جدول متمرکز"""
    if not supabase: return None
    try:
        res = supabase.table("pending_receipts").select("*").eq("id", receipt_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"خطا در دریافت فیش {receipt_id} از pending_receipts: {e}")
        return None

def update_pending_receipt_status(receipt_id: Union[str, int], status: str) -> bool:
    """تغییر وضعیت فیش در جدول متمرکز (approved/rejected)"""
    if not supabase: return False
    try:
        res = supabase.table("pending_receipts").update({
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", receipt_id).execute()
        return True if res.data else False
    except Exception as e:
        logger.error(f"خطا در بروزرسانی وضعیت فیش {receipt_id} در pending_receipts: {e}")
        return False


# ====================================================
# ۵. ماژول جدید داوری پیشرفته (Disputes)
# ====================================================

def create_withdrawal_request(user_id: int, amount: float, sheba: str, req_type: str = "user", holder_name: str = None) -> Optional[Dict[str, Any]]:
    """
    ثبت درخواست برداشت وجه در جدول withdrawal_requests.
    مبلغ بلافاصله کسر می‌شود (رزرو).
    """
    if not supabase:
        logger.error("❌ [WITHDRAW_ERR] supabase client is None")
        return None
    
    logger.info(f"🔄 [WITHDRAW_START] User: {user_id} | Amount: {amount} | Type: {req_type}")
    
    # کسر موجودی (رزرو)
    if req_type == "ambassador":
        if not update_ambassador_stats(user_id, earnings_delta=-amount, is_withdrawal=True):
            logger.error(f"❌ [WITHDRAW_ERR] Failed to deduct ambassador balance for user {user_id}")
            return None
    else:
        if not update_wallet_balance(user_id, -amount, "withdraw_reserved", "رزرو مبلغ درخواست برداشت"):
            logger.error(f"❌ [WITHDRAW_ERR] Wallet deduction failed for user {user_id}")
            return None

    try:
        # بررسی تعداد درخواست‌های معلق
        res_p = supabase.table("withdrawal_requests").select("id").eq("user_id", user_id).eq("status", "pending").execute()
        pending_count = len(res_p.data) if res_p.data else 0
        
        data = {
            "user_id": user_id,
            "amount": amount,
            "sheba": str(sheba),
            "sheba_or_card": str(sheba),
            "holder_name": holder_name,
            "type": req_type,
            "status": "pending",
            "suspicious_flag": pending_count >= 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            res = supabase.table("withdrawal_requests").insert(data).execute()
            rows = res.data or []
        except Exception as ins_exc:
            logger.error(f"❌ [WITHDRAW_INSERT_EXC] Failed to insert data: {data} | Error: {ins_exc}")
            # تلاش مجدد با داده‌های محدودتر (شاید برخی ستون‌ها وجود ندارند)
            limited_data = {
                "user_id": user_id,
                "amount": amount,
                "status": "pending",
                "created_at": data["created_at"]
            }
            if sheba: limited_data["sheba"] = str(sheba)
            logger.info(f"🔄 [WITHDRAW_RETRY] Retrying with limited data: {limited_data}")
            res = supabase.table("withdrawal_requests").insert(limited_data).execute()
            rows = res.data or []
        
        if not rows:
            logger.error(f"❌ [WITHDRAW_INSERT_ERR] Insert succeeded but no rows returned for user {user_id}")
            raise Exception("No data returned from withdrawal insert")
        
        logger.info(f"✅ [WITHDRAW_OK] Request #{rows[0].get('id')} created for user {user_id}")
        return rows[0]

    except Exception as e:
        logger.error(f"❌ [WITHDRAW_CRITICAL_ERR] Failed to insert withdrawal request for user {user_id}: {e}", exc_info=True)
        # بازگشت مبلغ در صورت خطا در دیتابیس
        if req_type == "ambassador":
            update_ambassador_stats(user_id, earnings_delta=amount, is_withdrawal=True)
        else:
            update_wallet_balance(user_id, amount, "withdraw_reserve_rollback", "بازگشت رزرو به دلیل خطای ثبت")
        return None


def get_pending_withdrawal_requests() -> List[Dict[str, Any]]:
    """لیست تمام درخواست‌های برداشت در وضعیت انتظار یا در صف، برای نمایش به ادمین"""
    if not supabase:
        return []
    try:
        res = supabase.table("withdrawal_requests").select("*").in_("status", ["pending", "queued"]).order("created_at", desc=False).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"خطا در دریافت لیست درخواست‌های برداشت: {e}")
        return []


def get_withdrawal_request(req_id: Union[int, str]) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    try:
        res = supabase.table("withdrawal_requests").select("*").eq("id", int(req_id)).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.error(f"خطا در دریافت درخواست برداشت {req_id}: {e}")
        return None


def resolve_withdrawal_request(req_id: Union[int, str], approve: bool, admin_id: Optional[int] = None, status: str = "paid") -> bool:
    """
    تسویه یا لغو یک درخواست برداشت.
    """
    if not supabase:
        logger.error("❌ [DB_RESOLVE] Supabase client is not initialized.")
        return False
    
    req = get_withdrawal_request(req_id)
    if not req:
        logger.error(f"❌ [DB_RESOLVE] Withdrawal request {req_id} not found.")
        return False
    
    current_status = req.get("status")
    new_status = status if approve else "cancelled"
    
    # اگر قبلاً در این وضعیت بوده، کار تمام است (Idempotency)
    if current_status == new_status:
        logger.info(f"ℹ️ [DB_RESOLVE] Request {req_id} is already in status '{new_status}'.")
        return True
        
    # فقط درخواست‌های معلق یا در صف قابل تغییر وضعیت هستند (مگر اینکه بخواهیم از queued به paid برویم)
    valid_transitions = ["pending", "queued"]
    if current_status not in valid_transitions:
        logger.warning(f"⚠️ [DB_RESOLVE] Cannot resolve request {req_id}. Current status is '{current_status}'.")
        return False

    try:
        update_data = {
            "status": new_status,
        }
        if admin_id:
            update_data["resolved_by"] = admin_id

        res = supabase.table("withdrawal_requests").update(update_data).eq("id", int(req_id)).execute()
        if not res.data:
            logger.error(f"❌ [DB_UPDATE_ERR] Failed to update withdrawal {req_id}. No records matched/updated.")
            return False
            
        logger.info(f"✅ [DB_RESOLVE] Successfully updated withdrawal {req_id} to '{new_status}'.")
        
        # منطق بازگشت وجه در صورت لغو
        if not approve:
            req_type = req.get("type", "user")
            if req_type == "ambassador":
                update_ambassador_stats(req["user_id"], earnings_delta=float(req["amount"]))
            else:
                update_wallet_balance(
                    req["user_id"], float(req["amount"]), "withdraw_refund",
                    f"بازگشت وجه به دلیل لغو درخواست برداشت #{req_id}"
                )
        return True
    except Exception as e:
        logger.error(f"❌ [DB_EXC] Error resolving withdrawal {req_id}: {e}", exc_info=True)
        # تلاش مجدد بدون resolved_by در صورت خطا
        if "resolved_by" in str(e):
            try:
                logger.info(f"🔄 [DB_RETRY] Retrying withdrawal resolution without resolved_by for {req_id}")
                res = supabase.table("withdrawal_requests").update({
                    "status": new_status,
                }).eq("id", int(req_id)).execute()
                return True if res.data else False
            except Exception as e2:
                logger.error(f"❌ [DB_RETRY_ERR] Retry also failed: {e2}", exc_info=True)
        return False


def update_withdrawal_documents(req_id: Union[int, str], document_file_ids: list, document_note: str = "") -> bool:
    """
    بروزرسانی درخواست برداشت با مستندات (عکس/فایل‌های متنی) که ادمین اضافه می‌کند.
    این توابع ادمین‌ها را قادر می‌سازد تا فیش‌های واقعی واریزی را به سیستم اضافه کنند.
    """
    if not supabase:
        return False
    
    try:
        update_data = {
            "documents": document_file_ids,  # لیست file_id های تلگرام
        }
        # اگر نوت وجود داشت، آن را در لاگ ذخیره می‌کنیم یا اگر ستونی بود اضافه می‌کنیم
        # در حال حاضر طبق شما، ستون document_note وجود ندارد.
        
        res = supabase.table("withdrawal_requests").update(update_data).eq("id", int(req_id)).execute()
        if not res.data:
             logger.error(f"❌ [DOC_UPDATE_ERR] No data returned when updating docs for {req_id}")
             return False
        return True
    except Exception as e:
        logger.error(f"❌ [DOC_UPDATE_EXC] Error saving docs for withdrawal {req_id}: {e}", exc_info=True)
        return False
        try:
            import json
            supabase.table("withdrawal_requests").update({
                "documents": json.dumps(document_file_ids),
                "document_note": document_note,
            }).eq("id", int(req_id)).execute()
            return True
        except:
            return False


def create_dispute(
    transaction_id: str, 
    opened_by: int, 
    reason: str, 
    proof_file_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """ثبت اعتراض و تشکیل پرونده داوری"""
    if not supabase: return None
    try:
        payload = {
            "transaction_id": str(transaction_id),
            "opened_by": opened_by,
            "reason": reason,
            "proof_file_id": proof_file_id,
            "verdict": "pending"  # pending, employer_win, freelancer_win, split
        }
        res = supabase.table("disputes").insert(payload).execute()
        update_contract(transaction_id, "disputed")
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"خطا در ایجاد پرونده داوری برای معامله {transaction_id}: {e}")
        return None


def get_dispute_by_contract(contract_id: str) -> Optional[Dict[str, Any]]:
    """دریافت پرونده داوری بر اساس شناسه معامله"""
    if not supabase: return None
    try:
        res = supabase.table("disputes").select("*").eq("transaction_id", contract_id).order("created_at", desc=True).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"خطا در دریافت پرونده داوری معامله {contract_id}: {e}")
        return None


def resolve_dispute(
    dispute_id: Union[str, int], 
    verdict: str, 
    split_ratio_freelancer: Optional[float] = None
) -> bool:
    """صدور رای نهایی پرونده داوری توسط ادمین"""
    if not supabase: return False
    try:
        payload = {
            "verdict": verdict,
            "split_ratio_freelancer": split_ratio_freelancer
        }
        res = supabase.table("disputes").update(payload).eq("id", dispute_id).execute()
        return True if res.data else False
    except Exception as e:
        logger.error(f"خطا در ثبت رای داوری {dispute_id}: {e}")
        return False


def create_dispute_ticket(contract_id: str, user_id: int, reason: str) -> bool:
    """
    ثبت درخواست داوری کاربر برای یک معامله.
    وضعیت به in_dispute تغییر می‌کند و علت شکایت ذخیره می‌شود.
    """
    contract = get_contract(contract_id)
    if not contract:
        return False

    ok = update_contract(contract_id, {"status": "in_dispute", "dispute_reason": reason, "dispute_opened_by": user_id})

    # تلاش برای ثبت موازی در جدول disputes جهت گزارش‌گیری
    try:
        payload = {
            "transaction_id": str(contract_id),
            "opened_by": user_id,
            "reason": reason,
            "verdict": "pending"
        }
        supabase.table("disputes").insert(payload).execute()
    except Exception as e:
        logger.warning(f"ثبت موازی در جدول disputes ناموفق بود: {e}")

    append_contract_history(
        contract_id,
        f"⚖️ درخواست داوری ثبت شد و معامله به وضعیت 'داوری (In Dispute)' تغییر یافت.\nعلت:\n{reason}",
        actor_id=user_id
    )
    return ok


def create_support_ticket(user_id: int, subject: str, detail: str) -> bool:
    """
    ثبت تیکت پشتیبانی/درخواست مالی کاربر (شارژ یا برداشت کیف پول و ...).
    این تابع هم قبلاً فراخوانی می‌شد ولی تعریف نشده بود (باگ Crash در بخش
    «شارژ حساب» و «درخواست برداشت» کیف پول) — اکنون به‌صورت best-effort (بدون
    وابستگی سخت به وجود جدول در Supabase) اضافه شده تا کل فرآیند کیف پول کرش نکند.
    """
    if supabase:
        try:
            supabase.table("support_tickets").insert({
                "user_id": user_id,
                "subject": subject,
                "detail": detail,
                "status": "open"
            }).execute()
        except Exception as e:
            logger.warning(f"ثبت تیکت پشتیبانی در Supabase ناموفق بود (نادیده گرفته شد): {e}")
    return True


# ====================================================
# ۶. مدیریت دیتابیس محلی (SQLite) و کلاس پوششی DB
# ====================================================

def get_public_transparency_stats() -> Dict[str, Any]:
    """
    آمار عمومی شفافیت (اثبات اجتماعی) برای نمایش به کاربران/مشتریان کانال‌های
    همکار — تعداد و حجم معاملات موفق تسویه‌شده و تعداد معاملات در حال انجام.
    """
    stats = {"completed_count": 0, "completed_volume": 0.0, "active_count": 0}
    if not supabase:
        return stats
    try:
        res_completed = supabase.table("contracts").select("amount").eq("status", "completed").execute()
        rows = res_completed.data or []
        stats["completed_count"] = len(rows)
        stats["completed_volume"] = sum(float(r.get("amount", 0) or 0) for r in rows)

        active_statuses = getattr(config, "LOCKED_DEAL_STATUSES", []) + [
            "pending_approval", "bargaining", "awaiting_payment", "awaiting_receipt_approval"
        ]
        res_active = supabase.table("contracts").select("contract_id", count="exact").in_("status", active_statuses).execute()
        stats["active_count"] = res_active.count if res_active.count is not None else len(res_active.data or [])
    except Exception as e:
        logger.error(f"خطا در محاسبه آمار شفافیت عمومی: {e}")
    return stats


# init_db (SQLite محلی) — حذف شد.
# پایگاه داده واقعی پروژه Supabase است. جداول contracts، receipts،
# disputes و support_tickets همگی در Supabase تعریف شده‌اند.
# برای migration، فایل supabase_migration.sql را اجرا کنید.


# ====================================================
# سیستم تنظیمات زنده (Live Settings) — جدول bot_settings
# ====================================================

def get_all_settings() -> Dict[str, Any]:
    """دریافت تمام تنظیمات جهت نمایش در پنل ادمین"""
    if not supabase:
        return {}
    try:
        res = supabase.table("bot_settings").select("*").execute()
        settings = {}
        for row in (res.data or []):
            k = row.get("key")
            v = row.get("value")
            if k:
                settings[k] = v
        return settings
    except Exception as e:
        logger.error(f"خطا در دریافت لیست تنظیمات: {e}")
        return {}


# ====================================================
# لاگ فعالیت ادمین‌ها (Audit Log)
# ====================================================

def log_admin_action(user_id: int, action: str, detail: str = "", target_id: Optional[int] = None) -> None:
    """
    ثبت تمام اقدامات کلیدی ادمین (آزادسازی وجه، بن کاربر، تغییر موجودی،
    تغییر کارمزد و ...) در جدول admin_audit_log با تاریخ/زمان دقیق.
    این تابع هرگز Exception پرتاب نمی‌کند تا جریان اصلی برنامه را مختل نکند.
    """
    if not supabase:
        return
    try:
        supabase.table("admin_audit_log").insert({
            "admin_id": user_id,
            "action": action,
            "detail": str(detail)[:500],
            "target_id": target_id,
        }).execute()
    except Exception as e:
        logger.warning(f"خطا در ثبت لاگ ادمین: {e}")


def get_admin_audit_logs(limit: int = 20, admin_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """دریافت آخرین لاگ‌های فعالیت ادمین‌ها برای نمایش در پنل"""
    if not supabase:
        return []
    try:
        q = supabase.table("admin_audit_log").select("*").order("created_at", desc=True).limit(limit)
        if admin_id:
            q = q.eq("admin_id", admin_id)
        res = q.execute()
        return res.data or []
    except Exception as e:
        logger.warning(f"خطا در دریافت لاگ‌های ادمین: {e}")
        return []


# ====================================================
# آمار سریع برای نمایش در پنل مدیریت (Quick Stats Badge)
# ====================================================

def get_admin_quick_stats() -> Dict[str, int]:
    """
    دریافت تعداد موارد نیازمند اقدام فوری برای نمایش «بج» در منوی پنل ادمین:
    پرونده‌های داوری باز، درخواست‌های برداشت در انتظار، فیش‌های تایید‌نشده.
    """
    stats = {"disputes": 0, "withdrawals": 0, "deal_receipts": 0, "deposit_receipts": 0}
    if not supabase:
        return stats
    try:
        r1 = supabase.table("contracts").select("contract_id", count="exact").in_("status", ["disputed", "in_dispute"]).execute()
        stats["disputes"] = r1.count or len(r1.data or [])
    except Exception:
        pass
    try:
        r2 = supabase.table("withdrawal_requests").select("id", count="exact").eq("status", "pending").execute()
        stats["withdrawals"] = r2.count or len(r2.data or [])
    except Exception:
        pass
    try:
        # فیش‌های معاملات در انتظار
        r3 = supabase.table("pending_receipts").select("id", count="exact").eq("status", "pending").in_("type", ["contract", "milestone"]).execute()
        stats["deal_receipts"] = r3.count or len(r3.data or [])
    except Exception:
        pass
    try:
        # فیش‌های شارژ کیف پول (تراکنش‌های معلق)
        r4 = supabase.table("pending_receipts").select("id", count="exact").eq("status", "pending").eq("type", "wallet").execute()
        stats["deposit_receipts"] = r4.count or len(r4.data or [])
    except Exception:
        pass
    return stats


def get_transaction_chart_data():
    """دریافت داده‌های تراکنش‌های موفق برای نمودار (۳۰ روز اخیر)"""
    if not supabase: return []
    
    try:
        # تراکنش‌های موفق (شارژ حساب یا پرداخت‌های قرارداد)
        # وضعیت‌های موفق: 'approved', 'completed'
        res = supabase.table("transactions").select("amount, created_at")\
            .in_("status", ["approved", "completed"])\
            .order("created_at", desc=False).execute()
        
        data = res.data or []
        # تجمیع بر اساس روز در پایتون
        daily_stats = {}
        for tx in data:
            # ایجاد تاریخ (YYYY-MM-DD)
            # created_at: 2023-10-27T10:00:00+00:00
            date_str = tx['created_at'].split('T')[0]
            amount = float(tx['amount'] or 0)
            daily_stats[date_str] = daily_stats.get(date_str, 0) + amount
            
        # تبدیل به لیست مرتب شده
        sorted_dates = sorted(daily_stats.keys())
        result = [{"date": d, "amount": daily_stats[d]} for d in sorted_dates]
        return result[-30:] # فقط ۳۰ روز آخر
    except Exception as e:
        logger.error(f"Error fetching transaction chart data: {e}")
        return []


# Alias functions for module-level access (used as db.get_setting in other files)
get_setting = get_bot_setting
set_setting = set_bot_setting

class Database:
    def __init__(self):
        self.register_or_update_user = register_or_update_user
        self.get_user = get_user
        self.update_user_fields = update_user_fields
        self.update_user_identity = update_user_identity
        self.update_user_phone = update_user_phone
        self.update_wallet_balance = update_wallet_balance
        self.is_user_blacklisted = is_user_blacklisted
        self.ensure_user_exists = ensure_user_exists
        
        self.create_contract = create_contract
        self.get_contract = get_contract
        self.update_contract = update_contract
        self.get_user_contracts = get_user_contracts
        self.append_contract_history = append_contract_history
        self.get_contract_history = get_contract_history
        self.get_contract_file_attachments = get_contract_file_attachments
        self.format_contract_history_text = format_contract_history_text
        
        self.create_receipt = create_pending_receipt
        self.get_receipt = get_pending_receipt
        self.update_receipt_status = update_pending_receipt_status
        
        self.create_deposit_transaction = create_deposit_transaction
        self.update_deposit_receipt = update_deposit_receipt
        self.approve_deposit_transaction = approve_deposit_transaction
        self.reject_deposit_transaction = reject_deposit_transaction
        
        self.create_dispute = create_dispute
        self.get_dispute_by_contract = get_dispute_by_contract
        self.resolve_dispute = resolve_dispute
        self.create_dispute_ticket = create_dispute_ticket
        self.create_support_ticket = create_support_ticket
        
        self.register_ambassador = register_ambassador
        self.get_ambassador = get_ambassador
        self.update_ambassador_stats = update_ambassador_stats
        self.update_affiliate_wallet_balance = update_affiliate_wallet_balance
        self.get_ambassador_dashboard_stats = get_ambassador_dashboard_stats
        
        self.create_withdrawal_request = create_withdrawal_request
        self.get_pending_withdrawal_requests = get_pending_withdrawal_requests
        self.get_withdrawal_request = get_withdrawal_request
        self.resolve_withdrawal_request = resolve_withdrawal_request
        self.update_withdrawal_documents = update_withdrawal_documents
        
        self.set_user_state = set_user_state
        self.get_user_state = get_user_state
        self.clear_user_state = clear_user_state
        
        self.get_public_transparency_stats = get_public_transparency_stats
        self.get_admin_quick_stats = get_admin_quick_stats
        self.get_admin_audit_logs = get_admin_audit_logs
        self.log_admin_action = log_admin_action
        
        self.get_setting = get_bot_setting
        self.set_setting = set_bot_setting
        self.get_all_settings = get_all_settings
        
        self.get_current_user_phone = get_current_user_phone
        self.set_user_session = set_user_session
        self.clear_user_session = clear_user_session

        self.validate_iranian_sheba = validate_iranian_sheba
        self.supabase = supabase

# ====================================================
# Auth & Session Management (Phone-based Identity)
# ====================================================

def get_current_user_phone(telegram_id: int) -> Optional[str]:
    """دریافت شماره تلفن نشست فعال برای یک telegram_id از جدول users"""
    if not supabase: return None
    try:
        res = supabase.table("users").select("phone_number").eq("id", telegram_id).execute()
        if res.data and len(res.data) > 0:
            return res.data[0].get("phone_number")
        return None
    except Exception as e:
        logger.error(f"Error getting session for {telegram_id}: {e}")
        return None

def set_user_session(telegram_id: int, phone_number: str) -> bool:
    """ثبت یا بروزرسانی نشست فعال کاربر در جدول users"""
    if not supabase: return False
    try:
        norm_phone = normalize_phone_number(phone_number) or phone_number
        supabase.table("users").upsert({
            "id": telegram_id,
            "phone_number": norm_phone,
            "is_verified": True
        }).execute()
        _clear_user_cache(telegram_id)
        return True
    except Exception as e:
        logger.error(f"Error setting session for {telegram_id}: {e}")
        return False

def clear_user_session(telegram_id: int) -> bool:
    """خروج از حساب (پاکسازی شماره تلفن نشست از جدول users)"""
    if not supabase: return False
    try:
        supabase.table("users").update({
            "phone_number": None,
            "is_verified": False
        }).eq("id", telegram_id).execute()
        _clear_user_cache(telegram_id)
        return True
    except Exception as e:
        logger.error(f"Error clearing session for {telegram_id}: {e}")
        return False

def get_user_by_phone(phone_number: str) -> Optional[Dict[str, Any]]:
    """یافتن کاربر از طریق شماره موبایل (با پشتیبانی کامل از جستجوی دقیق، الگو و فیلترینگ پایتون)"""
    if not supabase: return None
    try:
        digits = "".join([c for c in phone_number if c.isdigit()])
        if not digits:
            return None
        
        norm_phone = normalize_phone_number(phone_number) or (("0" + digits[-10:]) if len(digits) >= 10 else digits)
        last_10 = digits[-10:] if len(digits) >= 10 else digits

        # 1. Try exact match
        try:
            res = supabase.table("users").select("*").eq("phone_number", norm_phone).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception as e:
            logger.warning(f"Exact phone match query failed: {e}")

        # 2. Try ilike match
        try:
            if len(digits) >= 10:
                res2 = supabase.table("users").select("*").ilike("phone_number", f"%{last_10}").execute()
                if res2.data and len(res2.data) > 0:
                    return res2.data[0]
        except Exception as e:
            logger.warning(f"Ilike phone match query failed: {e}")

        # 3. Fallback: fetch users and check in Python (guarantees 100% reliability against RLS or format quirks)
        try:
            res_all = supabase.table("users").select("*").limit(1000).execute()
            if res_all.data:
                for u in res_all.data:
                    u_phone = u.get("phone_number")
                    if u_phone:
                        u_digits = "".join([c for c in str(u_phone) if c.isdigit()])
                        if u_digits and (u_digits == digits or u_digits.endswith(last_10) or digits.endswith(u_digits)):
                            return u
        except Exception as e:
            logger.warning(f"Python fallback phone search failed: {e}")

        return None
    except Exception as e:
        logger.error(f"Error fetching user by phone {phone_number}: {e}", exc_info=True)
        return None

def check_phone_exists(phone_number: str) -> bool:
    """بررسی وجود شماره موبایل در دیتابیس"""
    user = get_user_by_phone(phone_number)
    return user is not None

def link_telegram_id(phone_number: str, telegram_id: int) -> bool:
    """اتصال تلگرام آیدی به حساب کاربری موجود"""
    if not supabase: return False
    try:
        user = get_user_by_phone(phone_number)
        if user:
            old_id = user.get("id")
            if old_id and old_id != telegram_id:
                user_data = dict(user)
                user_data["id"] = telegram_id
                user_data["is_verified"] = True
                supabase.table("users").upsert(user_data).execute()
                try:
                    supabase.table("users").delete().eq("id", old_id).execute()
                except:
                    pass
            else:
                supabase.table("users").update({"is_verified": True}).eq("phone_number", phone_number).execute()
        _clear_user_cache(telegram_id)
        set_user_session(telegram_id, phone_number)
        return True
    except Exception as e:
        logger.error(f"Error linking telegram_id {telegram_id} to phone {phone_number}: {e}")
        return False

def normalize_phone_number(phone_str: str) -> Optional[str]:
    """تبدیل انواع فرمت‌های شماره به 09XXXXXXXXX"""
    if not phone_str: return None
    normalized = unicodedata.normalize('NFKC', phone_str)
    digits = "".join([c for c in normalized if c.isdigit()])
    if digits.startswith(("98", "0098")):
        digits = "0" + digits[2:] if len(digits) > 10 else digits[2:]
    elif digits.startswith("09") and len(digits) == 11:
        pass
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    else:
        return None
    return digits if len(digits) == 11 else None

def ensure_user_exists_basic(user_id: int, username: str = ""):
    """اطمینان از وجود کاربر برای جلوگیری از خطای Foreign Key"""
    if not supabase: return
    try:
        supabase.table("users").upsert({"id": user_id, "username": username}, on_conflict="id").execute()
    except Exception as e:
        logger.error(f"Error ensuring user {user_id}: {e}")

def set_user_state_safe(user_id: int, state: str, data: Dict = None, username: str = ""):
    """تنظیم وضعیت با اطمینان از وجود کاربر"""
    ensure_user_exists_basic(user_id, username)
    set_user_state(user_id, state, data)

def register_or_update_user_by_phone(phone_number: str, full_name: str, telegram_id: Optional[int] = None, username: str = "", **kwargs) -> Optional[Dict[str, Any]]:
    """ثبت یا بروزرسانی کاربر بر اساس شماره تلفن"""
    if not supabase: return None
    try:
        norm_phone = normalize_phone_number(phone_number)
        if not norm_phone:
            return None
        existing = get_user_by_phone(norm_phone)
        if existing:
            update_data = {}
            if full_name: update_data["full_name"] = full_name
            if username: update_data["username"] = username
            for k, v in kwargs.items():
                if v is not None: update_data[k] = v
            if update_data:
                supabase.table("users").update(update_data).eq("phone_number", norm_phone).execute()
                existing.update(update_data)
            if telegram_id:
                set_user_session(telegram_id, norm_phone)
            return existing
        else:
            uid = telegram_id if telegram_id else int(str(abs(hash(norm_phone)))[:10])
            new_user = {
                "id": uid,
                "phone_number": norm_phone,
                "full_name": full_name or "کاربر میانجی",
                "username": username or "",
                "wallet_balance": kwargs.get("wallet_balance", 0.0),
                "role": kwargs.get("role", "user"),
                "is_verified": True,
                "national_id": kwargs.get("national_id"),
                "is_blacklisted": kwargs.get("is_blacklisted", False),
                "payment_cards": kwargs.get("payment_cards", "[]")
            }
            res = supabase.table("users").insert(new_user).execute()
            if res.data:
                created = res.data[0]
                if telegram_id:
                    set_user_session(telegram_id, norm_phone)
                return created
            return None
    except Exception as e:
        logger.error(f"Error registering/updating user by phone: {e}")
        return None

db = Database()

# بخش‌های اضافی/تغییر یافتهٔ database.py
