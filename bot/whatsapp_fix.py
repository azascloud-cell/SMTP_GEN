"""
WhatsApp Fix — kirim email banding ke support@support.whatsapp.com
dan monitor IMAP untuk balasan otomatis.
"""

import smtplib
import imaplib
import email as email_lib
import logging
import re
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
from storage import load as _gh_load, save as _gh_save

logger = logging.getLogger(__name__)

# File simpan pending fix requests
PENDING_FILE = Path(__file__).parent / "pending_fixes.json"

WHATSAPP_SUPPORT = "support@support.whatsapp.com"

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


def add_pending(chat_id: int, phone: str, smtp_email: str, sent_at: float) -> str:
    """Tambah pending fix, return key unik."""
    data = _load_pending()
    key  = f"{chat_id}_{phone}_{int(sent_at)}"
    data[key] = {
        "chat_id":    chat_id,
        "phone":      phone,
        "smtp_email": smtp_email,
        "sent_at":    sent_at,
        "notified":   False,
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


# ─────────────────────────────────────────────────────────────────────────────
# Email sender
# ─────────────────────────────────────────────────────────────────────────────

def send_appeal_email(smtp_account: dict, phone: str) -> dict:
    """
    Kirim email banding ke support@support.whatsapp.com.
    smtp_account: dict dengan keys email, password, smtp_host, smtp_port
    Mengembalikan dict {success, error?}
    """
    sender   = smtp_account["email"]
    password = smtp_account["password"]
    host     = smtp_account["smtp_host"]
    port     = smtp_account["smtp_port"]

    subject = f"WhatsApp Ban Appeal - {phone}"
    body    = APPEAL_TEMPLATE.format(phone=phone)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"WhatsApp Support <{sender}>"
    msg["To"]      = WHATSAPP_SUPPORT
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP(host, int(port), timeout=15)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, WHATSAPP_SUPPORT, msg.as_string())
        server.quit()
        return {"success": True}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Auth gagal — periksa email/password SMTP."}
    except Exception as e:
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
    except Exception:
        return val or ""


def _strip_html(text: str) -> str:
    """Hapus tag HTML dan bersihkan whitespace berlebih."""
    # Ganti <br>, <p>, <div> dengan newline dulu
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.IGNORECASE)
    # Hapus semua tag HTML
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
               .replace("&nbsp;", " ").replace("&#039;", "'").replace("&quot;", '"')
    # Bersihkan baris kosong berlebih
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
            except Exception:
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
        except Exception:
            pass

    # Pilih plain text; kalau tidak ada, strip dari HTML
    body = plain if plain else _strip_html(html)

    # Bersihkan quoted-printable artifacts (=20, =\n, dll)
    body = re.sub(r"=\r?\n", "", body)
    body = re.sub(r"=[0-9A-Fa-f]{2}", lambda m: chr(int(m.group()[1:], 16)), body)

    # Potong jika terlalu panjang
    if len(body) > 600:
        body = body[:600] + "…"
    return body.strip()


def check_whatsapp_reply(smtp_account: dict, since_timestamp: float) -> Optional[dict]:
    """
    Cek IMAP apakah ada balasan dari WhatsApp support sejak waktu tertentu.
    Mengembalikan dict {subject, body, from, date} atau None jika tidak ada.
    """
    imap_host = smtp_account.get("imap_host", "")
    imap_port = int(smtp_account.get("imap_port", 993))
    email_addr = smtp_account["email"]
    password   = smtp_account["password"]

    if not imap_host:
        return None

    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port)
        conn.login(email_addr, password)
        conn.select("INBOX")

        # Cari email dari whatsapp support sejak tanggal kirim
        since_date = datetime.fromtimestamp(since_timestamp, tz=timezone.utc)
        date_str   = since_date.strftime("%d-%b-%Y")

        _, data = conn.search(None, f'(FROM "whatsapp" SINCE {date_str})')
        ids = data[0].split() if data[0] else []

        # Ambil email terbaru yang belum dibaca dari WhatsApp
        for uid in reversed(ids):
            _, msg_data = conn.fetch(uid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)

            from_addr = _decode_header(msg.get("From", ""))
            subject   = _decode_header(msg.get("Subject", ""))
            date_str2 = msg.get("Date", "")

            # Filter: hanya dari support WhatsApp
            if "whatsapp" not in from_addr.lower() and "whatsapp" not in subject.lower():
                continue

            body = _get_email_body(msg)

            conn.logout()
            return {
                "from":    from_addr,
                "subject": subject,
                "date":    date_str2,
                "body":    body,
            }

        conn.logout()
        return None

    except imaplib.IMAP4.error as e:
        logger.warning(f"IMAP error untuk {email_addr}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Check reply error: {e}")
        return None
