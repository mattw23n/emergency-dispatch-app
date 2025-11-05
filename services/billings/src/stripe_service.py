"""Stripe payment service for processing medical billing payments."""

import os
from pathlib import Path

import stripe
from dotenv import load_dotenv

# Load Stripe API key from environment variable
# Resolve dev.env relative to this file's location
# Navigate up to project root
project_root = Path(__file__).resolve().parents[2]
env_path = project_root / "dev.env"
load_dotenv(dotenv_path=env_path)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
if not stripe.api_key:
    raise RuntimeError(
        f"STRIPE_SECRET_KEY not found in environment. Tried loading from: {env_path}"
    )


def process_stripe_payment(
    amount, currency="usd", description="Medical billing payment"
):
    """Process payment using Stripe API with test tokens.

    Uses Stripe's test environment with test tokens instead of raw card data.
    """
    try:
        # Convert amount to cents for Stripe
        amount_cents = int(float(amount) * 100)

        print(f"[STRIPE] Processing payment of {amount} {currency} for: {description}")

        # Create a payment intent with a test token
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency.lower(),
            payment_method_types=["card"],
            payment_method_data={
                "type": "card",
                # Test token that always succeeds
                "card": {"token": "tok_visa"},
            },
            confirm=True,
            description=description,
            metadata={"test_transaction": "true", "description": description},
        )

        print(f"[STRIPE] Payment processed successfully. PaymentIntent: {intent.id}")

        return {
            "success": True,
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
        }

    except Exception as e:
        error_msg = f"Payment processing error: {str(e)}"
        print(f"[ERROR] {error_msg}")
        return {
            "success": False,
            "error": error_msg,
            "client_secret": None,
            "payment_intent_id": None,
        }
