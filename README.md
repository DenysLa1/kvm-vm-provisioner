# KVM VM Provisioner

A Bash-based CLI tool for automated provisioning of AlmaLinux 10 virtual machines using KVM/QEMU, libvirt, and cloud-init.

## Overview

This project was created as a hands-on exercise in Linux systems administration, virtualization, and infrastructure automation.

The goal is to simplify the deployment of local Linux virtual machines while gaining practical experience with KVM, libvirt, Bash scripting, networking, and cloud-init.

## Features

- Automated AlmaLinux 10 VM provisioning from a cloud image
- Configurable vCPU count, memory, and disk size
- Automatic initial configuration using cloud-init
- SSH public-key authentication for the `vmuser` account
- VM listing and status inspection
- Basic input validation and duplicate VM name protection

## Technology Stack

- **Host environment:** Nobara Linux
- **Guest operating system:** AlmaLinux 10
- **Virtualization:** KVM/QEMU and libvirt
- **Automation:** Bash, virt-install, virsh, cloud-init
- **Remote access:** OpenSSH

## Requirements

A Linux host with hardware virtualization support, a working system libvirt connection, an active `default` virtual network, and a configured storage pool.

The current implementation expects the following utilities: `virsh`, `virt-install`, `qemu-img`, `sudo`, and `restorecon`.

It also expects:

- An AlmaLinux 10 Generic Cloud qcow2 image at `~/VM-images/AlmaLinux-10-GenericCloud.qcow2`
- An SSH public key at `~/.ssh/id_ed25519.pub`
- A writable libvirt storage directory at `/var/lib/libvirt/images`
- A `qemu:qemu` system account and the necessary administrative permissions

The cloud image is available from the official AlmaLinux repositories.

## Usage

Clone the repository and make the script executable using `chmod +x vm-manager.sh`.

**Display help:** `./vm-manager.sh help`

**Create a VM:** `./vm-manager.sh create alma10-example 2 2048 15`

The arguments specify the VM name, virtual CPUs, memory in MiB, and disk size in GiB.

**List virtual machines:** `./vm-manager.sh list`

**Check VM status:** `./vm-manager.sh status alma10-example`

## Testing

The tool has been tested locally on Nobara Linux using AlmaLinux 10 cloud images.

Testing included VM creation, cloud-init initialization, SSH key authentication, basic parameter validation, and duplicate-name rejection.

The initial virtual machines were named `alma10-test01` (manual deployment) and `alma10-test02` (script-based deployment).

## Current Limitations

This is an educational project, not a production-ready provisioning system.

The current implementation uses fixed paths and host-specific assumptions, requires local sudo access, and has limited failure recovery. A failed deployment may leave a virtual disk that requires manual inspection.

## Planned Improvements

- Improved cleanup and error handling
- Configurable storage and SSH key paths
- Automated tests and ShellCheck
- Improved lifecycle management
- Support for additional Linux cloud images

## Project Purpose

The project is part of my independent learning in Linux infrastructure and virtualization. It focuses on understanding how virtual machines are provisioned, configured, and managed rather than building a production orchestration platform.