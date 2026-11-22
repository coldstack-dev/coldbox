#!/bin/bash
set -Eeuo pipefail

usage='usage: scripts/restart-matrix.sh INCUS_POOL INNER_SUBNET (run on the dedicated nested-test host)'
die() { printf 'restart-matrix: %s\n' "$*" >&2; exit 1; }
[[ ${1:-} != --help && ${1:-} != -h ]] || { echo "$usage"; exit 0; }
(( $# == 2 )) || die "$usage"
[[ -c /dev/kvm ]] || die 'nested KVM is required'
[[ $1 =~ ^[a-zA-Z0-9_.-]+$ ]] || die "$usage"
root=$(git -C "$(dirname -- "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
cd "$root"
pool=$1 subnet=$2
mkdir -p .smoke
work=$(mktemp -d .smoke/restart-matrix-XXXXXXXX)
id=${work##*-}
vm=restart-matrix-${id,,} net=rmx${id,,}
marker=user.coldbox.restart_matrix

outer() { incus --force-local --project default "$@" </dev/null; }
owns_vm() { [[ $(outer config get "$vm" "$marker") == "$id" ]]; }
guest() { owns_vm && outer exec "$vm" -- "$@"; }
fixture() { guest /usr/local/lib/restart-matrix/fixture.py "$@"; }
inventory() { outer list --format csv -c n && outer network list --format csv -c n; }
cleanup() {
    local status=$? names
    trap - EXIT ERR INT TERM HUP
    if (( status != 0 )); then
        guest journalctl -u 'coldbox-runner@*' -u coldbox-reap.service -n 100 --no-pager \
            >"$work/guest-journal.log" 2>&1 || true
    fi
    for _ in 1 2 3; do
        if owns_vm 2>/dev/null; then outer delete --force "$vm" || true; fi
        if [[ $(outer network get "$net" "$marker" 2>/dev/null) == "$id" ]]; then
            outer network delete "$net" || true
        fi
        if names=$(inventory) && ! grep -Fxq -e "$vm" -e "$net" <<<"$names"; then
            echo "restart-matrix: guest and network removed. Evidence: $work"
            exit "$status"
        fi
        sleep 5
    done
    die "could not confirm removal of guest $vm and network $net. Evidence: $work"
}
wait_for_guest() {
    local stopped=0
    for _ in $(seq 120); do
        if guest true 2>/dev/null; then return; fi
        if [[ $(outer list "$vm" --format csv -c ns) == "$vm,STOPPED" ]]; then
            stopped=$((stopped + 1))
        else
            stopped=0
        fi
        if (( stopped >= 6 )) && owns_vm; then
            outer start "$vm"
            stopped=0
        fi
        sleep 5
    done
    die "guest $vm did not become ready"
}

names=$(inventory)
if grep -Fxq -e "$vm" -e "$net" <<<"$names"; then die 'trial resource name is already in use'; fi
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
trap 'printf "restart-matrix: line %s exited %s\n" "$LINENO" "$?" >&2' ERR

git bundle create -q "$work/coldbox.bundle" HEAD
git rev-parse HEAD >"$work/revision"
outer network create "$net" ipv4.address=auto ipv4.nat=true ipv6.address=none "$marker=$id"
outer launch images:ubuntu/24.04/cloud "$vm" --vm -s "$pool" -n "$net" -d root,size=80GiB \
    -c limits.cpu=6 -c limits.memory=12GiB -c "$marker=$id"
wait_for_guest
guest cloud-init status --wait
guest test ! -e /etc/coldbox
guest apt-get update -q
guest env DEBIAN_FRONTEND=noninteractive apt-get install -y -q git
guest install -d /usr/local/lib/restart-matrix
guest hostnamectl set-hostname "$vm"
outer file push "$work/coldbox.bundle" "$vm/root/coldbox.bundle"
outer file push scripts/restart-matrix-fixture.py "$vm/usr/local/lib/restart-matrix/fixture.py" --mode 0755
guest git clone -q /root/coldbox.bundle /opt/coldbox-source
guest runuser -u ubuntu -- sudo env COLDBOX_STORAGE=dir /opt/coldbox-source/install.sh \
    --install-incus --memory 3GiB --disk 20GiB --subnet "$subnet"
fixture setup

report=$work/matrix.md
printf '| Fault | Trial | Receipt | Verdict | Cause | Job VMs | Terminal results | Submissions | Artifacts |\n' >"$report"
printf '| --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |\n' >>"$report"
for fault in worker supervisor reboot; do
    for trial in 1 2; do
        echo "restart-matrix: $fault $trial"
        name=$(fixture start)
        prefix="$work/$fault-$trial"
        guest cat "/var/lib/coldbox/receipts/$name.json" >"$prefix.before.json"
        if [[ $fault == reboot ]]; then
            before=$(guest cat /proc/sys/kernel/random/boot_id)
            guest sync
        fi
        fixture interrupt "$name" "$fault" >"$prefix.fault.json"
        if [[ $fault == reboot ]]; then
            owns_vm || die "refusing to restart unowned guest $vm"
            outer restart --force "$vm"
            wait_for_guest
            after=$(guest cat /proc/sys/kernel/random/boot_id)
            [[ $after != "$before" ]] || die 'guest boot ID did not change'
            printf '%s\n%s\n' "$before" "$after" >"$prefix.boot-ids"
        fi
        status=0
        fixture recover "$name" "$fault" "$trial" >"$prefix.result.json" || status=$?
        guest cat "/var/lib/coldbox/receipts/$name.json" >"$prefix.receipt.json"
        (( status == 0 )) || exit "$status"
        python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["row"])' "$prefix.result.json" | tee -a "$report"
    done
done
guest cat /var/lib/coldbox/restart-matrix/checks.jsonl >"$work/checks.jsonl"
[[ $(wc -l <"$work/checks.jsonl") -eq 6 ]] || die 'expected six terminal results across all trials'
printf '\nRecovery rate: 6/6.\n' | tee -a "$report"
