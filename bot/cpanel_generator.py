"""
Email Generator — Multi-backend:
  1. PHP Proxy API (InfinityFree hosting — RECOMMENDED, tidak kena Cloudflare block)
  2. Standard cPanel UAPI (Niagahoster, DomaiNesia, dll — port 2083)

Env vars:
  CPANEL_API_URL  – https://smtpgen.xo.je/api_email.php  ← PHP proxy di hosting
  CPANEL_API_KEY  – kunci rahasia yang diset di api_email.php
  CPANEL_DOMAIN   – domain email (smtpgen.xo.je)

  (opsional — untuk cPanel UAPI langsung, non-InfinityFree)
  CPANEL_URL      – https://yourpanel.com:2083
  CPANEL_USER     – cPanel username
  CPANEL_PASS     – cPanel password
"""

import logging
import os
import random
import string

import requests

logger = logging.getLogger(__name__)
requests.packages.urllib3.disable_warnings()

# ── PHP Proxy API (cara baru — tidak kena Cloudflare block) ──────────────────
CPANEL_API_URL = os.environ.get("CPANEL_API_URL", "").rstrip("/")
CPANEL_API_KEY = os.environ.get("CPANEL_API_KEY", "")

# ── cPanel UAPI langsung (cara lama — untuk hosting non-InfinityFree) ─────────
CPANEL_URL    = os.environ.get("CPANEL_URL",    "").rstrip("/")
CPANEL_USER   = os.environ.get("CPANEL_USER",   "")
CPANEL_PASS   = os.environ.get("CPANEL_PASS",   "")

# ── Shared ────────────────────────────────────────────────────────────────────
CPANEL_DOMAIN = os.environ.get("CPANEL_DOMAIN", "")
TIMEOUT       = 20


# ─────────────────────────────────────────────────────────────────────────────
# Deteksi backend yang aktif
# ─────────────────────────────────────────────────────────────────────────────

def _use_proxy_api() -> bool:
    """Gunakan PHP proxy jika CPANEL_API_URL dan CPANEL_API_KEY sudah diset."""
    return bool(CPANEL_API_URL and CPANEL_API_KEY)


def _use_direct_uapi() -> bool:
    return bool(CPANEL_URL and CPANEL_USER and CPANEL_PASS)


def is_configured() -> bool:
    return bool(CPANEL_DOMAIN) and (_use_proxy_api() or _use_direct_uapi())


def _backend_label() -> str:
    if _use_proxy_api():
        return "PHP Proxy API (InfinityFree)"
    if _use_direct_uapi():
        return "cPanel UAPI langsung"
    return "Belum dikonfigurasi"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: random creds
# ─────────────────────────────────────────────────────────────────────────────

def _rnd_user(n=8) -> str:
    c = string.ascii_lowercase
    return random.choice(c) + "".join(random.choices(c + string.digits, k=n - 1))


def _rnd_pass(n=14) -> str:
    pool = string.ascii_letters + string.digits + "!@#$"
    p = [random.choice(string.ascii_lowercase),
         random.choice(string.ascii_uppercase),
         random.choice(string.digits),
         random.choice("!@#$")]
    p += random.choices(pool, k=n - 4)
    random.shuffle(p)
    return "".join(p)


# ─────────────────────────────────────────────────────────────────────────────
# Backend 1: PHP Proxy API (UTAMA untuk InfinityFree)
# ─────────────────────────────────────────────────────────────────────────────

def _proxy_call(action: str, params: dict | None = None) -> dict:
    """Panggil api_email.php yang ada di hosting InfinityFree."""
    payload = {"key": CPANEL_API_KEY, "action": action}
    if params:
        payload.update(params)
    try:
        r = requests.get(CPANEL_API_URL, params=payload, timeout=TIMEOUT, verify=True)
        if r.status_code == 401:
            return {"ok": False, "error": "API Key salah. Periksa CPANEL_API_KEY di GitHub Secrets."}
        if r.status_code == 404:
            return {"ok": False, "error": f"api_email.php tidak ditemukan di {CPANEL_API_URL}. Pastikan sudah diupload ke public_html."}
        data = r.json()
        return data
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "error": f"Tidak bisa reach {CPANEL_API_URL}. Pastikan domain aktif dan file sudah diupload. Detail: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _proxy_create(username: str, password: str, quota: int = 250) -> dict:
    return _proxy_call("create", {"email": username, "password": password, "quota": quota})


def _proxy_list() -> dict:
    return _proxy_call("list")


def _proxy_ping() -> dict:
    return _proxy_call("ping")


# ─────────────────────────────────────────────────────────────────────────────
# Backend 2: cPanel UAPI langsung (untuk hosting berbayar / non-InfinityFree)
# ─────────────────────────────────────────────────────────────────────────────

