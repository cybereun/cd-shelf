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


def lookup_releases(disc_id):
    import musicbrainzngs as mb

    mb.set_useragent("CDShelf", __version__, "https://github.com/cybereun/cd-shelf")
    res = mb.get_releases_by_discid(disc_id, includes=["artists", "recordings", "labels"])
    out = []
    for rel in res.get("disc", {}).get("release-list", []):
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
def scan():
    msg = ""
    if request.method == "POST":
        PENDING.clear()
        try:
            disc = read_disc()
            found = lookup_releases(disc.id)
            PENDING.extend(found)
            if not found:
                msg = f'MusicBrainz에 이 디스크(ID {disc.id})가 없습니다. 아래 수동 입력을 쓰세요.'
        except ImportError:
            msg = "CD 읽기 라이브러리(discid)를 불러오지 못했습니다."
        except Exception as ex:  # 드라이브 비어 있음, 네트워크 오류 등
            msg = f"읽기 실패: {ex}"
    cand = ""
    for i, c in enumerate(PENDING):
        cand += f'''<form method="post" action="/scan/save" class="card" style="padding:12px;margin-bottom:10px;display:block">
        <input type="hidden" name="i" value="{i}"><b>{e(c["title"])}</b> — {e(c["artist"])}
        <div class="sub">{e(c["year"])} · {e(c["label"])} · {e(c["country"])} · {len(c["tracks"])}곡</div>
        <button style="margin-top:8px">이 앨범으로 저장</button></form>'''
    body = f"""<h2>CD 등록</h2><p class="sub">CD를 드라이브에 넣고 버튼을 누르세요.</p>
    {f'<div class="msg">{e(msg)}</div>' if msg else ''}
    <form method="post"><button>💿 CD 읽기</button></form><br>{cand}
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
    aid, _ = save_album(PENDING[idx])
    PENDING.clear()
    return redirect(url_for("album", aid=aid))


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
