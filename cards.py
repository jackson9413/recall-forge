"""Card generation: parse free-form source notes into atomic, self-contained flashcards."""

import re
import hashlib

STOPWORDS = {
    "the","a","an","and","or","but","if","of","to","in","on","at","for","with","as","by",
    "is","are","was","were","be","been","being","it","its","this","that","these","those",
    "from","we","you","they","he","she","i","our","their","which","who","whom","whose",
    "not","no","so","than","then","there","here","have","has","had","do","does","did",
    "will","would","can","could","should","may","might","must","shall","also","into",
    "about","over","under","more","most","very","just","only","such","any","all","each",
}

# Words that are almost never the thing worth testing on their own.
FILLER_OPENERS = {
    "note","notes","todo","to-do","tip","tips","important","remember","reminder",
    "chapter","section","page","topic","overview","summary","intro","introduction",
    "see","ref","reference","example","e.g.","i.e.","etc","bonus","extra","warning",
    "key point","key idea","key points","idea","concept","definition","def","basically",
}

ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})\b")
DEFINITION_RE = re.compile(
    r"^(?P<term>.{2,60}?)\s*(?:\bis\b|\bare\b|\bmeans\b|\brefers\s+to\b|\bstands\s+for\b|"
    r"\bcan\s+be\s+defined\s+as\b|\bis\s+defined\s+as\b|\bdescribes\b|\bmeans\b)\s+(?P<def>.{6,})$",
    re.IGNORECASE,
)
# A definition/cause subject must be a noun phrase -- these verbs mean it is not one.
TERM_VERB_RE = re.compile(
    r"\b(is|are|was|were|plays?|means?|causes?|uses?|produces?|occurs?|happens?|converts?|"
    r"becomes?|gives?|makes?|takes?|needs?|requires?|shows?|allows?|helps?|creates?|"
    r"moves?|works?|functions?|explain|explains?)\b",
    re.IGNORECASE,
)
# The dash alternative must be whitespace-padded, otherwise "Light-dependent" splits into "Light".
COLON_DEF_RE = re.compile(
    r"^(?P<term>[^:\n]{2,60}?)\s*(?::|\s[\u2014\u2013-]\s)\s*(?P<def>.{6,})$"
)
CAUSAL_RE = re.compile(
    r"^(?P<term>.{4,80}?)\s+(?:because|since|so that|which means|which causes|due to the fact that)\s+(?P<why>.{6,})$",
    re.IGNORECASE,
)
CONTRAST_RE = re.compile(r"\b(unlike|whereas|while|however|in contrast|but not|as opposed to|rather than)\b", re.IGNORECASE)
QUESTION_SPLIT_RE = re.compile(r"^\s*(?:q(?:uestion)?\s*[:.\-]|\?\s*$)", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*(?:[-*\u2022\u2013\u2014]|\d+[.)]|\(?[a-z]\))\s+", re.IGNORECASE)
NOISE_LINE_RE = re.compile(
    r"^\s*(?:page\s*\d+|\d+\s*$|table\s+\d+|figure\s+\d+|chapter\s+\d+\s*$|"
    r"contents?\s*$|table of contents\s*$|copyright.*|all rights reserved.*|https?://\S+\s*$)",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(
    r"(?:\$\s?\d[\d,.]*\s?(?:[kmb])?\b|\b\d[\d,.]*\s?(?:%|percent|x|\u00d7|ms|s|sec|seconds?|minutes?|min|hours?|hr|hrs|"
    r"days?|weeks?|months?|years?|nm|\u00b0c|c|f|kg|lb|lbs|gb|mb|kb|tb|kwh|mwh|miles?|mi|feet|ft|kg|meters?|metres?)\b|\b\d{4}\b)"
)
VAGUE_PRONOUN_START_RE = re.compile(r"^(?:this|that|these|those|it|they|he|she|such)\b", re.IGNORECASE)
VAGUE_QUANTIFIER_RE = re.compile(
    r"\b(a lot of|lots of|several|various|numerous|some|many|most of|pretty much|"
    r"kind of|sort of|roughly|almost always|generally usually|often times)\b",
    re.IGNORECASE,
)
PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+\w+(?:ed|en)\b|\bhas been\b|\bhave been\b",
    re.IGNORECASE,
)
MULTILIST_RE = re.compile(r"(?:;|,\s+(?:and|or)\s+|(?:\s+and\s+){3,})")
WEAK_VERB_RE = re.compile(
    r"\b(is|are|was|were|has|have|had)\s+(?:a|an|the)?\s*"
    r"(thing|concept|process|system|method|idea|principle|type|kind|form)\b",
    re.IGNORECASE,
)


def _norm(text):
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def _words(text):
    return re.findall(r"[A-Za-z0-9'\-]+", text or "")


def _content_words(text):
    return [w for w in _words(text) if w.lower() not in STOPWORDS and len(w) > 2]


def _clip(text, limit):
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0]
    return cut + "\u2026"


def _sentence(text):
    t = re.sub(r"\s+", " ", (text or "").strip().rstrip(".;:,"))
    if not t:
        return t
    return t[0].upper() + t[1:] if t[0].islower() else t


