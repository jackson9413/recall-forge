# recall-forge

**Paste notes. Get atomic flashcards, a card-quality scorecard, and an SM-2 review schedule you can actually keep.**

recall-forge is a local-first spaced-repetition flashcard workbench. It turns free-form source
material -- lecture notes, a textbook section, an article, a syllabus topic -- into a deck of
atomic, self-contained flashcards, scores the deck across 5 axes, flags the specific cards that
will fail you, projects a real day-by-day review load on the SM-2 ladder, and exports everything
in a format Anki imports directly.

No API keys. No LLM. No network calls. Your notes never leave your machine.

---

## Why this exists

Most people fail at flashcards for the same boring reason: they make bad cards and then blame
themselves for not doing the reviews.

Bad cards have predictable fingerprints -- a front that starts with "This", a back that bundles
four facts with semicolons, an "Explain:" prompt whose answer is literally the question, cards
so easy you recognize them instead of recalling them. Those are mechanical problems, and
mechanical problems can be detected without any AI.

recall-forge checks for all of them, per card, and tells you which line of your source produced
the bad one.

## What it does

**Card generation** -- parses bullets, paragraphs, `Label: definition` lines, causal
("X happens because Y"), and contrast ("unlike a cell wall...") structures. Prose paragraphs are
split to sentence-level atoms so every fact earns its own card. Four card styles blended by
weighted mix: Q&A, cloze deletion, definition, and why-it-matters.

**5-axis card-quality score (0-100)** -- weights rebalance per deck intent:

| Axis | What it measures |
| --- | --- |
| `atomicity` | one fact per card, no semicolon bundles, no multi-number backs |
| `clarity` | no vague quantifiers ("roughly", "several"), no thin or echoing answers |
| `self_contained` | no dangling pronouns, no unexpanded acronyms, cloze actually has a blank |
| `difficulty` | tests recall not recognition; rewards why/contrast cards |
| `coverage` | how much of the source actually became cards |

Intent presets reweight the axes: `retention` leans hard on difficulty, `exam` on atomicity,
`professional` on clarity plus self-containedness, `language` on self-containedness.

**Per-card findings** -- 20+ card-level flags with evidence and a concrete fix, e.g.
`card_vague_front_pronoun`, `card_echo_card`, `card_multi_fact_back`, `card_acronym_unexpanded`,
`card_trivial_recognition`, `card_vague_quantifier`, plus deck-level and source-level findings.

**SM-2 review projection** -- a real interval ladder (1 / 6 / x ease), new-card introductions
capped per day, review waves projected forward, peak day load, completion date, first-14-day
minute estimate, and overload days where load exceeds 2x your daily cap.

**Hard gates** -- a 4-word source can never score well, no matter how clean the surviving card is.
Degenerate input is capped structurally rather than nudged down by penalties.

## Install & run

```bash
cd recall-forge
python3 -m pip install -r requirements.txt   # just Flask
python3 app.py
```

Then open <http://127.0.0.1:8788>.

Set `PORT` to change the port, `RECALL_FORGE_DB` to move the SQLite file.

## Anki import

Build a deck, hit **Download Anki TSV**, then in Anki: *File -> Import* and select the file.

The export is 3 tab-separated columns:

```
Front<TAB>Back<TAB>Tag1 ::nested Tag2
```

Nested tags use Anki's `::` syntax. CSV and Markdown exports are also available.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | liveness |
| GET | `/api/sample` | sample source text |
| POST | `/api/analyze` | full analysis (scores, cards, findings, schedule); saves a session |
| POST | `/api/cards` | cards only, no scoring |
| GET | `/api/sessions` | recent session log |
| GET | `/api/sessions/<id>` | reopen a saved session |
| PATCH | `/api/sessions/<id>` | set `self_rating` (1-5) and `notes` |
| GET | `/api/export/<id>?fmt=tsv\|csv\|md\|json` | export a session |

```bash
curl -s localhost:8788/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"source":"Mitochondria are the organelles responsible for cellular respiration.","intent":"exam","max_cards":12}' \
  | python3 -m json.tool | head -40
```

## Tests

```bash
python3 tests/test_smoke.py
```

## Stack

Python 3 - Flask 3 - SQLite - vanilla JS. No build step, no frontend framework, no
dependencies beyond Flask.

## License

MIT
