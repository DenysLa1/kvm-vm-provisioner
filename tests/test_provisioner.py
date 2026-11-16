"""Isolated CLI tests. Only three fixed paths are changed in the fixture copy.

PATH contains strict mocks only. No real sudo/libvirt/QEMU calls are possible
through these tool names. Real ssh-keygen checks disposable test public keys.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml

REPO = Path(__file__).resolve().parents[1]
BASH, SSH_KEYGEN = shutil.which('bash'), shutil.which('ssh-keygen')
TOOLS = ('virsh', 'virt-install', 'qemu-img', 'sudo', 'restorecon', 'ssh-keygen',
         'iconv', 'mktemp', 'cp', 'chown', 'ln', 'rm', 'stat', 'test', 'id', 'getent')


class Lab:
    def __init__(self, root, key, scenario):
        self.root, self.storage = root, root / 'storage'
        for part in ('bin', 'storage', 'tmp'):
            (root / part).mkdir()
        self.disk = self.storage / 'demo.qcow2'
        (root / 'base.qcow2').write_bytes(b'base-image\n')
        (root / 'ssh.pub').write_bytes(key)
        text = (REPO / 'vm-manager.sh').read_text()
        replacements = {
            '"$HOME/VM-images/AlmaLinux-10-GenericCloud.qcow2"': root / 'base.qcow2',
            '"$HOME/.ssh/id_ed25519.pub"': root / 'ssh.pub',
            '"/var/lib/libvirt/images"': self.storage,
        }
        for old, new in replacements.items():
            if text.count(old) != 1:
                raise AssertionError(f'Expected one fixed path: {old}')
            text = text.replace(old, shlex.quote(str(new)))
        self.script = root / 'fixture.sh'
        self.script.write_text(text)
        stub = '#!' + sys.executable + '\n' + (REPO / 'tests/mock_tools.py').read_text()
        for tool in TOOLS:
            p = root / 'bin' / tool
            p.write_text(stub)
            p.chmod(0o755)
        self.env = dict(os.environ, PATH=str(root / 'bin'), LC_ALL='C.UTF-8',
                        TMPDIR=str(root / 'tmp'), VM_TEST_ROOT=str(root),
                        VM_TEST_SCENARIO=scenario, VM_TEST_SSH_KEYGEN=SSH_KEYGEN)
        for name in list(self.env):
            if name in ('BASH_ENV', 'ENV') or name.startswith('BASH_FUNC_'):
                del self.env[name]

    def run(self, *args):
        return subprocess.run([BASH, str(self.script), *args], env=self.env,
                              text=True, capture_output=True, timeout=20)

    def create(self, *resources):
        return self.run('create', 'demo', *(resources or ('2', '2048', '15')))

    def calls(self, tool=None):
        p = self.root / 'calls.jsonl'
        rows = [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []
        return [args for name, args in rows if tool is None or name == tool]

    def staging(self):
        return list(self.storage.glob('.demo.*.qcow2'))


class ProvisionerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASH or not SSH_KEYGEN:
            raise RuntimeError('Bash and OpenSSH ssh-keygen are required')
        cls.keys = tempfile.TemporaryDirectory(prefix='vm-provisioner-keys-')
        p = Path(cls.keys.name) / 'fixture'
        subprocess.run([SSH_KEYGEN, '-q', '-t', 'ed25519', '-N', '', '-C', 'fixture', '-f', str(p)], check=True)
        cls.prefix = ' '.join(p.with_suffix('.pub').read_text().split()[:2])

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    @contextmanager
    def lab(self, scenario='', key=None):
        with tempfile.TemporaryDirectory(prefix='vm-provisioner-test-') as name:
            if key is None:
                key = (self.prefix + ' normal@host\n').encode()
            yield Lab(Path(name), key, scenario)

    def assert_clean_preflight(self, lab):
        self.assertEqual(list(lab.storage.iterdir()), [])
        for tool in ('cp', 'qemu-img', 'chown', 'restorecon', 'ln', 'virt-install'):
            self.assertFalse(lab.calls(tool), tool)
        self.assertEqual(list((lab.root / 'tmp').iterdir()), [])

    def test_cli_commands(self):
        with self.lab() as lab:
            for args in ((), ('help',), ('list',), ('status', 'demo')):
                self.assertEqual(lab.run(*args).returncode, 0, args)
            for args in (('unknown',), ('status',), ('status', 'a', 'b')):
                self.assertNotEqual(lab.run(*args).returncode, 0, args)
            self.assert_clean_preflight(lab)

    def test_invalid_arguments_and_overflow(self):
        cases = [('create',), ('create', '../escape', '2', '2048', '15'),
                 ('create', 'Demo', '2', '2048', '15'), ('create', 'a'*31, '2', '2048', '15')]
        for resources in [('0','2048','15'), ('9','2048','15'), ('2','511','15'),
                          ('2','16385','15'), ('2','2048','9'), ('2','2048','101'),
                          ('2','text','15'), ('02','2048','15'), ('-1','2048','15'),
                          ('18446744073709551618','2048','15'), ('2','9'*1000,'15')]:
            cases.append(('create', 'demo', *resources))
        for args in cases:
            with self.subTest(args=args), self.lab() as lab:
                self.assertNotEqual(lab.run(*args).returncode, 0)
                self.assertFalse(lab.calls())
                self.assert_clean_preflight(lab)

    def test_comma_in_temp_dir_stops_before_storage_changes(self):
        # The mock mktemp creates its directory under this fixture root.
        with tempfile.TemporaryDirectory(prefix='vm-provisioner-test,') as name:
            lab = Lab(Path(name), (self.prefix + '\n').encode(), '')
            result = lab.create()
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('Use a TMPDIR without commas, quotes, or backslashes.', result.stderr)
            self.assertEqual(lab.calls('mktemp'), [['-d']])
            self.assertFalse(lab.calls('sudo'))
            self.assert_clean_preflight(lab)

    def test_missing_dependency(self):
        with self.lab() as lab:
            (lab.root / 'bin/qemu-img').unlink()
            result = lab.create()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing dependency', result.stderr)
            self.assert_clean_preflight(lab)

    def test_valid_boundaries_and_success(self):
        for resources in [('1','512','10'), ('8','16384','100')]:
            with self.subTest(resources=resources), self.lab() as lab:
                result = lab.create(*resources)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Guest readiness (cloud-init and SSH) has NOT been verified', result.stdout)
                self.assertEqual(lab.disk.read_bytes(), b'base-image\nguest-written\n')
                self.assertFalse(lab.staging())
                self.assertEqual(list((lab.root / 'tmp').iterdir()), [])

    def test_key_comments_preserved_in_yaml(self):
        comments = ['normal@host', "comment: Denys's computer", '"quoted" # hash \\slash',
                    'Український коментар 🐧', 'literal $(touch injected) `touch injected`', 'trailing spaces  ']
        for comment in comments:
            for ending in ('', '\n'):
                with self.subTest(comment=comment, ending=ending):
                    expected = self.prefix + ' ' + comment
                    with self.lab(key=(expected + ending).encode()) as lab:
                        result = lab.create()
                        self.assertEqual(result.returncode, 0, result.stderr)
                        data = yaml.safe_load((lab.root / 'captured-user-data').read_text())
                        self.assertEqual(data['users'][1]['ssh_authorized_keys'], [expected])
                        self.assertFalse((lab.root / 'injected').exists())
                        self.assertFalse(data['ssh_pwauth'])

    def test_bad_public_keys(self):
        valid = (self.prefix + ' comment').encode()
        cases = [b'', b'\n', b'not a key\n', b'ssh-ed25519 AAAA invalid\n',
                 valid+b'\n\n', valid+b'\n'+valid+b'\n', valid+b'\r\n',
                 valid+b'\x00junk', valid+b'\x01', valid+b'\xff', valid+'\u0080'.encode(), valid+'\u2028second'.encode(),
                 valid+'\ufffe'.encode(), valid+'\uffff'.encode()]
        for key in cases:
            with self.subTest(key=key), self.lab(key=key) as lab:
                self.assertNotEqual(lab.create().returncode, 0)
                self.assert_clean_preflight(lab)
                self.assertFalse(lab.calls('virsh'))

    def test_preflight_errors(self):
        cases = {'connection-fail':21, 'network-error':22, 'pool-error':23,
                 'network-inactive':1, 'pool-inactive':1, 'duplicate':1,
                 'sudo-auth-fail':31, 'sudo-policy-fail':32, 'storage-test-error':33, 'unsafe-storage':1}
        for scenario, code in cases.items():
            with self.subTest(scenario=scenario), self.lab(scenario) as lab:
                result = lab.create()
                self.assertEqual(result.returncode, code, result.stderr)
                self.assert_clean_preflight(lab)
                self.assertIn('Current domain state: UNKNOWN', result.stderr)

    def test_duplicate_matching_is_exact(self):
        with self.lab('similar-name') as lab:
            result = lab.create()
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_destination_types_remain_unchanged(self):
        for kind in ('file', 'symlink', 'dangling', 'directory'):
            with self.subTest(kind=kind), self.lab() as lab:
                target = lab.root / 'precious'
                original = b'valuable bytes\x00\xff'
                target.write_bytes(original)
                if kind == 'file':
                    lab.disk.write_bytes(original)
                elif kind == 'symlink':
                    lab.disk.symlink_to(target)
                elif kind == 'dangling':
                    lab.disk.symlink_to(lab.root / 'absent')
                else:
                    lab.disk.mkdir()
                    (lab.disk / 'marker').write_text('keep')
                before = lab.disk.lstat()
                result = lab.create()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(lab.disk.lstat().st_ino, before.st_ino)
                self.assertEqual(target.read_bytes(), original)
                if kind == 'file':
                    self.assertEqual(lab.disk.read_bytes(), original)
                if kind == 'directory':
                    self.assertEqual((lab.disk / 'marker').read_text(), 'keep')
                self.assertEqual(len(lab.staging()), 1)
                self.assertIn(str(lab.staging()[0]), result.stderr)
                self.assertFalse(lab.calls('virt-install'))

    def test_resize_failure_and_no_shrink(self):
        for scenario in ('resize-fail', 'base-too-large'):
            with self.subTest(scenario=scenario), self.lab(scenario) as lab:
                result = lab.create()
                self.assertEqual(result.returncode, 40, result.stderr)
                self.assertFalse(lab.disk.exists())
                self.assertEqual(len(lab.staging()), 1)
                self.assertIn(str(lab.staging()[0]), result.stderr)
                self.assertFalse(lab.calls('ln'))
                self.assertEqual(list((lab.root / 'tmp').iterdir()), [])

    def test_failures_after_publication(self):
        for scenario, code in [('refresh-fail',41), ('install-fail',42), ('final-label-fail',43), ('identity-fail',1), ('unlink-fail',44)]:
            with self.subTest(scenario=scenario), self.lab(scenario) as lab:
                result = lab.create()
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(lab.disk.read_bytes(), b'base-image\n')
                self.assertIn('Published disk preserved', result.stderr)
                self.assertIn('Current domain state: UNKNOWN', result.stderr)
                self.assertEqual(list((lab.root / 'tmp').iterdir()), [])
                if scenario == 'install-fail':
                    self.assertTrue((lab.root / 'possible-domain').exists())
                    self.assertIn('may have defined or started', result.stderr)
                else:
                    self.assertFalse(lab.calls('virt-install'))
                if scenario in ('final-label-fail', 'identity-fail', 'unlink-fail'):
                    self.assertEqual(len(lab.staging()), 1)

    def test_concurrent_creates(self):
        with self.lab('race') as lab:
            args = [BASH, str(lab.script), 'create', 'demo', '2', '2048', '15']
            procs = [subprocess.Popen(args, env=lab.env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(2)]
            try:
                output = [p.communicate(timeout=20) for p in procs]
            finally:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                        p.wait()
            self.assertEqual(sorted(p.returncode for p in procs), [0,1], output)
            self.assertEqual(lab.disk.read_bytes(), b'base-image\nguest-written\n')
            self.assertEqual(len(lab.calls('virt-install')), 1)
            self.assertEqual(len(lab.staging()), 1)
            self.assertEqual(list((lab.root / 'tmp').iterdir()), [])

    def test_guard_rejects_real_command(self):
        with self.lab() as lab:
            p = subprocess.run([str(lab.root / 'bin/sudo'), '-n', '--', '/usr/bin/true'],
                               env=lab.env, text=True, capture_output=True)
            self.assertEqual(p.returncode, 97)
            self.assertIn('unsafe or unexpected path', p.stderr)


if __name__ == '__main__':
    unittest.main()
