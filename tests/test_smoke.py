"""Smoke tests for recall-forge. Run: python3 tests/test_smoke.py"""
import os, sys, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["RECALL_FORGE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from analyzer import analyze, build_schedule, sm2_intervals, verdict
from cards import segment_source, build_cards, collect_acronyms
import app as flask_app

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  -- " + str(extra)) if (extra and not cond) else ""))

GOOD = """Mitochondria are the organelles responsible for cellular respiration. They contain their own DNA.
The inner mitochondrial membrane is where the electron transport chain sits.
Oxidative phosphorylation produces roughly 26 to 28 ATP per glucose molecule.

CRISPR is a gene-editing system derived from bacterial immune defenses. Cas9 cuts DNA at a guide RNA site.
Off-target edits occur when Cas9 cuts similar sequences elsewhere in the genome.

Backoff works by increasing the wait interval after each failed attempt.
Exponential backoff doubles the interval each time; jitter prevents synchronized retries across clients.
Retry-After is the standard HTTP header a server uses to tell a client how long to wait."""

BULLETS = """- TCP is a connection-oriented transport protocol.
- UDP is a connectionless transport protocol that does not guarantee delivery.
- The three-way handshake is SYN, SYN-ACK, ACK.
- TLS provides confidentiality and integrity for application data.
- A certificate authority issues the certificates that anchor TLS trust."""

BAD = "This is important. It means stuff. Approximately several things happen etc."

print("\n== segmentation ==")
segs, noise, bullets = segment_source(GOOD)
check("prose splits into sentence-level atoms", len(segs) >= 8, len(segs))
bsegs, bnoise, bbul = segment_source(BULLETS)
check("bullet list detected", len(bsegs) == 5, len(bsegs))
check("bullet lines counted", bbul == 5, bbul)
check("prose source has no bullets", bullets == 0, bullets)
noise_segs, noise_n, _ = segment_source("Page 12\nhttps://example.com/x\nReal content about widgets here.")
check("noise lines skipped", noise_n == 2, noise_n)

print("\n== acronym collection ==")
acs = collect_acronyms("CRISPR pairs with Cas9. CRISPR needs a guide RNA. HTTP is a protocol. HTTP is stateless.")
check("multi-occurrence acronyms found", "CRISPR" in acs and "HTTP" in acs, acs)
check("single-occurrence token excluded", "Cas9" not in acs, acs)

print("\n== style distribution ==")
from cards import _style_sequence
seq = _style_sequence({"qa": 40, "cloze": 25, "definition": 20, "why": 15}, 100)
check("sequence is full length", len(seq) == 100, len(seq))
check("all four styles requested appear", set(seq) == {"qa", "cloze", "definition", "why"}, sorted(set(seq)))
check("styles are interleaved not clustered", seq.index("why") < 20 and seq[:40].count("qa") < 40, seq[:20])
check("mix proportions roughly honoured", abs(seq.count("qa") - 40) <= 3, seq.count("qa"))
cards = build_cards(segs, {"qa": 40, "cloze": 25, "definition": 20, "why": 15}, 40, acs)
kinds = [c["kind"] for c in cards]
check("deck generated", len(cards) >= 8, len(cards))
check("cloze cards present", kinds.count("cloze") >= 1, kinds.count("cloze"))
check("no duplicate fronts", len({c["front"] for c in cards}) == len(cards))

CAUSAL = """
Spaced repetition works because forgetting is the signal that memory needs strengthening.
Review timing matters because recall at the edge of forgetting is what consolidates a memory.
Interleaving helps because discrimination between similar items strengthens each pathway.
Active recall works because retrieving information strengthens the retrieval path itself."""
ccards = build_cards(segment_source(CAUSAL)[0], {"why": 100}, 10, [])
check("causal lines become why-cards", sum(1 for c in ccards if c["kind"] == "why") >= 3,
      [(c["kind"], c["front"][:40]) for c in ccards])

print("\n== definition quality ==")
defs = [c["front"] for c in cards if c["kind"] == "definition"]
check("no What-is-The artifact", not any(f.startswith("What is The ") for f in defs), defs)
check("no question-fragment fronts", not any(f.startswith("What is Sleep") for f in defs), defs)
check("no Note filler card", not any(c["front"].lower().startswith("what is note") for c in cards))

print("\n== hyphen is not a colon-definition ==")
hy = segment_source("Light-dependent reactions happen in the thylakoid membrane and produce ATP and NADPH.")[0]
hc = build_cards(hy, {"definition": 100}, 5, [])
check("Light-dependent stays one card", len(hc) == 1, [c["front"] for c in hc])
check("no What-is-Light card", not any(c["front"] == "What is Light?" for c in hc))

print("\n== scoring: good source ==")
r = analyze(GOOD, intent="retention", max_cards=24)
check("score in range", 0 <= r["score"] <= 100, r["score"])
check("all five axes present", set(r["axes"]) == {"atomicity","clarity","self_contained","difficulty","coverage"})
check("top_fixes present", isinstance(r["top_fixes"], list))
check("every card scored", len(r["card_scores"]) == len(r["cards"]))
check("good source scores >= 70", r["score"] >= 70, r["score"])

print("\n== scoring: bad source is gated ==")
rb = analyze(BAD, max_cards=10)
check("garbage capped below 50", rb["score"] <= 45, rb["score"])
check("source_too_short flagged", any(f["code"] == "source_too_short" for f in rb["findings"]))
re_ = analyze("", max_cards=10)
check("empty source gated hard", re_["score"] <= 25, re_["score"])
check("empty source has zero coverage", re_["axes"]["coverage"] == 0)

print("\n== intent reweighting ==")
ra = analyze(GOOD, intent="exam", max_cards=24)
check("exam weights differ from retention", ra["weights"] != r["weights"])
check("retention weights difficulty highest", r["weights"]["difficulty"] == max(r["weights"].values()))

print("\n== SM-2 scheduler ==")
lad = sm2_intervals()
check("ladder starts 1 then 6", lad[0] == 1 and lad[1] == 6, lad[:3])
check("ladder is non-decreasing", all(b >= a for a, b in zip(lad, lad[1:])), lad)
big = build_schedule([{"id": str(i)} for i in range(120)], horizon_days=90, daily_new_cap=10)
check("120 cards at cap 10 completes day 12", big["completion_day"] == 12, big["completion_day"])
check("all 120 scheduled", big["cards_scheduled"] == 120, big["cards_scheduled"])
check("reviews appear after intro phase", any(d["reviews"] > 0 for d in big["days"][11:]), big["days"][12])
tight = build_schedule([{"id": str(i)} for i in range(400)], horizon_days=45, daily_new_cap=5)
check("400 cards at cap 5 overflows 45d", tight["unscheduled"] == 400 - 5 * 45, tight["unscheduled"])
check("overload days reported as a list", isinstance(tight["overload_days"], list))

print("\n== verdict bands ==")
check("verdict(90) is deck_ready", verdict(90) == "deck_ready")
check("verdict(60) is needs_polish", verdict(60) == "needs_polish")
check("verdict(10) is needs_rework", verdict(10) == "needs_rework")

print("\n== flask api ==")
c = flask_app.app.test_client()
check("health ok", c.get("/api/health").get_json()["ok"] is True)
check("sample returns source", len(c.get("/api/sample").get_json()["source"]) > 200)
check("index serves html", c.get("/").status_code == 200)
check("static css serves", c.get("/style.css").status_code == 200)
check("static js serves", c.get("/app.js").status_code == 200)
res = c.post("/api/analyze", json={"source": GOOD, "max_cards": 10}).get_json()
check("analyze returns a session_id", "session_id" in res)
sid = res["session_id"]
check("sessions list is non-empty", len(c.get("/api/sessions").get_json()["sessions"]) >= 1)
check("session round-trips", c.get("/api/sessions/" + sid).get_json()["score"] == res["score"])
p = c.patch("/api/sessions/" + sid, json={"self_rating": 4, "notes": "solid"}).get_json()
check("patch applies rating", p["self_rating"] == 4)
sess2 = c.get("/api/sessions/" + sid).get_json()
check("rating and notes persist", sess2["self_rating"] == 4 and sess2["notes"] == "solid")
tsv = c.get("/api/export/" + sid + "?fmt=tsv")
check("tsv export downloads", tsv.status_code == 200 and "attachment" in tsv.headers.get("Content-Disposition", ""))
rows = [r for r in tsv.get_data(as_text=True).strip().split("\n") if r]
check("tsv row count matches card count", len(rows) == len(res["cards"]), (len(rows), len(res["cards"])))
check("every tsv row has 3 columns", all(len(r.split("\t")) == 3 for r in rows))
csv_out = c.get("/api/export/" + sid + "?fmt=csv").get_data(as_text=True)
check("csv has a header row", csv_out.split("\n")[0].startswith("front,back"))
md = c.get("/api/export/" + sid + "?fmt=md").get_data(as_text=True)
check("markdown report has axis table", "| Axis | Score |" in md)
check("markdown report has schedule", "14-day review load" in md)
check("json export parses", isinstance(json.loads(c.get("/api/export/" + sid + "?fmt=json").get_data(as_text=True)), dict))
check("cards endpoint returns cards", len(c.post("/api/cards", json={"source": GOOD}).get_json()["cards"]) > 0)
check("empty source returns 400", c.post("/api/analyze", json={"source": "  "}).status_code == 400)
check("missing session returns 404", c.get("/api/sessions/nope").status_code == 404)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILURES:")
    [print("  -", f) for f in FAIL]
    sys.exit(1)
print("ALL GREEN")
