/* ============================================================================
   SIA web demo — single-app renderer of demo_data.json.

   Scope (BUILT): one fetch of demo_data.json -> one `state` -> one render(state).
     - the 7-screen CSS-class stepper + stage dots + manual nav (keyboard /
       presenter-clicker driven; no tour, no auto-advance — the presenter drives
       everything by hand)
     - screen 4: 3 panes — ECharts curve, the custom "What changed" pane, and the
       ECharts taxonomy bar — all driven by ONE scrubber.
     - the "What changed" pane LEADS WITH THE KEY CHANGE, then the topic list:
         lead      = the single most prominent change for this gen — a "Key change
                     this generation" block: bucket badge (BUCKET_META) + the
                     summary (larger) + its AUTHORITATIVE code (change.code) as a
                     focused mini-diff, green-pulsed at the self-repair generation.
                     Selection (deterministic): a self-repair change (bucket
                     retry/robustness and/or gen == self_repair_gen) if present,
                     else the change whose bucket == taxonomy.primary_bucket, else
                     the first change.
         topics    = the full list of expandable change cards from
                     taxonomy.changes[] (the key change is also kept here; the list
                     header "All changes this generation" makes the relation clear)
         optional  = a collapsible "Show full diff" (diff2html lives only here)
       gen 1 / diff_from_prev:null / empty changes render an intentional
       "first generation — nothing to compare yet" state (no key-change block),
       never broken UI.
     - screens 0-3/5/6 content + honesty card + model-choice hover + five UI
       states
     - the SIA-loop SVG explainer: hand-laid SVG + GSAP 4-beat cycle + a sparkline
       of the real accuracy series
     - the GSAP synchronized "wow" master-timeline on screen 4: one timeline per
       scrub transition fuses curve + key-change block + taxonomy; at the
       self-repair generation the key-change block's code rows green-pulse while
       the curve marker pops and the taxonomy self-repair segment is emphasized,
       all in lockstep. Keyed off headline.self_repair_gen + diff_highlights[] — never a
       hardcoded gen index. prefers-reduced-motion collapses it to the static
       final frame.

   Pure renderer of demo_data.json. Zero-build, CDN-only, static.
   ========================================================================== */

