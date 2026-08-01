"""
cPanel Email Generator
======================
Generate akun email NYATA dengan SMTP aktif menggunakan cPanel API.
Bekerja dengan InfinityFree (gratis), Niagahoster, DomaiNesia, dan
hosting cPanel manapun yang punya API.

Variabel environment yang dibutuhkan:
  CPANEL_URL      = https://yourdomain.epizy.com:2083   (atau port 2082 untuk non-SSL)
  CPANEL_USER     = username cPanel kamu
  CPANEL_PASS     = password cPanel kamu
  CPANEL_DOMAIN   = yourdomain.epizy.com
"""

import os
import random
import string
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CPANEL_URL    = os.environ.get("CPANEL_URL", "").rstrip("/")
CPANEL_USER   = os.environ.get("CPANEL_USER", "")
CPANEL_PASS   = os.environ.get("CPANEL_PASS", "")
CPANEL_DOMAIN = os.environ.get("CPANEL_DOMAIN", "")

TIMEOUT = 20


def is_configured() -> bool:
    return all([CPANEL_URL, CPANEL_USER, CPANEL_PASS, CPANEL_DOMAIN])


def _random_username(length: int = 8) -> str:
    """Generate username acak huruf kecil + angka."""
    chars = string.ascii_lowercase + string.digits
    prefix = random.choice(string.ascii_lowercase)           # harus diawali huruf
    return prefix + "".join(random.choices(chars, k=length - 1))


def _random_password(length: int = 16) -> str:
    """Generate password kuat: huruf besar+kecil+angka+simbol."""
    lower   = string.ascii_lowercase
    upper   = string.ascii_uppercase
    digits  = string.digits
    symbols = "!@#$%^&*"
    # Pastikan minimal 1 dari masing-masing kategori
    pwd = [
        random.choice(lower),
        random.choice(upper),
        random.choice(digits),
        random.choice(symbols),
    ]
    all_chars = lower + upper + digits + symbols
    pwd += random.choices(all_chars, k=length - 4)
    random.shuffle(pwd)
    return "".join(pwd)


def _cpanel_request(endpoint: str, params: dict) -> dict:
    """Kirim request ke cPanel UAPI."""
    url  = f"{CPANEL_URL}/execute/{endpoint}"
    auth = (CPANEL_USER, CPANEL_PASS)
    try:
        r = requests.get(url, params=params, auth=auth, timeout=TIMEOUT, verify=False)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.SSLError:
        # InfinityFree kadang SSL bermasalah — coba tanpa verifikasi
        r = requests.get(url, params=params, auth=auth, timeout=TIMEOUT, verify=False)
        return r.json()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Tidak bisa connect ke cPanel ({CPANEL_URL}): {e}")
    except Exception as e:
        raise RuntimeError(f"cPanel request error: {e}")


def create_email(username: Optional[str] = None, password: Optional[str] = None,
                 quota: int = 250) -> dict:
    """
    Buat akun email baru via cPanel UAPI.
    Kembalikan dict berisi credentials + SMTP config.
    """
    if not is_configured():
        return {
            "success": False,
            "error":   "cPanel belum dikonfigurasi. Set CPANEL_URL, CPANEL_USER, CPANEL_PASS, CPANEL_DOMAIN di GitHub Secrets.",
            "setup_needed": True,
        }

    uname = username or _random_username()
    pwd   = password or _random_password()
    email = f"{uname}@{CPANEL_DOMAIN}"

    try:
        result = _cpanel_request("Email/add_pop", {
            "email":    uname,
            "password": pwd,
            "domain":   CPANEL_DOMAIN,
            "quota":    quota,
        })

        if result.get("status") != 1:
            errors = result.get("errors") or [result.get("message", "Unknown error")]
            error_msg = "; ".join(str(e) for e in errors)
            return {"success": False, "error": error_msg}

        # Tentukan mail server
        mail_host = f"mail.{CPANEL_DOMAIN}"

        return {
            "success":   True,
            "email":     email,
            "password":  pwd,
            "username":  uname,
            "domain":    CPANEL_DOMAIN,
            "smtp_host": mail_host,
            "smtp_port": "587",
            "smtp_ssl":  "STARTTLS",
            "smtp_port_ssl": "465",
            "imap_host": mail_host,
            "imap_port": "993",
            "pop3_host": mail_host,
            "pop3_port": "995",
            "webmail":   f"{CPANEL_URL.split(':2')[0]}/webmail",
            "note":      "Akun real — bisa kirim & terima email via SMTP",
            "expires":   "Tidak expire (akun permanen selama hosting aktif)",
        }

    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"create_email error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def delete_email(username: str) -> dict:
    """Hapus akun email dari cPanel."""
    if not is_configured():
        return {"success": False, "error": "cPanel belum dikonfigurasi."}
    try:
        result = _cpanel_request("Email/delete_pop", {
            "email":  username,
            "domain": CPANEL_DOMAIN,
        })
        if result.get("status") == 1:
            return {"success": True}
        errors = result.get("errors") or [result.get("message", "Unknown")]
        return {"success": False, "error": "; ".join(str(e) for e in errors)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_emails() -> dict:
    """Ambil daftar email yang sudah dibuat di cPanel."""
    if not is_configured():
        return {"success": False, "error": "cPanel belum dikonfigurasi.", "accounts": []}
    try:
        result = _cpanel_request("Email/list_pops", {"domain": CPANEL_DOMAIN})
        if result.get("status") == 1:
            accounts = result.get("data", [])
            return {
                "success":  True,
                "accounts": accounts,
                "count":    len(accounts),
                "domain":   CPANEL_DOMAIN,
            }
        return {"success": False, "error": "Gagal ambil daftar email", "accounts": []}
    except Exception as e:
        return {"success": False, "error": str(e), "accounts": []}


def test_connection() -> dict:
    """Test apakah cPanel bisa diakses."""
    if not is_configured():
        return {"success": False, "error": "cPanel belum dikonfigurasi."}
    try:
        result = _cpanel_request("Email/list_pops", {"domain": CPANEL_DOMAIN})
        if result.get("status") == 1:
            return {
                "success": True,
                "domain":  CPANEL_DOMAIN,
                "url":     CPANEL_URL,
                "accounts": len(result.get("data", [])),
            }
        return {"success": False, "error": str(result.get("errors", "Unknown"))}
    except Exception as e:
        return {"success": False, "error": str(e)}
