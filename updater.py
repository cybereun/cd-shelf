"""GitHub Releases 기반 업데이트 확인/설치."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from version import GITHUB_REPO, __version__

API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"


def _parse(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _latest_release():
    req = urllib.request.Request(
        API, headers={"Accept": "application/vnd.github+json", "User-Agent": "CDShelf-Updater"}
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
    return data["tag_name"], asset


def check():
    """반환: {current, latest, newer, error}"""
    try:
        tag, asset = _latest_release()
    except Exception as e:  # 오프라인, 레이트리밋, 릴리스 없음 등
        return {"current": __version__, "latest": None, "newer": False, "error": str(e)}
    newer = bool(asset) and _parse(tag) > _parse(__version__)
    return {"current": __version__, "latest": tag.lstrip("v"), "newer": newer, "error": None}


def download_and_install():
    """최신 설치 파일을 받아 조용히 설치 후 앱 종료. 설치기가 완료 후 앱을 다시 실행한다."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        raise RuntimeError("설치형 Windows 앱에서만 자동 업데이트가 가능합니다.")
    tag, asset = _latest_release()
    if not asset or _parse(tag) <= _parse(__version__):
        raise RuntimeError("적용할 새 버전이 없습니다.")
    url = asset["browser_download_url"]
    if not url.startswith(ASSET_PREFIX):
        raise RuntimeError("신뢰할 수 없는 다운로드 주소입니다.")
    dest = os.path.join(tempfile.mkdtemp(prefix="cdshelf-upd-"), asset["name"])
    req = urllib.request.Request(url, headers={"User-Agent": "CDShelf-Updater"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
            h.update(chunk)
    digest = (asset.get("digest") or "").removeprefix("sha256:")
    if digest and digest.lower() != h.hexdigest().lower():
        os.remove(dest)
        raise RuntimeError("다운로드 파일 검증에 실패했습니다.")
    subprocess.Popen(
        [dest, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/NORESTART"],
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        close_fds=True,
    )
    threading.Thread(target=lambda: (time.sleep(1.5), os._exit(0)), daemon=True).start()
