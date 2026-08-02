"""
SMTP Auto Generator — generate akun SMTP/API nyata via backend
tanpa perlu input manual dari user.

Strategi:
1. MailerSend API  → Kirim email via MailerSend API (butuh MAILERSEND_API_KEY)
2. Mail.tm API      → daftar akun gratis, dapat SMTP credentials (tanpa token)
"""

import logging
import os
import random
import string

import requests

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "SMTPGenBot/2.0"})
TIMEOUT = 15

MAILERSEND_API_KEY = os.environ.get("MAILERSEND_API_KEY", "")
MAILERSEND_SENDER_EMAIL = os.environ.get("MAILERSEND_SENDER_EMAIL", "")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rand(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ─────────────────────────────────────────────────────────────────────────────
# Provider 1: MailerSend API
# ─────────────────────────────────────────────────────────────────────────────

def gen_mailersend_smtp() -> dict:
    """
    Generate MailerSend SMTP/API credentials from environment variables.
    """
    if not MAILERSEND_API_KEY or not MAILERSEND_SENDER_EMAIL:
        return {
            "success": False,
            "error": "MAILERSEND_API_KEY atau MAILERSEND_SENDER_EMAIL belum diset di env."
        }

    return {
        "success": True,
        "provider": "MailerSend",
        "key": f"mailersend:{MAILERSEND_SENDER_EMAIL}",
        "username": MAILERSEND_SENDER_EMAIL,
        "password": MAILERSEND_API_KEY,
        "smtp_host": "api.mailersend.com",
        "smtp_port": 443,
        "imap_host": "api.mailersend.com",
        "imap_port": 443,
        "note": f"MailerSend API (Sender: {MAILERSEND_SENDER_EMAIL})",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Provider 2: Mail.tm (free, no token needed)
# ─────────────────────────────────────────────────────────────────────────────

def gen_mailtm_smtp() -> dict:
    """
    Buat akun Mail.tm baru via API publik.
    Mail.tm mendukung IMAP SSL, cocok untuk monitor reply.
    """
    BASE = "https://api.mail.tm"

    try:
        # Ambil domain aktif
        r = SESSION.get(f"{BASE}/domains", timeout=TIMEOUT)
        if r.status_code != 200:
            return {"success": False, "error": f"Gagal ambil domain mail.tm: HTTP {r.status_code}"}
        
        domains = r.json().get("hydra:member", [])
        if not domains:
            return {"success": False, "error": "Tidak ada domain tersedia di mail.tm."}
        
        domain   = domains[0]["domain"]
        username = _rand(12)
        email    = f"{username}@{domain}"
        password = _rand(10) + "A1!"  # mail.tm butuh password kuat

        # Daftar akun
        r2 = SESSION.post(
            f"{BASE}/accounts",
            json={"address": email, "password": password},
            timeout=TIMEOUT,
        )
        if r2.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Gagal daftar mail.tm: HTTP {r2.status_code} — {r2.text[:200]}",
            }

        # Ambil token JWT untuk verifikasi
        r3 = SESSION.post(
            f"{BASE}/token",
            json={"address": email, "password": password},
            timeout=TIMEOUT,
        )
        jwt_ok = r3.status_code == 200

        return {
            "success":   True,
            "provider":  "Mail.tm",
            "key":       email,
            "username":  email,
            "password":  password,
            "smtp_host": "smtp.mail.tm",
            "smtp_port": 587,
            "imap_host": "imap.mail.tm",
            "imap_port": 993,
            "note":      f"Akun mail.tm — inbox di mail.tm (JWT login {'✅' if jwt_ok else '⚠️'})",
        }

    except Exception as e:  # noqa: BLE001
        logger.error(f"gen_mailtm_smtp error: {e}")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Provider 3: Guerrilla Mail (email saja, SMTP tidak real — placeholder)
# ─────────────────────────────────────────────────────────────────────────────

def gen_guerrilla_smtp() -> dict:
    """Generate email GuerrillaMail. SMTP tidak tersedia — hanya untuk receive."""
    try:
        r = SESSION.get(
            "https://api.guerrillamail.com/ajax.php",
            params={"f": "get_email_address"},
            timeout=TIMEOUT,
        )
        data  = r.json()
        email = data.get("email_addr", f"{_rand(10)}@guerrillamail.com")
        sid   = data.get("sid_token", "")
        domain = email.split("@")[1] if "@" in email else "guerrillamail.com"
        password = _rand(16)
        return {
            "success":   True,
            "provider":  "GuerrillaMail",
            "key":       email,
            "username":  email,
            "password":  password,
            "sid_token": sid,
            "smtp_host": f"smtp.{domain}",
            "smtp_port": 587,
            "imap_host": f"imap.{domain}",
            "imap_port": 993,
            "note":      "Guerrilla Mail — cek inbox di guerrillamail.com",
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Auto-select best available provider
# ─────────────────────────────────────────────────────────────────────────────

BACKEND_PROVIDERS = {
    "mailersend": gen_mailersend_smtp,
    "mailtm":   gen_mailtm_smtp,
}


def auto_gen_smtp(provider: str = "auto") -> dict:
    """
    Generate SMTP otomatis. Pilih provider:
      'auto'        → MailerSend jika token ada, fallback Mail.tm
      'mailersend'  → MailerSend API (butuh MAILERSEND_API_KEY)
      'mailtm'      → Mail.tm (gratis, tanpa token)
    """
    if provider == "auto":
        if MAILERSEND_API_KEY and MAILERSEND_SENDER_EMAIL:
            result = gen_mailersend_smtp()
            if result["success"]:
                return result
            logger.warning(f"MailerSend gagal, fallback Mail.tm: {result.get('error')}")
        return gen_mailtm_smtp()

    fn = BACKEND_PROVIDERS.get(provider)
    if not fn:
        return {"success": False, "error": f"Provider '{provider}' tidak dikenal."}
    return fn()
