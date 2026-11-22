import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'updates'))
from reset_xui import reset, runtime_mounts


class ResetTests(unittest.TestCase):
    def test_only_xui_runtime_tmpfs_can_be_unmounted(self):
        row = '1 0 0:1 / /home/xui/tmp rw - tmpfs tmpfs rw'
        self.assertEqual(runtime_mounts(row, ''), ['/home/xui/tmp'])
        for mounted in [row.replace('tmpfs tmpfs', 'ext4 /dev/test'),
                        row.replace('/home/xui/tmp', '/home/xui/media'), row + '\n' + row]:
            with self.assertRaises(RuntimeError):
                runtime_mounts(mounted, '')
        with self.assertRaises(RuntimeError):
            runtime_mounts('', '/dev/test /home/xui/content ext4 defaults 0 0')

    def test_foreign_schema_aborts_before_stopping_services(self):
        command = Mock()
        sql = Mock(return_value='xui\nxui_migrate\nother\n')
        with self.assertRaises(RuntimeError):
            reset(command, sql)
        command.assert_not_called()
        self.assertEqual(sql.call_count, 1)
