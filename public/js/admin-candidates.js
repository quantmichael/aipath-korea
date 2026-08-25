const isLocalEnvironment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname,
);

const API_BASE_URL = isLocalEnvironment
  ? "http://127.0.0.1:8000/api/collect"
  : "/api/collect";

const SECRET_STORAGE_KEY = "aipathCollectSecret";

const secretForm = document.querySelector("#secret-form");
const secretInput = document.querySelector("#secret-input");
const candidateSearch = document.querySelector("#candidate-search");
const statusFilter = document.querySelector("#status-filter");
const sourceFilter = document.querySelector("#source-filter");
const categoryFilter = document.querySelector("#category-filter");
const tableBody = document.querySelector("#candidate-table-body");
const selectAll = document.querySelector("#select-all");
const selectionCount = document.querySelector("#selection-count");
const detailPanel = document.querySelector("#detail-panel");
const detailTitle = document.querySelector("#detail-title");
const detailList = document.querySelector("#detail-list");
const detailRaw = document.querySelector("#detail-raw");
const detailClose = document.querySelector("#detail-close");

const metrics = {
  total: document.querySelector("#metric-total"),
  verified: document.querySelector("#metric-verified"),
  review: document.querySelector("#metric-review"),
  rejected: document.querySelector("#metric-rejected"),
  promoted: document.querySelector("#metric-promoted"),
};

const statusLabels = {
  pending: "대기",
  needs_review: "보류",
  verified: "검수 가능",
  rejected: "제외",
  promoted: "공개 완료",
  duplicate: "중복",
};

let adminSecret = localStorage.getItem(SECRET_STORAGE_KEY) || "";
let candidates = [];
let filteredCandidates = [];
let selectedIds = new Set();

if (adminSecret) {
  secretInput.value = adminSecret;
  loadCandidates();
}

function createElement(tagName, className, text) {
  const element = document.createElement(tagName);

  if (className) {
    element.className = className;
  }

  if (text !== undefined) {
    element.textContent = text;
  }

  return element;
}

function formatDate(dateValue) {
  if (!dateValue) {
    return "확인 필요";
  }

  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(dateValue));
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

function getSourceName(candidate) {
  return candidate.sources?.name || "출처 확인";
}

function getCategoryName(candidate) {
  return candidate.categories?.name || "분류 필요";
}

function getSearchText(candidate) {
  return normalizeText(
    [
      candidate.title,
      candidate.summary,
      getSourceName(candidate),
      getCategoryName(candidate),
      candidate.organizer,
      candidate.target_audience,
    ].join(" "),
  );
}

function getSecretHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Collect-Secret": adminSecret,
  };
}

async function loadCandidates() {
  if (!adminSecret) {
    renderMessage("관리자 키를 입력하면 후보를 불러옵니다.");
    return;
  }

  renderMessage("후보를 불러오는 중입니다.");

  try {
    const response = await fetch(`${API_BASE_URL}/candidates?limit=50`, {
      headers: {
        "X-Collect-Secret": adminSecret,
      },
    });

    if (!response.ok) {
      throw new Error(`후보 조회 실패: ${response.status}`);
    }

    const data = await response.json();
    candidates = data.candidates || [];
    selectedIds = new Set();
    populateFilters();
    updateMetrics();
    applyFilters();
  } catch (error) {
    console.error(error);
    renderMessage(
      `후보를 불러오지 못했습니다. ${error.message}`,
      true,
    );
  }
}

function populateFilters() {
  const currentSource = sourceFilter.value;
  const currentCategory = categoryFilter.value;
  const sources = new Set();
  const categories = new Set();

  candidates.forEach((candidate) => {
    sources.add(getSourceName(candidate));
    categories.add(getCategoryName(candidate));
  });

  sourceFilter.replaceChildren(createElement("option", null, "전체"));
  sourceFilter.firstChild.value = "";

  Array.from(sources).sort().forEach((sourceName) => {
    const option = createElement("option", null, sourceName);
    option.value = sourceName;
    sourceFilter.append(option);
  });

  categoryFilter.replaceChildren(createElement("option", null, "전체"));
  categoryFilter.firstChild.value = "";

  Array.from(categories).sort().forEach((categoryName) => {
    const option = createElement("option", null, categoryName);
    option.value = categoryName;
    categoryFilter.append(option);
  });

  sourceFilter.value = Array.from(sources).has(currentSource) ? currentSource : "";
  categoryFilter.value = Array.from(categories).has(currentCategory)
    ? currentCategory
    : "";
}

