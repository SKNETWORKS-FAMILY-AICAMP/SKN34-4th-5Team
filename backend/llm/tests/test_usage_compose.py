"""Run directly: python -m unittest discover -s backend/llm/tests -p test_usage_compose.py."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = {"USAGE_GUEST_TOKENS": "500000", "USAGE_MEMBER_MONTHLY_TOKENS": "999999000"}


class UsageComposeTest(unittest.TestCase):
    def test_prod_allowances_reach_settings_without_real_env(self):
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        required = set(re.findall(r"\$\{([A-Z_]+):\?", compose))
        settings = ast.parse((ROOT / "backend/config/settings.py").read_text())
        assignments = ast.Module(body=[node for node in settings.body if isinstance(node, ast.Assign)
                                      and any(isinstance(target, ast.Name) and target.id in DEFAULTS
                                              for target in node.targets)], type_ignores=[])
        example = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines()
                       if line.startswith(tuple(f"{name}=" for name in DEFAULTS)))
        self.assertEqual(example, DEFAULTS)
        # Copy only public Compose text, so Compose cannot discover the real root .env.
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "compose.yml"
            fixture = Path(directory) / "fixture.env"
            config.write_text(compose)
            for overrides, expected in (
                ({}, DEFAULTS),
                ({name: "" for name in DEFAULTS}, DEFAULTS),
                ({"USAGE_GUEST_TOKENS": "12345", "USAGE_MEMBER_MONTHLY_TOKENS": "67890"},
                 {"USAGE_GUEST_TOKENS": "12345", "USAGE_MEMBER_MONTHLY_TOKENS": "67890"}),
            ):
                with self.subTest(overrides=overrides):
                    fixture.write_text("\n".join(f"{name}={value}" for name, value in
                                               {**dict.fromkeys(required, "test-only"), **overrides}.items()))
                    result = subprocess.run(
                        ["docker", "compose", "--env-file", str(fixture), "-f", str(config),
                         "config", "--format", "json"], cwd=directory,
                        env={"PATH": os.environ.get("PATH", ""), "COMPOSE_DISABLE_ENV_FILE": "1"},
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    environment = json.loads(result.stdout)["services"]["backend"]["environment"]
                    actual = {name: environment[name] for name in DEFAULTS}
                    self.assertEqual(actual, expected)
                    namespace = {"os": os}
                    with patch.dict(os.environ, actual, clear=True):
                        exec(compile(assignments, "usage settings", "exec"), namespace)
                    self.assertEqual({name: namespace[name] for name in DEFAULTS},
                                     {name: int(value) for name, value in expected.items()})


class JevDeploymentTest(unittest.TestCase):
    def test_workflows_build_reader_and_preserve_deployment_steps(self):
        dev = (ROOT / ".github/workflows/deploy-dev.yml").read_text()
        prod = (ROOT / ".github/workflows/deploy-prod.yml").read_text()
        self.assertIn("--profile web-research up -d --build --renew-anon-volumes backend frontend jev-browser", dev)
        self.assertIn("--no-deps --force-recreate nginx", dev)
        self.assertIn("--profile web-research up -d --build backend frontend nginx minio jev-browser", prod)
        for workflow in (dev, prod):
            self.assertIn("exec -T nginx nginx -t", workflow)
            self.assertNotIn("down -v", workflow)
            self.assertNotIn("python3 -c", workflow)
            self.assertNotIn("config --format json |", workflow)

    def test_reader_config_without_real_env(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.env"
            for production in (False, True):
                with self.subTest(production=production):
                    names = ["docker-compose.prod.yml"] if production else ["docker-compose.yml", "docker-compose.dev.yml"]
                    texts = [(ROOT / name).read_text() for name in names]
                    required = set(re.findall(r"\$\{([A-Z_]+):\?", "\n".join(texts)))
                    # Isolated fixture replaces even the base service env_file; never read the real .env.
                    fixture.write_text("\n".join(f"{key}=test-only" for key in required)
                                       + "\nJEV_MCP_URL=http://jev-browser:8080/mcp\nWEB_RESEARCH_ENABLED=false\n")
                    command = ["docker", "compose", "--env-file", str(fixture), "--project-directory", str(ROOT)]
                    for index, text in enumerate(texts):
                        config = Path(directory) / f"compose-{index}.yml"
                        config.write_text(text.replace("- ./.env", f"- {fixture}"))
                        command += ["-f", str(config)]
                    command += ["--profile", "web-research", "config"]
                    environment = {"PATH": os.environ.get("PATH", ""), "COMPOSE_DISABLE_ENV_FILE": "1",
                                   "JEV_MCP_TOKEN": "test-shared-token", "OPENAI_API_KEY": "test-only"}

                    def resolve(options):
                        return subprocess.run(command + options, cwd=directory, env=environment,
                                              capture_output=True, text=True, timeout=30)

                    quiet = resolve(["--quiet"])
                    self.assertEqual(quiet.returncode, 0, quiet.stderr)
                    resolved = resolve(["--format", "json"])
                    self.assertEqual(resolved.returncode, 0, resolved.stderr)
                    services = json.loads(resolved.stdout)["services"]
                    reader, backend = services["jev-browser"], services["backend"]["environment"]
                    self.assertEqual(reader["profiles"], ["web-research"])
                    self.assertEqual(Path(reader["build"]["context"]), ROOT / "docker/jev-browser")
                    self.assertTrue((Path(reader["build"]["context"]) / "Dockerfile").is_file())
                    self.assertFalse(reader.get("ports"))
                    self.assertIn("NET_ADMIN", reader["cap_add"])
                    self.assertTrue(any("seccomp=" in value for value in reader["security_opt"]))
                    self.assertEqual(reader["shm_size"], "268435456")
                    self.assertEqual(reader["logging"]["options"], {"max-size": "10m", "max-file": "3"})
                    self.assertEqual(reader["environment"]["JEV_MCP_TOKEN"], backend["JEV_MCP_TOKEN"])
                    self.assertTrue(backend["JEV_MCP_TOKEN"].strip())
                    self.assertTrue({"backend", "frontend", "nginx", "minio", "qdrant"} <= services.keys())
                    self.assertEqual(backend["JEV_MCP_URL"], "http://jev-browser:8080/mcp")
                    self.assertEqual(backend["WEB_RESEARCH_ENABLED"], "false")
                    if production:
                        for key, value in {"RESEARCH_MODEL": "gpt-6-luna", "RESEARCH_BASE_URL": "https://api.openai.com/v1",
                                           "RESEARCH_API_KEY": ""}.items():
                            self.assertEqual(backend[key], value)
                        environment.update(JEV_MCP_URL="http://jev-browser:8080/custom", WEB_RESEARCH_ENABLED="true",
                                           RESEARCH_MODEL="custom-model", RESEARCH_BASE_URL="https://example.test/v1",
                                           RESEARCH_API_KEY="test-research-key")
                        custom = resolve(["--format", "json"])
                        self.assertEqual(custom.returncode, 0, custom.stderr)
                        custom_services = json.loads(custom.stdout)["services"]
                        for key in ("JEV_MCP_URL", "WEB_RESEARCH_ENABLED", "RESEARCH_MODEL", "RESEARCH_BASE_URL", "RESEARCH_API_KEY"):
                            self.assertEqual(custom_services["backend"]["environment"][key], environment[key])
                        self.assertEqual(custom_services["jev-browser"]["environment"]["TEXT_MODEL"], "custom-model")
                        self.assertEqual(custom_services["jev-browser"]["environment"]["TEXT_MODEL_BASE_URL"], "https://example.test/v1")
                        self.assertEqual(custom_services["jev-browser"]["environment"]["TEXT_MODEL_API_KEY"], "test-research-key")
                        environment["JEV_MCP_TOKEN"] = ""
                        self.assertNotEqual(resolve(["--quiet"]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
