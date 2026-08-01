"""
SMTP Auto Generator — generate akun SMTP nyata via backend API
tanpa perlu input manual dari user.

Strategi:
1. Mailtrap API  → buat inbox baru, ambil SMTP credentials (butuh API token)
2. Mail.tm API   → daftar akun gratis, dapat SMTP credentials (tanpa token)
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

MAILTRAP_API_TOKEN = os.environ.get("MAILTRAP_API_TOKEN", "")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rand(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ─────────────────────────────────────────────────────────────────────────────
# Provider 1: Mailtrap API
# ─────────────────────────────────────────────────────────────────────────────

def gen_mailtrap_inbox(api_token: str = "") -> dict:
    """
    Buat inbox Mailtrap baru via API, return SMTP credentials.
    Butuh MAILTRAP_API_TOKEN di env atau di-pass langsung.
    
    Docs: https://api-docs.mailtrap.io/docs/mailtrap-api-docs/
    """
    token = api_token or MAILTRAP_API_TOKEN
    if not token:
        return {"success": False, "error": "MAILTRAP_API_TOKEN tidak diset di env."}

    inbox_name = f"bot-{_rand(8)}"

    try:
        # Step 1: Ambil daftar account (ambil account ID pertama)
        r = SESSION.get(
            "https://mailtrap.io/api/v1/accounts",
            headers={"Api-Token": token},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return {"success": False, "error": f"Gagal ambil accounts: HTTP {r.status_code}"}
        
        accounts = r.json()
        if not accounts:
            return {"success": False, "error": "Tidak ada account Mailtrap ditemukan."}
        
        account_id = accounts[0].get("id")
        if not account_id:
            return {"success": False, "error": "account_id tidak ditemukan di response."}

        # Step 2: Buat inbox baru
        r2 = SESSION.post(
            f"https://mailtrap.io/api/v1/accounts/{account_id}/inboxes",
            headers={"Api-Token": token, "Content-Type": "application/json"},
            json={"inbox": {"name": inbox_name}},
            timeout=TIMEOUT,
        )
        if r2.status_code not in (200, 201):
            return {"success": False, "error": f"Gagal buat inbox: HTTP {r2.status_code} — {r2.text[:200]}"}

        inbox = r2.json()
        username = inbox.get("username", "")
        password = inbox.get("password", "")
        domain   = inbox.get("domain", "sandbox.smtp.mailtrap.io")
        inbox_id = inbox.get("id", "?")

        if not username or not password:
            return {"success": False, "error": "Username/password tidak ada di response Mailtrap."}

        return {
            "success":   True,
            "provider":  "Mailtrap",
            "key":       f"mailtrap:{username}",
            "username":  username,
            "password":  password,
            "smtp_host": domain,
            "smtp_port": 2525,
            "imap_host": domain.replace("smtp", "imap"),
            "imap_port": 993,
            "inbox_name": inbox_name,
            "inbox_id":   str(inbox_id),
            "note":      f"Inbox '{inbox_name}' di mailtrap.io/inboxes/{inbox_id}",
        }

    except Exception as e:
        logger.error(f"gen_mailtrap_inbox error: {e}")
        return {"success": False, "error": str(e)}


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

    except Exception as e:
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
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Auto-select best available provider
# ─────────────────────────────────────────────────────────────────────────────

BACKEND_PROVIDERS = {
    "mailtrap": gen_mailtrap_inbox,
    "mailtm":   gen_mailtm_smtp,
}


def auto_gen_smtp(provider: str = "auto") -> dict:
    """
    Generate SMTP otomatis. Pilih provider:
      'auto'     → Mailtrap jika token ada, fallback Mail.tm
      'mailtrap' → Mailtrap API (butuh MAILTRAP_API_TOKEN)
      'mailtm'   → Mail.tm (gratis, tanpa token)
    """
    if provider == "auto":
        if MAILTRAP_API_TOKEN:
            result = gen_mailtrap_inbox()
            if result["success"]:
                return result
            logger.warning(f"Mailtrap gagal, fallback Mail.tm: {result.get('error')}")
        return gen_mailtm_smtp()

    fn = BACKEND_PROVIDERS.get(provider)
    if not fn:
        return {"success": False, "error": f"Provider '{provider}' tidak dikenal."}
    return fn()
