import unittest
from unittest.mock import patch
import apple_reference as ref


class ReferenceTests(unittest.TestCase):
    def test_family_generation_and_screen_must_match(self):
        self.assertTrue(ref.model_status('MACBOOK PRO M5 14"'))
        self.assertFalse(ref.model_status('MACBOOK PRO M5 13"'))
        self.assertFalse(ref.model_status('AIRPODS PRO 4'))
        self.assertTrue(ref.model_status('AIRPODS 4 ANC'))
        self.assertFalse(ref.model_status('IPHONE 19 PRO MAX'))

    def test_failed_refresh_keeps_last_verified_index(self):
        before = dict(ref.index)
        with patch.object(ref.urllib.request, 'urlopen', side_effect=OSError):
            ref.refresh(force=True)
        self.assertEqual(ref.index, before)
