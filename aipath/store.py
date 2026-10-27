"""Local file snapshot. One locked, atomic metadata transaction per CLI operation."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .domain import Bundle, GateError, digest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = ROOT / ".runtime" / "demo"


def read_bundle(directory: Path) -> tuple[Bundle, str]:
    raw = (directory / "catalog.json").read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("revisions"), list):
        raise GateError("Invalid catalog structure")
    bodies = {}
    for revision in data["revisions"]:
        if not isinstance(revision, dict) or not isinstance(revision.get("body_file"), str):
            raise GateError("Revision body_file missing")
        relative = revision.pop("body_file")
        path = (directory / relative).resolve()
        if path.parent != (directory / "bodies").resolve() or path.suffix != ".md":
            raise GateError("body_file must be a Markdown file directly inside bodies/")
        body = path.read_text()
        bodies[relative] = body
        revision["body_markdown"] = body
    return Bundle.model_validate(data), digest({"metadata": raw.decode(), "bodies": bodies})


def write_bundle(directory: Path, bundle: Bundle):
    """Only metadata changes here; body revisions are immutable separate files."""
    Bundle.model_validate(bundle.model_dump())
    data = bundle.model_dump()
    for revision in data["revisions"]:
        body_file = f"bodies/{revision['id']}.md"
        body = revision.pop("body_markdown")
        if (directory / body_file).read_text() != body:
            raise GateError("Create a new Markdown revision before saving metadata")
        revision["body_file"] = body_file
    fd, name = tempfile.mkstemp(dir=directory, prefix=".catalog-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "catalog.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def transaction(directory: Path):
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        bundle, snapshot = read_bundle(directory)
        yield bundle
        _, latest = read_bundle(directory)
        if latest != snapshot:
            raise GateError("Files changed after validation; nothing committed")
        write_bundle(directory, bundle)
