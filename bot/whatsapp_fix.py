"""
WhatsApp Fix — kirim email banding ke support@support.whatsapp.com
dan monitor IMAP untuk balasan otomatis.

Fix v2:
- IMAP login pakai `username` field jika ada (Mailtrap pakai username, bukan email)
- Search fallback: coba strict FROM filter dulu, kalau 0 hasil coba ALL sejak tanggal
- Filter di Python (lebih andal daripada IMAP server-side filter)
- Logging di setiap langkah agar mudah debug
- Skip akun tanpa imap_host yang valid
"""

import email as email_lib
import imaplib
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from storage import load as _gh_load
from storage import save as _gh_save

logger = logging.getLogger(__name__)

# File simpan pending fix requests
PENDING_FILE = Path(__file__).parent / "pending_fixes.json"

WHATSAPP_SUPPORT   = "support@support.whatsapp.com"
WHATSAPP_KEYWORDS  = [
    "whatsapp",
    "support@support.whatsapp.com",
    "whatsapp ban",
    "account banned",
    "noreply@whatsapp.com",
    "smb@whatsapp.com",
    "no-reply@whatsapp.com",
    "appeal",
    "ban appeal",
    "account",
]

# Template email banding
APPEAL_TEMPLATE = """\
{phone}

Dear WhatsApp Support Team,

I am writing to appeal the ban on my WhatsApp account associated with the phone number {phone}.

I use WhatsApp solely for personal communication with family and friends. I have not violated any of WhatsApp's Terms of Service intentionally. If there was any activity that triggered this ban, I assure you it was unintentional.

I kindly request you to review my account and restore access at your earliest convenience. I greatly value WhatsApp as my primary means of communication.

Thank you for your understanding and cooperation.

Best regards,
{phone}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Pending fix storage
# ─────────────────────────────────────────────────────────────────────────────

def _load_pending() -> dict:
    return _gh_load(PENDING_FILE)


def _save_pending(data: dict):
    _gh_save(PENDING_FILE, data)


def add_pending(chat_id: int, phone: str, smtp_email: str, sent_at: float, user_info: str = "") -> str:
    """Tambah pending fix, return key unik."""
    data = _load_pending()
    key  = f"{chat_id}_{phone}_{int(sent_at)}"
    data[key] = {
        "chat_id":    chat_id,
        "phone":      phone,
        "smtp_email": smtp_email,
        "sent_at":    sent_at,
        "notified":   False,
        "check_count": 0,
        "user_info":  user_info,
    }
    _save_pending(data)
    return key


def remove_pending(key: str):
    data = _load_pending()
    data.pop(key, None)
    _save_pending(data)


def get_all_pending() -> dict:
    return _load_pending()


def mark_notified(key: str):
    data = _load_pending()
    if key in data:
        data[key]["notified"] = True
        _save_pending(data)


def increment_check(key: str):
    data = _load_pending()
    if key in data:
        data[key]["check_count"] = data[key].get("check_count", 0) + 1
        _save_pending(data)


# ─────────────────────────────────────────────────────────────────────────────
# Email sender
# ─────────────────────────────────────────────────────────────────────────────

def _get_smtp_login(smtp_account: dict) -> tuple[str, str]:
    """
    Ambil login SMTP yang benar.
    - Gmail/Yahoo: pakai email field
    - Mailtrap: pakai username field (bukan key 'mailtrap:xxx')
    """
    username = smtp_account.get("username", "")
    email    = smtp_account.get("email", "")
    # Kalau email berisi 'mailtrap:' atau 'mailpit:', pakai username
    if email.startswith(("mailtrap:", "mailpit:")):
        login = username or email.split(":", 1)[-1]
    else:
        login = email
    return login, smtp_account.get("password", "")


def send_appeal_email(smtp_account: dict, phone: str) -> dict:
    """
    Kirim email banding ke support@support.whatsapp.com.
    smtp_account: dict dengan keys email/username, password, smtp_host, smtp_port
    """
    if smtp_account.get("provider") == "MailerSend":
        api_key = smtp_account.get("password")
        sender_email = smtp_account.get("username") or smtp_account.get("email")

        # Prepare MailerSend HTTP POST request payload
        url = "https://api.mailersend.com/v1/email"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        body_text = APPEAL_TEMPLATE.format(phone=phone)
        payload = {
            "from": {
                "email": sender_email,
                "name": "WhatsApp Ban Appeal"
            },
            "to": [
                {
                    "email": WHATSAPP_SUPPORT,
                    "name": "WhatsApp Support"
                }
            ],
            "subject": f"WhatsApp Ban Appeal - {phone}",
            "text": body_text
        }

        try:
            import requests
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code in (200, 201, 202):
                logger.info(f"Email banding terkirim via MailerSend: {sender_email} → {WHATSAPP_SUPPORT}")
                return {"success": True, "from": sender_email}
            else:
                return {"success": False, "error": f"MailerSend API returned HTTP {r.status_code}: {r.text}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"MailerSend send error: {e}"}

    login, password = _get_smtp_login(smtp_account)
    host = smtp_account["smtp_host"]
    port = int(smtp_account["smtp_port"])

    # Sender address: kalau ada username/email valid pakai itu, otherwise generate
    sender_email = smtp_account.get("email", login)
    if sender_email.startswith(("mailtrap:", "mailpit:")):
        # Gunakan format username@host untuk From
        sender_email = f"{login}@{host.replace('smtp.', '').replace('sandbox.smtp.', '')}"

    subject = f"WhatsApp Ban Appeal - {phone}"
    body    = APPEAL_TEMPLATE.format(phone=phone)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender_email
    msg["To"]      = WHATSAPP_SUPPORT
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(host, port, timeout=15)
        server.ehlo()
        try:
            server.starttls()
            server.ehlo()
        except Exception:  # noqa: BLE001, S110
            pass  # Beberapa server tidak support STARTTLS (e.g. port 2525 Mailtrap)
        server.login(login, password)
        server.sendmail(sender_email, WHATSAPP_SUPPORT, msg.as_string())
        server.quit()
        logger.info(f"Email banding terkirim: {sender_email} → {WHATSAPP_SUPPORT}")
        return {"success": True, "from": sender_email}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Auth gagal — periksa email/password SMTP."}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# IMAP reply checker
# ─────────────────────────────────────────────────────────────────────────────

def _decode_header(val: str) -> str:
    """Decode email header yang mungkin encoded."""
    try:
        import email.header
        parts = email.header.decode_header(val)
        decoded = []
        for part, enc in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(enc or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)
    except Exception:  # noqa: BLE001
        return val or ""


def _strip_html(text: str) -> str:
    """Hapus tag HTML dan bersihkan whitespace berlebih."""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
               .replace("&nbsp;", " ").replace("&#039;", "'").replace("&quot;", '"')
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    return "\n".join(lines)


def _get_email_body(msg) -> str:
    """Ambil body text dari email, prefer text/plain, fallback strip HTML."""
    plain = ""
    html  = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            try:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                decoded = payload.decode(charset, errors="replace")
                if ct == "text/plain" and not plain:
                    plain = decoded
                elif ct == "text/html" and not html:
                    html = decoded
            except Exception:  # noqa: BLE001, S110
                pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    html = body
                else:
                    plain = body
        except Exception:  # noqa: BLE001, S110
            pass

    body = plain if plain else _strip_html(html)
    # Bersihkan quoted-printable
    body = re.sub(r"=\r?\n", "", body)
    body = re.sub(r"=[0-9A-Fa-f]{2}", lambda m: chr(int(m.group()[1:], 16)), body)

    if len(body) > 600:
        body = body[:600] + "…"
    return body.strip()


def _is_whatsapp_reply(from_addr: str, subject: str) -> bool:
    """Cek apakah email ini dari WhatsApp support."""
    combined = (from_addr + " " + subject).lower()
    return any(kw in combined for kw in WHATSAPP_KEYWORDS)


def _is_likely_whatsapp(from_addr: str, subject: str) -> bool:
    """Pengecekan lebih longgar — email apapun yang masuk setelah kirim banding."""
    combined = (from_addr + " " + subject).lower()
    # Pasti WA
    if any(kw in combined for kw in WHATSAPP_KEYWORDS):
        return True
    # Apapun yang terlihat seperti balasan otomatis/support
    soft_kw = ["noreply", "no-reply", "support", "donotreply", "do-not-reply", "autoresponder"]
    return any(kw in combined for kw in soft_kw)


def _get_imap_login(smtp_account: dict) -> tuple[str, str]:
    """
    Ambil login IMAP yang benar.
    - Gmail/Yahoo  : pakai email
    - Mailtrap     : pakai username field
    - Mail.tm      : pakai email (address)
    """
    email    = smtp_account.get("email", "")
    username = smtp_account.get("username", "")
    password = smtp_account.get("password", "")

    if email.startswith(("mailtrap:", "mailpit:")):
        login = username or email.split(":", 1)[-1]
    else:
        login = email
    return login, password


def check_whatsapp_reply(smtp_account: dict, since_timestamp: float) -> dict | None:
    """
    Cek IMAP apakah ada balasan dari WhatsApp support sejak waktu tertentu.

    Strategi:
    1. Login IMAP (gunakan username yang benar, bukan key)
    2. Coba search strict: FROM "whatsapp" SINCE <date>
    3. Kalau 0 hasil, coba broad: ALL SINCE <date>  (filter di Python)
    4. Return dict {subject, body, from, date} atau None jika tidak ada.
    """
    provider = smtp_account.get("provider", "")
    email_addr = smtp_account.get("email", "")

    # Skip checking IMAP for MailerSend since it does not have IMAP capability
    if provider == "MailerSend" or "mailersend" in email_addr.lower() or "mailersend" in smtp_account.get("imap_host", "").lower():
        logger.debug("IMAP skip — MailerSend does not support IMAP.")
        return None

    imap_host = smtp_account.get("imap_host", "")
    imap_port = int(smtp_account.get("imap_port", 993))

    # Skip jika tidak ada IMAP host valid
    if not imap_host or imap_host in ("localhost", "127.0.0.1"):
        logger.debug(f"IMAP skip — host tidak valid: {imap_host}")
        return None

    login, password = _get_imap_login(smtp_account)
    if not login or not password:
        logger.warning("IMAP skip — login/password kosong")
        return None

    since_date = datetime.fromtimestamp(since_timestamp, tz=timezone.utc)
    date_str   = since_date.strftime("%d-%b-%Y")

    logger.debug(f"IMAP check — {login}@{imap_host}:{imap_port} since {date_str}")

    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=20)
        try:
            conn.login(login, password)
        except imaplib.IMAP4.error as e:
            logger.warning(f"IMAP login gagal ({login}@{imap_host}): {e}")
            return None

        conn.select("INBOX")

        # ── Coba search strict: FROM whatsapp ─────────────────────────────────
        ids = []
        try:
            _, data = conn.search(None, f'(FROM "whatsapp" SINCE {date_str})')
            ids = data[0].split() if data[0] else []
            logger.debug(f"IMAP strict search → {len(ids)} pesan")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"IMAP strict search error: {e}")

        # ── Fallback: ALL email sejak tanggal kirim ───────────────────────────
        if not ids:
            try:
                _, data2 = conn.search(None, f'(SINCE {date_str})')
                ids = data2[0].split() if data2[0] else []
                logger.debug(f"IMAP broad search → {len(ids)} pesan")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"IMAP broad search error: {e}")

        if not ids:
            conn.logout()
            logger.debug("IMAP — tidak ada pesan baru")
            return None

        # ── Periksa tiap email, filter di Python ─────────────────────────────
        # Pass 1: cari yang pasti dari WA
        best_possible = None
        for uid in reversed(ids):
            try:
                _, msg_data = conn.fetch(uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"IMAP fetch uid {uid} error: {e}")
                continue

            from_addr = _decode_header(msg.get("From", ""))
            subject   = _decode_header(msg.get("Subject", ""))
            date_hdr  = msg.get("Date", "")
            body      = _get_email_body(msg)

            if _is_whatsapp_reply(from_addr, subject):
                conn.logout()
                logger.info(f"✅ Balasan WA ditemukan! From: {from_addr} | Subject: {subject}")
                return {
                    "from":     from_addr,
                    "subject":  subject,
                    "date":     date_hdr,
                    "body":     body,
                    "confirmed": True,
                }

            # Simpan kandidat terbaik (email apapun yang masuk setelah banding)
            if best_possible is None and _is_likely_whatsapp(from_addr, subject):
                best_possible = {
                    "from":     from_addr,
                    "subject":  subject,
                    "date":     date_hdr,
                    "body":     body,
                    "confirmed": False,
                }

        conn.logout()

        # Jika tidak ada email pasti dari WA tapi ada email lain masuk, laporkan juga
        if best_possible:
            logger.info(f"📬 Email masuk setelah banding (kemungkinan balasan): {best_possible['from']}")
            return best_possible

        logger.debug("IMAP — tidak ada balasan WA setelah filter")
        return None

    except imaplib.IMAP4.error as e:
        logger.warning(f"IMAP error ({login}@{imap_host}): {e}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"check_whatsapp_reply error: {e}")
        return None
