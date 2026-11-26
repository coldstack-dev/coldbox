import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReadmeQuickstart(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(dir=ROOT))
        self.project = Path(scratch)
        self.config = self.project / 'coldbox.toml'

    def run_config_recipe(self):
        readme = (ROOT / 'README.md').read_text()
        _, _, local = readme.partition('### Run checks locally\n')
        blocks = local.split('### Set up GitHub Actions\n', 1)[0].split('```sh\n')
        self.assertEqual(len(blocks), 4)
        recipe = blocks[2].split('```', 1)[0]
        env = dict(os.environ, PATH=f"{ROOT}{os.pathsep}{os.environ['PATH']}",
                   COLDBOX_HOST_CHECKS=str(self.project / 'host-checks.toml'))
        return subprocess.run(['sh', '-c', recipe], cwd=self.project, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_existing_config_is_preserved_byte_for_byte(self):
        original = b'[commands]\nverify = "make check"\n'
        self.config.write_bytes(original)

        result = self.run_config_recipe()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('coldbox.toml already exists', result.stdout)
        self.assertEqual(self.config.read_bytes(), original)

    def test_failed_detection_leaves_project_empty(self):
        result = self.run_config_recipe()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('no checks detected', result.stderr)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_successful_detection_creates_config(self):
        (self.project / 'check.sh').write_text('#!/bin/sh\nexit 0\n')

        result = self.run_config_recipe()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tomllib.loads(self.config.read_text()), {'checks': {'lint': True}})
        self.assertEqual(sorted(path.name for path in self.project.iterdir()),
                         ['check.sh', 'coldbox.toml'])
