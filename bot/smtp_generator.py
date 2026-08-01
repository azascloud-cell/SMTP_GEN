"""
SMTP Generator – mengambil email sementara dari beberapa provider publik
dan mengembalikan konfigurasi SMTP / IMAP yang siap pakai.
"""

import logging
import random
import re
import string
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

SESSION = requests.Session()


def _strip_html(html: str) -> str:
    if not html:
        return ""
    # Hapus basic HTML tags
    text = re.sub(r"<[^>]+>", "", html)
    return text.strip()
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

    @staticmethod
    def get_inbox(email: str) -> list[dict]:
        if "@" not in email:
            return []
        username, domain = email.split("@", 1)
        try:
            r = SESSION.get(
                "https://www.1secmail.com/api/v1/",
                params={"action": "getMessages", "login": username, "domain": domain},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                messages = r.json()
                results = []
                for msg in messages:
                    results.append({
                        "id":      str(msg.get("id")),
                        "from":    msg.get("from", "?"),
                        "subject": msg.get("subject", "?"),
                        "date":    msg.get("date", "?"),
                    })
                return results
        except Exception as e:
            logger.warning(f"1SecMail check inbox error: {e}")
        return []

    @staticmethod
    def read_message(email: str, msg_id: str) -> dict | None:
        if "@" not in email:
            return None
        username, domain = email.split("@", 1)
        try:
            r = SESSION.get(
                "https://www.1secmail.com/api/v1/",
                params={"action": "readMessage", "login": username, "domain": domain, "id": msg_id},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                msg = r.json()
                return {
                    "from":    msg.get("from", "?"),
                    "subject": msg.get("subject", "?"),
                    "date":    msg.get("date", "?"),
                    "body":    msg.get("textBody") or _strip_html(msg.get("htmlBody", "")),
                }
        except Exception as e:
            logger.warning(f"1SecMail read message error: {e}")
        return None


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

    @staticmethod
    def get_inbox(sid_token: str) -> list[dict]:
        if not sid_token:
            return []
        try:
            r = SESSION.get(
                "https://api.guerrillamail.com/ajax.php",
                params={"f": "check_email", "sid_token": sid_token, "seq": 0},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                messages = data.get("list", [])
                results = []
                for msg in messages:
                    results.append({
                        "id":      str(msg.get("mail_id")),
                        "from":    msg.get("mail_from", "?"),
                        "subject": msg.get("mail_subject", "?"),
                        "date":    msg.get("mail_date", "?"),
                    })
                return results
        except Exception as e:
            logger.warning(f"GuerrillaMail check inbox error: {e}")
        return []

    @staticmethod
    def read_message(sid_token: str, msg_id: str) -> dict | None:
        if not sid_token:
            return None
        try:
            r = SESSION.get(
                "https://api.guerrillamail.com/ajax.php",
                params={"f": "fetch_email", "sid_token": sid_token, "email_id": msg_id},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "from":    data.get("mail_from", "?"),
                    "subject": data.get("mail_subject", "?"),
                    "date":    data.get("mail_date", "?"),
                    "body":    data.get("mail_body", ""),
                }
        except Exception as e:
            logger.warning(f"GuerrillaMail read message error: {e}")
        return None


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

    @classmethod
    def get_token(cls, email: str, password: str) -> str | None:
        try:
            r = SESSION.post(
                f"{cls.BASE}/token",
                json={"address": email, "password": password},
                timeout=TIMEOUT,
            )
            if r.status_code in (200, 201):
                return r.json().get("token")
        except Exception as e:
            logger.warning(f"Mail.tm get token error: {e}")
        return None

    @classmethod
    def get_inbox(cls, email: str, password: str) -> list[dict]:
        token = cls.get_token(email, password)
        if not token:
            return []
        try:
            r = SESSION.get(
                f"{cls.BASE}/messages",
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                messages = r.json().get("hydra:member", [])
                results = []
                for msg in messages:
                    results.append({
                        "id":      str(msg.get("id")),
                        "from":    msg.get("from", {}).get("address", "?"),
                        "subject": msg.get("subject", "?"),
                        "date":    msg.get("createdAt", "?"),
                    })
                return results
        except Exception as e:
            logger.warning(f"Mail.tm check inbox error: {e}")
        return []

    @classmethod
    def read_message(cls, email: str, password: str, msg_id: str) -> dict | None:
        token = cls.get_token(email, password)
        if not token:
            return None
        try:
            r = SESSION.get(
                f"{cls.BASE}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                msg = r.json()
                htmls = msg.get("html", [])
                html_body = htmls[0] if htmls else ""
                return {
                    "from":    msg.get("from", {}).get("address", "?"),
                    "subject": msg.get("subject", "?"),
                    "date":    msg.get("createdAt", "?"),
                    "body":    msg.get("text") or _strip_html(html_body),
                }
        except Exception as e:
            logger.warning(f"Mail.tm read message error: {e}")
        return None


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

    @staticmethod
    def check_inbox(last_email_data: dict) -> list[dict]:
        provider = last_email_data.get("provider")
        email = last_email_data.get("email", "")
        password = last_email_data.get("password", "")
        sid_token = last_email_data.get("sid_token", "")

        if provider == "1SecMail":
            return _1SecMail.get_inbox(email)
        elif provider == "GuerrillaMail":
            return _GuerrillaMail.get_inbox(sid_token)
        elif provider == "Mail.tm":
            return _MailTm.get_inbox(email, password)
        return []

    @staticmethod
    def read_message(last_email_data: dict, msg_id: str) -> dict | None:
        provider = last_email_data.get("provider")
        email = last_email_data.get("email", "")
        password = last_email_data.get("password", "")
        sid_token = last_email_data.get("sid_token", "")

        if provider == "1SecMail":
            return _1SecMail.read_message(email, msg_id)
        elif provider == "GuerrillaMail":
            return _GuerrillaMail.read_message(sid_token, msg_id)
        elif provider == "Mail.tm":
            return _MailTm.read_message(email, password, msg_id)
        return None
