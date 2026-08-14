const isLocalEnvironment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname,
);

const API_URL = isLocalEnvironment
  ? "http://127.0.0.1:8000/api/opportunities"
  : "/api/opportunities";

const opportunityList = document.querySelector("#opportunity-list");
const resultCount = document.querySelector("#result-count");

const statusLabels = {
  scheduled: "모집 예정",
  open: "모집 중",
  closed: "마감",
  ongoing: "진행 중",
  ended: "종료",
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

async function loadOpportunities() {
  try {
    const response = await fetch(API_URL);

    if (!response.ok) {
      throw new Error(`API 요청 실패: ${response.status}`);
    }

    const data = await response.json();
    const opportunities = data.opportunities || [];

    opportunityList.replaceChildren();
    resultCount.textContent = opportunities.length;

    if (opportunities.length === 0) {
      opportunityList.append(
        createElement(
          "p",
          "result-message",
          "현재 조건에 맞는 기회가 없습니다.",
        ),
      );

      return;
    }

    opportunities.forEach((opportunity) => {
      opportunityList.append(createOpportunityCard(opportunity));
    });
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

loadOpportunities();