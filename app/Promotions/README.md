# Promotional plan access

Promotions are the active plan-acquisition method while `BILLING_MODE=promo`.
They grant Mini, Pro, or Max without calling a payment provider. Normal agent
requests continue to use the cached `rate_limits` row and never validate a
promo code.

## Configuration

Set a private random value containing at least 32 characters:

```env
BILLING_MODE="promo"
PROMO_CODE_PEPPER="replace-with-a-long-random-secret"
```

Changing the pepper makes all existing codes impossible to look up, so store
it in the same secret manager and backup policy as other application secrets.

## Generate a code

Run the internal CLI after configuring the database and pepper:

```powershell
python -m app.Promotions.admin_cli --plan pro --duration-days 90 --max-redemptions 1 --valid-for-days 30 --label "beta-user"
```

The command above generates a secure code automatically. To choose the code,
pass `--code`; it must contain 16 to 128 letters or numbers after spaces and
hyphens are removed:

```powershell
python -m app.Promotions.admin_cli --plan max --code "AURA-FOUNDERS-2026-ACCESS" --duration-days 90 --max-redemptions 5 --valid-for-days 30 --label "founders"
```

Custom codes are normalized for redemption, so capitalization, spaces, and
hyphens do not create different codes. Reusing an existing code is rejected.
Automatically generated codes are safer when a memorable code is unnecessary.

Use `0` for permanent access, unlimited redemptions, or no redemption deadline.
The plaintext code is printed once. The database stores only its HMAC-SHA256
hash and a masked hint.

## Redeem a code

Authenticated endpoint:

```http
POST /api/promotions/redeem
Authorization: Bearer <auth0-token>
Content-Type: application/json

{"code":"PRO-ABCDE-FGHIJ-KLMNP-QRSTU-23456"}
```

Successful response:

```json
{
  "plan_code": "pro",
  "previous_plan_code": "free",
  "entitlement_source": "promo",
  "access_started_at": "2026-08-20T00:00:00Z",
  "access_expires_at": "2026-11-18T00:00:00Z",
  "usage_limit_usd": "10.000000",
  "window_spent_usd": "1.250000",
  "message": "Pro plan activated"
}
```

The transaction preserves current 12-hour spend. It updates `promo_codes`,
`promo_redemptions`, `user_billing`, `users`, and `rate_limits` together.

## Entitlement rules

- Payment and admin entitlements cannot be overwritten by a promo.
- One active promo is allowed per user.
- A higher promotional tier can replace a lower promotional tier.
- The same or a lower promotional tier is rejected while access is active.
- Each user can redeem a particular code only once.
- Expired access returns to Free without resetting the 12-hour usage window.
- A permanent promotion has `access_expires_at = null`.

Promo codes must never be logged, placed in URLs, committed to source control,
or stored in plaintext.
