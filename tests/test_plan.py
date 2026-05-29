"""Unit tests for the pure (no-vault) logic of gen_team_folder_batch.

Covers CSV parsing, filtering, seed/title generation, and the plan builder
including the critical guarantees: grant-by-UID, dedupe, repair-existing,
single seed, and additive-only (no destructive verbs emitted).

Run:  python3 -m unittest discover -s tests -v
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gen_team_folder_batch as g  # noqa: E402


def args(**kw):
    """Build an argparse-like namespace with sensible defaults."""
    base = dict(prefix="Team-", permissions="full", seed=None, seed_title=None,
                seed_text="Team vault provisioned.", seed_login=None,
                root_record=None, root_login=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestParseTitle(unittest.TestCase):
    def test_single_quoted(self):
        self.assertEqual(g.parse_title("record-add --title 'Hello World' --folder x"), "Hello World")

    def test_double_quoted(self):
        self.assertEqual(g.parse_title('record-add --title "Hi" x'), "Hi")

    def test_bare(self):
        self.assertEqual(g.parse_title("record-add --title Solo x"), "Solo")

    def test_none(self):
        self.assertIsNone(g.parse_title("record-add --folder x"))


class TestSeedCommand(unittest.TestCase):
    def test_login_with_user(self):
        cmd = g.seed_command("Team-Legal", "'Team-Legal'", args(seed="login", seed_login="svc@example.com"))
        self.assertIn("--record-type login", cmd)
        self.assertIn("login=svc@example.com", cmd)
        self.assertIn("'password=$GEN'", cmd)

    def test_login_without_user_omits_login_field(self):
        cmd = g.seed_command("Team-Legal", "'Team-Legal'", args(seed="login", seed_login=None))
        self.assertNotIn("login=", cmd)
        self.assertIn("'password=$GEN'", cmd)

    def test_note(self):
        cmd = g.seed_command("Team-Legal", "'Team-Legal'", args(seed="note", seed_text="hi there"))
        self.assertIn("--record-type encryptedNotes", cmd)
        self.assertIn("'note=hi there'", cmd)

    def test_no_seed(self):
        self.assertIsNone(g.seed_command("f", "'f'", args(seed=None)))

    def test_custom_title(self):
        cmd = g.seed_command("Team-Legal", "'Team-Legal'", args(seed="login", seed_title="Custom"))
        self.assertIn("--title Custom", cmd)


class TestFilterGroups(unittest.TestCase):
    def setUp(self):
        self.groups = g.OrderedDict([("Dept - Legal", ["a"]), ("Role Policies", ["b"]),
                                     ("Dept - Sales", ["c"]), ("MIGTEST-x", ["d"])])

    def test_include(self):
        out = g.filter_groups(self.groups, "Dept", None)
        self.assertEqual(list(out), ["Dept - Legal", "Dept - Sales"])

    def test_exclude(self):
        out = g.filter_groups(self.groups, None, "Role Policies|MIGTEST")
        self.assertEqual(list(out), ["Dept - Legal", "Dept - Sales"])

    def test_include_then_exclude(self):
        out = g.filter_groups(self.groups, "Dept|MIGTEST", "Sales")
        self.assertEqual(list(out), ["Dept - Legal", "MIGTEST-x"])

    def test_case_insensitive(self):
        self.assertEqual(list(g.filter_groups(self.groups, "dept", None)), ["Dept - Legal", "Dept - Sales"])


class TestReadCsvGroups(unittest.TestCase):
    def _write(self, text):
        path = os.path.join(self.tmp, "t.csv")
        with open(path, "w") as f:
            f.write(text)
        return path

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_headered(self):
        p = self._write("Team Name,Node\nLegal,Root\nSales,Root\n")
        self.assertEqual(list(g.read_csv_groups(p)), ["Legal", "Sales"])

    def test_headerless(self):
        p = self._write("Legal\nSales\n")
        self.assertEqual(list(g.read_csv_groups(p)), ["Legal", "Sales"])

    def test_quoted_commas(self):
        p = self._write('Team Name\n"Legal, EMEA"\n')
        self.assertEqual(list(g.read_csv_groups(p)), ["Legal, EMEA"])

    def test_empty(self):
        p = self._write("")
        self.assertEqual(list(g.read_csv_groups(p)), [])


class TestBuildPlan(unittest.TestCase):
    def test_grant_by_uid_for_duplicate_names(self):
        # one name, two UIDs (cross-node) -> ONE folder, TWO grants by UID
        groups = g.OrderedDict([("Legal", ["uidA", "uidB"])])
        cmds, stats = g.build_plan(groups, existing=set(), a=args(seed=None))
        mkdirs = [c for c in cmds if c.startswith("mkdir")]
        grants = [c for c in cmds if c.startswith("share-folder -a grant")]
        self.assertEqual(len(mkdirs), 1)
        self.assertEqual(len(grants), 2)
        self.assertIn("-e uidA", grants[0] + grants[1])
        self.assertIn("-e uidB", grants[0] + grants[1])
        self.assertEqual(stats["made"], 1)

    def test_existing_folder_repairs_grants_no_mkdir_no_seed(self):
        groups = g.OrderedDict([("Legal", ["uidA"])])
        cmds, stats = g.build_plan(groups, existing={"Team-Legal"}, a=args(seed="login", seed_login="x"))
        self.assertFalse(any(c.startswith("mkdir") for c in cmds))
        self.assertFalse(any(c.startswith("record-add") for c in cmds))  # no new seed on existing
        self.assertTrue(any(c.startswith("share-folder -a grant") for c in cmds))
        self.assertEqual(stats["repaired"], 1)

    def test_seed_once_for_new_folder(self):
        groups = g.OrderedDict([("Legal", ["uidA", "uidB"])])
        cmds, _ = g.build_plan(groups, existing=set(), a=args(seed="login", seed_login="x"))
        seeds = [c for c in cmds if c.startswith("record-add")]
        self.assertEqual(len(seeds), 1)  # one record per folder, not per UID

    def test_root_record_emitted_first(self):
        cmds, _ = g.build_plan(g.OrderedDict(), set(), args(root_record="Root Login", root_login="x"))
        self.assertTrue(cmds[0].startswith("record-add --record-type login --title 'Root Login'"))
        self.assertNotIn("--folder", cmds[0])  # root => no folder

    def test_csv_fallback_grants_by_name_when_no_uids(self):
        groups = g.OrderedDict([("Legal", [])])  # CSV mode: no UIDs
        cmds, _ = g.build_plan(groups, set(), args(seed=None))
        grants = [c for c in cmds if c.startswith("share-folder -a grant")]
        self.assertEqual(len(grants), 1)
        self.assertIn("-e Legal", grants[0])


class TestNoDestructiveVerbs(unittest.TestCase):
    """build_plan must NEVER emit a delete/revoke/remove command."""
    def test_no_destructive_commands(self):
        groups = g.OrderedDict([("Legal", ["uidA", "uidB"]), ("Sales", ["uidC"])])
        for existing in (set(), {"Team-Legal"}):
            cmds, _ = g.build_plan(groups, existing, args(seed="login", seed_login="x",
                                                          root_record="R", root_login="x"))
            for c in cmds:
                low = c.lower()
                self.assertFalse(c.startswith(("rm ", "share-folder -a remove")),
                                 f"destructive command emitted: {c}")
                self.assertNotIn("--purge", low)


if __name__ == "__main__":
    unittest.main()
