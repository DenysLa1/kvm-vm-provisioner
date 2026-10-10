#!/usr/bin/env bash

set -euo pipefail

# Libvirt system connection
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

create_vm() (
    # Require four arguments
    if [[ $# -ne 4 ]]; then
        echo "Usage: ./vm-manager.sh create <name> <vcpus> <ram-mib> <disk-gib>"
        exit 1
    fi

    local name="$1"
    local vcpus="$2"
    local ram="$3"
    local disk_size="$4"

    local base_image="$HOME/VM-images/AlmaLinux-10-GenericCloud.qcow2"
    local ssh_key="$HOME/.ssh/id_ed25519.pub"
    local storage="/var/lib/libvirt/images"
    local vm_disk="$storage/$name.qcow2"

    # Validate VM name
    if [[ ! "$name" =~ ^[a-z][a-z0-9-]{0,29}$ ]]; then
        echo "Error: Invalid VM name."
        exit 1
    fi

    # Validate numeric arguments
    for value in "$vcpus" "$ram" "$disk_size"; do
        if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
            echo "Error: CPU, RAM and disk must be positive integers."
            exit 1
        fi
    done

    if (( vcpus > 8 || ram < 512 || ram > 16384 ||
          disk_size < 10 || disk_size > 100 )); then
        echo "Error: Resource values are outside supported ranges."
        exit 1
    fi

    # Check required tools and files
    for tool in virsh virt-install qemu-img sudo restorecon; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "Error: Missing dependency: $tool"
            exit 1
        fi
    done

    if [[ ! -r "$base_image" || ! -r "$ssh_key" ]]; then
        echo "Error: Cloud image or SSH public key missing."
        exit 1
    fi

    # Prevent duplicate VM names and disk overwrites
    if virsh -c "$LIBVIRT_URI" dominfo "$name" >/dev/null 2>&1; then
        echo "Error: VM '$name' already exists."
        exit 1
    fi

    if sudo test -e "$vm_disk"; then
        echo "Error: Disk '$vm_disk' already exists."
        exit 1
    fi

    # Generate temporary cloud-init files
    local temp_dir
    temp_dir="$(mktemp -d)"
    trap 'rm -rf -- "$temp_dir"' EXIT

    cat > "$temp_dir/user-data" <<EOF
#cloud-config
users:
  - default
  - name: vmuser
    groups: [wheel]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - $(cat "$ssh_key")

ssh_pwauth: false
disable_root: true
EOF

    cat > "$temp_dir/meta-data" <<EOF
instance-id: iid-$name-001
local-hostname: $name
EOF

    echo "Creating disk for $name..."

    sudo cp -- "$base_image" "$vm_disk"
    sudo qemu-img resize "$vm_disk" "${disk_size}G"
    sudo chown qemu:qemu "$vm_disk"
    sudo restorecon "$vm_disk"

    virsh -c "$LIBVIRT_URI" pool-refresh default

    echo "Provisioning $name..."

    virt-install \
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

    echo "VM '$name' has been created."
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