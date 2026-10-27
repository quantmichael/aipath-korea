import base64
import binascii
import ipaddress
import json
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt
from markupsafe import Markup

from .domain import SECTIONS, GateError, exposure_gate, instant, pilot_preview_gate, projection, utcnow
from .store import DEFAULT_STORE, ROOT, read_bundle

markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")
# Inline HTML is escaped; images are disabled to avoid external requests.
templates = Environment(loader=FileSystemLoader(ROOT / "aipath/templates"), autoescape=select_autoescape())
templates.filters["markdown"] = lambda value: Markup(markdown.render(value))
templates.filters["date"] = lambda value: instant(value).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d") if value else "미확인"


def create_app(store: Path = DEFAULT_STORE, *, pilot_token: str | None = None,
               public_shell: bool = False):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    if pilot_token is not None and len(pilot_token) < 24:
        raise ValueError("Pilot preview token must have at least 24 characters")
    settings = json.loads((ROOT / "settings.json").read_text())
    if settings != {"publication_mode": "disabled", "source_connectors_enabled": False,
                    "external_ai_enabled": False, "analytics_enabled": False, "production_target": None}:
        raise RuntimeError("This prototype only supports disabled publication and external integrations")

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        try:
            local = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            local = False
        if not public_shell and (not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}):
            response = PlainTextResponse("Local preview only", status_code=403)
        elif pilot_token is not None and (request.url.path == "/pilot" or request.url.path.startswith("/pilot/")) and not pilot_authenticated(request):
            response = PlainTextResponse("Pilot preview sign-in required", status_code=401,
                                         headers={"WWW-Authenticate": 'Basic realm="AIPATH Pilot", charset="UTF-8"'})
        else:
            response = await call_next(request)
        response.headers.update({"Cache-Control": "public, max-age=300" if public_shell else "private, no-store, max-age=0",
                                 "CDN-Cache-Control": "max-age=300" if public_shell else "no-store",
                                 "Vercel-CDN-Cache-Control": "max-age=300" if public_shell else "no-store",
                                 "X-Robots-Tag": "index, follow" if public_shell else "noindex, nofollow, noarchive",
                                 "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                                 "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})
        return response

    def pilot_authenticated(request: Request):
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            username, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False
        return secrets.compare_digest(username, "pilot") and secrets.compare_digest(password, pilot_token)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def render(name, *, mode="public", status=200, **context):
        html = templates.get_template(name).render(sections=SECTIONS, demo=mode == "demo", pilot=mode == "pilot",
               public_shell=public_shell,
               prefix="/" + mode if mode != "public" else "", active=None, **context)
        return HTMLResponse(html, status_code=status)

    def snapshot(mode):
        if public_shell:
            return [], None
        if mode == "public":
            return [], None
        if not (store / "catalog.json").exists():
            return [], None
        # Reload on every request: permission changes never depend on a rebuild.
        b, _ = read_bundle(store)
        visible = []
        now = utcnow()
        for c in b.contents:
            if mode == "demo":
                r = next((r for r in b.revisions if r.id == c.public_revision_id), None)
            else:
                r = max((r for r in b.revisions if r.content_id == c.id),
                        key=lambda r: r.revision_number, default=None)
            if r is None:
                continue
            try:
                if mode == "demo":
                    exposure_gate(b, c, r, now, demo=True)
                else:
                    pilot_preview_gate(b, c, r, now)
                visible.append(projection(b, c, r, now, prefix="/" + mode))
            except GateError:
                continue
        visible.sort(key=lambda c: c["id"])
        visible.sort(key=lambda c: instant(c["published_at"] if mode == "demo" else c["verified_at"]), reverse=True)
        return visible, b

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nAllow: /\n" if public_shell else "User-agent: *\nDisallow: /\n"

    @app.get("/sitemap.xml")
    def sitemap():
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', media_type="application/xml")

    @app.get("/{path:path}", response_class=HTMLResponse)
    def page(request: Request, path: str):
        path = path.rstrip("/")
        if public_shell and (path == "demo" or path.startswith("demo/") or
                             path == "pilot" or path.startswith("pilot/")):
            return missing("public")
        mode = "demo" if path == "demo" or path.startswith("demo/") else (
            "pilot" if path == "pilot" or path.startswith("pilot/") else "public")
        if mode == "pilot" and pilot_token is None:
            return missing("public")
        route = path.removeprefix(mode).lstrip("/") if mode != "public" else path
        if route == "editorial-policy":
            return render("policy.html", mode=mode, title="편집·출처 정책", description="AIPATH의 검증·출처·정정 원칙")
        try:
            visible, b = snapshot(mode)
        except (ValueError, OSError, StopIteration):
            return render("state.html", mode=mode, status=503, title="잠시 콘텐츠를 제공할 수 없습니다",
                          description="콘텐츠 파일을 확인하고 있습니다. 잠시 후 다시 확인해 주세요.")
        if not route:
            return render("home.html", mode=mode, title="AI의 변화가, 나의 가능성으로", description="AI 소식을 이해하고, 내 일에 적용하고, 필요한 프롬프트를 가져가세요.", articles=visible)
        parts = route.split("/")
        section = next((key for key, value in SECTIONS.items() if value[0] == parts[0]), None)
        if section and len(parts) == 1:
            raw_page = request.query_params.get("page", "1")
            if not raw_page.isascii() or not raw_page.isdigit() or len(raw_page) > 6:
                return missing(mode)
            number = int(raw_page)
            articles = [c for c in visible if c["section"] == section]
            pages = max(1, (len(articles) + 11) // 12)
            if number < 1 or number > pages:
                return missing(mode)
            return render("listing.html", mode=mode, title=SECTIONS[section][1], description=SECTIONS[section][2],
                          current_section=section, articles=articles[(number-1)*12:number*12], number=number, pages=pages)
        if section and len(parts) == 2:
            article = next((c for c in visible if c["path"] == "/" + mode + "/" + route), None)
            if article:
                related = [c for c in visible if c["id"] in article["related_content_ids"]]
                return render("detail.html", mode=mode, title=article["title"], description=article["summary"],
                              article=article, related=related, current_section=section)
            # Only formerly visible fixtures get a generic 410; no title/body leaks.
            if mode == "demo" and b and any(c.section == section and c.slug == parts[1] and c.published_at for c in b.contents):
                return render("state.html", mode=mode, status=410, title="현재 제공하지 않는 콘텐츠입니다",
                              description="다른 콘텐츠를 살펴보세요.")
        return missing(mode)

    def missing(mode):
        return render("state.html", mode=mode, status=404, title="페이지를 찾을 수 없습니다",
                      description="주소를 확인하거나 홈에서 다시 시작해 주세요.")

    return app


app = create_app()
