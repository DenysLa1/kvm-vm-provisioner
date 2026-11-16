#!/usr/bin/env bash

set -euo pipefail

LIBVIRT_URI="qemu:///system"

show_help() {
    echo "KVM VM Provisioner"
    echo ""
    echo "Usage: ./vm-manager.sh <command> [arguments]"
    echo ""
    echo "Commands:"
    echo "  help             Show help"
    echo "  list             List virtual machines"
    echo "  status <name>    Show VM status"
    echo "  create <name> <cpu> <ram> <disk>    Provision AlmaLinux VM"
}

check_dependencies() {
    if ! command -v virsh >/dev/null 2>&1; then
        echo "Error: virsh is not installed."
        exit 1
    fi
}

list_vms() {
    virsh -c "$LIBVIRT_URI" list --all
}

vm_status() {
    if [[ $# -ne 1 ]]; then
        echo "Usage: ./vm-manager.sh status <name>"
        exit 1
    fi

    virsh -c "$LIBVIRT_URI" domstate "$1"
}

# Compare complete lines, never substrings or patterns supplied by the user.
contains_name() {
    local names="$1" wanted="$2" item
    while IFS= read -r item; do
        [[ "$item" != "$wanted" ]] || return 0
    done <<< "$names"
    return 1
}

create_vm() (
    if [[ $# -ne 4 ]]; then
        echo "Usage: ./vm-manager.sh create <name> <vcpus> <ram-mib> <disk-gib>" >&2
        exit 1
    fi

    local LC_ALL=C  # Byte-oriented checks; iconv validates UTF-8 separately.
    local name="$1" vcpus="$2" ram="$3" disk_size="$4" value
    local base_image="$HOME/VM-images/AlmaLinux-10-GenericCloud.qcow2"
    local ssh_key="$HOME/.ssh/id_ed25519.pub"
    local storage="/var/lib/libvirt/images"
    local vm_disk="$storage/$name.qcow2"
    local temp_dir="" staging="" published=no installer_attempted=no
    local phase="argument validation" domain_snapshot="not obtained"
    local -A bins=()

    fail() { echo "Error: $*" >&2; exit 1; }

    # Use the same absolute executable paths checked by sudo -l.
    root_command() {
        local tool="$1"
        shift
        "${bins[sudo]}" -n -- "${bins[$tool]}" "$@"
    }

    finish() {
        local rc=$? cleanup_rc=0
        trap - EXIT
        set +e
        if [[ -n "$temp_dir" ]]; then
            "${bins[rm]}" -r -- "$temp_dir"
            cleanup_rc=$?
            if (( cleanup_rc != 0 )); then
                echo "Error: temporary config cleanup failed: $temp_dir" >&2
                (( rc != 0 )) || rc=$cleanup_rc
            fi
        fi
        if (( rc != 0 )); then
            echo "Error: provisioning stopped during $phase (exit $rc)." >&2
            [[ -z "$staging" ]] || echo "Staging path (may remain): $staging" >&2
            if [[ "$published" == yes ]]; then
                echo "Published disk preserved: $vm_disk" >&2
            elif [[ -n "$staging" ]]; then
                echo "Publication not confirmed; destination may belong to another invocation: $vm_disk" >&2
            fi
            echo "Last domain snapshot: $domain_snapshot. Current domain state: UNKNOWN (not rechecked)." >&2
            echo "virt-install attempted by this invocation: $installer_attempted." >&2
            if [[ "$installer_attempted" == yes ]]; then
                echo "virt-install may have defined or started the domain. Inspect it before any manual recovery." >&2
            fi
        fi
        exit "$rc"
    }
    trap finish EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    [[ "$name" =~ ^[a-z][a-z0-9-]{0,29}$ ]] || fail "Invalid VM name."
    for value in "$vcpus" "$ram" "$disk_size"; do
        [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail "CPU, RAM and disk must be positive integers."
        # Bound the input before Bash evaluates it as an integer.
        (( ${#value} <= 5 )) || fail "Resource value is too long."
    done
    if (( vcpus > 8 || ram < 512 || ram > 16384 || disk_size < 10 || disk_size > 100 )); then
        fail "Resource values are outside supported ranges."
    fi

    phase="dependency checks"
    local tool
    for tool in virsh virt-install qemu-img sudo restorecon ssh-keygen iconv mktemp cp chown ln rm stat test id getent; do
        bins[$tool]="$(command -v "$tool")" || fail "Missing dependency: $tool"
        # Privileged calls must resolve to executable files, not shell functions/builtins.
        if [[ "${bins[$tool]}" != /* ]]; then
            bins[$tool]="$(type -P "$tool")" || fail "Missing executable: $tool"
        fi
    done
    [[ -f "$base_image" && -r "$base_image" ]] || fail "Cloud image is not a readable regular file."
    [[ -f "$ssh_key" && -r "$ssh_key" ]] || fail "SSH public key is not a readable regular file."

    phase="SSH public-key validation"
    local public_key key_yaml c1_controls=$'\xc2[\x80-\x9f]'
    # read -d '' preserves trailing newlines and reports a NUL delimiter as success.
    if IFS= read -r -d '' public_key < "$ssh_key"; then
        fail "SSH public key contains a NUL byte."
    fi
    public_key="${public_key%$'\n'}"  # Permit exactly one optional terminal LF.
    [[ -n "$public_key" && "$public_key" != *$'\n'* && "$public_key" != *$'\r'* ]] ||
        fail "SSH public key must contain exactly one nonempty logical line (no CR)."
    # Reject characters that YAML parsers may reject or fold inside the key.
    [[ ! "$public_key" =~ [[:cntrl:]] && ! "$public_key" =~ $c1_controls && "$public_key" != *$'\xc2\x85'* &&
       "$public_key" != *$'\xe2\x80\xa8'* && "$public_key" != *$'\xe2\x80\xa9'* &&
       "$public_key" != *$'\xef\xbf\xbe'* && "$public_key" != *$'\xef\xbf\xbf'* ]] ||
        fail "SSH public key contains an unexpected control character, line separator or YAML noncharacter."
    [[ "$public_key" =~ ^[A-Za-z0-9@._+-]+[[:blank:]]+[A-Za-z0-9+/]+={0,2}([[:blank:]].*)?$ ]] ||
        fail "Expected an OpenSSH public key: type, base64 data, optional comment."

    umask 077
    temp_dir="$("${bins[mktemp]}" -d)"
    case "$temp_dir" in
        *,*|*\"*|*\'*|*\\*)
            fail "Use a TMPDIR without commas, quotes, or backslashes."
            ;;
    esac
    printf '%s\n' "$public_key" > "$temp_dir/public-key"
    "${bins[iconv]}" -f UTF-8 -t UTF-8 "$temp_dir/public-key" >/dev/null
    # Validate the same captured key line that will go into cloud-init.
    "${bins[ssh-keygen]}" -l -f "$temp_dir/public-key" >/dev/null
    # YAML escapes an apostrophe in a single-quoted string by doubling it.
    key_yaml="${public_key//\'/\'\'}"

    phase="libvirt domain preflight"
    local domains networks pools
    domains="$("${bins[virsh]}" -c "$LIBVIRT_URI" list --all --name)"
    domain_snapshot="'$name' absent at preflight only"
    if contains_name "$domains" "$name"; then
        domain_snapshot="'$name' present at preflight"
        fail "VM '$name' already exists."
    fi
    phase="network preflight"
    networks="$("${bins[virsh]}" -c "$LIBVIRT_URI" net-list --name)"
    contains_name "$networks" default || fail "The default network is not active or visible."
    phase="storage pool preflight"
    pools="$("${bins[virsh]}" -c "$LIBVIRT_URI" pool-list --name)"
    contains_name "$pools" default || fail "The default storage pool is not active or visible."

    phase="sudo and storage preflight"
    "${bins[sudo]}" -v
    # Sudo rules that require specific arguments may fail this check.
    for tool in test stat mktemp cp qemu-img chown restorecon ln rm; do
        "${bins[sudo]}" -n -l -- "${bins[$tool]}" >/dev/null
    done
    root_command test -d "$storage"
    root_command test ! -L "$storage"
    root_command test -w "$storage"
    root_command test -x "$storage"
    local storage_meta owner mode
    storage_meta="$(root_command stat -c '%u:%a' -- "$storage")"
    IFS=: read -r owner mode <<< "$storage_meta"
    [[ "$owner" == 0 && "$mode" =~ ^[0-7]{3,4}$ ]] || fail "Storage must be a root-owned directory with readable permission metadata."
    (( (8#$mode & 0022) == 0 )) || fail "Storage must not be writable by group or others."
    "${bins[id]}" -u qemu >/dev/null
    "${bins[getent]}" group qemu >/dev/null

    phase="cloud-init configuration"
    cat_config() {
        printf '%s\n' "#cloud-config
users:
  - default
  - name: vmuser
    groups: [wheel]
    sudo: [\"ALL=(ALL) NOPASSWD:ALL\"]
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - '$key_yaml'

ssh_pwauth: false
disable_root: true"
    }
    cat_config > "$temp_dir/user-data"
    printf 'instance-id: iid-%s-001\nlocal-hostname: %s\n' "$name" "$name" > "$temp_dir/meta-data"

    phase="private disk staging"
    staging="$(root_command mktemp "$storage/.$name.XXXXXXXX.qcow2")"
    echo "Preparing private staging disk: $staging"
    phase="image copy"
    root_command cp -- "$base_image" "$staging"
    phase="image resize (shrinking is not permitted)"
    # qemu-img refuses shrinking without --shrink. Failure affects staging only.
    root_command qemu-img resize -f qcow2 "$staging" "${disk_size}G"
    phase="staging ownership and SELinux context"
    root_command chown qemu:qemu "$staging"
    root_command restorecon "$staging"

    phase="atomic disk publication"
    # -T uses the destination as an exact path. An existing entry causes failure without -f.
    root_command ln -T -- "$staging" "$vm_disk"
    published=yes
    phase="publication verification"
    root_command test "$staging" -ef "$vm_disk"
    # Both paths share one inode. Restore its label using the final path.
    root_command restorecon "$vm_disk"
    phase="staging alias removal"
    root_command rm -- "$staging"
    staging=""

    phase="storage pool refresh"
    "${bins[virsh]}" -c "$LIBVIRT_URI" pool-refresh default
    phase="virt-install"
    installer_attempted=yes
    "${bins[virt-install]}" \
        --connect "$LIBVIRT_URI" \
        --name "$name" \
        --memory "$ram" \
        --vcpus "$vcpus" \
        --osinfo name=almalinux10 \
        --import \
        --disk "path=$vm_disk,format=qcow2,bus=virtio" \
        --network network=default,model=virtio \
        --cloud-init "user-data=$temp_dir/user-data,meta-data=$temp_dir/meta-data" \
        --graphics none \
        --console pty,target_type=serial \
        --noautoconsole

    echo "Provisioning command completed for '$name'. Guest readiness (cloud-init and SSH) has NOT been verified."
)

main() {
    local command="${1:-help}"

    case "$command" in
        help)
            show_help
            ;;
        list)
            check_dependencies
            list_vms
            ;;
        status)
            check_dependencies
            shift
            vm_status "$@"
            ;;
        create)
            shift
            create_vm "$@"
            ;;
        *)
            echo "Error: Unknown command '$command'"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
