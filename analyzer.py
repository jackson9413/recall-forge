"""Scoring, risk flags, and the SM-2 spaced-repetition scheduler."""

import re
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

from cards import (
    _norm, _words, _content_words, segment_source, build_cards, collect_acronyms,
    VAGUE_PRONOUN_START_RE, VAGUE_QUANTIFIER_RE, PASSIVE_RE, MULTILIST_RE,
    WEAK_VERB_RE, NUMBER_RE, CONTRAST_RE,
)

# weights per deck intent -- retune the axis mix for what the deck is for
WEIGHTS = {
    "exam":       {"atomicity": 0.28, "clarity": 0.22, "self_contained": 0.22, "difficulty": 0.13, "coverage": 0.15},
    "retention":  {"atomicity": 0.20, "clarity": 0.18, "self_contained": 0.18, "difficulty": 0.30, "coverage": 0.14},
    "professional":{"atomicity": 0.22, "clarity": 0.26, "self_contained": 0.26, "difficulty": 0.12, "coverage": 0.14},
    "language":   {"atomicity": 0.24, "clarity": 0.24, "self_contained": 0.26, "difficulty": 0.16, "coverage": 0.10},
}

BANDS = [
    (85, "deck_ready", "Deck is review-ready. Start the schedule today."),
    (70, "mostly_ready", "Solid deck. Fix the top findings before you start the schedule."),
    (55, "needs_polish", "Usable, but cards will stick. Split multi-fact cards and sharpen vague fronts."),
    (40, "tight_but_usable", "Rough deck. Expect to relearn half of these inside the first two weeks."),
    (0,  "needs_rework", "Not review-ready yet. Go back to the source and re-segment it."),
]


def verdict(score):
    for cutoff, name, _ in BANDS:
        if score >= cutoff:
            return name
    return "needs_rework"


def verdict_blurb(score):
    for cutoff, name, blurb in BANDS:
        if score >= cutoff:
            return blurb
    return BANDS[-1][2]


def analyze(source, intent="retention", max_cards=30, style_mix=None,
             horizon_days=45, daily_new_cap=20, context=""):
    style_mix = style_mix or {"qa": 40, "cloze": 25, "definition": 20, "why": 15}
    source = (source or "").strip()
    ctx = (context or "").lower()

    findings = []
    segments, noise_lines, bullet_lines = segment_source(source)
    acronyms = collect_acronyms(source)

    cards = build_cards(segments, style_mix, max(1, int(max_cards)), acronyms)

    findings.extend(_source_findings(source, segments, noise_lines, bullet_lines, cards, ctx))
    per_card = [_score_card(c, acronyms) for c in cards]
    findings.extend(_card_findings(cards, per_card, acronyms))
    findings.extend(_deck_findings(cards, style_mix, acronyms, ctx))

    axes = _axis_scores(cards, per_card, segments, source, findings)
    weights = WEIGHTS.get(intent, WEIGHTS["retention"])
    raw = sum(axes[a] * w for a, w in weights.items())

    penalty = 0.0
    high = sum(1 for f in findings if f["severity"] == "high")
    med = sum(1 for f in findings if f["severity"] == "medium")
    penalty += high * 3.0 + med * 1.2
    score = int(max(0, min(100, round(raw - penalty))))

    # Hard gates: degenerate input can never look good, however clean the surviving cards are.
    codes = {f["code"] for f in findings}
    if "no_cards_generated" in codes:
        score = min(score, 22)
    elif "source_too_short" in codes:
        score = min(score, 44)
    if cards and len(cards) < 4:
        score = min(score, 52)

    plan = build_schedule(cards, horizon_days, daily_new_cap)

    top = sorted([f for f in findings if f["severity"] == "high"],
                 key=lambda f: f.get("weight", 0), reverse=True)[:3]

    return {
        "score": score,
        "verdict": verdict(score),
        "blurb": verdict_blurb(score),
        "intent": intent,
        "axes": axes,
        "weights": weights,
        "cards": cards,
        "card_scores": per_card,
        "findings": findings,
        "top_fixes": top,
        "plan": plan,
        "stats": {
            "source_chars": len(source),
            "segments": len(segments),
            "cards": len(cards),
            "kinds": dict(Counter(c["kind"] for c in cards)),
            "noise_lines_skipped": noise_lines,
            "bullet_lines": bullet_lines,
            "acronyms": acronyms,
            "context_applied": bool(context.strip()),
        },
        "scaffold": _scaffold(cards, findings),
    }


