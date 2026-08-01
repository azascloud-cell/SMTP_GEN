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
import socket
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
# DNS-over-HTTPS helper
# GitHub Actions runner kadang tidak bisa resolve domain tertentu via DNS biasa.
# Solusi: resolve lewat DoH (Google / Cloudflare) dan patch socket.getaddrinfo.
# ─────────────────────────────────────────────────────────────────────────────

_DOH_CACHE: dict = {}          # { hostname: ip }
_ORIG_GETADDRINFO = socket.getaddrinfo


def _resolve_via_doh(hostname: str) -> Optional[str]:
    """Resolve hostname lewat DNS-over-HTTPS.

    Pakai IP address langsung untuk DoH agar tidak perlu DNS sama sekali
    (GitHub Actions runner kadang tidak bisa resolve domain tertentu).
    """
    if hostname in _DOH_CACHE:
        return _DOH_CACHE[hostname]

    # Gunakan IP langsung — tidak butuh DNS untuk reach endpoint DoH ini
    endpoints = [
        # Cloudflare 1.1.1.1 — JSON API, IP langsung, cert valid untuk 1.1.1.1
        f"https://1.1.1.1/dns-query?name={hostname}&type=A",
        # Google 8.8.8.8 — cert punya SAN untuk 8.8.8.8
        f"https://8.8.8.8/resolve?name={hostname}&type=A",
        # Quad9 9.9.9.9
        f"https://9.9.9.9:5053/dns-query?name={hostname}&type=A",
        # Cloudflare alt IP
        f"https://1.0.0.1/dns-query?name={hostname}&type=A",
    ]
    doh_headers = {"Accept": "application/dns-json"}
    for url in endpoints:
        try:
            r = requests.get(url, headers=doh_headers, timeout=8, verify=False)
            if r.status_code != 200:
                continue
            data = r.json()
            for ans in data.get("Answer", []):
                if ans.get("type") == 1:   # A record
                    ip = ans["data"]
                    _DOH_CACHE[hostname] = ip
                    logger.info(f"DoH ({url.split('/')[2]}) resolved {hostname} → {ip}")
                    return ip
        except Exception as exc:
            logger.debug(f"DoH endpoint {url} failed: {exc}")
    logger.warning(f"DoH: semua endpoint gagal untuk {hostname}")
    return None


def _patched_getaddrinfo(host, port, *args, **kwargs):
    """socket.getaddrinfo yang fallback ke DoH jika DNS biasa gagal."""
    try:
        return _ORIG_GETADDRINFO(host, port, *args, **kwargs)
    except socket.gaierror:
        ip = _resolve_via_doh(host)
        if ip:
            logger.info(f"DNS biasa gagal untuk {host!r}, pakai DoH IP={ip}")
            return _ORIG_GETADDRINFO(ip, port, *args, **kwargs)
        raise   # biarkan error asli naik


