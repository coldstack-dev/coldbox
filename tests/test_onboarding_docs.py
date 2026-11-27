import argparse
import contextlib
import io
import json
import subprocess
import unittest
import urllib.parse
from pathlib import Path

from test_coldbox import cb

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'release-check.json'
LEGACY_NAMESPACE = 'tay' + 'hcode'
SKIP_DIRS = {'.git', '.worktrees', '.venv', '__pycache__', '.pytest_cache'}


def is_excluded(rel_posix, excludes):
    for entry in excludes:
        trimmed = entry.rstrip('/')
        if rel_posix == trimmed or rel_posix.startswith(trimmed + '/'):
            return True
    return False


def tracked_files():
    result = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    paths = []
    for entry in result.stdout.split('\0'):
        if not entry:
            continue
        path = ROOT / entry
        if path.is_file():
            paths.append(path)
    return sorted(paths)


def shipped_files():
    excludes = json.loads(MANIFEST.read_text())['exclude']
    candidates = tracked_files()
    if candidates is None:
        candidates = [
            path for path in sorted(ROOT.rglob('*'))
            if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
        ]
    return [path for path in candidates if not is_excluded(path.relative_to(ROOT).as_posix(), excludes)]


class OnboardingDocs(unittest.TestCase):
    def test_shipped_files_contain_no_legacy_namespace(self):
        hits = []
        for path in shipped_files():
            text = path.read_text(errors='ignore')
            if LEGACY_NAMESPACE in text.lower():
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, [])

    def test_runtime_urls_use_coldstack_dev_namespace(self):
        self.assertIn('https://github.com/coldstack-dev/coldbox#autoscaling', cb.CI_HOSTS_HELP)
        self.assertNotIn(LEGACY_NAMESPACE, cb.CI_HOSTS_HELP.lower())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cb.setup_app(argparse.Namespace(org=None, app_id=None, key=None))
        self.assertEqual(code, 0)
        url = output.getvalue().split('Open ')[1].split()[0]
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(query['url'], 'https://github.com/coldstack-dev/coldbox')
        self.assertNotIn(LEGACY_NAMESPACE, output.getvalue().lower())

    def test_readme_onboarding_blocks_and_no_internal_coldfix_references(self):
        readme = (ROOT / 'README.md').read_text()
        what_you_get = readme.index('## What you get')
        safety_model = readme.index('## Safety model')
        self.assertLess(what_you_get, safety_model)
        intro_section = readme[:safety_model]
        self.assertIn('Needs Linux with /dev/kvm. No macOS or Windows.', intro_section)
        self.assertIn('"verdict"', intro_section)
        self.assertIn('"tests_ran"', intro_section)
        self.assertIn('"destroyed"', intro_section)
        self.assertIn(
            'No telemetry. Network: host setup downloads OS packages and (with opt-in) the Zabbly Incus '
            'repo key/packages; image builds download the VM base image and toolchain/runner packages; '
            'runner registration and CI use the GitHub API; running jobs can reach public IPv4 '
            'destinations unless run with `--offline`.',
            readme,
        )
        for name in ('README.md', 'CONTRIBUTING.md', 'docs/receipt.md'):
            with self.subTest(file=name):
                self.assertNotIn('coldfix', (ROOT / name).read_text().lower())

    def test_agents_md_and_setup_prompt_require_explicit_approval(self):
        excludes = json.loads(MANIFEST.read_text())['exclude']
        self.assertFalse(is_excluded('AGENTS.md', excludes))
        agents = (ROOT / 'AGENTS.md').read_text()
        lines = agents.splitlines()
        self.assertGreaterEqual(len(lines), 15)
        self.assertLessEqual(len(lines), 25)
        for token in ('prompts/0-setup.md', '--json', 'doctor', '--apply', '--yes'):
            with self.subTest(token=token):
                self.assertIn(token, agents)
        setup_prompt = (ROOT / 'prompts/0-setup.md').read_text()
        self.assertIn('ASK for my explicit approval before modifying host packages or running `sudo ~/coldbox/install.sh`', setup_prompt)
        self.assertIn('ASK for my explicit approval before starting a persistent runner service', setup_prompt)
        self.assertLess(
            setup_prompt.index('ASK for my explicit approval before starting a persistent runner service'),
            setup_prompt.index('`coldbox register OWNER/REPO --slots 0`'),
        )
        install_prompt = (ROOT / 'prompts/1-install.md').read_text()
        self.assertIn('ASK for my explicit approval before modifying host packages or running', install_prompt)
        register_prompt = (ROOT / 'prompts/3-register-runner.md').read_text()
        self.assertIn('ASK for my explicit approval before starting a persistent systemd runner service', register_prompt)
        uninstall_prompt = (ROOT / 'prompts/4-uninstall.md').read_text()
        self.assertIn('ASK for my explicit approval before unregistering runners, changing workflows, or running', uninstall_prompt)
        mutating = ('sudo ', 'coldbox register', 'coldbox unregister', '--apply', '--install-incus')
        for prompt in sorted((ROOT / 'prompts').glob('*.md')):
            text = prompt.read_text()
            positions = [text.index(term) for term in mutating if term in text]
            if positions:
                with self.subTest(prompt=prompt.name):
                    self.assertIn('ASK for my explicit approval', text)
                    self.assertLess(text.index('ASK for my explicit approval'), min(positions))


if __name__ == '__main__':
    unittest.main()
