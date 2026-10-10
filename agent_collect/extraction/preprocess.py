"""Deterministic HTML cleanup and stable, selectable evidence units."""

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from agent_collect.collectors.security import UnsafeTargetError, validate_target_url


_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "p", "li", "tr", "section", "article", "div", "br"})
_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "aside", "noscript", "svg", "form"})
_VOID_TAGS = frozenset({"br", "img", "meta", "link", "input", "hr", "source", "area", "embed"})
_NOISE = re.compile(r"cookie|consent|breadcrumb|newsletter|site[-_ ]?footer|site[-_ ]?nav", re.I)
_SIGNALS = re.compile(
    r"registration|register|deadline|date|schedule|venue|location|conference|"
    r"call for participation|call for papers|fee|price|organizer|hosted by|"
    r"등록|신청|마감|일정|장소|컨퍼런스|학회|참가비|주최", re.I,
)
_DATE_FRAGMENT = re.compile(r"(?:\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\b|\d{1,2}[–-]\d{1,2})", re.I)


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    block_id: str
    text: str
    source_position: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class LinkEvidence:
    link_id: str
    text: str
    href: str
    source_position: int | None


@dataclass(frozen=True, slots=True)
class PreparedContent:
    full_text: str
    selected_text: str
    links: tuple[str, ...]
    truncated: bool
    evidence_blocks: tuple[EvidenceBlock, ...]
    link_evidence: tuple[LinkEvidence, ...]

    @property
    def model_input(self) -> str:
        link_lines = [f"[{link.link_id}] {link.text} -> {link.href}" for link in self.link_evidence]
        return self.selected_text + "\nLINKS\n" + "\n".join(link_lines)


class _TextParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[tuple[str, str, int]] = []
        self.skip_depth = 0
        self.active_link: tuple[str, list[str], int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_depth:
            if tag not in _VOID_TAGS:
                self.skip_depth += 1
            return
        attributes = dict(attrs)
        if tag in _SKIP_TAGS or _NOISE.search(" ".join(attributes.get(k) or "" for k in ("id", "class", "role"))):
            if tag not in _VOID_TAGS:
                self.skip_depth = 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        if tag == "a" and attributes.get("href"):
            url = urljoin(self.base_url, attributes["href"])
            try:
                validate_target_url(url)
            except UnsafeTargetError:
                return
            self.active_link = (url, [], len(self.parts))

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "a" and self.active_link:
            href, words, position = self.active_link
            self.links.append((" ".join(" ".join(words).split()) or href, href, position))
            self.active_link = None
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            if self.parts and self.parts[-1].rstrip().endswith((",", ":")) and data[:1].isalnum():
                self.parts.append(" ")
            self.parts.append(data)
            if self.active_link:
                self.active_link[1].append(data)


def _merge_fragments(lines: list[str]) -> list[tuple[int, str]]:
    merged: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        start = index
        text = lines[index]
        # HTML spans often split a label, month/day and year into separate lines.
        for _ in range(2):
            if index + 1 >= len(lines):
                break
            following = lines[index + 1]
            if not (text.endswith((":", ",")) or
                    (_DATE_FRAGMENT.search(text) and len(text) < 55 and
                     (following[:1].isdigit() or following.lower().startswith("202")))):
                break
            text += " " + following
            index += 1
        merged.append((start, text))
        index += 1
    return merged


def preprocess_html(content: bytes, *, source_url: str, max_input_chars: int) -> PreparedContent:
    if max_input_chars <= 0:
        raise ValueError("max_input_chars must be positive")
    parser = _TextParser(source_url)
    parser.feed(content.decode("utf-8", errors="replace"))
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    meaningful = [line for line in lines if line]
    merged = _merge_fragments(meaningful)
    blocks = tuple(EvidenceBlock(
        f"B{index:03d}", value, source_position + 1,
        hashlib.sha256(value.encode("utf-8")).hexdigest(),
    ) for index, (source_position, value) in enumerate(merged, 1))
    full = "\n".join(block.text for block in blocks)

    # L000 is the fetched page URL, not an invented outgoing link.
    all_links = [LinkEvidence("L000", "Fetched page", source_url, None)]
    seen = {source_url}
    for label, href, position in parser.links:
        if href not in seen:
            seen.add(href)
            all_links.append(LinkEvidence(f"L{len(all_links):03d}", label, href, position))
    text_limit = max(1, int(max_input_chars * 0.8))
    link_limit = max_input_chars - text_limit
    selected_links: list[LinkEvidence] = []
    for link in sorted(all_links, key=lambda row: (0 if row.link_id == "L000" else -len(_SIGNALS.findall(row.text + row.href)), row.link_id)):
        line = f"[{link.link_id}] {link.text} -> {link.href}"
        if len(line) + 1 <= link_limit:
            selected_links.append(link)
            link_limit -= len(line) + 1
    selected_links.sort(key=lambda row: row.link_id)

    rendered = [f"[{block.block_id}] {block.text}" for block in blocks]
    if len("\n".join(rendered)) <= text_limit:
        selected = blocks
    else:
        chosen: dict[int, EvidenceBlock] = {}
        remaining = text_limit
        order = list(range(min(2, len(blocks)))) + sorted(
            range(2, len(blocks)),
            key=lambda i: (-len(_SIGNALS.findall(blocks[i].text)), i),
        )
        for index in order:
            line = rendered[index]
            cost = len(line) + (1 if chosen else 0)
            if cost <= remaining:
                chosen[index] = blocks[index]
                remaining -= cost
        selected = tuple(chosen[i] for i in sorted(chosen))
    selected_text = "\n".join(f"[{block.block_id}] {block.text}" for block in selected)
    return PreparedContent(full, selected_text, tuple(link.href for link in selected_links),
                           len(selected) < len(blocks) or len(selected_links) < len(all_links),
                           selected, tuple(selected_links))