# ---------------------------------------------------------------- findings

def _source_findings(source, segments, noise, bullets, cards, ctx):
    out = []
    words = len(_words(source))
    if words < 20:
        out.append({"severity": "high", "code": "source_too_short", "evidence": source[:120] or "(empty)",
                    "fix": "Paste at least a few paragraphs or a full set of notes \u2014 20 words gives the generator nothing to atomize.",
                    "axis": "coverage", "weight": 10})
    if len(segments) <= 2 and words > 120:
        out.append({"severity": "medium", "code": "source_unstructured", "evidence": _sample(segments),
                    "fix": "Your source is one wall of prose. Break it into bullets or one-fact-per-line so each line can become one card.",
                    "axis": "atomicity", "weight": 6})
    if noise:
        out.append({"severity": "low", "code": "noise_lines_skipped", "evidence": f"{noise} page-number / URL / header line(s)",
                    "fix": f"Skipped {noise} boilerplate line(s). Fine \u2014 just noise to confirm nothing real was dropped.",
                    "axis": "coverage", "weight": 1})
    if bullets == 0 and words > 150:
        out.append({"severity": "low", "code": "no_bullets",
                    "evidence": f"{words} words of continuous prose",
                    "fix": "No bullet structure detected. Bullets are the cheapest path to atomic cards \u2014 dash-prefix your facts next time.",
                    "axis": "atomicity", "weight": 2})
    if not cards and words >= 20:
        out.append({"severity": "high", "code": "no_cards_generated",
                    "evidence": _sample(segments) or source[:120],
                    "fix": "Nothing parsed into a card. Make each line a self-contained sentence (no 'this' / 'it' referring backwards).",
                    "axis": "coverage", "weight": 12})
    if ctx:
        out.append({"severity": "low", "code": "context_applied", "evidence": ctx[:120],
                    "fix": f"Context '{_clip(ctx, 60)}' adjusted card style weights toward how you'd actually be tested.",
                    "axis": "clarity", "weight": 0})
    return out


