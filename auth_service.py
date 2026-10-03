import hashlib
import bcrypt
from typing import Optional, Dict, Any
import database as db

def hash_password(password: str) -> str:
    """هش کردن رمز عبور با استفاده از bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed_password: str) -> bool:
    """بررسی صحت رمز عبور"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))

def register_user_credentials(user_id: int, phone_number: str, password: str, full_name: str) -> bool:
    """ثبت اطلاعات کاربری در جدول user_credentials و بروزرسانی جدول کاربران"""
    try:
        # هش کردن رمز
        hashed = hash_password(password)
        
        # ۱. درج در credentials
        data = {
            "telegram_id": user_id,
            "phone_number": phone_number,
            "password_hash": hashed
        }
        db.supabase.table("user_credentials").insert(data).execute()
        
        # ۲. بروزرسانی نام و فامیل در جدول کاربران
        db.register_or_update_user(user_id, full_name=full_name, phone_number=phone_number)
        
        return True
    except Exception as e:
        print(f"Error registering credentials for user {user_id}: {e}")
        return False

def check_credentials(phone_number: str, password: str) -> Optional[int]:
    """بررسی شماره تماس و رمز عبور. در صورت موفقیت telegram_id کاربر را برمی‌گرداند"""
    try:
        res = db.supabase.table("user_credentials").select("*").eq("phone_number", phone_number).execute()
        if res.data:
            user = res.data[0]
            if verify_password(password, user["password_hash"]):
                return user["telegram_id"]
        return None
    except Exception as e:
        print(f"Error checking credentials: {e}")
        return None

def is_authenticated(user_id: int) -> bool:
    """بررسی احراز هویت کاربر"""
    try:
        res = db.supabase.table("user_credentials").select("telegram_id").eq("telegram_id", user_id).execute()
        return bool(res.data)
    except Exception as e:
        print(f"Error checking auth status: {e}")
        return False
