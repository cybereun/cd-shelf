"""사용: python tools/set_version.py v1.2.3  -> version.py의 __version__ 갱신"""
import re
import sys
from pathlib import Path

tag = sys.argv[1].lstrip("v")
if not re.fullmatch(r"\d+\.\d+\.\d+", tag):
    sys.exit(f"버전 형식이 올바르지 않습니다: {tag}")
p = Path(__file__).resolve().parent.parent / "version.py"
text = p.read_text(encoding="utf-8")
p.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{tag}"', text), encoding="utf-8")
print("version ->", tag)
