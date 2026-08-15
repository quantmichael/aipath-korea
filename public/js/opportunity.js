const isLocalEnvironment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname,
);

const API_BASE_URL = isLocalEnvironment
  ? "http://127.0.0.1:8000"
  : "";

const statusLabels = {
  scheduled: "모집 예정",
  open: "모집 중",
  closed: "마감",
  ongoing: "진행 중",
  ended: "종료",
};

const formatLabels = {
  online: "온라인",
  offline: "오프라인",
  hybrid: "온·오프라인",
};

const difficultyLabels = {
  beginner: "입문",
  intermediate: "중급",
  advanced: "고급",
  all: "제한 없음",
};

const loadingElement = document.querySelector("#detail-loading");
const detailElement = document.querySelector("#opportunity-detail");
const errorElement = document.querySelector("#detail-error");

function setText(selector, value, fallback = "공식 페이지 확인 필요") {
  const element = document.querySelector(selector);
  element.textContent = value || fallback;
}

function formatDate(dateValue) {
  if (!dateValue) {
    return null;
  }

  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date(dateValue));
}

function formatPeriod(startValue, endValue) {
  const startDate = formatDate(startValue);
  const endDate = formatDate(endValue);

  if (!startDate && !endDate) {
    return "공식 페이지 확인 필요";
  }

  if (startDate && !endDate) {
    return startDate;
  }

  if (!startDate && endDate) {
    return endDate;
  }

  if (startDate === endDate) {
    return startDate;
  }

  return `${startDate} ~ ${endDate}`;
}

function getLocationText(opportunity) {
  const locationParts = [
    opportunity.region,
    opportunity.venue,
  ].filter(Boolean);

  return locationParts.join(" · ") || "공식 페이지 확인 필요";
}

function renderTags(tagRelations) {
  const tagContainer = document.querySelector("#detail-tags");
  tagContainer.replaceChildren();

  (tagRelations || []).forEach((relation) => {
    if (!relation.tags?.name) {
      return;
    }

    const tag = document.createElement("span");
    tag.textContent = relation.tags.name;
    tagContainer.append(tag);
  });
}

function renderOpportunity(opportunity) {
  setText("#detail-category", opportunity.categories?.name, "기타");
  setText(
    "#detail-status",
    statusLabels[opportunity.status] || opportunity.status,
  );

  setText("#detail-title", opportunity.title);
  setText("#detail-summary", opportunity.summary);
  setText("#detail-description", opportunity.description);
  setText("#detail-audience", opportunity.target_audience);
  setText("#detail-organizer", opportunity.organizer);

  setText(
    "#detail-application-period",
    formatPeriod(
      opportunity.application_start_at,
      opportunity.application_deadline_at,
    ),
  );

  setText(
    "#detail-event-period",
    formatPeriod(
      opportunity.event_start_at,
      opportunity.event_end_at,
    ),
  );

  setText(
    "#detail-format",
    formatLabels[opportunity.format],
  );

  setText(
    "#detail-location",
    getLocationText(opportunity),
  );

  setText(
    "#detail-price",
    opportunity.price_text,
  );

  setText(
    "#detail-difficulty",
    difficultyLabels[opportunity.difficulty],
  );

  setText(
    "#detail-source",
    opportunity.sources?.name,
  );

  setText(
    "#detail-verified-at",
    formatDate(opportunity.last_verified_at),
  );

  renderTags(opportunity.opportunity_tags);

  const officialLink = document.querySelector("#official-link");
  officialLink.href = opportunity.official_url;

  document.title = `${opportunity.title} | AI PATH KOREA`;

  loadingElement.hidden = true;
  errorElement.hidden = true;
  detailElement.hidden = false;
}

function showError(message) {
  loadingElement.hidden = true;
  detailElement.hidden = true;
  errorElement.hidden = false;
  errorElement.textContent = message;
}

async function loadOpportunity() {
  const searchParams = new URLSearchParams(window.location.search);
  const slug = searchParams.get("slug");

  if (!slug) {
    showError("잘못된 접근입니다. 기회 식별 정보가 없습니다.");
    return;
  }

  try {
    const response = await fetch(
      `${API_BASE_URL}/api/opportunities/${encodeURIComponent(slug)}`,
    );

    if (response.status === 404) {
      showError("해당 기회를 찾을 수 없습니다.");
      return;
    }

    if (!response.ok) {
      throw new Error(`API 요청 실패: ${response.status}`);
    }

    const data = await response.json();
    renderOpportunity(data.opportunity);
  } catch (error) {
    console.error(error);
    showError("기회 상세 정보를 불러오지 못했습니다.");
  }
}

loadOpportunity();