import importlib.util
from pathlib import Path
import stat
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('check_artifacts',ROOT/'tools/check_artifacts.py')
assert spec is not None and spec.loader is not None
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def entry(name,data=b'',mode=stat.S_IFREG|0o644):
    encoded=name.encode()+b'\0'
    fields=[1,mode,0,0,1,0,len(data),0,0,0,0,len(encoded),0]
    header=b'070701'+b''.join(('{:08x}'.format(v)).encode() for v in fields)
    prefix=header+encoded
    prefix+=b'\0'*((-len(prefix))%4)
    return prefix+data+b'\0'*((-len(data))%4)


class ArchiveTests(unittest.TestCase):
    def test_actual_cpio_record_format_and_content(self):
        data=entry('./usr/share/example',b'hello')+entry('TRAILER!!!')
        result=m.cpio_entries(data)
        self.assertEqual(result['usr/share/example'][1],b'hello')

    def test_payload_path_traversal_refused(self):
        for name in ('../etc/passwd','/etc/passwd','usr/../../etc/passwd'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                m.cpio_entries(entry(name,b'x')+entry('TRAILER!!!'))

    def test_symlink_refused(self):
        with self.assertRaises(ValueError):
            m.cpio_entries(entry('usr/link',b'../../../etc',stat.S_IFLNK|0o777)+entry('TRAILER!!!'))

    def test_duplicate_paths_refused(self):
        with self.assertRaises(ValueError):
            m.cpio_entries(entry('usr/item')+entry('usr/item')+entry('TRAILER!!!'))


if __name__=='__main__':unittest.main()
