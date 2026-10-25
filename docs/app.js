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
  sort: "verified",
  liveReviewVisible: 8,
  showLowSupportRealtime: false,
  showSeenAlreadyRealtime: false,
  githubLiveEmailIssues: [],
  githubEmailLastRequested: 0,
  githubEmailFetchPending: false
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

function realtimeFrameUtc(value) {
  const match = String(value || "").match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_c3_(?:512|1024)\.jpg$/i);
  if (!match) return "—";
  return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]} UTC`;
}
function realtimeStatusClass(payload) {
  if (!payload || !payload.available) return "waiting";
  const updated = new Date(payload.hunter_heartbeat_at || payload.source_updated_at || 0).getTime();
  if (!Number.isFinite(updated) || updated <= 0) return "waiting";
  const ageMinutes = (Date.now() - updated) / 60000;
  return ageMinutes <= 45 ? "live" : "stale";
}

function realtimeCandidateCard(c, emailIssue = null) {
  const status = String(c.status || "UNKNOWN").toUpperCase();
  let media = "";
  if (c.review_video) {
    media = `<div class="realtime-review-media">
        <video controls muted loop playsinline preload="metadata" ${c.review_thumbnail ? `poster="${escapeHtml(c.review_thumbnail)}"` : ""}>
          <source src="${escapeHtml(c.review_video)}" type="video/mp4">
        </video>
        <div class="realtime-review-links">
          <a href="${escapeHtml(c.review_video)}" target="_blank" rel="noopener">Annotated zoom</a>
          ${c.review_raw_video ? `<a href="${escapeHtml(c.review_raw_video)}" target="_blank" rel="noopener">Raw zoom</a>` : ""}
          ${c.review_full_video ? `<a href="${escapeHtml(c.review_full_video)}" target="_blank" rel="noopener">Full frame</a>` : ""}
        </div>
      </div>`;
  } else if (c.review_thumbnail) {
    media = `<div class="realtime-review-media">
        <a href="${escapeHtml(c.review_thumbnail)}" target="_blank" rel="noopener">
          <img src="${escapeHtml(c.review_thumbnail)}" alt="Realtime candidate contact sheet for ${escapeHtml(c.candidate || "candidate")}" loading="lazy">
        </a>
        <div class="realtime-review-links">
          <a href="${escapeHtml(c.review_thumbnail)}" target="_blank" rel="noopener">Open review image</a>
        </div>
      </div>`;
  }
  if (!media) {
    media = '<div class="realtime-media-missing">Review animation unavailable. Inspect original SOHO images; this candidate has not been visually verified.</div>';
  }
  const trajectoryWarning = c.trajectory_warning
    ? '<div class="realtime-media-missing">Trajectory warning: ' + escapeHtml(prettyClass(c.trajectory_warning)) + '. Motion fit requires independent verification.</div>'
    : '';
  const statusClass = status === "UNMATCHED"
    ? "live-unmatched"
    : status === "KNOWN_REPORT"
      ? "live-known"
      : "live-seen";

  const emailMarker = emailIssue
    ? '<div class="c3-email-alert-label">✉ GitHub email alert · ' +
        escapeHtml(formatDate(emailIssue.created_at)) + ' · <a href="' +
        escapeHtml(emailIssue.issue_url) +
        '" target="_blank" rel="noopener">View GitHub issue #' +
        escapeHtml(emailIssue.issue_number) + '</a></div>'
    : '';
  return `
    <article id="c3-review-${escapeHtml(c.review_id || `${c.candidate}-${c.first}`)}" class="realtime-candidate ${statusClass} ${emailIssue ? "c3-email-alerted" : ""}">${emailMarker}
      <div class="realtime-candidate-top">
        <strong>${escapeHtml(c.candidate || "Candidate")}</strong>
        <span>${escapeHtml(prettyClass(status))}</span>
      </div>
      
      <div class="realtime-observed-times">
        <span>First seen <strong>${escapeHtml(realtimeFrameUtc(c.first))}</strong></span>
        <span>Last seen <strong>${escapeHtml(realtimeFrameUtc(c.last))}</strong></span>
      </div>
      ${media}
      ${trajectoryWarning}
      ${(c.quality_issues || []).includes("FRAME_COUNT_EXCEEDS_SOURCE_IMAGES") ? '<div class="realtime-media-missing">Reported track frames exceed distinct available source images. This event is not validated as a comet.</div>' : ''}
      ${c.pixel_verification?.endpoint_model ? '<small>Alternate endpoint-path pixel check: ' + escapeHtml(c.pixel_verification.endpoint_model.status || "UNAVAILABLE") + ' (' + escapeHtml(String(c.pixel_verification.endpoint_model.hits ?? 0)) + '/' + escapeHtml(String(c.pixel_verification.endpoint_model.samples ?? 0)) + ' hits). Independent observation review required.</small>' : ''}
      <div class="realtime-candidate-meta">\n        <div><span>Review class</span><strong>${escapeHtml(prettyClass(c.review_class || "UNCLASSIFIED"))}</strong></div>\n        <div><span>Frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>\n        <div><span>Speed</span><strong>${escapeHtml(metricText(c.speed))} px/h</strong></div>\n        <div><span>Fit RMS</span><strong>${escapeHtml(metricText(c.rms))}</strong></div>\n        <div><span>Sunward</span><strong>${escapeHtml(metricText(c.sunward))} px/h</strong></div>\n        <div><span>Pixel hits</span><strong>${escapeHtml((c.pixel_verification?.hits ?? "—") + "/" + (c.pixel_verification?.samples ?? "—"))}</strong></div>\n        <div><span>Peak SNR</span><strong>${escapeHtml(metricText(c.pixel_verification?.peak_snr))}</strong></div>\n        <div><span>Pixel status</span><strong>${escapeHtml(c.pixel_verification?.status || "—")}</strong></div>\n      </div>\n      <div class="realtime-track">
        <span>${escapeHtml(compactFrameName(c.first))}</span>
        <b>→</b>
        <span>${escapeHtml(compactFrameName(c.last))}</span>
      </div>
      ${c.report_id ? `<small>Matched report: ${escapeHtml(c.report_id)}</small>` : ""}
    </article>
  `;
}

// Weak exploratory tracks are retained for engineering review, but must not
// crowd out events with pixel support or a known-report match. This is a
// DISPLAY decision only: all detections and clips remain in the raw archive.
function isRealtimeDiagnosticOnly(row) {
  const cls = normalizeClass(row?.review_class);
  const status = normalizeClass(row?.status);
  return (cls === "LOW_SUPPORT" || cls === "VISUAL_REJECT") && status !== "KNOWN_REPORT";
}

function realtimeReviewGroups(rows) {
  const diagnostics = [];
  const primary = [];
  for (const row of rows) {
    if (isRealtimeDiagnosticOnly(row)) diagnostics.push(row);
    else primary.push(row);
  }
  return { primary, diagnostics };
}

// SEEN_ALREADY is a probable repeat from position/motion matching, not proof
// that two reviews are exactly the same object. Hide only in the browser;
// preserve every published review and its videos for optional inspection.
function visiblePrimaryReviews(rows, showPreviouslySeen) {
  return showPreviouslySeen
    ? rows
    : rows.filter(row => normalizeClass(row.status) !== "SEEN_ALREADY");
}

function attachPreviouslySeenToggle(payload) {
  const toggle = $("#toggleSeenRealtime");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    state.showSeenAlreadyRealtime = !state.showSeenAlreadyRealtime;
    state.liveReviewVisible = 8;
    renderRealtime(payload);
  });
}

function realtimeFrameKey(value) {
  // SOHO frames are named YYYYMMDD_HHMM_c3_1024.jpg or ..._512.jpg.
  // Their UTC timestamps sort correctly as strings; malformed dates go last.
  const match = String(value || "").match(/^(\d{8}_\d{4})_c3_(?:512|1024)\.jpg$/i);
  return match ? match[1] : "";
}

function sortRealtimeReviews(rows) {
  const statusRank = { UNMATCHED: 0, KNOWN_REPORT: 1, SEEN_ALREADY: 2 };
  return [...rows].sort((a, b) =>
    realtimeFrameKey(b.last).localeCompare(realtimeFrameKey(a.last)) ||
    realtimeFrameKey(b.first).localeCompare(realtimeFrameKey(a.first)) ||
    (statusRank[String(a.status || "").toUpperCase()] ?? 9) -
      (statusRank[String(b.status || "").toUpperCase()] ?? 9)
  );
}

// GitHub Actions creates assigned issues that trigger the user's C3 emails.
// Use the exact review_id (not a run-local C-number) as the primary identity.
// Fetch the latest public GitHub issue metadata independently of the 15-minute
// dashboard publisher. The issue created by GitHub Actions is what triggers
// the user's email; detector alerts.tsv is NOT the email notification ledger.
const GITHUB_C3_ISSUES_API = "https://api.github.com/repos/greatn8/soho-comet-hunter/issues?state=all&sort=created&direction=desc&per_page=100";
const GITHUB_C3_MARKER = "<!-- SOHO_STRONG_REVIEW_ALERT ";
const GITHUB_C3_REFRESH_MS = 5 * 60 * 1000;

function parseC3GitHubAlertIssues(issues) {
  if (!Array.isArray(issues)) return [];
  const accepted = [];
  for (const issue of issues) {
    if (!issue || issue.pull_request || typeof issue.body !== "string" ||
        !issue.body.startsWith(GITHUB_C3_MARKER)) continue;
    const first = issue.body.split("\n", 1)[0];
    if (!first.endsWith(" -->")) continue;
    let data;
    try {
      data = JSON.parse(first.slice(GITHUB_C3_MARKER.length, -4));
    } catch { continue; }
    const number = Number(issue.number);
    const candidate = String(data?.candidate || "").toUpperCase();
    const reviewId = String(data?.review_id || "");
    const start = String(data?.first || "");
    const end = String(data?.last || "");
    const timestamp = String(issue.created_at || "");
    if (!Number.isSafeInteger(number) || number <= 0 ||
        !/^C\d+$/.test(candidate) ||
        !/^RT\d{8}T\d{4}_[A-Za-z0-9]+$/.test(reviewId) ||
        !/^20\d{6}_\d{4}_c3_(?:512|1024)\.jpg$/i.test(start) ||
        !/^20\d{6}_\d{4}_c3_(?:512|1024)\.jpg$/i.test(end) ||
        !Number.isFinite(Date.parse(timestamp))) continue;
    accepted.push({
      issue_number: number,
      issue_url: "https://github.com/greatn8/soho-comet-hunter/issues/" + number,
      created_at: timestamp,
      candidate,
      review_id: reviewId,
      first: start,
      last: end
    });
  }
  return accepted.sort((a, b) =>
    b.created_at.localeCompare(a.created_at) || b.issue_number - a.issue_number);
}

function currentC3GitHubAlerts(payload) {
  const published = Array.isArray(payload?.github_email_alert_issues)
    ? payload.github_email_alert_issues : [];
  const all = [...state.githubLiveEmailIssues, ...published];
  const seen = new Set();
  return all.filter(issue => {
    const key = String(issue.review_id || "") ||
      [issue.candidate, issue.first, issue.last].join("|");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")) ||
      Number(b.issue_number || 0) - Number(a.issue_number || 0));
}

function githubEmailForReview(row, issues) {
  const id = String(row?.review_id || "");
  const candidate = String(row?.candidate || "").toUpperCase();
  const first = String(row?.first || "");
  const last = String(row?.last || "");
  return (issues || []).find(issue =>
    id && issue.review_id
      ? id === String(issue.review_id)
      : candidate === String(issue.candidate || "").toUpperCase() &&
        first === String(issue.first || "") && last === String(issue.last || "")
  ) || null;
}

function sortEmailAlertReviews(rows, issues) {
  return sortRealtimeReviews(rows).sort((a, b) => {
    const ea = githubEmailForReview(a, issues);
    const eb = githubEmailForReview(b, issues);
    if (!!ea !== !!eb) return ea ? -1 : 1;
    if (ea && eb) return String(eb.created_at).localeCompare(String(ea.created_at)) ||
      Number(eb.issue_number) - Number(ea.issue_number);
    return 0;
  });
}

async function refreshGithubC3EmailIssues() {
  const now = Date.now();
  if (state.githubEmailFetchPending ||
      now - state.githubEmailLastRequested < GITHUB_C3_REFRESH_MS) return;
  state.githubEmailLastRequested = now;
  state.githubEmailFetchPending = true;
  try {
    const response = await fetch(GITHUB_C3_ISSUES_API, {
      cache: "no-store", headers: {Accept:"application/vnd.github+json"}
    });
    if (!response.ok) throw new Error("GitHub API HTTP " + response.status);
    const raw = await response.json();
    if (!Array.isArray(raw)) throw new Error("Unexpected GitHub issues response");
    state.githubLiveEmailIssues = parseC3GitHubAlertIssues(raw);
    if (state.realtime?.available) {
      renderRealtime(state.realtime);
      if (c3HashTarget()) c3TryHashTarget(state.realtime);
    }
  } catch (error) {
    // Public GitHub API can occasionally be rate-limited. Retain the
    // publisher snapshot and allow direct candidate-ID / review-ID lookup.
    console.warn("Latest GitHub C3 email alerts unavailable:", error);
  } finally {
    state.githubEmailFetchPending = false;
  }
}

function c3ReviewKey(row) {
  return String(row?.review_id || "") ||
    [row?.candidate, row?.first, row?.last].map(x => String(x || "")).join("|");
}
function c3FindReview(payload, identifier) {
  const rows = payload?.review_candidates?.length ? payload.review_candidates : (payload?.latest_candidates || []);
  const query = String(identifier || "").trim().toUpperCase();
  return rows.find(row => String(row.review_id || "").toUpperCase() === query) ||
    rows.find(row => String(row.candidate || "").toUpperCase() === query);
}
function c3OpenReview(identifier, payload = state.realtime) {
  const row = c3FindReview(payload, identifier);
  const status = $("#realtimeFindStatus");
  if (!row) {
    if (status) status.textContent = "No retained review for " + identifier +
      ". It may be awaiting publication or older than the live retention window. Check the GitHub alert issue.";
    return false;
  }
  state.liveReviewVisible = 1000;
  renderRealtime(payload);
  const id = "c3-review-" + (row.review_id || row.candidate + "-" + row.first);
  const target = document.getElementById(id);
  if (target) {
    target.scrollIntoView({behavior:"smooth",block:"start"});
    target.classList.add("c3-jump-highlight");
    if (status) status.textContent = "Showing " + row.candidate + " (" + (row.review_id || "no review ID") + ").";
    return true;
  }
  if (status) status.textContent = "Candidate exists but its review card could not be displayed.";
  return false;
}
function c3HashTarget() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return params.get("c3");
}
function c3TryHashTarget(payload) {
  const target = c3HashTarget();
  if (target && !c3OpenReview(target,payload)) {
    $("#realtimePanel")?.scrollIntoView({behavior:"auto",block:"start"});
  }
}
function renderRealtime(payload) {
  state.realtime = payload || null;

  const stateEl = $("#realtimeState");
  const lastFrameEl = $("#realtimeLastFrame");
  const alertCountEl = $("#realtimeAlertCount");
  const knownCountEl = $("#realtimeKnownCount");
  const updatedEl = $("#realtimeUpdated");
  const sourceNoteEl = $("#realtimeSourceNote");
  const frameWrap = $("#realtimeFrameWrap");
  const frameImage = $("#realtimeFrameImage");
  const frameCaption = $("#realtimeFrameCaption");
  const alertBox = $("#realtimeAlertBox");
  const candidatesEl = $("#realtimeCandidates");

  if (!stateEl || !lastFrameEl || !alertCountEl || !knownCountEl || !updatedEl ||
      !frameWrap || !frameImage || !frameCaption || !alertBox || !candidatesEl || !sourceNoteEl) {
    return;
  }

  if (!payload || !payload.available) {
    stateEl.className = "realtime-state waiting";
    stateEl.innerHTML = "<span></span> WAITING FOR LIVE DATA";
    lastFrameEl.textContent = "—";
    alertCountEl.textContent = "0";
    knownCountEl.textContent = "0";
    updatedEl.textContent = "—";
    sourceNoteEl.textContent = "Input source: —";
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

  lastFrameEl.innerHTML =
    frameDisplay(payload.last_frame) +
    (Number(payload.hunter_new_frames ?? 0) === 0
      ? '<small>No newer SOHO C3 frame at last check</small>'
      : `<small>${escapeHtml(payload.hunter_new_frames)} new SOHO frame(s) this check</small>`);
  // The previous counter included unmatched LOW_SUPPORT artifacts, so it
  // regularly displayed a misleading positive count with zero useful tracks.
  const latestRows = Array.isArray(payload.latest_candidates) ? payload.latest_candidates : [];
  alertCountEl.textContent = String(realtimeReviewGroups(latestRows).primary.length);
  knownCountEl.textContent = String(
    payload.current_known_total ??
    (Array.isArray(payload.latest_candidates)
      ? payload.latest_candidates.filter(c => String(c.status || "").toUpperCase() === "KNOWN_REPORT").length
      : 0)
  );
  updatedEl.textContent = formatDate(payload.hunter_heartbeat_at || payload.source_updated_at);

  const activeSource = String(payload.active_input_source || "").toLowerCase();
  if (activeSource === "near_realtime") {
    const lag = Number(payload.completed_lag_minutes);
    sourceNoteEl.innerHTML =
      '<strong>Input source: NEAR REALTIME</strong>' +
      '<span>Preliminary SOHO quicklook frames used for discovery' +
      (Number.isFinite(lag) && lag > 0 ? ` · completed archive lag ~${Math.round(lag)} min` : "") +
      '</span>';
    sourceNoteEl.className = "realtime-source-note preliminary";
  } else if (activeSource === "completed") {
    sourceNoteEl.innerHTML =
      '<strong>Input source: COMPLETED / REPROCESSED</strong>' +
      '<span>Science-quality SOHO archive frames</span>';
    sourceNoteEl.className = "realtime-source-note completed";
  } else {
    sourceNoteEl.textContent = "Input source: awaiting source status";
    sourceNoteEl.className = "realtime-source-note";
  }

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

  const currentRows = Array.isArray(payload.latest_candidates) ? payload.latest_candidates : [];
  const reviewRows = Array.isArray(payload.review_candidates) && payload.review_candidates.length
    ? payload.review_candidates
    : currentRows;
  const { primary, diagnostics } = realtimeReviewGroups(reviewRows);
  const githubIssues = currentC3GitHubAlerts(payload);
  // Keep an alerted candidate accessible even if an older classification
  // would otherwise put it in the diagnostics or previously-seen filter.
  // A C3 candidate number may be reused; for email prioritization require
  // the exact issue review ID. The most recent email issue gets first place.
  const latestAlertId = String(latestAlert.match(/(?:^|\n)Candidate:\s*(C\d+)/i)?.[1] || "").toUpperCase();
  const latestAlertRow = reviewRows.find(row =>
    String(row.candidate || "").toUpperCase() === latestAlertId &&
    String(row.review_class || "").toUpperCase() === "STRONG_REVIEW");
  const emailRows = reviewRows.filter(row => githubEmailForReview(row, githubIssues));
  const quickNav = $("#realtimeEmailQuickNav");
  if (quickNav) {
    // The earlier list silently truncated to eight alerts. A batch of nine
    // GitHub Actions issues therefore hid the oldest issue in that batch
    // (e.g. C0120469, issue #924) even though its review was published.
    // Render ALL recently issued alert identities as compact jump controls.
    // They are inexpensive links, not video cards or detector alerts.
    const cutoff = Date.now() - 48 * 60 * 60 * 1000;
    const recentIssues = githubIssues.filter(issue => {
      const timestamp = Date.parse(issue.created_at || "");
      return Number.isFinite(timestamp) && timestamp >= cutoff;
    });
    const matchedCount = recentIssues.filter(issue =>
      reviewRows.some(row => githubEmailForReview(row, [issue]))).length;
    quickNav.classList.toggle("hidden", !recentIssues.length);
    quickNav.innerHTML = recentIssues.length
      ? '<strong>Recent C3 email-alert reviews (' + matchedCount +
          ' ready / ' + recentIssues.length + ' GitHub issues)</strong>' +
        '<small>All alerts from the last 48 hours, newest issue first. ' +
          'Select a candidate to jump to its video. Issues without retained ' +
          'review media link to GitHub instead.</small>' +
        '<div class="c3-email-chip-row">' +
        recentIssues.map(issue => {
          const row = reviewRows.find(candidate =>
            githubEmailForReview(candidate, [issue]));
          const label = escapeHtml(issue.candidate || "Unknown");
          const title = escapeHtml("Issue #" + issue.issue_number +
            " · " + (issue.created_at || ""));
          return row
            ? '<button type="button" class="c3-email-chip" data-c3-jump="' +
              escapeHtml(row.review_id || row.candidate) +
              '" title="' + title + '">' + label + '</button>'
            : '<a class="c3-email-chip c3-email-issue-only" href="' +
              escapeHtml(issue.issue_url) +
              '" target="_blank" rel="noopener noreferrer" title="' + title +
              ' · Review not in retained dashboard data">' + label + ' ↗</a>';
        }).join("") + '</div>'
      : "";
    quickNav.querySelectorAll("[data-c3-jump]").forEach(button =>
      button.addEventListener("click", () =>
        c3OpenReview(button.dataset.c3Jump, payload)));
  }

  const seenCount = primary.filter(c => normalizeClass(c.status) === "SEEN_ALREADY").length;
  const visiblePrimary = visiblePrimaryReviews(primary, state.showSeenAlreadyRealtime);
  // Never leave an active discovery dashboard visually empty when the only
  // results are unverified. Show a *small* preview of CURRENT weak tracks,
  // clearly labelled, while retaining the full diagnostic toggle. Do not
  // promote them to alerts or mix them into the permanent archive.
  const currentDiagnosticPreviews = !state.showLowSupportRealtime && !primary.length
    ? currentRows.filter(isRealtimeDiagnosticOnly).slice(0, 3)
    : [];
  const baseRows = state.showLowSupportRealtime
    ? [...visiblePrimary, ...diagnostics]
    : [...visiblePrimary, ...currentDiagnosticPreviews];
  const rowsToDisplay = [...emailRows, ...(latestAlertRow ? [latestAlertRow] : []), ...baseRows.filter(row =>
    !emailRows.some(alerted => (alerted.review_id && alerted.review_id === row.review_id) ||
      (!alerted.review_id && alerted.candidate === row.candidate &&
       alerted.first === row.first && alerted.last === row.last)))];
  const diagnosticNotice = currentDiagnosticPreviews.length
    ? '<div class="realtime-empty"><strong>No evidence-supported candidates in the published review set.</strong> Showing ' +
      currentDiagnosticPreviews.length +
      ' recent LOW SUPPORT track preview(s) below for manual inspection. These are not verified comets or discovery alerts.</div>'
    : '';

  if (!reviewRows.length) {
    candidatesEl.innerHTML = '<div class="realtime-empty">No retained realtime review candidates yet.</div>';
    return;
  }

  const seenControls = seenCount ? `<div class="realtime-review-more">
    <small>${seenCount} previously seen review(s) retained. These matches are approximate and may contain useful follow-up observations.</small>
    <button type="button" id="toggleSeenRealtime">${state.showSeenAlreadyRealtime
      ? "Hide previously seen reviews"
      : `Show ${seenCount} previously seen reviews`}</button>
  </div>` : "";

  const diagnosticControls = diagnostics.length ? `<div class="realtime-review-more">
    <small>${diagnostics.length} low-support / inconsistent track(s) retained for engineering diagnostics, not validated comets.</small>
    <button type="button" id="toggleWeakRealtime">
      ${state.showLowSupportRealtime ? "Hide weak diagnostic tracks" : `Show ${diagnostics.length} weak diagnostic tracks`}
    </button>
  </div>` : "";

  if (!rowsToDisplay.length) {
    candidatesEl.innerHTML = '<div class="realtime-empty">' +
      (seenCount && !state.showSeenAlreadyRealtime
        ? "No new unmatched reviews in this retained set. Previously seen reviews are hidden below."
        : "No evidence-supported realtime candidates yet. The detector is running, but its current tracks have not passed review.") +
      '</div>' + seenControls + diagnosticControls;
    attachPreviouslySeenToggle(payload);
    const toggle = $("#toggleWeakRealtime");
    if (toggle) toggle.addEventListener("click", () => {
      state.showLowSupportRealtime = !state.showLowSupportRealtime;
      state.liveReviewVisible = 8;
      renderRealtime(payload);
    });
    return;
  }

  // Keep newest-first ordering among the reviews currently shown.
  // Repeated detections can be revealed without changing stored reviews.
  const ordered = sortEmailAlertReviews([...new Map(rowsToDisplay.map(row => [c3ReviewKey(row),row])).values()], githubIssues);
  // Fallback when the GitHub issue has not yet appeared. Never override a
  // newer, positively identified email notification with an older text alert.
  if (latestAlertRow && !emailRows.length)
    ordered.sort((a,b) => (c3ReviewKey(a) === c3ReviewKey(latestAlertRow) ? -1 : c3ReviewKey(b) === c3ReviewKey(latestAlertRow) ? 1 : 0));

  const visible = Math.min(state.liveReviewVisible || 8, ordered.length);
  const more = visible < ordered.length
    ? `<div class="realtime-review-more"><button type="button" id="showMoreRealtimeReviews">Show more reviews (${ordered.length - visible} remaining)</button></div>`
    : "";

  candidatesEl.innerHTML =
    diagnosticNotice + seenControls +
    ordered.slice(0, visible).map(row => realtimeCandidateCard(row, githubEmailForReview(row, githubIssues))).join("") +
    more + diagnosticControls;

  attachPreviouslySeenToggle(payload);
  const toggle = $("#toggleWeakRealtime");
  if (toggle) toggle.addEventListener("click", () => {
    state.showLowSupportRealtime = !state.showLowSupportRealtime;
    state.liveReviewVisible = 8;
    renderRealtime(payload);
  });
  const moreButton = $("#showMoreRealtimeReviews");
  if (moreButton) {
    moreButton.addEventListener("click", () => {
      state.liveReviewVisible = Math.min((state.liveReviewVisible || 8) + 8, ordered.length);
      renderRealtime(payload);
    });
  }
}

async function refreshRealtime() {
  try {
    const response = await fetch(`./data/realtime.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderRealtime(payload);
    if (c3HashTarget()) c3TryHashTarget(payload);
    refreshGithubC3EmailIssues();
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
    if (c3HashTarget()) c3TryHashTarget(realtime);
    refreshGithubC3EmailIssues();

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


