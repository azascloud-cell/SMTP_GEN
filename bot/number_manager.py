"""
Number Manager — kelola daftar nomor dari file .txt dan cek status WA.

Cara kerja:
  1. User upload file .txt (satu nomor per baris)
  2. Bot pilih 3 nomor acak
  3. Cek apakah tiap nomor terdaftar di WhatsApp via WA_CHECKER_URL
  4. Tampilkan dengan tombol hijau (fresh/belum WA) atau merah (sudah WA)

WA Checker:
  Set env var WA_CHECKER_URL = https://your-checker.example.com
  Endpoint harus menerima: GET /check?phone=+628xxx
  dan mengembalikan JSON: {"registered": true}  atau  {"registered": false}

  Jika WA_CHECKER_URL tidak diset, bot menampilkan status "Tidak diketahui".
"""

import logging
import os
import random
import re

import requests

logger = logging.getLogger(__name__)

WA_CHECKER_URL = os.environ.get("WA_CHECKER_URL", "").rstrip("/")
TIMEOUT = 10


# ─────────────────────────────────────────────────────────────────────────────
# Phone number utilities
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(phone: str) -> str:
    """Normalise: hilangkan spasi, tanda kurung, strip; tambah + jika perlu."""
    phone = phone.strip()
    # Hapus karakter bukan digit / +
    phone = re.sub(r"[^\d+]", "", phone)
    if not phone:
        return ""
    if not phone.startswith("+"):
        phone = "+" + phone
    # Minimal 7 digit setelah '+'
    if len(phone) < 8:
        return ""
    return phone


def parse_numbers_from_text(text: str) -> list[str]:
    """
    Baca teks dari file .txt dan kembalikan daftar nomor yang valid.
    Mendukung: satu nomor per baris, dengan atau tanpa kode negara.
    """
    results = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        norm = _normalize(line)
        if norm and norm not in seen:
            seen.add(norm)
            results.append(norm)
    return results


def pick_random(numbers: list[str], count: int = 3) -> list[str]:
    """Ambil `count` nomor acak dari daftar."""
    if len(numbers) <= count:
        return numbers[:]
    return random.sample(numbers, count)


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp registration checker
# ─────────────────────────────────────────────────────────────────────────────

def is_checker_connected() -> bool:
    return bool(WA_CHECKER_URL)


def check_wa_registered(phone: str) -> bool | None:
    """
    Cek apakah nomor terdaftar di WhatsApp.
    Return:
        True  → terdaftar (merah)
        False → belum terdaftar / fresh (hijau)
        None  → tidak bisa cek (checker belum konek)
    """
    if not WA_CHECKER_URL:
        return None

    try:
        r = requests.get(
            f"{WA_CHECKER_URL}/check",
            params={"phone": phone},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            return bool(data.get("registered", False))
        logger.warning(f"WA checker returned HTTP {r.status_code} for {phone}")
        return None
    except requests.exceptions.Timeout:
        logger.warning(f"WA checker timeout for {phone}")
        return None
    except Exception as e:
        logger.warning(f"WA checker error for {phone}: {e}")
        return None


def check_numbers(phones: list[str]) -> list[dict]:
    """
    Cek daftar nomor, return list of:
      {"phone": "+628...", "registered": True/False/None}
    """
    results = []
    for phone in phones:
        registered = check_wa_registered(phone)
        results.append({"phone": phone, "registered": registered})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Button label helpers
# ─────────────────────────────────────────────────────────────────────────────

def status_emoji(registered: bool | None) -> str:
    if registered is True:
        return "🔴"   # Terdaftar WA
    if registered is False:
        return "🟢"   # Fresh / belum terdaftar
    return "⚪"       # Tidak diketahui


def status_label(registered: bool | None) -> str:
    if registered is True:
        return "Terdaftar WA"
    if registered is False:
        return "Fresh ✓"
    return "?"
