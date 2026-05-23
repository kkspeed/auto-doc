import unittest

from harness import claim_graph as cg


def _empty_registry():
    return cg.CanonicalSlugRegistry()


class AddCanonicalPositionTest(unittest.TestCase):
    def test_first_canonical_for_new_decision_creates_entry(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        self.assertEqual(reg.data["retry-policy"]["canonical"], ["expo-backoff"])
        self.assertEqual(reg.data["retry-policy"]["aliases"], {})

    def test_additional_canonical_for_existing_decision_appends(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "linear-no-backoff")
        self.assertEqual(set(reg.data["retry-policy"]["canonical"]),
                         {"expo-backoff", "linear-no-backoff"})

    def test_adding_duplicate_canonical_is_noop(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")   # dup
        self.assertEqual(reg.data["retry-policy"]["canonical"], ["expo-backoff"])

    def test_adding_alias_key_as_canonical_fails(self):
        # If a slug exists as an alias key, it cannot be added as canonical.
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        with self.assertRaises(cg.RegistryInvariantError):
            cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")


class RegisterAliasTest(unittest.TestCase):
    def test_register_alias_to_canonical_succeeds(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        # from slug moved out of canonical, into aliases
        self.assertNotIn("exponential-backoff", reg.data["retry-policy"]["canonical"])
        self.assertEqual(reg.data["retry-policy"]["aliases"]["exponential-backoff"],
                         "expo-backoff")
        self.assertIn("expo-backoff", reg.data["retry-policy"]["canonical"])

    def test_register_alias_to_non_canonical_fails(self):
        # 'to' MUST be in canonical list
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        with self.assertRaises(cg.RegistryInvariantError) as cm:
            cg.register_alias(reg, "retry-policy",
                              "exponential-backoff", "novel-slug")
        self.assertIn("not canonical", str(cm.exception).lower())

    def test_register_alias_from_non_canonical_fails(self):
        # 'from' must currently be canonical (else nothing to rewrite)
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        with self.assertRaises(cg.RegistryInvariantError):
            cg.register_alias(reg, "retry-policy",
                              "never-existed", "expo-backoff")

    def test_alias_keys_are_append_only(self):
        # Once a slug is an alias key, it cannot be re-pointed.
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "expo-bo")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        # Now try to re-point 'exponential-backoff' to 'expo-bo'
        with self.assertRaises(cg.RegistryInvariantError) as cm:
            cg.register_alias(reg, "retry-policy",
                              "exponential-backoff", "expo-bo")
        self.assertIn("already an alias", str(cm.exception).lower())

    def test_canonical_list_cannot_shrink_except_via_register_alias(self):
        # There is no remove_canonical operation; the only way out of canonical
        # is via register_alias. We test this by trying to call register_alias
        # with both from and to as canonical (legal) and verify the from is now
        # absent from canonical.
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "aa")
        cg.add_canonical_position(reg, "retry-policy", "bb")
        cg.register_alias(reg, "retry-policy", "aa", "bb")
        self.assertEqual(reg.data["retry-policy"]["canonical"], ["bb"])
        # Verify no public remove method exists
        self.assertFalse(hasattr(cg, "remove_canonical_position"))


class RewritePositionTest(unittest.TestCase):
    def test_rewrite_alias_returns_canonical(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        cg.add_canonical_position(reg, "retry-policy", "exponential-backoff")
        cg.register_alias(reg, "retry-policy", "exponential-backoff", "expo-backoff")
        self.assertEqual(
            cg.rewrite_position_to_canonical(reg, "retry-policy",
                                             "exponential-backoff"),
            "expo-backoff",
        )

    def test_rewrite_canonical_returns_self(self):
        reg = _empty_registry()
        cg.add_canonical_position(reg, "retry-policy", "expo-backoff")
        self.assertEqual(
            cg.rewrite_position_to_canonical(reg, "retry-policy", "expo-backoff"),
            "expo-backoff",
        )

    def test_rewrite_unknown_slug_returns_self(self):
        reg = _empty_registry()
        self.assertEqual(
            cg.rewrite_position_to_canonical(reg, "retry-policy", "novel"),
            "novel",
        )


if __name__ == "__main__":
    unittest.main()
