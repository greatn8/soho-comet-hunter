const state = {
  candidates: [],
  query: "",
  status: "all",
  family: "all",
  sort: "newest"
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

function confidenceText(c) {
  return Number.isFinite(Number(c)) ? `${Math.round(Number(c))}%` : "—";
}

function normalizeStatus(status) {
  return String(status || "candidate").toLowerCase().replaceAll(" ", "_");
}

function prettyStatus(status) {
  const s = normalizeStatus(status).replaceAll("_", " ");
  return s.replace(/\b\w/g, c => c.toUpperCase());
}

function candidateCard(c) {
  const confidence = Number.isFinite(Number(c.confidence)) ? Number(c.confidence) : null;
  const preview = c.thumbnail
    ? `<img src="${escapeHtml(c.thumbnail)}" alt="Preview for ${escapeHtml(c.id)}">`
    : `<div class="preview-fallback">☄</div>`;

  const videoButton = c.video
    ? `<button data-open="${escapeHtml(c.id)}">Review candidate</button>`
    : `<button data-open="${escapeHtml(c.id)}">View details</button>`;

  return `
    <article class="candidate-card">
      <div class="preview">
        ${preview}
        <div class="preview-overlay">
          <span class="badge status-${escapeHtml(normalizeStatus(c.status))}">
            ${escapeHtml(prettyStatus(c.status))}
          </span>
          <span class="badge">${escapeHtml(c.source || "SOHO")}</span>
        </div>
      </div>
      <div class="card-body">
        <div class="card-top">
          <div>
            <h3 class="card-title">${escapeHtml(c.title || c.id)}</h3>
            <p class="card-subtitle">${escapeHtml(c.id)}</p>
          </div>
          <div class="confidence">
            ${confidenceText(confidence)}
            <small>CONFIDENCE</small>
          </div>
        </div>

        <div class="meter"><span style="width:${confidence ?? 0}%"></span></div>

        <div class="meta-grid">
          <div class="meta-item">
            <span>Family</span>
            <strong>${escapeHtml(c.family || "Unknown")}</strong>
          </div>
          <div class="meta-item">
            <span>Frames</span>
            <strong>${escapeHtml(c.frames ?? "—")}</strong>
          </div>
          <div class="meta-item">
            <span>First seen</span>
            <strong>${escapeHtml(formatDate(c.first_seen))}</strong>
          </div>
          <div class="meta-item">
            <span>Instrument</span>
            <strong>${escapeHtml(c.instrument || "LASCO")}</strong>
          </div>
        </div>

        <div class="card-actions">${videoButton}</div>
      </div>
    </article>
  `;
}

function filteredCandidates() {
  const q = state.query.trim().toLowerCase();
  let rows = state.candidates.filter(c => {
    const matchesQuery = !q || [
      c.id, c.title, c.family, c.source, c.instrument, c.notes
    ].some(v => String(v || "").toLowerCase().includes(q));

    const matchesStatus = state.status === "all" ||
      normalizeStatus(c.status) === state.status;

    const matchesFamily = state.family === "all" ||
      String(c.family || "Unknown") === state.family;

    return matchesQuery && matchesStatus && matchesFamily;
  });

  rows.sort((a, b) => {
    if (state.sort === "confidence") {
      return Number(b.confidence || -1) - Number(a.confidence || -1);
    }
    if (state.sort === "frames") {
      return Number(b.frames || 0) - Number(a.frames || 0);
    }
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
        <div><span>Status</span><strong>${escapeHtml(prettyStatus(c.status))}</strong></div>
        <div><span>Confidence</span><strong>${confidenceText(c.confidence)}</strong></div>
        <div><span>Family</span><strong>${escapeHtml(c.family || "Unknown")}</strong></div>
        <div><span>Frames</span><strong>${escapeHtml(c.frames ?? "—")}</strong></div>
      </div>

      <p><strong>First seen:</strong> ${escapeHtml(formatDate(c.first_seen))}<br>
      <strong>Source:</strong> ${escapeHtml(c.source || "SOHO")} · ${escapeHtml(c.instrument || "LASCO")}</p>

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
    $("#highConfidenceCount").textContent =
      state.candidates.filter(c => Number(c.confidence) >= 80).length;
    $("#reviewCount").textContent =
      state.candidates.filter(c => ["candidate", "review"].includes(normalizeStatus(c.status))).length;
    $("#lastUpdated").textContent = formatDate(payload.generated_at || status.generated_at);

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
        <p>${escapeHtml(error.message)}. If you opened index.html directly, run a local web server or publish it with GitHub Pages.</p>
      </section>
    `;
  }
}

$("#searchInput").addEventListener("input", e => { state.query = e.target.value; render(); });
$("#statusFilter").addEventListener("change", e => { state.status = e.target.value; render(); });
$("#familyFilter").addEventListener("change", e => { state.family = e.target.value; render(); });
$("#sortSelect").addEventListener("change", e => { state.sort = e.target.value; render(); });
$("#dialogClose").addEventListener("click", () => $("#candidateDialog").close());
$("#candidateDialog").addEventListener("click", e => {
  if (e.target === $("#candidateDialog")) $("#candidateDialog").close();
});

loadData();
