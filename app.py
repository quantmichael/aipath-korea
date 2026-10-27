"""AIPATH 2.0 public entrypoint. Reads approved articles only when configured."""

from pathlib import Path

from aipath.web import create_app
from aipath.supabase_content import configured_reader

app = create_app(Path("/nonexistent-aipath-public-store"), public_shell=True,
                 public_reader=configured_reader())
