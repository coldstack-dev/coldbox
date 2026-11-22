#!/usr/bin/python3
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


TARGET = "example/recovery-matrix"
REPO = f"repos/{TARGET}"
HEAD, TREE, BASE = ("a" * 40, "b" * 40, "c" * 40)
STATE = Path("/var/lib/coldbox")
RECEIPTS = STATE / "receipts"
FIXTURE = STATE / "restart-matrix"
REGISTRATION = FIXTURE / "runner.json"
CHECKS = FIXTURE / "checks.jsonl"
UNIT = "coldbox-runner@example-recovery\\x2dmatrix.service"
REAPER = "coldbox-reap.service"


def command(*args):
    return subprocess.check_output(args, text=True, timeout=180).strip()


def read_json(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def setup():
    require(not Path("/etc/coldbox/github-app.pem").exists(), "prepare a keyless guest")
    require(not Path("/etc/coldbox/github-app.env").exists(), "prepare a keyless guest")
    FIXTURE.mkdir(exist_ok=False)
    os.chown(FIXTURE, STATE.stat().st_uid, STATE.stat().st_gid)
    Path(__file__).with_name("gh").symlink_to(Path(__file__).name)
    environment = (
        "[Service]\n"
        f"Environment=PATH={Path(__file__).parent}:/usr/local/bin:/usr/bin:/bin\n"
        "Environment=COLDBOX_ALLOW_" "PERSONAL_LOGIN=1\n"
    )
    for unit in (UNIT, REAPER):
        dropin = Path("/etc/systemd/system") / f"{unit}.d" / "restart-matrix.conf"
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(environment + ("Restart=no\n" if unit == UNIT else ""))
    runner = Path("/etc/coldbox/guest/opt/actions-runner/run.sh")
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        '#!/bin/sh\nset -eu\nmkdir -p "$COLDBOX_JOB_DIR/artifacts"\n'
        'echo "test artifact" >"$COLDBOX_JOB_DIR/artifacts/fixture.txt"\n'
        'echo "Running job: verify"\nexec sleep 900\n'
    )
    runner.chmod(0o755)
    command("systemctl", "daemon-reload")


def github(args):
    if args == ["auth", "token"]:
        return 1
    if args == ["api", "-X", "POST", f"{REPO}/actions/runners/generate-jitconfig", "--input", "-"]:
        body = json.load(sys.stdin)
        staged = REGISTRATION.with_suffix(".new")
        staged.write_text(json.dumps(dict(name=body["name"], id=27)))
        staged.replace(REGISTRATION)
        reply = dict(encoded_jit_config="fixture", runner=dict(id=27))
    elif args == ["api", "-X", "POST", f"{REPO}/check-runs", "--input", "-"]:
        body = json.load(sys.stdin)
        with CHECKS.open("a") as log:
            log.write(json.dumps(body) + "\n")
            log.flush()
            os.fsync(log.fileno())
        reply = dict(id=51)
    elif args == ["api", "-X", "DELETE", f"{REPO}/actions/runners/27"]:
        return 0
    elif len(args) == 2 and args[0] == "api":
        path = args[1]
        if path == REPO:
            reply = dict(full_name=TARGET, visibility="private")
        elif path == f"{REPO}/actions/runs?event=pull_request&per_page=100":
            reply = dict(workflow_runs=[dict(id=9, updated_at="9999-12-31T00:00:00+00:00")])
        elif path == f"{REPO}/actions/runs/9/attempts/1/jobs?per_page=100&page=1":
            runner = read_json(REGISTRATION)
            reply = dict(jobs=[dict(name="verify", runner_name=runner["name"], runner_id=runner["id"],
                                    run_id=9, head_sha=HEAD, status="completed", conclusion="cancelled")])
        elif path == f"{REPO}/actions/runs/9":
            reply = dict(event="pull_request", path=".github/workflows/verify.yml", head_sha=HEAD,
                         pull_requests=[dict(base=dict(sha=BASE))])
        elif path == f"{REPO}/git/commits/{HEAD}":
            reply = dict(tree=dict(sha=TREE))
        elif path in (f"{REPO}/git/trees/{TREE}", f"{REPO}/git/trees/{BASE}"):
            reply = dict(tree=[])
        else:
            raise SystemExit(f"unexpected fixture API path: {path}")
    else:
        raise SystemExit("unexpected fixture gh invocation")
    print(json.dumps(reply))
    return 0


def job_vms():
    return [name for name in command("incus", "--force-local", "--project", "coldbox", "list",
                                     "-c", "n", "-f", "csv").splitlines() if name.startswith("cbx-")]


