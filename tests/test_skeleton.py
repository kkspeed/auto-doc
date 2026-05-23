import unittest


class SkeletonTest(unittest.TestCase):
    def test_python_version(self):
        import sys
        self.assertGreaterEqual(sys.version_info[:2], (3, 11),
                                "Requires Python 3.11+ for tomllib stdlib import")

    def test_tomllib_importable(self):
        import tomllib
        self.assertIsNotNone(tomllib)


if __name__ == "__main__":
    unittest.main()