def _score_card(card, acronyms):
    front, back, kind = card["front"], card["back"], card["kind"]
    fw, bw = _words(front), _words(back)
    f_content = _content_words(front)
    scores, flags = {}, []

    # atomicity -------------------------------------------------------
    a = 100.0
    if MULTILIST_RE.search(back):
        a -= 22; flags.append("multi_fact_back")
    if len(NUMBER_RE.findall(back)) >= 3:
        a -= 14; flags.append("many_numbers")
    if len(f_content) > 16:
        a -= 12; flags.append("front_overloaded")
    if len(bw) > 55:
        a -= 16; flags.append("back_too_long")
    if kind == "cloze" and back.count("[...]") >= 2:
        a -= 18; flags.append("multi_cloze")
    scores["atomicity"] = _clamp(a)

    # clarity ---------------------------------------------------------
    c = 100.0
    if VAGUE_QUANTIFIER_RE.search(back):
        c -= 14; flags.append("vague_quantifier")
    if WEAK_VERB_RE.search(back):
        c -= 10; flags.append("weak_answer")
    if len(bw) < 4:
        c -= 18; flags.append("thin_answer")
    if len(f_content) == 0:
        c -= 25; flags.append("front_all_stopwords")
    if re.search(r"\betc\.?$", back, re.IGNORECASE):
        c -= 12; flags.append("trailing_etc")
    if back == front or _norm(back) == _norm(front):
        c -= 40; flags.append("echo_card")
    scores["clarity"] = _clamp(c)

    # self_contained --------------------------------------------------
    s = 100.0
    if VAGUE_PRONOUN_START_RE.match(front):
        s -= 22; flags.append("vague_front_pronoun")
    if re.search(r"\b(?:it|they|this|that)\b", front, re.IGNORECASE) and len(f_content) < 8:
        s -= 10; flags.append("dense_reference")
    for ac in acronyms:
        if re.search(r"\b" + re.escape(ac) + r"\b", f"{front} {back}"):
            expanded = re.search(re.escape(ac) + r"\s*\(([^)]{3,60})\)", f"{front} {back}", re.IGNORECASE)
            if not expanded and not _acronym_expanded_elsewhere(f"{front} {back}", ac):
                s -= 12; flags.append("acronym_unexpanded")
            break
    if kind == "cloze" and not re.search(r"\[\.\.\.\]", front):
        s -= 15; flags.append("cloze_without_blank")
    scores["self_contained"] = _clamp(s)

    # difficulty ------------------------------------------------------
    d = 100.0
    if kind == "qa" and len(f_content) <= 3:
        d -= 25; flags.append("trivial_recognition")
    if re.search(r"\b(what|who|when|where|which)\b", front, re.IGNORECASE) and kind == "qa":
        d -= 12; flags.append("recognition_not_recall")
    if len(back) > 240:
        d -= 15; flags.append("answer_requires_reread")
    if kind == "cloze":
        d += 10
    if kind == "why":
        d += 8
    if CONTRAST_RE.search(back) or CONTRAST_RE.search(front):
        d += 6
    scores["difficulty"] = _clamp(d)

    scores["overall"] = int(round(0.22 * scores["atomicity"] + 0.26 * scores["clarity"]
                                  + 0.24 * scores["self_contained"] + 0.28 * scores["difficulty"]))
    return {"id": card["id"], "scores": {k: int(round(v)) for k, v in scores.items() if k != "overall"},
            "overall": scores["overall"], "flags": flags}


def _acronym_expanded_elsewhere(blob, ac):
    return bool(re.search(r"\(" + re.escape(ac) + r"\)", blob)) or         bool(re.search(re.escape(ac) + r"\s+[A-Za-z]+", blob))


def _card_findings(cards, per_card, acronyms):
    out = []
    counts = Counter(_norm(c["front"]) for c in cards)
    for c, pc in zip(cards, per_card):
        f = c["front"]
        if pc["flags"]:
            top = pc["flags"][0]
            severity = "high" if top in ("echo_card", "vague_front_pronoun", "front_all_stopwords",
                                         "no_cards_generated", "acronym_unexpanded") else "medium"
            out.append({
                "severity": severity, "code": f"card_{top}",
                "evidence": f, "card_id": c["id"],
                "fix": _fix_for(top, c),
                "axis": _axis_for(top),
                "weight": 8 if severity == "high" else 4,
            })
        if counts[_norm(f)] > 1:
            out.append({"severity": "medium", "code": "duplicate_front", "evidence": f,
                        "card_id": c["id"], "fix": "Duplicate front detected. Merge these into one card or change the prompt angle.",
                        "axis": "coverage", "weight": 5})
    if cards and len(cards) < 4:
        out.append({"severity": "medium", "code": "deck_tiny", "evidence": f"{len(cards)} card(s)",
                    "fix": "Under 4 cards is not a deck \u2014 it is a bookmark. Feed more source material.",
                    "axis": "coverage", "weight": 6})
    return out


