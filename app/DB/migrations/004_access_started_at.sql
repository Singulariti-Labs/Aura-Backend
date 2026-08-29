-- Normalize when the user's current entitlement began. This differs from a
-- promo code's valid_from timestamp: a code can stop accepting redemptions
-- while access already granted from that code remains active.

ALTER TABLE user_billing
    ADD COLUMN access_started_at TIMESTAMPTZ NULL;

-- Recover the most accurate start available for existing rows. Promo
-- redemptions have an exact redeemed_at value, Stripe subscriptions expose
-- the current billing-period start, and Free/admin records fall back to when
-- their local billing snapshot was created.
UPDATE user_billing AS billing
SET access_started_at = COALESCE(
    CASE
        WHEN billing.entitlement_source = 'promo'
            THEN (
                SELECT redemption.redeemed_at
                FROM promo_redemptions AS redemption
                WHERE redemption.id = billing.promo_redemption_id
            )
        WHEN billing.entitlement_source = 'payment'
            THEN billing.current_period_start
        ELSE billing.created_at
    END,
    billing.current_period_start,
    billing.created_at,
    NOW()
);

ALTER TABLE user_billing
    ALTER COLUMN access_started_at SET DEFAULT NOW(),
    ALTER COLUMN access_started_at SET NOT NULL;
