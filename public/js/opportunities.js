const isLocalEnvironment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname,
);

const API_URL = isLocalEnvironment
  ? "http://127.0.0.1:8000/api/opportunities"
  : "/api/opportunities";

const opportunityList = document.querySelector("#opportunity-list");
const resultCount = document.querySelector("#result-count");
const searchInput = document.querySelector("#search-input");
const categoryFilter = document.querySelector("#category-filter");
const statusFilter = document.querySelector("#status-filter");
const formatFilter = document.querySelector("#format-filter");
const priceFilter = document.querySelector("#price-filter");

let allOpportunities = [];

const initialParams = new URLSearchParams(window.location.search);
const initialCategory = initialParams.get("category");

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
    return "공식 페이지 확인 필요";
  }

  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date(dateValue));
}

function createMetaItem(label, value) {
  const wrapper = document.createElement("div");
  const term = createElement("dt", null, label);
  const description = createElement("dd", null, value);

  wrapper.append(term, description);

  return wrapper;
}

function createOpportunityCard(opportunity) {
  const article = createElement("article", "opportunity-card");
  const cardTop = createElement("div", "opportunity-card-top");

  const categoryName = opportunity.categories?.name || "기타";
  const categoryBadge = createElement(
    "span",
    "category-badge",
    categoryName,
  );

  const statusBadge = createElement(
    "span",
    "status-open",
    statusLabels[opportunity.status] || opportunity.status,
  );

  cardTop.append(categoryBadge, statusBadge);

  const heading = document.createElement("h3");
  const titleLink = createElement("a", null, opportunity.title);

  titleLink.href =
    `./opportunity.html?slug=${encodeURIComponent(opportunity.slug)}`;

  heading.append(titleLink);

  const summary = createElement(
    "p",
    "opportunity-summary",
    opportunity.summary,
  );

  const metadata = createElement("dl", "opportunity-meta");

  metadata.append(
    createMetaItem("주최", opportunity.organizer),
    createMetaItem(
      "모집 마감",
      formatDate(opportunity.application_deadline_at),
    ),
    createMetaItem(
      "참여 방식",
      formatLabels[opportunity.format] || "공식 페이지 확인 필요",
    ),
    createMetaItem(
      "참가비",
      opportunity.price_text || "공식 페이지 확인 필요",
    ),
  );

  const tagList = createElement("div", "tag-list");
  tagList.setAttribute("aria-label", "관련 태그");

  const tagRelations = opportunity.opportunity_tags || [];

  tagRelations.forEach((relation) => {
    if (relation.tags?.name) {
      tagList.append(
        createElement("span", null, relation.tags.name),
      );
    }
  });

  const detailLink = createElement("a", "card-link", "자세히 보기");

  detailLink.href =
    `./opportunity.html?slug=${encodeURIComponent(opportunity.slug)}`;

  article.append(
    cardTop,
    heading,
    summary,
    metadata,
    tagList,
    detailLink,
  );

  return article;
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

function getOpportunitySearchText(opportunity) {
  const tagNames = (opportunity.opportunity_tags || [])
    .map((relation) => relation.tags?.name || "")
    .join(" ");

  return normalizeText(
    [
      opportunity.title,
      opportunity.summary,
      opportunity.organizer,
      opportunity.target_audience,
      opportunity.categories?.name,
      tagNames,
    ].join(" "),
  );
}

function renderOpportunities(opportunities) {
  opportunityList.replaceChildren();
  resultCount.textContent = opportunities.length;

  if (opportunities.length === 0) {
    opportunityList.append(
      createElement(
        "p",
        "result-message",
        "검색 조건에 맞는 기회가 없습니다.",
      ),
    );

    return;
  }

  opportunities.forEach((opportunity) => {
    opportunityList.append(createOpportunityCard(opportunity));
  });
}

function applyFilters() {
  const keyword = normalizeText(searchInput.value);
  const category = categoryFilter.value;
  const status = statusFilter.value;
  const format = formatFilter.value;
  const priceType = priceFilter.value;

  const filteredOpportunities = allOpportunities.filter((opportunity) => {
    const matchesKeyword =
      !keyword ||
      getOpportunitySearchText(opportunity).includes(keyword);

    const matchesCategory =
      !category ||
      opportunity.categories?.slug === category;

    const matchesStatus =
      !status ||
      opportunity.status === status;

    const matchesFormat =
      !format ||
      opportunity.format === format;

    const matchesPrice =
      !priceType ||
      opportunity.price_type === priceType;

    return (
      matchesKeyword &&
      matchesCategory &&
      matchesStatus &&
      matchesFormat &&
      matchesPrice
    );
  });

  renderOpportunities(filteredOpportunities);
}

async function loadOpportunities() {
  try {
    const response = await fetch(API_URL);

    if (!response.ok) {
      throw new Error(`API 요청 실패: ${response.status}`);
    }

    const data = await response.json();

    allOpportunities = data.opportunities || [];

    const hasInitialCategory = Array.from(categoryFilter.options).some(
      (option) => option.value === initialCategory,
    );

    if (initialCategory && hasInitialCategory) {
      categoryFilter.value = initialCategory;
      applyFilters();
      return;
    }

    renderOpportunities(allOpportunities);
  } catch (error) {
    console.error(error);

    resultCount.textContent = "0";
    opportunityList.replaceChildren(
      createElement(
        "p",
        "result-message result-message-error",
        "기회 정보를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
      ),
    );
  }
}

searchInput.addEventListener("input", applyFilters);

[
  categoryFilter,
  statusFilter,
  formatFilter,
  priceFilter,
].forEach((filterElement) => {
  filterElement.addEventListener("change", applyFilters);
});

loadOpportunities();