def _fix_for(flag, card):
    return {
        "vague_front_pronoun": "Front starts with 'This/That/It' with no antecedent \u2014 name the concept in the front.",
        "multi_fact_back": "Back bundles 2+ facts. Split into one fact per card so each can be graded separately.",
        "many_numbers": "Three or more numbers in one back. Split by dimension (value, unit, threshold).",
        "front_overloaded": "Front carries too much. Cut to the single cue that triggers recall.",
        "back_too_long": "Back is over ~55 words. Trim to the load-bearing sentence plus one example.",
        "multi_cloze": "More than one [...] in a single cloze. Make one cloze per card.",
        "vague_quantifier": "'roughly / several / a lot of' \u2014 give the actual number or bound.",
        "weak_answer": "Answer is a vague noun phrase. State the fact directly.",
        "thin_answer": "Answer too short to grade yourself against. Add the definition plus one concrete instance.",
        "trailing_etc": "'etc.' hides the tail. List the items or cut the sentence.",
        "echo_card": "Back repeats the front \u2014 this tests nothing. Rewrite so the back supplies new information.",
        "front_all_stopwords": "Front has no content words \u2014 it cannot cue recall.",
        "dense_reference": "Front leans on back-references. Name the entity directly.",
        "acronym_unexpanded": "Acronym never expanded. Spell it out on first use so the card stands alone.",
        "trivial_recognition": "Too easy \u2014 recognition, not recall. Ask for the mechanism or the consequence.",
        "recognition_not_recall": "Surface-level wh-question. Push to 'why' / 'how' / 'what if'.",
        "answer_requires_reread": "Answer is long enough that rereading beats recalling. Compress.",
        "cloze_without_blank": "Cloze card has no [...] \u2014 it is not actually testing anything.",
    }.get(flag, "Review this card for precision.")


def _axis_for(flag):
    return {
        "vague_front_pronoun": "self_contained", "front_all_stopwords": "clarity",
        "echo_card": "difficulty", "multi_fact_back": "atomicity", "many_numbers": "atomicity",
        "front_overloaded": "atomicity", "back_too_long": "atomicity",
        "multi_cloze": "atomicity", "cloze_without_blank": "difficulty",
        "trivial_recognition": "difficulty", "recognition_not_recall": "difficulty",
        "answer_requires_reread": "difficulty", "acronym_unexpanded": "self_contained",
        "dense_reference": "self_contained", "vague_quantifier": "clarity",
        "weak_answer": "clarity", "thin_answer": "clarity", "trailing_etc": "clarity",
    }.get(flag, "clarity")


def _deck_findings(cards, style_mix, acronyms, ctx):
    out = []
    kinds = Counter(c["kind"] for c in cards)
    total = sum(kinds.values()) or 1
    if cards and kinds.get("qa", 0) == total:
        out.append({"severity": "medium", "code": "single_direction_bias", "evidence": "100% question-answer cards",
                    "fix": "All recall, zero recognition. Add cloze + definition cards \u2014 mixed formats survive longer.",
                    "axis": "difficulty", "weight": 6})
    if cards and kinds.get("why", 0) == 0:
        out.append({"severity": "medium", "code": "no_why_card", "evidence": "0 'why does this matter' cards",
                    "fix": "Causal cards are the ones you actually remember. Set why >= 10% of the mix.",
                    "axis": "difficulty", "weight": 5})
    if len(cards) >= 8 and all(len(c["tags"]) <= 1 for c in cards):
        out.append({"severity": "low", "code": "no_tags", "evidence": "no topical tags derived",
                    "fix": "No tags generated. Tags are how you slice a deck for a specific exam sub-topic.",
                    "axis": "coverage", "weight": 3})
    return out


# ---------------------------------------------------------------- axes

def _axis_scores(cards, per_card, segments, source, findings=()):
    if not cards:
        return {"atomicity": 0, "clarity": 0, "self_contained": 0, "difficulty": 0, "coverage": 0}
    codes = {f["code"] for f in findings}
    avg = lambda k: sum(pc["scores"][k] for pc in per_card) / len(per_card)

    atomicity = avg("atomicity")
    clarity = avg("clarity")
    self_contained = avg("self_contained")
    difficulty = avg("difficulty")

    src_words = len(_content_words(source))
    produced = sum(len(_content_words(c["back"])) for c in cards)
    coverage = _clamp(min(100.0, 100.0 * produced / max(1.0, src_words * 0.6)) + 8)
    if len(segments) > len(cards) * 1.6:
        coverage = _clamp(coverage - 12)
    # Coverage gates: too few cards over too much source is a coverage failure, not a rounding error.
    if "source_too_short" in codes:
        coverage = 0
    if len(cards) < 4:
        coverage = _clamp(coverage * 0.3)
    elif len(cards) < 8 and src_words > 120:
        coverage = _clamp(coverage * 0.6)

    return {k: int(round(v)) for k, v in {
        "atomicity": atomicity, "clarity": clarity, "self_contained": self_contained,
        "difficulty": difficulty, "coverage": coverage,
    }.items()}


