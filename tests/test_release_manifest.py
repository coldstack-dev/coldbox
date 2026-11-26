import json
import shlex
import unittest
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / 'release-check.json'
PUBLIC_HISTORY_FINDINGS = [
    ('tailnet-ip', 'tests/test_coldbox.py', 'd72676dfa714edb2'),
]


class ReleaseManifest(unittest.TestCase):
    def test_quickstart_commands_match_documented_lines(self):
        quickstart = json.loads(MANIFEST.read_text())['quickstart']
        for example in quickstart:
            with self.subTest(command=example['argv']):
                lines = (MANIFEST.parent / example['file']).read_text().splitlines()
                number = example['line']
                self.assertIn(number, range(1, len(lines) + 1))
                self.assertEqual(shlex.split(lines[number - 1]), example['argv'])

    def test_public_mirror_history_findings_stay_approved(self):
        approvals = json.loads(MANIFEST.read_text())['approvals']
        approved = {(a['kind'], a['path'], a.get('fingerprint')) for a in approvals if a['scope'] == 'history'}
        for finding in PUBLIC_HISTORY_FINDINGS:
            with self.subTest(finding=finding):
                self.assertIn(finding, approved)

    def test_audit_closeouts_stay_out_of_the_public_export(self):
        excluded = json.loads(MANIFEST.read_text())['exclude']
        for closeout in ('docs/oss-audit-2026-45.md', 'docs/oss-audit-2026-47.md', 'docs/oss-audit-2026-47-tailnet-fixture.md'):
            with self.subTest(closeout=closeout):
                self.assertIn(closeout, excluded)


if __name__ == '__main__':
    unittest.main()
