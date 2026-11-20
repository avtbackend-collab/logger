"""Exfil event logger: accepts events, renders them organized by run and phase."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

from flask import (Flask, Response, abort, jsonify, redirect, render_template_string,
                   request, url_for)

DB_PATH = os.environ.get("LOG_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs.db"))
TOKEN = os.environ.get("LOG_TOKEN", "")
KEEP_DAYS = int(os.environ.get("LOG_KEEP_DAYS", "30"))
MAX_BODY_EVENTS = 500

app = Flask(__name__)

PHASE_ORDER = ["init", "launch", "scan", "preflight", "repo", "mirror", "commit", "push", "done"]
BAD_STATUSES = {"failed", "cmd_failed", "auth_failed"}
WARN_STATUSES = {"warn"}
GOOD_STATUSES = {"pushed", "run_complete", "cmd_ok"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  step INTEGER,
  phase TEXT,
  status TEXT NOT NULL,
  repo TEXT,
  message TEXT,
  data TEXT,
  error TEXT,
  ts REAL NOT NULL,
  iso TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_repo ON events(repo);
"""


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)


init_db()


def authorized() -> bool:
    if not TOKEN:
        return True
    supplied = request.args.get("t") or request.headers.get("X-Log-Token", "")
    return supplied == TOKEN


def guard():
    if not authorized():
        abort(401)


def _store(events):
    rows = []
    now = time.time()
    for ev in events:
        if not isinstance(ev, dict) or not ev.get("status"):
            continue
        run_id = str(ev.get("run_id") or "unknown")
        try:
            seq = int(ev.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        ts = ev.get("timestamp")
        if not isinstance(ts, (int, float)):
            ts = now
        iso = ev.get("iso") or datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
        rows.append((
            run_id, seq, ev.get("step"), str(ev.get("phase") or "init"), str(ev["status"]),
            ev.get("repo"),
            str(ev.get("message") or "")[:4000],
            json.dumps(ev.get("data"), sort_keys=True)[:20000] if ev.get("data") else None,
            str(ev.get("error"))[:8000] if ev.get("error") else None,
            float(ts), str(iso),
        ))
    if not rows:
        return 0
    with db() as conn:
        conn.executemany(
            "INSERT INTO events (run_id, seq, step, phase, status, repo, message, data, error, ts, iso)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        cutoff = time.time() - KEEP_DAYS * 86400
        conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    return len(rows)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, t=time.time())


@app.post("/log")
def log_one():
    guard()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="expected object"), 400
    return jsonify(ok=True, stored=_store([payload]))


@app.post("/bulk")
def log_bulk():
    guard()
    payload = request.get_json(silent=True)
    events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        return jsonify(ok=False, error="expected list"), 400
    if len(events) > MAX_BODY_EVENTS:
        events = events[:MAX_BODY_EVENTS]
    return jsonify(ok=True, stored=_store(events))


def _runs(query: str = "", status: str = "", repo: str = "", limit: int = 60):
    where, params = ["1=1"], []
    if query:
        where.append("(message LIKE ? OR error LIKE ? OR repo LIKE ? OR run_id LIKE ?)")
        params += [f"%{query}%"] * 4
    if status:
        where.append("status = ?")
        params.append(status)
    if repo:
        where.append("repo = ?")
        params.append(repo)
    sql = f"""
        SELECT run_id,
               MIN(ts) AS started,
               MAX(ts) AS ended,
               COUNT(*) AS total,
               SUM(status IN ('failed','cmd_failed','auth_failed')) AS errors,
               SUM(status = 'warn') AS warnings,
               SUM(status = 'pushed') AS pushes,
               MAX(host) AS host,
               MAX(base) AS base,
               MAX(CASE WHEN status = 'run_complete' THEN message END) AS outcome
        FROM (SELECT e.*,
                     json_extract(e.data, '$.host') AS host,
                     json_extract(e.data, '$.base') AS base
              FROM events e)
        WHERE {' AND '.join(where)}
        GROUP BY run_id
        ORDER BY started DESC
        LIMIT ?
    """
    params.append(limit)
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def _repos():
    with db() as conn:
        return [r["repo"] for r in conn.execute(
            "SELECT DISTINCT repo FROM events WHERE repo IS NOT NULL ORDER BY repo")]


@app.get("/")
def index():
    guard()
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    repo = (request.args.get("repo") or "").strip()
    runs = _runs(q, status, repo)
    return Response(render_template_string(
        INDEX_TPL, runs=runs, q=q, status=status, repo=repo, repos=_repos(),
        token=TOKEN), mimetype="text/html")


@app.get("/run/<run_id>")
def run_detail(run_id):
    guard()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY "
            "CASE phase " + " ".join(f"WHEN '{p}' THEN {i}" for i, p in enumerate(PHASE_ORDER))
            + " ELSE 99 END, seq, id", (run_id,)).fetchall()
    if not rows:
        abort(404)
    grouped = {}
    for row in rows:
        grouped.setdefault(row["phase"] or "init", []).append(row)
    return Response(render_template_string(
        RUN_TPL, run_id=run_id, grouped=grouped, rows=rows, token=TOKEN), mimetype="text/html")


