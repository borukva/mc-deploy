import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from deploy import parse_targets, plan_release, route, stale_for_rule


class ParseTargets(unittest.TestCase):
    def test_parses_lines_and_comments(self):
        rules = parse_targets(
            "# коментар\n"
            "mindbattle-pvp-*.jar -> mindbattle-pvp\n"
            "\n"
            "mindbattle-*.jar     -> mindbattle-main  # інлайн-коментар\n"
        )
        self.assertEqual(
            rules,
            [("mindbattle-pvp-*.jar", "mindbattle-pvp"),
             ("mindbattle-*.jar", "mindbattle-main")],
        )

    def test_rejects_line_without_arrow(self):
        with self.assertRaises(ValueError):
            parse_targets("mindbattle-*.jar mindbattle-main\n")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            parse_targets("# only comments\n")


class Route(unittest.TestCase):
    RULES = [("mindbattle-pvp-*.jar", "pvp"), ("mindbattle-*.jar", "main")]

    def test_first_match_wins(self):
        got = route(["mindbattle-pvp-0.6.0.jar", "mindbattle-0.6.0.jar"], self.RULES)
        self.assertEqual(got, {0: ["mindbattle-pvp-0.6.0.jar"], 1: ["mindbattle-0.6.0.jar"]})

    def test_ignores_service_jars_and_foreign_names(self):
        got = route(
            ["mindbattle-0.6.0-sources.jar", "mindbattle-0.6.0-javadoc.jar",
             "mindbattle-0.6.0-dev.jar", "bridge-1.0.jar"],
            self.RULES,
        )
        self.assertEqual(got, {})


class PlanRelease(unittest.TestCase):
    RULES = [("abyss-*.jar", "abyss")]

    def test_exactly_one_asset_per_rule(self):
        self.assertEqual(plan_release(["abyss-1.2.3.jar"], self.RULES), {0: "abyss-1.2.3.jar"})

    def test_zero_matches_is_error(self):
        with self.assertRaises(ValueError):
            plan_release(["other-1.0.jar"], self.RULES)

    def test_two_matches_is_error(self):
        with self.assertRaises(ValueError):
            plan_release(["abyss-1.jar", "abyss-2.jar"], self.RULES)


class StaleForRule(unittest.TestCase):
    RULES = [("mindbattle-pvp-*.jar", "pvp"), ("mindbattle-*.jar", "main")]

    def test_only_same_rule_jars_are_stale(self):
        remote = ["mindbattle-0.5.9.jar", "mindbattle-pvp-0.5.9.jar",
                  "bridge-1.0.jar", "fabric-api-0.154.2.jar"]
        self.assertEqual(
            stale_for_rule(remote, self.RULES, 1, keep="mindbattle-0.6.0.jar"),
            ["mindbattle-0.5.9.jar"],
        )

    def test_keep_is_not_stale(self):
        self.assertEqual(
            stale_for_rule(["abyss-1.2.3.jar"], [("abyss-*.jar", "abyss")], 0,
                           keep="abyss-1.2.3.jar"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