def _clean_term(term):
    """Normalize a definition/cause subject: drop punctuation, quotes, leading articles."""
    t = (term or "").strip().strip("*").strip()
    t = t.strip(" \t-\u2014\u2013:;,.?!")
    t = re.sub(r"\s{2,}", " ", t)
    return t if t and len(t) <= 70 else ""


SUBJECT_TRIM_RE = re.compile(
    r"^(?P<subj>.{3,60}?)\s+(?:is|are|was|were|works?|helps?|matters?|causes?|means?|makes?|"
    r"enables?|allows?|requires?|needs?|provides?|gives?|creates?|drives?|governs?|"
    r"beats?|improves?|affects?|depends?|relies?|plays?|uses?|occurs?|happens?|converts?|"
    r"becomes?|takes?|shows?|supports?|determines?|controls?|describes?|produces?|results?|"
    r"involves?|contains?|includes?|represents?|refers?|appears?|carries?|holds?|leads?|brings?)\b.*$",
    re.IGNORECASE,
)


def _subject_of(term):
    """Trim a trailing verb phrase so 'Spaced repetition works' becomes 'Spaced repetition'."""
    m = SUBJECT_TRIM_RE.match((term or "").strip())
    subj = m.group("subj").strip() if m else (term or "").strip()
    subj = re.sub(r"\s{2,}", " ", subj).strip(" ,;:.")
    return subj if 3 <= len(subj) <= 60 else ""


def _display_term(term):
    """Keep the article but lowercase it: 'What is the Krebs cycle?', not 'What is The Krebs cycle?'."""
    t = (term or "").strip()
    for art in ("The ", "A ", "An "):
        if t.startswith(art):
            return art.lower() + t[len(art):]
    return t


def _is_filler_label(seg):
    n = _norm(seg)
    if not n:
        return True
    return n.split()[0] in FILLER_OPENERS and len(_content_words(seg)) < 4


def _strip_bullet(line):
    return BULLET_RE.sub("", line).strip()


def segment_source(text):
    """Break raw notes into candidate segments. Returns (segments, noise_lines, bullets)."""
    lines = (text or "").splitlines()
    segments, noise, bullets = [], 0, 0
    buffer = []

    def flush():
        if not buffer:
            return
        joined = " ".join(s.strip() for s in buffer if s.strip())
        joined = re.sub(r"\s+", " ", joined).strip()
        if joined:
            if len(joined) > 320:
                # long prose paragraph: split into sentences later
                for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", joined):
                    piece = piece.strip()
                    if len(piece) > 4:
                        segments.append(piece)
            else:
                segments.append(joined)
        buffer.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if NOISE_LINE_RE.match(line):
            noise += 1
            flush()
            continue
        if BULLET_RE.match(line):
            bullets += 1
            flush()
            buffer.append(_strip_bullet(line))
            flush()
            continue
        if line[:1].isspace() and buffer:
            buffer.append(line.strip())
            continue
        flush()
        buffer.append(line.strip())
    flush()

    out = []
    for seg in segments:
        seg = seg.strip().strip("*")
        if len(seg) < 6 or _is_filler_label(seg):
            continue
        # A "Label: definition" line is already atomic -- never split it.
        if COLON_DEF_RE.match(seg):
            out.append(seg)
            continue
        # Prose paragraphs become sentence-level atoms so every fact earns its own card.
        parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", seg) if x.strip()]
        if len(parts) <= 1:
            out.append(seg)
            continue
        kept = [x for x in parts if len(_content_words(x)) >= 3]
        out.extend(kept or [seg])
    return out, noise, bullets


def _pick_focus_term(text):
    words = _words(text)
    cands = [w for w in words if w.lower() not in STOPWORDS and len(w) > 3]
    if not cands:
        return None
    for w in cands:
        if w[:1].isupper() or NUMBER_RE.search(w):
            return w
    return cands[0]


def _is_causal(seg):
    cm = CAUSAL_RE.match(seg or "")
    return bool(cm and _subject_of(_clean_term(cm.group("term"))) and len(_content_words(cm.group("why"))) >= 3)


def _assign_styles(segments, style_mix):
    """Spread styles evenly, then swap why-slots onto causal segments.

    Causal lines ("X because Y") are the highest-value cards in any deck, so a deck that asks for
    why-cards but lands them on a non-causal line wastes the slot.
    """
    styles = _style_sequence(style_mix, len(segments))
    if style_mix.get("why", 0) <= 0:
        return styles
    causal = [i for i, s in enumerate(segments) if _is_causal(s)]
    if not causal:
        return styles
    donors = [i for i in range(len(segments)) if styles[i] == "why" and i not in causal]
    for target, donor in zip(causal, donors):
        styles[target], styles[donor] = styles[donor], styles[target]
    return styles


