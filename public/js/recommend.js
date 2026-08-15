const isLocalEnvironment = ["localhost", "127.0.0.1"].includes(
  window.location.hostname,
);

const API_URL = isLocalEnvironment
  ? "http://127.0.0.1:8000/api/recommend"
  : "/api/recommend";

const recommendForm = document.querySelector("#recommend-form");
const resultContainer = document.querySelector("#recommend-result");
const submitButton = recommendForm.querySelector("button[type='submit']");

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

function showLoading() {
  resultContainer.replaceChildren();

  const loading = createElement("div", "recommend-empty");
  const mark = createElement("span", null, "AI");
  const title = createElement(
    "h2",
    null,
    "등록된 기회를 비교하고 있습니다.",
  );
  const description = createElement(
    "p",
    null,
    "잠시만 기다려주세요.",
  );

  loading.append(mark, title, description);
  resultContainer.append(loading);
}

function showError(message) {
  resultContainer.replaceChildren(
    createElement(
      "p",
      "result-message result-message-error",
      message,
    ),
  );
}

function renderRecommendations(data) {
    resultContainer.replaceChildren();

    const heading = createElement("div", "recommend-result-heading");

    heading.append(
    createElement("span", null, "AI MATCHING RESULT"),
    createElement(
            "h2",
            null,
            `${data.count}개의 기회를 추천합니다.`,
        ),
    );

    if (data.recommendations.length) {
        heading.append(
            createElement("p", null, data.message),
    );
    }

    resultContainer.append(heading);

    if (!data.recommendations.length) {
        resultContainer.append(
        createElement(
            "p",
            "result-message",
            data.message || "추천할 수 있는 기회가 없습니다.",
        ),
    );

    return;
  }

  const list = createElement("div", "recommendation-list");

  data.recommendations.forEach((item, index) => {
    const opportunity = item.opportunity;
    const card = createElement("article", "recommendation-card");

    const rank = createElement(
      "span",
      "recommendation-rank",
      `추천 ${index + 1}순위`,
    );

    const category = createElement(
      "span",
      "category-badge",
      opportunity.categories?.name || "기타",
    );

    const cardTop = createElement("div", "recommendation-card-top");
    cardTop.append(rank, category);

    const title = createElement("h3", null, opportunity.title);
    const summary = createElement(
      "p",
      "recommendation-summary",
      opportunity.summary,
    );

    const reasonBox = createElement("div", "recommendation-reason");
    reasonBox.append(
      createElement("strong", null, "추천 이유"),
      createElement("p", null, item.reason),
    );

    const detailLink = createElement(
      "a",
      "button button-secondary recommendation-link",
      "상세 정보 확인",
    );

    detailLink.href =
      `./opportunity.html?slug=${encodeURIComponent(opportunity.slug)}`;

    card.append(
      cardTop,
      title,
      summary,
      reasonBox,
      detailLink,
    );

    list.append(card);
  });

  resultContainer.append(list);
}

function getRequestData() {
  const formData = new FormData(recommendForm);

  return {
    level: formData.get("level"),
    interest: String(formData.get("interest") || "").trim(),
    goal: formData.get("goal"),
    format: formData.get("format") || null,
    price: formData.get("price") || null,
    available_period:
      String(formData.get("available_period") || "").trim() || null,
  };
}

recommendForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const requestData = getRequestData();

  submitButton.disabled = true;
  submitButton.textContent = "추천 분석 중...";
  showLoading();

  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestData),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.detail || `API 요청 실패: ${response.status}`,
      );
    }

    renderRecommendations(data);
  } catch (error) {
    console.error(error);

    const message =
    error instanceof TypeError
      ? "추천 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
      : error.message || "AI 추천을 불러오지 못했습니다.";

    showError(message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "AI 추천받기";
  }
});