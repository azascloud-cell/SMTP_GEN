"""
SMTP Manager – simpan, verifikasi, dan kelola akun SMTP nyata
(Gmail dengan App Password, atau provider lain yang mendukung)
"""

import imaplib
import logging
import smtplib
from pathlib import Path

from storage import load as _gh_load
from storage import save as _gh_save

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "smtp_accounts.json"


PROVIDER_PRESETS = {
    "gmail.com": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "note": "Gunakan App Password dari https://myaccount.google.com/apppasswords",
    },
    "yahoo.com": {
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "note": "Aktifkan App Password di keamanan akun Yahoo",
    },
    "outlook.com": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "note": "Gunakan password Outlook biasa",
    },
    "hotmail.com": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "note": "Gunakan password Hotmail biasa",
    },
}

# ─── Mailtrap & Mailpit presets ───────────────────────────────────────────────
MAILTRAP_PRESET = {
    "smtp_host": "sandbox.smtp.mailtrap.io",
    "smtp_port": 2525,
    "imap_host": "sandbox.imap.mailtrap.io",
    "imap_port": 993,
    "provider":  "Mailtrap",
    "note":      "Dapatkan credentials di mailtrap.io → Inboxes → SMTP Settings",
}

# Mailpit default — bisa di-override user dengan host:port sendiri
MAILPIT_DEFAULT = {
    "smtp_host": "localhost",
    "smtp_port": 1025,
    "imap_host": "localhost",
    "imap_port": 1143,
    "provider":  "Mailpit",
    "note":      "Mailpit self-hosted – no auth by default",
}

DEFAULT_PRESET = {
    "smtp_host": "smtp.{domain}",
    "smtp_port": 587,
    "imap_host": "imap.{domain}",
    "imap_port": 993,
    "note": "Konfigurasi otomatis – pastikan SMTP aktif di provider",
}


# ─────────────────────────────────────────────────────────────────────────────
def _load() -> dict:
    return _gh_load(DATA_FILE)


def _save(data: dict):
    _gh_save(DATA_FILE, data)


# ─────────────────────────────────────────────────────────────────────────────
def get_preset(email: str) -> dict:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    preset = PROVIDER_PRESETS.get(domain, DEFAULT_PRESET).copy()
    # Isi placeholder domain
    for key, val in preset.items():
        if isinstance(val, str):
            preset[key] = val.replace("{domain}", domain)
    return preset


