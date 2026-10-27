"use strict";
const copyButton = document.getElementById("copy-prompt");
if (copyButton) {
  copyButton.addEventListener("click", async () => {
    const template = document.getElementById("prompt-template");
    const status = document.getElementById("copy-status");
    copyButton.disabled = true;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(template.textContent);
      status.textContent = "복사했습니다. {{변수}}를 바꿔 사용하세요.";
    } catch {
      status.textContent = "복사하지 못했습니다. 아래 내용을 선택해 복사하세요.";
      template.focus();
      const range = document.createRange();
      range.selectNodeContents(template);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } finally {
      copyButton.disabled = false;
    }
  });
}
if (location.hash === "#list-title") document.getElementById("list-title")?.focus();
