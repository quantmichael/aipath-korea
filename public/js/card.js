const vCardButton = document.querySelector("[data-vcard-download]");

const vCard = [
  "BEGIN:VCARD",
  "VERSION:3.0",
  "N:남;병광;;;",
  "FN:남병광",
  "TITLE:AI / Data Developer",
  "ORG:AIPath",
  "TEL;TYPE=CELL:010-8244-7879",
  "EMAIL:michaelis@naver.com",
  "URL:https://aipath.kr",
  "END:VCARD",
].join("\r\n");

vCardButton?.addEventListener("click", () => {
  const blob = new Blob([vCard], { type: "text/vcard;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download = "nam-byeonggwang-ai-path.vcf";
  document.body.appendChild(link);
  link.click();
  link.remove();

  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