function updateMetrics() {
  const counts = candidates.reduce((accumulator, candidate) => {
    const status = candidate.candidate_status || "unknown";
    accumulator[status] = (accumulator[status] || 0) + 1;
    return accumulator;
  }, {});

  metrics.total.textContent = candidates.length;
  metrics.verified.textContent = counts.verified || 0;
  metrics.review.textContent = counts.needs_review || 0;
  metrics.rejected.textContent = counts.rejected || 0;
  metrics.promoted.textContent = counts.promoted || 0;
}

function applyFilters() {
  const keyword = normalizeText(candidateSearch.value);
  const status = statusFilter.value;
  const sourceName = sourceFilter.value;
  const categoryName = categoryFilter.value;

  filteredCandidates = candidates.filter((candidate) => {
    const matchesKeyword = !keyword || getSearchText(candidate).includes(keyword);
    const matchesStatus = !status || candidate.candidate_status === status;
    const matchesSource = !sourceName || getSourceName(candidate) === sourceName;
    const matchesCategory = !categoryName || getCategoryName(candidate) === categoryName;

    return matchesKeyword && matchesStatus && matchesSource && matchesCategory;
  });

  renderCandidates();
}

function renderMessage(message, isError = false) {
  tableBody.replaceChildren();
  const row = document.createElement("tr");
  const cell = createElement(
    "td",
    isError ? "admin-empty-cell admin-error-cell" : "admin-empty-cell",
    message,
  );

  cell.colSpan = 6;
  row.append(cell);
  tableBody.append(row);
}

function renderCandidates() {
  tableBody.replaceChildren();

  if (filteredCandidates.length === 0) {
    renderMessage("조건에 맞는 후보가 없습니다.");
    updateSelectionState();
    return;
  }

  filteredCandidates.forEach((candidate) => {
    tableBody.append(createCandidateRow(candidate));
  });

  updateSelectionState();
}

function createCandidateRow(candidate) {
  const row = document.createElement("tr");
  row.dataset.candidateId = candidate.id;

  const checkboxCell = document.createElement("td");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selectedIds.has(candidate.id);
  checkbox.setAttribute("aria-label", `${candidate.title} 선택`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      selectedIds.add(candidate.id);
    } else {
      selectedIds.delete(candidate.id);
    }

    updateSelectionState();
  });
  checkboxCell.append(checkbox);

  const titleCell = document.createElement("td");
  const titleButton = createElement("button", "candidate-title-button", candidate.title);
  titleButton.type = "button";
  titleButton.addEventListener("click", () => showCandidateDetail(candidate));

  const summary = createElement(
    "p",
    "candidate-summary",
    candidate.summary || "요약 정보가 없습니다.",
  );
  const meta = createElement(
    "p",
    "candidate-row-meta",
    `${getCategoryName(candidate)} · ${candidate.organizer || "기관 확인 필요"}`,
  );
  titleCell.append(titleButton, summary, meta);

  const sourceCell = createElement("td", null, getSourceName(candidate));

  const statusCell = document.createElement("td");
  const badge = createElement(
    "span",
    `candidate-status candidate-status-${candidate.candidate_status}`,
    statusLabels[candidate.candidate_status] || candidate.candidate_status,
  );
  statusCell.append(badge);

  const deadlineCell = createElement(
    "td",
    null,
    formatDate(candidate.application_deadline_at),
  );

  const actionCell = document.createElement("td");
  const actionWrap = createElement("div", "candidate-row-actions");
  actionWrap.append(
    createRowAction("원문", () => window.open(candidate.official_url, "_blank", "noopener")),
    createRowAction("보류", () => applyAction("hold", [candidate.id])),
    createRowAction("제외", () => applyAction("reject", [candidate.id]), "danger"),
    createRowAction("공개", () => applyAction("publish", [candidate.id]), "primary"),
  );
  actionCell.append(actionWrap);

  row.append(
    checkboxCell,
    titleCell,
    sourceCell,
    statusCell,
    deadlineCell,
    actionCell,
  );

  return row;
}

