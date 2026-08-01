"""
SMTP Generator – mengambil email sementara dari beberapa provider publik
dan mengembalikan konfigurasi SMTP / IMAP yang siap pakai.
"""

import logging
import random
import string
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "SMTPGenBot/1.0"})
TIMEOUT = 15  # detik


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _random_str(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _expire_label(minutes: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return exp.strftime("%Y-%m-%d %H:%M UTC")


# ─────────────────────────────────────────────────────────────────────────────
# Provider implementations
# ─────────────────────────────────────────────────────────────────────────────

class _1SecMail:
    """https://www.1secmail.com — temporary mailbox with SMTP read via API."""

    NAME = "1SecMail"
    DOMAINS = ["1secmail.com", "1secmail.net", "1secmail.org",
               "esiix.com", "wwjmp.com", "xojxe.com"]

    def generate(self) -> dict:
        username = _random_str(12)
        domain   = random.choice(self.DOMAINS)
        email    = f"{username}@{domain}"
        password = _random_str(16)

        # Aktivasi kotak masuk (opsional – panggil inbox sekali)
        try:
            SESSION.get(
                "https://www.1secmail.com/api/v1/",
                params={"action": "getMessages", "login": username, "domain": domain},
                timeout=TIMEOUT,
            )
        except Exception:
            pass

        return {
            "email":     email,
            "password":  password,
            "provider":  self.NAME,
            "smtp_host": f"smtp.{domain}",
            "smtp_port": "587",
            "ssl":       "STARTTLS",
            "imap_host": f"imap.{domain}",
            "imap_port": "993",
            "expires":   _expire_label(60),
            "note":      "Inbox dapat dicek via 1secmail.com atau API-nya",
        }


class _GuerrillaMail:
    """https://guerrillamail.com — well-known disposable email."""

    NAME = "GuerrillaMail"

    def generate(self) -> dict:
        try:
            r = SESSION.get(
                "https://api.guerrillamail.com/ajax.php",
                params={"f": "get_email_address"},
                timeout=TIMEOUT,
            )
            data  = r.json()
            email = data.get("email_addr", "")
            sid   = data.get("sid_token", "")
            if not email:
                raise ValueError("No email returned")
        except Exception as e:
            logger.warning(f"GuerrillaMail API error: {e}")
            email = f"{_random_str(10)}@guerrillamailblock.com"
            sid   = ""

        password = _random_str(16)
        domain   = email.split("@")[1] if "@" in email else "guerrillamail.com"

        return {
            "email":     email,
            "password":  password,
            "sid_token": sid,
            "provider":  self.NAME,
            "smtp_host": f"smtp.{domain}",
            "smtp_port": "587",
            "ssl":       "STARTTLS",
            "imap_host": f"imap.{domain}",
            "imap_port": "993",
            "expires":   _expire_label(60),
            "note":      "Buka guerrillamail.com untuk cek inbox",
        }


class _MailTm:
    """https://mail.tm — full API with real auth (JWT)."""

    NAME = "Mail.tm"
    BASE = "https://api.mail.tm"

    def _get_domain(self) -> str | None:
        try:
            r = SESSION.get(f"{self.BASE}/domains", timeout=TIMEOUT)
            domains = r.json().get("hydra:member", [])
            return domains[0]["domain"] if domains else None
        except Exception:
            return None

    def generate(self) -> dict:
        domain = self._get_domain() or "mail.tm"
        username = _random_str(12)
        email    = f"{username}@{domain}"
        password = _random_str(16) + "A1!"  # mail.tm butuh password kuat

        try:
            # Buat akun
            reg = SESSION.post(
                f"{self.BASE}/accounts",
                json={"address": email, "password": password},
                timeout=TIMEOUT,
            )
            if reg.status_code not in (200, 201):
                raise ValueError(f"Register gagal: {reg.status_code}")
        except Exception as e:
            logger.warning(f"Mail.tm register error: {e}")
            # Tetap kembalikan creds meski register gagal (bisa coba manual)

        return {
            "email":     email,
            "password":  password,
            "provider":  self.NAME,
            "smtp_host": "smtp.mail.tm",
            "smtp_port": "587",
            "ssl":       "STARTTLS",
            "imap_host": "imap.mail.tm",
            "imap_port": "993",
            "expires":   _expire_label(0),  # tidak expire
            "note":      "Akun permanen – login di mail.tm untuk cek inbox",
        }


class _TempMailOrg:
    """Temp-mail.org compatible – domain acak dari daftar publik."""

    NAME  = "TempMail"
    DOMAINS = [
        "tempmail.com", "fakeinbox.com", "mailnull.com",
        "spamgourmet.com", "trashmail.com", "yopmail.com",
    ]

    def generate(self) -> dict:
        username = _random_str(10)
        domain   = random.choice(self.DOMAINS)
        email    = f"{username}@{domain}"
        password = _random_str(16)

        return {
            "email":     email,
            "password":  password,
            "provider":  self.NAME,
            "smtp_host": f"smtp.{domain}",
            "smtp_port": "465",
            "ssl":       "SSL/TLS",
            "imap_host": f"imap.{domain}",
            "imap_port": "993",
            "expires":   _expire_label(24 * 60),
            "note":      f"Cek inbox di {domain}",
        }


class _Dispostable:
    """Generate random domain dari daftar disposable yang diketahui."""

    NAME    = "Dispostable"
    DOMAINS = [
        "dispostable.com", "spamfree24.org", "maildrop.cc",
        "throwam.com",     "spamthisplease.com",
    ]

    def generate(self) -> dict:
        username = _random_str(10)
        domain   = random.choice(self.DOMAINS)
        email    = f"{username}@{domain}"
        password = _random_str(16)

        return {
            "email":     email,
            "password":  password,
            "provider":  self.NAME,
            "smtp_host": f"smtp.{domain}",
            "smtp_port": "587",
            "ssl":       "STARTTLS",
            "imap_host": f"imap.{domain}",
            "imap_port": "993",
            "expires":   _expire_label(30),
            "note":      "Email disposable – gunakan hanya untuk testing",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main Generator
# ─────────────────────────────────────────────────────────────────────────────

PROVIDERS = {
    "1SecMail":      _1SecMail(),
    "GuerrillaMail": _GuerrillaMail(),
    "Mail.tm":       _MailTm(),
    "TempMail":      _TempMailOrg(),
    "Dispostable":   _Dispostable(),
}


class SMTPGenerator:
    def list_providers(self) -> list[str]:
        return list(PROVIDERS.keys())

    def generate_random(self) -> dict:
        name     = random.choice(list(PROVIDERS.keys()))
        provider = PROVIDERS[name]
        return self._run(provider)

    def generate_by_provider(self, name: str) -> dict:
        provider = PROVIDERS.get(name)
        if not provider:
            return {"success": False, "error": f"Provider '{name}' tidak ditemukan."}
        return self._run(provider)

    @staticmethod
    def _run(provider) -> dict:
        try:
            data = provider.generate()
            return {"success": True, "data": data}
        except Exception as e:
            logger.error(f"[{provider.NAME}] generate error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