def verify_smtp_plain(host: str, port: int, username: str, password: str, timeout: int = 10) -> dict:
    """Coba koneksi SMTP tanpa perlu email — untuk Mailtrap/Mailpit."""
    try:
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.ehlo()
        # Coba STARTTLS dulu, kalau gagal coba plain
        try:
            server.starttls()
            server.ehlo()
        except Exception:  # noqa: BLE001, S110
            pass
        if username and password:
            server.login(username, password)
        server.quit()
        return {"success": True, "method": "SMTP"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Login ditolak. Periksa username/password."}
    except smtplib.SMTPConnectError as e:
        return {"success": False, "error": f"Tidak bisa connect ke {host}:{port} – {e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def verify_smtp(email: str, password: str, host: str, port: int, timeout: int = 10) -> dict:
    """Coba koneksi SMTP STARTTLS ke server."""
    try:
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(email, password)
        server.quit()
        return {"success": True, "method": "SMTP STARTTLS"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Login ditolak. Periksa email/password atau aktifkan App Password."}
    except smtplib.SMTPConnectError as e:
        return {"success": False, "error": f"Tidak bisa connect ke {host}:{port} – {e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def verify_imap(email: str, password: str, host: str, port: int, timeout: int = 10) -> dict:
    """Coba koneksi IMAP SSL."""
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(email, password)
        conn.logout()
        return {"success": True, "method": "IMAP SSL"}
    except imaplib.IMAP4.error as e:
        return {"success": False, "error": f"IMAP auth gagal: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
class SMTPManager:

    def list_accounts(self, chat_id: int = None) -> list[dict]:
        data = _load()
        if chat_id is not None:
            user_data = data.get(str(chat_id), {})
        else:
            user_data = data

        result = []
        for email, info in user_data.items():
            if isinstance(info, dict):
                result.append({
                    "email":     email,
                    "provider":  info.get("provider", "?"),
                    "smtp_host": info.get("smtp_host", "?"),
                    "smtp_port": info.get("smtp_port", "?"),
                    "verified":  info.get("verified", False),
                })
        return result

    def add_account(self, email: str, password: str, chat_id: int = None) -> dict:
        """Tambah akun SMTP baru, verifikasi dulu sebelum simpan."""
        email   = email.strip().lower()
        password = password.strip()

        if "@" not in email:
            return {"success": False, "error": "Format email tidak valid."}
        if len(password) < 8:
            return {"success": False, "error": "Password terlalu pendek (min 8 karakter)."}

        preset = get_preset(email)
        domain = email.split("@")[-1]

        # Verifikasi SMTP
        smtp_result = verify_smtp(
            email, password,
            preset["smtp_host"], preset["smtp_port"],
        )
        if not smtp_result["success"]:
            return {
                "success": False,
                "step": "SMTP",
                "error": smtp_result["error"],
                "hint": preset.get("note", ""),
                "tried_host": f"{preset['smtp_host']}:{preset['smtp_port']}",
            }

        # Verifikasi IMAP (opsional – tidak gagalkan add jika IMAP tidak bisa)
        imap_result = verify_imap(
            email, password,
            preset["imap_host"], preset["imap_port"],
        )

        # Simpan akun
        data = _load()
        account_info = {
            "password":   password,
            "provider":   domain,
            "smtp_host":  preset["smtp_host"],
            "smtp_port":  preset["smtp_port"],
            "imap_host":  preset["imap_host"],
            "imap_port":  preset["imap_port"],
            "verified":   True,
            "imap_ok":    imap_result["success"],
        }

        if chat_id is not None:
            chat_id_str = str(chat_id)
            if chat_id_str not in data or not isinstance(data[chat_id_str], dict):
                data[chat_id_str] = {}
            data[chat_id_str][email] = account_info
        else:
            data[email] = account_info

        _save(data)

        return {
            "success":  True,
            "email":    email,
            "smtp_ok":  True,
            "imap_ok":  imap_result["success"],
            "smtp_host": preset["smtp_host"],
            "smtp_port": preset["smtp_port"],
            "imap_host": preset["imap_host"],
            "imap_port": preset["imap_port"],
            "note":     preset.get("note", ""),
        }

    def add_mailtrap(self, username: str, password: str, chat_id: int = None) -> dict:
        """Tambah akun Mailtrap SMTP (sandbox testing)."""
        username = username.strip()
        password = password.strip()
        if not username or not password:
            return {"success": False, "error": "Username dan password tidak boleh kosong."}

        preset = MAILTRAP_PRESET.copy()
        host, port = preset["smtp_host"], preset["smtp_port"]

        smtp_result = verify_smtp_plain(host, port, username, password)
        if not smtp_result["success"]:
            return {
                "success": False,
                "step": "SMTP",
                "error": smtp_result["error"],
                "hint": preset["note"],
                "tried_host": f"{host}:{port}",
            }

        # Simpan dengan key = username (bukan email)
        key = f"mailtrap:{username}"
        data = _load()
        account_info = {
            "password":   password,
            "username":   username,
            "provider":   "Mailtrap",
            "smtp_host":  host,
            "smtp_port":  port,
            "imap_host":  preset["imap_host"],
            "imap_port":  preset["imap_port"],
            "verified":   True,
            "imap_ok":    True,
        }

        if chat_id is not None:
            chat_id_str = str(chat_id)
            if chat_id_str not in data or not isinstance(data[chat_id_str], dict):
                data[chat_id_str] = {}
            data[chat_id_str][key] = account_info
        else:
            data[key] = account_info

        _save(data)

        return {
            "success":   True,
            "email":     key,
            "smtp_ok":   True,
            "imap_ok":   True,
            "smtp_host": host,
            "smtp_port": port,
            "imap_host": preset["imap_host"],
            "imap_port": preset["imap_port"],
            "note":      preset["note"],
        }

    def add_mailpit(self, host: str, port: int, username: str = "", password: str = "", chat_id: int = None) -> dict:
        """Tambah akun Mailpit SMTP (self-hosted testing)."""
        host     = host.strip()
        username = username.strip()
        password = password.strip()
        if not host:
            return {"success": False, "error": "Host tidak boleh kosong."}

        smtp_result = verify_smtp_plain(host, port, username, password)
        if not smtp_result["success"]:
            return {
                "success": False,
                "step": "SMTP",
                "error": smtp_result["error"],
                "hint": f"Pastikan Mailpit berjalan di {host}:{port}",
                "tried_host": f"{host}:{port}",
            }

        key = f"mailpit:{host}:{port}"
        data = _load()
        account_info = {
            "password":   password,
            "username":   username,
            "provider":   "Mailpit",
            "smtp_host":  host,
            "smtp_port":  port,
            "imap_host":  host,
            "imap_port":  MAILPIT_DEFAULT["imap_port"],
            "verified":   True,
            "imap_ok":    False,
        }

        if chat_id is not None:
            chat_id_str = str(chat_id)
            if chat_id_str not in data or not isinstance(data[chat_id_str], dict):
                data[chat_id_str] = {}
            data[chat_id_str][key] = account_info
        else:
            data[key] = account_info

        _save(data)

        return {
            "success":   True,
            "email":     key,
            "smtp_ok":   True,
            "imap_ok":   False,
            "smtp_host": host,
            "smtp_port": port,
            "imap_host": host,
            "imap_port": MAILPIT_DEFAULT["imap_port"],
            "note":      f"Mailpit di {host}:{port}",
        }

    def remove_account(self, email: str, chat_id: int = None) -> dict:
        email = email.strip().lower()
        data  = _load()

        if chat_id is not None:
            chat_id_str = str(chat_id)
            user_data = data.get(chat_id_str, {})
            if email not in user_data:
                return {"success": False, "error": f"Akun `{email}` tidak ditemukan."}
            del user_data[email]
            data[chat_id_str] = user_data
        else:
            if email not in data:
                return {"success": False, "error": f"Akun `{email}` tidak ditemukan."}
            del data[email]

        _save(data)
        return {"success": True, "email": email}

    def get_account(self, email: str, chat_id: int = None) -> dict | None:
        data = _load()
        email_key = email.strip().lower()

        if chat_id is not None:
            chat_id_str = str(chat_id)
            user_data = data.get(chat_id_str, {})
            acc = user_data.get(email_key)
            if not acc:
                # Fallback to top-level if not found in user-scoped
                acc = data.get(email_key)
        else:
            acc = data.get(email_key)

        if acc and isinstance(acc, dict):
            acc["email"] = email
            return acc
        return None

    def add_auto_generated(self, gen_result: dict, chat_id: int = None) -> dict:
        """
        Simpan akun SMTP hasil auto-generate (dari smtp_auto_generator).
        Tidak perlu verifikasi ulang — credentials sudah valid dari provider.
        """
        if not gen_result.get("success"):
            return {"success": False, "error": gen_result.get("error", "Generate gagal.")}

        key      = gen_result.get("key", gen_result.get("username", ""))
        if not key:
            return {"success": False, "error": "Key/username tidak ada di hasil generate."}

        data = _load()
        account_info = {
            "password":  gen_result.get("password", ""),
            "username":  gen_result.get("username", key),
            "provider":  gen_result.get("provider", "Auto"),
            "smtp_host": gen_result.get("smtp_host", ""),
            "smtp_port": int(gen_result.get("smtp_port", 587)),
            "imap_host": gen_result.get("imap_host", ""),
            "imap_port": int(gen_result.get("imap_port", 993)),
            "verified":  True,
            "imap_ok":   True,
            "auto_gen":  True,
            "note":      gen_result.get("note", ""),
        }

        if chat_id is not None:
            chat_id_str = str(chat_id)
            if chat_id_str not in data or not isinstance(data[chat_id_str], dict):
                data[chat_id_str] = {}
            data[chat_id_str][key] = account_info
        else:
            data[key] = account_info

        _save(data)

        return {
            "success":   True,
            "email":     key,
            "smtp_ok":   True,
            "imap_ok":   True,
            "smtp_host": gen_result.get("smtp_host", ""),
            "smtp_port": int(gen_result.get("smtp_port", 587)),
            "imap_host": gen_result.get("imap_host", ""),
            "imap_port": int(gen_result.get("imap_port", 993)),
            "note":      gen_result.get("note", ""),
        }

    def count(self, chat_id: int = None) -> int:
        data = _load()
        if chat_id is not None:
            return len(data.get(str(chat_id), {}))
        return len(data)
