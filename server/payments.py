"""Payment provider architecture.

Providers implement create_order() and verify(). Which providers are offered is
controlled by PAYMENT_PROVIDERS; a provider only activates when its credentials
are configured, so no real payment can happen until keys are supplied.

  sandbox   - simulated checkout for development / QA. No money moves. Disable
              in production by removing it from PAYMENT_PROVIDERS.
  razorpay  - India: UPI, cards, netbanking, wallets. Needs RAZORPAY_KEY_ID /
              RAZORPAY_KEY_SECRET (+ RAZORPAY_WEBHOOK_SECRET for webhooks).

Add Stripe, PayPal, Paytm, etc. by implementing the same three methods and
registering the class in PROVIDERS.
"""
import base64, hashlib, hmac, json, secrets, urllib.request

from . import config


class PaymentError(Exception):
    pass


class SandboxProvider:
    key = "sandbox"
    label = "Sandbox (test mode, no real charge)"
    methods = ["upi", "card", "netbanking"]

    def enabled(self):
        return "sandbox" in config.PAYMENT_PROVIDERS

    def public_config(self):
        return {"key": self.key, "label": self.label, "methods": self.methods, "test_mode": True}

    def create_order(self, amount_minor, currency, receipt):
        return {"order_id": "sbx_order_" + secrets.token_hex(8), "amount": amount_minor, "currency": currency}

    def verify(self, order_id, payload):
        outcome = payload.get("outcome")
        if outcome not in ("success", "failure"):
            raise PaymentError("Sandbox checkout needs outcome=success|failure")
        return {"paid": outcome == "success", "payment_id": "sbx_pay_" + secrets.token_hex(8), "method": payload.get("method", "upi")}


class RazorpayProvider:
    key = "razorpay"
    label = "UPI, cards, netbanking & wallets (Razorpay)"
    methods = ["upi", "card", "netbanking", "wallet"]

    def enabled(self):
        return "razorpay" in config.PAYMENT_PROVIDERS and bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET)

    def public_config(self):
        return {"key": self.key, "label": self.label, "methods": self.methods, "key_id": config.RAZORPAY_KEY_ID, "test_mode": config.RAZORPAY_KEY_ID.startswith("rzp_test")}

    def create_order(self, amount_minor, currency, receipt):
        auth = base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
        req = urllib.request.Request("https://api.razorpay.com/v1/orders", method="POST",
                                     data=json.dumps({"amount": amount_minor, "currency": currency, "receipt": receipt}).encode(),
                                     headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
        except Exception as e:
            raise PaymentError(f"Could not create Razorpay order: {e}")
        return {"order_id": data["id"], "amount": data["amount"], "currency": data["currency"]}

    def verify(self, order_id, payload):
        pid, sig = payload.get("razorpay_payment_id"), payload.get("razorpay_signature")
        if not (pid and sig):
            raise PaymentError("Missing Razorpay payment id or signature")
        good = hmac.new(config.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{pid}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig):
            raise PaymentError("Payment signature verification failed")
        return {"paid": True, "payment_id": pid, "method": payload.get("method")}

    @staticmethod
    def verify_webhook(body: bytes, signature: str):
        if not config.RAZORPAY_WEBHOOK_SECRET:
            return False
        good = hmac.new(config.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, signature or "")


PROVIDERS = {p.key: p for p in (SandboxProvider(), RazorpayProvider())}


def enabled_providers():
    return [p for p in PROVIDERS.values() if p.enabled()]


def get(key):
    p = PROVIDERS.get(key)
    if not p or not p.enabled():
        raise PaymentError("This payment method is not configured.")
    return p
