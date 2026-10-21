const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const n = document.createElement(t); if (c) n.className = c; if (h !== undefined) n.innerHTML = h; return n; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const VERDICT_COLOR = {
  deck_ready: "var(--green)", mostly_ready: "var(--blue)", needs_polish: "var(--yellow)",
  tight_but_usable: "var(--orange)", needs_rework: "var(--red)"
};
const AXIS_LABEL = { atomicity: "Atomicity", clarity: "Clarity", self_contained: "Self-contained", difficulty: "Difficulty", coverage: "Coverage" };

let FILTER = "all";
let RATING = null;
let CURRENT = null;

const num = (id) => parseInt($(id).value, 10) || 0;

function payload() {
  return {
    source: $("#source").value,
    intent: $("#intent").value,
    max_cards: num("#maxCards"),
    horizon_days: num("#horizon"),
    daily_new_cap: num("#cap"),
    context: $("#ctxFilter").value,
    style_mix: { qa: num("#mQa"), cloze: num("#mCloze"), definition: num("#mDef"), why: num("#mWhy") }
  };
}

async function build() {
  const btn = $("#build");
  btn.disabled = true; btn.textContent = "Building…";
  try {
    const res = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload())
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    CURRENT = data; RATING = null;
    $("#notes").value = "";
    render(data);
    loadHistory();
  } catch (e) {
    alert("Request failed: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Build deck";
  }
}

function axisColor(v) { return v >= 85 ? "var(--green)" : v >= 70 ? "var(--blue)" : v >= 55 ? "var(--yellow)" : v >= 40 ? "var(--orange)" : "var(--red)"; }

function render(d) {
  $("#empty").classList.add("hidden");
  $("#result").classList.remove("hidden");

  const sn = $("#scoreNum");
  sn.textContent = d.score;
  sn.style.color = VERDICT_COLOR[d.verdict] || "var(--text)";
  const vb = $("#verdict");
  vb.textContent = d.verdict.replace(/_/g, " ");
  vb.style.background = VERDICT_COLOR[d.verdict];
  vb.style.color = "#04121f";
  $("#blurb").textContent = d.blurb;

  const s = d.stats, p = d.plan;
  $("#statChips").innerHTML = [
    ["cards", s.cards], ["segments", s.segments], ["words", s.source_chars],
    ["acronyms", s.acronyms.length], ["noise skipped", s.noise_lines_skipped]
  ].map(([k, v]) => `<span class="chip"><b>${v}</b> ${k}</span>`).join("")
    + Object.entries(s.kinds).map(([k, v]) => `<span class="chip"><b>${v}</b> ${k}</span>`).join("");

  $("#axes").innerHTML = Object.keys(AXIS_LABEL).map((k) => `
    <div class="axis">
      <div class="n" style="color:${axisColor(d.axes[k])}">${d.axes[k]}</div>
      <div class="l">${AXIS_LABEL[k]}</div>
      <div class="bar"><i style="width:${d.axes[k]}%;background:${axisColor(d.axes[k])}"></i></div>
    </div>`).join("");

  $("#ladder").textContent = p.sm2_ladder.join(" / ") + " d";
  $("#completion").textContent = `day ${p.completion_day} (${p.completion_date})`;
  $("#peak").textContent = `${p.peak_load} cards`;
  $("#mins").textContent = p.est_minutes_first_14d;

  const sid = d.session_id;
  $("#expTsv").href = `/api/export/${sid}?fmt=tsv`;
  $("#expCsv").href = `/api/export/${sid}?fmt=csv`;
  $("#expMd").href = `/api/export/${sid}?fmt=md`;
  $("#expJson").href = `/api/export/${sid}?fmt=json`;

  renderSchedule(p);
  renderStars();
  renderCards(d);
  renderFindings(d);
  renderScaffold(d);

  const tf = $("#topFixes");
  tf.innerHTML = d.top_fixes.length
    ? d.top_fixes.map(f => `<li><b>${esc(f.code)}</b> &mdash; ${esc(f.fix)}<br><span style="color:var(--muted)">“${esc(f.evidence)}”</span></li>`).join("")
    : "<li>Nothing blocking. Start the schedule.</li>";
}

function renderSchedule(p) {
  const days = p.days.slice(0, 21);
  const max = Math.max(1, ...days.map(x => x.load));
  $("#schedule").innerHTML = days.map(x => `
    <div class="sday" title="Day ${x.day} · ${x.date} · ${x.new} new · ${x.reviews} reviews">
      <div class="snum">${x.day}</div>
      <div class="sbar ${x.load > p.daily_new_cap * 2 ? "over" : ""}" style="height:${Math.round((x.load / max) * 82)}px"></div>
    </div>`).join("");
}

function renderStars() {
  $("#stars").innerHTML = [1, 2, 3, 4, 5]
    .map(i => `<button data-r="${i}" class="${RATING === i ? "on" : ""}">★</button>`).join("");
  document.querySelectorAll("#stars button").forEach(b => b.onclick = () => {
    RATING = parseInt(b.dataset.r, 10); renderStars();
  });
}

function clozeHtml(s) { return esc(s).replace(/\[\.\.\.\]/g, '<span class="cloze">[...]</span>'); }

