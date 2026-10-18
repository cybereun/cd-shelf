"""CD 보관함: CD를 넣으면 디스크 ID로 앨범/트랙/커버를 조회해 DB에 저장하고,
검색과 트랙별 별점을 제공하는 로컬 웹앱."""
import os
import socket
import sqlite3
import sys
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory, url_for
from markupsafe import escape as e

import updater
from version import __version__

if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "CDShelf"
else:
    DATA_DIR = Path(__file__).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cdshelf.db"
COVER_DIR = DATA_DIR / "covers"
COVER_DIR.mkdir(exist_ok=True)

PORT = 5000
app = Flask(__name__)
PENDING = []  # 스캔 후보 목록 (선택 대기)


# ---------- DB ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mbid TEXT UNIQUE,
                discid TEXT,
                title TEXT NOT NULL,
                artist TEXT,
                year TEXT,
                label TEXT,
                country TEXT,
                cover TEXT,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
                disc_no INTEGER DEFAULT 1,
                number INTEGER,
                title TEXT NOT NULL,
                length_ms INTEGER,
                rating INTEGER DEFAULT 0
            );
            """
        )


# ---------- CD 읽기 / 인터넷 조회 ----------
def read_disc():
    import discid  # python-discid (libdiscid 필요)

    return discid.read()  # 기본 CD 드라이브


def lookup_releases(disc_id, toc=None):
    import musicbrainzngs as mb

    mb.set_useragent("CDShelf", __version__, "https://github.com/cybereun/cd-shelf")
    includes = ["artists", "recordings", "labels"]
    try:
        # toc를 같이 보내면 정확한 ID가 없을 때 트랙 구성이 비슷한 릴리스를 대신 찾아준다
        res = mb.get_releases_by_discid(disc_id, includes=includes, toc=toc)
    except mb.ResponseError as ex:
        if getattr(ex.cause, "code", None) == 404:  # MusicBrainz에 없는 디스크
            return []
        raise
    out = []
    for rel in res.get("disc", {}).get("release-list", []) or res.get("release-list", []):
        medium = None
        for m in rel.get("medium-list", []):
            if any(d.get("id") == disc_id for d in m.get("disc-list", [])):
                medium = m
                break
        if medium is None and rel.get("medium-list"):
            medium = rel["medium-list"][0]
        if medium is None:
            continue
        label = ""
        try:
            label = rel["label-info-list"][0]["label"]["name"]
        except (KeyError, IndexError):
            pass
        tracks = []
        for t in medium.get("track-list", []):
            tracks.append(
                {
                    "number": int(t.get("number", t.get("position", 0)) or 0),
                    "title": t["recording"]["title"],
                    "length_ms": int(t.get("length") or t["recording"].get("length") or 0),
                }
            )
        out.append(
            {
                "mbid": rel["id"],
                "discid": disc_id,
                "title": rel["title"],
                "artist": rel.get("artist-credit-phrase", ""),
                "year": (rel.get("date") or "")[:4],
                "label": label,
                "country": rel.get("country", ""),
                "disc_no": int(medium.get("position", 1)),
                "tracks": tracks,
            }
        )
    return out


def cddb_disc_id(offsets, leadout):
    n = sum(sum(int(c) for c in str(o // 75)) for o in offsets)
    t = leadout // 75 - offsets[0] // 75
    return f"{((n % 255) << 24) | (t << 8) | len(offsets):08x}"


def lookup_gnudb(toc_string):
    """GnuDB(CDDB 호환)에서 TOC로 조회. 한국 CD가 MusicBrainz보다 많이 등록돼 있다."""
    parts = [int(x) for x in toc_string.split()]
    first, last, leadout = parts[0], parts[1], parts[2]
    offsets = parts[3:3 + (last - first + 1)]
    did = cddb_disc_id(offsets, leadout)
    url = "http://gnudb.gnudb.org/~cddb/cddb.cgi"
    hello = "hello=cdshelf+localhost+xmcd+2.5&proto=6"  # GnuDB는 등록된 클라이언트명만 허용

    def call(cmd):
        r = requests.get(f"{url}?cmd={cmd}&{hello}", timeout=15)
        r.encoding = "utf-8"
        return r.text.splitlines()

    q = f"cddb+query+{did}+{len(offsets)}+" + "+".join(map(str, offsets)) + f"+{leadout // 75}"
    lines = call(q)
    if not lines:
        return []
    code = lines[0][:3]
    if code == "200":
        hits = [lines[0][4:]]
    elif code in ("210", "211"):
        hits = [l for l in lines[1:] if l and l != "."]
    else:
        return []
    out = []
    for h in hits[:5]:
        cat, hid, rest = h.split(" ", 2)
        data = call(f"cddb+read+{cat}+{hid}")
        if not data or data[0][:3] != "210":
            continue
        titles, tr, year = "", {}, ""
        for l in data[1:]:
            if l.startswith("DTITLE="):
                titles += l[7:]
            elif l.startswith("DYEAR="):
                year = l[6:].strip()
            elif l.startswith("TTITLE"):
                k, _, v = l[6:].partition("=")
                if k.isdigit():
                    tr[int(k)] = tr.get(int(k), "") + v
        artist, _, title = titles.partition(" / ")
        if not title:
            artist, title = "", titles
        tracks = []
        for i in range(len(offsets)):
            end = offsets[i + 1] if i + 1 < len(offsets) else leadout
            tracks.append({"number": i + 1, "title": tr.get(i, f"Track {i + 1}").strip() or f"Track {i + 1}",
                           "length_ms": (end - offsets[i]) * 1000 // 75})
        out.append({"mbid": None, "discid": did, "title": title.strip(), "artist": artist.strip(),
                    "year": year, "label": "GnuDB", "country": "", "disc_no": 1, "tracks": tracks})
    return out


def _dur(txt):
    try:
        m, sec = str(txt).split(":")[-2:]
        return (int(m) * 60 + int(sec)) * 1000
    except (ValueError, TypeError):
        return 0


def discogs_token():
    t = os.environ.get("DISCOGS_TOKEN", "")
    f = DATA_DIR / "discogs_token.txt"
    if not t and f.exists():
        t = f.read_text(encoding="utf-8").strip()
    return t


def search_releases_text(q):
    """앨범명/아티스트로 여러 곳(MusicBrainz, iTunes, Deezer, Discogs)을 검색. 트랙은 저장 시점에 가져온다(lazy)."""
    import musicbrainzngs as mb

    out, errs = [], []

    def add(src, id_, title, artist, year, country="", n="?"):
        out.append({"src": src, "mbid": id_ if src == "mb" else None, "ext_id": str(id_), "lazy": True,
                    "title": title, "artist": artist, "year": year, "label": src_names[src],
                    "country": country, "n": n, "tracks": []})

    src_names = {"mb": "MusicBrainz", "itunes": "iTunes", "deezer": "Deezer", "discogs": "Discogs"}

    def run(fn):
        try:
            fn()
        except Exception as ex:
            errs.append(str(ex))

    def mb_search():
        mb.set_useragent("CDShelf", __version__, "https://github.com/cybereun/cd-shelf")
        for rel in mb.search_releases(query=q, limit=8).get("release-list", []):
            add("mb", rel["id"], rel["title"], rel.get("artist-credit-phrase", ""),
                (rel.get("date") or "")[:4], rel.get("country", ""), rel.get("medium-track-count", "?"))

    def itunes_search():
        seen = set()
        for cc in ("US", "KR"):  # 스토어마다 카탈로그가 달라 둘 다 조회
            r = requests.get("https://itunes.apple.com/search", params={"term": q, "entity": "album",
                                                                        "limit": 6, "country": cc}, timeout=15)
            for x in r.json().get("results", []):
                if x["collectionId"] in seen:
                    continue
                seen.add(x["collectionId"])
                add("itunes", f'{cc}:{x["collectionId"]}', x["collectionName"], x["artistName"],
                    (x.get("releaseDate") or "")[:4], "", x.get("trackCount", "?"))

    def deezer_search():
        for x in requests.get("https://api.deezer.com/search/album", params={"q": q, "limit": 8},
                              timeout=15).json().get("data", []):
            add("deezer", x["id"], x["title"], x.get("artist", {}).get("name", ""), "", "", x.get("nb_tracks", "?"))

    def discogs_search():
        tok = discogs_token()
        if not tok:
            return
        r = requests.get("https://api.discogs.com/database/search",
                         params={"q": q, "type": "release", "format": "CD", "per_page": 8, "token": tok},
                         headers={"User-Agent": f"CDShelf/{__version__}"}, timeout=15)
        for x in r.json().get("results", []):
            t = x.get("title", "")
            artist, _, title = t.partition(" - ")
            add("discogs", x["id"], title or t, artist if title else "", str(x.get("year", "")),
                x.get("country", ""))

    for fn in (mb_search, itunes_search, deezer_search, discogs_search):
        run(fn)
    if not out and errs:
        raise RuntimeError(" / ".join(errs))
    return out


def resolve_lazy(c):
    src, eid = c.get("src", "mb"), c["ext_id"] if "ext_id" in c else c["mbid"]
    if src == "mb":
        import musicbrainzngs as mb

        full = mb.get_release_by_id(eid, includes=["artists", "recordings", "labels", "media"])["release"]
        medium = full["medium-list"][0]
        tracks = [{"number": int(t.get("number") or t.get("position") or 0), "title": t["recording"]["title"],
                   "length_ms": int(t.get("length") or t["recording"].get("length") or 0)}
                  for t in medium.get("track-list", [])]
        try:
            c["label"] = full["label-info-list"][0]["label"]["name"]
        except (KeyError, IndexError):
            pass
        c["disc_no"] = int(medium.get("position", 1))
    elif src == "itunes":
        cc, _, eid = eid.partition(":")
        res = requests.get("https://itunes.apple.com/lookup", params={"id": eid, "entity": "song", "country": cc},
                           timeout=15).json()["results"]
        tracks = [{"number": x["trackNumber"], "title": x["trackName"], "length_ms": x.get("trackTimeMillis", 0)}
                  for x in res if x.get("wrapperType") == "track" and x.get("discNumber", 1) == 1]
        if res and res[0].get("artworkUrl100"):
            c["cover_url"] = res[0]["artworkUrl100"].replace("100x100bb", "600x600bb")
    elif src == "deezer":
        d = requests.get(f"https://api.deezer.com/album/{eid}", timeout=15).json()
        tracks = [{"number": i + 1, "title": t["title"], "length_ms": t.get("duration", 0) * 1000}
                  for i, t in enumerate(d.get("tracks", {}).get("data", []))]
        c["cover_url"] = d.get("cover_big") or d.get("cover_medium")
        c["year"] = c.get("year") or (d.get("release_date") or "")[:4]
    else:  # discogs
        d = requests.get(f"https://api.discogs.com/releases/{eid}", params={"token": discogs_token()},
                         headers={"User-Agent": f"CDShelf/{__version__}"}, timeout=15).json()
        tracks = [{"number": i + 1, "title": t["title"], "length_ms": _dur(t.get("duration"))}
                  for i, t in enumerate(x for x in d.get("tracklist", []) if x.get("type_", "track") == "track")]
        if d.get("images"):
            c["cover_url"] = d["images"][0].get("uri150") and d["images"][0].get("uri")
        c["year"] = c.get("year") or str(d.get("year", ""))
    c.update(tracks=tracks, lazy=False)
    return c


def cover_from_itunes(artist, title):
    """표지 보충: iTunes에서 앨범 표지를 찾는다."""
    try:
        r = requests.get("https://itunes.apple.com/search", params={"term": f"{artist} {title}".strip(),
                         "entity": "album", "limit": 1}, timeout=15).json().get("results", [])
        if r:
            return r[0]["artworkUrl100"].replace("100x100bb", "600x600bb")
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def download_cover(url, key):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": f"CDShelf/{__version__}"})
        if r.status_code == 200 and r.content:
            fname = f"{key}.jpg"
            (COVER_DIR / fname).write_bytes(r.content)
            return fname
    except requests.RequestException:
        pass
    return None


def fetch_cover(mbid):
    try:
        r = requests.get(f"https://coverartarchive.org/release/{mbid}/front-500", timeout=15)
        if r.status_code == 200 and r.content:
            fname = f"{mbid}.jpg"
            (COVER_DIR / fname).write_bytes(r.content)
            return fname
    except requests.RequestException:
        pass
    return None


def save_album(data):
    with db() as con:
        exists = data.get("mbid") and con.execute("SELECT id FROM albums WHERE mbid=?", (data["mbid"],)).fetchone()
        if exists:
            return exists["id"], False
        cover = fetch_cover(data["mbid"]) if data.get("mbid") else None
        key = data.get("mbid") or f"x{abs(hash((data['title'], data.get('artist'))))}"
        if not cover and data.get("cover_url"):
            cover = download_cover(data["cover_url"], key)
        if not cover and data.get("artist") is not None:
            url = cover_from_itunes(data.get("artist") or "", data["title"])
            cover = download_cover(url, key) if url else None
        cur = con.execute(
            "INSERT INTO albums (mbid, discid, title, artist, year, label, country, cover) VALUES (?,?,?,?,?,?,?,?)",
            (data.get("mbid"), data.get("discid"), data["title"], data.get("artist"), data.get("year"),
             data.get("label"), data.get("country"), cover),
        )
        aid = cur.lastrowid
        for t in data["tracks"]:
            con.execute(
                "INSERT INTO tracks (album_id, disc_no, number, title, length_ms) VALUES (?,?,?,?,?)",
                (aid, data.get("disc_no", 1), t["number"], t["title"], t.get("length_ms", 0)),
            )
        return aid, True


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# ---------- 화면 ----------
CSS = """
<style>
:root{--bg:#111;--card:#1c1c1e;--fg:#f2f2f2;--mut:#9a9aa0;--acc:#ffb020}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"Malgun Gothic","Noto Sans KR",sans-serif;background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;background:#000d;backdrop-filter:blur(8px);padding:12px 16px;display:flex;gap:8px;align-items:center;z-index:5}
header a.logo{color:var(--acc);font-weight:700;text-decoration:none;white-space:nowrap}
header form{flex:1;display:flex;gap:6px}
input,textarea,button,.btn{font:inherit;border-radius:10px;border:1px solid #333;background:#222;color:var(--fg);padding:9px 12px}
header input{flex:1;min-width:0}
button,.btn{background:var(--acc);color:#000;border:0;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
button.ghost{background:#2a2a2d;color:var(--fg);font-weight:400}
main{padding:16px;max-width:1100px;margin:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
.card{background:var(--card);border-radius:12px;overflow:hidden;text-decoration:none;color:inherit}
.card img,.ph{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#2a2a2d}
.ph{display:flex;align-items:center;justify-content:center;font-size:40px}
.card div.t{padding:8px 10px;font-size:14px}.sub{color:var(--mut);font-size:12px}
.stars{color:var(--acc);letter-spacing:1px}
.row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}.row img,.row .ph{width:220px;border-radius:12px}
table{width:100%;border-collapse:collapse}td{padding:10px 6px;border-bottom:1px solid #2a2a2d}
.star{cursor:pointer;font-size:22px;color:#555;user-select:none}.star.on{color:var(--acc)}
.msg{background:#2a2410;border:1px solid #5a4a10;padding:12px;border-radius:10px;margin-bottom:14px}
footer{padding:20px 16px;text-align:center}
</style>
"""

LAYOUT = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CD 보관함</title>""" + CSS + """</head><body>
<header><a class="logo" href="/">💿 CD 보관함</a>
<form action="/" method="get"><input name="q" placeholder="앨범·아티스트·곡 검색" value="{{ q|default('') }}"><button>검색</button></form>
<a class="btn" href="/scan">+ CD</a></header>
<main><div id="upd" class="msg" style="display:none"></div>{{ body|safe }}</main>
<footer class="sub">v{{ ver }} · <button type="button" class="ghost" id="chk">업데이트 확인</button></footer>
<script>
async function checkUpdate(manual){
  try{
    const r=await fetch('/api/update/check'); const j=await r.json(); const b=document.getElementById('upd');
    if(j.error){ if(manual) alert('업데이트 확인 실패: '+j.error); return; }
    if(j.newer){
      b.style.display='block';
      b.innerHTML='새 버전 '+j.latest+' 을(를) 사용할 수 있습니다. <button id="ub">지금 업데이트</button>';
      document.getElementById('ub').onclick=async function(){
        this.disabled=true; this.textContent='다운로드·설치 중... 앱이 곧 다시 시작됩니다';
        const a=await fetch('/api/update/apply',{method:'POST'}); const k=await a.json();
        if(k.error){ alert(k.error); this.disabled=false; this.textContent='지금 업데이트'; }
      };
    } else if(manual){ alert('최신 버전입니다 (v'+j.current+')'); }
  }catch(err){ if(manual) alert('업데이트 확인 실패'); }
}
document.getElementById('chk').onclick=function(){ checkUpdate(true); };
if(!sessionStorage.getItem('upd_checked')){ sessionStorage.setItem('upd_checked','1'); checkUpdate(false); }
</script>
</body></html>"""


def page(body, **kw):
    return render_template_string(LAYOUT, body=body, ver=__version__, **kw)


def stars_html(v):
    n = int(round(v or 0))
    return '<span class="stars">' + "★" * n + "☆" * (5 - n) + "</span>"


@app.route("/api/ping")
def ping():
    return "cdshelf"


@app.route("/api/update/check")
def update_check():
    return jsonify(updater.check())


@app.route("/api/update/apply", methods=["POST"])
def update_apply():
    if request.remote_addr not in ("127.0.0.1", "::1"):  # 같은 PC의 앱 창에서만 허용
        return jsonify(error="이 PC의 앱에서만 업데이트할 수 있습니다."), 403
    try:
        updater.download_and_install()
    except Exception as ex:
        return jsonify(error=str(ex)), 400
    return jsonify(ok=True)


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    sql = """SELECT a.*, (SELECT AVG(rating) FROM tracks WHERE album_id=a.id AND rating>0) AS avg,
             (SELECT COUNT(*) FROM tracks WHERE album_id=a.id) AS n FROM albums a"""
    params = []
    if q:
        like = f"%{q}%"
        sql += """ WHERE a.title LIKE ? OR a.artist LIKE ? OR a.label LIKE ? OR a.year LIKE ?
                   OR a.id IN (SELECT album_id FROM tracks WHERE title LIKE ?)"""
        params = [like] * 5
    sql += " ORDER BY a.artist COLLATE NOCASE, a.year"
    with db() as con:
        rows = con.execute(sql, params).fetchall()
    cards = ""
    for r in rows:
        img = f'<img src="/covers/{e(r["cover"])}" loading="lazy">' if r["cover"] else '<div class="ph">💿</div>'
        avg = f'{stars_html(r["avg"])} <span class="sub">{r["avg"]:.1f}</span>' if r["avg"] else '<span class="sub">미평가</span>'
        cards += (f'<a class="card" href="/album/{r["id"]}">{img}<div class="t"><b>{e(r["title"])}</b><br>'
                  f'<span class="sub">{e(r["artist"] or "")} · {e(r["year"] or "")}</span><br>{avg}</div></a>')
    head = f'<p class="sub">총 {len(rows)}장'
    ip = lan_ip()
    if ip:
        head += f' · 폰에서 접속: http://{ip}:{PORT}'
    head += '</p>'
    if not rows:
        head += '<p>아직 없습니다. 우측 상단 “+ CD”로 첫 CD를 등록하세요.</p>'
    return page(head + f'<div class="grid">{cards}</div>', q=q)


@app.route("/album/<int:aid>")
def album(aid):
    with db() as con:
        a = con.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
        if not a:
            return redirect("/")
        tr = con.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY disc_no, number", (aid,)).fetchall()
    rated = [t["rating"] for t in tr if t["rating"]]
    mean = sum(rated) / len(rated) if rated else 0
    img = f'<img src="/covers/{e(a["cover"])}">' if a["cover"] else '<div class="ph">💿</div>'
    rows = ""
    for t in tr:
        secs = (t["length_ms"] or 0) // 1000
        dur = f"{secs // 60}:{secs % 60:02d}" if secs else ""
        st = "".join(f'<span class="star {"on" if i <= t["rating"] else ""}" data-v="{i}">★</span>' for i in range(1, 6))
        rows += f'<tr data-id="{t["id"]}"><td class="sub">{t["number"]}</td><td>{e(t["title"])}</td><td class="sub">{dur}</td><td>{st}</td></tr>'
    body = f"""<div class="row">{img}<div><h2>{e(a["title"])}</h2><div>{e(a["artist"] or "")}</div>
    <p class="sub">{e(a["year"] or "")} · {e(a["label"] or "")} · {e(a["country"] or "")}</p>
    <p>앨범 평균 <b id="avg">{mean:.1f}</b> <span class="sub">({len(rated)}/{len(tr)}곡 평가)</span></p>
    <form method="post" action="/album/{a["id"]}/delete" onsubmit="return confirm('삭제할까요?')"><button style="background:#333;color:#fff">삭제</button></form></div></div>
    <table id="tracks">{rows}</table>
    <script>
    document.querySelectorAll('#tracks tr').forEach(tr=>{{
      tr.querySelectorAll('.star').forEach(s=>s.onclick=async()=>{{
        const cur=tr.querySelectorAll('.star.on').length; let v=+s.dataset.v; if(v===cur) v=0;
        const r=await fetch('/api/rate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{track_id:tr.dataset.id,rating:v}})}});
        const j=await r.json();
        tr.querySelectorAll('.star').forEach(x=>x.classList.toggle('on',+x.dataset.v<=v));
        document.getElementById('avg').textContent=j.avg.toFixed(1);
      }});
    }});
    </script>"""
    return page(body)


@app.route("/api/rate", methods=["POST"])
def rate():
    d = request.get_json()
    v = max(0, min(5, int(d["rating"])))
    with db() as con:
        con.execute("UPDATE tracks SET rating=? WHERE id=?", (v, d["track_id"]))
        aid = con.execute("SELECT album_id FROM tracks WHERE id=?", (d["track_id"],)).fetchone()["album_id"]
        avg = con.execute("SELECT AVG(rating) FROM tracks WHERE album_id=? AND rating>0", (aid,)).fetchone()[0] or 0
    return jsonify(avg=avg)


@app.route("/album/<int:aid>/delete", methods=["POST"])
def delete(aid):
    with db() as con:
        c = con.execute("SELECT cover FROM albums WHERE id=?", (aid,)).fetchone()
        con.execute("DELETE FROM albums WHERE id=?", (aid,))
    if c and c["cover"]:
        (COVER_DIR / c["cover"]).unlink(missing_ok=True)
    return redirect("/")


@app.route("/covers/<path:f>")
def covers(f):
    return send_from_directory(COVER_DIR, f)


@app.route("/scan", methods=["GET", "POST"])
def scan(msg=""):
    if request.method == "POST" and request.endpoint == "scan":
        PENDING.clear()
        try:
            disc = read_disc()
            found, errs = [], []
            for name, fn in (("MusicBrainz", lambda: lookup_releases(disc.id, disc.toc_string)),
                             ("GnuDB", lambda: lookup_gnudb(disc.toc_string))):
                try:
                    found += fn()
                except Exception as ex:  # 한 곳이 실패해도 다른 곳은 계속 조회
                    errs.append(f"{name}: {ex}")
            PENDING.extend(found)
            if not found:
                msg = (f'CD는 정상적으로 읽었지만(트랙 {len(disc.tracks)}개) MusicBrainz·GnuDB에 '
                       f'이 디스크(ID {disc.id})가 없습니다. 아래에서 앨범명으로 검색하거나 수동 입력하세요.')
                if errs:
                    msg += " (" + " / ".join(errs) + ")"
        except ImportError:
            msg = "CD 읽기 라이브러리(discid)를 불러오지 못했습니다."
        except Exception as ex:  # 드라이브 비어 있음, 네트워크 오류 등
            msg = f"읽기 실패: {ex}"
    cand = ""
    for i, c in enumerate(PENDING):
        cand += f'''<form method="post" action="/scan/save" class="card" style="padding:12px;margin-bottom:10px;display:block">
        <input type="hidden" name="i" value="{i}"><b>{e(c["title"])}</b> — {e(c["artist"])}
        <div class="sub">{e(c["year"])} · {e(c["label"])} · {e(c["country"])} · {c.get("n") or len(c["tracks"])}곡</div>
        <button style="margin-top:8px">이 앨범으로 저장</button></form>'''
    body = f"""<h2>CD 등록</h2><p class="sub">CD를 드라이브에 넣고 버튼을 누르세요.</p>
    {f'<div class="msg">{e(msg)}</div>' if msg else ''}
    <form method="post"><button>💿 CD 읽기</button></form><br>{cand}
    <hr style="border-color:#2a2a2d;margin:24px 0"><h3>앨범명·아티스트로 검색</h3>
    <form method="post" action="/scan/search" style="display:flex;gap:6px">
    <input name="q" placeholder="예: 아이유 Palette" required style="flex:1"><button>검색</button></form>
    <hr style="border-color:#2a2a2d;margin:24px 0"><h3>수동 입력 (조회 실패 시)</h3>
    <form method="post" action="/manual" style="display:grid;gap:8px">
    <input name="title" placeholder="앨범명" required><input name="artist" placeholder="아티스트">
    <input name="year" placeholder="발매연도"><textarea name="tracks" rows="8" placeholder="트랙 제목을 한 줄에 하나씩" required></textarea>
    <button>저장</button></form>"""
    return page(body)


@app.route("/scan/save", methods=["POST"])
def scan_save():
    idx = int(request.form["i"])
    if idx >= len(PENDING):
        return redirect("/scan")
    c = PENDING[idx]
    if c.get("lazy"):
        try:
            resolve_lazy(c)
        except Exception as ex:
            return page(f'<div class="msg">트랙 정보를 가져오지 못했습니다: {e(ex)}</div><a class="btn" href="/scan">돌아가기</a>')
    aid, _ = save_album(c)
    PENDING.clear()
    return redirect(url_for("album", aid=aid))


@app.route("/scan/search", methods=["POST"])
def scan_search():
    PENDING.clear()
    msg = ""
    try:
        PENDING.extend(search_releases_text(request.form["q"].strip()))
        if not PENDING:
            msg = "검색 결과가 없습니다. 수동 입력을 쓰세요."
    except Exception as ex:
        msg = f"검색 실패: {ex}"
    return scan(msg)


@app.route("/manual", methods=["POST"])
def manual():
    lines = [l.strip() for l in request.form["tracks"].splitlines() if l.strip()]
    data = {"mbid": None, "title": request.form["title"], "artist": request.form.get("artist"),
            "year": request.form.get("year"), "tracks": [{"number": i + 1, "title": t} for i, t in enumerate(lines)]}
    aid, _ = save_album(data)
    return redirect(url_for("album", aid=aid))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT)