@app.get("/run/<run_id>.json")
def run_json(run_id):
    guard()
    with db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE run_id = ? ORDER BY seq, id",
                            (run_id,)).fetchall()
    return jsonify(run_id=run_id, events=[dict(r) for r in rows])


@app.get("/api/runs.json")
def api_runs():
    guard()
    return jsonify(runs=[dict(r) for r in _runs(limit=200)])


STYLE = """
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--txt:#d7dce5;--dim:#8b95a7;
--good:#2ecc71;--warn:#f0b429;--bad:#ef5350;--accent:#5b9cf5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:5;background:#12151b;border-bottom:1px solid var(--line);padding:12px 18px}
h1{margin:0 0 10px;font-size:16px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
form{display:flex;gap:8px;flex-wrap:wrap}
input,select,button{background:#0d1015;color:var(--txt);border:1px solid var(--line);
border-radius:6px;padding:7px 10px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--accent)}
main{padding:18px;max-width:1400px;margin:0 auto}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
tr:hover td{background:#141821}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;
border:1px solid var(--line);white-space:nowrap}
.b-failed,.b-cmd_failed,.b-auth_failed{color:var(--bad);border-color:#5c2a2a;background:#2a1618}
.b-warn{color:var(--warn);border-color:#5c4a1c;background:#2a2413}
.b-pushed,.b-run_complete{color:var(--good);border-color:#1f4d33;background:#12251b}
.b-info,.b-step,.b-cmd_ok,.b-run_start,.b-spool,.b-match{color:var(--dim)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.err{color:var(--bad);white-space:pre-wrap;word-break:break-word}
details{margin:6px 0}summary{cursor:pointer;color:var(--dim)}
pre{margin:6px 0;padding:10px;background:#0b0d11;border:1px solid var(--line);
border-radius:6px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.meta{color:var(--dim);font-size:12px;margin:0 0 14px}
.phase{margin:22px 0 8px;color:var(--accent);text-transform:uppercase;letter-spacing:.08em;font-size:12px}
.empty{color:var(--dim);padding:40px;text-align:center}
"""

INDEX_TPL = """<!doctype html><html><head><meta charset="utf-8">
<title>exfil runs</title><style>{{ style }}</style>
<meta http-equiv="refresh" content="30"></head><body>
<header><h1>exfil runs</h1>
<form method="get">
<input name="q" value="{{ q }}" placeholder="search message / repo / run id" size="34">
<select name="status"><option value="">any status</option>
{% for s in ['failed','cmd_failed','auth_failed','warn','pushed','run_complete'] %}
<option value="{{ s }}" {% if status==s %}selected{% endif %}>{{ s }}</option>{% endfor %}
</select>
<select name="repo"><option value="">any repo</option>
{% for r in repos %}<option value="{{ r }}" {% if repo==r %}selected{% endif %}>{{ r }}</option>{% endfor %}
</select>
<button>filter</button><button type="button" onclick="location.href='/'">reset</button>
</form></header><main>
{% if not runs %}<div class="empty">No runs yet.</div>{% else %}
<table><thead><tr><th>run</th><th>started</th><th class="num">events</th>
<th class="num">errors</th><th class="num">warns</th><th class="num">pushes</th>
<th>host</th><th>outcome</th></tr></thead><tbody>
{% for r in runs %}<tr>
<td><a href="/run/{{ r.run_id }}{% if token %}?t={{ token }}{% endif %}">{{ r.run_id }}</a></td>
<td>{{ r.started | int | datetime }}</td>
<td class="num">{{ r.total }}</td>
<td class="num">{{ r.errors or 0 }}</td>
<td class="num">{{ r.warnings or 0 }}</td>
<td class="num">{{ r.pushes or 0 }}</td>
<td>{{ r.host or '—' }}</td>
<td>{{ r.outcome or '—' }}</td>
</tr>{% endfor %}</tbody></table>{% endif %}
</main></body></html>"""

RUN_TPL = """<!doctype html><html><head><meta charset="utf-8">
<title>run {{ run_id }}</title><style>{{ style }}</style></head><body>
<header><h1>run {{ run_id }}</h1>
<p class="meta">{{ rows|length }} events · <a href="/run/{{ run_id }}.json{% if token %}?t={{ token }}{% endif %}">raw json</a>
 · <a href="/{% if token %}?t={{ token }}{% endif %}">all runs</a></p></header><main>
{% for phase, items in grouped.items() %}
<div class="phase">{{ phase }} · {{ items|length }}</div>
<table><tbody>
{% for e in items %}<tr>
<td class="num" style="width:44px">{{ e.step if e.step is not none else '' }}</td>
<td style="width:150px">{{ e.iso }}</td>
<td style="width:120px"><span class="badge b-{{ e.status }}">{{ e.status }}</span></td>
<td style="width:160px">{{ e.repo or '' }}</td>
<td>{{ e.message }}
{% if e.data %}<details><summary>data</summary><pre>{{ e.data }}</pre></details>{% endif %}
{% if e.error %}<div class="err">{{ e.error }}</div>{% endif %}</td>
</tr>{% endfor %}</tbody></table>
{% endfor %}</main></body></html>"""


@app.template_filter("datetime")
def _dt(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "—"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
