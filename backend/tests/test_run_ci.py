import importlib.util
from pathlib import Path
import re
import unittest

from tests.run_ci import evaluate

OWNERS = [{"test": "tests.test_a.Kafka", "job": "kafka", "skip_reason": "needs kafka"}]
KAFKA_TEST = "tests.test_a.Kafka.test_one"
OTHER_TEST = "tests.test_b.T.test_x"


class EvaluateTests(unittest.TestCase):
    def test_clean_backend_run_with_owned_skip_passes(self):
        problems = evaluate("backend", [OTHER_TEST, KAFKA_TEST], [OTHER_TEST], {KAFKA_TEST: "needs kafka"}, [], OWNERS)
        self.assertEqual(problems, [])

    def test_clean_owning_job_passes(self):
        self.assertEqual(evaluate("kafka", [KAFKA_TEST], [KAFKA_TEST], {}, [], OWNERS), [])

    def test_zero_discovered_tests_fail(self):
        self.assertIn("No tests were discovered", evaluate("kafka", [], [], {}, [], []))

    def test_failed_test_fails(self):
        self.assertIn(f"Failed: {KAFKA_TEST}", evaluate("kafka", [KAFKA_TEST], [], {}, [KAFKA_TEST], OWNERS))

    def test_skip_without_owner_fails(self):
        problems = evaluate("backend", [OTHER_TEST], [], {OTHER_TEST: "why"}, [], [])
        self.assertTrue(any("no owning job" in problem for problem in problems))

    def test_changed_skip_reason_fails(self):
        problems = evaluate("backend", [KAFKA_TEST], [], {KAFKA_TEST: "something else"}, [], OWNERS)
        self.assertTrue(any("reason changed" in problem for problem in problems))

    def test_owning_job_may_not_skip(self):
        problems = evaluate("kafka", [KAFKA_TEST], [], {KAFKA_TEST: "needs kafka"}, [], OWNERS)
        self.assertTrue(any("Owned by this job but skipped" in problem for problem in problems))

    def test_owned_test_missing_from_owning_job_fails(self):
        problems = evaluate("kafka", [OTHER_TEST], [OTHER_TEST], {}, [], OWNERS)
        self.assertTrue(any("did not pass" in problem for problem in problems))

    def test_stale_manifest_entry_fails_backend(self):
        problems = evaluate("backend", [OTHER_TEST], [OTHER_TEST], {}, [], OWNERS)
        self.assertTrue(any("matches no test" in problem for problem in problems))

    def test_prefix_does_not_match_a_longer_name(self):
        longer = "tests.test_a.KafkaOther.test_one"
        problems = evaluate("backend", [longer], [], {longer: "needs kafka"}, [], OWNERS)
        self.assertTrue(any("no owning job" in problem for problem in problems))


class SubprocessHelperPathTests(unittest.TestCase):
    def test_every_subprocess_helper_module_exists(self):
        # Without this, a moved helper only fails in jobs that have the real service.
        pattern = re.compile(r'"(?:-m|-A)",\s*"(tests\.[\w.]+)')
        modules = {module for path in Path(__file__).parent.glob("test_*.py")
                   for module in pattern.findall(path.read_text(encoding="utf-8"))}
        self.assertTrue(modules)
        for module in sorted(modules):
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.util.find_spec(module))


if __name__ == "__main__":
    unittest.main()