// NOAA CCOR visual review is independent of the existing C3 workflow.
let ccorPayload = null;
let ccorCurrent = null;
let ccorFrameIndex = 0;
let ccorPlayback = null;
let ccorShowIntermediateFrames = false;
let ccorCamera = "all";
let ccorSort = "newest";
let ccorVisibleTracks = [];

function ccorFrameEntries(track, includeIntermediate = ccorShowIntermediateFrames) {
  const camera = track.camera;
  const manifest = ccorPayload?.frame_manifests?.[camera] || [];
  const observations = (track.points || []).filter(p => p && p.time);
  const byTime = new Map(observations.map(p => [p.time, p]));
  const first = Date.parse(track.first);
  const last = Date.parse(track.last);
  // Include intermediate frames to distinguish a moving object from stars.
  const available = manifest.filter(f => f.image_url && Number.isFinite(Date.parse(f.time)) &&
    Date.parse(f.time) >= first - 15 * 60000 &&
    Date.parse(f.time) <= last + 15 * 60000);
  if (available.length) {
    const withEvidence = available.map(f => ({...f, point: byTime.get(f.time) || null}));
    // Default to frames on which the detector actually measured a peak.
    // Intermediate source images are important for diagnostics but must
    // never masquerade as measured points in the normal playback.
    const selected = includeIntermediate ? withEvidence :
      withEvidence.filter(entry => entry.point);
    if (selected.length) return selected;
  }
  return observations.filter(p => p.image_url).map(p => ({
    time: p.time, image_url: p.image_url, point: p
  }));
}

