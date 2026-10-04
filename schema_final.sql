-- Drop dependent tables first if resetting
DROP TABLE IF EXISTS user_sessions CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- 1. جدول اصلی کاربران (Phone-based identity primary key with password support)
CREATE TABLE users (
    id BIGINT PRIMARY KEY, -- Telegram ID or user internal ID
    phone_number VARCHAR(15) NOT NULL,
    full_name TEXT,
    username TEXT,
    password_hash TEXT,
    wallet_balance NUMERIC DEFAULT 0,
    role TEXT DEFAULT 'user',
    is_verified BOOLEAN DEFAULT TRUE,
    national_id TEXT,
    invited_by BIGINT,
    payment_cards JSONB DEFAULT '[]',
    is_blacklisted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    CONSTRAINT users_phone_unique UNIQUE (phone_number)
);

-- Ensure explicit unique index for foreign key referencing
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_number ON users(phone_number);

-- 2. جدول نشست‌های کاربران (User Sessions)
CREATE TABLE user_sessions (
    telegram_id BIGINT PRIMARY KEY,
    phone_number VARCHAR(15) NOT NULL REFERENCES users(phone_number) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()),
    last_activity TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 3. جدول مدیریت وضعیت (FSM)
CREATE TABLE IF NOT EXISTS user_states (
    id BIGINT PRIMARY KEY,
    state TEXT,
    data JSONB DEFAULT '{}',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 4. جدول سفیران (Ambassadors)
CREATE TABLE IF NOT EXISTS ambassadors (
    telegram_id BIGINT PRIMARY KEY,
    commission_rate NUMERIC DEFAULT 30.0,
    total_referrals INT DEFAULT 0,
    total_earnings NUMERIC DEFAULT 0,
    withdrawable_balance NUMERIC DEFAULT 0,
    tier_level TEXT DEFAULT 'Bronze',
    is_active BOOLEAN DEFAULT TRUE
);

-- 5. جدول معاملات (Contracts)
CREATE TABLE IF NOT EXISTS contracts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id TEXT UNIQUE NOT NULL,
    title TEXT,
    amount NUMERIC,
    buyer_id BIGINT,
    seller_id BIGINT,
    status TEXT,
    category TEXT,
    milestones JSONB DEFAULT '[]',
    history JSONB DEFAULT '[]',
    delivery_files JSONB DEFAULT '[]',
    paid_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 6. جدول تراکنش‌ها (Transactions)
CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    amount NUMERIC,
    type TEXT,
    status TEXT,
    description TEXT,
    receipt_file_id TEXT,
    admin_document_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 7. جدول تنظیمات سیستمی (Bot Settings)
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 8. جدول لاگ پورسانت سفیران (Commission Logs)
CREATE TABLE IF NOT EXISTS commission_logs (
    id BIGSERIAL PRIMARY KEY,
    ambassador_id BIGINT,
    trade_id TEXT,
    trade_amount NUMERIC,
    platform_fee NUMERIC,
    ambassador_share NUMERIC,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 9. جدول اختلافات (Disputes)
CREATE TABLE IF NOT EXISTS disputes (
    id BIGSERIAL PRIMARY KEY,
    transaction_id TEXT,
    opened_by BIGINT,
    reason TEXT,
    verdict TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);
