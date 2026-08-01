"""
Email Generator — Multi-backend:
  1. Standard cPanel UAPI (Niagahoster, DomaiNesia, dll — port 2083)
  2. InfinityFree / VistaPanel (web scraping karena port 2083 diblokir)
  3. Fallback: Zoho Mail free tier

Env vars:
  CPANEL_URL    – https://yourpanel.com:2083  (standard cPanel)
               – https://app.infinityfree.com (InfinityFree)
  CPANEL_USER   – cPanel username (if0_42550468 untuk InfinityFree)
  CPANEL_PASS   – cPanel password
  CPANEL_DOMAIN – domain email (smtpgen.xo.je)
"""

import os
import re
import random
import string
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)
requests.packages.urllib3.disable_warnings()

CPANEL_URL    = os.environ.get("CPANEL_URL",    "").rstrip("/")
CPANEL_USER   = os.environ.get("CPANEL_USER",   "")
CPANEL_PASS   = os.environ.get("CPANEL_PASS",   "")
CPANEL_DOMAIN = os.environ.get("CPANEL_DOMAIN", "")

TIMEOUT = 20

# ─────────────────────────────────────────────────────────────────────────────
# Deteksi provider
# ─────────────────────────────────────────────────────────────────────────────

def _is_infinityfree() -> bool:
    return CPANEL_USER.startswith("if0_") or "infinityfree" in CPANEL_URL.lower() or \
           "epizy.com" in CPANEL_DOMAIN or "rf.gd" in CPANEL_DOMAIN or \
           "xo.je" in CPANEL_DOMAIN or "42web.io" in CPANEL_DOMAIN or \
           "infinityfreeapp.com" in CPANEL_DOMAIN


def is_configured() -> bool:
    return all([CPANEL_USER, CPANEL_PASS, CPANEL_DOMAIN])


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Random creds
# ─────────────────────────────────────────────────────────────────────────────

def _rnd_user(n=8) -> str:
    c = string.ascii_lowercase
    return random.choice(c) + "".join(random.choices(c + string.digits, k=n-1))


def _rnd_pass(n=14) -> str:
    pool = string.ascii_letters + string.digits + "!@#$"
    p = [random.choice(string.ascii_lowercase),
         random.choice(string.ascii_uppercase),
         random.choice(string.digits),
         random.choice("!@#$")]
    p += random.choices(pool, k=n-4)
    random.shuffle(p)
    return "".join(p)


# ─────────────────────────────────────────────────────────────────────────────
# Backend 1: Standard cPanel UAPI
# ─────────────────────────────────────────────────────────────────────────────

