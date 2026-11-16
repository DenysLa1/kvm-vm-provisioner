# KVM VM Provisioner

An educational Bash CLI for provisioning local AlmaLinux 10 virtual machines with KVM/QEMU, libvirt and cloud-init. The project exercises Linux administration and infrastructure automation on a controlled student lab host.

## Commands

```bash
./vm-manager.sh help
./vm-manager.sh list
./vm-manager.sh status alma10-example
./vm-manager.sh create alma10-example 2 2048 15
```

`create` takes a name, vCPUs, RAM in MiB and target disk size in GiB. Names must match `[a-z][a-z0-9-]{0,29}`. Supported ranges are 1–8 vCPUs, 512–16384 MiB RAM and 10–100 GiB disk. Numeric inputs must be positive decimal integers without leading zeros; excessive length is rejected before arithmetic.

`create` changes storage and may define/start a guest. Use a dedicated test name only after inspecting your lab. `list` and `status` read libvirt state.

## Requirements and assumptions

See [Real VM Verification](#real-vm-verification) for the recorded Nobara Linux test run.

- A Linux KVM/QEMU host with hardware virtualization and an accessible `qemu:///system` libvirt connection. The invoking user must have visibility of all domains and permission to create guests.
- An active `default` network and `default` storage pool. The script checks that the pool is active and visible. It assumes the pool targets `/var/lib/libvirt/images` and leaves that mapping for you to verify with `virsh -c qemu:///system pool-dumpxml default`.
- A readable AlmaLinux 10 Generic Cloud **qcow2** base image at `~/VM-images/AlmaLinux-10-GenericCloud.qcow2`. Use an official image with a verified checksum and keep it unused by running VMs. The image must be standalone, with no backing file or external data file. Inspect it with `qemu-img info --output=json -f qcow2 "$HOME/VM-images/AlmaLinux-10-GenericCloud.qcow2"` and check that `backing-filename` and `data-file` are absent.
- An OpenSSH public-key file at `~/.ssh/id_ed25519.pub`. Exactly one nonempty logical line is accepted, with one optional final LF. Extra blank lines, CR/CRLF, NUL/control characters, Unicode line separators and YAML-forbidden noncharacters are rejected. Key fields use spaces; comments may contain printable UTF-8, quotes, apostrophes, `: `, `#` and backslashes. The captured line is checked by `iconv` and `ssh-keygen -l`; its content is preserved when encoded into YAML.
- A real, root-owned `/var/lib/libvirt/images` directory, without group/other write permission or ACLs granting untrusted writers access. Its ancestor directories and the tool executables/PATH must also be trusted. The directory itself must not be a symlink. POSIX mode checks do not audit ACLs or defend against privileged actors replacing paths.
- A `qemu` user and `qemu` group, and the host's SELinux labelling tools/policy. These are host-specific assumptions, not universal Linux account names.
- Bash with associative arrays, `virsh`, `virt-install`, `qemu-img`, `sudo`, `restorecon`, OpenSSH `ssh-keygen`, `iconv`, and GNU-style `mktemp`, `cp`, `chown`, `ln`, `rm`, `stat`, `test`, plus `id` and `getent`. The osinfo database must recognise `almalinux10`.
- Sudo authorisation for the privileged utilities. The preflight authenticates with `sudo -v`, checks each command with `sudo -n -l`, and uses the checked absolute executable paths. This intentionally conservative check may reject argument-restricted sudoers policies; it does not change those policies. Each subsequent sudo failure also stops execution.

Run as the ordinary lab user with the necessary permissions, so the default image/key paths refer to that user's files. The temporary directory path must be free of commas, quotes and backslashes because `virt-install` parses its cloud-init file paths as suboptions.

## Provisioning sequence

1. Validate arguments, dependencies and the captured public key.
2. Obtain a successful `virsh list --all --name` result, then compare full names exactly. A failed query aborts; it never means “VM absent”. Check active network/pool visibility and storage/sudo prerequisites.
3. Generate private temporary cloud-init files for `vmuser`, hostname and SSH public-key authentication. The user has passwordless sudo inside the guest; password SSH is disabled.
4. Create a unique mode-0600 staging file in the storage filesystem. Copy the base image into it, resize it, set ownership and apply SELinux context.
5. Publish it with `ln -T` **without force**, creating the final name only if it is unused. Verify that staging and final paths reference the same inode, apply the final pathname's SELinux policy, and only then remove the staging alias.
6. Refresh the pool and invoke `virt-install --import` with the cloud-init data.

The success message means that the provisioning command completed. It does **not** verify that cloud-init finished, an address was assigned, or SSH works. There is no automatic guest readiness loop.

## Disk safety and partial failures

The script copies and resizes a standalone base image in a private staging file. The requested disk size must be at least the base image's virtual size as well as within the CLI range. QEMU rejects a smaller target in staging. Guest partition and filesystem growth happens separately.

The script publishes the disk with a hard link on the same filesystem. An existing file, symlink or directory makes publication fail. If two runs compete for one name, only one can publish while that path remains present. Domain and storage state can still change between checks.

After an error, check the reported phase and paths. A failed run can leave a staging file or a published disk, and a failed `virt-install` can leave a defined or running guest or seed media. The script reports the last domain snapshot and treats the current state as unknown. Check libvirt before recovering resources by hand.

The script cleans up temporary cloud-init files on exit when possible. It removes the staging name only after verifying publication and leaves the final disk for manual recovery. A forced kill or power loss can leave resources before their paths are recorded.

## Real VM Verification

I ran `bash vm-manager.sh create alma10-review 2 2048 15` on Nobara Linux with an AlmaLinux 10 guest. Libvirt created and started `alma10-review` with 2 vCPUs and 2048 MiB RAM.

The guest received an address through DHCP, and SSH public-key login worked as `vmuser`. Cloud-init completed with no reported errors, and the static hostname was `alma10-review`. The guest saw a 15 GiB virtual disk and an approximately 14 GiB root filesystem. `sudo -n whoami` returned `root`, and `timedatectl` reported a synchronized guest clock.

- [Host VM status](docs/screenshots/host-vm-status.png) shows the running domain, vCPU count and memory.
- [Guest checks](docs/screenshots/guest-cloud-init.png) show cloud-init status, hostname, login user, root filesystem size and the sudo check.

This records one successful provisioning run on one host. The host capture reports `Security model: none`. SELinux confinement and real failure paths remain untested.

## Manual verification

Read-only host inspection:

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system net-list --all
virsh -c qemu:///system net-dumpxml default
virsh -c qemu:///system pool-list --all
virsh -c qemu:///system pool-dumpxml default
virsh -c qemu:///system dominfo alma10-example
virsh -c qemu:///system domiflist alma10-example
virsh -c qemu:///system net-dhcp-leases default
```

Substitute your dedicated test guest's name and match its interface MAC to the DHCP lease. A missing lease is not proof of a guest failure; the actual network configuration determines address assignment.

After checking the host key/fingerprint and connecting with your matching private key (`ssh -i ~/.ssh/id_ed25519 vmuser@ADDRESS`), inspect inside the guest:

```bash
timeout 120s cloud-init status --wait --long
hostnamectl
id vmuser
lsblk
df -h /
```

Record output and exit status. A timeout differs from cloud-init reporting errors. Inspect `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log` if needed. Check the actual partition/filesystem capacity: growing the disk image alone does not prove the guest uses the additional space.

## Automated tests

Tests require Python 3.9+, PyYAML and OpenSSH `ssh-keygen`. Python/PyYAML are **test-only**; provisioning remains Bash. If necessary, install the test dependencies in an isolated environment (these commands do not install system packages):

```bash
test_env="$(mktemp -d)"
python3 -m venv "$test_env"
"$test_env/bin/python" -m pip install -r tests/requirements.txt
"$test_env/bin/python" -m unittest discover -s tests -v
```

If those test dependencies are already available, run from the repository root:

```bash
bash -n vm-manager.sh
python3 -m unittest discover -s tests -v
git diff --check
```

Also run `shellcheck vm-manager.sh` when ShellCheck is installed.

The suite copies the script to temporary directories and substitutes only its three fixed input/storage paths. It does not override `HOME`. The CLI's PATH contains strict command doubles with no fallback to real infrastructure tools. Unexpected commands/unsafe paths fail; the sudo double refuses to delegate to real executables. OpenSSH validates fresh disposable test keys. File operations and atomic hard links operate on temporary fixture files, not real VM images. QEMU resizing, libvirt, sudo policy, ownership and SELinux behaviour are simulated.

Coverage includes input bounds/overflow, YAML string preservation, malformed keys, infrastructure/preflight failures, exact duplicate matching, existing destination types, resize/pool-refresh/installer failures, retained resources and concurrent same-name publication. Installer failure simulates a domain possibly having been defined. These tests do not prove real guest boot, SELinux access, actual sudoers configuration, image validity, network/SSH readiness or storage behaviour on every filesystem.

## Remaining limitations

The image and storage paths, pool and network names, guest account and AlmaLinux osinfo are fixed. Guest lifecycle management and recovery are manual.
