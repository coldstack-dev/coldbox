# Prompt: uninstall Coldbox

```text
Remove Coldbox from this machine. Stop and ask me before anything marked ASK.

1. List what is registered (`systemctl list-units 'coldbox-runner@*' --all`) and show me which repos or orgs would be
   affected. ASK for my explicit approval before unregistering runners, changing workflows, or running
   `sudo ~/coldbox/uninstall.sh`.
2. Once approved, for each registered repo or org run `coldbox unregister OWNER/REPO` (this stops the service and
   deletes its cbx-* runners on GitHub), and remove workflows that use `runs-on: [self-hosted, coldbox]` from those
   repos (or tell me which ones still do) so jobs do not sit queued forever.
3. Run `sudo ~/coldbox/uninstall.sh`. It deletes the Coldbox VMs, base image, bridge, ACL, storage pool,
   systemd units and the CLI. It keeps Incus itself and the receipts in /var/lib/coldbox.
4. Prove it is gone: `incus project list`, `incus network list` and `incus storage list` show no coldbox entries,
   and `gh api repos/OWNER/REPO/actions/runners` shows no cbx-* runners.
5. ASK me before deleting /var/lib/coldbox/receipts.
```
