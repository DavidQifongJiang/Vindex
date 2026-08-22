import unittest

from app.services.retry_policy import attempts_exhausted, retry_delay_seconds


class RetryPolicyTest(unittest.TestCase):
    def test_retry_delay_uses_exponential_backoff_without_jitter(self):
        self.assertEqual(retry_delay_seconds(1, jitter_ratio=0), 30)
        self.assertEqual(retry_delay_seconds(2, jitter_ratio=0), 60)
        self.assertEqual(retry_delay_seconds(3, jitter_ratio=0), 120)

    def test_retry_delay_caps_large_attempts(self):
        self.assertEqual(
            retry_delay_seconds(10, base_seconds=30, cap_seconds=300, jitter_ratio=0),
            300,
        )

    def test_retry_delay_applies_testable_jitter(self):
        delay = retry_delay_seconds(
            1,
            base_seconds=30,
            jitter_ratio=0.2,
            jitter=lambda low, high: high,
        )

        self.assertEqual(delay, 36)

    def test_three_total_attempts_are_exhausted_on_attempt_three(self):
        self.assertFalse(attempts_exhausted(1, 3))
        self.assertFalse(attempts_exhausted(2, 3))
        self.assertTrue(attempts_exhausted(3, 3))


if __name__ == "__main__":
    unittest.main()
