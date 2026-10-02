const REVIEW_STATES = [
  "UNREVIEWED",
  "INTERESTING",
  "LIKELY_COMET",
  "LIKELY_STREAMER",
  "LIKELY_STAR",
  "LIKELY_ARTIFACT",
  "KNOWN_OBJECT"
];

const state = {
  candidates: [],
  realtime: null,
  query: "",
  reviewClass: "all",
  motion: "all",
  instrument: "all",
  myReview: "all",
  markedOnly: false,
  sort: "verified"
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit"
  });
}

function scoreText(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(1) : "—";
}

function metricText(value, digits = 2) {
  const n = Number(value);
  if (Number.isFinite(n)) return n.toFixed(digits).replace(/\.00$/, "");
  return value ?? "—";
}

function normalizeClass(value) {
  return String(value || "UNCLASSIFIED").trim().toUpperCase().replaceAll(" ", "_");
}

function prettyClass(value) {
  return normalizeClass(value).replaceAll("_", " ");
}

function prettyVideoVariant(value) {
  return String(value || "verification video").replaceAll("_", " ");
}

function badgeClass(value) {
  const c = normalizeClass(value);
  if (c === "STRONG_REVIEW") return "status-confirmed";
  if (c === "SECONDARY") return "status-known";
  if (c === "REJECTED") return "status-rejected";
  return "status-candidate";
}

function motionClass(c) {
  const speed = Math.abs(Number(c.speed));
  const sun = Number(c.sun_distance);
  if (!Number.isFinite(speed) || speed < 0.05 || !Number.isFinite(sun)) return "Unclassified motion";
  const ratio = Math.min(1.5, Math.abs(sun) / speed);
  if (sun > 0 && ratio >= 0.78) return "Strong sunward";
  if (sun > 0 && ratio >= 0.35) return "Sunward";
  if (sun < 0 && ratio >= 0.78) return "Strong anti-sunward";
  if (sun < 0 && ratio >= 0.35) return "Anti-sunward";
  return "Mostly transverse";
}

function radialityText(c) {
  const speed = Math.abs(Number(c.speed));
  const sun = Number(c.sun_distance);
  if (!Number.isFinite(speed) || speed < 0.05 || !Number.isFinite(sun)) return "—";
  return Math.round(Math.min(1, Math.abs(sun) / speed) * 100) + "%";
}

function isFresh(c) {
  if (!c.verified_at) return false;
  const t = new Date(c.verified_at).getTime();
  return Number.isFinite(t) && (Date.now() - t) >= 0 && (Date.now() - t) < 36 * 60 * 60 * 1000;
}

function knownFamily(c) {
  const family = String(c.family || "").trim();
  return family && !/^unknown$/i.test(family) && !/^unclassified$/i.test(family);
}

function candidateKey(c) {
  return String(c.event_id || c.id);
}

function reviewStorageKey(c) {
  return "soho-review:" + candidateKey(c);
}

function markStorageKey(c) {
  return "soho-mark:" + candidateKey(c);
}

function getReview(c) {
  const persisted = String(c.review_state || "").toUpperCase();
  if (REVIEW_STATES.includes(persisted) && persisted !== "UNREVIEWED") return persisted;

  const value = localStorage.getItem(reviewStorageKey(c)) || "UNREVIEWED";
  return REVIEW_STATES.includes(value) ? value : "UNREVIEWED";
}

function setReview(c, value) {
  if (c.review_locked) return;
  if (value === "UNREVIEWED") localStorage.removeItem(reviewStorageKey(c));
  else localStorage.setItem(reviewStorageKey(c), value);
}

function isMarked(c) {
  return c.starred === true || localStorage.getItem(markStorageKey(c)) === "1";
}

function setMarked(c, marked) {
  if (c.star_locked) return;
  if (marked) localStorage.setItem(markStorageKey(c), "1");
  else localStorage.removeItem(markStorageKey(c));
}

function reviewBadge(c) {
  const review = getReview(c);
  if (review === "UNREVIEWED") return "";
  return `<span class="badge user-review-badge">${escapeHtml(prettyClass(review))}</span>`;
}

