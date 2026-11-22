import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log(**fields):
    print(json.dumps(dict(host=os.uname().nodename, at=time.time(), **fields)), flush=True)


def interrupt(signum, _frame):
    if signum == signal.SIGALRM:
        raise TimeoutError('30-minute admission pause expired')
    raise InterruptedError(f'rollout interrupted by {signal.Signals(signum).name}')


@contextmanager
def admission_pause(cb):
    held = False
    try:
        with cb['host_lock']() as lock:
            held = True
            signal.setitimer(signal.ITIMER_REAL, 1800)
            log(admission_lock='held')
            yield lock
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if held:
            log(admission_lock='released')


def retired_worker(state, worker, lock):
    try:
        stat = (Path('/proc') / str(worker.pid) / 'stat').read_text()
    except FileNotFoundError:
        return True
    lock_stat = os.fstat(lock.fileno())
    lock_id = f'{os.major(lock_stat.st_dev):02x}:{os.minor(lock_stat.st_dev):02x}:{lock_stat.st_ino}'
    waited = [str(worker.pid), lock_id]
    if stat.rsplit(')', 1)[1].split()[0] != 'Z':
        if not any(fields[1:4] == ['->', 'FLOCK', 'ADVISORY'] and fields[5:7] == waited
                   for fields in map(str.split, Path('/proc/locks').read_text().splitlines())):
            return False
    try:
        receipt = json.loads((state / 'receipts' / f'{worker.name}.json').read_text())
    except FileNotFoundError:
        return False
    vm = receipt['vm']
    return receipt['id'] == worker.name and vm['destroyed'] is True and bool(vm['destroyed_at'])


def idle_inventory(cb, lock):
    state = cb['STATE']
    folder = state / 'workers'
    workers = cb['live_workers'](folder, prune=False)
    vms = cb['host_vms']() - cb['adhoc_vms']().keys()
    workers = [w for w in workers if w.kind != 'vm' and (w.name in vms or not retired_worker(state, w, lock))]
    if cb['unreadable_live_workers'](folder) or {w.name for w in workers} != vms:
        return None
    for worker in workers:
        if not cb['waiting'](worker):
            return None
        try:
            if b'Listening for Jobs' not in (state / 'receipts' / f'{worker.name}.log').read_bytes():
                return None
        except FileNotFoundError:
            return None
    return {w.name: (w.target, w.pid) for w in workers}


def warm_runners_idle(cb, github, idle):
    for target in {target for target, _pid in idle.values()}:
        runners = cb['listing'](github, f"{cb['scope'](target)}/actions/runners?", 'runners')
        online_idle = {r['name'] for r in runners if r['status'] == 'online' and not r['busy']}
        if any(owner == target and name not in online_idle for name, (owner, _pid) in idle.items()):
            return False
    return True


def active_units(cb):
    return {r['unit']: r['target'] for r in cb['registrations']() if r['active'] == 'active'}


def swap(cb, install, source, expected, prior, units, backup=None):
    github = cb['GitHub']()
    with TemporaryDirectory(dir=install.parent, prefix='.coldbox-rollout-') as folder:
        staged = Path(folder) / 'coldbox'
        shutil.copyfile(source, staged)
        staged.chmod(0o755)
        if digest(staged) != expected:
            raise RuntimeError('staged executable does not match the pinned SHA-256')
        with admission_pause(cb) as lock:
            while True:
                idle = idle_inventory(cb, lock)
                if idle is not None and warm_runners_idle(cb, github, idle):
                    if digest(install) != prior:
                        raise RuntimeError('installed executable changed during rollout')
                    active = active_units(cb)
                    if (backup is not None and active != units) or active.items() - units.items():
                        raise RuntimeError('active supervisor set changed during rollout')
                    if idle_inventory(cb, lock) == idle:
                        break
                time.sleep(3)
            if backup is not None:
                with backup.open('xb') as output, install.open('rb') as old:
                    shutil.copyfileobj(old, output)
                if digest(backup) != prior:
                    raise RuntimeError('backup does not match the installed SHA-256')
                if idle_inventory(cb, lock) != idle:
                    raise RuntimeError('idle runner inventory changed before the swap')
            log(running_jobs=0, prior=prior, expected=expected, supervisors=len(units))
            staged.replace(install)
            subprocess.run(['systemctl', 'restart', '--no-block', *units], check=True, timeout=30)


def check_build(install, expected, units):
    cb = runpy.run_path(str(install))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        builds = {cb['supervisor_build'](unit, target) for unit, target in units.items()}
        if active_units(cb) == units and builds == {expected[:12]} and digest(install) == expected:
            log(build=expected[:12], supervisors=len(units))
            return
        time.sleep(2)
    raise RuntimeError(f'could not confirm the expected build on all {len(units)} supervisors')


def main():
    parser = argparse.ArgumentParser(description='Swap Coldbox at zero active jobs and verify after releasing admission')
    parser.add_argument('host', help='local hostname expected by the operator')
    parser.add_argument('candidate', type=Path, help='trusted, staged Coldbox executable')
    parser.add_argument('sha256', help='full SHA-256 of the candidate executable')
    parser.add_argument('backup', type=Path, help='new path for the installed executable being replaced')
    args = parser.parse_args()
    if os.uname().nodename != args.host:
        parser.error('local hostname does not match the requested host')
    command = shutil.which('coldbox')
    if not command:
        parser.error('coldbox is not installed on PATH')
    install = Path(command).resolve()
    expected = args.sha256
    if not re.fullmatch(r'[0-9a-f]{64}', expected) or digest(args.candidate) != expected:
        parser.error('candidate does not match the pinned SHA-256')
    version = subprocess.check_output([sys.executable, str(args.candidate), '--version'], text=True, timeout=30)
    if expected[:12] not in version:
        parser.error('candidate did not report the pinned build')
    if args.backup.exists():
        parser.error('backup path already exists')
    cb = runpy.run_path(str(install))
    units = active_units(cb)
    if not units:
        parser.error('no active Coldbox supervisors')
    prior = digest(install)
    if prior == expected:
        parser.error('candidate is already installed')
    for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupt)
    try:
        swap(cb, install, args.candidate, expected, prior, units, args.backup)
        check_build(install, expected, units)
    except Exception as error:
        log(rollout='failed', error=str(error))
        current = digest(install)
        if current == expected:
            try:
                swap(cb, install, args.backup, prior, expected, units)
                check_build(install, prior, units)
            except Exception as rollback_error:
                log(rollback='failed', error=str(rollback_error))
            else:
                log(rollback='restored', build=prior[:12])
        elif current != prior:
            log(rollback='refused', error='installed SHA-256 changed after the swap')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
