"""Offline checks for the two source-upload boundaries of the Agent image."""

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentBuildContextTests(unittest.TestCase):
    def test_upload_and_docker_contexts_keep_runtime_inputs_and_exclude_secrets(self):
        required = (
            "Dockerfile.agent",
            "requirements.agent.txt",
            "agent_collect/shadow_job.py",
            "agent_collect/config/defaults.toml",
        )
        excluded = (
            ".env",
            "agent_collect/.env",
            "agent_collect/credentials.json",
            "agent_collect/secrets/service.key",
            "agent_collect/__pycache__/settings.pyc",
            "agent_collect/migrations/001_agent_foundation.sql",
            "api/opportunities.py",
            "tmp/raw.data",
        )
        # gcloudignore uses gitignore syntax. Dockerignore is checked with the
        # same sample paths; Docker's own parser still needs a real build check.
        for ignore_file in (".gcloudignore", ".dockerignore"):
            with self.subTest(ignore_file=ignore_file), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                (root / ".gitignore").write_text((ROOT / ignore_file).read_text())
                for name in (*required, *excluded):
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
                    result = subprocess.run(
                        ["git", "-C", str(root), "check-ignore", "-q", "--no-index", name],
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, name in excluded,
                                     f"{ignore_file}: unexpected context membership for {name}")

    def test_cloud_build_config_is_build_only_and_sha_parameterized(self):
        config = (ROOT / "cloudbuild.agent.yaml").read_text()
        self.assertIn("Dockerfile.agent", config)
        self.assertIn("${_IMAGE_TAG}", config)
        self.assertIn("asia-northeast3-docker.pkg.dev/aipath-511213/aipath-agent/aipath-agent-shadow", config)
        self.assertNotIn("gcloud run jobs", config)
        self.assertNotIn("--set-secrets", config)
        self.assertNotIn("--execute-now", config)


if __name__ == "__main__":
    unittest.main()