function candidateCard(c) {
  const preview = c.thumbnail
    ? `<img src="${escapeHtml(c.thumbnail)}" alt="Preview for ${escapeHtml(c.id)}">`
    : `<div class="preview-fallback">☄</div>`;

  const freshBadge = isFresh(c) ? '<span class="badge badge-new">NEW</span>' : "";
  const marked = isMarked(c);
  const permanentMark = c.starred === true && c.star_locked === true;
  const motion = motionClass(c);

  return `
    <article class="candidate-card" data-open-card="${escapeHtml(c.id)}" role="button" tabindex="0"
      aria-label="Open review for ${escapeHtml(c.event_id || c.id)}">
      <div class="preview">
        ${preview}
        <button class="mark-button ${marked ? "is-marked" : ""}" data-mark="${escapeHtml(c.id)}"
          type="button" ${permanentMark ? "disabled" : ""}
          aria-label="${permanentMark ? "Permanently starred" : marked ? "Unmark" : "Mark"} ${escapeHtml(c.event_id || c.id)}"
          title="${permanentMark ? "Permanently starred in repository" : marked ? "Remove mark" : "Mark interesting"}">${marked ? "★" : "☆"}</button>
        <div class="preview-play" aria-hidden="true">▶</div>
        <div class="preview-overlay">
          <span class="badge ${badgeClass(c.review_class)}">${escapeHtml(prettyClass(c.review_class))}</span>
          <span class="badge-stack">
            ${reviewBadge(c)}
            ${freshBadge}
            <span class="badge">${escapeHtml(c.source || "SOHO")}</span>
          </span>
        </div>
      </div>

      <div class="card-body">
        <div class="card-top">
          <div>
            <h3 class="card-title">${escapeHtml(c.event_id || c.id)}</h3>
            <p class="card-subtitle">${c.archived_from_realtime
              ? `Realtime archive · observed ${escapeHtml(formatDate(c.last_seen))}`
              : `Verified ${escapeHtml(formatDate(c.verified_at))}`}</p>
          </div>
          <div class="confidence">
            ${scoreText(c.score)}
            <small>PIPELINE SCORE</small>
          </div>
        </div>

        <div class="motion-strip">
          <span>${escapeHtml(motion)}</span>
          <small>${escapeHtml(radialityText(c))} radial component</small>
        </div>

        <div class="meta-grid">
          <div class="meta-item"><span>Track frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>
          <div class="meta-item"><span>Verified frames</span><strong>${escapeHtml(c.verified_frames ?? "—")}</strong></div>
          <div class="meta-item"><span>Speed</span><strong>${escapeHtml(metricText(c.speed))} px/h</strong></div>
          <div class="meta-item"><span>Sunward</span><strong>${escapeHtml(metricText(c.sun_distance))} px/h</strong></div>
          <div class="meta-item"><span>Fit RMS</span><strong>${escapeHtml(metricText(c.rms))}</strong></div>
          <div class="meta-item"><span>Instrument</span><strong>${escapeHtml(c.instrument || "LASCO")}</strong></div>
        </div>

        <div class="card-open-hint">Open candidate review <span>→</span></div>
      </div>
    </article>
  `;
}

function filteredCandidates() {
  const q = state.query.trim().toLowerCase();
  let rows = state.candidates.filter(c => {
    const motion = motionClass(c);
    const review = getReview(c);
    const matchesQuery = !q || [
      c.id, c.event_id, c.title, c.family, c.source, c.instrument, c.notes,
      c.review_class, motion, review
    ].some(v => String(v || "").toLowerCase().includes(q));

    const matchesClass = state.reviewClass === "all" ||
      normalizeClass(c.review_class) === state.reviewClass;
    const matchesMotion = state.motion === "all" || motion === state.motion;
    const matchesInstrument = state.instrument === "all" ||
      String(c.instrument || "LASCO") === state.instrument;
    const matchesReview = state.myReview === "all" || review === state.myReview;
    const matchesMarked = !state.markedOnly || isMarked(c);

    return matchesQuery && matchesClass && matchesMotion &&
      matchesInstrument && matchesReview && matchesMarked;
  });

  rows.sort((a, b) => {
    if (state.sort === "score") {
      const av = Number.isFinite(Number(a.score)) ? Number(a.score) : -Infinity;
      const bv = Number.isFinite(Number(b.score)) ? Number(b.score) : -Infinity;
      return bv - av;
    }
    if (state.sort === "frames") return Number(b.frames || 0) - Number(a.frames || 0);
    if (state.sort === "observed") return new Date(b.first_seen || 0) - new Date(a.first_seen || 0);
    return new Date(b.verified_at || 0) - new Date(a.verified_at || 0);
  });

  return rows;
}

