"""
Number Files — simpan, list, load, hapus file .txt nomor WA
ke GitHub (data/numbers/) agar persisten antar restart bot.
"""

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Daftar nama negara yang dikenali dari nama file
_COUNTRY_KEYWORDS: list[tuple[str, str]] = [
    # (keyword_lowercase, label)
    ("indonesia",   "🇮🇩 Indonesia"),
    ("indo",        "🇮🇩 Indonesia"),
    ("malaysia",    "🇲🇾 Malaysia"),
    ("malaysia",    "🇲🇾 Malaysia"),
    ("singapore",   "🇸🇬 Singapore"),
    ("singapur",    "🇸🇬 Singapore"),
    ("philippines", "🇵🇭 Philippines"),
    ("filipin",     "🇵🇭 Philippines"),
    ("vietnam",     "🇻🇳 Vietnam"),
    ("thailand",    "🇹🇭 Thailand"),
    ("myanmar",     "🇲🇲 Myanmar"),
    ("cambodia",    "🇰🇭 Cambodia"),
    ("kamboja",     "🇰🇭 Cambodia"),
    ("nigeria",     "🇳🇬 Nigeria"),
    ("ghana",       "🇬🇭 Ghana"),
    ("kenya",       "🇰🇪 Kenya"),
    ("ethiopia",    "🇪🇹 Ethiopia"),
    ("tanzania",    "🇹🇿 Tanzania"),
    ("egypt",       "🇪🇬 Egypt"),
    ("mesir",       "🇪🇬 Egypt"),
    ("sudan",       "🇸🇩 Sudan"),
    ("iraq",        "🇮🇶 Iraq"),
    ("iran",        "🇮🇷 Iran"),
    ("saudi",       "🇸🇦 Saudi Arabia"),
    ("turkey",      "🇹🇷 Turkey"),
    ("turki",       "🇹🇷 Turkey"),
    ("pakistan",    "🇵🇰 Pakistan"),
    ("india",       "🇮🇳 India"),
    ("bangladesh",  "🇧🇩 Bangladesh"),
    ("brazil",      "🇧🇷 Brazil"),
    ("brasil",      "🇧🇷 Brazil"),
    ("mexico",      "🇲🇽 Mexico"),
    ("colombia",    "🇨🇴 Colombia"),
    ("argentina",   "🇦🇷 Argentina"),
    ("peru",        "🇵🇪 Peru"),
    ("haiti",       "🇭🇹 Haiti"),
    ("algeria",     "🇩🇿 Algeria"),
    ("morocco",     "🇲🇦 Morocco"),
    ("maroko",      "🇲🇦 Morocco"),
    ("cameroon",    "🇨🇲 Cameroon"),
    ("cameroun",    "🇨🇲 Cameroon"),
    ("congo",       "🇨🇩 Congo"),
    ("senegal",     "🇸🇳 Senegal"),
    ("mali",        "🇲🇱 Mali"),
    ("ivory",       "🇨🇮 Ivory Coast"),
    ("madagascar",  "🇲🇬 Madagascar"),
    ("angola",      "🇦🇴 Angola"),
    ("zimbabwe",    "🇿🇼 Zimbabwe"),
    ("zambia",      "🇿🇲 Zambia"),
    ("uganda",      "🇺🇬 Uganda"),
    ("mozambique",  "🇲🇿 Mozambique"),
    ("russia",      "🇷🇺 Russia"),
    ("rusia",       "🇷🇺 Russia"),
    ("ukraine",     "🇺🇦 Ukraine"),
    ("china",       "🇨🇳 China"),
    ("japan",       "🇯🇵 Japan"),
    ("korea",       "🇰🇷 Korea"),
    ("usa",         "🇺🇸 USA"),
    ("united_states","🇺🇸 USA"),
    ("uk",          "🇬🇧 UK"),
    ("france",      "🇫🇷 France"),
    ("germany",     "🇩🇪 Germany"),
    ("spain",       "🇪🇸 Spain"),
    ("portugal",    "🇵🇹 Portugal"),
    ("italy",       "🇮🇹 Italy"),
]


def detect_region(filename: str) -> str:
    """Coba deteksi nama negara dari nama file. Return label atau ''."""
    lower = filename.lower()
    for keyword, label in _COUNTRY_KEYWORDS:
        if keyword in lower:
            return label
    return ""

logger = logging.getLogger(__name__)

GH_PAT = os.environ.get("GH_PAT", "")
REPO   = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = "main"

INDEX_PATH  = "data/numbers_index.json"   # metadata index
FILES_DIR   = "data/numbers/"             # folder file .txt di repo


# ─────────────────────────────────────────────────────────────────────────────
# GitHub API helper
# ─────────────────────────────────────────────────────────────────────────────

def _gh(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {GH_PAT}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "SMTPBot/1.0")
    data = None
    if body:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {}
    except Exception as ex:
        logger.warning(f"GitHub API error: {ex}")
        return 0, {}


def _api(path: str) -> str:
    return f"https://api.github.com/repos/{REPO}/contents/{path}"