def _cpanel_uapi_create(username: str, password: str, quota: int = 250) -> dict:
    """Buat email via cPanel UAPI (hosting berbayar / non-InfinityFree)."""
    url = f"{CPANEL_URL}/execute/Email/add_pop"
    try:
        r = requests.get(url, params={
            "email":    username,
            "password": password,
            "domain":   CPANEL_DOMAIN,
            "quota":    quota,
        }, auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)

        data = r.json()
        if data.get("status") == 1:
            return {"success": True}
        errors = data.get("errors") or [data.get("message", "Unknown")]
        return {"success": False, "error": "; ".join(str(e) for e in errors)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Backend 2: InfinityFree / VistaPanel web scraping
# ─────────────────────────────────────────────────────────────────────────────

class _IFSession:
    """Session untuk InfinityFree panel (VistaPanel via web scraping)."""

    PANEL_BASE  = "https://app.infinityfree.com"
    LOGIN_URL   = "https://app.infinityfree.com/login"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self._logged_in    = False
        self._account_id   = None
        self._vpanel_url   = None

    # ── Login ─────────────────────────────────────────────────────────────────
    def login(self) -> bool:
        try:
            # 1. Ambil CSRF token
            r = self.s.get(self.LOGIN_URL, timeout=TIMEOUT)
            token = re.search(r'name="_token"\s+value="([^"]+)"', r.text)
            if not token:
                token = re.search(r'"_token":"([^"]+)"', r.text)
            csrf = token.group(1) if token else ""

            # 2. POST login
            r = self.s.post(self.LOGIN_URL, data={
                "_token":   csrf,
                "email":    CPANEL_USER if "@" in CPANEL_USER else f"{CPANEL_USER}@infinityfree.com",
                "password": CPANEL_PASS,
            }, timeout=TIMEOUT, allow_redirects=True)

            # InfinityFree bisa pakai username langsung
            if "dashboard" not in r.url and "accounts" not in r.url:
                # Coba login dengan username as-is
                r2 = self.s.post(self.LOGIN_URL, data={
                    "_token":   csrf,
                    "email":    CPANEL_USER,
                    "password": CPANEL_PASS,
                }, timeout=TIMEOUT, allow_redirects=True)
                r = r2

            self._logged_in = "dashboard" in r.url or "accounts" in r.url or \
                              r.url == self.PANEL_BASE + "/" or \
                              "infinityfree" in r.url

            if self._logged_in:
                self._find_account(r.text)
            return self._logged_in

        except Exception as e:
            logger.error(f"InfinityFree login error: {e}")
            return False

    def _find_account(self, html: str):
        """Cari account ID yang sesuai domain."""
        # Cari link ke control panel akun hosting
        pattern = r'/accounts/(\d+)/cpanel'
        matches = re.findall(pattern, html)
        if matches:
            self._account_id = matches[0]

    def _get_vpanel_url(self) -> Optional[str]:
        """Ambil URL VistaPanel untuk akun ini."""
        if not self._account_id:
            # Coba ambil dari halaman accounts
            r = self.s.get(f"{self.PANEL_BASE}/accounts", timeout=TIMEOUT)
            m = re.search(r'/accounts/(\d+)', r.text)
            if m:
                self._account_id = m.group(1)
        if not self._account_id:
            return None

        # Akses cPanel redirect dari InfinityFree
        try:
            r = self.s.get(
                f"{self.PANEL_BASE}/accounts/{self._account_id}/cpanel",
                timeout=TIMEOUT, allow_redirects=True,
            )
            # URL setelah redirect adalah URL VistaPanel
            if "cpanel" in r.url or "2082" in r.url or "2083" in r.url:
                # Ambil base URL
                from urllib.parse import urlparse
                p = urlparse(r.url)
                self._vpanel_url = f"{p.scheme}://{p.netloc}"
                return self._vpanel_url
            return r.url
        except Exception as e:
            logger.error(f"Get vpanel url error: {e}")
            return None

    # ── Email create via VistaPanel ───────────────────────────────────────────
    def create_email(self, username: str, password: str, quota: int = 250) -> dict:
        if not self._logged_in and not self.login():
            return {"success": False, "error": "Login ke InfinityFree gagal. Periksa email/password akun InfinityFree."}

        vpanel = self._get_vpanel_url()
        if vpanel:
            result = self._create_via_vpanel(username, password, quota, vpanel)
            if result["success"]:
                return result

        # Fallback: coba langsung via VistaPanel known endpoint
        result = self._create_via_direct_api(username, password, quota)
        return result

    def _create_via_vpanel(self, username: str, password: str, quota: int, base_url: str) -> dict:
        """Create email via VistaPanel web interface."""
        try:
            # VistaPanel email add endpoint
            url = f"{base_url}/frontend/paper_lantern/mail/doaddpop.html"
            r = self.s.post(url, data={
                "email":         username,
                "password":      password,
                "password2":     password,
                "quota":         quota,
                "domain":        CPANEL_DOMAIN,
                "discard_regex": "",
            }, timeout=TIMEOUT, verify=False)

            if r.status_code == 200 and ("success" in r.text.lower() or "added" in r.text.lower()):
                return {"success": True}
            # Coba UAPI via VistaPanel
            r2 = self.s.get(
                f"{base_url}/execute/Email/add_pop",
                params={"email": username, "password": password, "domain": CPANEL_DOMAIN, "quota": quota},
                timeout=TIMEOUT, verify=False,
            )
            if r2.status_code == 200:
                d = r2.json()
                if d.get("status") == 1:
                    return {"success": True}
                return {"success": False, "error": str(d.get("errors", "Unknown"))}
            return {"success": False, "error": f"VistaPanel HTTP {r.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _create_via_direct_api(self, username: str, password: str, quota: int) -> dict:
        """Coba akses cPanel UAPI langsung dari panel InfinityFree."""
        servers_to_try = [
            f"https://cpanel.epizy.com",
            f"https://www.epizy.com:2082",
            f"https://softaculous.epizy.com:2083",
        ]
        for server in servers_to_try:
            try:
                r = self.s.get(
                    f"{server}/execute/Email/add_pop",
                    params={"email": username, "password": password,
                            "domain": CPANEL_DOMAIN, "quota": quota},
                    auth=(CPANEL_USER, CPANEL_PASS),
                    timeout=8, verify=False,
                )
                if r.status_code == 200:
                    d = r.json()
                    if d.get("status") == 1:
                        return {"success": True}
            except Exception:
                continue
        return {"success": False, "error": "Semua endpoint InfinityFree tidak bisa diakses dari luar. Lihat /cpanelsetup untuk solusi alternatif."}

    def list_emails(self) -> dict:
        if not self._logged_in and not self.login():
            return {"success": False, "error": "Login gagal.", "accounts": []}
        vpanel = self._get_vpanel_url()
        if not vpanel:
            return {"success": False, "error": "Tidak bisa temukan panel URL.", "accounts": []}
        try:
            r = self.s.get(f"{vpanel}/execute/Email/list_pops",
                           params={"domain": CPANEL_DOMAIN},
                           timeout=TIMEOUT, verify=False)
            if r.status_code == 200:
                d = r.json()
                if d.get("status") == 1:
                    return {"success": True, "accounts": d.get("data", []),
                            "count": len(d.get("data", [])), "domain": CPANEL_DOMAIN}
        except Exception as e:
            pass
        return {"success": False, "error": "Tidak bisa list email.", "accounts": []}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

_if_session: Optional[_IFSession] = None


def _get_if_session() -> _IFSession:
    global _if_session
    if _if_session is None:
        _if_session = _IFSession()
    return _if_session


def create_email(username: Optional[str] = None, password: Optional[str] = None,
                 quota: int = 250) -> dict:
    if not is_configured():
        return {"success": False, "setup_needed": True,
                "error": "cPanel/hosting belum dikonfigurasi. Set CPANEL_USER, CPANEL_PASS, CPANEL_DOMAIN di GitHub Secrets."}

    uname = username or _rnd_user()
    pwd   = password or _rnd_pass()
    email = f"{uname}@{CPANEL_DOMAIN}"
    mail_host = f"mail.{CPANEL_DOMAIN}"

    # Pilih backend
    if _is_infinityfree():
        logger.info("Using InfinityFree backend (web scraping)")
        sess   = _get_if_session()
        result = sess.create_email(uname, pwd, quota)
    else:
        logger.info("Using standard cPanel UAPI backend")
        result = _cpanel_uapi_create(uname, pwd, quota)

    if not result["success"]:
        return result

    return {
        "success":       True,
        "email":         email,
        "password":      pwd,
        "username":      uname,
        "domain":        CPANEL_DOMAIN,
        "smtp_host":     mail_host,
        "smtp_port":     "587",
        "smtp_ssl":      "STARTTLS",
        "smtp_port_ssl": "465",
        "imap_host":     mail_host,
        "imap_port":     "993",
        "pop3_host":     mail_host,
        "pop3_port":     "995",
        "webmail":       f"https://{CPANEL_DOMAIN}/webmail",
        "expires":       "Permanen (selama hosting aktif)",
        "note":          "Akun email real — bisa kirim & terima via SMTP",
        "provider":      "InfinityFree" if _is_infinityfree() else "cPanel Hosting",
    }


def delete_email(username: str) -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi."}
    if _is_infinityfree():
        return {"success": False, "error": "Hapus email manual di panel InfinityFree."}
    try:
        url = f"{CPANEL_URL}/execute/Email/delete_pop"
        r   = requests.get(url, params={"email": username, "domain": CPANEL_DOMAIN},
                           auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        d   = r.json()
        return {"success": d.get("status") == 1,
                "error": str(d.get("errors", ""))}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_emails() -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi.", "accounts": []}
    if _is_infinityfree():
        return _get_if_session().list_emails()
    try:
        url = f"{CPANEL_URL}/execute/Email/list_pops"
        r   = requests.get(url, params={"domain": CPANEL_DOMAIN},
                           auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        d   = r.json()
        if d.get("status") == 1:
            return {"success": True, "accounts": d.get("data", []),
                    "count": len(d.get("data", [])), "domain": CPANEL_DOMAIN}
        return {"success": False, "error": "Gagal list email", "accounts": []}
    except Exception as e:
        return {"success": False, "error": str(e), "accounts": []}


def test_connection() -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi."}
    backend = "InfinityFree (web scraping)" if _is_infinityfree() else "cPanel UAPI"
    if _is_infinityfree():
        sess = _get_if_session()
        ok   = sess.login()
        return {
            "success":  ok,
            "backend":  backend,
            "domain":   CPANEL_DOMAIN,
            "error":    "Login gagal" if not ok else None,
        }
    # Standard cPanel test
    try:
        url = f"{CPANEL_URL}/execute/Email/list_pops"
        r   = requests.get(url, params={"domain": CPANEL_DOMAIN},
                           auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        d   = r.json()
        return {"success": d.get("status") == 1, "backend": backend,
                "domain": CPANEL_DOMAIN, "accounts": len(d.get("data", []))}
    except Exception as e:
        return {"success": False, "error": str(e), "backend": backend}
