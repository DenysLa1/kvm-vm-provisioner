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
        *)
            echo "Error: Unknown command '$command'"
            show_help
            exit 1
            ;;
    esac
}

main "$@"