"""version.py의 __version__ 값을 출력한다 (CI에서 사용)."""
import re
from pathlib import Path

text = (Path(__file__).resolve().parent.parent / "version.py").read_text(encoding="utf-8")
print(re.search(r'__version__ = "(.*?)"', text).group(1))
