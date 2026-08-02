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
TIMEOUT = 30


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
        None  → tidak bisa cek (checker belum konek / error)
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
            reg = data.get("registered")
            if reg is None:
                err_msg = data.get("error", "No registration result")
                logger.warning(f"WA checker registered=null for {phone}: {err_msg}")
                return None
            return bool(reg)

        # Parse error message from non-200 responses
        try:
            err_msg = r.json().get("error", "Unknown checker error")
        except Exception:  # noqa: BLE001
            err_msg = r.text or "Unknown checker error"
        logger.warning(f"WA checker returned HTTP {r.status_code} for {phone}: {err_msg}")
        return None
    except requests.exceptions.Timeout:
        logger.warning(f"WA checker timeout for {phone}")
        return None
    except Exception as e:  # noqa: BLE001
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


# ─────────────────────────────────────────────────────────────────────────────
# Country Info & Metode Gacha
# ─────────────────────────────────────────────────────────────────────────────

COUNTRY_DATA = {
    "1": {"name": "United States/Canada", "flag": "🇺🇸", "lang": "Inggris (US)", "timezone": "America/New_York (EST/PST)"},
    "7": {"name": "Rusia", "flag": "🇷🇺", "lang": "Rusia", "timezone": "Europe/Moscow (GMT+3)"},
    "20": {"name": "Mesir", "flag": "🇪🇬", "lang": "Arab (Mesir)", "timezone": "Africa/Cairo (GMT+2)"},
    "27": {"name": "Afrika Selatan", "flag": "🇿🇦", "lang": "Inggris/Afrikaans", "timezone": "Africa/Johannesburg (GMT+2)"},
    "30": {"name": "Yunani", "flag": "🇬🇷", "lang": "Yunani", "timezone": "Europe/Athens (GMT+2)"},
    "31": {"name": "Belanda", "flag": "🇳🇱", "lang": "Belanda", "timezone": "Europe/Amsterdam (GMT+1)"},
    "32": {"name": "Belgia", "flag": "🇧🇪", "lang": "Belanda/Prancis", "timezone": "Europe/Brussels (GMT+1)"},
    "33": {"name": "Prancis", "flag": "🇫🇷", "lang": "Prancis", "timezone": "Europe/Paris (GMT+1)"},
    "34": {"name": "Spanyol", "flag": "🇪🇸", "lang": "Spanyol", "timezone": "Europe/Madrid (GMT+1)"},
    "39": {"name": "Italia", "flag": "🇮🇹", "lang": "Italia", "timezone": "Europe/Rome (GMT+1)"},
    "40": {"name": "Rumania", "flag": "🇷🇴", "lang": "Rumania", "timezone": "Europe/Bucharest (GMT+2)"},
    "41": {"name": "Swiss", "flag": "🇨🇭", "lang": "Jerman/Prancis/Italia", "timezone": "Europe/Zurich (GMT+1)"},
    "43": {"name": "Austria", "flag": "🇦🇹", "lang": "Jerman", "timezone": "Europe/Vienna (GMT+1)"},
    "44": {"name": "United Kingdom", "flag": "🇬🇧", "lang": "Inggris (UK)", "timezone": "Europe/London (GMT)"},
    "45": {"name": "Denmark", "flag": "🇩🇰", "lang": "Denmark", "timezone": "Europe/Copenhagen (GMT+1)"},
    "46": {"name": "Swedia", "flag": "🇸🇪", "lang": "Swedia", "timezone": "Europe/Stockholm (GMT+1)"},
    "47": {"name": "Norwegia", "flag": "🇳🇴", "lang": "Norwegia", "timezone": "Europe/Oslo (GMT+1)"},
    "48": {"name": "Polandia", "flag": "🇵🇱", "lang": "Polandia", "timezone": "Europe/Warsaw (GMT+1)"},
    "49": {"name": "Jerman", "flag": "🇩🇪", "lang": "Jerman", "timezone": "Europe/Berlin (GMT+1)"},
    "51": {"name": "Peru", "flag": "🇵🇪", "lang": "Spanyol", "timezone": "America/Lima (GMT-5)"},
    "52": {"name": "Meksiko", "flag": "🇲🇽", "lang": "Spanyol", "timezone": "America/Mexico_City (GMT-6)"},
    "53": {"name": "Kuba", "flag": "🇨🇺", "lang": "Spanyol", "timezone": "America/Havana (GMT-5)"},
    "54": {"name": "Argentina", "flag": "🇦🇷", "lang": "Spanyol", "timezone": "America/Argentina/Buenos_Aires (GMT-3)"},
    "55": {"name": "Brasil", "flag": "🇧🇷", "lang": "Portugis (Brasil)", "timezone": "America/Sao_Paulo (GMT-3)"},
    "56": {"name": "Chile", "flag": "🇨🇱", "lang": "Spanyol", "timezone": "America/Santiago (GMT-4)"},
    "57": {"name": "Kolombia", "flag": "🇨🇴", "lang": "Spanyol", "timezone": "America/Bogota (GMT-5)"},
    "58": {"name": "Venezuela", "flag": "🇻🇪", "lang": "Spanyol", "timezone": "America/Caracas (GMT-4)"},
    "60": {"name": "Malaysia", "flag": "🇲🇾", "lang": "Melayu/Malaysia", "timezone": "Asia/Kuala_Lumpur (GMT+8)"},
    "61": {"name": "Australia", "flag": "🇦🇺", "lang": "Inggris (AU)", "timezone": "Australia/Sydney (GMT+10)"},
    "62": {"name": "Indonesia", "flag": "🇮🇩", "lang": "Indonesia", "timezone": "Asia/Jakarta (WIB/WITA/WIT atau GMT+7)"},
    "63": {"name": "Filipina", "flag": "🇵🇭", "lang": "Filipino/Tagalog", "timezone": "Asia/Manila (GMT+8)"},
    "64": {"name": "Selandia Baru", "flag": "🇳🇿", "lang": "Inggris", "timezone": "Pacific/Auckland (GMT+12)"},
    "65": {"name": "Singapura", "flag": "🇸🇬", "lang": "Inggris/Melayu", "timezone": "Asia/Singapore (GMT+8)"},
    "66": {"name": "Thailand", "flag": "🇹🇭", "lang": "Thailand", "timezone": "Asia/Bangkok (GMT+7)"},
    "81": {"name": "Jepang", "flag": "🇯🇵", "lang": "Jepang", "timezone": "Asia/Tokyo (GMT+9)"},
    "82": {"name": "Korea Selatan", "flag": "🇰🇷", "lang": "Korea", "timezone": "Asia/Seoul (GMT+9)"},
    "84": {"name": "Vietnam", "flag": "🇻🇳", "lang": "Vietnam", "timezone": "Asia/Ho_Chi_Minh (GMT+7)"},
    "86": {"name": "China", "flag": "🇨🇳", "lang": "Mandarin/China", "timezone": "Asia/Shanghai (GMT+8)"},
    "90": {"name": "Turki", "flag": "🇹🇷", "lang": "Turki", "timezone": "Europe/Istanbul (GMT+3)"},
    "91": {"name": "India", "flag": "🇮🇳", "lang": "India (Hindi/Inggris)", "timezone": "Asia/Kolkata (IST)"},
    "92": {"name": "Pakistan", "flag": "🇵🇰", "lang": "Urdu/Inggris", "timezone": "Asia/Karachi (GMT+5)"},
    "93": {"name": "Afganistan", "flag": "🇦🇫", "lang": "Pashto/Dari", "timezone": "Asia/Kabul (GMT+4:30)"},
    "94": {"name": "Sri Lanka", "flag": "🇱🇰", "lang": "Sinhala/Tamil", "timezone": "Asia/Colombo (GMT+5:30)"},
    "95": {"name": "Myanmar", "flag": "🇲🇲", "lang": "Burma", "timezone": "Asia/Yangon (GMT+6:30)"},
    "98": {"name": "Iran", "flag": "🇮🇷", "lang": "Persia", "timezone": "Asia/Tehran (GMT+3:30)"},
    "212": {"name": "Maroko", "flag": "🇲🇦", "lang": "Arab (Maroko)", "timezone": "Africa/Casablanca (GMT+1)"},
    "213": {"name": "Aljazair", "flag": "🇩🇿", "lang": "Arab", "timezone": "Africa/Algiers (GMT+1)"},
    "216": {"name": "Tunisia", "flag": "🇹🇳", "lang": "Arab", "timezone": "Africa/Tunis (GMT+1)"},
    "218": {"name": "Libia", "flag": "🇱🇾", "lang": "Arab", "timezone": "Africa/Tripoli (GMT+2)"},
    "220": {"name": "Gambia", "flag": "🇬🇲", "lang": "Inggris", "timezone": "Africa/Banjul (GMT)"},
    "234": {"name": "Nigeria", "flag": "🇳🇬", "lang": "Inggris", "timezone": "Africa/Lagos (GMT+1)"},
    "249": {"name": "Sudan", "flag": "🇸🇩", "lang": "Arab (Sudan)", "timezone": "Africa/Khartoum (GMT+2)"},
    "251": {"name": "Etiopia", "flag": "🇪🇹", "lang": "Amharik", "timezone": "Africa/Addis_Ababa (GMT+3)"},
    "254": {"name": "Kenya", "flag": "🇰🇪", "lang": "Swahili/Inggris", "timezone": "Africa/Nairobi (GMT+3)"},
    "255": {"name": "Tanzania", "flag": "🇹🇿", "lang": "Swahili/Inggris", "timezone": "Africa/Dar_es_Salaam (GMT+3)"},
    "256": {"name": "Uganda", "flag": "🇺🇬", "lang": "Inggris/Swahili", "timezone": "Africa/Kampala (GMT+3)"},
    "351": {"name": "Portugal", "flag": "🇵🇹", "lang": "Portugis", "timezone": "Europe/Lisbon (GMT)"},
    "358": {"name": "Finlandia", "flag": "🇫🇮", "lang": "Finlandia", "timezone": "Europe/Helsinki (GMT+2)"},
    "380": {"name": "Ukraina", "flag": "🇺🇦", "lang": "Ukraina", "timezone": "Europe/Kyiv (GMT+2)"},
    "852": {"name": "Hong Kong", "flag": "🇭🇰", "lang": "Kanton/Inggris", "timezone": "Asia/Hong_Kong (GMT+8)"},
    "880": {"name": "Bangladesh", "flag": "🇧🇩", "lang": "Bengali", "timezone": "Asia/Dhaka (GMT+6)"},
    "960": {"name": "Maladewa", "flag": "🇲🇻", "lang": "Dhivehi", "timezone": "Indian/Maldives (GMT+5)"},
    "961": {"name": "Lebanon", "flag": "🇱🇧", "lang": "Arab", "timezone": "Asia/Beirut (GMT+2)"},
    "962": {"name": "Yordania", "flag": "🇯🇴", "lang": "Arab", "timezone": "Asia/Amman (GMT+3)"},
    "963": {"name": "Suriah", "flag": "🇸🇾", "lang": "Arab", "timezone": "Asia/Damascus (GMT+3)"},
    "964": {"name": "Irak", "flag": "🇮🇶", "lang": "Arab/Kurdi", "timezone": "Asia/Baghdad (GMT+3)"},
    "965": {"name": "Kuwait", "flag": "🇰🇼", "lang": "Arab/Inggris", "timezone": "Asia/Kuwait (GMT+3)"},
    "966": {"name": "Arab Saudi", "flag": "🇸🇦", "lang": "Arab", "timezone": "Asia/Riyadh (GMT+3)"},
    "967": {"name": "Yaman", "flag": "🇾🇪", "lang": "Arab", "timezone": "Asia/Aden (GMT+3)"},
    "968": {"name": "Oman", "flag": "🇴🇲", "lang": "Arab", "timezone": "Asia/Muscat (GMT+3)"},
    "971": {"name": "Uni Emirat Arab", "flag": "🇦🇪", "lang": "Arab/Inggris", "timezone": "Asia/Dubai (GMT+4)"},
    "972": {"name": "Israel", "flag": "🇮🇱", "lang": "Ibrani/Arab", "timezone": "Asia/Jerusalem (GMT+2)"},
    "974": {"name": "Qatar", "flag": "🇶🇦", "lang": "Arab", "timezone": "Asia/Qatar (GMT+3)"},
}


