"""
GitHub Auto-Updater
Cek commit terbaru di repo, trigger workflow_dispatch jika ada update baru.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

COMMIT_CACHE = Path(__file__).parent / ".current_commit"

GH_PAT  = os.environ.get("GH_PAT", "")
REPO    = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH  = "main"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gh_request(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict | str]:
    """Buat request ke GitHub API, return (status_code, parsed_body)."""
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {GH_PAT}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "SMTPBot/1.0")

    data = None
    if body:
        payload = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
        data = payload

    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as ex:
        return 0, str(ex)


def get_latest_commit() -> dict:
    """
    Ambil commit SHA terbaru di branch main.
    Return dict: {success, sha, message, author, date, error?}
    """
    if not GH_PAT or not REPO:
        return {"success": False, "error": "GH_PAT atau GITHUB_REPOSITORY tidak tersedia."}

    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
    code, body = _gh_request(url)

    if code != 200 or not isinstance(body, dict):
        return {"success": False, "error": f"GitHub API {code}: {str(body)[:200]}"}

    sha     = body.get("sha", "")[:7]
    sha_full = body.get("sha", "")
    commit  = body.get("commit", {})
    message = commit.get("message", "").split("\n")[0][:80]
    author  = commit.get("author", {}).get("name", "?")
    date    = commit.get("author", {}).get("date", "")[:10]

    return {
        "success":  True,
        "sha":      sha,
        "sha_full": sha_full,
        "message":  message,
        "author":   author,
        "date":     date,
    }


def get_cached_commit() -> str:
    """Baca SHA commit yang sedang dijalankan (disimpan saat startup)."""
    if COMMIT_CACHE.exists():
        return COMMIT_CACHE.read_text().strip()
    # Fallback: coba dari env (GitHub Actions set GITHUB_SHA)
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        save_commit(sha)
    return sha


def save_commit(sha: str):
    """Simpan SHA saat ini ke cache file."""
    COMMIT_CACHE.write_text(sha.strip())


def trigger_update() -> dict:
    """
    Trigger workflow_dispatch untuk bot.yml via GitHub API.
    Return dict: {success, error?}
    """
    if not GH_PAT or not REPO:
        return {"success": False, "error": "GH_PAT atau GITHUB_REPOSITORY tidak tersedia."}

    url = f"https://api.github.com/repos/{REPO}/actions/workflows/bot.yml/dispatches"
    code, body = _gh_request(url, method="POST", body={"ref": BRANCH})

    if code == 204:
        return {"success": True}
    return {"success": False, "error": f"HTTP {code}: {str(body)[:200]}"}


def get_commit_files(sha: str) -> list[str]:
    """
    Ambil daftar file yang berubah di commit tertentu.
    Return list of file paths.
    """
    if not GH_PAT or not REPO:
        return []
    url = f"https://api.github.com/repos/{REPO}/commits/{sha}"
    code, body = _gh_request(url)
    if code != 200 or not isinstance(body, dict):
        return []
    files = body.get("files", [])
    return [f.get("filename", "") for f in files]


# File path patterns yang dianggap "bot code" (bukan data)
BOT_CODE_PATHS = ("bot/", "requirements.txt", ".github/workflows/bot.yml")


def _is_bot_code_change(files: list[str]) -> bool:
    """True jika ada file bot code yang berubah (bukan hanya data/)."""
    for f in files:
        for pattern in BOT_CODE_PATHS:
            if f.startswith(pattern):
                return True
    return False


def check_for_update() -> dict:
    """
    Bandingkan commit terbaru di GitHub dengan commit yang sedang jalan.
    Hanya anggap 'update tersedia' jika ada perubahan file bot code,
    bukan perubahan data/ saja.
    Return dict: {update_available, latest_sha, current_sha, commit_info?, error?}
    """
    current = get_cached_commit()
    latest  = get_latest_commit()

    if not latest["success"]:
        return {"update_available": False, "error": latest["error"]}

    latest_full  = latest["sha_full"]
    sha_changed  = bool(current) and not latest_full.startswith(current) and current != latest_full

    # Jika SHA berbeda, cek apakah ada file bot code yang berubah
    has_bot_change = False
    if sha_changed:
        changed_files = get_commit_files(latest_full)
        has_bot_change = _is_bot_code_change(changed_files)
        if not has_bot_change:
            # Perubahan hanya di data/ — update cache SHA tapi jangan trigger restart
            logger.debug(f"Commit {latest['sha']} hanya data — skip trigger update")
            save_commit(latest_full)

    return {
        "update_available": sha_changed and has_bot_change,
        "latest_sha":       latest["sha"],
        "latest_sha_full":  latest_full,
        "current_sha":      current[:7] if current else "?",
        "commit_info":      latest,
    }
