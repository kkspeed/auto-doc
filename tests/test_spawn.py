import dataclasses
import unittest

from harness import spawn


class RoleOutputDataclassTest(unittest.TestCase):
    def test_role_output_default_fields_set(self):
        r = spawn.RoleOutput(verdict="ok")
        self.assertEqual(r.verdict, "ok")
        self.assertIsNone(r.parsed)
        self.assertEqual(r.stderr_tail, "")
        self.assertEqual(r.elapsed_seconds, 0.0)
        self.assertEqual(r.retry_count, 0)

    def test_role_output_is_frozen(self):
        r = spawn.RoleOutput(verdict="ok")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.verdict = "spawn-failed"


if __name__ == "__main__":
    unittest.main()
