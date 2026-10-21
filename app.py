"""recall-forge -- local-first spaced-repetition flashcard deck builder."""

import json
import os
import re
import sqlite3
import csv
import io
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_file, send_from_directory

from analyzer import analyze

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RECALL_FORGE_DB", os.path.join(BASE, "recall.db"))

app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="")

SAMPLE = """Photosynthesis is the process plants use to convert light energy into chemical energy stored in glucose. Light-dependent reactions happen in the thylakoid membrane and produce ATP and NADPH. The Calvin cycle happens in the stroma and uses ATP and NADPH to fix carbon dioxide into sugar.

The Krebs cycle: a series of metabolic reactions that oxidize acetyl-CoA into CO2, producing NADH, FGTP, and electron carriers. Occurs in the mitochondrial matrix. Produces about 3 NADH, 1 FGTP, and 2 CO2 per turn.

Memory consolidation is the process by which fragile new memories become stable long-term stores. Sleep plays a critical role because during slow-wave sleep the hippocampus replays recent experiences and transfers them to the neocortex. Interleaving related material produces better retention than blocking because it forces discrimination between similar items.

Compound interest means interest earned on both the original principal and on accumulated interest. A 7% annual return doubles roughly every 10 years (rule of 72: 72 / 7 = 10.3). Because growth is exponential, starting at 25 rather than 35 compounds into a much larger gap at 65.

HTTP 429 means the server is rate limiting the client. Retry-After header tells you how long to wait. Backoff should be exponential with jitter.

Note: see chapter 4
Page 12
https://example.com/whatever

The cell membrane is a phospholipid bilayer with embedded proteins. It is selectively permeable. Unlike a cell wall, it is flexible and dynamic rather than rigid.
""".strip()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        created_at TEXT,
        intent TEXT,
        context TEXT,
        score INTEGER,
        verdict TEXT,
        payload TEXT,
        self_rating INTEGER,
        notes TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC)")
    conn.commit()
    conn.close()


init_db()


def _styles_from_request(data):
    mix = data.get("style_mix") or {}
    clean = {}
    for k in ("qa", "cloze", "definition", "why"):
        try:
            v = int(mix.get(k, 0))
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            clean[k] = min(80, v)
    return clean or {"qa": 40, "cloze": 25, "definition": 20, "why": 15}


def run(data):
    return analyze(
        source=data.get("source", "") or "",
        intent=data.get("intent", "retention"),
        max_cards=int(data.get("max_cards") or 30),
        style_mix=_styles_from_request(data),
        horizon_days=int(data.get("horizon_days") or 45),
        daily_new_cap=int(data.get("daily_new_cap") or 20),
        context=data.get("context", "") or "",
    )


# ------------------------------------------------------------------ routes

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "recall-forge", "version": "1.0.0"})


@app.route("/api/sample")
def sample():
    return jsonify({"source": SAMPLE})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    if not (data.get("source") or "").strip():
        return jsonify({"error": "source is required"}), 400
    try:
        result = run(data)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"analysis failed: {exc}"}), 500

    conn = db()
    sid = f"rf_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}"
    conn.execute(
        "INSERT INTO sessions (id, created_at, intent, context, score, verdict, payload, self_rating, notes)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, datetime.now(timezone.utc).isoformat(), result["intent"],
         data.get("context", "") or "", result["score"], result["verdict"],
         json.dumps(result), None, None),
    )
    conn.commit()
    conn.close()
    result["session_id"] = sid
    return jsonify(result)


@app.route("/api/cards", methods=["POST"])
def api_cards():
    """Card-only view: returns just the generated deck (no scoring)."""
    data = request.get_json(silent=True) or {}
    if not (data.get("source") or "").strip():
        return jsonify({"error": "source is required"}), 400
    result = run(data)
    return jsonify({"cards": result["cards"], "stats": result["stats"], "kind_counts": result["stats"]["kinds"]})


@app.route("/api/sessions")
def api_sessions():
    conn = db()
    rows = conn.execute(
        "SELECT id, created_at, intent, context, score, verdict, self_rating, notes"
        " FROM sessions ORDER BY created_at DESC LIMIT 40"
    ).fetchall()
    conn.close()
    return jsonify({"sessions": [dict(r) for r in rows]})


@app.route("/api/sessions/<sid>")
def api_session(sid):
    conn = db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    payload = json.loads(row["payload"])
    payload["session_id"] = row["id"]
    payload["self_rating"] = row["self_rating"]
    payload["notes"] = row["notes"]
    payload["created_at"] = row["created_at"]
    return jsonify(payload)