# Aktifkan patch sekali saat modul di-import
socket.getaddrinfo = _patched_getaddrinfo

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

    # Header mirip browser nyata
    _BROWSER_HEADERS = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;"
                           "q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Cache-Control":   "max-age=0",
    }

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(self._BROWSER_HEADERS)
        self._logged_in    = False
        self._account_id   = None
        self._vpanel_url   = None
        self._login_error  = None

    # ── Helper: ekstrak CSRF ──────────────────────────────────────────────────
    @staticmethod
    def _extract_csrf(html: str) -> str:
        for pat in [
            r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
            r'"_token"\s*:\s*"([^"]+)"',
            r'name="_token"\s+value="([^"]+)"',
            r'_token["\s]+value=["\']([^"\']{20,})',
        ]:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1)
        return ""

    # ── Helper: cek apakah sudah login ───────────────────────────────────────
    @staticmethod
    def _is_logged_in_url(url: str) -> bool:
        markers = ["/accounts", "/dashboard", "/home", "/panel"]
        return any(m in url for m in markers)

    @staticmethod
    def _is_logged_in_html(html: str) -> bool:
        markers = [
            "logout", "Log Out", "Sign Out",
            "accounts", "control panel", "my account",
            "create account", "Hosting Accounts",
        ]
        html_lower = html.lower()
        return any(m.lower() in html_lower for m in markers)

    # ── Login ─────────────────────────────────────────────────────────────────
    def login(self) -> bool:
        self._logged_in   = False
        self._login_error = None
        try:
            # 1. GET halaman login untuk ambil CSRF + cookies
            r = self.s.get(self.LOGIN_URL, timeout=TIMEOUT)
            csrf = self._extract_csrf(r.text)
            logger.debug(f"InfinityFree CSRF: {'found' if csrf else 'not found'}")

            # InfinityFree login pakai EMAIL REGISTRASI akun infinityfree.net
            # CPANEL_USER harus diisi dengan email tsb (contoh: user@gmail.com).
            # Jika diisi username hosting (if0_xxxxx), login AKAN gagal.
            login_email = CPANEL_USER  # gunakan apa adanya; validasi dilakukan setelah POST

            for email_try in [login_email]:
                self.s.cookies.clear()
                # GET lagi agar cookies segar
                r0 = self.s.get(self.LOGIN_URL, timeout=TIMEOUT)
                csrf = self._extract_csrf(r0.text)

                post_headers = {
                    "Referer":      self.LOGIN_URL,
                    "Origin":       self.PANEL_BASE,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-User": "?1",
                    "Sec-Fetch-Dest": "document",
                }

                r = self.s.post(
                    self.LOGIN_URL,
                    data={
                        "_token":   csrf,
                        "email":    email_try,
                        "password": CPANEL_PASS,
                        "remember": "on",
                    },
                    headers=post_headers,
                    timeout=TIMEOUT,
                    allow_redirects=True,
                )
                logger.debug(f"InfinityFree login attempt email={email_try!r} "
                             f"-> url={r.url} status={r.status_code}")

                if self._is_logged_in_url(r.url) or self._is_logged_in_html(r.text):
                    self._logged_in = True
                    self._find_account(r.text)
                    # Jika account_id belum ditemukan, coba GET /accounts
                    if not self._account_id:
                        self._fetch_account_list()
                    logger.info(f"InfinityFree login OK, account_id={self._account_id}")
                    return True

                # Cek apakah ada pesan error di HTML
                err_m = re.search(
                    r'class="[^"]*alert[^"]*"[^>]*>(.*?)</div>',
                    r.text, re.DOTALL | re.IGNORECASE,
                )
                if err_m:
                    self._login_error = re.sub(r'<[^>]+>', '', err_m.group(1)).strip()
                    logger.warning(f"InfinityFree login error msg: {self._login_error}")

            return False

        except Exception as e:
            logger.error(f"InfinityFree login exception: {e}")
            self._login_error = str(e)
            return False

    def _fetch_account_list(self):
        """Ambil halaman /accounts untuk cari account_id."""
        try:
            r = self.s.get(f"{self.PANEL_BASE}/accounts", timeout=TIMEOUT)
            self._find_account(r.text)
        except Exception:
            pass

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
            err = self._login_error or "Periksa email/password akun InfinityFree."
            return {"success": False, "error": f"Login ke InfinityFree gagal. {err}"}

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


def check_dns(domain: str) -> dict:
    """Cek apakah domain sudah resolve (DNS sudah propagate)."""
    import socket
    checks = {
        "domain":    domain,
        "mail_host": f"mail.{domain}",
        "mx":        f"_smtp._tcp.{domain}",
    }
    results = {}
    for label, host in checks.items():
        try:
            ip = socket.gethostbyname(host)
            results[label] = {"resolved": True, "ip": ip}
        except socket.gaierror:
            results[label] = {"resolved": False, "ip": None}
    dns_ok = results["domain"]["resolved"] or results["mail_host"]["resolved"]
    return {
        "dns_ready": dns_ok,
        "details":   results,
        "domain":    domain,
    }


def test_connection() -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi."}
    backend = "InfinityFree (web scraping)" if _is_infinityfree() else "cPanel UAPI"

    # Selalu cek DNS dulu
    dns = check_dns(CPANEL_DOMAIN)

    if _is_infinityfree():
        # Untuk InfinityFree, cek DNS dulu karena blokir port 2083
        sess = _get_if_session()
        ok   = sess.login()
        err  = None
        if not ok:
            detail = sess._login_error or ""
            if "@" not in CPANEL_USER:
                hint = (
                    f"CPANEL_USER saat ini bernilai '{CPANEL_USER}' (bukan email). "
                    "Untuk InfinityFree, isi CPANEL_USER dengan EMAIL yang dipakai "
                    "saat daftar di infinityfree.net (contoh: user@gmail.com)."
                )
            else:
                hint = detail or "Pastikan email & password akun InfinityFree benar."
            err = f"Login ke panel InfinityFree gagal. {hint}"
        return {
            "success":     ok,
            "backend":     backend,
            "domain":      CPANEL_DOMAIN,
            "dns_ready":   dns["dns_ready"],
            "dns_details": dns["details"],
            "error":       err,
        }
    # Standard cPanel test
    try:
        url = f"{CPANEL_URL}/execute/Email/list_pops"
        r   = requests.get(url, params={"domain": CPANEL_DOMAIN},
                           auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        d   = r.json()
        return {"success": d.get("status") == 1, "backend": backend,
                "domain": CPANEL_DOMAIN, "dns_ready": dns["dns_ready"],
                "dns_details": dns["details"],
                "accounts": len(d.get("data", []))}
    except Exception as e:
        return {"success": False, "error": str(e), "backend": backend,
                "dns_ready": dns["dns_ready"], "dns_details": dns["details"]}
