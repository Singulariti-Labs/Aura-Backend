-- Subscription catalog, local Stripe state, and durable webhook inbox.
-- Existing user identifiers are TEXT in this database, so every new user_id
-- column intentionally uses TEXT as well.

CREATE TABLE subscription_plans (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    billing_interval TEXT NULL
        CHECK (billing_interval IS NULL OR billing_interval IN ('month', 'year')),
    stripe_product_id TEXT NULL,
    stripe_price_id TEXT NULL,
    usage_limit_usd NUMERIC(12, 6) NOT NULL CHECK (usage_limit_usd >= 0),
    window_hours SMALLINT NOT NULL DEFAULT 12 CHECK (window_hours > 0),
    features JSONB NOT NULL DEFAULT '{"included_features": []}'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX subscription_plans_stripe_product_uidx
    ON subscription_plans (stripe_product_id)
    WHERE stripe_product_id IS NOT NULL;

CREATE UNIQUE INDEX subscription_plans_stripe_price_uidx
    ON subscription_plans (stripe_price_id)
    WHERE stripe_price_id IS NOT NULL;

INSERT INTO subscription_plans (
    code,
    name,
    price_cents,
    currency,
    billing_interval,
    usage_limit_usd,
    window_hours,
    features
)
VALUES
    (
        'free', 'Free', 0, 'USD', NULL, 3.000000, 12,
        '{"included_features": [], "metadata": {"description": "Default plan"}}'
    ),
    (
        'mini', 'Mini', 1900, 'USD', 'month', 6.000000, 12,
        '{"included_features": [], "metadata": {"description": "Mini plan"}}'
    ),
    (
        'pro', 'Pro', 2800, 'USD', 'month', 10.000000, 12,
        '{"included_features": [], "metadata": {"description": "Pro plan"}}'
    ),
    (
        'max', 'Max', 10000, 'USD', 'month', 25.000000, 12,
        '{"included_features": [], "metadata": {"description": "Max plan"}}'
    );

ALTER TABLE users
    ADD COLUMN plan_code TEXT NOT NULL DEFAULT 'free'
        REFERENCES subscription_plans(code),
    ADD COLUMN plan_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX users_auth0_id_uidx
    ON users (auth0_id)
    WHERE auth0_id IS NOT NULL;

CREATE TABLE user_billing (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan_code TEXT NOT NULL DEFAULT 'free' REFERENCES subscription_plans(code),
    entitlement_status TEXT NOT NULL DEFAULT 'active'
        CHECK (entitlement_status IN ('active', 'grace', 'restricted')),
    stripe_customer_id TEXT NULL,
    stripe_subscription_id TEXT NULL,
    stripe_subscription_status TEXT NULL,
    stripe_product_id TEXT NULL,
    stripe_price_id TEXT NULL,
    current_period_start TIMESTAMPTZ NULL,
    current_period_end TIMESTAMPTZ NULL,
    access_until TIMESTAMPTZ NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    canceled_at TIMESTAMPTZ NULL,
    last_stripe_event_id TEXT NULL,
    last_stripe_event_created_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX user_billing_customer_uidx
    ON user_billing (stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

CREATE UNIQUE INDEX user_billing_subscription_uidx
    ON user_billing (stripe_subscription_id)
    WHERE stripe_subscription_id IS NOT NULL;

CREATE INDEX user_billing_plan_idx ON user_billing (plan_code);
CREATE INDEX user_billing_status_idx ON user_billing (stripe_subscription_status);

INSERT INTO user_billing (user_id, plan_code, entitlement_status)
SELECT id, plan_code, 'active'
FROM users
ON CONFLICT (user_id) DO NOTHING;

ALTER TABLE rate_limits
    ADD COLUMN plan_code TEXT NOT NULL DEFAULT 'free'
        REFERENCES subscription_plans(code),
    ADD COLUMN block_reason TEXT NULL
        CHECK (block_reason IS NULL OR block_reason IN ('usage_limit'));

CREATE UNIQUE INDEX rate_limits_user_uidx ON rate_limits (user_id);
CREATE UNIQUE INDEX user_token_usage_user_uidx ON user_token_usage (user_id);

-- Every existing user receives a ready-to-use Free rate-limit row.  This keeps
-- first task execution on a single read path instead of lazily creating data.
INSERT INTO rate_limits (
    id,
    user_id,
    plan_code,
    window_start,
    window_input_tokens,
    window_output_tokens,
    window_spent_usd,
    limit_usd,
    status,
    block_reason,
    updated_at
)
SELECT
    gen_random_uuid(),
    users.id,
    users.plan_code,
    NOW(),
    0,
    0,
    0,
    plans.usage_limit_usd,
    'active'::rate_limit_status,
    NULL,
    NOW()
FROM users
JOIN subscription_plans AS plans ON plans.code = users.plan_code
ON CONFLICT (user_id) DO NOTHING;

CREATE TABLE stripe_webhook_events (
    stripe_event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    stripe_object_id TEXT NULL,
    payload JSONB NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (processing_status IN ('pending', 'processing', 'processed', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    stripe_created_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_started_at TIMESTAMPTZ NULL,
    processed_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX stripe_webhook_pending_idx
    ON stripe_webhook_events (processing_status, received_at);

CREATE INDEX stripe_webhook_object_idx
    ON stripe_webhook_events (stripe_object_id, stripe_created_at);
