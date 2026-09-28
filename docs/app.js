const state = {
  candidates: [],
  query: "",
  reviewClass: "all",
  family: "all",
  sort: "score"
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
  if (!value) return "Unknown";
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

function candidateCard(c) {
  const preview = c.thumbnail
    ? `<img src="${escapeHtml(c.thumbnail)}" alt="Preview for ${escapeHtml(c.id)}">`
    : `<div class="preview-fallback">☄</div>`;

  return `
    <article class="candidate-card">
      <div class="preview">
        ${preview}
        <div class="preview-overlay">
          <span class="badge ${badgeClass(c.review_class)}">${escapeHtml(prettyClass(c.review_class))}</span>
          <span class="badge">${escapeHtml(c.source || "SOHO")}</span>
        </div>
      </div>

      <div class="card-body">
        <div class="card-top">
          <div>
            <h3 class="card-title">${escapeHtml(c.title || c.id)}</h3>
            <p class="card-subtitle">${escapeHtml(c.event_id || c.id)}</p>
          </div>
          <div class="confidence">
            ${scoreText(c.score)}
            <small>PIPELINE SCORE</small>
          </div>
        </div>

        <div class="meta-grid">
          <div class="meta-item"><span>Track frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>
          <div class="meta-item"><span>Verified frames</span><strong>${escapeHtml(c.verified_frames ?? "—")}</strong></div>
          <div class="meta-item"><span>Members</span><strong>${escapeHtml(c.members ?? "—")}</strong></div>
          <div class="meta-item"><span>Speed</span><strong>${escapeHtml(metricText(c.speed))}</strong></div>
          <div class="meta-item"><span>Sunward motion</span><strong>${escapeHtml(metricText(c.sun_distance))}</strong></div>
          <div class="meta-item"><span>RMS</span><strong>${escapeHtml(metricText(c.rms))}</strong></div>
          <div class="meta-item"><span>Family</span><strong>${escapeHtml(c.family || "Unknown")}</strong></div>
          <div class="meta-item"><span>First seen</span><strong>${escapeHtml(formatDate(c.first_seen))}</strong></div>
          <div class="meta-item"><span>Instrument</span><strong>${escapeHtml(c.instrument || "LASCO")}</strong></div>
        </div>

        <div class="card-actions">
          <button data-open="${escapeHtml(c.id)}">${c.video ? "Review candidate" : "View details"}</button>
        </div>
      </div>
    </article>
  `;
}

function filteredCandidates() {
  const q = state.query.trim().toLowerCase();
  let rows = state.candidates.filter(c => {
    const matchesQuery = !q || [
      c.id, c.event_id, c.title, c.family, c.source, c.instrument, c.notes, c.review_class
    ].some(v => String(v || "").toLowerCase().includes(q));

    const matchesClass = state.reviewClass === "all" ||
      normalizeClass(c.review_class) === state.reviewClass;

    const matchesFamily = state.family === "all" ||
      String(c.family || "Unknown") === state.family;

    return matchesQuery && matchesClass && matchesFamily;
  });

  rows.sort((a, b) => {
    if (state.sort === "score") {
      const av = Number.isFinite(Number(a.score)) ? Number(a.score) : -Infinity;
      const bv = Number.isFinite(Number(b.score)) ? Number(b.score) : -Infinity;
      return bv - av;
    }
    if (state.sort === "frames") return Number(b.frames || 0) - Number(a.frames || 0);
    return new Date(b.first_seen || 0) - new Date(a.first_seen || 0);
  });

  return rows;
}

function render() {
  const rows = filteredCandidates();
  $("#candidateGrid").innerHTML = rows.map(candidateCard).join("");
  $("#visibleCount").textContent = `${rows.length} shown`;
  $("#emptyState").classList.toggle("hidden", rows.length !== 0);

  document.querySelectorAll("[data-open]").forEach(button => {
    button.addEventListener("click", () => {
      const candidate = state.candidates.find(c => c.id === button.dataset.open);
      if (candidate) openDialog(candidate);
    });
  });
}

function openDialog(c) {
  const media = c.video
    ? `<video controls preload="metadata" ${c.thumbnail ? `poster="${escapeHtml(c.thumbnail)}"` : ""}>
         <source src="${escapeHtml(c.video)}" type="video/mp4">
         Your browser could not play this video.
       </video>`
    : (c.thumbnail ? `<img src="${escapeHtml(c.thumbnail)}" alt="Candidate preview">` : "");

  $("#dialogBody").innerHTML = `
    <div class="dialog-content">
      <div class="eyebrow">CANDIDATE REVIEW</div>
      <h2>${escapeHtml(c.title || c.id)}</h2>
      <p>${escapeHtml(c.notes || "No review notes have been added yet.")}</p>

      <div class="dialog-info">
        <div><span>Event ID</span><strong>${escapeHtml(c.event_id || c.id)}</strong></div>
        <div><span>Review class</span><strong>${escapeHtml(prettyClass(c.review_class))}</strong></div>
        <div><span>Pipeline score</span><strong>${scoreText(c.score)}</strong></div>
        <div><span>Track frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>
        <div><span>Verified frames</span><strong>${escapeHtml(c.verified_frames ?? "—")}</strong></div>
        <div><span>Members</span><strong>${escapeHtml(c.members ?? "—")}</strong></div>
        <div><span>Video</span><strong>${escapeHtml(prettyVideoVariant(c.video_variant))}</strong></div>
      </div>

      <p>
        <strong>Speed:</strong> ${escapeHtml(metricText(c.speed))}<br>
        <strong>Sunward motion:</strong> ${escapeHtml(metricText(c.sun_distance))}<br>
        <strong>RMS:</strong> ${escapeHtml(metricText(c.rms))}<br>
        <strong>Family:</strong> ${escapeHtml(c.family || "Unknown")}<br>
        <strong>First seen:</strong> ${escapeHtml(formatDate(c.first_seen))}<br>
        <strong>Last seen:</strong> ${escapeHtml(formatDate(c.last_seen))}<br>
        <strong>Source:</strong> ${escapeHtml(c.source || "SOHO")} · ${escapeHtml(c.instrument || "LASCO")}
      </p>

      ${media}
    </div>
  `;
  $("#candidateDialog").showModal();
}

async function loadData() {
  try {
    const [candidateResponse, statusResponse] = await Promise.all([
      fetch("./data/candidates.json", { cache: "no-store" }),
      fetch("./data/status.json", { cache: "no-store" })
    ]);

    if (!candidateResponse.ok) throw new Error("Could not load candidate data");

    const payload = await candidateResponse.json();
    const status = statusResponse.ok ? await statusResponse.json() : {};
    state.candidates = Array.isArray(payload.candidates) ? payload.candidates : [];

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

    const families = [...new Set(state.candidates.map(c => c.family || "Unknown"))]
      .sort((a, b) => a.localeCompare(b));
    $("#familyFilter").innerHTML =
      `<option value="all">All families</option>` +
      families.map(f => `<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join("");

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
$("#familyFilter").addEventListener("change", e => { state.family = e.target.value; render(); });
$("#sortSelect").addEventListener("change", e => { state.sort = e.target.value; render(); });
$("#dialogClose").addEventListener("click", () => $("#candidateDialog").close());
$("#candidateDialog").addEventListener("click", e => {
  if (e.target === $("#candidateDialog")) $("#candidateDialog").close();
});

loadData();