@app.route("/api/sessions/<sid>", methods=["PATCH"])
def api_patch_session(sid):
    data = request.get_json(silent=True) or {}
    conn = db()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404
    rating = data.get("self_rating", None)
    if rating is not None:
        try:
            rating = max(1, min(5, int(rating)))
        except (TypeError, ValueError):
            rating = None
    conn.execute("UPDATE sessions SET self_rating = ?, notes = ? WHERE id = ?",
                 (rating, data.get("notes"), sid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "self_rating": rating})


def _payload(sid):
    conn = db()
    row = conn.execute("SELECT payload, score, verdict, intent FROM sessions WHERE id = ?", (sid,)).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["payload"]), row


def _tsv_cell(text):
    return re.sub(r"[\r\n\t]+", " ", str(text or ""))


@app.route("/api/export/<sid>")
def api_export(sid):
    found = _payload(sid)
    if not found:
        return jsonify({"error": "not found"}), 404
    result, row = found
    fmt = (request.args.get("fmt") or "md").lower()

    if fmt in ("tsv", "anki", "csv"):
        buf = io.StringIO()
        delim = "\t" if fmt in ("tsv", "anki") else ","
        w = csv.writer(buf, delimiter=delim, lineterminator="\n")
        if fmt == "csv":
            w.writerow(["front", "back", "kind", "tags", "source"])
        for c in result["cards"]:
            tags = " ".join(f"::{t}" if i else t for i, t in enumerate(c["tags"]))
            if fmt == "csv":
                w.writerow([_tsv_cell(c["front"]).replace('"', "'"),
                            _tsv_cell(c["back"]).replace('"', "'"),
                            c["kind"], _tsv_cell(tags).replace('"', "'"),
                            _tsv_cell(c["source"]).replace('"', "'")])
            else:
                # Anki expects: Front<tab>Back<tab>Tag1 Tag2
                w.writerow([_tsv_cell(c["front"]), _tsv_cell(c["back"]), _tsv_cell(tags)])
        mime = "text/tab-separated-values" if fmt != "csv" else "text/csv"
        return send_file(io.BytesIO(buf.getvalue().encode()), mimetype=mime,
                         as_attachment=True, download_name=f"recall-forge-{sid}.{fmt}")

    if fmt == "json":
        return send_file(io.BytesIO(json.dumps(result, indent=2).encode()), mimetype="application/json",
                         as_attachment=True, download_name=f"recall-forge-{sid}.json")

    return send_file(io.BytesIO(_to_markdown(result, row).encode()), mimetype="text/markdown",
                     as_attachment=True, download_name=f"recall-forge-{sid}.md")


def _to_markdown(result, row):
    plan, axes = result["plan"], result["axes"]
    L = []
    L.append(f"# recall-forge \u2014 deck review ({result['intent']} intent)")
    L.append("")
    L.append(f"**Score {result['score']}/100 \u2014 {result['verdict']}**  ")
    L.append(f"{result['blurb']}")
    L.append("")
    L.append("| Axis | Score |")
    L.append("| --- | --- |")
    for k in ("atomicity", "clarity", "self_contained", "difficulty", "coverage"):
        L.append(f"| {k.replace('_',' ')} | {axes[k]}/100 |")
    L.append("")
    st = result["stats"]
    L.append(f"- Cards: **{st['cards']}** from {st['segments']} source segment(s)")
    L.append(f"- Style mix: {st['kinds']}")
    L.append(f"- SM-2 ladder: {plan['sm2_ladder']} days \u2014 all new cards introduced by **day {plan['completion_day']}** ({plan['completion_date']})")
    L.append(f"- Peak day load: {plan['peak_load']} reviews \u2248 {plan['est_minutes_first_14d']} min over the first 14 days")
    L.append("")

    if result["findings"]:
        L.append("## Findings")
        L.append("")
        for f in result["findings"]:
            L.append(f"- **{f['code']}** ({f['severity']}) \u2014 \u201c{f['evidence']}\u201d  ")
            L.append(f"  - Fix: {f['fix']}")
        L.append("")

    L.append("## Cards")
    L.append("")
    for i, c in enumerate(result["cards"], 1):
        L.append(f"### {i}. [{c['kind']}] {c['front']}")
        L.append("")
        L.append(f"**Back:** {c['back']}")
        L.append("")
        L.append(f"<sub>tags: {' '.join(c['tags'])} &middot; source: {c['source']}</sub>")
        L.append("")

    L.append("## 14-day review load")
    L.append("")
    L.append("| Day | Date | New | Reviews | Total |")
    L.append("| --- | --- | --- | --- | --- |")
    for d in plan["days"][:14]:
        L.append(f"| {d['day']} | {d['date']} | {d['new']} | {d['reviews']} | {d['load']} |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8788))
    print(f"recall-forge -> http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)