function updateMarkedButton() {
  const button = $("#markedOnly");
  button.setAttribute("aria-pressed", state.markedOnly ? "true" : "false");
  button.textContent = state.markedOnly ? "★ Marked only" : "☆ Show marked only";
  button.classList.toggle("active", state.markedOnly);
}

function bindCards() {
  document.querySelectorAll("[data-open-card]").forEach(card => {
    const open = (e) => {
      if (e && e.target.closest("button, select, input, a")) return;
      const candidate = state.candidates.find(c => c.id === card.dataset.openCard);
      if (candidate) openDialog(candidate);
    };
    card.addEventListener("click", open);
    card.addEventListener("keydown", e => {
      if ((e.key === "Enter" || e.key === " ") && !e.target.closest("button, select, input, a")) {
        e.preventDefault();
        open(e);
      }
    });
  });

  document.querySelectorAll("[data-mark]").forEach(button => {
    button.addEventListener("click", e => {
      e.stopPropagation();
      const candidate = state.candidates.find(c => c.id === button.dataset.mark);
      if (!candidate) return;
      setMarked(candidate, !isMarked(candidate));
      render();
    });
  });
}

function render() {
  const rows = filteredCandidates();
  $("#candidateGrid").innerHTML = rows.map(candidateCard).join("");
  const markedCount = state.candidates.filter(isMarked).length;
  $("#visibleCount").textContent = `${rows.length} shown · ${markedCount} marked`;
  $("#emptyState").classList.toggle("hidden", rows.length !== 0);
  updateMarkedButton();
  bindCards();
}

function reportText(c) {
  return [
    "SOHO Comet Hunter candidate review",
    "Event ID: " + (c.event_id || c.id),
    "Instrument: " + (c.instrument || "LASCO"),
    "Pipeline class: " + prettyClass(c.review_class),
    "Pipeline score: " + scoreText(c.score),
    "My review: " + prettyClass(getReview(c)),
    "Known object: " + (c.known_object || "—"),
    "Marked: " + (isMarked(c) ? "yes" : "no"),
    "Trajectory: " + motionClass(c),
    "Speed: " + metricText(c.speed) + " px/h",
    "Sunward component: " + metricText(c.sun_distance) + " px/h",
    "Fit RMS: " + metricText(c.rms),
    "Track frames: " + (c.frames ?? "—"),
    "Verified frames: " + (c.verified_frames ?? "—"),
    "First seen: " + formatDate(c.first_seen),
    "Last seen: " + formatDate(c.last_seen),
    "Verified: " + formatDate(c.verified_at)
  ].join("\n");
}

