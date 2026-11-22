#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import shlex
import signal
import subprocess
import time
from datetime import datetime, timezone

def write(path, value):
    staged = path.with_suffix('.new')
    staged.write_text(json.dumps(value, indent=2, allow_nan=False))
    staged.replace(path)


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def finalized(receipt):
    return 'destroyed_at' in receipt.get('vm', {})


def broken(receipt):
    no_tests = receipt.get('reason') and receipt.get('tests_ran') == 0
    return bool(receipt.get('error')) or receipt.get('verdict') == 'incomplete' and not no_tests


def successful_completions(jobs, window_start, window_end):
    """Count successes finishing in [window_start, window_end], regardless of start time."""
    return sum(job['outcome'] == 'success'
               and window_start <= job['completed_at'] <= window_end for job in jobs)


def qualified(window):
    return (window['count'] >= 30 and window['successful_completions'] >= 30 and window['seconds'] >= 1800
            and window['at_limit_samples'] >= 3 and not window['unsafe']
            and window.get('saturated_seconds', 0) >= window['seconds'] * .9
            and bool(window.get('job_mix')))


def decision(control, current, limit):
    if current['unsafe']:
        return 'STOP'
    if not qualified(control) or not qualified(current):
        return 'INCOMPLETE'
    if (current['cli'] != control['cli']
            or set(current['job_mix']) != set(control['job_mix'])
            or any(abs(current['job_mix'][key] - control['job_mix'][key]) > .05
                   for key in control['job_mix'])):
        return 'INCOMPLETE'
    if (current['failures'] / current['count'] > control['failures'] / control['count']
            or current['duration_p90'] > control['duration_p90'] * 1.2):
        return 'STOP'
    before = control['successful_completions'] / control['seconds']
    after = current['successful_completions'] / current['seconds']
    return 'PASS' if after > before * 1.05 else 'STOP'


def remote(args):
    cb = runpy.run_path(args.coldbox)
    if args.action == 'trial-cap':
        with cb['host_lock']():
            path = cb['STATE'] / 'ramp-cap.json'
            previous = path.read_text() if path.exists() else None
            cap = json.loads(previous) if previous is not None else None
            if cap and cap.get('expires') is not None and cap['expires'] <= time.time():
                cap = None
            if cap and cap.get('owner') != args.owner:
                raise ValueError('cap belongs to another run; preserve it')
            workers = cb['live_workers'](cb['STATE'] / 'workers')
            if workers or cb['host_load'](workers)[0]:
                return dict(applied=False, reason='live workers present; cap preserved')
            if args.limit is None:
                path.unlink(missing_ok=True)
            else:
                if (args.limit < 1 or not 1800 <= args.seconds <= 86400
                        or not re.fullmatch(r'[a-f0-9]{32}', args.owner)):
                    raise ValueError('invalid trial lease')
                if cap is None and not args.acquire:
                    raise ValueError('cap removed; trial aborted')
                cpus = max(0, os.sysconf('SC_NPROCESSORS_ONLN')-cb['host_setting'](cb['RESERVE'], 'CPU threads'))
                if not quiet(dict(cpus=cpus, load=list(os.getloadavg())), args):
                    return dict(applied=False, reason='live load exceeds gate')
                write(path, dict(limit=args.limit, owner=args.owner,
                                 expires=time.time()+args.seconds+21600+300))
        return dict(applied=True)
    if args.action not in ('sample', 'jobs'):
        raise ValueError('only sample and jobs actions are supported')
    if args.action == 'jobs':
        if cb['app_token'](f'repos/{args.repo}/actions/runs/{args.run}/jobs') is None:
            raise ValueError('installed GitHub App required')
        client = cb['GitHub']()
        path = f'repos/{args.repo}/actions/runs/{args.run}/attempts/{args.attempt}/jobs?'
        jobs = cb['listing'](client, path, 'jobs')
        return dict(jobs=jobs, requests=len(client.used))
    proc = Path('/proc')
    cpu = [int(x) for x in (proc / 'stat').read_text().splitlines()[0].split()[1:9]]
    def psi(resource):
        rows = (proc / 'pressure' / resource).read_text().splitlines()
        return {row.split()[0]: float(row.split()[1].split('=')[1]) for row in rows}
    cap_path = cb['STATE'] / 'ramp-cap.json'
    cap = json.loads(cap_path.read_text()) if cap_path.exists() else None
    workers = cb['live_workers'](cb['STATE'] / 'workers')
    busy, warm = cb['host_load'](workers)
    vmstat = dict(line.split() for line in (proc / 'vmstat').read_text().splitlines())
    active = []
    for worker in workers:
        log = cb['STATE'] / 'receipts' / f'{worker.name}.log'
        if log.exists():
            content = log.read_bytes()
            if cb['running_job'](content):
                active.append(worker.name)
    receipts = []
    for path in (cb['STATE'] / 'receipts').glob('*.json'):
        if path.stat().st_mtime >= args.since:
            receipt = json.loads(path.read_text())
            if receipt.get('mode') == 'runner' and receipt.get('verdict') != 'idle':
                receipts.append(receipt)
    return dict(at=time.time(), cpu=cpu, load=list(os.getloadavg()), cpus=max(0, os.sysconf('SC_NPROCESSORS_ONLN')-cb['host_setting'](cb['RESERVE'], 'CPU threads')), busy=busy, warm=warm,
                available=cb['host_memory']('MemAvailable'), total=cb['host_memory']()-cb['additional_vm_memory'](workers, cb['vm_memory']()),
                reserve=cb['HOST_RESERVE'], memory=cb['vm_memory'](),
                cpu_psi=psi('cpu'), memory_psi=psi('memory'), oom=int(vmstat['oom_kill']),
                cli=hashlib.sha256(Path(args.coldbox).read_bytes()).hexdigest(),
                cap=cap, active=active, cap_supported='ramp_room' in cb, receipts=receipts)