def receipt(name):
    return read_json(RECEIPTS / f"{name}.json")


def start():
    require(not job_vms(), "unexpected job VM before trial")
    REGISTRATION.unlink(missing_ok=True)
    command("systemctl", "start", UNIT)
    for _ in range(150):
        if REGISTRATION.is_file():
            name = read_json(REGISTRATION)["name"]
            log = RECEIPTS / f"{name}.log"
            if log.is_file() and "Running job: verify" in log.read_text():
                print(name)
                return
        time.sleep(2)
    print(command("journalctl", "-u", UNIT, "-n", "40", "--no-pager"), file=sys.stderr)
    raise SystemExit("the fixture job never started")


def interrupt(name, fault):
    supervisor = int(command("systemctl", "show", UNIT, "-p", "MainPID", "--value"))
    require(supervisor > 1, "the fixture supervisor disappeared before the fault")
    before = receipt(name)
    require(before.get("job_started_at") and not before["vm"]["destroyed"] and "job_ended_at" not in before,
            "the receipt does not describe a running job")
    require(job_vms() == [name], "expected exactly one job VM before the fault")
    workers = [(int(path.name), fields[1]) for path in (STATE / "workers").iterdir()
               if path.name.isdecimal() and (fields := read_json(path))[2] == name]
    require(len(workers) == 1, "could not identify the running fixture worker")
    worker, started = workers[0]
    fd = os.pidfd_open(worker)
    try:
        stat = Path(f"/proc/{worker}/stat").read_text().rsplit(")", 1)[1].split()
        require(stat[1] == str(supervisor) and stat[19] == started, "fixture worker identity changed")
        if fault == "worker":
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        elif fault == "supervisor":
            command("systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", UNIT)
        else:
            require(fault == "reboot", "unknown fault")
        print(json.dumps(dict(receipt=name, supervisor_pid=supervisor, worker_pid=worker, fault=fault)))
    finally:
        os.close(fd)


def recover(name, fault, trial):
    for _ in range(30):
        if command("systemctl", "show", UNIT, "-p", "ActiveState", "--value") in ("failed", "inactive"):
            break
        time.sleep(1)
    else:
        raise SystemExit("fixture unit is still active after the fault")
    if fault == "reboot":
        for _ in range(120):
            if receipt(name).get("verification", {}).get("state") == "posted":
                break
            time.sleep(1)
        else:
            raise SystemExit("the boot reaper did not publish a terminal check")
    else:
        command("systemctl", "start", REAPER)
    command("systemctl", "start", REAPER)
    result = receipt(name)
    posts = [json.loads(line) for line in CHECKS.read_text().splitlines()] if CHECKS.exists() else []
    matching = [post for post in posts if json.loads(post["output"]["text"])["coldbox_receipt"] == name]
    vms = job_vms()
    submissions = RECEIPTS / "submissions"
    queue = submissions / "queue.json"
    pending = read_json(queue) if queue.exists() else []
    records = list(submissions.glob("cbx-*"))
    artifacts = list(RECEIPTS.glob("*.artifacts"))
    count = len(matching)
    verdict, cause = result["verdict"], result.get("cause")
    print(json.dumps(dict(checks=matching, job_vms=vms, queue=pending,
                          submissions=[path.name for path in records], artifacts=[path.name for path in artifacts],
                          row=f"| {fault} | {trial} | `{name}` | {verdict} | {cause} | {len(vms)} | {count} | "
                              f"{len(records) + len(pending)} | {len(artifacts)} |")))
    require(result["vm"]["destroyed"] is True and result["vm"].get("destroyed_at")
            and result.get("reconciled_by") == "coldbox reap"
            and (verdict, cause) == ("incomplete", "worker_interrupted"), "receipt did not finalize after interruption")
    require(not (vms or pending or records or artifacts), "resources remained after reaping")
    require(count == 1 and result.get("verification") == dict(state="posted", check_id=51),
            "expected exactly one check result for the recovered receipt")
    check = matching[0]
    require((check["name"], check["status"], check["conclusion"]) == ("coldbox verify", "completed", "failure"),
            "the sink did not record a terminal interruption result")


if __name__ == "__main__":
    require(socket.gethostname().startswith("restart-matrix-"), "run only inside the disposable matrix guest")
    if Path(sys.argv[0]).name == "gh":
        sys.exit(github(sys.argv[1:]))
    actions = dict(setup=setup, start=start, interrupt=interrupt, recover=recover)
    actions[sys.argv[1]](*sys.argv[2:])
