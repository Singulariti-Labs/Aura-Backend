# Subscription integration

This package owns the plan catalog and the future Stripe Checkout, Customer
Portal, and webhook synchronization. The current launch mode is promotional
access, implemented in `app/Promotions`.
Normal agent tasks never call Stripe. Stripe events update local billing and the
existing `rate_limits` row, and `/ws` continues to make one local rate-limit
check immediately before agent execution.

Set `BILLING_MODE=promo` to disable Checkout, Portal, and Stripe webhook
processing. Promo redemption remains available at `POST /api/promotions/redeem`.

## Plans and 12-hour usage limits

| Plan | Monthly price | Platform usage per 12 hours |
|---|---:|---:|
| Free | $0 | $3 |
| Mini | $19 | $6 |
| Pro | $28 | $10 |
| Max | $100 | $25 |

Plan features are intentionally flexible JSON. The initial rows contain an
empty `included_features` list. A future example is:

```json
{
  "included_features": [
    {
      "key": "priority_queue",
      "label": "Priority task queue",
      "enabled": true
    },
    {
      "key": "realtime_stt",
      "label": "Realtime speech to text",
      "enabled": true,
      "limits": {"max_sessions": 3}
    }
  ],
  "metadata": {
    "description": "For frequent individual use"
  }
}
```

Critical enforcement values remain typed plan columns instead of JSON.

## Server endpoints

- `GET /api/subscriptions/plans` — public plan catalog.
- `GET /api/subscriptions/me` — authenticated billing, entitlement start/end,
  plan metadata, and current usage.

The account response names the entitlement boundary `access_expires_at`,
matching the promotion redemption response. The internal database column is
still `user_billing.access_until`.
- `POST /api/subscriptions/checkout-session` — authenticated paid Checkout.
- `POST /api/subscriptions/portal-session` — authenticated Customer Portal.
- `POST /api/subscriptions/webhook` — Stripe-signed webhook endpoint.

Checkout body:

```json
{
  "plan_code": "pro",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

The client sends a plan code, never an amount or trusted Stripe Price ID. Reuse
the same `request_id` only when retrying the same click/network operation.

## Client flow

1. Fetch `/api/subscriptions/plans` for the pricing UI.
2. Call `/checkout-session` and redirect to the returned `url`.
3. On the configured success page, poll `/api/subscriptions/me` until the paid
   `plan_code` appears. The redirect itself never grants access.
4. Call `/portal-session` for Manage Billing and redirect to its returned URL.
5. Refresh `/api/subscriptions/me` after returning from the portal.
6. When `/ws` returns a rate-limit payload, display its plan, limit, usage, and
   reset time; offer an upgrade button for Free/Mini/Pro users.

## Stripe Dashboard setup

Create monthly Products/Prices for Mini, Pro, and Max, configure the Customer
Portal, then subscribe the webhook endpoint to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

Set every environment variable documented in `.env.example`. Test and live
Stripe environments have different keys, Product IDs, Price IDs, and webhook
secrets.

Current payment-failure policy is intentionally conservative: `past_due`
immediately falls back to Free limits. Change this explicitly if the product
later adopts a grace period.

Stripe webhooks remain the primary entitlement update path. As a missed-event
fallback, login sync, `GET /api/subscriptions/me`, and Checkout compare a
payment-backed entitlement's cached access end with the current time. Only an
overdue entitlement is fetched directly from Stripe; renewed subscriptions
receive their new period, while canceled or unpaid subscriptions fall back to
Free. A transient Stripe read failure is logged without breaking login or the
account summary.