def call(args, host, action, *extra):
    command = ['sudo', '-n', 'python3', args.remote_script, '--coldbox', args.coldbox,
               action, *map(str, extra)]
    if action == 'trial-cap':
        command.extend(['--owner', args.owner])
    result = subprocess.run(['ssh', '-oBatchMode=yes', '-oConnectTimeout=10', host, shlex.join(command)],
                            check=True, capture_output=True, text=True, timeout=120)
    return json.loads(result.stdout)


def measure(args, host, folder, limit=None):
    folder.mkdir()
    start = time.time()
    previous = None
    unsafe = False
    receipts = {}
    samples = []
    cutoff = None
    pending = set()
    while True:
        sample = call(args, host, 'sample', '--since', start)
        if previous is None:
            start = sample['at']
        if limit is not None and getattr(args, 'trials', False) is True:
            cap = sample['cap']
            if (not cap or cap['limit'] != limit or cap['owner'] != args.owner
                    or cap['expires'] is None or cap['expires'] <= sample['at']):
                raise ValueError('trial cap absent, changed or expired')
            if sample['busy'] > limit:
                unsafe = True
        if previous:
            delta = [b - a for a, b in zip(previous['cpu'], sample['cpu'])]
            if min(delta) < 0 or sum(delta) <= 0:
                raise ValueError('invalid CPU counters')
            sample['cpu_percent'] = 100 * (1 - delta[3] / sum(delta))
            unsafe |= sample['cpu_psi']['some'] >= 50 and previous['cpu_psi']['some'] >= 50
            unsafe |= sample['oom'] != samples[0]['oom']
        unsafe |= sample['available'] < sample['reserve'] or sample['memory_psi']['full'] >= 1
        for receipt in sample.pop('receipts'):
            receipts[receipt['id']] = receipt
        for receipt in receipts.values():
            unsafe |= bool(receipt.get('error')) or (
                broken(receipt) and (finalized(receipt) or receipt['id'] not in sample['active']))
        with (folder / 'samples.jsonl').open('a') as out:
            out.write(json.dumps(sample) + '\n')
        write(folder / 'receipts.json', list(receipts.values()))
        samples.append(sample)
        previous = sample
        if unsafe:
            break
        if cutoff is None and sample['at'] - start >= args.seconds:
            cutoff = sample['at']
            pending = set(sample['active'])
        if cutoff is not None:
            pending -= {key for key, receipt in receipts.items() if finalized(receipt)}
            if not pending:
                break
            if sample['at'] - cutoff >= 21600:
                unsafe = True
                break
        time.sleep(30)
    cutoff = cutoff or samples[-1]['at']
    cache = args.output / 'jobs-cache'
    cache.mkdir(exist_ok=True)
    matched = {}
    requests = 0
    failures = 0
    for receipt in receipts.values():
        if not finalized(receipt):
            if broken(receipt):
                unsafe = True
            continue
        if broken(receipt):
            unsafe = True
        github = receipt.get('github')
        if not github:
            unsafe = True
            continue
        repo, run = github.get('repo'), str(github.get('run_id'))
        attempt = str(github.get('run_attempt') or 1)
        if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not run.isdigit() or not attempt.isdigit():
            unsafe = True
            continue
        key = hashlib.sha256(f'{repo}/{run}/{attempt}'.encode()).hexdigest()
        path = cache / f'{key}.json'
        cached = json.loads(path.read_text())['jobs'] if path.exists() else []
        if not any(j.get('runner_name') == receipt['id'] and j.get('status') == 'completed' for j in cached):
            response = call(args, host, 'jobs', '--repo', repo, '--run', run, '--attempt', attempt)
            requests += response['requests']
            write(path, response)
        jobs = json.loads(path.read_text())['jobs']
        matches = [job for job in jobs if job.get('runner_name') == receipt['id']
                   and job.get('status') == 'completed']
        if len(matches) != 1:
            unsafe = True
            continue
        job = matches[0]
        created, started, ended = [datetime.fromisoformat(job[k].replace('Z', '+00:00')).timestamp()
                                   for k in ('created_at', 'started_at', 'completed_at')]
        if (not (start <= started < cutoff or start <= ended <= cutoff)
                or not started <= ended <= samples[-1]['at'] or created > started):
            continue
        matched[job['id']] = dict(mix=json.dumps([repo, job.get('workflow_name'), job.get('name'), job.get('labels')], sort_keys=True), created_at=created, started_at=started, completed_at=ended,
                                  outcome=job['conclusion'], wait=started-created, duration=ended-started,
                                  failure=job['conclusion'] not in ('success', 'skipped', 'cancelled'))
    mix = {}
    for job in matched.values():
        mix[job['mix']] = mix.get(job['mix'], 0) + 1 / len(matched)
    saturated = sum(min(after['at'], cutoff) - before['at']
                    for before, after in zip(samples, samples[1:])
                    if before['at'] < cutoff and after['at'] - before['at'] <= 60
                    and limit is not None and before['busy'] >= limit and after['busy'] >= limit)
    failures = sum(job['failure'] for job in matched.values())
    successful = successful_completions(matched.values(), start, cutoff)
    result = dict(saturated_seconds=saturated, job_mix=mix, successful_completions=successful, count=len(matched), receipts=len(receipts), requests=requests, unsafe=unsafe,
                  cutoff=cutoff, seconds=cutoff-start, completions_per_hour=successful*3600/max(1, cutoff-start), at_limit_samples=sum(
                  limit is not None and s['busy'] >= limit for s in samples[1:] if s['at'] <= cutoff), peak_busy=max(s['busy'] for s in samples), failures=failures,
                  cli=samples[-1]['cli'], hardware_ceiling=min(samples[-1]['cpus'],
                  (samples[-1]['total']-samples[-1]['reserve']) // samples[-1]['memory']))
    if len({s['cli'] for s in samples}) != 1:
        result['unsafe'] = True
    for field in ('wait', 'duration'):
        for p in (50, 90):
            result[f'{field}_p{p}'] = percentile([j[field] for j in matched.values()], p/100) if matched else None
    write(folder / 'matched.json', matched)
    write(folder / 'summary.json', result)
    return result



def quiet(sample, args):
    threshold = getattr(args, 'max_load', None)
    if not isinstance(threshold, (int, float)):
        threshold = sample.get('cpus', 1) * .5
    return max(sample.get('load', [float('inf')])) < threshold


def measure_only(args):
    for host in args.host:
        result = measure(args, host, args.output / f'{host}-measurement')
        samples = [json.loads(line) for line in
                   (args.output / f'{host}-measurement' / 'samples.jsonl').read_text().splitlines()]
        levels = {}
        for before, after in zip(samples, samples[1:]):
            if before['at'] >= result['cutoff']:
                break
            key = str(before['busy'])
            levels[key] = levels.get(key, 0) + min(after['at'], result['cutoff']) - before['at']
        result['exposure_seconds_by_level'] = levels
        jobs = json.loads((args.output / f'{host}-measurement' / 'matched.json').read_text())
        result['levels'] = {}
        for level, exposure in levels.items():
            cohort = [job for job in jobs.values() if str(next(
                (sample['busy'] for sample in reversed(samples)
                 if sample['at'] < result['cutoff'] and sample['at'] <= job['completed_at']), -1)) == level]
            result['levels'][level] = dict(
                exposure_seconds=exposure, jobs=cohort,
                completions_per_hour=successful_completions(cohort, samples[0]['at'], result['cutoff'])*3600/max(1, exposure),
                queue_p90=percentile([j['wait'] for j in cohort], .9) if cohort else None,
                duration_p90=percentile([j['duration'] for j in cohort], .9) if cohort else None)
        write(args.output / f'{host}-report.json', result)
        print(json.dumps(result, allow_nan=False))


def capacity_sample(cb, ready_workers):
    state = cb['STATE']
    with cb['host_lock']():
        folder = state / 'workers'
        workers = cb['live_workers'](folder, prune=False)
        if cb['unreadable_live_workers'](folder):
            raise ValueError('cannot measure commitments with unreadable live workers')
        ready_workers.intersection_update(worker.name for worker in workers)
        ready = state / 'ready'
        ready_workers.update(worker.name for worker in workers if (ready / worker.name).exists())
        memory, host = cb['vm_memory'](), cb['host_memory']()
        sizes = {worker.name: cb['vm_bytes'](worker, memory) for worker in workers}
        vms = cb['host_vms']()
        untracked = vms - sizes.keys()
        builders = sum(worker.kind == 'vm' for worker in workers)
        if untracked:
            builders += len(untracked.intersection(cb['adhoc_vms']()))
        extra = cb['additional_vm_memory'](workers, memory, vms)
        pending = 0
        for worker in workers:
            if worker.name in ready_workers:
                continue
            if (ready / worker.name).exists():
                ready_workers.add(worker.name)
                continue
            try:
                receipt = json.loads((state / 'receipts' / f'{worker.name}.json').read_text())
            except FileNotFoundError:
                pass
            else:
                if 'ready_seconds' in receipt.get('vm', {}):
                    ready_workers.add(worker.name)
                    continue
                if worker.name not in vms and finalized(receipt) and receipt['vm']['destroyed']:
                    continue
            pending += 1
        space = cb['query'](f"/1.0/storage-pools/{cb['POOL']}/resources")['space']
        disk = cb['vm_disk']()
        if disk <= 0 or not 0 <= space['used'] <= space['total'] or space['total'] <= 0:
            raise ValueError('cannot measure disk commitments with invalid pool capacity')
        cpus = cb['host_cpus']() - cb['host_setting'](cb['RESERVE'], 'CPU threads')
        budgets = dict(cpu=[(len(workers) + len(untracked)) * cb['CORES_PER_JOB'], cpus],
                       ram=[sum(sizes.values()) + len(untracked) * memory + extra, host - cb['host_share'](host)],
                       disk=[space['used'] + pending * disk, space['total'] - space['total'] // 10])
        queue = state / 'receipts' / 'submissions' / 'queue.json'
        try:
            queued = json.loads(queue.read_text())
        except FileNotFoundError:
            queued = []
        admitted = {worker.name: (folder / str(worker.pid)).stat().st_mtime_ns for worker in workers
                    if worker.kind == 'once' and (state / 'receipts' / f'{worker.name}.submit.log').is_file()}
        return dict(at=time.time(), build=cb['build_id'](loaded=True), budgets=budgets,
                    available=cb['host_memory']('MemAvailable'), floor=cb['memory_floor'](host),
                    workers=sizes, builder_vms=builders, direct_admitted=admitted, direct_queued=queued)


def capacity_summary(samples):
    return dict(samples=len(samples), builds=sorted({sample['build'] for sample in samples} - {None}),
                build_changed=any(sample['build'] != samples[0]['build'] or not sample['build'] for sample in samples),
                over_admissions=sum(any(used > limit for used, limit in sample['budgets'].values())
                                    for sample in samples),
                floor_violations=sum(sample['available'] < sample['floor'] for sample in samples),
                peak_builder_vms=max(sample['builder_vms'] for sample in samples),
                min_floor_margin_bytes=min(sample['available'] - sample['floor'] for sample in samples),
                peak={resource: max((sample['budgets'][resource] for sample in samples), key=lambda pair: pair[0])
                      for resource in ('cpu', 'ram', 'disk')})


def burst_summary(samples, receipts, cohort, sizes, job_size):
    direct = cohort['direct']
    jobs = cohort['jobs']
    repo, sha = cohort['repo'], cohort['sha']
    if (len(direct) < 2 or len(set(direct)) != len(direct)
            or len({job['id'] for job in jobs}) != len(jobs)
            or len({job.get('runner_name') for job in jobs if job.get('runner_name')})
            != sum(bool(job.get('runner_name')) for job in jobs)):
        raise ValueError('expected an ordered list of distinct submissions and distinct Actions jobs')
    receipts = {receipt['id']: receipt for receipt in receipts
                if receipt.get('repo') == repo and receipt.get('sha') == sha}
    job_sizes = {}
    terminal = 0
    for job in jobs:
        size = job_size(job['labels'])
        if size is None:
            raise ValueError('each Actions job needs compatible Coldbox runner labels')
        name = job.get('runner_name')
        receipt = receipts.get(name, {})
        github = receipt.get('github') or {}
        if (job['status'] == 'completed' and receipt.get('mode') == 'runner' and github.get('job_id') == job['id']
                and finalized(receipt) and receipt['vm']['destroyed']):
            terminal += 1
        if name and any(sample['workers'].get(name) == sizes[size] for sample in samples):
            job_sizes[name] = size
    terminal += sum(receipts.get(name, {}).get('mode') == 'local' and finalized(receipts[name])
                    and receipts[name]['vm']['destroyed'] for name in direct)
    stamps = {}
    for sample in samples:
        for name, stamp in sample['direct_admitted'].items():
            stamps.setdefault(name, stamp)
    observed = sorted((name for name in direct if name in stamps), key=stamps.get)
    waiting = [[request['id'] for request in sample['direct_queued']
                if request['repo'] == repo and request['sha'] == sha] for sample in samples]
    requested = set(direct) | {job.get('runner_name') for job in jobs}
    unlisted = sorted((set(receipts) | {name for queue in waiting for name in queue}) - requested)
    fifo = (observed == direct and len({stamps[name] for name in observed}) == len(direct)
            and any(len(queue) >= 2 for queue in waiting) and not unlisted
            and all(queue == [name for name in direct if name in queue] for queue in waiting))
    overlap = any(set(sample['workers']).intersection(direct) and set(sample['workers']).intersection(job_sizes)
                  for sample in samples)
    return dict(requests=len(jobs) + len(direct), non_terminal=len(jobs) + len(direct) - terminal,
                unlisted=unlisted,
                actions_sizes=sorted(set(job_sizes.values())), mixed_overlap=overlap,
                direct_acceptance_order=direct, direct_admission_order=observed, fifo=fifo)


def supervisor_builds(cb):
    return {record['unit']: cb['supervisor_build'](record['unit'], record['target'])
            for record in cb['registrations']() if record['active'] == 'active'}


def audit(args):
    cb = runpy.run_path(args.coldbox)
    args.output.mkdir(parents=True)
    supervisors = supervisor_builds(cb) if not args.burst else {}
    samples = []
    ready_workers = set()
    deadline = time.monotonic() + args.seconds
    with (args.output / 'samples.jsonl').open('x') as out:
        while True:
            try:
                sample = capacity_sample(cb, ready_workers)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report = dict(host=os.uname().nodename, status='INCOMPLETE', samples=len(samples),
                              sample_error=str(error))
                write(args.output / 'summary.json', report)
                print(json.dumps(report, allow_nan=False))
                return 1
            samples.append(sample)
            out.write(json.dumps(sample, allow_nan=False) + '\n')
            out.flush()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or args.burst and args.burst.is_file():
                break
            time.sleep(min(2 if args.burst else 30, remaining))
    start, end = samples[0]['at'], samples[-1]['at']
    report = dict(host=os.uname().nodename, start=start, end=end, **capacity_summary(samples))
    complete = len(samples) > 1 and not any(report[key] for key in (
        'build_changed', 'over_admissions', 'floor_violations'))
    if args.burst:
        if not args.burst.is_file():
            raise ValueError('mixed burst evidence never arrived')
        receipts, unreadable = cb['read_receipts']([cb['STATE'] / 'receipts'], start, end)
        sizes = {size: cb['vm_shape'](size)[0] or cb['vm_memory']() for size in cb['SIZES']}
        report.update(burst_summary(samples, receipts, json.loads(args.burst.read_text()), sizes, cb['job_size']),
                      unreadable_receipts=unreadable)
        report['passed'] = (complete and report['fifo'] and report['mixed_overlap']
                            and report['actions_sizes'] == sorted(sizes) and not (unreadable or report['non_terminal']))
        passed = report['passed']
    else:
        end_supervisors = supervisor_builds(cb)
        passed = (complete and bool(supervisors) and supervisors == end_supervisors
                  and set(supervisors.values()) == {samples[0]['build']})
        report.update(capacity_passed=passed, supervisor_builds=supervisors, final_supervisor_builds=end_supervisors)
    write(args.output / 'summary.json', report)
    print(json.dumps(report, allow_nan=False))
    return 0 if passed else 1


def trial_run(args):
    def interrupted(signum, frame):
        raise InterruptedError(f'signal {signum}')
    old_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    args.owner = os.urandom(16).hex()
    try:
        for host in args.host:
            report = dict(status='INCOMPLETE', measured_cap=None, trials=[], configured=False)
            acquired = False
            acquisition_attempted = False
            try:
                initial = call(args, host, 'sample', '--since', time.time())
                cap = initial['cap']
                active_cap = cap and (cap.get('expires') is None or cap['expires'] > initial['at'])
                if not quiet(initial, args) or active_cap or not initial['cap_supported']:
                    report.update(status='BLOCKED', reason='load gate, existing cap or unsupported CLI')
                    continue
                ceiling = min(initial['cpus'], (initial['total']-initial['reserve']) // initial['memory'])
                if args.limits[-1] > ceiling:
                    report.update(status='BLOCKED', reason='requested trial exceeds measured hardware ceiling')
                    continue
                previous = None
                for limit in args.limits:
                    deadline = time.monotonic() + 900
                    while True:
                        extra = ['--limit', limit, '--seconds', args.seconds]
                        if not acquired:
                            extra.append('--acquire')
                        if getattr(args, 'max_load', None) is not None:
                            extra.extend(['--max-load', args.max_load])
                        acquisition_attempted = True
                        response = call(args, host, 'trial-cap', *extra)
                        acquisition_attempted = False
                        if response['applied']:
                            acquired = True
                            break
                        if time.monotonic() >= deadline:
                            raise ValueError(response['reason'])
                        time.sleep(30)
                    window = measure(args, host, args.output / f'{host}-limit-{limit}', limit)
                    verdict = decision(previous, window, limit) if previous else (
                        'PASS' if qualified(window) else 'INCOMPLETE')
                    report['trials'].append(dict(limit=limit, verdict=verdict, summary=window))
                    if verdict == 'STOP':
                        report.update(status='STOP', reason='throughput plateau/degradation or safety regression')
                        if previous and qualified(window) and window['cli'] == previous['cli']:
                            report['measured_cap'] = report['trials'][-2]['limit']
                        break
                    if verdict == 'INCOMPLETE':
                        break
                    previous = window
                else:
                    report['reason'] = 'all requested limits improved; plateau not observed'
            except InterruptedError as error:
                report.update(status='INCOMPLETE', measured_cap=None, reason=str(error))
                return
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                report.update(status='INCOMPLETE', measured_cap=None, reason=str(error))
            finally:
                if acquired or acquisition_attempted:
                    try:
                        report['cleanup'] = call(args, host, 'trial-cap')
                    except (ValueError, OSError, subprocess.SubprocessError) as error:
                        report['cleanup'] = dict(applied=False, reason=str(error))
                write(args.output / f'{host}-report.json', report)
                print(json.dumps(report, allow_nan=False))
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def run(args):
    if getattr(args, 'max_load', None) is not None and isinstance(args.max_load, (int, float)) and (not math.isfinite(args.max_load) or args.max_load <= 0):
        raise ValueError('max load must be finite and positive')
    if not 1800 <= args.seconds <= 86400:
        raise ValueError('each window must be between 1800 and 86400 seconds')
    if getattr(args, 'trials', False) and (not args.limits or len(args.limits) < 2
            or args.limits[0] < 1 or args.limits != sorted(set(args.limits))):
        raise ValueError('trials require at least two increasing positive limits')
    if args.output.exists() and any(args.output.glob('*-report.json')):
        raise ValueError('output already contains reports; use a fresh directory')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (args.output / 'started.json').exists():
            raise ValueError('output already started; use a fresh directory')
        write(args.output / 'started.json', dict(hosts=args.host, at=time.time()))
        if getattr(args, 'trials', False):
            trial_run(args)
        else:
            measure_only(args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--coldbox', default='/usr/local/bin/coldbox')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('sample').add_argument('--since', type=float, required=True)
    cap = sub.add_parser('trial-cap')
    cap.add_argument('--limit', type=int)
    cap.add_argument('--seconds', type=int, default=1800)
    cap.add_argument('--owner', required=True)
    cap.add_argument('--acquire', action='store_true')
    cap.add_argument('--max-load', type=float)
    jobs = sub.add_parser('jobs')
    for name in ('repo', 'run', 'attempt'):
        jobs.add_argument('--' + name, required=True)
    runner = sub.add_parser('run')
    runner.add_argument('--trials', action='store_true', help='explicitly authorize temporary idle-only limit trials')
    runner.add_argument('--limits', nargs='+', type=int, help='increasing distinct trial limits')
    runner.add_argument('--max-load', type=float, help='gate threshold; defaults to half the available CPUs')
    runner.add_argument('--host', action='append', required=True)
    runner.add_argument('--remote-script', required=True)
    runner.add_argument('--seconds', type=int, default=1800)
    runner.add_argument('--output', type=Path, required=True)
    observer = sub.add_parser('audit', help='check admission commitments and request completion on this host')
    observer.add_argument('--seconds', type=int, default=86400)
    observer.add_argument('--output', type=Path, required=True)
    observer.add_argument('--burst', type=Path, help='path to the mixed burst jobs and submission IDs')
    args = parser.parse_args()
    if args.action == 'run':
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', h) for h in args.host):
            parser.error('hosts must be SSH names')
        run(args)
    elif args.action == 'audit':
        if not 0 < args.seconds <= 86400:
            parser.error('audit window must be between 1 and 86400 seconds')
        return audit(args)
    else:
        print(json.dumps(remote(args), allow_nan=False))


if __name__ == '__main__':
    raise SystemExit(main())