def get_country_info(phone: str) -> dict:
    """Scan country code from phone number and return country details."""
    # Clean non-digits
    digits = re.sub(r"[^\d]", "", phone)

    # Normalize Indonesian 08... -> 628...
    if digits.startswith("08"):
        digits = "62" + digits[1:]

    # Match prefix from longest (3 digits) to shortest (1 digit)
    for length in (3, 2, 1):
        prefix = digits[:length]
        if prefix in COUNTRY_DATA:
            info = COUNTRY_DATA[prefix]
            return {
                "code": prefix,
                "name": info["name"],
                "flag": info["flag"],
                "lang": info["lang"],
                "timezone": info["timezone"],
            }

    # Fallback if no country code matches
    guess_code = digits[:2] if len(digits) >= 2 else (digits[:1] if digits else "62")
    return {
        "code": guess_code,
        "name": f"Region (+{guess_code})",
        "flag": "🌍",
        "lang": "Sesuai Negara",
        "timezone": "Sesuai Region / GMT",
    }


def format_gacha_method(phone: str) -> str:
    """Format the dynamic 6-step Gacha Method tailored to the phone's country."""
    info = get_country_info(phone)
    name = info["name"]
    lang = info["lang"]
    tz   = info["timezone"]

    return (
        f"📋 *Metode Gacha yang Disesuaikan:* \n"
        f"1️⃣ *Device/ROM:* Boleh ROM original HP atau VPhoneGaga (Virtual ROM).\n"
        f"2️⃣ *IP/VPN:* Gunakan server *{name}* atau server yang berhubungan dengan bahasa dari kode negaranya.\n"
        f"3️⃣ *Timezone:* Ubah Timezone device/Virtual ROM sesuai region *{tz}*.\n"
        f"4️⃣ *Aplikasi:* Gunakan aplikasi 💬 *WhatsApp Messenger* atau *WhatsApp Business* resmi (bukan clone app).\n"
        f"5️⃣ *Bahasa:* Atur bahasa aplikasi (WhatsApp) dan device ke *{lang}*.\n"
        f"6️⃣ *VPhoneGaga Note:* Jika menggunakan VPhoneGaga, disarankan gunakan **one-click ganti device** tiap kali mau gacha kode negara baru. Jangan mencampur device jika gacha sebelumnya gagal (misal {name} gagal, ganti negara lain tapi belum ganti device dan setting metode sebelumnya)."
    )