function createRowAction(label, onClick, tone) {
  const button = createElement(
    "button",
    tone ? `candidate-mini-button ${tone}` : "candidate-mini-button",
    label,
  );
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function updateSelectionState() {
  const visibleIds = filteredCandidates.map((candidate) => candidate.id);
  const selectedVisibleCount = visibleIds.filter((id) => selectedIds.has(id)).length;

  selectionCount.textContent = `선택된 후보 ${selectedIds.size}개`;
  selectAll.checked =
    visibleIds.length > 0 && selectedVisibleCount === visibleIds.length;
  selectAll.indeterminate =
    selectedVisibleCount > 0 && selectedVisibleCount < visibleIds.length;
}

function showCandidateDetail(candidate) {
  detailPanel.hidden = false;
  detailTitle.textContent = candidate.title;
  detailList.replaceChildren();

  const rows = [
    ["상태", statusLabels[candidate.candidate_status] || candidate.candidate_status],
    ["Source", getSourceName(candidate)],
    ["카테고리", getCategoryName(candidate)],
    ["주최", candidate.organizer || "확인 필요"],
    ["대상", candidate.target_audience || "확인 필요"],
    ["신청 시작", formatDate(candidate.application_start_at)],
    ["신청 마감", formatDate(candidate.application_deadline_at)],
    ["원문", candidate.official_url || "확인 필요"],
    [
      "검증 오류",
      (candidate.validation_errors || []).length
        ? candidate.validation_errors.join(", ")
        : "없음",
    ],
    ["요약", candidate.summary || "요약 정보가 없습니다."],
  ];

  rows.forEach(([label, value]) => {
    const wrapper = document.createElement("div");
    wrapper.append(
      createElement("dt", null, label),
      createElement("dd", null, value),
    );
    detailList.append(wrapper);
  });

  detailRaw.textContent = JSON.stringify(
    candidate.raw_payload || {
      message: "목록 성능을 위해 raw payload는 목록 응답에서 제외했습니다.",
    },
    null,
    2,
  );
}

async function applyAction(action, candidateIds) {
  const ids = candidateIds.length ? candidateIds : Array.from(selectedIds);

  if (ids.length === 0) {
    alert("처리할 후보를 선택하세요.");
    return;
  }

  if (action === "publish" && !confirm(`${ids.length}개 후보를 공개할까요?`)) {
    return;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/candidates/action`, {
      method: "POST",
      headers: getSecretHeaders(),
      body: JSON.stringify({
        action,
        candidate_ids: ids,
      }),
    });

    if (!response.ok) {
      throw new Error(`처리 실패: ${response.status}`);
    }

    const result = await response.json();
    if (result.errors?.length) {
      alert(result.errors.join("\n"));
    }

    selectedIds = new Set();
    await loadCandidates();
  } catch (error) {
    console.error(error);
    alert("후보 처리에 실패했습니다.");
  }
}

secretForm.addEventListener("submit", (event) => {
  event.preventDefault();
  adminSecret = secretInput.value.trim();

  if (!adminSecret) {
    renderMessage("관리자 키를 입력하세요.", true);
    return;
  }

  localStorage.setItem(SECRET_STORAGE_KEY, adminSecret);
  loadCandidates();
});

candidateSearch.addEventListener("input", applyFilters);
statusFilter.addEventListener("change", applyFilters);
sourceFilter.addEventListener("change", applyFilters);
categoryFilter.addEventListener("change", applyFilters);

selectAll.addEventListener("change", () => {
  filteredCandidates.forEach((candidate) => {
    if (selectAll.checked) {
      selectedIds.add(candidate.id);
    } else {
      selectedIds.delete(candidate.id);
    }
  });

  renderCandidates();
});

document.querySelectorAll("[data-bulk-action]").forEach((button) => {
  button.addEventListener("click", () => {
    applyAction(button.dataset.bulkAction, Array.from(selectedIds));
  });
});

detailClose.addEventListener("click", () => {
  detailPanel.hidden = true;
});
