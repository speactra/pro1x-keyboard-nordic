import configparser
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_separate_physical_registry_entry(self):
        config = configparser.ConfigParser()
        config.read(ROOT/'packaging/pro1x_nordic.conf')
        self.assertEqual(config.sections(), ['pro1x_nordic'])
        self.assertEqual(config['pro1x_nordic']['name'], 'Pro1 X Nordic')
        self.assertTrue((ROOT/'src/xkb/symbols/pro1x_nordic').is_file())

    def test_direct_accents_not_dead_keys(self):
        symbol = (ROOT/'src/xkb/symbols/pro1x_nordic').read_text()
        self.assertNotIn('dead_acute', symbol)
        self.assertNotIn('dead_grave', symbol)
        self.assertNotIn('dead_diaeresis', symbol)
        self.assertNotIn('dead_circumflex', symbol)
        self.assertIn('[ acute, grave, grave, acute ]', symbol)

    def test_service_has_independent_recovery_and_watchdog(self):
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(ROOT/'packaging/pro1x-keyboard-nordic.service')
        self.assertEqual(config['Service']['Type'], 'notify')
        self.assertEqual(config['Service']['WatchdogSec'], '20')
        self.assertTrue(config['Service']['ExecStopPost'].endswith('service_runtime.py recover'))
        self.assertNotIn('PrivateDevices', config['Service'])

    def test_in_place_upgrade_rejected_before_payload_changes(self):
        import subprocess
        spec = (ROOT/'packaging/pro1x-keyboard-nordic.spec').read_text()
        pre = spec.split('%pre\n', 1)[1].split('%post\n', 1)[0]
        result = subprocess.run(['/bin/sh', '-c', pre, 'rpm-pre', '2'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('erase', result.stderr)
        fresh = subprocess.run(['/bin/sh', '-c', pre, 'rpm-pre', '1'], capture_output=True, text=True)
        self.assertEqual(fresh.returncode, 0)

    def test_removal_hook_uses_bounded_helper(self):
        spec = (ROOT/'packaging/pro1x-keyboard-nordic.spec').read_text()
        preun = spec.split('%preun\n', 1)[1].split('%postun\n', 1)[0]
        self.assertIn('remove_package.py || exit 1', preun)
        self.assertIn('if [ "$1" -eq 0 ]', preun)
        self.assertIn('/usr/lib/pro1x-keyboard-nordic/remove_package.py', spec.split('%files', 1)[1])

    def test_target_python_provider(self):
        spec = (ROOT/'packaging/pro1x-keyboard-nordic.spec').read_text()
        self.assertIn('Requires:       python3-base >= 3.8', spec)
        for phase in ('pre', 'preun'):
            self.assertIn('Requires(' + phase + '):  python3-base' if phase == 'pre' else 'Requires(preun): python3-base', spec)

    def test_compatible_rpm_and_opt_in_activation(self):
        spec = (ROOT/'packaging/pro1x-keyboard-nordic.spec').read_text()
        self.assertIn('%global _rpmformat 4', spec)
        self.assertIn('%global _binary_payload w9.gzdio', spec)
        self.assertIn('BuildArch:      noarch', spec)
        self.assertNotIn('Obsoletes:', spec)
        self.assertNotIn('systemctl start ', spec.split('%post\n', 1)[1].split('%preun', 1)[0])
        self.assertNotIn('/symbols/se\n', spec)
        self.assertIn('remove_package.py || exit 1', spec)


if __name__ == '__main__':
    unittest.main()