def _cpanel_uapi_create(username: str, password: str, quota: int = 250) -> dict:
    url = f"{CPANEL_URL}/execute/Email/add_pop"
    try:
        r = requests.get(url, params={
            "email": username, "password": password,
            "domain": CPANEL_DOMAIN, "quota": quota,
        }, auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        data = r.json()
        if data.get("status") == 1:
            return {"ok": True}
        errors = data.get("errors") or [data.get("message", "Unknown")]
        return {"ok": False, "error": "; ".join(str(e) for e in errors)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _cpanel_uapi_list() -> dict:
    url = f"{CPANEL_URL}/execute/Email/list_pops"
    try:
        r    = requests.get(url, params={"domain": CPANEL_DOMAIN},
                            auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        data = r.json()
        if data.get("status") == 1:
            accs = [a.get("email", "") for a in data.get("data", [])]
            return {"ok": True, "accounts": accs, "count": len(accs)}
        return {"ok": False, "error": "Gagal list email.", "accounts": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "accounts": []}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def create_email(username: str | None = None, password: str | None = None,
                 quota: int = 250) -> dict:
    if not is_configured():
        return {
            "success": False, "setup_needed": True,
            "error": (
                "cPanel belum dikonfigurasi. "
                "Set CPANEL_API_URL + CPANEL_API_KEY + CPANEL_DOMAIN di GitHub Secrets, "
                "lalu upload hosting/api_email.php ke public_html hosting kamu."
            ),
        }

    uname = username or _rnd_user()
    pwd   = password or _rnd_pass()

    if _use_proxy_api():
        res = _proxy_create(uname, pwd, quota)
        ok  = res.get("ok", False)
    else:
        res = _cpanel_uapi_create(uname, pwd, quota)
        ok  = res.get("ok", False)

    if not ok:
        return {"success": False, "error": res.get("error", "Unknown error")}

    mail_host = f"mail.{CPANEL_DOMAIN}"
    return {
        "success":       True,
        "email":         f"{uname}@{CPANEL_DOMAIN}",
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
        "provider":      _backend_label(),
    }


def delete_email(username: str) -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi."}
    if _use_proxy_api():
        res = _proxy_call("delete", {"email": username})
        return {"success": res.get("ok", False), "error": res.get("error", "")}
    try:
        url = f"{CPANEL_URL}/execute/Email/delete_pop"
        r   = requests.get(url, params={"email": username, "domain": CPANEL_DOMAIN},
                           auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        d   = r.json()
        return {"success": d.get("status") == 1, "error": str(d.get("errors", ""))}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def list_emails() -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi.", "accounts": []}
    if _use_proxy_api():
        res  = _proxy_list()
        accs = res.get("accounts", [])
        return {"success": res.get("ok", False), "accounts": accs,
                "count": len(accs), "domain": CPANEL_DOMAIN,
                "error": res.get("error", "")}
    res = _cpanel_uapi_list()
    return {"success": res.get("ok", False), "accounts": res.get("accounts", []),
            "count": len(res.get("accounts", [])), "domain": CPANEL_DOMAIN,
            "error": res.get("error", "")}


def check_dns(domain: str) -> dict:
    import socket as _socket
    checks = {"domain": domain, "mail_host": f"mail.{domain}"}
    results = {}
    for label, host in checks.items():
        try:
            ip = _socket.gethostbyname(host)
            results[label] = {"resolved": True, "ip": ip}
        except _socket.gaierror:
            results[label] = {"resolved": False, "ip": None}
    dns_ok = results["domain"]["resolved"] or results["mail_host"]["resolved"]
    return {"dns_ready": dns_ok, "details": results, "domain": domain}


def test_connection() -> dict:
    if not is_configured():
        return {"success": False, "error": "Belum dikonfigurasi."}

    dns     = check_dns(CPANEL_DOMAIN)
    backend = _backend_label()

    if _use_proxy_api():
        res = _proxy_ping()
        ok  = res.get("ok", False)
        err = None if ok else res.get("error", "Ping ke PHP API gagal.")
        if not ok and not err:
            err = (
                f"api_email.php tidak bisa diakses di {CPANEL_API_URL}. "
                "Pastikan file sudah diupload ke public_html dan domain aktif. "
                "Lihat hosting/DEPLOY.md untuk panduan lengkap."
            )
        return {
            "success":     ok,
            "backend":     backend,
            "domain":      CPANEL_DOMAIN,
            "dns_ready":   dns["dns_ready"],
            "dns_details": dns["details"],
            "error":       err,
            "api_url":     CPANEL_API_URL,
        }

    # Standard cPanel UAPI test
    try:
        url = f"{CPANEL_URL}/execute/Email/list_pops"
        r   = requests.get(url, params={"domain": CPANEL_DOMAIN},
                           auth=(CPANEL_USER, CPANEL_PASS), timeout=TIMEOUT, verify=False)
        d   = r.json()
        return {"success": d.get("status") == 1, "backend": backend,
                "domain": CPANEL_DOMAIN, "dns_ready": dns["dns_ready"],
                "dns_details": dns["details"],
                "accounts": len(d.get("data", []))}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e), "backend": backend,
                "dns_ready": dns["dns_ready"], "dns_details": dns["details"]}
