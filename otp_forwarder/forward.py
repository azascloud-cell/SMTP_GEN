import logging
import os
import time

import requests

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("OTPForwarder")

# ── Configuration ────────────────────────────────────────────────────────────
# Replace these with your values, or set them as environment variables (recommended for GitHub Actions)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "YOUR_CHAT_ID_HERE"
XCLUSOR_API_KEY = os.environ.get("XCLUSOR_API_KEY") or "YOUR_API_KEY_HERE"

# API Endpoint (Can be overridden via env var)
API_URL = os.environ.get("XCLUSOR_API_URL") or "https://api.sms-activate.org/steward.php"

# Set to keep track of processed message signatures
processed_activations = set()

def send_telegram_message(text: str):
    """Send a message to the specified Telegram group or channel."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.warning("BOT_TOKEN is not configured.")
        return
    if not CHAT_ID or CHAT_ID == "YOUR_CHAT_ID_HERE":
        logger.warning("CHAT_ID is not configured.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram notification sent successfully!")
        else:
            logger.error(f"Telegram API returned HTTP {r.status_code}: {r.text}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to send Telegram message: {e}")

def fetch_active_activations():
    """Fetch all active/purchased numbers and incoming OTP messages."""
    if not XCLUSOR_API_KEY or XCLUSOR_API_KEY == "YOUR_API_KEY_HERE":
        logger.warning("XCLUSOR_API_KEY is not configured.")
        return []

    # Standard steward API request to get active activations
    params = {
        "api_key": XCLUSOR_API_KEY,
        "action": "getActiveActivations"
    }
    try:
        r = requests.get(API_URL, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data.get("activeActivations", [])
            elif isinstance(data, list):
                return data
            return []
        else:
            logger.error(f"API returned HTTP {r.status_code}: {r.text}")
            return []
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to fetch activations from API: {e}")
        return []

def main():
    logger.info("🚀 Starting XclusoR OTP Forwarder Bot...")
    send_telegram_message("🚀 *XclusoR OTP Forwarder Bot Aktif!* 🛫\nBot siap meneruskan kode OTP ke chat ini secara real-time.")

    while True:
        try:
            activations = fetch_active_activations()
            for act in activations:
                # Extract activation details
                act_id = act.get("id") or act.get("activationId")
                phone = act.get("phone") or act.get("phoneNumber")
                service = act.get("service") or act.get("serviceName", "Unknown")
                sms_text = act.get("smsText") or act.get("smsCode") or act.get("smsText", "")

                if not act_id or not phone:
                    continue

                # If we have an incoming SMS text and we haven't processed this activation message yet
                if sms_text:
                    sig = f"{act_id}_{sms_text}"
                    if sig not in processed_activations:
                        processed_activations.add(sig)

                        # Format dynamic message
                        msg = (
                            f"🛫 *OTP DITERIMA!* 🚀\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📱 *Nomor:* `{phone}`\n"
                            f"🔑 *Layanan:* `{service.upper()}`\n"
                            f"💬 *Pesan SMS:*\n"
                            f"```{sms_text}```\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📅 *Waktu:* {time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        send_telegram_message(msg)

        except Exception as e:  # noqa: BLE001
            logger.error(f"Main loop error: {e}")

        # Poll every 5 seconds
        time.sleep(5)

if __name__ == "__main__":
    main()
