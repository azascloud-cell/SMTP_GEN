"""
Persistent storage via GitHub Contents API.
Data files (smtp_accounts.json, pending_fixes.json) disimpan di
folder `data/` dalam repo sehingga tidak hilang saat GitHub Actions restart.
"""

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

GH_PAT = os.environ.get("GH_PAT", "")
REPO   = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = "main"
DATA_PREFIX = "data/"


# ─────────────────────────────────────────────────────────────────────────────
# GitHub Contents API helper
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
        with urllib.request.urlopen(req, data=data, timeout=15) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as ex:  # noqa: BLE001
        logger.warning(f"GitHub API error: {ex}")
        return 0, {}


def _remote_url(filename: str) -> str:
    return f"https://api.github.com/repos/{REPO}/contents/{DATA_PREFIX}{filename}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def load(path: Path) -> dict:
    """
    Load JSON data.
    Priority: GitHub (source of truth) → local cache → empty dict.
    """
    if GH_PAT and REPO:
        code, body = _gh(_remote_url(path.name))
        if code == 200 and "content" in body:
            try:
                raw = base64.b64decode(body["content"]).decode("utf-8")
                data = json.loads(raw)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=2))
                logger.debug(f"Loaded {path.name} from GitHub ({len(data)} entries)")
                return data
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Decode GitHub data gagal ({path.name}): {e}")

    # Fallback lokal
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001, S110
            pass
    return {}


def save(path: Path, data: dict):
    """
    Simpan JSON ke disk dan push ke GitHub.
    Jika GitHub tidak tersedia, hanya simpan lokal.
    """
    content = json.dumps(data, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

    if not GH_PAT or not REPO:
        return

    # Ambil SHA file yang ada (untuk update, bukan create)
    code, body = _gh(_remote_url(path.name))
    sha = body.get("sha") if code == 200 else None

    payload: dict = {
        "message": f"data: update {path.name}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch":  BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_code, put_body = _gh(_remote_url(path.name), method="PUT", body=payload)
    if put_code in (200, 201):
        logger.debug(f"Pushed {path.name} ke GitHub ✅")
    else:
        logger.warning(f"Gagal push {path.name}: HTTP {put_code} — {put_body.get('message', '')}")
