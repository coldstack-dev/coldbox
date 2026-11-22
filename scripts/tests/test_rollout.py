import contextlib
import fcntl
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class Rollout(unittest.TestCase):
    def setUp(self):
        script = Path(__file__).resolve().parents[1] / 'rollout-coldbox.py'
        self.assertTrue(script.is_file(), 'the idle-only rollout command is missing')
        self.main = runpy.run_path(str(script))['main']
        root = self.enterContext(tempfile.TemporaryDirectory(dir='.'))
        self.root = Path(root).resolve()
        self.install, self.candidate, self.backup = (self.root / n for n in ('coldbox', 'candidate', 'before'))
        self.install.write_bytes(b'old')
        self.candidate.write_bytes(b'new')
        self.lock = self.root / 'autoscale.lock'
        self.prior = hashlib.sha256(b'old').hexdigest()
        self.expected = hashlib.sha256(b'new').hexdigest()
        self.units = {f'unit{i}': f'owner/repo{i}' for i in range(39)}
        self.checks = set()
        self.restarts = []
        self.blocked_builds = set()
        self.always_busy = False

    def host_lock(self):
        handle = self.lock.open('a+')
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    def held(self):
        with self.lock.open('a+') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    def cb(self, _path):
        def supervisor(unit, target):
            self.assertFalse(self.held(), 'supervisor startup needs the admission lock')
            self.assertEqual(target, self.units[unit])
            self.checks.add(unit)
            current = hashlib.sha256(self.install.read_bytes()).hexdigest()[:12]
            return None if current in self.blocked_builds else current

        return dict(STATE=self.root, host_lock=self.host_lock, live_workers=lambda *a, **k: [],
                    host_vms=lambda: {'busy'} if self.always_busy else set(),
                    adhoc_vms=dict, unreadable_live_workers=lambda _: [],
                    GitHub=object, supervisor_build=supervisor,
                    registrations=lambda: [dict(active='active', unit=u, target=t) for u, t in self.units.items()])

    def restart(self, args, *, check, timeout):
        self.assertTrue(self.held())
        self.assertEqual(args, ['systemctl', 'restart', '--no-block', *self.units])
        self.assertFalse(self.always_busy)
        self.restarts.append(self.install.read_bytes())

    def invoke(self, cap=None):
        clock = itertools.count(step=61)
        args = ['rollout', os.uname().nodename, str(self.candidate), self.expected, str(self.backup)]
        output = io.StringIO()
        real_timer = signal.setitimer
        handlers = {s: signal.getsignal(s) for s in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            with (patch.object(sys, 'argv', args), patch.object(runpy, 'run_path', self.cb),
                  patch('shutil.which', return_value=str(self.install)),
                  patch.object(subprocess, 'check_output', return_value=self.expected[:12]),
                  patch.object(subprocess, 'run', self.restart),
                  patch.object(time, 'monotonic', lambda: next(clock)),
                  patch.object(time, 'sleep', time.sleep if cap else lambda _: None),
                  patch.object(signal, 'setitimer', side_effect=lambda t, v: real_timer(t, cap if cap and v else v)),
                  contextlib.redirect_stdout(output)):
                rc = self.main()
        finally:
            for s, handler in handlers.items():
                signal.signal(s, handler)
        self.assertFalse(self.held())
        return rc, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_release_precedes_every_supervisor_check(self):
        rc, rows = self.invoke()
        self.assertEqual(rc, 0)
        self.assertEqual(self.restarts, [b'new'])
        self.assertEqual(self.checks, set(self.units))
        self.assertEqual([(r.get('admission_lock'), r.get('build')) for r in rows if 'admission_lock' in r or 'build' in r],
                         [('held', None), ('released', None), (None, self.expected[:12])])
        self.assertEqual(self.backup.read_bytes(), b'old')

    def test_failed_build_restores_actual_previous_executable(self):
        self.blocked_builds.add(self.expected[:12])
        rc, rows = self.invoke()
        self.assertEqual(rc, 1)
        self.assertEqual(self.restarts, [b'new', b'old'])
        self.assertEqual(self.install.read_bytes(), b'old')
        self.assertEqual([r['admission_lock'] for r in rows if 'admission_lock' in r],
                         ['held', 'released', 'held', 'released'])
        self.assertEqual(rows[-1]['rollback'], 'restored')

    def test_pause_deadline_releases_lock_without_swapping_busy_host(self):
        self.always_busy = True
        rc, rows = self.invoke(cap=0.05)
        self.assertEqual(rc, 1)
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.install.read_bytes(), b'old')
        self.assertFalse(self.backup.exists())
        self.assertIn('30-minute admission pause expired', rows[-1]['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