# ─────────────────────────────────────────────────────────────────────────────
# Sanitize filename
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize(name: str) -> str:
    """Buat nama file aman untuk GitHub path."""
    name = re.sub(r"[^\w\-.]", "_", name)
    if not name.lower().endswith(".txt"):
        name += ".txt"
    return name[:80]


# ─────────────────────────────────────────────────────────────────────────────
# Index (metadata)
# ─────────────────────────────────────────────────────────────────────────────

def _load_index() -> dict:
    """Load numbers_index.json dari GitHub."""
    if not GH_PAT or not REPO:
        return {}
    code, body = _gh(_api(INDEX_PATH))
    if code == 200 and "content" in body:
        try:
            raw = base64.b64decode(body["content"]).decode("utf-8")
            return json.loads(raw)
        except Exception:
            pass
    return {}


def _save_index(index: dict):
    """Push numbers_index.json ke GitHub."""
    if not GH_PAT or not REPO:
        return
    content = json.dumps(index, indent=2, ensure_ascii=False)
    code, body = _gh(_api(INDEX_PATH))
    sha = body.get("sha") if code == 200 else None
    payload: dict = {
        "message": "data: update numbers_index.json",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch":  BRANCH,
    }
    if sha:
        payload["sha"] = sha
    _gh(_api(INDEX_PATH), method="PUT", body=payload)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

MAX_FILES = 30  # maksimal file tersimpan per instance


def save_file(original_name: str, content: str, chat_id: int, total: int) -> dict:
    """
    Simpan file .txt ke GitHub dan update index.
    Return: {success, key, filename, error?}
    """
    if not GH_PAT or not REPO:
        return {"success": False, "error": "GitHub PAT tidak tersedia."}

    filename = _sanitize(original_name)
    gh_path  = FILES_DIR + filename

    # Cek apakah sudah ada (ambil SHA untuk update)
    code0, body0 = _gh(_api(gh_path))
    sha = body0.get("sha") if code0 == 200 else None

    payload: dict = {
        "message": f"data: upload {filename}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch":  BRANCH,
    }
    if sha:
        payload["sha"] = sha

    code, _body = _gh(_api(gh_path), method="PUT", body=payload)
    if code not in (200, 201):
        return {"success": False, "error": f"GitHub PUT gagal: HTTP {code}"}

    # Update index
    index = _load_index()

    # Jika melebihi MAX_FILES, hapus yang paling lama
    if len(index) >= MAX_FILES:
        oldest_key = min(index.items(), key=lambda x: x[1].get("uploaded_at", ""))[0]
        delete_file(oldest_key)
        index = _load_index()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    region = detect_region(original_name)
    index[filename] = {
        "original_name": original_name,
        "filename":      filename,
        "total":         total,
        "region":        region,
        "uploaded_at":   now,
        "uploaded_by":   chat_id,
    }
    _save_index(index)
    logger.info(f"File saved: {filename} ({total} nomor)")
    return {"success": True, "key": filename, "filename": filename}


def list_files() -> list[dict]:
    """
    List semua file tersimpan dari index.
    Return: [{filename, original_name, total, uploaded_at, uploaded_by}]
    """
    index = _load_index()
    files = list(index.values())
    # Sort: terbaru dulu
    files.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return files


def load_file(filename: str) -> list[str] | None:
    """
    Load nomor dari file tersimpan di GitHub.
    Return: list of phone numbers atau None jika tidak ada.
    """
    if not GH_PAT or not REPO:
        return None
    filename = _sanitize(filename)
    gh_path  = FILES_DIR + filename
    code, body = _gh(_api(gh_path))
    if code != 200 or "content" not in body:
        return None
    try:
        raw = base64.b64decode(body["content"]).decode("utf-8")
        return raw.splitlines()
    except Exception as e:
        logger.warning(f"load_file {filename} error: {e}")
        return None


def delete_file(filename: str) -> dict:
    """
    Hapus file dari GitHub dan update index.
    Return: {success, error?}
    """
    if not GH_PAT or not REPO:
        return {"success": False, "error": "GitHub PAT tidak tersedia."}

    filename = _sanitize(filename)
    gh_path  = FILES_DIR + filename

    # Ambil SHA untuk delete
    code, body = _gh(_api(gh_path))
    if code == 404:
        # File tidak ada di GitHub, hapus saja dari index
        _remove_from_index(filename)
        return {"success": True}
    if code != 200:
        return {"success": False, "error": f"Gagal ambil file: HTTP {code}"}

    sha = body.get("sha", "")
    if not sha:
        return {"success": False, "error": "SHA tidak ditemukan."}

    del_code, _ = _gh(_api(gh_path), method="DELETE", body={
        "message": f"data: delete {filename}",
        "sha": sha,
        "branch": BRANCH,
    })
    if del_code not in (200, 204):
        return {"success": False, "error": f"Gagal hapus di GitHub: HTTP {del_code}"}

    _remove_from_index(filename)
    logger.info(f"File deleted: {filename}")
    return {"success": True}


def _remove_from_index(filename: str):
    index = _load_index()
    if filename in index:
        del index[filename]
        _save_index(index)