function ccorClose() {
  if (ccorPlayback) clearInterval(ccorPlayback);
  ccorPlayback = null;
  ccorCurrent = null;
  const dialog = document.getElementById("ccorVisualViewer");
  if (dialog?.open) dialog.close();
  const play = document.getElementById("ccorReviewPlay");
  if (play) play.textContent = "▶ Play";
}

function ccorDraw() {
  if (!ccorCurrent) return;
  const entries = ccorFrameEntries(ccorCurrent);
  const image = document.getElementById("ccorReviewImage");
  const overlay = document.getElementById("ccorReviewOverlay");
  const zoom = document.getElementById("ccorReviewZoom");
  const caption = document.getElementById("ccorReviewCaption");
  const evidence = document.getElementById("ccorReviewFrameEvidence");
  const allEntries = ccorFrameEntries(ccorCurrent, true);
  if (!image || !overlay || !zoom || !caption) return;
  if (!entries.length) {
    document.getElementById("ccorFramePosition").textContent = "No frames";
    image.removeAttribute("src"); image.classList.add("hidden");
    overlay.innerHTML = ""; zoom.innerHTML = "";
    caption.textContent = "No measured NOAA images available for this review. Older images may have expired.";
    if (evidence) evidence.textContent = "NO MEASURED IMAGE AVAILABLE";
    return;
  }
  ccorFrameIndex = (ccorFrameIndex + entries.length) % entries.length;
  document.getElementById("ccorFramePosition").textContent =
    "Frame " + (ccorFrameIndex + 1) + " / " + entries.length;
  const entry = entries[ccorFrameIndex];
  const p = entry.point;
  if (evidence) {
    evidence.textContent = p ? "MEASURED PEAK IN THIS FRAME" :
      "NO MEASURED DETECTION IN THIS FRAME";
    evidence.classList.toggle("ccor-no-measurement", !p);
  }
  const shape = ccorCurrent.processing_shape || [512,512];
  const w = Number(shape[0]) || 512, h = Number(shape[1]) || 512;
  // Detector points use a 512-wide aspect-ratio-preserving representation.
  // The source image scales uniformly in both axes; SVG preserves alignment.
  const trace = (ccorCurrent.points || []).filter(pt => Number.isFinite(+pt.x) && Number.isFinite(+pt.y));
  const circles = trace.map(pt => '<circle cx="' + (+pt.x) + '" cy="' + (+pt.y) +
    '" r="2.5" fill="#73fcd6" opacity=".75"/>').join("");
  const path = trace.map(pt => (+pt.x) + "," + (+pt.y)).join(" ");
  const marker = p ? '<circle cx="' + (+p.x) + '" cy="' + (+p.y) +
    '" r="9" fill="none" stroke="#ffcc62" stroke-width="2.2"/>' +
    '<path d="M' + (+p.x-14) + ' ' + (+p.y) + 'h28 M' + (+p.x) + ' ' + (+p.y-14) +
    'v28" stroke="#ffcc62" stroke-width="1" opacity=".8"/>' : "";
  overlay.setAttribute("viewBox", '0 0 ' + w + ' ' + h);
  overlay.innerHTML = '<polyline points="' + path + '" fill="none" stroke="#73fcd6" stroke-width="2"/>' +
    circles + marker;
  image.classList.remove("hidden");
  image.onload = () => {
    const srcw = image.naturalWidth, srch = image.naturalHeight;
    if (!srcw || !srch) return;
    // CSS object is fully contained in the same aspect-ratio box as overlay.
    const sameAspect = Math.abs(srcw / srch - w / h) < 0.02;
    overlay.style.display = sameAspect ? "block" : "none";
    // Intermediate frames have NO measured position. Center their zoom at
    // the time-interpolated point only to inspect an absence; never draw an
    // amber measurement marker for these frames.
    const t0 = Date.parse(ccorCurrent.first);
    const t1 = Date.parse(ccorCurrent.last);
    const tt = Date.parse(entry.time);
    const fraction = t1 > t0 && Number.isFinite(tt) ?
      Math.max(0, Math.min(1, (tt - t0) / (t1 - t0))) : 0.5;
    const currentX = p ? +p.x :
      +ccorCurrent.x_first + (+ccorCurrent.x_last - +ccorCurrent.x_first) * fraction;
    const currentY = p ? +p.y :
      +ccorCurrent.y_first + (+ccorCurrent.y_last - +ccorCurrent.y_first) * fraction;
    const crop = 72;
    const sx = srcw / w, sy = srch / h;
    const viewX = Math.max(0, Math.min(w - crop, currentX - crop / 2));
    const viewY = Math.max(0, Math.min(h - crop, currentY - crop / 2));
    zoom.innerHTML = "";
    const cropCanvas = document.createElement("canvas");
    cropCanvas.width = cropCanvas.height = 288;
    const ctx = cropCanvas.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    try {
      ctx.drawImage(image, viewX * sx, viewY * sy, crop * sx, crop * sy, 0, 0, 288, 288);
      // A ring is only shown for a REAL peak measurement.
      if (p) {
        ctx.strokeStyle = "#ffcc62";
        ctx.lineWidth = 1;
        const zx = (currentX - viewX) * 288/crop;
        const zy = (currentY - viewY) * 288/crop;
        ctx.beginPath(); ctx.arc(zx,zy,13,0,Math.PI*2); ctx.stroke();
      }
      zoom.appendChild(cropCanvas);
    } catch (error) {
      zoom.textContent = "Zoom unavailable (cross-origin NOAA image).";
    }
  };
  image.onerror = () => {
    overlay.innerHTML = ""; zoom.innerHTML = "";
    caption.textContent = "NOAA image not available: " + entry.time +
      ". Remote JPEGs can expire; this does not invalidate the stored track measurements.";
  };
  image.src = entry.image_url;
  caption.textContent = ccorCurrent.id + " · " + entry.time +
    " UTC · frame " + (ccorFrameIndex+1) + "/" + entries.length +
    (p ? " · MEASURED detector peak" :
      " · NO DETECTION in this frame (zoom centered at estimated position; no marker)") +
    " · " + ccorCurrent.points.length + " measured frames / " +
    allEntries.length + " source frames in viewer · green: proposed track" +
    (p ? " · amber: measured position" : "");
  const sourceLink = document.getElementById("ccorReviewSource");
  sourceLink.href = entry.image_url;
}
function ccorOpen(id) {
  const row = ccorPayload?.tracks?.find(t => t.id === id);
  if (!row) return;
  // Switching candidates keeps the modal open; closing/reopening it
  // would schedule a close event that could clear the next candidate.
  if (ccorPlayback) clearInterval(ccorPlayback);
  ccorPlayback = null;
  document.getElementById("ccorReviewPlay").textContent = "▶ Play";
  ccorCurrent = row;
  ccorFrameIndex = 0;
  // Enter at the first measured peak rather than a potentially empty
  // pre-track source frame.
  const viewer = document.getElementById("ccorVisualViewer");
  if (!viewer.open) viewer.showModal();
  ccorUpdateCandidatePosition();
  document.getElementById("ccorReviewHeading").textContent = row.id + " — " + row.camera.toUpperCase();
  ccorDraw();
}
function ccorUpdateCandidatePosition() {
  const index = ccorVisibleTracks.findIndex(row => row.id === ccorCurrent?.id);
  document.getElementById("ccorCandidatePosition").textContent =
    index >= 0 ? "Candidate " + (index + 1) + " / " + ccorVisibleTracks.length :
    "Candidate not in current filter";
}
function ccorCandidateStep(shift) {
  if (!ccorVisibleTracks.length) return;
  const index = ccorVisibleTracks.findIndex(row => row.id === ccorCurrent?.id);
  const next = (Math.max(index, 0) + shift + ccorVisibleTracks.length) % ccorVisibleTracks.length;
  ccorOpen(ccorVisibleTracks[next].id);
}
function ccorStep(shift) { ccorFrameIndex += shift; ccorDraw(); }
function ccorTogglePlay() {
  const button = document.getElementById("ccorReviewPlay");
  if (ccorPlayback) {
    clearInterval(ccorPlayback); ccorPlayback = null;
    button.textContent = "▶ Play";
  } else {
    ccorPlayback = setInterval(() => ccorStep(1), 700);
    button.textContent = "⏸ Pause";
  }
}
function ccorRenderCards() {
  const box = document.getElementById("ccorCandidates");
  if (!box || !ccorPayload) return;
  const tracks = ccorPayload.tracks || [];
  // The publisher exports shortlisted tracks only. Never expose raw
  // hypotheses as a fallback if an older payload is briefly cached.
  const allowed = new Set(Array.isArray(ccorPayload.review_shortlist_ids)
    ? ccorPayload.review_shortlist_ids : []);
  const selected = tracks.filter(t => allowed.has(t.id) &&
    (ccorCamera === "all" || t.camera === ccorCamera))
    .sort((a,b) => ccorSort === "newest"
      ? String(b.last || "").localeCompare(String(a.last || "")) ||
        String(b.first || "").localeCompare(String(a.first || ""))
      : (Number(b.frames || 0) - Number(a.frames || 0)) ||
        String(b.last || "").localeCompare(String(a.last || "")));
  ccorVisibleTracks = selected;
  if (ccorCurrent) ccorUpdateCandidatePosition();
  box.innerHTML = selected.map(t =>
    '<article class="realtime-candidate">' +
    '<div class="realtime-candidate-top"><strong>' + escapeHtml(t.id) +
    '</strong><span>UNVERIFIED / ' + escapeHtml(t.camera.toUpperCase()) + '</span></div>' +
    (t.review_motion_evidence?.status === "LIMITED_MOTION_EVIDENCE" ?
      '<div class="realtime-media-missing">Limited motion evidence: short displacement, duration, or too few observations. Full review only.</div>' : '') +
    (t.review_validation?.status === "PASS" ?
      '<div class="ccor-evidence-pass">Frame continuity and source-pixel checks passed (not a confirmed comet).</div>' : '') +
    (t.review_stage === "EARLY" ?
      '<div class="realtime-media-missing">Early candidate — shorter motion history; verify original frames carefully.</div>' : '') +
    (t.motion_consistency?.status === "IRREGULAR_MOTION" ?
      '<div class="realtime-media-missing">Irregular motion: abrupt trajectory change; diagnostic review only, not shortlisted.</div>' : '') +

    '<div class="realtime-candidate-meta"><div><span>First</span><strong>' + escapeHtml(t.first) +
    '</strong></div><div><span>Last</span><strong>' + escapeHtml(t.last) +
    '</strong></div><div><span>Frames</span><strong>' + escapeHtml(t.frames) +
    '</strong></div><div><span>Frames with detections</span><strong>' +
    escapeHtml((t.review_validation?.continuity?.matched ?? "—") + " / " +
      (t.review_validation?.continuity?.available ?? "—")) +
    '</strong></div><div><span>Pixel evidence</span><strong>' +
    escapeHtml((t.review_validation?.pixels?.passed ?? "—") + " / " +
      (t.review_validation?.pixels?.evaluated ?? "—")) +
    '</strong></div><div><span>Review stage</span><strong>' +
    escapeHtml(t.review_stage || "UNVERIFIED") +
    '</strong></div><div><span>Movement (px / 512)</span><strong>' +
    escapeHtml(t.review_motion_evidence?.displacement_px_512 ?? "—") +
    '</strong></div><div><span>Duration (h)</span><strong>' +
    escapeHtml(t.review_motion_evidence?.duration_hours ?? "—") +
    '</strong></div><div><span>RMS (px)</span><strong>' + escapeHtml(t.rms_px) +
    '</strong></div></div><button type="button" class="ccor-inspect" data-ccor-id="' +
    escapeHtml(t.id) + '">Inspect NOAA frames ↗</button></article>'
  ).join("") || '<div class="realtime-empty">No tracks matching the selected camera.</div>';
  box.querySelectorAll("[data-ccor-id]").forEach(button =>
    button.addEventListener("click", () => ccorOpen(button.dataset.ccorId)));
}
async function refreshCCOR() {
  const status = document.getElementById("ccorStatus");
  if (!status) return;
  try {
    const response = await fetch("./data/ccor.json?t=" + Date.now(), {cache: "no-store"});
    if (!response.ok) throw Error("CCOR data not found");
    const payload = await response.json();
    ccorPayload = payload;
    const cameras = payload.instruments || {};
    status.textContent = ["ccor1", "ccor2"].map(camera => {
      const info = cameras[camera] || {};
      const search = info.detector_search || {};
      return camera.toUpperCase() + ": " + (info.status || "NOT_STARTED") +
        " · frames " + (info.cached_recent_frames || 0) +
        " · reviews " + ((payload.published_per_camera || {})[camera] ?? (payload.tracks || []).filter(t => t.camera === camera).length) +
        (search.distinct_hypotheses != null ? " · hypotheses " + search.distinct_hypotheses : "");
    }).join(" | ") + " · Last check: " + (payload.generated_at || "unknown");
    ccorRenderCards();
    if (ccorCurrent) {
      const updated = payload.tracks?.find(t => t.id === ccorCurrent.id);
      if (updated) ccorCurrent = updated;
    }
  } catch (error) {
    status.textContent = "CCOR data temporarily unavailable; the C3 hunter is unaffected.";
  }
}
document.getElementById("ccorSortOrder")?.addEventListener("change", e => {
  ccorSort = e.target.value;
  ccorRenderCards();
});
document.getElementById("ccorCameraFilter")?.addEventListener("change", e => {
  ccorCamera = e.target.value; ccorRenderCards();
});
document.getElementById("ccorFrameMode")?.addEventListener("click", e => {
  if (ccorPlayback) clearInterval(ccorPlayback);
  ccorPlayback = null;
  document.getElementById("ccorReviewPlay").textContent = "▶ Play";
  ccorShowIntermediateFrames = !ccorShowIntermediateFrames;
  e.target.textContent = ccorShowIntermediateFrames ?
    "Show measured frames only" : "Include intermediate frames";
  ccorFrameIndex = 0;
  ccorDraw();
});
document.getElementById("ccorReviewClose")?.addEventListener("click", ccorClose);
document.getElementById("ccorReviewPrev")?.addEventListener("click", () => ccorStep(-1));
document.getElementById("ccorReviewNext")?.addEventListener("click", () => ccorStep(1));
document.getElementById("ccorReviewPlay")?.addEventListener("click", ccorTogglePlay);
document.getElementById("ccorCandidatePrev")?.addEventListener("click", () => ccorCandidateStep(-1));
document.getElementById("ccorCandidateNext")?.addEventListener("click", () => ccorCandidateStep(1));
document.getElementById("ccorVisualViewer")?.addEventListener("close", () => {
  if (ccorPlayback) clearInterval(ccorPlayback);
  ccorPlayback = null;
  ccorCurrent = null;
  document.getElementById("ccorReviewPlay").textContent = "▶ Play";
});
document.addEventListener("keydown", event => {
  const dialog = document.getElementById("ccorVisualViewer");
  if (!dialog?.open || event.altKey || event.ctrlKey || event.metaKey ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
  if (event.key === "ArrowLeft") { event.preventDefault(); ccorStep(-1); }
  else if (event.key === "ArrowRight") { event.preventDefault(); ccorStep(1); }
  else if (event.key === "ArrowUp") { event.preventDefault(); ccorCandidateStep(-1); }
  else if (event.key === "ArrowDown") { event.preventDefault(); ccorCandidateStep(1); }
  else if (event.code === "Space") { event.preventDefault(); ccorTogglePlay(); }
});
refreshCCOR();
setInterval(refreshCCOR, 60 * 1000);

document.getElementById("realtimeFindForm")?.addEventListener("submit", event => {
  event.preventDefault();
  c3OpenReview(document.getElementById("realtimeFindId").value);
});
window.addEventListener("hashchange", () => { if(state.realtime) c3TryHashTarget(state.realtime); });
