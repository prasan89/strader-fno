import unittest
from pathlib import Path
from unittest.mock import patch

from strader_fno.http import ApiError
from strader_fno.service import install_service


class ServiceTests(unittest.TestCase):
    def test_failed_authentication_does_not_install_or_start_launch_agent(self):
        with patch('strader_fno.service.sys.platform', 'darwin'), \
             patch('strader_fno.settings.load_settings', return_value={'UPSTOX_ACCESS_TOKEN': 'test'}), \
             patch('strader_fno.brokers.Upstox.verify', side_effect=ApiError('Upstox', 401)), \
             patch('strader_fno.service.plistlib.dump') as dump, \
             patch('strader_fno.service.subprocess.run') as command:
            with self.assertRaises(ApiError):
                install_service(Path('/tmp/not-installed'))
            dump.assert_not_called()
            command.assert_not_called()
