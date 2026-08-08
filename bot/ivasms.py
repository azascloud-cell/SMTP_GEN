"""
iVasms Module — Integrasi dengan iVasms Dashboard untuk Distribusi Nomor & OTP.
Semua data (combos, user assignments, logs, credentials) disimpan di data/ivasms.json
dan disinkronkan ke GitHub secara otomatis menggunakan pattern storage.py.
"""

import json
import logging
import os
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Gunakan storage helper jika tersedia, jika tidak buat fallback lokal
try:
    from storage import load as storage_load, save as storage_save
except ImportError:
    def storage_load(path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}
    def storage_save(path: Path, data: dict):
        path.write_text(json.dumps(data, indent=2))

logger = logging.getLogger(__name__)

DATA_PATH = Path("data/ivasms.json")

# State management global dalam session
_session = requests.Session()
_is_logged_in = False
_csrf_token = None
_cookies = None

def update_cookies(cookie_str: str) -> dict:
    """Mengupdate dan menyimpan cookies dari user input (string raw atau JSON)."""
    global _session, _cookies, _is_logged_in
    cookies = {}
    try:
        # Coba parse sebagai JSON (misalnya export dari chrome extension)
        data = json.loads(cookie_str)
        if isinstance(data, dict):
            cookies = data
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    cookies[item["name"]] = item["value"]
    except Exception:
        # Parse standard Cookie header format
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookies[k.strip()] = v.strip()

    if cookies:
        _session.cookies.clear()
        for k, v in cookies.items():
            _session.cookies.set(k, v)
        data = load_ivasms_data()
        data["cookies"] = cookies
        save_ivasms_data(data)
        _cookies = cookies
        _is_logged_in = False  # Reset login state to force re-verification
    return cookies

def check_ivasms_connection() -> tuple[bool, str]:
    """Mengecek apakah status koneksi aktif menggunakan cookie yang tersimpan."""
    global _session, _is_logged_in, _csrf_token, _cookies
    creds = get_credentials()
    base_url = creds.get("base_url")
    if not base_url:
        return False, "Base URL iVasms belum diset."

    data = load_ivasms_data()
    stored_cookies = data.get("cookies")
    if stored_cookies:
        _session.cookies.clear()
        for k, v in stored_cookies.items():
            _session.cookies.set(k, v)
        _cookies = stored_cookies

    test_url = f"{base_url.rstrip('/')}/portal/sms/received"
    try:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        resp = _session.get(test_url, headers=headers, timeout=20, allow_redirects=True)
        if resp.status_code == 200 and "login" not in resp.url.lower() and "sign in" not in resp.text.lower():
            soup = BeautifulSoup(resp.text, "html.parser")
            csrf_meta = soup.find("meta", {"name": "csrf-token"})
            if csrf_meta:
                _csrf_token = csrf_meta.get("content")
            _is_logged_in = True
            return True, "Koneksi sukses! Terhubung via Cookie. ✅"
        else:
            # Jika cookie gagal, coba login otomatis dengan email/password
            if login_to_ivasms():
                return True, "Koneksi sukses! Login via Email & Password berhasil. ✅"
            return False, "Koneksi gagal. Cookie/Kredensial tidak valid."
    except Exception as e:
        return False, f"Koneksi gagal: {e}"

def load_ivasms_data() -> dict:
    """Load data iVasms dari persistent storage."""
    data = storage_load(DATA_PATH)
    default_credentials = {
        "email": os.environ.get("IVASMS_EMAIL", ""),
        "password": os.environ.get("IVASMS_PASSWORD", ""),
        "login_url": "https://ivas.tempnum.qzz.io/login",
        "base_url": "https://ivas.tempnum.qzz.io",
        "sms_endpoint": "https://ivas.tempnum.qzz.io/portal/sms/received/getsms",
    }
    credentials = data.setdefault("credentials", {})
    for key, value in default_credentials.items():
        credentials.setdefault(key, value)
    if "combos" not in data:
        data["combos"] = {}  # { "62": ["+628...", ...] }
    if "assignments" not in data:
        data["assignments"] = {}  # { "chat_id": { "phone": "+628...", "country_code": "62", "assigned_at": 1234 } }
    if "otp_logs" not in data:
        data["otp_logs"] = []
    return data

