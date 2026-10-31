import unittest


@unittest.skip('negative test')
class Negative(unittest.TestCase):
    def test_skips(self):
        pass