function openDialog(c) {
  const media = c.video
    ? `<video controls autoplay muted playsinline preload="metadata" ${c.thumbnail ? `poster="${escapeHtml(c.thumbnail)}"` : ""}>
         <source src="${escapeHtml(c.video)}" type="video/mp4">
         Your browser could not play this video.
       </video>`
    : (c.thumbnail ? `<img src="${escapeHtml(c.thumbnail)}" alt="Candidate preview">` : "");

  const familyRow = knownFamily(c)
    ? `<strong>Comet family:</strong> ${escapeHtml(c.family)}<br>`
    : "";

  const reviewOptions = REVIEW_STATES.map(value =>
    `<option value="${value}" ${getReview(c) === value ? "selected" : ""}>${escapeHtml(prettyClass(value))}</option>`
  ).join("");

  const persistenceNote = c.review_locked || c.star_locked
    ? "Known-object status and star are saved in the repository."
    : "Review labels and marks are saved in this browser.";

  $("#dialogBody").innerHTML = `
    <div class="dialog-content">
      <div class="eyebrow">CANDIDATE REVIEW</div>
      <h2>${escapeHtml(c.event_id || c.id)}</h2>
      <p>${escapeHtml(c.notes || "Automatically selected for human review.")}</p>

      <div class="review-toolbar">
        <label>
          <span>${c.review_locked ? "Repository review" : "My review"}</span>
          <select id="dialogReview" ${c.review_locked ? "disabled" : ""}>${reviewOptions}</select>
        </label>
        <button id="dialogMark" type="button" ${c.star_locked ? "disabled" : ""}>${isMarked(c) ? "★ Marked" : "☆ Mark interesting"}</button>
        <button id="copyReport" type="button">Copy candidate report</button>
      </div>
      <small class="local-note">${escapeHtml(persistenceNote)}</small>

      <div class="dialog-info">
        <div><span>Review class</span><strong>${escapeHtml(prettyClass(c.review_class))}</strong></div>
        ${c.known_object ? `<div><span>Known object</span><strong>${escapeHtml(c.known_object)}</strong></div>` : ""}
        <div><span>Pipeline score</span><strong>${scoreText(c.score)}</strong></div>
        <div><span>Trajectory</span><strong>${escapeHtml(motionClass(c))}</strong></div>
        <div><span>Radial component</span><strong>${escapeHtml(radialityText(c))}</strong></div>
        <div><span>Track frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>
        <div><span>Verified frames</span><strong>${escapeHtml(c.verified_frames ?? "—")}</strong></div>
        <div><span>Fit RMS</span><strong>${escapeHtml(metricText(c.rms))}</strong></div>
        <div><span>${c.archived_from_realtime ? "Archived" : "Verified"}</span><strong>${escapeHtml(formatDate(c.verified_at))}</strong></div>
      </div>

      <p>
        <strong>Speed:</strong> ${escapeHtml(metricText(c.speed))} px/h<br>
        <strong>Sunward component:</strong> ${escapeHtml(metricText(c.sun_distance))} px/h<br>
        ${familyRow}
        <strong>First seen:</strong> ${escapeHtml(formatDate(c.first_seen))}<br>
        <strong>Last seen:</strong> ${escapeHtml(formatDate(c.last_seen))}<br>
        <strong>Source:</strong> ${escapeHtml(c.source || "SOHO")} · ${escapeHtml(c.instrument || "LASCO")}<br>
        <strong>Video:</strong> ${escapeHtml(prettyVideoVariant(c.video_variant))}
      </p>

      <div class="science-note">
        Trajectory labels are descriptive classifications from measured image-plane motion.
        A named comet family is shown only when the pipeline has an actual family classification.
      </div>

      ${media}
    </div>
  `;

  $("#dialogReview").addEventListener("change", e => {
    setReview(c, e.target.value);
    render();
  });

  $("#dialogMark").addEventListener("click", () => {
    setMarked(c, !isMarked(c));
    $("#dialogMark").textContent = isMarked(c) ? "★ Marked" : "☆ Mark interesting";
    render();
  });

  $("#copyReport").addEventListener("click", async () => {
    const button = $("#copyReport");
    try {
      await navigator.clipboard.writeText(reportText(c));
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = "Copy candidate report"; }, 1200);
    } catch {
      button.textContent = "Copy failed";
    }
  });

  $("#candidateDialog").showModal();
}


function compactFrameName(value) {
  const text = String(value || "");
  return text.replace(/_c3_512\.jpg$/i, "");
}

function frameDisplay(value) {
  const raw = compactFrameName(value);
  const m = raw.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})$/);
  if (!m) return raw || "—";
  return `${raw}<small>${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]} UTC</small>`;
}

function realtimeStatusClass(payload) {
  if (!payload || !payload.available) return "waiting";
  const updated = new Date(payload.hunter_heartbeat_at || payload.source_updated_at || 0).getTime();
  if (!Number.isFinite(updated) || updated <= 0) return "waiting";
  const ageMinutes = (Date.now() - updated) / 60000;
  return ageMinutes <= 45 ? "live" : "stale";
}