def save_ivasms_data(data: dict):
    """Save data iVasms ke persistent storage."""
    storage_save(DATA_PATH, data)

def get_credentials() -> dict:
    data = load_ivasms_data()
    return data["credentials"]

def update_credentials(email: str, password: str, base_url: str = None) -> dict:
    data = load_ivasms_data()
    data["credentials"]["email"] = email.strip()
    data["credentials"]["password"] = password.strip()
    if base_url:
        base_url = base_url.strip().rstrip("/")
        data["credentials"]["base_url"] = base_url
        data["credentials"]["login_url"] = f"{base_url}/login"
        data["credentials"]["sms_endpoint"] = f"{base_url}/portal/sms/received/getsms"
    save_ivasms_data(data)
    global _is_logged_in
    _is_logged_in = False # Force relogin on next action
    return data["credentials"]

# ─────────────────────────────────────────────────────────────────────────────
# iVasms Login & Scraping Logic
# ─────────────────────────────────────────────────────────────────────────────

def login_to_ivasms() -> bool:
    """Melakukan login ke iVasms Dashboard dan menyimpan CSRF token."""
    global _is_logged_in, _csrf_token, _cookies, _session
    creds = get_credentials()

    email = creds.get("email")
    password = creds.get("password")

    if not email or not password:
        logger.warning("Email atau password iVasms belum diset.")
        return False

    logger.info(f"Mencoba login ke iVasms Dashboard: {email}")

    try:
        # Reset session
        _session = requests.Session()

        # 1. Ambil halaman login untuk mengambil CSRF token
        login_url = urljoin(f"{creds.get('base_url', '').rstrip('/')}/", "login")
        r1 = _session.get(login_url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=20)
        r1.raise_for_status()

        soup = BeautifulSoup(r1.text, "html.parser")
        token_input = soup.find("input", {"name": "_token"})
        token_meta = soup.find("meta", {"name": "csrf-token"})
        csrf_token = token_input.get("value") if token_input else (token_meta.get("content") if token_meta else None)

        # 2. Kirim POST login
        payload = {"email": email, "password": password}
        if csrf_token:
            payload["_token"] = csrf_token

        r2 = _session.post(login_url, data=payload, headers={
            "Referer": login_url,
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=20, allow_redirects=True)

        # Berhasil jika diarahkan keluar dari halaman login dan tidak menampilkan form login.
        login_form = BeautifulSoup(r2.text, "html.parser").find("form", action=re.compile(r"login", re.I))
        if r2.ok and "login" not in r2.url.rstrip("/").lower() and not login_form:
            logger.info("Login iVasms Dashboard sukses! ✅")

            # Ambil CSRF token baru dari dashboard page setelah login
            soup2 = BeautifulSoup(r2.text, "html.parser")
            csrf_meta = soup2.find("meta", {"name": "csrf-token"})
            if csrf_meta:
                _csrf_token = csrf_meta.get("content")
            else:
                _csrf_token = csrf_token  # fallback

            _is_logged_in = True
            _cookies = _session.cookies.get_dict()

            # Simpan cookie ke persistent storage
            data = load_ivasms_data()
            data["cookies"] = _cookies
            save_ivasms_data(data)
            return True
        else:
            logger.warning("Gagal login ke iVasms: Email/password salah atau diblokir.")
            _is_logged_in = False
            return False

    except Exception as e:
        logger.error(f"Error login_to_ivasms: {e}")
        _is_logged_in = False
        return False

def fetch_ivasms_messages() -> list[dict]:
    """Membaca pesan masuk dari iVasms untuk rentang waktu terakhir."""
    global _is_logged_in, _csrf_token, _session

    if not _is_logged_in:
        connected, _ = check_ivasms_connection()
        if not connected:
            return []

    creds = get_credentials()
    base_url = creds.get("base_url")
    sms_endpoint = creds.get("sms_endpoint")

    try:
        headers = {
            "Referer": f"{base_url}/portal/sms/received",
            "X-Requested-With": "XMLHttpRequest"
        }

        # Default load messages for today
        now = datetime.now(timezone.utc)
        start_date = now.strftime("%m/%d/%Y")
        end_date = now.strftime("%m/%d/%Y")

        payload = {
            "from": start_date,
            "to": end_date,
            "_token": _csrf_token
        }

        resp = _session.post(sms_endpoint, headers=headers, data=payload, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        country_groups = soup.find_all("div", {"class": "pointer"})

        if not country_groups:
            logger.debug("Tidak ada grup negara iVasms yang ditemukan.")
            return []

        group_ids = []
        for group in country_groups:
            onclick = group.get("onclick", "")
            match = re.search(r"getDetials\('(.+?)'\)", onclick)
            if match:
                group_ids.append(match.group(1))

        all_messages = []
        numbers_url = urljoin(base_url, "portal/sms/received/getsms/number")
        sms_details_url = urljoin(base_url, "portal/sms/received/getsms/number/sms")

        for group_id in group_ids:
            # 1. Ambil list nomor untuk group ini
            num_payload = {
                "start": start_date,
                "end": end_date,
                "range": group_id,
                "_token": _csrf_token
            }
            num_resp = _session.post(numbers_url, headers=headers, data=num_payload, timeout=15)
            num_soup = BeautifulSoup(num_resp.text, "html.parser")

            number_divs = num_soup.select("div[onclick*='getDetialsNumber']")
            phone_numbers = [div.text.strip() for div in number_divs]

            # 2. Ambil list SMS untuk tiap nomor
            for phone in phone_numbers:
                # Normalisasi nomor telepon
                phone_clean = "+" + re.sub(r"[^\d]", "", phone)

                sms_payload = {
                    "start": start_date,
                    "end": end_date,
                    "Number": phone,
                    "Range": group_id,
                    "_token": _csrf_token
                }
                sms_resp = _session.post(sms_details_url, headers=headers, data=sms_payload, timeout=15)
                sms_soup = BeautifulSoup(sms_resp.text, "html.parser")

                sms_cards = sms_soup.find_all("div", class_="card-body")
                for card in sms_cards:
                    sms_text_p = card.find("p", class_="mb-0")
                    if sms_text_p:
                        sms_text = sms_text_p.get_text(separator="\n").strip()
                        msg_id = f"{phone_clean}-{sms_text[:30]}"

                        all_messages.append({
                            "id": msg_id,
                            "phone": phone_clean,
                            "text": sms_text,
                            "country": group_id.strip(),
                            "timestamp": int(time.time())
                        })

        return all_messages

    except Exception as e:
        logger.error(f"Error fetch_ivasms_messages: {e}")
        _is_logged_in = False  # force relogin next time
        return []

# ─────────────────────────────────────────────────────────────────────────────
# OTP / Service Parsing Helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_otp(message: str) -> str:
    """Mengekstrak kode angka OTP dari isi SMS."""
    # Cari pola angka 4 s.d. 8 digit
    match = re.search(r"\b(\d{4,8})\b", message)
    if match:
        return match.group(1)

    # Cari pola bertanda hubung/spasi (e.g., 123-456)
    match2 = re.search(r"\b(\d{3})[- ](\d{3})\b", message)
    if match2:
        return f"{match2.group(1)}{match2.group(2)}"

    return "N/A"

def detect_service(message: str) -> str:
    """Mendeteksi nama aplikasi/layanan dari isi SMS."""
    msg_lower = message.lower()

    services = {
        "WhatsApp": ["whatsapp", "wa", "واتس", "واتساب"],
        "Telegram": ["telegram", "tele", "تلي", "تيليجرام"],
        "Google": ["google", "gmail", "g-", "جوجل"],
        "Facebook": ["facebook", "fb", "فيسبوك"],
        "Instagram": ["instagram", "ig", "انستا"],
        "TikTok": ["tiktok", "تيك"],
        "Discord": ["discord", "ديسكورد"],
        "Microsoft": ["microsoft", "ms", "مايكروسوفت"],
        "PayPal": ["paypal", "باي بال"],
    }

    for service, keywords in services.items():
        for kw in keywords:
            if kw in msg_lower:
                return service

    return "SMS OTP"

# ─────────────────────────────────────────────────────────────────────────────
# User and Combo Management
# ─────────────────────────────────────────────────────────────────────────────

def add_combo(country_code: str, numbers: list[str]) -> bool:
    """Menambahkan daftar nomor untuk negara tertentu."""
    data = load_ivasms_data()
    clean_nums = []
    for n in numbers:
        n_clean = "+" + re.sub(r"[^\d]", "", n.strip())
        if len(n_clean) >= 8:
            clean_nums.append(n_clean)

    if not clean_nums:
        return False

    data["combos"][country_code] = list(set(data["combos"].get(country_code, []) + clean_nums))
    save_ivasms_data(data)
    return True

def delete_combo(country_code: str) -> bool:
    """Menghapus total combo untuk negara tertentu."""
    data = load_ivasms_data()
    if country_code in data["combos"]:
        del data["combos"][country_code]
        save_ivasms_data(data)
        return True
    return False

def list_combos() -> dict:
    """List semua combo."""
    data = load_ivasms_data()
    return data["combos"]

def assign_number(chat_id: int, country_code: str) -> dict | None:
    """Mengalokasikan satu nomor acak dari combo negara yang belum terpakai."""
    data = load_ivasms_data()

    # 1. Pastikan combo ada & tidak kosong
    combo_numbers = data["combos"].get(country_code, [])
    if not combo_numbers:
        return None

    # 2. Cari nomor yang belum teralokasi saat ini
    currently_assigned = {v["phone"] for v in data["assignments"].values()}
    available = [n for n in combo_numbers if n not in currently_assigned]

    if not available:
        # Jika habis, recycle/allow reuse (opsional) atau kembalikan None
        return None

    selected = random.choice(available)

    # 3. Alokasikan ke user
    # Batalkan penugasan sebelumnya jika ada
    release_number(chat_id)

    data = load_ivasms_data() # reload setelah release
    data["assignments"][str(chat_id)] = {
        "phone": selected,
        "country_code": country_code,
        "assigned_at": int(time.time())
    }
    save_ivasms_data(data)
    return data["assignments"][str(chat_id)]

def release_number(chat_id: int):
    """Membatalkan penugasan nomor untuk user tertentu."""
    data = load_ivasms_data()
    chat_id_str = str(chat_id)
    if chat_id_str in data["assignments"]:
        del data["assignments"][chat_id_str]
        save_ivasms_data(data)

def get_assignment(chat_id: int) -> dict | None:
    """Mengambil detail penugasan nomor user."""
    data = load_ivasms_data()
    return data["assignments"].get(str(chat_id))

def get_user_by_number(phone: str) -> int | None:
    """Mencari chat_id user berdasarkan nomor yang ditugaskan."""
    data = load_ivasms_data()
    for cid_str, val in data["assignments"].items():
        if val["phone"] == phone:
            return int(cid_str)
    return None

def log_otp(phone: str, otp: str, text: str, chat_id: int | None = None):
    """Mencatat OTP log di persistent storage."""
    data = load_ivasms_data()
    log_entry = {
        "phone": phone,
        "otp": otp,
        "text": text,
        "chat_id": chat_id,
        "timestamp": int(time.time())
    }
    data["otp_logs"].append(log_entry)
    # Batasi log agar tidak terlalu membengkak (misal max 500 logs)
    if len(data["otp_logs"]) > 500:
        data["otp_logs"] = data["otp_logs"][-200:]
    save_ivasms_data(data)
