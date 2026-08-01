"""
SMTP Manager – simpan, verifikasi, dan kelola akun SMTP nyata
(Gmail dengan App Password, atau provider lain yang mendukung)
"""

import json
import os
import smtplib
import imaplib
import logging
from pathlib import Path
from typing import Optional

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

DEFAULT_PRESET = {
    "smtp_host": "smtp.{domain}",
    "smtp_port": 587,
    "imap_host": "imap.{domain}",
    "imap_port": 993,
    "note": "Konfigurasi otomatis – pastikan SMTP aktif di provider",
}


# ─────────────────────────────────────────────────────────────────────────────
def _load() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
def get_preset(email: str) -> dict:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    preset = PROVIDER_PRESETS.get(domain, DEFAULT_PRESET).copy()
    # Isi placeholder domain
    for key, val in preset.items():
        if isinstance(val, str):
            preset[key] = val.replace("{domain}", domain)
    return preset


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
    except Exception as e:
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
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
class SMTPManager:

    def list_accounts(self) -> list[dict]:
        data = _load()
        result = []
        for email, info in data.items():
            result.append({
                "email":     email,
                "provider":  info.get("provider", "?"),
                "smtp_host": info.get("smtp_host", "?"),
                "smtp_port": info.get("smtp_port", "?"),
                "verified":  info.get("verified", False),
            })
        return result

    def add_account(self, email: str, password: str) -> dict:
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
        data[email] = {
            "password":   password,
            "provider":   domain,
            "smtp_host":  preset["smtp_host"],
            "smtp_port":  preset["smtp_port"],
            "imap_host":  preset["imap_host"],
            "imap_port":  preset["imap_port"],
            "verified":   True,
            "imap_ok":    imap_result["success"],
        }
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

    def remove_account(self, email: str) -> dict:
        email = email.strip().lower()
        data  = _load()
        if email not in data:
            return {"success": False, "error": f"Akun `{email}` tidak ditemukan."}
        del data[email]
        _save(data)
        return {"success": True, "email": email}

    def get_account(self, email: str) -> Optional[dict]:
        data = _load()
        acc  = data.get(email.strip().lower())
        if acc:
            acc["email"] = email
        return acc

    def count(self) -> int:
        return len(_load())