function realtimeCandidateCard(c) {
  const status = String(c.status || "UNKNOWN").toUpperCase();
  const media = c.review_video
    ? `<div class="realtime-review-media">
        <video controls muted loop playsinline preload="metadata" ${c.review_thumbnail ? `poster="${escapeHtml(c.review_thumbnail)}"` : ""}>
          <source src="${escapeHtml(c.review_video)}" type="video/mp4">
        </video>
        <div class="realtime-review-links">
          <a href="${escapeHtml(c.review_video)}" target="_blank" rel="noopener">Annotated zoom</a>
          ${c.review_raw_video ? `<a href="${escapeHtml(c.review_raw_video)}" target="_blank" rel="noopener">Raw zoom</a>` : ""}
          ${c.review_full_video ? `<a href="${escapeHtml(c.review_full_video)}" target="_blank" rel="noopener">Full frame</a>` : ""}
        </div>
      </div>`
    : "";
  const statusClass = status === "UNMATCHED"
    ? "live-unmatched"
    : status === "KNOWN_REPORT"
      ? "live-known"
      : "live-seen";

  return `
    <article class="realtime-candidate ${statusClass}">
      <div class="realtime-candidate-top">
        <strong>${escapeHtml(c.candidate || "Candidate")}</strong>
        <span>${escapeHtml(prettyClass(status))}</span>
      </div>
      ${media}
      <div class="realtime-candidate-meta">
        <div><span>Frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>
        <div><span>Speed</span><strong>${escapeHtml(metricText(c.speed))} px/h</strong></div>
        <div><span>Fit RMS</span><strong>${escapeHtml(metricText(c.rms))}</strong></div>
        <div><span>Sunward</span><strong>${escapeHtml(metricText(c.sunward))} px/h</strong></div>
      </div>
      <div class="realtime-track">
        <span>${escapeHtml(compactFrameName(c.first))}</span>
        <b>→</b>
        <span>${escapeHtml(compactFrameName(c.last))}</span>
      </div>
      ${c.report_id ? `<small>Matched report: ${escapeHtml(c.report_id)}</small>` : ""}
    </article>
  `;
}

function renderRealtime(payload) {
  state.realtime = payload || null;

  const stateEl = $("#realtimeState");
  const lastFrameEl = $("#realtimeLastFrame");
  const alertCountEl = $("#realtimeAlertCount");
  const knownCountEl = $("#realtimeKnownCount");
  const updatedEl = $("#realtimeUpdated");
  const frameWrap = $("#realtimeFrameWrap");
  const frameImage = $("#realtimeFrameImage");
  const frameCaption = $("#realtimeFrameCaption");
  const alertBox = $("#realtimeAlertBox");
  const candidatesEl = $("#realtimeCandidates");

  if (!stateEl || !lastFrameEl || !alertCountEl || !knownCountEl || !updatedEl ||
      !frameWrap || !frameImage || !frameCaption || !alertBox || !candidatesEl) {
    return;
  }

  if (!payload || !payload.available) {
    stateEl.className = "realtime-state waiting";
    stateEl.innerHTML = "<span></span> WAITING FOR LIVE DATA";
    lastFrameEl.textContent = "—";
    alertCountEl.textContent = "0";
    knownCountEl.textContent = "0";
    updatedEl.textContent = "—";
    frameWrap.classList.add("hidden");
    frameImage.removeAttribute("src");
    frameCaption.textContent = "—";
    alertBox.classList.add("hidden");
    candidatesEl.innerHTML = '<div class="realtime-empty">Realtime hunter has not published data yet.</div>';
    return;
  }

  const cls = realtimeStatusClass(payload);
  stateEl.className = "realtime-state " + cls;
  stateEl.innerHTML = "<span></span> " + (cls === "live" ? "LIVE" : "STALE / CHECK HUNTER");

  lastFrameEl.innerHTML = frameDisplay(payload.last_frame);
  alertCountEl.textContent = String(payload.alerts_total ?? 0);
  knownCountEl.textContent = String(payload.known_matches_total ?? 0);
  updatedEl.textContent = formatDate(payload.source_updated_at);

  if (payload.latest_frame_image) {
    frameImage.src = payload.latest_frame_image;
    frameImage.alt = `LASCO C3 frame ${compactFrameName(payload.last_frame)} processed by the realtime detector`;
    frameCaption.innerHTML = frameDisplay(payload.last_frame);
    frameWrap.classList.remove("hidden");
  } else {
    frameWrap.classList.add("hidden");
    frameImage.removeAttribute("src");
    frameCaption.textContent = "—";
  }

  const latestAlert = String(payload.latest_alert || "").trim();
  if (latestAlert) {
    alertBox.classList.remove("hidden");
    alertBox.innerHTML =
      '<strong>⚠ Unmatched realtime candidate requires review</strong>' +
      '<pre>' + escapeHtml(latestAlert) + '</pre>';
  } else {
    alertBox.classList.add("hidden");
    alertBox.innerHTML = "";
  }

  const rows = Array.isArray(payload.latest_candidates) ? payload.latest_candidates : [];
  if (!rows.length) {
    candidatesEl.innerHTML = '<div class="realtime-empty">No comet-like realtime tracks in the latest detector pass.</div>';
    return;
  }

  const ordered = [...rows].sort((a, b) => {
    const rank = { UNMATCHED: 0, KNOWN_REPORT: 1, SEEN_ALREADY: 2 };
    return (rank[String(a.status || "").toUpperCase()] ?? 9) -
      (rank[String(b.status || "").toUpperCase()] ?? 9);
  });

  candidatesEl.innerHTML = ordered.slice(0, 8).map(realtimeCandidateCard).join("");
}

