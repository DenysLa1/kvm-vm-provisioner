"""Strict tool double: no sudo/libvirt/QEMU processes are ever executed.

The test runner installs this under each allowed command name in an otherwise
empty PATH. Filesystem operations are confined to one test's directory. Only
ssh-keygen fingerprint validation is delegated to a real, preselected executable.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(os.environ['VM_TEST_ROOT']).resolve()
BIN = ROOT / 'bin'
SCENARIO = os.environ.get('VM_TEST_SCENARIO', '')
TOOL = Path(sys.argv[0]).name
ARGS = sys.argv[1:]


def abort(message, code=97):
    print(f'mock {TOOL}: {message}', file=sys.stderr)
    raise SystemExit(code)


def path(value):
    p = Path(value)
    if not p.is_absolute() or not p.resolve().is_relative_to(ROOT):
        abort(f'unsafe or unexpected path: {value}')
    return p


def log():
    # Each short record is one O_APPEND write, including during competing requests.
    fd = os.open(ROOT / 'calls.jsonl', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    os.write(fd, (json.dumps([TOOL, ARGS]) + '\n').encode())
    os.close(fd)


def barrier(label, count=2):
    (ROOT / f'{label}-{os.getppid()}').touch()
    deadline = time.monotonic() + 8
    while len(list(ROOT.glob(f'{label}-*'))) < count:
        if time.monotonic() > deadline:
            abort('concurrency barrier timed out')
        time.sleep(.01)


def main():
    log()
    if TOOL == 'sudo':
        if ARGS == ['-v']:
            if SCENARIO == 'sudo-auth-fail':
                abort('authentication denied', 31)
            return
        if ARGS[:3] == ['-n', '-l', '--'] and len(ARGS) == 4:
            command = path(ARGS[3])
            if command.parent != BIN:
                abort('sudo permissions queried for unexpected executable')
            if SCENARIO == 'sudo-policy-fail' and command.name == 'ln':
                abort('ln not permitted', 32)
            return
        if ARGS[:2] != ['-n', '--'] or len(ARGS) < 3:
            abort('unexpected sudo arguments')
        command = path(ARGS[2])
        if command.parent != BIN or command.name not in {
            'test', 'stat', 'mktemp', 'cp', 'qemu-img', 'chown', 'restorecon', 'ln', 'rm'
        }:
            abort('refusing to delegate to a real/unexpected command')
        raise SystemExit(subprocess.run([str(command), *ARGS[3:]], check=False).returncode)

    if TOOL == 'virsh':
        if ARGS[:2] != ['-c', 'qemu:///system']:
            abort('unexpected connection')
        args = ARGS[2:]
        if args == ['list', '--all', '--name']:
            if SCENARIO == 'connection-fail':
                abort('connection unavailable', 21)
            if SCENARIO == 'race':
                barrier('domain-list')
            if SCENARIO == 'duplicate':
                print('demo')
            elif SCENARIO == 'similar-name':
                print('demo-extra\nprefix-demo')
            return
        if args == ['net-list', '--name']:
            if SCENARIO == 'network-error':
                abort('network lookup denied', 22)
            print('default' if SCENARIO != 'network-inactive' else '')
            return
        if args == ['pool-list', '--name']:
            if SCENARIO == 'pool-error':
                abort('pool lookup denied', 23)
            print('default' if SCENARIO != 'pool-inactive' else '')
            return
        if args == ['pool-refresh', 'default']:
            if SCENARIO == 'refresh-fail':
                abort('pool refresh failed', 41)
            return
        if args == ['list', '--all']:
            print('mock VM list')
            return
        if len(args) == 2 and args[0] == 'domstate':
            print('running')
            return
        abort('unexpected or destructive virsh command')

    if TOOL == 'test':
        args = ARGS
        if SCENARIO == 'storage-test-error':
            abort('storage check denied', 33)
        if len(args) == 3 and args[1] == '-ef':
            left, right = path(args[0]), path(args[2])
            raise SystemExit(0 if SCENARIO != 'identity-fail' and os.path.samefile(left, right) else 1)
        if args[:2] == ['!', '-L'] and len(args) == 3:
            raise SystemExit(int(path(args[2]).is_symlink()))
        if len(args) != 2 or args[0] not in ('-d', '-w', '-x'):
            abort('unexpected test predicate')
        p = path(args[1])
        ok = p.is_dir() if args[0] == '-d' else os.access(p, os.W_OK if args[0] == '-w' else os.X_OK)
        raise SystemExit(0 if ok else 1)

    if TOOL == 'stat':
        if ARGS[:3] != ['-c', '%u:%a', '--'] or len(ARGS) != 4:
            abort('unexpected stat arguments')
        path(ARGS[3])
        print('0:777' if SCENARIO == 'unsafe-storage' else '0:755')
        return
    if TOOL == 'id' and ARGS == ['-u', 'qemu']:
        print('107')
        return
    if TOOL == 'getent' and ARGS == ['group', 'qemu']:
        print('qemu:x:107:')
        return
    if TOOL == 'mktemp':
        if ARGS == ['-d']:
            print(tempfile.mkdtemp(prefix='config-', dir=ROOT / 'tmp'))
            return
        if len(ARGS) != 1:
            abort('unexpected mktemp arguments')
        template = path(ARGS[0])
        if template.parent != ROOT / 'storage' or 'XXXXXXXX' not in template.name:
            abort('unexpected staging template')
        prefix, suffix = template.name.split('XXXXXXXX')
        fd, filename = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=template.parent)
        os.close(fd)
        print(filename)
        return
    if TOOL == 'cp':
        if len(ARGS) != 3 or ARGS[0] != '--':
            abort('unexpected copy arguments')
        src, dst = map(path, ARGS[1:])
        if dst.parent != ROOT / 'storage' or not dst.name.startswith('.demo.'):
            abort('copy must target private staging, never final disk')
        shutil.copyfile(src, dst)
        return
    if TOOL == 'qemu-img':
        if len(ARGS) != 5 or ARGS[:3] != ['resize', '-f', 'qcow2']:
            abort('unexpected qemu-img operation (shrinking forbidden)')
        disk = path(ARGS[3])
        if not disk.name.startswith('.demo.'):
            abort('resize must target staging')
        if SCENARIO in ('resize-fail', 'base-too-large'):
            abort('image resize rejected', 40)
        return
    if TOOL == 'chown':
        if len(ARGS) != 2 or ARGS[0] != 'qemu:qemu':
            abort('unexpected ownership arguments')
        path(ARGS[1])
        return
    if TOOL == 'restorecon':
        if len(ARGS) != 1:
            abort('unexpected restorecon arguments')
        p = path(ARGS[0])
        if SCENARIO == 'final-label-fail' and p.name == 'demo.qcow2':
            abort('SELinux labelling failed', 43)
        return
    if TOOL == 'ln':
        if len(ARGS) != 4 or ARGS[:2] != ['-T', '--']:
            abort('only atomic hard-link publication without force is allowed')
        src, dst = map(path, ARGS[2:])
        if SCENARIO == 'race':
            barrier('publish')
        try:
            os.link(src, dst, follow_symlinks=False)
        except OSError as error:
            abort(f'publication failed: {error}', 1)
        return
    if TOOL == 'rm':
        if ARGS[:2] == ['-r', '--'] and len(ARGS) == 3:
            p = path(ARGS[2])
            if p.parent != ROOT / 'tmp':
                abort('recursive deletion limited to temporary cloud-init configuration')
            shutil.rmtree(p)
            return
        if ARGS[:1] == ['--'] and len(ARGS) == 2:
            p = path(ARGS[1])
            final = ROOT / 'storage/demo.qcow2'
            if not p.name.startswith('.demo.') or not final.exists() or not os.path.samefile(p, final):
                abort('staging alias removal requires verified publication')
            if SCENARIO == 'unlink-fail':
                abort('staging unlink denied', 44)
            p.unlink()
            return
        abort('unexpected removal arguments')
    if TOOL == 'iconv':
        if ARGS[:4] != ['-f', 'UTF-8', '-t', 'UTF-8'] or len(ARGS) != 5:
            abort('unexpected encoding check')
        try:
            path(ARGS[4]).read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            abort('invalid UTF-8', 1)
        return
    if TOOL == 'ssh-keygen':
        if ARGS[:2] != ['-l', '-f'] or len(ARGS) != 3:
            abort('only fingerprint validation is allowed here')
        p = path(ARGS[2])
        if p.name != 'public-key' or p.parent.parent != ROOT / 'tmp':
            abort('key validation must use captured test key')
        raise SystemExit(subprocess.run([os.environ['VM_TEST_SSH_KEYGEN'], *ARGS], check=False).returncode)
    if TOOL == 'virt-install':
        if SCENARIO == 'install-fail':
            # Model failure AFTER a domain could have been created.
            (ROOT / 'possible-domain').write_text('possibly running')
            abort('failed after domain definition', 42)
        spec = ARGS[ARGS.index('--cloud-init') + 1]
        for entry in spec.split(','):
            key, value = entry.split('=', 1)
            shutil.copyfile(path(value), ROOT / f'captured-{key}')
        disk = path(ARGS[ARGS.index('--disk') + 1].split(',')[0].split('=', 1)[1])
        with disk.open('ab') as handle:
            handle.write(b'guest-written\n')
        (ROOT / 'installed').touch()
        return
    abort('unexpected tool or arguments')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        abort(str(error))
