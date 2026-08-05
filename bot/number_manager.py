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

def is_checker_connected(chat_id: int = None) -> bool:
    if not WA_CHECKER_URL:
        return False
    if chat_id is None:
        return True
    try:
        r = requests.get(f"{WA_CHECKER_URL}/status", params={"chat_id": chat_id}, timeout=5)
        if r.status_code == 200:
            return r.json().get("registered", False)
    except Exception:
        pass
    return False


def check_wa_registered(phone: str, chat_id: int = None) -> bool | None:
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
        params = {"phone": phone}
        if chat_id is not None:
            params["chat_id"] = chat_id

        r = requests.get(
            f"{WA_CHECKER_URL}/check",
            params=params,
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


def check_numbers(phones: list[str], chat_id: int = None) -> list[dict]:
    """
    Cek daftar nomor, return list of:
      {"phone": "+628...", "registered": True/False/None}
    """
    results = []
    for phone in phones:
        registered = check_wa_registered(phone, chat_id)
        results.append({"phone": phone, "registered": registered})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Button label helpers
# ─────────────────────────────────────────────────────────────────────────────

def status_emoji(registered: bool | None) -> str:
    if registered is True:
        return "🔴"   # Terdaftar (Linked)
    if registered is False:
        return "🟢"   # Fresh
    return "⚪"       # Terdaftar (Unlinked)


def status_label(registered: bool | None) -> str:
    if registered is True:
        return "Terdaftar (Linked)"
    if registered is False:
        return "Fresh"
    return "Terdaftar (Unlinked)"


# ─────────────────────────────────────────────────────────────────────────────
# Country Info & Metode Gacha
# ─────────────────────────────────────────────────────────────────────────────

COUNTRY_DATA = {
    "1": {"name": "United States/Canada", "flag": "🇺🇸", "lang": "Inggris (English)", "timezone": "GMT-5 s.d. GMT-8", "vpn": "United States / Kanada"},
    "7": {"name": "Rusia", "flag": "🇷🇺", "lang": "Rusia (Russian)", "timezone": "GMT+3", "vpn": "Rusia / Kazakhstan"},
    "20": {"name": "Mesir", "flag": "🇪🇬", "lang": "Arab (Arabic)", "timezone": "GMT+2", "vpn": "Mesir / Arab Saudi / UEA"},
    "27": {"name": "Afrika Selatan", "flag": "🇿🇦", "lang": "Inggris (English)", "timezone": "GMT+2", "vpn": "Afrika Selatan / Inggris"},
    "33": {"name": "Prancis", "flag": "🇫🇷", "lang": "Prancis (French)", "timezone": "GMT+1", "vpn": "Prancis / Jerman"},
    "34": {"name": "Spanyol", "flag": "🇪🇸", "lang": "Spanyol (Spanish)", "timezone": "GMT+1", "vpn": "Spanyol / Prancis"},
    "39": {"name": "Italia", "flag": "🇮🇹", "lang": "Italia (Italian)", "timezone": "GMT+1", "vpn": "Italia / Prancis"},
    "44": {"name": "United Kingdom", "flag": "🇬🇧", "lang": "Inggris (English)", "timezone": "GMT+0", "vpn": "United Kingdom / Jerman"},
    "49": {"name": "Jerman", "flag": "🇩🇪", "lang": "Jerman (German)", "timezone": "GMT+1", "vpn": "Jerman / Prancis"},
    "52": {"name": "Meksiko", "flag": "🇲🇽", "lang": "Spanyol (Spanish)", "timezone": "GMT-6", "vpn": "Meksiko / Amerika Serikat"},
    "55": {"name": "Brasil", "flag": "🇧🇷", "lang": "Portugis (Portuguese)", "timezone": "GMT-3", "vpn": "Brasil / Portugal"},
    "60": {"name": "Malaysia", "flag": "🇲🇾", "lang": "Melayu (Malay)", "timezone": "GMT+8", "vpn": "Malaysia / Singapura"},
    "61": {"name": "Australia", "flag": "🇦🇺", "lang": "Inggris (English)", "timezone": "GMT+10", "vpn": "Australia / Singapura"},
    "62": {"name": "Indonesia", "flag": "🇮🇩", "lang": "Indonesia", "timezone": "WIB/WITA/WIT (GMT+7)", "vpn": "Indonesia / Singapura / Malaysia"},
    "63": {"name": "Filipina", "flag": "🇵🇭", "lang": "Filipino", "timezone": "GMT+8", "vpn": "Filipina / Singapura"},
    "65": {"name": "Singapura", "flag": "🇸🇬", "lang": "Inggris (English)", "timezone": "GMT+8", "vpn": "Singapura / Malaysia"},
    "66": {"name": "Thailand", "flag": "🇹🇭", "lang": "Thailand (Thai)", "timezone": "GMT+7", "vpn": "Thailand / Singapura"},
    "81": {"name": "Jepang", "flag": "🇯🇵", "lang": "Jepang (Japanese)", "timezone": "GMT+9", "vpn": "Jepang / Korea Selatan"},
    "82": {"name": "Korea Selatan", "flag": "🇰🇷", "lang": "Korea (Korean)", "timezone": "GMT+9", "vpn": "Korea Selatan / Jepang"},
    "84": {"name": "Vietnam", "flag": "🇻🇳", "lang": "Vietnam (Vietnamese)", "timezone": "GMT+7", "vpn": "Vietnam / Singapura"},
    "86": {"name": "China", "flag": "🇨🇳", "lang": "Mandarin (Chinese)", "timezone": "GMT+8", "vpn": "China / Hong Kong"},
    "90": {"name": "Turki", "flag": "🇹🇷", "lang": "Turki (Turkish)", "timezone": "GMT+3", "vpn": "Turki / Jerman"},
    "91": {"name": "India", "flag": "🇮🇳", "lang": "Hindi / Inggris", "timezone": "GMT+5:30", "vpn": "India / Singapura"},
    "212": {"name": "Maroko", "flag": "🇲🇦", "lang": "Arab / Prancis", "timezone": "GMT+1", "vpn": "Maroko / Prancis"},
    "221": {"name": "Senegal", "flag": "🇸🇳", "lang": "Prancis (French)", "timezone": "GMT+0", "vpn": "Senegal / Prancis / Nigeria"},
    "225": {"name": "Pantai Gading", "flag": "🇨🇮", "lang": "Prancis (French)", "timezone": "GMT+0", "vpn": "Pantai Gading / Prancis / Nigeria"},
    "228": {"name": "Togo", "flag": "🇹🇬", "lang": "Prancis (French)", "timezone": "GMT+0", "vpn": "Togo / Prancis (French Server) / Nigeria"},
    "234": {"name": "Nigeria", "flag": "🇳🇬", "lang": "Inggris (English)", "timezone": "GMT+1", "vpn": "Nigeria / United Kingdom"},
    "249": {"name": "Sudan", "flag": "🇸🇩", "lang": "Arab (Arabic)", "timezone": "GMT+2", "vpn": "Sudan / Arab Saudi / Mesir"},
    "351": {"name": "Portugal", "flag": "🇵🇹", "lang": "Portugis (Portuguese)", "timezone": "GMT+0", "vpn": "Portugal / Spanyol"},
    "852": {"name": "Hong Kong", "flag": "🇭🇰", "lang": "Kanton / Inggris", "timezone": "GMT+8", "vpn": "Hong Kong / Singapura"},
    "966": {"name": "Arab Saudi", "flag": "🇸🇦", "lang": "Arab (Arabic)", "timezone": "GMT+3", "vpn": "Arab Saudi / UEA / Mesir"},
    "971": {"name": "Uni Emirat Arab", "flag": "🇦🇪", "lang": "Arab / Inggris", "timezone": "GMT+4", "vpn": "Uni Emirat Arab / Arab Saudi"},
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
                "vpn": info.get("vpn", f"{info['name']} / Alternatif VPN Terdekat"),
            }

    # Fallback if no country code matches
    guess_code = digits[:2] if len(digits) >= 2 else (digits[:1] if digits else "62")
    return {
        "code": guess_code,
        "name": f"Region (+{guess_code})",
        "flag": "🌍",
        "lang": "Sesuai Negara",
        "timezone": "Sesuai Region / GMT",
        "vpn": "Negara Asal / Alternatif VPN Terdekat",
    }


def format_gacha_method(phone: str) -> str:
    """Format the dynamic 6-step Gacha Method tailored to the phone's country."""
    info = get_country_info(phone)
    info["name"]
    code = info["code"]
    lang = info["lang"]
    tz   = info["timezone"]
    vpn  = info["vpn"]

    return (
        f"📋 *Metode Gacha yang Disesuaikan:* \n"
        f"1️⃣ *Device/ROM:* Boleh ROM original HP atau VPhoneGaga (Virtual ROM).\n"
        f"2️⃣ *IP/VPN:* Gunakan server *{vpn}* atau server yang berhubungan dengan bahasa dari kode negaranya.\n"
        f"3️⃣ *Timezone:* Ubah Timezone device/Virtual ROM sesuai region *{tz}*.\n"
        f"4️⃣ *Aplikasi:* Gunakan aplikasi 💬 *WhatsApp Messenger* atau *WhatsApp Business* resmi (bukan clone app).\n"
        f"5️⃣ *Bahasa:* Atur bahasa aplikasi (WhatsApp) dan device ke *{lang}*.\n"
        f"6️⃣ *VPhoneGaga Note:* Jika menggunakan VPhoneGaga, disarankan gunakan **one-click ganti device** tiap kali mau gacha kode negara baru. Jangan mencampur device jika gacha sebelumnya gagal (misal Region [+{code}] gagal, ganti negara lain tapi belum ganti device dan setting metode sebelumnya)."
    )


def get_recommended_banding_language(phone: str) -> str:
    """
    Rekomendasikan 1 bahasa terbaik berdasarkan region kode negara.
    - +228/+225/+221/+33 disarankan FR
    - +966/+971/+20/+249 disarankan AR
    - +7 disarankan RU
    - +62 disarankan ID
    - sisanya EN
    """
    info = get_country_info(phone)
    code = info["code"]
    if code in ("228", "225", "221", "33"):
        return "🇫🇷 Prancis (Français)"
    if code in ("966", "971", "20", "249"):
        return "🇸🇦 Arab (العربية)"
    if code == "7":
        return "🇷🇺 Rusia (Русский)"
    if code == "62":
        return "🇮🇩 Indonesia (ID)"
    return "🇬🇧 Inggris (English)"


def format_banding_templates(phone: str) -> str:
    lang_rec = get_recommended_banding_language(phone)
    return (
        f"📩 *TEMPLATE TEXT BANDING (UNBAN REQUEST)*\n"
        f"💡 *Bahasa Disarankan untuk Region Ini:* {lang_rec}\n\n"
        f"Silakan pilih dan salin teks banding di bawah ini sesuai kebutuhan:\n\n"
        f"1️⃣ 🇮🇩 *Bahasa Indonesia:*\n"
        f"`Halo Tim Dukungan WhatsApp, Nomor saya ({phone}) terblokir secara tidak sengaja. Saya selalu mematuhi Ketentuan Layanan WhatsApp dan tidak pernah mengirim spam. Nomor ini sangat penting untuk komunikasi harian saya. Mohon peninjauan ulang dan aktifkan kembali akun saya. Terima kasih.`\n\n"
        f"2️⃣ 🇬🇧 *Bahasa Inggris (English):*\n"
        f"`Hello WhatsApp Support Team, My phone number ({phone}) was banned by mistake. I strictly follow WhatsApp Terms of Service and have not sent any spam or violation content. This is my primary number for daily communication. Please review and unban my account. Thank you.`\n\n"
        f"3️⃣ 🇸🇦 *Bahasa Arab (العربية):*\n"
        f"`مرحبًا فريق دعم WhatsApp، تم حظر رقم هاتفي ({phone}) عن طريق الخطأ. أنا ألتزم تمامًا بشروط خدمة WhatsApp ولم أقم بإرسال أي رسائل مزعجة. هذا الرقم ضروري جدًا لتواصلي اليومي. يرجى إعادة النظر وإلغاء حظر حسابي. شكرًا لكم.`\n\n"
        f"4️⃣ 🇷🇺 *Bahasa Rusia (Русский):*\n"
        f"`Здравствуйте, служба поддержки WhatsApp! Мой номер телефона ({phone}) был заблокирован по ошибке. Я строго соблюдаю Условия использования WhatsApp и не рассылал спам. Этот номер жизненно важен для моего ежедневного общения. Пожалуйста, проверьте и разблокируйте мой аккаунт. Спасибо.`\n\n"
        f"5️⃣ 🇫🇷 *Bahasa Prancis (Français):*\n"
        f"`Bonjour l'équipe Support WhatsApp, Mon numéro de téléphone ({phone}) a été bloqué par erreur. Je respecte strictement les Conditions d'utilisation de WhatsApp et n'ai envoyé aucun spam. Ce numéro est essentiel pour ma communication quotidienne. Merci de bien vouloir vérifier et débloquer mon compte. Cordialement.`\n\n"
        f"📌 *CATATAN PENTING BANDING:*\n"
        f"• Pastikan ubah Bahasa Device & WhatsApp di VPhoneGaga/HP sesuai bahasa template yang dipilih sebelum klik tombol Banding / Minta Peninjauan.\n"
        f"• Jika banding via In-App ditolak, kirimkan teks di atas ke email: `support@support.whatsapp.com` dengan subjek: `[Unban Request] My phone number {phone}`."
    )


def get_masked_and_prefix(phone: str) -> tuple[str, str]:
    """
    Ekstrak prefix dan masked number dari nomor telepon.
    Contoh: +22879017409 -> ("228***7409", "228790")
    """
    # Ambil digit saja
    digits = re.sub(r"[^\d]", "", phone)

    # Prefix: 6 digit pertama (atau sepanjang digit yang ada jika kurang dari 6)
    prefix = digits[:6]

    # Masked: 3 digit pertama + *** + 4 digit terakhir
    if len(digits) >= 7:
        masked = digits[:3] + "***" + digits[-4:]
    else:
        masked = digits

    return masked, prefix