function renderCards(d) {
  const scores = Object.fromEntries(d.card_scores.map(x => [x.id, x]));
  $("#cardCount").textContent = `(${d.cards.length})`;
  $("#cards").innerHTML = d.cards.map((c, i) => {
    const sc = scores[c.id];
    return `<div class="card">
      <div class="top">
        <span class="kind">${esc(c.kind)}</span>
        <span class="cardscore">card ${sc ? sc.overall : "-"}/100${sc && sc.flags.length ? " · " + sc.flags.length + " flag(s)" : ""}</span>
      </div>
      <div class="front">${clozeHtml(c.front)}</div>
      <div class="reveal">
        <button class="linkbtn" data-rev="${i}">reveal answer</button>
        <div class="b hidden" data-back="${i}"><b>Answer:</b> ${clozeHtml(c.back)}</div>
      </div>
      <div class="src">tags: ${esc(c.tags.join(" · "))} &nbsp;|&nbsp; from: “${esc(c.source)}”</div>
    </div>`;
  }).join("");

  document.querySelectorAll("[data-rev]").forEach(b => b.onclick = () => {
    const back = document.querySelector(`[data-back="${b.dataset.rev}"]`);
    const show = back.classList.contains("hidden");
    back.classList.toggle("hidden", !show);
    b.textContent = show ? "hide answer" : "reveal answer";
  });
}

function renderFindings(d) {
  $("#findings").innerHTML = d.findings.filter(f => {
    if (FILTER === "all") return true;
    if (FILTER === "source" || FILTER === "card") return f.code.startsWith(FILTER);
    return f.severity === FILTER;
  }).map(f => `
    <div class="finding ${f.severity}">
      <span class="code">${esc(f.code)}</span><span class="sev">${f.severity}</span>
      <div class="ev">“${esc(f.evidence)}”</div>
      <div class="fx">→ ${esc(f.fix)}</div>
    </div>`).join("") || '<div style="color:var(--muted);font-size:13px">No findings in this filter.</div>';
}

function renderScaffold(d) {
  const sc = d.scaffold;
  const block = (title, items) => `<div style="margin-bottom:12px">
    <div style="color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">${title}</div>
    ${items.length ? `<ul style="padding-left:18px;margin:0">${items.map(i => `<li><code>${esc(i)}</code></li>`).join("")}</ul>`
      : '<div style="color:var(--muted);font-size:12.5px">Nothing flagged here.</div>'}
  </div>`;
  $("#scaffold").innerHTML =
    block("Split these (multi-fact cards)", sc.split) +
    block("Sharpen these (vague or echoing fronts)", sc.sharpen) +
    block("Raise difficulty on these", sc.raise_difficulty) +
    `<div style="color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em">Style mix actually used</div>
     <div>${Object.entries(sc.mix_used).map(([k, v]) => `<span class="chip"><b>${v}</b> ${k}</span>`).join(" ")}</div>`;
}

async function loadHistory() {
  try {
    const r = await fetch("/api/sessions");
    const { sessions } = await r.json();
    $("#history").innerHTML = sessions.length ? `<table>
      <tr><th>date</th><th>intent</th><th>score</th><th>verdict</th><th>you</th><th></th></tr>
      ${sessions.map(s => `<tr>
        <td>${esc((s.created_at || "").slice(0, 16).replace("T", " "))}</td>
        <td>${esc(s.intent)}</td>
        <td style="color:${VERDICT_COLOR[s.verdict] || "var(--text)"};font-weight:700">${s.score}</td>
        <td>${esc((s.verdict || "").replace(/_/g, " "))}</td>
        <td>${s.self_rating ? "★".repeat(s.self_rating) : "–"}</td>
        <td><button class="linkbtn" data-open="${esc(s.id)}">reopen</button></td>
      </tr>`).join("")}
    </table>` : '<div style="color:var(--muted);font-size:13px">No sessions yet.</div>';

    document.querySelectorAll("[data-open]").forEach(b => b.onclick = async () => {
      const r2 = await fetch("/api/sessions/" + b.dataset.open);
      const d = await r2.json();
      if (d.error) return;
      CURRENT = d; RATING = d.self_rating || null;
      render(d);
      $("#notes").value = d.notes || "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  } catch (e) {
    $("#history").innerHTML = '<div style="color:var(--muted);font-size:13px">history unavailable</div>';
  }
}

async function saveRating() {
  if (!CURRENT || !CURRENT.session_id) return;
  await fetch("/api/sessions/" + CURRENT.session_id, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ self_rating: RATING, notes: $("#notes").value })
  });
  const b = $("#saveRating");
  b.textContent = "saved";
  setTimeout(() => (b.textContent = "save"), 1400);
  loadHistory();
}

$("#build").onclick = build;
$("#saveRating").onclick = saveRating;
$("#loadSample").onclick = async () => {
  const r = await fetch("/api/sample");
  $("#source").value = (await r.json()).source;
  build();
};
document.querySelectorAll(".fchip").forEach(c => c.onclick = () => {
  document.querySelectorAll(".fchip").forEach(x => x.classList.remove("on"));
  c.classList.add("on"); FILTER = c.dataset.f;
  if (CURRENT) renderFindings(CURRENT);
});
$("#ctxFilter").addEventListener("keydown", e => { if (e.key === "Enter") build(); });

loadHistory();

