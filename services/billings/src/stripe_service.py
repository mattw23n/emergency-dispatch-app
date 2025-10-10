"""Stripe payment service for processing medical billing payments."""

import os
from pathlib import Path

import stripe
from dotenv import load_dotenv

# Load Stripe API key from environment variable
# Resolve dev.env relative to this file's location
project_root = Path(__file__).resolve().parents[
    2
]  # Navigate up to project root
env_path = project_root / "dev.env"
load_dotenv(dotenv_path=env_path)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
if not stripe.api_key:
    raise RuntimeError(
        "STRIPE_SECRET_KEY not found in environment. "
        f"Tried loading from: {env_path}"
    )


def process_stripe_payment(
    amount, currency="usd", description="Medical billing payment"
):
    """Create a Stripe PaymentIntent and return the client secret.

    Amount is expected in dollars; Stripe uses cents internally.
    """
    try:
        # Stripe expects amount in the smallest currency unit (e.g., cents)
        amount_cents = int(float(amount) * 100)

        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency,
            description=description,
            automatic_payment_methods={"enabled": True},
        )

        return {
            "success": True,
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