async function refreshRealtime() {
  try {
    const response = await fetch(`./data/realtime.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return;
    renderRealtime(await response.json());
  } catch (error) {
    console.warn("Realtime refresh failed:", error);
  }
}

async function loadData() {
  try {
    const cacheKey = Date.now();
    const [candidateResponse, statusResponse, realtimeResponse] = await Promise.all([
      fetch(`./data/candidates.json?t=${cacheKey}`, { cache: "no-store" }),
      fetch(`./data/status.json?t=${cacheKey}`, { cache: "no-store" }),
      fetch(`./data/realtime.json?t=${cacheKey}`, { cache: "no-store" })
    ]);

    if (!candidateResponse.ok) throw new Error("Could not load candidate data");

    const payload = await candidateResponse.json();
    const status = statusResponse.ok ? await statusResponse.json() : {};
    const realtime = realtimeResponse.ok
      ? await realtimeResponse.json()
      : { available: false };
    state.candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
    renderRealtime(realtime);

    $("#demoBanner").classList.toggle("hidden", !payload.demo);
    $("#candidateCount").textContent = state.candidates.length;
    $("#strongCount").textContent =
      state.candidates.filter(c => normalizeClass(c.review_class) === "STRONG_REVIEW").length;
    $("#secondaryCount").textContent =
      state.candidates.filter(c => normalizeClass(c.review_class) === "SECONDARY").length;
    $("#lastUpdated").textContent = formatDate(payload.generated_at || status.generated_at);

    const classes = [...new Set(state.candidates.map(c => normalizeClass(c.review_class)))].sort();
    $("#classFilter").innerHTML =
      `<option value="all">All classes</option>` +
      classes.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(prettyClass(c))}</option>`).join("");

    const motions = [...new Set(state.candidates.map(motionClass))].sort((a, b) => a.localeCompare(b));
    $("#motionFilter").innerHTML =
      `<option value="all">All trajectories</option>` +
      motions.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");

    const instruments = [...new Set(state.candidates.map(c => String(c.instrument || "LASCO")))]
      .sort((a, b) => a.localeCompare(b));
    $("#instrumentFilter").innerHTML =
      `<option value="all">All instruments</option>` +
      instruments.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

    render();
  } catch (error) {
    $("#candidateGrid").innerHTML = `
      <section class="empty panel">
        <div class="empty-icon">!</div>
        <h3>Dashboard data could not be loaded</h3>
        <p>${escapeHtml(error.message)}</p>
      </section>
    `;
  }
}

$("#searchInput").addEventListener("input", e => { state.query = e.target.value; render(); });
$("#classFilter").addEventListener("change", e => { state.reviewClass = e.target.value; render(); });
$("#motionFilter").addEventListener("change", e => { state.motion = e.target.value; render(); });
$("#instrumentFilter").addEventListener("change", e => { state.instrument = e.target.value; render(); });
$("#reviewFilter").addEventListener("change", e => { state.myReview = e.target.value; render(); });
$("#sortSelect").addEventListener("change", e => { state.sort = e.target.value; render(); });
$("#markedOnly").addEventListener("click", () => { state.markedOnly = !state.markedOnly; render(); });
$("#dialogClose").addEventListener("click", () => $("#candidateDialog").close());
$("#candidateDialog").addEventListener("click", e => {
  if (e.target === $("#candidateDialog")) $("#candidateDialog").close();
});

loadData();
setInterval(refreshRealtime, 60 * 1000);
