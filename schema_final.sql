-- 1. جدول اصلی کاربران
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY, -- Telegram ID
    username TEXT,
    full_name TEXT,
    first_name TEXT, -- Added for compatibility
    last_name TEXT,  -- Added for compatibility
    phone_number TEXT,
    wallet_balance NUMERIC DEFAULT 0,
    role TEXT DEFAULT 'user',
    is_verified BOOLEAN DEFAULT FALSE,
    national_id TEXT,
    invited_by BIGINT REFERENCES users(id),
    payment_cards JSONB DEFAULT '[]',
    is_blacklisted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 2. جدول جدید برای احراز هویت
CREATE TABLE IF NOT EXISTS user_credentials (
    telegram_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    phone_number TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 3. جدول مدیریت وضعیت (FSM)
CREATE TABLE IF NOT EXISTS user_states (
    id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT,
    data JSONB DEFAULT '{}',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 4. جدول سفیران (Ambassadors)
CREATE TABLE IF NOT EXISTS ambassadors (
    telegram_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
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
    buyer_id BIGINT REFERENCES users(id),
    seller_id BIGINT REFERENCES users(id),
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
    user_id BIGINT REFERENCES users(id),
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
    ambassador_id BIGINT REFERENCES users(id),
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
    opened_by BIGINT REFERENCES users(id),
    reason TEXT,
    verdict TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);
