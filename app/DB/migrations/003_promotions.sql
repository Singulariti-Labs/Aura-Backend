-- Promotional plan access for the payment-free launch phase.
-- Plain promo codes are never stored: the application looks them up by a
-- keyed HMAC-SHA256 hash generated with PROMO_CODE_PEPPER.

CREATE TABLE promo_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash CHAR(64) NOT NULL UNIQUE,
    code_hint TEXT NOT NULL,
    plan_code TEXT NOT NULL REFERENCES subscription_plans(code),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ NULL,
    access_duration_days INTEGER NULL
        CHECK (access_duration_days IS NULL OR access_duration_days > 0),
    max_redemptions INTEGER NULL
        CHECK (max_redemptions IS NULL OR max_redemptions > 0),
    redemption_count INTEGER NOT NULL DEFAULT 0
        CHECK (redemption_count >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (plan_code IN ('mini', 'pro', 'max')),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (
        max_redemptions IS NULL
        OR redemption_count <= max_redemptions
    )
);

CREATE INDEX promo_codes_active_window_idx
    ON promo_codes (active, valid_from, valid_until);

CREATE TABLE promo_redemptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    promo_code_id UUID NOT NULL REFERENCES promo_codes(id) ON DELETE RESTRICT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    granted_plan_code TEXT NOT NULL REFERENCES subscription_plans(code),
    previous_plan_code TEXT NOT NULL REFERENCES subscription_plans(code),
    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    access_expires_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expired', 'revoked')),
    revoked_at TIMESTAMPTZ NULL,
    UNIQUE (promo_code_id, user_id)
);

CREATE INDEX promo_redemptions_user_time_idx
    ON promo_redemptions (user_id, redeemed_at DESC);

-- Only one promotional entitlement may be active for a user. A higher-tier
-- code can replace the current promotion after that record is revoked.
CREATE UNIQUE INDEX promo_redemptions_one_active_per_user_uidx
    ON promo_redemptions (user_id)
    WHERE status = 'active';

ALTER TABLE user_billing
    ADD COLUMN entitlement_source TEXT NOT NULL DEFAULT 'free'
        CHECK (entitlement_source IN ('free', 'promo', 'payment', 'admin')),
    ADD COLUMN promo_redemption_id UUID NULL
        REFERENCES promo_redemptions(id) ON DELETE SET NULL;

-- Preserve any already-synchronized paid Stripe entitlement if this migration
-- is applied after payments have been tested.
UPDATE user_billing
SET entitlement_source = 'payment'
WHERE stripe_subscription_id IS NOT NULL
  AND plan_code <> 'free';

ALTER TABLE rate_limits
    ADD COLUMN plan_expires_at TIMESTAMPTZ NULL;

CREATE INDEX rate_limits_plan_expiry_idx
    ON rate_limits (plan_expires_at)
    WHERE plan_expires_at IS NOT NULL;
