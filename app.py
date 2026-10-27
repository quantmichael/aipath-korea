"""Public, content-empty AIPATH 2.0 entrypoint for Vercel.

No fixture or draft store is packaged into the deployment. Publishing
real articles requires a separately reviewed release implementation.
"""

from pathlib import Path

from aipath.web import create_app

app = create_app(Path("/nonexistent-aipath-public-store"), public_shell=True)