(function () {
  "use strict";

  const SCREEN_COUNT = 9;
  const SELF_REPAIR_HIGHLIGHT_CLASS = "self-repair";
  // R2 bucket the self-repair change lands in — the taxonomy segment the wow
  // emphasizes. Matches taxonomy.counts keys emitted by the R2 classifier.
  const SELF_REPAIR_BUCKET = "retry/robustness";
  // green-pulse / segment-emphasis dwell (ms) at the self-repair gen
  const WOW_PULSE_MS = 900;
  const RESULT_SCREEN = 6;
  const LIVE_SCREEN = 4;
  const HOOK_SCREEN = 0;
  const LOOP_SCREEN = 2;
  const CLOSE_LOOP_SCREEN = 7; // Ongoing-TODO screen 1: the re-routed-wire diagram
  const LAST_SCREEN = 8; // Ongoing-TODO screen 2 — the true end of the deck

  // B3 — SIA-loop explainer timing
  const LOOP_BEAT_MS = 1500; // a room can follow one beat per 1.5s on a projector

  // Human-readable screen names for the chrome position label ("3 / 9 · …").
  const SCREEN_NAMES = [
    "Hook",
    "The problem",
    "The SIA loop",
    "Use case",
    "Live improvement",
    "The Research lens",
    "Result",
    "Closing the loop",
    "What would change",
  ];

  const reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Solarized Light chart palette (mirrors the CSS :root tokens — ECharts can't
  // read CSS custom properties, so the hues are duplicated here as the single
  // JS-side source of truth for canvas-rendered charts).
  const COLORS = {
    text: "#586e75", // base01 — axis labels / legend
    muted: "#5c6f73", // darkened base1 (P1) — axis lines
    grid: "#d6cfb8", // split/grid lines
    accent: "#268bd2", // blue — accuracy line
    success: "#859900", // green — current-gen marker / self-repair fill
    successOutline: "#5f6f00", // darker green outline so the marker clears 3:1 (P2/E)
    // taxonomy stacked-bar segments: high-contrast hues stack-adjacent (P4) so
    // no two confusable luminances are neighbours (blue/orange, green/violet
    // alternate; never green-next-to-cyan/yellow).
    taxonomy: ["#268bd2", "#cb4b16", "#859900", "#6c71c4", "#b58900", "#2aa198"],
    seam: "#fdf6e3", // base3 — segment separator seam (P4)
  };
  // --- ONE source of truth for bucket label + color -------------------------
  // Every screen-4 view that names or colors an R2 bucket reads THIS map: the
  // per-gen taxonomy bars (renderTaxonomy) AND the "What changed" cards
  // (renderChangeCards). A bucket therefore shows the SAME label text and the
  // SAME hue in both places. Colors reuse COLORS.taxonomy (the taxonomy hue
  // palette) so the bars are visually unchanged; here each bucket key is pinned
  // to its hue + a concise human-readable label. Reward-hack = amber.
  const BUCKET_META = {
    "parser-hardening": { label: "Parser hardening", color: COLORS.taxonomy[0] }, // blue
    "retry/robustness": { label: "Retry / robustness", color: COLORS.taxonomy[1] }, // orange
    validation: { label: "Validation", color: COLORS.taxonomy[2] }, // green
    "new-tool": { label: "New tool", color: COLORS.taxonomy[3] }, // violet
    "prompt-restructure": { label: "Prompt restructure", color: COLORS.taxonomy[5] }, // cyan
    "schema-injection": { label: "Schema injection", color: COLORS.taxonomy[2] }, // green (domain)
    "task-specific-hack": { label: "Reward-hack", color: COLORS.taxonomy[4] }, // amber
  };

  /** Concise label for a bucket (single source of truth, used by both views). */
  function bucketLabel(bucket) {
    return (BUCKET_META[bucket] && BUCKET_META[bucket].label) || bucket || "change";
  }

  /** Hue for a bucket (single source of truth, used by both views). */
  function bucketColor(bucket) {
    return (BUCKET_META[bucket] && BUCKET_META[bucket].color) || COLORS.muted;
  }

  // self-repair green-pulse endpoints (RGBA so GSAP can tween backgroundColor).
  // The settle value MUST equal the key-change block's resting self-repair tint
  // so the pulse lands seamlessly on the static highlight.
  const PULSE_GREEN_BRIGHT = "rgba(133, 153, 0, 0.62)";
  const PULSE_GREEN_REST = "rgba(133, 153, 0, 0.30)";

  // R2 family copy used by the screen-5 reward-hack note.
  const NO_HACK_NOTE =
    "No reward-hacking changes were flagged in this run — the agent hardened its harness and got better at the task, not at gaming the grader.";

  // --- single source of truth ------------------------------------------------
  const state = {
    screen: HOOK_SCREEN, // open on the title/hook screen
    gen: 1,
  };

  let data = null; // demo_data.json once loaded
  let curveChart = null; // ECharts instance
  let taxonomyChart = null; // ECharts instance
  let diffUI = null; // Diff2HtmlUI instance (full-diff toggle only)
  let fullDiffDrawnGen = null; // gen the full diff was last rendered for (lazy)
  let taxonomyBuckets = []; // stable bucket order across generations

  // B3 explainer timers
  let loopBeatTimer = null;
  let loopBeat = 0;

  // --- DOM handles -----------------------------------------------------------
  const el = {
    dots: document.getElementById("stage-dots"),
    screenLabel: document.getElementById("screen-label"),
    title: document.getElementById("chrome-title"),
    screens: Array.from(document.querySelectorAll(".screen")),
    ticks: document.getElementById("gen-ticks"),
    caption: document.getElementById("caption"),
    // taxonomy pane — per-gen title + "how it improved" sub-line
    taxonomyTitle: document.getElementById("taxonomy-title"),
    taxonomySub: document.getElementById("taxonomy-sub"),
    // experiment selector (small vs big model)
    expNemotron: document.getElementById("exp-nemotron"),
    expGemma: document.getElementById("exp-gemma"),
    // "What changed" pane (custom render)
    changeCards: document.getElementById("change-cards"),
    changesHead: document.getElementById("changes-head"),
    // key-change block (leads the pane; the wow's pulse target)
    keychange: document.getElementById("keychange"),
    keychangeBadge: document.getElementById("keychange-badge"),
    keychangeSummary: document.getElementById("keychange-summary"),
    keychangeCode: document.getElementById("keychange-code"),
    fullDiff: document.getElementById("fulldiff"),
    paneDiff: document.getElementById("pane-diff"),
    // overlays / banners (five UI states)
    overlayLoading: document.getElementById("overlay-loading"),
    overlayError: document.getElementById("overlay-error"),
    errorText: document.getElementById("error-text"),
    overlayEmpty: document.getElementById("overlay-empty"),
    partialBanner: document.getElementById("partial-banner"),
    // nav
    btnPrev: document.getElementById("btn-prev"),
    btnNext: document.getElementById("btn-next"),
    btnStart: document.getElementById("btn-start"),
    btnImprove: document.getElementById("btn-improve"),
    // screen 0 — hook
    hookSpark: document.getElementById("hook-spark"),
    hookStat: document.getElementById("hook-stat"),
    hookWatch: document.getElementById("hook-watch"),
    // screen 2 — SIA-loop explainer
    loopGen: document.getElementById("loop-gen"),
    loopBeatText: document.getElementById("loop-beat"),
    loopSpark: document.getElementById("loop-spark"),
    nodeMeta: document.getElementById("node-meta"),
    nodeTarget: document.getElementById("node-target"),
    nodeFeedback: document.getElementById("node-feedback"),
    nodeVerifier: document.getElementById("node-verifier"),
    feedbackArrow: document.getElementById("loop-feedback-arrow"),
    // screen 7 — closing-the-loop (the new-wire draw-in)
    clNewwire: document.getElementById("cl-newwire"),
    clNewwireHead: document.getElementById("cl-newwire-head"),
    clR2: document.getElementById("cl-r2"),
    // screen 3 — worked example
    exampleBox: document.getElementById("example-query"),
    exampleSql: document.getElementById("example-sql"),
    exampleVerdict: document.getElementById("example-verdict"),
    // screen 5 — R2 lens
    lensSePct: document.getElementById("lens-se-pct"),
    lensDomainPct: document.getElementById("lens-domain-pct"),
    lensHackCount: document.getElementById("lens-hack-count"),
    lensHackNote: document.getElementById("lens-hack-note"),
    // screen 6 — result + honesty
    resultNums: document.getElementById("result-nums"),
    resultRestart: document.getElementById("result-restart"),
    resultExplore: document.getElementById("result-explore"),
  };

  // ===========================================================================
  // Data helpers
  // ===========================================================================

  /** Generation object at the current scrubber index (1-based gen number). */
  function genAt(genNumber) {
    if (!data || !Array.isArray(data.generations)) return null;
    return data.generations.find((g) => g.gen === genNumber) || null;
  }

  function generationNumbers() {
    if (!data || !Array.isArray(data.generations)) return [];
    return data.generations.map((g) => g.gen);
  }

  function isSelfRepairGen(genNumber) {
    return !!(data && data.headline && data.headline.self_repair_gen === genNumber);
  }

  /** Union of all bucket keys across generations, in first-seen order. */
  function collectTaxonomyBuckets() {
    const seen = [];
    const known = new Set();
    for (const g of data.generations || []) {
      const counts = (g.taxonomy && g.taxonomy.counts) || {};
      for (const bucket of Object.keys(counts)) {
        if (!known.has(bucket)) {
          known.add(bucket);
          seen.push(bucket);
        }
      }
    }
    return seen;
  }

  /** Per-gen accuracy_percent series; entries are number or null (ungraded). */
  function accuracySeries() {
    return (data.generations || []).map((g) =>
      g.metrics && typeof g.metrics.accuracy_percent === "number"
        ? g.metrics.accuracy_percent
        : null
    );
  }

  /** First graded accuracy_percent in gen order, or null if none. */
  function firstGradedAccuracy() {
    for (const v of accuracySeries()) {
      if (typeof v === "number") return v;
    }
    return null;
  }

  /** Last graded accuracy_percent in gen order, or null if none. */
  function lastGradedAccuracy() {
    const s = accuracySeries();
    for (let i = s.length - 1; i >= 0; i--) {
      if (typeof s[i] === "number") return s[i];
    }
    return null;
  }

  // ===========================================================================
  // Pane renderers — each reads the contract for the current gen and re-renders.
  // ECharts setOption auto-morphs old->new data (free transition). No GSAP here.
  // ===========================================================================

  /** Accuracy curve over all gens, with a marker at the current gen. */
  function renderCurve(genNumber) {
    if (!curveChart) return;
    const gens = generationNumbers();
    // accuracy_percent can be null (e.g. gen 1 with no graded results) -> null
    // leaves a gap in the line rather than plotting a misleading zero.
    const series = data.generations.map((g) =>
      g.metrics && typeof g.metrics.accuracy_percent === "number"
        ? g.metrics.accuracy_percent
        : null
    );
    const current = genAt(genNumber);
    const markerY =
      current && current.metrics && typeof current.metrics.accuracy_percent === "number"
        ? current.metrics.accuracy_percent
        : null;

    curveChart.setOption({
      grid: { left: 52, right: 24, top: 28, bottom: 36 },
      xAxis: {
        type: "category",
        data: gens.map((n) => "gen " + n),
        axisLine: { lineStyle: { color: COLORS.muted } },
        axisLabel: { color: COLORS.text, fontSize: 14 },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        name: "accuracy %",
        nameTextStyle: { color: COLORS.text, fontSize: 14 },
        axisLabel: { formatter: "{value}%", color: COLORS.text, fontSize: 14 },
        splitLine: { lineStyle: { color: COLORS.grid } },
      },
      tooltip: { trigger: "axis", valueFormatter: (v) => (v == null ? "n/a" : v.toFixed(1) + "%") },
      series: [
        {
          type: "line",
          data: series,
          smooth: true,
          connectNulls: false,
          symbolSize: 8,
          lineStyle: { color: COLORS.accent, width: 3 },
          itemStyle: { color: COLORS.accent },
          // moving marker at the current gen — green fill with a darker outline
          // so the non-text green clears the 3:1 floor on the warm bg (P2/E).
          markPoint:
            markerY == null
              ? undefined
              : {
                  symbol: "circle",
                  symbolSize: 18,
                  data: [{ coord: ["gen " + genNumber, markerY], name: "current" }],
                  itemStyle: {
                    color: COLORS.success,
                    borderColor: COLORS.successOutline,
                    borderWidth: 1,
                  },
                  label: { show: false },
                },
        },
      ],
    });
  }

  // ===========================================================================
  // "What changed" — the redesigned centerpiece. Key-change-first (the single
  // most prominent change, spotlighted with its authoritative code), then the
  // full topic list of change cards, then a curious-optional full diff toggle.
  // ===========================================================================

  /** Render the whole "What changed" pane for a generation. */
  function renderWhatChanged(genNumber) {
    const g = genAt(genNumber);
    renderKeyChange(g, genNumber);
    renderChangeCards(g, genNumber);
    prepareFullDiff(g, genNumber);
  }

  /**
   * Pick the single most prominent change for a generation, deterministically:
   *   1. a self-repair change — bucket retry/robustness, and/or (when this gen IS
   *      the self_repair_gen) any retry/robustness change — preferred so the wow's
   *      pulse target is the change it celebrates;
   *   2. else the change whose bucket == taxonomy.primary_bucket;
   *   3. else the first change.
   * Returns the change object, or null when there are no changes (gen 1 / empty).
   */
  function selectKeyChange(g, genNumber) {
    const changes = (g && g.taxonomy && Array.isArray(g.taxonomy.changes) && g.taxonomy.changes) || [];
    if (changes.length === 0) return null;

    const selfRepairGen = isSelfRepairGen(genNumber);
    const selfRepair = changes.find(
      (c) => c.bucket === SELF_REPAIR_BUCKET || (selfRepairGen && c.bucket === SELF_REPAIR_BUCKET)
    );
    if (selfRepair) return selfRepair;

    const primary = g.taxonomy && g.taxonomy.primary_bucket;
    if (primary) {
      const byPrimary = changes.find((c) => c.bucket === primary);
      if (byPrimary) return byPrimary;
    }
    return changes[0];
  }

  /**
   * Lead block: the single most prominent change, spotlighted with its bucket
   * badge (BUCKET_META) + the summary (larger) + its authoritative code rendered
   * as a focused mini-diff. At the self-repair generation the code rows carry the
   * .self-repair class so the wow can green-pulse them. Hidden on gen 1 / empty.
   */
  function renderKeyChange(g, genNumber) {
    const change = selectKeyChange(g, genNumber);
    if (!change) {
      el.keychange.hidden = true;
      el.keychangeCode.innerHTML = "";
      return;
    }
    el.keychange.hidden = false;

    const color = bucketColor(change.bucket);
    el.keychangeBadge.textContent = bucketLabel(change.bucket);
    el.keychangeBadge.style.color = color;
    el.keychangeBadge.style.borderColor = color;
    el.keychangeSummary.textContent = change.summary || "";

    // Mark the code rows .self-repair at the self-repair gen so the wow pulses
    // THIS block's rows (replacing the old receipt's pulse target).
    const markSelfRepair = isSelfRepairGen(genNumber);
    el.keychange.style.borderLeftColor = color;
    el.keychangeCode.innerHTML = keyChangeCodeHtml(change, markSelfRepair);
  }

  /**
   * Render the key change's authoritative code (change.code) as a focused
   * mini-diff. When markSelfRepair is true, the added rows carry the .self-repair
   * class — the wow's pulse target. Falls back to the per-card heuristic mini-diff
   * (offline-flattened data with no `code`) so the block is never empty.
   */
  function keyChangeCodeHtml(change, markSelfRepair) {
    const rows = authoritativeCodeRows(change);
    if (!rows) return cardMiniDiff(change, genAt(state.gen));
    return rows
      .map((r) => {
        const sr = markSelfRepair && r.kind === "add" ? " " + SELF_REPAIR_HIGHLIGHT_CLASS : "";
        return (
          `<span class="rcode rcode--${r.kind}${sr}">` +
          `<span class="rcode__sign">${r.kind === "add" ? "+" : r.kind === "del" ? "−" : " "}</span>` +
          `<span class="rcode__text">${escapeHtml(r.text)}</span></span>`
        );
      })
      .join("");
  }

  /**
   * Primary view: plain-language change cards from taxonomy.changes[]. Each card
   * = the summary sentence + a bucket badge whose label and color come from the
   * single BUCKET_META map — so a card matches its taxonomy bar exactly. gen 1 /
   * empty changes render an intentional first-generation state (not broken UI).
   *
   * Each card is an accordion: clicking it (or Enter/Space) reveals, directly
   * below, a focused mini-diff of the literal code lines that change refers to —
   * matched heuristically from diff_from_prev (see cardMiniDiff). Opening one
   * card collapses the others so only one reveal shows at a time.
   */
  function renderChangeCards(g, genNumber) {
    const changes = (g && g.taxonomy && Array.isArray(g.taxonomy.changes) && g.taxonomy.changes) || [];

    if (changes.length === 0) {
      // First generation (or any gen with nothing to compare): honest, framed.
      if (el.changesHead) el.changesHead.hidden = true;
      const first = !g || !g.diff_from_prev;
      el.changeCards.innerHTML =
        '<li class="changes__empty">' +
        (first
          ? "<strong>First generation.</strong> This is the agent’s starting harness — there’s no previous generation to compare it against yet. Scrub right to watch it rewrite itself."
          : "No classified changes for this generation.") +
        "</li>";
      return;
    }

    // The key change is spotlighted above; the list shows every change so the
    // pane stays scannable — the header makes that relationship explicit.
    if (el.changesHead) el.changesHead.hidden = false;

    el.changeCards.innerHTML = changes
      .map((c, i) => {
        // Label + color from the single BUCKET_META map (same as the taxonomy bar).
        const label = bucketLabel(c.bucket);
        const color = bucketColor(c.bucket);
        return (
          `<li class="change" data-card="${i}" style="border-left-color:${color}">` +
          `<button class="change__row" type="button" aria-expanded="false" aria-controls="change-reveal-${i}">` +
          `<span class="change__badge" style="color:${color};border-color:${color}">${escapeHtml(label)}</span>` +
          `<span class="change__text">${escapeHtml(c.summary || "")}</span>` +
          `<span class="change__chevron" aria-hidden="true">&#9656;</span>` +
          "</button>" +
          `<div class="change__reveal" id="change-reveal-${i}" hidden></div>` +
          "</li>"
        );
      })
      .join("");

    // Wire each card's expand toggle. The mini-diff is built lazily on first
    // open and cached in the reveal element, keeping the scrub hot-path light.
    const cards = el.changeCards.querySelectorAll(".change");
    cards.forEach((card) => {
      const idx = parseInt(card.dataset.card, 10);
      const row = card.querySelector(".change__row");
      row.addEventListener("click", () => toggleCard(cards, card, changes[idx], g));
    });
  }

  /** Expand the clicked card (building its mini-diff once), collapse the rest. */
  function toggleCard(allCards, card, change, g) {
    const reveal = card.querySelector(".change__reveal");
    const row = card.querySelector(".change__row");
    const wasOpen = !reveal.hidden;

    // Collapse every card (accordion: one open at a time).
    allCards.forEach((c) => {
      c.classList.remove("change--open");
      const r = c.querySelector(".change__reveal");
      r.hidden = true;
      c.querySelector(".change__row").setAttribute("aria-expanded", "false");
    });
    if (wasOpen) return; // clicking the open card just closes it

    if (!reveal.dataset.built) {
      reveal.innerHTML = cardMiniDiff(change, g);
      reveal.dataset.built = "1";
    }
    reveal.hidden = false;
    card.classList.add("change--open");
    row.setAttribute("aria-expanded", "true");
  }

  /**
   * The authoritative code rows for a change, or null if the classifier did not
   * attribute any (offline-flattened data). `change.code` is a list of literal diff
   * lines with their leading +/- markers preserved (a space or no marker = context).
   * Returns {kind, text} rows for miniDiffHtml; null when empty/absent.
   */
  function authoritativeCodeRows(change) {
    const code = change && change.code;
    if (!Array.isArray(code) || code.length === 0) return null;
    const rows = [];
    for (const raw of code) {
      if (typeof raw !== "string" || raw.trim() === "") continue;
      const sign = raw[0];
      if (sign === "+") rows.push({ kind: "add", text: raw.slice(1) });
      else if (sign === "-") rows.push({ kind: "del", text: raw.slice(1) });
      else rows.push({ kind: "ctx", text: sign === " " ? raw.slice(1) : raw });
    }
    return rows.length > 0 ? rows : null;
  }

  /**
   * Build a focused mini-diff for a single change card.
   *
   * AUTHORITATIVE path: if the change carries a non-empty `code` field (the R2
   * classifier attributed the real implementing diff lines to THIS change), render
   * those lines verbatim. This is the ground truth — it comes from the classifier
   * reading the actual code, not from matching against the agent's prose docstring.
   *
   * FALLBACK path (offline-flattened data with no `code`): the approach-B heuristic
   * — extract salient terms from the summary (identifiers, numbers, quoted strings,
   * code keywords) and score each diff hunk by how many of its changed (+/-) lines
   * mention those terms. The best-matched hunk wins; ties fall back to the nearest
   * changed hunk with a muted "closest change shown" note.
   */
  function cardMiniDiff(change, g) {
    const authoritative = authoritativeCodeRows(change);
    if (authoritative) return miniDiffHtml(authoritative);

    const diff = g && g.diff_from_prev;
    if (typeof diff !== "string" || !diff) {
      return '<p class="change__note">No diff available for this generation.</p>';
    }
    const terms = salientTerms(change && change.summary);
    const hunks = parseDiffHunks(diff);
    if (hunks.length === 0) {
      return '<p class="change__note">No code change found for this generation.</p>';
    }

    const scored = scoreHunks(hunks, terms);
    const best = scored.best;
    const matched = scored.maxScore > 0;
    // Window the chosen hunk around its matched (or first changed) lines.
    const rows = windowHunk(best, terms);
    const note = matched
      ? ""
      : '<p class="change__note">Closest change shown — see “Show full diff” for all.</p>';
    return note + miniDiffHtml(rows);
  }

  /**
   * Extract salient code-ish terms from a change summary: ALL_CAPS / snake_case /
   * camelCase identifiers, bare numbers, quoted strings, and a small set of
   * SQL / code keywords. Returns a deduped, lowercased term list for matching.
   */
  function salientTerms(summary) {
    if (typeof summary !== "string" || !summary) return [];
    const terms = new Set();
    // identifiers: ALL_CAPS, snake_case, camelCase, dotted (>=3 chars, has a
    // letter; excludes plain lowercase prose words via the case/underscore test).
    const idRe = /[A-Za-z_][A-Za-z0-9_.]*/g;
    let m;
    while ((m = idRe.exec(summary)) !== null) {
      const t = m[0];
      const codeish =
        /[A-Z]/.test(t) && /[a-z]/.test(t) // camelCase / PascalCase
          ? true
          : /_/.test(t) || // snake_case
            t === t.toUpperCase() || // ALL_CAPS
            t.includes("."); // dotted (env.var, obj.attr)
      if (codeish && t.length >= 3) terms.add(t.toLowerCase());
    }
    // numbers (512, 2048, 0)
    const numRe = /\b\d+\b/g;
    while ((m = numRe.exec(summary)) !== null) terms.add(m[0]);
    // quoted strings ('DISTINCT', "WHERE IN")
    const quoteRe = /['"]([^'"]+)['"]/g;
    while ((m = quoteRe.exec(summary)) !== null) {
      const inner = m[1].trim().toLowerCase();
      if (inner) terms.add(inner);
    }
    // code keywords appearing as whole words in the prose
    const KEYWORDS = [
      "distinct", "join", "group by", "order by", "where", "cast", "limit",
      "subquery", "try", "except", "retry", "temperature", "schema",
      "where in", "count", "sum",
    ];
    const lower = summary.toLowerCase();
    for (const kw of KEYWORDS) {
      if (lower.includes(kw)) terms.add(kw);
    }
    return Array.from(terms);
  }

  /**
   * Parse a unified diff into hunks. Each hunk = {lines:[{kind,text}], adds},
   * kind ∈ {"add","del","ctx"}. Keeps every changed line for per-card scoring
   * (used by the offline-data fallback when a change carries no authoritative code).
   */
  function parseDiffHunks(diff) {
    const out = [];
    let cur = null;
    for (const line of diff.split("\n")) {
      if (line.startsWith("@@")) {
        cur = { lines: [], adds: 0 };
        out.push(cur);
        continue;
      }
      if (!cur) continue; // ---/+++ headers before the first hunk
      if (line.startsWith("+") && !line.startsWith("+++")) {
        cur.lines.push({ kind: "add", text: line.slice(1) });
        cur.adds += 1;
      } else if (line.startsWith("-") && !line.startsWith("---")) {
        cur.lines.push({ kind: "del", text: line.slice(1) });
      } else {
        cur.lines.push({ kind: "ctx", text: line.replace(/^ /, "") });
      }
    }
    return out;
  }

  /** Does a line's text mention any salient term? (case-insensitive substring) */
  function lineMatches(text, terms) {
    if (terms.length === 0) return false;
    const lower = text.toLowerCase();
    return terms.some((t) => lower.includes(t));
  }

  /**
   * Score every hunk by the count of its CHANGED (+/-) lines that mention a
   * salient term. Returns {best, maxScore}. With no term hits, best = the hunk
   * with the most added lines (the nearest substantive change) and maxScore = 0.
   */
  function scoreHunks(hunks, terms) {
    let best = hunks[0];
    let maxScore = -1;
    for (const h of hunks) {
      let score = 0;
      for (const r of h.lines) {
        if (r.kind !== "ctx" && lineMatches(r.text, terms)) score += 1;
      }
      if (score > maxScore) {
        maxScore = score;
        best = h;
      }
    }
    if (maxScore <= 0) {
      // fallback: densest-add hunk so we never show nothing
      best = hunks.slice().sort((a, b) => b.adds - a.adds)[0];
      maxScore = 0;
    }
    return { best, maxScore };
  }

  /**
   * Window a hunk down to the matched changed lines plus ~1-2 lines of context.
   * Anchors on the first matched changed line (or the first changed line if
   * nothing matched) and shows a capped window so the mini-diff stays focused.
   */
  function windowHunk(hunk, terms) {
    const lines = hunk.lines;
    const CTX = 2;
    const MAX = 12;
    // anchor = first changed line that matches a term, else first changed line.
    let anchor = lines.findIndex((r) => r.kind !== "ctx" && lineMatches(r.text, terms));
    if (anchor === -1) anchor = lines.findIndex((r) => r.kind !== "ctx");
    if (anchor === -1) anchor = 0;
    const start = Math.max(0, anchor - CTX);
    const end = Math.min(lines.length, start + MAX);
    return lines.slice(start, end);
  }

  /** Render windowed diff rows as a compact mini-diff (reuses .rcode styling). */
  function miniDiffHtml(rows) {
    if (!rows || rows.length === 0) {
      return '<p class="change__note">No code change found.</p>';
    }
    const body = rows
      .map(
        (r) =>
          `<span class="rcode rcode--${r.kind}">` +
          `<span class="rcode__sign">${r.kind === "add" ? "+" : r.kind === "del" ? "−" : " "}</span>` +
          `<span class="rcode__text">${escapeHtml(r.text)}</span></span>`
      )
      .join("");
    return `<pre class="change__code">${body}</pre>`;
  }

  /**
   * Lazily render the full diff (diff2html) into the collapsible toggle. Only
   * draws when the toggle is open and the gen changed — keeps the heavy render
   * off the scrub hot-path. Hidden entirely when there's no diff.
   */
  function prepareFullDiff(g, genNumber) {
    const diffText = g ? g.diff_from_prev : null;
    if (!diffText) {
      el.fullDiff.hidden = true;
      el.fullDiff.open = false;
      fullDiffDrawnGen = null;
      el.paneDiff.innerHTML = "";
      return;
    }
    el.fullDiff.hidden = false;
    // If it's already open, redraw for the new gen; otherwise defer to toggle.
    if (el.fullDiff.open) {
      drawFullDiff(genNumber);
    } else {
      fullDiffDrawnGen = null;
      el.paneDiff.innerHTML = "";
    }
  }

  function drawFullDiff(genNumber) {
    if (fullDiffDrawnGen === genNumber) return;
    const g = genAt(genNumber);
    const diffText = g ? g.diff_from_prev : null;
    if (!diffText || !window.Diff2HtmlUI) return;
    diffUI = new window.Diff2HtmlUI(el.paneDiff, diffText, {
      drawFileList: false,
      matching: "words",
      outputFormat: "line-by-line",
    });
    diffUI.draw();
    fullDiffDrawnGen = genNumber;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /**
   * Per-selected-generation bucket breakdown: one bar per bucket present in
   * generations[gen].taxonomy.counts, colored by the stable taxonomy hue order.
   * The pane title + the "how it improved" sub-line update with the gen. gen 1
   * (empty taxonomy) renders gracefully as a "first harness" empty state.
   */
  function renderTaxonomy(genNumber) {
    if (!taxonomyChart) return;
    const g = genAt(genNumber);
    updateTaxonomyTitle(genNumber);
    updateTaxonomySub(g, genNumber);

    const counts = (g && g.taxonomy && g.taxonomy.counts) || {};
    // Order the gen's buckets by the stable cross-gen order so a bucket keeps
    // its hue when you scrub. Only buckets present this gen get a bar.
    const buckets = taxonomyBuckets.filter((b) => (counts[b] || 0) > 0);

    if (buckets.length === 0) {
      // gen 1 / empty taxonomy — clear the canvas, the sub-line carries the why.
      taxonomyChart.clear();
      taxonomyChart.setOption({
        grid: { left: 120, right: 24, top: 16, bottom: 24 },
        xAxis: { type: "value", show: false },
        yAxis: { type: "category", data: [], show: false },
        series: [],
      });
      return;
    }

    // Labels + colors from the single BUCKET_META map — identical to the cards.
    const labels = buckets.map((b) => bucketLabel(b));
    const barData = buckets.map((b) => ({
      value: counts[b] || 0,
      itemStyle: {
        color: bucketColor(b),
        borderColor: COLORS.seam,
        borderWidth: 2,
      },
    }));

    taxonomyChart.clear();
    taxonomyChart.setOption({
      grid: { left: 130, right: 36, top: 16, bottom: 28 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "value",
        name: "changes",
        minInterval: 1,
        nameTextStyle: { color: COLORS.text, fontSize: 14 },
        axisLabel: { color: COLORS.text, fontSize: 14 },
        splitLine: { lineStyle: { color: COLORS.grid } },
      },
      yAxis: {
        type: "category",
        data: labels,
        inverse: true,
        axisLine: { lineStyle: { color: COLORS.muted } },
        axisLabel: { color: COLORS.text, fontSize: 14 },
      },
      series: [
        {
          type: "bar",
          data: barData,
          barMaxWidth: 26,
          label: { show: true, position: "right", color: COLORS.text, fontSize: 14 },
          emphasis: { focus: "self" },
        },
      ],
    });
  }

  function updateTaxonomyTitle(genNumber) {
    if (el.taxonomyTitle) {
      el.taxonomyTitle.textContent = "Research taxonomy — Generation " + genNumber;
    }
  }

  /**
   * "how it improved" sub-line: accuracy delta vs the previous gen + the change
   * count and its bucket breakdown, e.g. "+22.9% · 4 changes: 3 prompt-restructure,
   * 1 retry". gen 1 (no prior, no changes) gets a framed first-harness note.
   */
  function updateTaxonomySub(g, genNumber) {
    if (!el.taxonomySub) return;
    const counts = (g && g.taxonomy && g.taxonomy.counts) || {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);

    if (total === 0) {
      el.taxonomySub.textContent =
        "The first harness — no previous generation to compare against yet.";
      return;
    }

    const prior = priorGradedAccuracy(genNumber);
    const pct = g.metrics && typeof g.metrics.accuracy_percent === "number" ? g.metrics.accuracy_percent : null;
    let deltaTxt = "";
    if (typeof prior === "number" && typeof pct === "number") {
      const d = pct - prior;
      const sign = d >= 0 ? "+" : "−";
      deltaTxt = sign + Math.abs(d).toFixed(1) + "% · ";
    }

    const parts = taxonomyBuckets
      .filter((b) => (counts[b] || 0) > 0)
      .map((b) => counts[b] + " " + bucketLabel(b));
    const changeWord = total === 1 ? "change" : "changes";
    el.taxonomySub.textContent = deltaTxt + total + " " + changeWord + ": " + parts.join(", ");
  }

  // ===========================================================================
  // Caption — felt-outcome copy, data-keyed (honest framing). Numbers pair with
  // plain meaning; verbs stay mechanical. Derived from the gen's own data
  // (self-repair presence, accuracy delta, primary_bucket) — never hardcoded.
  // ===========================================================================

  const BUCKET_GLOSS = {
    "retry/robustness": "added SQL self-repair — when a query errors it catches it and retries",
    "prompt-restructure": "restructured its prompt with sharper SQL rules and schema hints",
    validation: "added validation rules so the SQL it emits matches what the question asked",
    "parser-hardening": "hardened how it parses the model's reply into runnable SQL",
    "new-tool": "added a new capability to its harness",
    "schema-injection": "fed the live database schema into its own prompt",
  };

  /** Previous graded accuracy_percent before genNumber, or null. */
  function priorGradedAccuracy(genNumber) {
    let prior = null;
    for (const g of data.generations || []) {
      if (g.gen >= genNumber) break;
      if (g.metrics && typeof g.metrics.accuracy_percent === "number") {
        prior = g.metrics.accuracy_percent;
      }
    }
    return prior;
  }

  /** Does this gen carry a self-repair diff highlight? (data-keyed, not by index) */
  function hasSelfRepairDiff(g) {
    return !!(
      g &&
      Array.isArray(g.diff_highlights) &&
      g.diff_highlights.some(
        (h) => h.kind === "self-repair" && Array.isArray(h.added_lines) && h.added_lines.length > 0
      )
    );
  }

  function changeSentence(g, genNumber) {
    const tx = g.taxonomy || {};
    const prior = priorGradedAccuracy(genNumber);
    const pct = g.metrics && typeof g.metrics.accuracy_percent === "number" ? g.metrics.accuracy_percent : null;
    const stepped = typeof prior === "number" && typeof pct === "number" && pct - prior >= 0.05;
    const stepTxt =
      stepped && pct != null ? ` Accuracy stepped up ${prior.toFixed(0)}% → ${pct.toFixed(0)}%.` : "";

    if (isSelfRepairGen(genNumber) || hasSelfRepairDiff(g)) {
      const gloss = BUCKET_GLOSS["retry/robustness"];
      return `The agent ${gloss}, and tightened its prompt with sample rows and schema hints.${stepTxt}`;
    }

    const primary = tx.primary_bucket;
    if (primary && BUCKET_GLOSS[primary]) {
      return `The agent ${BUCKET_GLOSS[primary]}.${stepTxt}`;
    }
    if (stepTxt) return `The agent reworked its harness.${stepTxt}`;
    return "The agent reworked its harness.";
  }

  function renderCaption(genNumber) {
    const g = genAt(genNumber);
    if (!g) {
      el.caption.textContent = "";
      return;
    }
    const m = g.metrics || {};
    if (typeof m.accuracy_percent !== "number") {
      el.caption.textContent =
        `Generation ${genNumber}: the first harness, run as captured — no graded results for this generation.`;
      return;
    }
    const pct = m.accuracy_percent.toFixed(0);
    const count =
      typeof m.correct === "number" && typeof m.total === "number"
        ? ` (${m.correct} of ${m.total} queries returned exactly the right rows when we ran them)`
        : "";
    el.caption.textContent = `Generation ${genNumber} — ${changeSentence(g, genNumber)} Now at ${pct}%${count}.`;
  }

  // ===========================================================================
  // Scrubber
  // ===========================================================================
  function buildScrubber() {
    const gens = generationNumbers();
    if (gens.length === 0) return;

    el.ticks.innerHTML = "";
    for (const n of gens) {
      const tick = document.createElement("button");
      tick.type = "button";
      tick.className = "tick";
      tick.dataset.gen = String(n);
      tick.textContent = String(n);
      tick.setAttribute("aria-label", "Generation " + n);
      tick.addEventListener("click", () => scrubTo(n));
      el.ticks.appendChild(tick);
    }
  }

  /** Drive all three panes from one gen index, then run the wow choreography. */
  function scrubTo(genNumber) {
    const gens = generationNumbers();
    if (gens.indexOf(genNumber) === -1) return;
    state.gen = genNumber;
    updateTickHighlight();
    renderCurve(genNumber);
    renderWhatChanged(genNumber);
    renderTaxonomy(genNumber);
    renderCaption(genNumber);
    playWow(genNumber);
  }

  // ===========================================================================
  // The synchronized "wow": one GSAP master timeline per scrub transition.
  // At the self-repair generation: the key-change block's code rows green-pulse,
  // the curve marker pops, the taxonomy self-repair segment is emphasized — all
  // started together so they read as one motion. Keyed off the DATA
  // (headline.self_repair_gen + diff_highlights[]), never a hardcoded gen index.
  // prefers-reduced-motion collapses every tween to the static final frame.
  // ===========================================================================
  let wowTimeline = null;

  function playWow(genNumber) {
    if (wowTimeline) {
      wowTimeline.kill();
      wowTimeline = null;
    }
    if (!isSelfRepairGen(genNumber)) return;
    if (reduceMotion || !window.gsap) {
      // reduced-motion / no-GSAP: leave the static .self-repair tint as-is.
      return;
    }

    // The key-change block's rows are in the DOM synchronously (renderKeyChange
    // ran in scrubTo). A ~150ms stagger keeps the pulse below the 1s "feels
    // simultaneous" threshold while guaranteeing the target exists.
    const pulseRows = el.keychange.querySelectorAll("." + SELF_REPAIR_HIGHLIGHT_CLASS);
    const tl = window.gsap.timeline();
    wowTimeline = tl;

    if (pulseRows.length > 0) {
      tl.fromTo(
        pulseRows,
        { backgroundColor: PULSE_GREEN_BRIGHT },
        {
          backgroundColor: PULSE_GREEN_REST,
          duration: WOW_PULSE_MS / 1000,
          ease: "power2.out",
          overwrite: "auto",
        },
        0.15
      );
    }

    tl.add(() => popCurveMarker(genNumber), 0);
    tl.add(() => emphasizeTaxonomySegment(SELF_REPAIR_BUCKET), 0);
  }

  /** Pop the accuracy curve marker at the self-repair gen via ECharts highlight. */
  function popCurveMarker(genNumber) {
    if (!curveChart || reduceMotion) return;
    try {
      curveChart.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex: genIndexOf(genNumber) });
      window.setTimeout(() => {
        if (curveChart) {
          curveChart.dispatchAction({ type: "downplay", seriesIndex: 0 });
        }
      }, WOW_PULSE_MS);
    } catch (_e) {
      /* highlight unsupported — the ECharts setOption morph already moved the marker */
    }
  }

  function genIndexOf(genNumber) {
    return generationNumbers().indexOf(genNumber);
  }

  /**
   * Emphasize the self-repair bucket's bar in the per-gen taxonomy. The bars are
   * ordered by taxonomyBuckets filtered to those present this gen, so find the
   * bucket's row index in the currently-rendered set.
   */
  function emphasizeTaxonomySegment(bucket) {
    if (!taxonomyChart || reduceMotion) return;
    const g = genAt(state.gen);
    const counts = (g && g.taxonomy && g.taxonomy.counts) || {};
    const present = taxonomyBuckets.filter((b) => (counts[b] || 0) > 0);
    const dataIndex = present.indexOf(bucket);
    if (dataIndex === -1) return;
    try {
      taxonomyChart.dispatchAction({ type: "highlight", seriesIndex: 0, dataIndex });
      window.setTimeout(() => {
        if (taxonomyChart) {
          taxonomyChart.dispatchAction({ type: "downplay", seriesIndex: 0, dataIndex });
        }
      }, WOW_PULSE_MS);
    } catch (_e) {
      /* highlight unsupported for this option shape — non-fatal */
    }
  }

  function updateTickHighlight() {
    const ticks = el.ticks.querySelectorAll(".tick");
    for (const t of ticks) {
      const active = parseInt(t.dataset.gen, 10) === state.gen;
      t.classList.toggle("tick--active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    }
  }

  /** Step the current generation by ±1 (keyboard ←/→ on the live screen). */
  function stepGen(delta) {
    const gens = generationNumbers();
    const i = gens.indexOf(state.gen);
    if (i === -1) return;
    const next = gens[Math.max(0, Math.min(gens.length - 1, i + delta))];
    if (next !== state.gen) scrubTo(next);
  }

  // ===========================================================================
  // Shared static sparkline — a tiny hand-laid SVG of the real accuracy series.
  // Used by screen 0 (frozen climb) and screen 2 (advancing under the explainer).
  // The y-range is fit to the data (min..max + padding), NOT anchored to 0..100,
  // so the climb reads steep on a projector. `progress` (0..1) clips how many
  // points are drawn (B3 beat-4 advance).
  // ===========================================================================
  function renderSparkline(container, progress) {
    if (!container) return;
    const series = accuracySeries().filter((v) => typeof v === "number");
    if (series.length === 0) {
      container.innerHTML = "";
      return;
    }
    const visible =
      typeof progress === "number"
        ? Math.max(1, Math.round(progress * series.length))
        : series.length;
    const pts = series.slice(0, visible);

    const w = 100;
    const h = 100;
    // data-fitted range: pad ~15% of the data span (min 5pts) so the 4 points
    // fill the height and the climb reads steep — never the flat 0..100 box.
    const rawMax = Math.max(...series);
    const rawMin = Math.min(...series);
    const pad = (rawMax - rawMin) * 0.15 || 5;
    const max = rawMax + pad;
    const min = Math.max(0, rawMin - pad);
    const span = max - min || 1;
    const dx = series.length > 1 ? w / (series.length - 1) : 0;
    const coords = pts.map((v, i) => {
      const x = i * dx;
      const y = h - ((v - min) / span) * h;
      return { x, y };
    });
    const path = coords.map((c, i) => (i === 0 ? "M" : "L") + c.x.toFixed(1) + " " + c.y.toFixed(1)).join(" ");
    const dots = coords
      .map((c, i) => {
        const last = i === coords.length - 1 && pts.length === series.length;
        return `<circle class="spark-dot${last ? " spark-dot--last" : ""}" cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="4" />`;
      })
      .join("");

    container.innerHTML =
      `<svg class="spark-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
      `<path class="spark-line" d="${path}" />${dots}</svg>`;
  }

  // ===========================================================================
  // Static screen content renderers (B6). Each fills its screen from `data`.
  // Copy: no anthropomorphizing verbs; numbers paired with meaning.
  // ===========================================================================
  function renderHook() {
    renderSparkline(el.hookSpark, 1);
    const first = firstGradedAccuracy();
    const last = lastGradedAccuracy();
    if (el.hookStat && typeof first === "number" && typeof last === "number") {
      const gain = Math.round((last - first) * 10) / 10;
      // The number dominates (own span, --fs-stat); the plain-meaning gloss
      // stays caption-sized beneath it.
      el.hookStat.innerHTML =
        `<span class="hook__stat-num">${first.toFixed(0)}% → ${last.toFixed(0)}%</span>` +
        `<span class="hook__stat-gloss">Same model, better harness — +${gain} points. ` +
        `That many more queries returned the right answer when we ran them.</span>`;
    }
  }

  function renderExample() {
    const ex = data.example_query;
    if (!ex || !ex.generated_sql || !el.exampleBox) return;
    el.exampleBox.hidden = false;
    el.exampleSql.textContent = ex.generated_sql;
    const pass = ex.passed === true;
    el.exampleVerdict.textContent = pass
      ? "Ran clean and returned the right rows."
      : "Ran, but the rows did not match the answer.";
    el.exampleVerdict.classList.toggle("is-pass", pass);
    el.exampleVerdict.classList.toggle("is-fail", !pass);
  }

  function renderLens() {
    const h = (data && data.headline) || {};
    if (el.lensSePct && typeof h.se_hygiene_pct === "number") {
      el.lensSePct.textContent = h.se_hygiene_pct + "%";
    }
    if (el.lensDomainPct && typeof h.domain_reasoning_pct === "number") {
      el.lensDomainPct.textContent = h.domain_reasoning_pct + "%";
    }
    const hacks = typeof h.task_specific_hack_count === "number" ? h.task_specific_hack_count : 0;
    if (el.lensHackCount) el.lensHackCount.textContent = String(hacks);
    if (el.lensHackNote) {
      el.lensHackNote.textContent =
        hacks === 0
          ? NO_HACK_NOTE
          : `Research flagged ${hacks} change${hacks === 1 ? "" : "s"} that look like gaming the grader — worth a human review.`;
    }
  }

  function renderResult() {
    const h = (data && data.headline) || {};
    if (!el.resultNums) return;
    const first = typeof h.accuracy_first === "number" ? h.accuracy_first : firstGradedAccuracy();
    const last = typeof h.accuracy_last === "number" ? h.accuracy_last : lastGradedAccuracy();
    const gain = typeof h.gain === "number" ? h.gain : null;
    const items = [];
    if (typeof last === "number") {
      items.push([
        last.toFixed(0) + "%",
        "of the queries returned exactly the right rows when we ran them, by the last generation.",
      ]);
    }
    if (typeof gain === "number" && typeof first === "number") {
      items.push([
        "+" + gain + " pts",
        `up from ${first.toFixed(0)}% at the first working harness — same model throughout.`,
      ]);
    }
    el.resultNums.innerHTML = items
      .map(
        ([val, lbl]) =>
          `<div class="result__num"><span class="result__num-val">${val}</span>` +
          `<span class="result__num-lbl">${lbl}</span></div>`
      )
      .join("");
  }

  // ===========================================================================
  // B3 — SIA-loop explainer: 4-beat cycle over the hand-laid SVG.
  // Beat 1 Meta · Beat 2 Target+Verifier · Beat 3 Feedback · Beat 4 feedback
  // arrow flows + gen counter advances + sparkline advances. GSAP draws the
  // arrow when available; prefers-reduced-motion freezes to a static frame.
  // ===========================================================================
  const LOOP_BEATS = [
    { active: ["nodeMeta"], text: "Meta writes the first agent." },
    { active: ["nodeTarget", "nodeVerifier"], text: "The target tries English→SQL; we run it and grade ✓/✗." },
    { active: ["nodeFeedback"], text: "Feedback reads the failures." },
    { active: ["feedbackArrow"], text: "Feedback rewrites the agent. Next generation — repeat." },
  ];

  function clearLoopActive() {
    [el.nodeMeta, el.nodeTarget, el.nodeFeedback, el.nodeVerifier, el.feedbackArrow].forEach((node) => {
      if (node) node.classList.remove("is-active");
    });
  }

  function applyLoopBeat(beatIndex) {
    clearLoopActive();
    const beat = LOOP_BEATS[beatIndex];
    beat.active.forEach((key) => {
      if (el[key]) el[key].classList.add("is-active");
    });
    if (el.loopBeatText) el.loopBeatText.textContent = beat.text;

    const genCount = generationNumbers().length || 1;

    if (beatIndex === LOOP_BEATS.length - 1) {
      const cycle = Math.floor(loopBeat / LOOP_BEATS.length);
      const gen = Math.min(genCount, (cycle % genCount) + 1);
      if (el.loopGen) el.loopGen.textContent = "Generation " + gen;
      renderSparkline(el.loopSpark, gen / genCount);
      if (window.gsap && el.feedbackArrow && window.DrawSVGPlugin && !reduceMotion) {
        try {
          window.gsap.fromTo(
            el.feedbackArrow,
            { drawSVG: "0%" },
            { drawSVG: "100%", duration: 0.6, ease: "power1.inOut" }
          );
        } catch (_e) {
          /* DrawSVG unavailable — the CSS .is-active stroke still highlights it */
        }
      }
    } else if (beatIndex === 0) {
      const cycle = Math.floor(loopBeat / LOOP_BEATS.length);
      const gen = (cycle % genCount) + 1;
      if (el.loopGen) el.loopGen.textContent = "Generation " + gen;
      renderSparkline(el.loopSpark, Math.max(1, gen) / genCount);
    }
  }

  function startLoopExplainer() {
    stopLoopExplainer();
    if (reduceMotion) {
      [el.nodeMeta, el.nodeTarget, el.nodeFeedback, el.nodeVerifier, el.feedbackArrow].forEach((n) => {
        if (n) n.classList.add("is-active");
      });
      if (el.loopBeatText) el.loopBeatText.textContent = LOOP_BEATS[LOOP_BEATS.length - 1].text;
      if (el.loopGen) el.loopGen.textContent = "Generation " + (generationNumbers().length || 1);
      renderSparkline(el.loopSpark, 1);
      return;
    }
    loopBeat = 0;
    applyLoopBeat(0);
    loopBeatTimer = window.setInterval(() => {
      loopBeat += 1;
      applyLoopBeat(loopBeat % LOOP_BEATS.length);
    }, LOOP_BEAT_MS);
  }

  function stopLoopExplainer() {
    if (loopBeatTimer !== null) {
      window.clearInterval(loopBeatTimer);
      loopBeatTimer = null;
    }
  }

  // ===========================================================================
  // Screen 7 — closing-the-loop: draw in the ONE new wire (the credit signal
  // re-routed back into the loop). Echoes the screen-2 DrawSVG technique. The
  // wire is visible by default (CSS); GSAP just reveals it with a draw-on +
  // a brief pulse of the R2 node. Reduced-motion / no-GSAP: stays static.
  // ===========================================================================
  function startCloseLoop() {
    if (reduceMotion || !window.gsap || !window.DrawSVGPlugin || !el.clNewwire) return;
    try {
      const tl = window.gsap.timeline();
      // arrowhead hidden until the wire finishes drawing, so it doesn't float
      if (el.clNewwireHead) tl.set(el.clNewwireHead, { opacity: 0 }, 0);
      tl.fromTo(
        el.clNewwire,
        { drawSVG: "0%" },
        { drawSVG: "100%", duration: 1.1, ease: "power2.inOut" },
        0.2
      );
      if (el.clNewwireHead) tl.to(el.clNewwireHead, { opacity: 1, duration: 0.2 }, ">-0.05");
    } catch (_e) {
      /* DrawSVG unavailable — the CSS stroke keeps the wire visible regardless */
    }
  }

  // ===========================================================================
  // Screen state machine — manual only. The presenter drives with the dots,
  // Prev/Next, and the keyboard / presenter clicker (←/→/Home/End). The
  // scrubber drives generations within screen 4.
  // ===========================================================================
  function buildStageDots() {
    el.dots.innerHTML = "";
    for (let i = 0; i < SCREEN_COUNT; i++) {
      const dot = document.createElement("button");
      dot.type = "button";
      dot.className = "dot";
      dot.setAttribute("aria-label", "Go to " + (SCREEN_NAMES[i] || "screen " + i));
      dot.addEventListener("click", () => goToScreen(i));
      el.dots.appendChild(dot);
    }
  }

  function goToScreen(screenIndex) {
    state.screen = Math.max(0, Math.min(SCREEN_COUNT - 1, screenIndex));
    render(state);
  }

  /** The one render(state): swap the active screen, then refresh its content. */
  function render(s) {
    el.screens.forEach((sec) => {
      const idx = parseInt(sec.dataset.screen, 10);
      sec.classList.toggle("screen--active", idx === s.screen);
    });
    el.dots.querySelectorAll(".dot").forEach((dot, i) => {
      dot.classList.toggle("dot--active", i === s.screen);
    });
    if (el.screenLabel) {
      el.screenLabel.textContent = `${s.screen + 1} / ${SCREEN_COUNT} · ${SCREEN_NAMES[s.screen] || ""}`;
    }

    // The SIA-loop explainer only runs while screen 2 is visible.
    if (s.screen === LOOP_SCREEN && data) {
      startLoopExplainer();
    } else {
      stopLoopExplainer();
    }

    // Screen 7 (Ongoing TODO): draw in the new wire each time it becomes visible.
    // Independent of run data — it's a conceptual diagram, not a data view.
    if (s.screen === CLOSE_LOOP_SCREEN) {
      requestAnimationFrame(startCloseLoop);
    }

    if (data) {
      if (s.screen === HOOK_SCREEN) renderHook();
      else if (s.screen === 3) renderExample();
      else if (s.screen === 5) renderLens();
      else if (s.screen === RESULT_SCREEN) renderResult();
    }

    // Screen 4 owns the live panes; ECharts must resize after becoming visible.
    if (s.screen === LIVE_SCREEN && data) {
      requestAnimationFrame(() => {
        ensureCharts();
        if (curveChart) curveChart.resize();
        if (taxonomyChart) taxonomyChart.resize();
        scrubTo(s.gen);
      });
    }
  }

  function ensureCharts() {
    const curveEl = document.getElementById("pane-curve");
    const taxEl = document.getElementById("pane-taxonomy");
    if (!curveChart && curveEl) curveChart = window.echarts.init(curveEl, null, { renderer: "canvas" });
    if (!taxonomyChart && taxEl) taxonomyChart = window.echarts.init(taxEl, null, { renderer: "canvas" });
  }

  // ===========================================================================
  // Wiring + boot
  // ===========================================================================
  function wireControls() {
    el.btnPrev.addEventListener("click", () => goToScreen(state.screen - 1));
    el.btnNext.addEventListener("click", () => goToScreen(state.screen + 1));
    el.btnStart.addEventListener("click", () => goToScreen(HOOK_SCREEN));
    el.btnImprove.addEventListener("click", () => goToScreen(LIVE_SCREEN));
    el.title.addEventListener("click", () => goToScreen(HOOK_SCREEN));

    // Full-diff toggle: draw lazily the first time it's opened for a gen.
    if (el.fullDiff) {
      el.fullDiff.addEventListener("toggle", () => {
        if (el.fullDiff.open) drawFullDiff(state.gen);
      });
    }

    // Hook + result CTAs
    if (el.hookWatch) el.hookWatch.addEventListener("click", () => goToScreen(LIVE_SCREEN));
    if (el.resultRestart) el.resultRestart.addEventListener("click", () => goToScreen(HOOK_SCREEN));
    if (el.resultExplore) el.resultExplore.addEventListener("click", () => goToScreen(LIVE_SCREEN));

    // Keyboard / presenter-clicker navigation. A clicker sends arrow keys; this
    // is the primary driver for a live talk. The scrubber drives generations
    // within screen 4 — only intercept arrows when focus isn't on the range.
    window.addEventListener("keydown", onKeyNav);

    window.addEventListener("resize", () => {
      if (curveChart) curveChart.resize();
      if (taxonomyChart) taxonomyChart.resize();
    });
  }

  function onKeyNav(e) {
    // On the live screen, ←/→ step generations (the scrubber's job); PageUp/Down
    // still move between screens so the presenter can leave screen 4.
    const onLive = state.screen === LIVE_SCREEN;
    switch (e.key) {
      case "ArrowRight":
        e.preventDefault();
        if (onLive) stepGen(1);
        else goToScreen(state.screen + 1);
        break;
      case "ArrowLeft":
        e.preventDefault();
        if (onLive) stepGen(-1);
        else goToScreen(state.screen - 1);
        break;
      case "PageDown":
        e.preventDefault();
        goToScreen(state.screen + 1);
        break;
      case "PageUp":
        e.preventDefault();
        goToScreen(state.screen - 1);
        break;
      case "Home":
        e.preventDefault();
        goToScreen(HOOK_SCREEN);
        break;
      case "End":
        e.preventDefault();
        goToScreen(LAST_SCREEN);
        break;
      default:
        break;
    }
  }

  function showError(message) {
    el.errorText.textContent = message;
    el.overlayError.hidden = false;
  }

  function registerGsapPlugins() {
    if (window.gsap && window.DrawSVGPlugin) {
      try {
        window.gsap.registerPlugin(window.DrawSVGPlugin);
      } catch (_e) {
        /* plugin already registered or unavailable — CSS highlight still works */
      }
    }
  }

  // Default run = the BIG model (Gemma) — the opener. The presenter then switches
  // to the smaller Nemotron for the reveal ("smaller model, even bigger SIA gain").
  const DEFAULT_RUN_FILE = "demo_data_gemma.json";

  /**
   * Load (or switch to) a run data file: fetch it, reset state to the run's
   * self-repair gen, rebuild the charts + scrubber, and re-render everything.
   * Robust loading/error/empty states. Called on boot and by the selector.
   */
  async function loadRun(file) {
    el.overlayError.hidden = true;
    if (el.overlayEmpty) el.overlayEmpty.hidden = true;
    el.overlayLoading.hidden = false;

    let loaded;
    try {
      const resp = await fetch(file, { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      loaded = await resp.json();
    } catch (err) {
      el.overlayLoading.hidden = true;
      showError(
        "Couldn't load " +
          file +
          " (" +
          err.message +
          ").\nServe this folder with:  python -m http.server\nthen open  http://localhost:8000/"
      );
      return;
    }
    el.overlayLoading.hidden = true;

    if (!loaded || !Array.isArray(loaded.generations) || loaded.generations.length === 0) {
      if (el.overlayEmpty) el.overlayEmpty.hidden = false;
      return;
    }

    data = loaded;
    taxonomyBuckets = collectTaxonomyBuckets();
    const gens = generationNumbers();
    const sr = data.headline && data.headline.self_repair_gen;
    state.gen = gens.indexOf(sr) !== -1 ? sr : gens[0];

    maybeShowPartialBanner();
    buildScrubber();
    ensureCharts();
    render(state);
  }

  function setActiveExp(file) {
    const map = [
      [el.expNemotron, "demo_data_nemotron.json"],
      [el.expGemma, "demo_data_gemma.json"],
    ];
    for (const [btn, f] of map) {
      if (!btn) continue;
      const active = f === file;
      btn.classList.toggle("exp__btn--active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function wireExperimentSelector() {
    const map = [
      [el.expNemotron, "demo_data_nemotron.json"],
      [el.expGemma, "demo_data_gemma.json"],
    ];
    for (const [btn, file] of map) {
      if (!btn) continue;
      btn.addEventListener("click", () => {
        setActiveExp(file);
        loadRun(file);
      });
    }
  }

  async function boot() {
    registerGsapPlugins();
    buildStageDots();
    wireControls();
    wireExperimentSelector();
    setActiveExp(DEFAULT_RUN_FILE);
    await loadRun(DEFAULT_RUN_FILE);
  }

  function maybeShowPartialBanner() {
    if (!el.partialBanner) return;
    const missing = (data.generations || []).filter(
      (g) => !g.metrics || typeof g.metrics.accuracy_percent !== "number"
    );
    if (missing.length === 0) return;
    const gensTxt = missing.map((g) => "gen " + g.gen).join(", ");
    el.partialBanner.hidden = false;
    el.partialBanner.textContent =
      `Showing the run as captured. ${gensTxt} has no graded results — its panes say so; the rest render normally.`;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
