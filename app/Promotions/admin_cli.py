"""Internal command-line utility for securely creating promo codes."""

from __future__ import annotations

import argparse
import asyncio

from app.DB.pool import close_db_pool, init_db_pool
from app.DB.migrations import run_db_migrations
from app.Promotions.service import generate_and_store_promotion


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser with safe single-use defaults."""

    parser = argparse.ArgumentParser(description="Generate a promotional plan code")
    parser.add_argument("--plan", required=True, choices=("mini", "pro", "max"))
    parser.add_argument(
        "--code",
        default=None,
        help=(
            "Optional custom code (16-128 letters/numbers after formatting); "
            "omit to generate one securely"
        ),
    )
    parser.add_argument(
        "--duration-days",
        type=int,
        default=30,
        help="Days of plan access; use 0 for permanent access",
    )
    parser.add_argument(
        "--max-redemptions",
        type=int,
        default=1,
        help="Maximum uses; use 0 for unlimited uses",
    )
    parser.add_argument(
        "--valid-for-days",
        type=int,
        default=30,
        help="Days before the code itself expires; use 0 for no deadline",
    )
    parser.add_argument("--label", default="", help="Optional internal label")
    return parser


async def create_from_arguments(arguments: argparse.Namespace) -> None:
    """Create one database record and print its unrecoverable plaintext once."""

    pool = await init_db_pool()
    try:
        await run_db_migrations(pool)
        code, record = await generate_and_store_promotion(
            pool=pool,
            plan_code=arguments.plan,
            access_duration_days=arguments.duration_days or None,
            max_redemptions=arguments.max_redemptions or None,
            valid_for_days=arguments.valid_for_days or None,
            metadata={"label": arguments.label} if arguments.label else {},
            code=arguments.code,
        )
        print("Promo code created. Store it securely; it cannot be recovered.")
        print(f"Code: {code}")
        print(f"Plan: {record['plan_code']}")
        print(f"Access days: {record['access_duration_days'] or 'permanent'}")
        print(f"Maximum uses: {record['max_redemptions'] or 'unlimited'}")
        print(f"Redeem before: {record['valid_until'] or 'no deadline'}")
    finally:
        await close_db_pool()


def main() -> None:
    """Parse command-line arguments and execute the asynchronous generator."""

    parser = build_parser()
    try:
        asyncio.run(create_from_arguments(parser.parse_args()))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