def _clamp(x):
    return max(0.0, min(100.0, x))


def _sample(items):
    if not items:
        return "(none)"
    return " / ".join(items[:2])[:160]


def _clip(t, n):
    t = re.sub(r"\s+", " ", (t or "").strip())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "\u2026"


# ---------------------------------------------------------------- SM-2

def sm2_intervals(ease=2.5):
    """SM-2 style interval ladder in days for a 5/5/4/3/2/1 grade track."""
    ladder = [1, 6]
    for _ in range(8):
        ladder.append(max(1, round(ladder[-1] * ease)))
    return ladder


def build_schedule(cards, horizon_days=45, daily_new_cap=20):
    """Project a real review load: N new cards introduced on the SM-2 ladder."""
    horizon = max(7, min(365, int(horizon_days or 45)))
    cap = max(1, min(200, int(daily_new_cap or 20)))
    ladder = sm2_intervals()
    today = date.today()

    introduced = {}
    day_load = defaultdict(int)
    new_left = list(cards)
    new_per_day = max(1, cap)

    day_index = 0
    while new_left and day_index < horizon:
        batch = new_left[:new_per_day]
        for c in batch:
            introduced[c["id"]] = day_index
            day_load[day_index] += 1
        new_left = new_left[len(batch):]
        day_index += 1

    # day_index is exactly the number of days the introduction phase ran.
    completion_day = day_index if not new_left else horizon

    # review waves: every introduced card comes back at +1, +6, then *ease
    waves = defaultdict(int)
    for cid, d in introduced.items():
        for off in ladder:
            waves[d + off] += 1
    for d in range(min(horizon, completion_day + 60)):
        day_load[d] += waves.get(d, 0)

    days = []
    peak = 0
    for d in range(min(horizon, max(completion_day + 2, 14))):
        load = day_load.get(d, 0)
        new_cards = sum(1 for v in introduced.values() if v == d)
        reviews = load - new_cards
        peak = max(peak, load)
        days.append({
            "date": (today + timedelta(days=d)).isoformat(),
            "day": d + 1,
            "new": new_cards,
            "reviews": reviews,
            "load": load,
        })

    overload = [d for d in days if d["load"] > cap * 2]
    est_minutes = round(sum(d["load"] for d in days[:14]) * 0.35)

    return {
        "horizon_days": horizon,
        "daily_new_cap": cap,
        "sm2_ladder": ladder,
        "completion_date": (today + timedelta(days=min(horizon - 1, completion_day - 1))).isoformat(),
        "completion_day": completion_day,
        "cards_scheduled": len(introduced),
        "unscheduled": len(new_left),
        "peak_load": peak,
        "overload_days": overload[:6],
        "est_minutes_first_14d": est_minutes,
        "days": days,
    }


def _scaffold(cards, findings):
    kinds = Counter(c["kind"] for c in cards)
    return {
        "split": [f["evidence"] for f in findings if f["code"] in ("card_multi_fact_back", "card_many_numbers", "card_back_too_long")][:6],
        "sharpen": [f["evidence"] for f in findings if f["code"] in ("card_vague_front_pronoun", "card_echo_card", "card_weak_answer", "card_vague_quantifier")][:6],
        "raise_difficulty": [f["evidence"] for f in findings if f["code"] in ("card_trivial_recognition", "card_recognition_not_recall", "single_direction_bias", "no_why_card")][:6],
        "add_tags": [f["evidence"] for f in findings if f["code"] in ("no_tags", "card_acronym_unexpanded")][:6],
        "mix_used": dict(kinds),
    }

