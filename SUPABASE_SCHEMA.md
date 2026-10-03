# مستندات دیتابیس سوپابیس (Supabase Schema) - پروژه ربات میانجی (نسخه نهایی و یکپارچه)

این مستند شامل ساختار استاندارد و یکپارچه جداول، فیلدها و روابط موجود در دیتابیس پروژه است. این ساختار با آخرین تغییرات کد (Refactoring) کاملاً هماهنگ است.

## ۱. جدول کاربران (`users`)
ذخیره اطلاعات پروفایل، احراز هویت و موجودی کیف پول.

| نام فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `id` | `BigInt` (PK) | شناسه عددی تلگرام کاربر |
| `username` | `Text` | نام کاربری تلگرام |
| `full_name` | `Text` | نام و نام خانوادگی نمایشی |
| `phone_number` | `Text` | شماره موبایل تایید شده |
| `wallet_balance` | `Numeric` | موجودی کیف پول (تومان) |
| `role` | `Text` | نقش: `user`, `ambassador`, `admin`, `owner` |
| `is_verified` | `Boolean` | وضعیت احراز هویت هوشمند |
| `national_id` | `Text` | کد ملی تایید شده |
| `invited_by` | `BigInt` | شناسه معرف (FK به `users.id`) |
| `payment_cards` | `JSONB` | لیست کارت‌های بانکی تایید شده |
| `is_blacklisted` | `Boolean` | وضعیت مسدودی کاربر |
| `created_at` | `Timestamp` | زمان عضویت |

## ۲. جدول وضعیت کاربران (`user_states`)
مدیریت وضعیت FSM (ماشین وضعیت) ربات.

| نام فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `id` | `BigInt` (PK) | شناسه تلگرام (FK به `users.id`) |
| `state` | `Text` | وضعیت فعلی در ربات (مثلاً `WAITING_TITLE`) |
| `data` | `JSONB` | داده‌های موقت ذخیره شده در این وضعیت |
| `updated_at` | `Timestamp` | زمان آخرین تغییر |

## ۳. جدول سفیران (`ambassadors`)
اطلاعات اختصاصی همکاران و سفیران پلتفرم.

| نام فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `telegram_id` | `BigInt` (PK) | شناسه تلگرام (FK به `users.id`) |
| `commission_rate` | `Numeric` | درصد پورسانت اختصاصی (پیش‌فرض ۳۰٪) |
| `total_referrals` | `Int` | تعداد کل زیرمجموعه‌ها |
| `total_earnings` | `Numeric` | مجموع سود کسب شده تا کنون |
| `withdrawable_balance` | `Numeric` | موجودی قابل برداشت پورسانت |
| `tier_level` | `Text` | سطح سفیر: `Bronze`, `Silver`, `Gold` |

## ۴. جدول معاملات (`contracts`)
هسته اصلی سیستم (Escrow) برای مدیریت قراردادها.

| نام فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `id` | `Text` (PK) | شناسه سیستمی (مثلاً UUID) |
| `contract_id` | `Text` (Unique) | شناسه نمایشی (مثلاً DEV-123) |
| `title` | `Text` | عنوان پروژه |
| `amount` | `Numeric` | مبلغ کل معامله (تومان) |
| `buyer_id` | `BigInt` | شناسه خریدار (کارفرما) |
| `seller_id` | `BigInt` | شناسه فروشنده (مجری) |
| `status` | `Text` | وضعیت: `waiting_signature`, `active`, `completed`, etc. |
| `category` | `Text` | دسته‌بندی پروژه (DEV, DS, ...) |
| `milestones` | `JSONB` | مراحل پرداخت پروژه |
| `history` | `JSONB` | لاگ تمامی رویدادهای معامله |
| `delivery_files` | `JSONB` | فایل‌های خروجی پروژه |
| `paid_at` | `Timestamp` | زمان تایید پرداخت وجه |

## ۵. جدول تراکنش‌ها (`transactions`)
ثبت تمامی رویدادهای مالی (واریز، برداشت، جریمه، تسویه).

| نام فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `id` | `BigInt` (PK) | شناسه تراکنش |
| `user_id` | `BigInt` | کاربر ذینفع |
| `amount` | `Numeric` | مبلغ جابجایی |
| `type` | `Text` | نوع: `deposit`, `withdrawal`, `commission`, `payment` |
| `status` | `Text` | وضعیت: `completed`, `pending`, `rejected` |

## ۶. جدول پرونده‌های داوری (`disputes`)
مدیریت اختلافات در معاملات.

| نام فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `id` | `BigInt` (PK) | شناسه پرونده |
| `transaction_id` | `Text` | شناسه معامله (FK به `contracts.contract_id`) |
| `opened_by` | `BigInt` | بازکننده پرونده |
| `reason` | `Text` | علت اختلاف |
| `verdict` | `Text` | حکم نهایی داور |

---
**نکته امنیتی:** تمامی عملیات حذف در دیتابیس به صورت `CASCADE` تنظیم شده است تا با حذف یک کاربر، اطلاعات مرتبط (مانند وضعیت) نیز پاک شوند.