def _style_sequence(style_mix, n):
    """Deterministic largest-remainder spread of card styles.

    Round-robin on raw weights clusters every style into one contiguous block
    (all 15% "why" cards landing at the very end, and never at all on short decks).
    Scoring each slot against its running target interleaves the styles evenly.
    """
    modes = [k for k in ("qa", "cloze", "definition", "why") if style_mix.get(k, 0) > 0]
    if not modes:
        modes = ["qa", "cloze", "definition", "why"]
    weights = [max(1.0, float(style_mix.get(m, 0))) for m in modes]
    total = sum(weights)
    counts = {m: 0.0 for m in modes}
    seq = []
    for i in range(max(0, n)):
        best, best_gap = modes[0], -1e9
        for m, w in zip(modes, weights):
            gap = ((i + 1) * w / total) - counts[m]
            if gap > best_gap:
                best, best_gap = m, gap
        counts[best] += 1
        seq.append(best)
    return seq


def build_cards(segments, style_mix, max_cards, source_acronyms):
    cards = []
    seen_fronts = set()
    styles = _assign_styles(segments, style_mix)
    for idx, seg in enumerate(segments):
        if len(cards) >= max_cards:
            break
        seg = seg.strip()
        style = styles[idx]

        front = back = None
        kind = style

        # Causal clauses resolve first: "X happens because Y" is a why-card, never a definition.
        if style in ("why", "qa"):
            cm = CAUSAL_RE.match(seg)
            if cm:
                cterm = _subject_of(_clean_term(cm.group("term")))
                why = cm.group("why").strip()
                if cterm and len(_content_words(why)) >= 3:
                    front = f"Why does {cterm} matter?"
                    back = _sentence(why)
                    kind = "why"

        if front is None and style in ("definition", "qa"):
            mm = DEFINITION_RE.match(seg) or COLON_DEF_RE.match(seg)
            if mm:
                term = _clean_term(mm.group("term"))
                defn = mm.group("def").strip()
                if (term and not TERM_VERB_RE.search(term)
                        and len(_content_words(defn)) >= 2):
                    if re.search(r"^(?:what is|define|who is|when is)\b", term, re.IGNORECASE):
                        front = _sentence(term)
                    else:
                        front = f"What is {_display_term(term)}?"
                    back = _sentence(defn)
                    kind = "definition"

        if front is None and style == "cloze":
            term = _pick_focus_term(seg)
            num = NUMBER_RE.search(seg)
            if num:
                front = seg.replace(num.group(0), "[...]")
                back = f"{num.group(0)}  \u2014  {_clip(seg, 160)}"
                kind = "cloze"
            elif term and term.lower() in _norm(seg):
                pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
                front = pattern.sub("[...]", seg, count=1)
                back = _clip(seg, 160)
                kind = "cloze"

        if front is None:
            lines = seg.split("? ")
            if len(lines) == 2 and lines[0].strip().endswith("?"):
                front, back = lines[0].strip(), _sentence(lines[1])
                kind = "qa"
            elif len(_content_words(seg)) >= 4 and _pick_focus_term(seg):
                # No definitional shape to exploit -- blank out the focus term instead of
                # producing a garbled "Explain: <first six content words>" prompt.
                ft = _pick_focus_term(seg)
                pat = re.compile(r"(?<!\w)" + re.escape(ft) + r"(?!\w)", re.IGNORECASE)
                front, back, kind = pat.sub("[...]", seg, count=1), _clip(seg, 160), "cloze"
            else:
                content = _content_words(seg)
                if len(content) < 3:
                    continue
                head = _clip(" ".join(content[:10]), 90)
                front = f"Explain: {head}"
                back = _sentence(seg)
                kind = "qa"

        front = re.sub(r"\s+", " ", front).strip()
        back = re.sub(r"\s+", " ", back).strip()
        if not front or not back:
            continue
        key = _norm(front)
        if key in seen_fronts:
            continue
        seen_fronts.add(key)

        tags = _tags_for(front, back, kind, source_acronyms)
        cid = hashlib.sha1(f"{key}|{_norm(back)}".encode()).hexdigest()[:12]
        cards.append({
            "id": cid,
            "front": _clip(front, 220),
            "back": _clip(back, 420),
            "kind": kind,
            "tags": tags,
            "source": _clip(seg, 200),
        })
    return cards


def _tags_for(front, back, kind, source_acronyms):
    tags = [kind]
    blob = f"{front} {back}"
    for ac in source_acronyms:
        if re.search(r"\b" + re.escape(ac) + r"\b", blob):
            tags.append(ac.lower())
            break
    topical = [w.lower() for w in _content_words(back)[:12]
               if w.lower() not in FILLER_OPENERS and len(w) > 5]
    for t in topical[:2]:
        tags.append(t[:18])
    return sorted(set(tags))


def collect_acronyms(text):
    found = []
    for m in ACRONYM_RE.finditer(text or ""):
        ac = m.group(1)
        if ac in ("I", "A", "OK", "US", "UK"):
            continue
        found.append(ac)
    # count only ones that appear more than once (real acronyms, not sentence starts)
    counts = {}
    for ac in found:
        counts[ac] = counts.get(ac, 0) + 1
    return [ac for ac, n in counts.items() if n >= 2]

