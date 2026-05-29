#!/usr/bin/env python3
"""
Provision & SYNC Keeper shared folders from SCIM teams -- lockout-safe,
strictly additive (never deletes).

For each unique team NAME: ensure one shared folder exists, grant every team
that carries that name (BY UID, so duplicate-named teams work), and optionally
ensure one starter record. Designed to be run REPEATEDLY (e.g. on a schedule)
as a reconciler -- it only ever adds what is missing.

Why grant by UID
----------------
SCIM can provision duplicate-named teams (one display name -> several team
UIDs). `share-folder -e "<name>"` then finds multiple matches and SILENTLY
skips the grant. Granting by team_uid is unambiguous, so grants actually land.

Lockout safety
--------------
--fetch-teams / --execute / --sync resume your PERSISTENT LOGIN through the
keepercommander *library* (never the `keeper` binary). A fail-closed LoginUi
raises on every interactive step and params.password stays empty, so the run
either resumes silently or aborts -- it never prompts and never submits an
empty master password.

No-delete by default
--------------------
By default the sync NEVER deletes anything. A team that disappears from SCIM
is flagged `absent` in the state file (with a timestamp) and left untouched.
Folders and records are NEVER deleted under any flag. The only removal the
tool can perform is revoking out-of-scope team GRANTS, and only when you
explicitly opt in with --prune-grants.

Modes
-----
  Stateful sync (the recommended, repeatable method):
    gen_team_folder_batch.py --sync --state ~/keeper-team-sync.json \
        --prefix "Team-" --permissions full --seed login \
        --seed-login svc@example.com --include "Departaments"
    (add --dry-run to preview without changing anything)

  One-shot generate a batch file:
    gen_team_folder_batch.py --fetch-teams --prefix "Team-" --seed login --out plan.batch

  Create a single record in the vault ROOT:
    gen_team_folder_batch.py --root-record "Shared Root Login" --root-login svc@example.com --execute
"""
import argparse
import csv
import json
import os
import re
import shlex
import sys
from collections import OrderedDict, Counter
from datetime import datetime, timezone

PERMS = {
    "full":       ["-u", "-r", "-s", "-e"],
    "edit-share": ["-s", "-e"],
    "edit-only":  ["-e"],
    "view-only":  [],
}
NAME_HINTS = ("team name", "name", "team")
DEFAULT_CONFIG = "~/.keeper/config.json"
STATE_VERSION = 1


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_title(cmd):
    m = re.search(r"--title\s+('([^']*)'|\"([^\"]*)\"|(\S+))", cmd or "")
    if not m:
        return None
    return next((g for g in (m.group(2), m.group(3), m.group(4)) if g), None)


# --------------------------------------------------------------------------
# Team sources
# --------------------------------------------------------------------------
def read_csv_groups(path):
    """CSV export -> OrderedDict name -> [] (no UIDs available offline)."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r]
    groups = OrderedDict()
    if not rows:
        return groups
    header = [c.strip().lower() for c in rows[0]]
    idx, start = 0, 0
    for i, col in enumerate(header):
        if col in NAME_HINTS:
            idx, start = i, 1
            break
    for r in rows[start:]:
        if len(r) > idx and r[idx].strip():
            groups.setdefault(r[idx].strip(), [])
    return groups


def safe_login(config_path):
    """Fail-closed persistent-login resume. Returns an authenticated params."""
    try:
        from keepercommander import api
        from keepercommander.__main__ import get_params_from_config
        from keepercommander.auth.login_steps import LoginUi
    except ImportError as e:
        sys.exit(f"keepercommander is required for live modes: {e}")

    class _Interactive(Exception):
        pass

    class FailClosedUi(LoginUi):
        def on_device_approval(self, s): raise _Interactive("device approval")
        def on_password(self, s):        raise _Interactive("master password")
        def on_two_factor(self, s):      raise _Interactive("two-factor")
        def on_sso_data_key(self, s):    raise _Interactive("SSO data key")
        def on_sso_redirect(self, s):    raise _Interactive("SSO redirect")

    cfg = os.path.expanduser(config_path)
    if not os.path.exists(cfg):
        sys.exit(f"No Keeper config at {cfg}; run `keeper login` first.")
    params = get_params_from_config(cfg)
    params.batch_mode = True
    if params.password:
        sys.exit("Refusing to run: a password is present in config (not persistent-login-only).")
    try:
        api.login(params, login_ui=FailClosedUi())
    except _Interactive as e:
        sys.exit(f"Persistent login not active ({e} required). Run `keeper login`. NOT prompting.")
    except Exception as e:
        sys.exit(f"Could not resume session ({type(e).__name__}: {e}). NOT prompting.")
    if not params.session_token:
        sys.exit("No session established; aborting without prompting.")
    api.sync_down(params)
    print(f"Persistent login OK as {params.user}.", file=sys.stderr)
    return params


def _node_map(ent):
    nodes = {}
    for n in ent.get("nodes", []):
        disp = (n.get("data") or {}).get("displayname")
        if not disp and n.get("parent_id", 0) == 0:
            disp = "[root]"
        nodes[n["node_id"]] = disp or str(n["node_id"])
    return nodes


def fetch_groups(params, node=None):
    """OrderedDict name -> [team_uid,...] from enterprise data.

    If `node` is given (name or node_id), only teams in that node are kept --
    which removes cross-node name collisions. Optional: omit it to span all nodes.
    """
    from keepercommander import api
    api.query_enterprise(params)
    ent = params.enterprise or {}
    nodes = _node_map(ent)

    node_ids = None
    if node:
        node_ids = {nid for nid, disp in nodes.items()
                    if str(nid) == str(node) or (disp and disp.lower() == node.lower())}
        if not node_ids:
            avail = ", ".join(sorted(set(nodes.values())))
            sys.exit(f"--node {node!r} matched no node. Available nodes: {avail}")
        print(f"--node {node!r} -> {sorted(node_ids)}", file=sys.stderr)

    groups = OrderedDict()
    for t in (ent.get("teams") or []):
        name = (t.get("name") or "").strip()
        if not name:
            continue
        if node_ids is not None and t.get("node_id") not in node_ids:
            continue
        groups.setdefault(name, []).append(t.get("team_uid"))
    dup = sum(1 for u in groups.values() if len(u) > 1)
    scope = f" in node {node!r}" if node else ""
    print(f"{len(groups)} unique team name(s){scope}; {dup} with duplicate UIDs "
          f"(omit --node and they span nodes; set --node to separate them).", file=sys.stderr)
    return groups


def vault_state(params):
    """Returns (folders{name->uid}, grants{folder_uid->set(team_uid)},
    rec_titles{folder_uid->set(title)})."""
    from keepercommander import vault as kv
    from keepercommander.subfolder import BaseFolderNode
    folders = {f.name: f.uid for f in params.folder_cache.values()
               if getattr(f, "type", None) == BaseFolderNode.SharedFolderType and f.name}
    grants, rec_titles = {}, {}
    for uid, sf in params.shared_folder_cache.items():
        grants[uid] = {t.get("team_uid") for t in sf.get("teams", [])}
        titles = set()
        for rp in sf.get("records", []):
            try:
                r = kv.KeeperRecord.load(params, rp.get("record_uid"))
                if r and r.title:
                    titles.add(r.title)
            except Exception:
                pass
        rec_titles[uid] = titles
    return folders, grants, rec_titles


# --------------------------------------------------------------------------
def filter_groups(groups, include, exclude):
    if include:
        inc = re.compile(include, re.I)
        groups = OrderedDict((n, u) for n, u in groups.items() if inc.search(n))
    if exclude:
        exc = re.compile(exclude, re.I)
        groups = OrderedDict((n, u) for n, u in groups.items() if not exc.search(n))
    return groups


def seed_command(folder, fq, a):
    if a.seed == "note":
        title = a.seed_title or f"{folder} - Welcome"
        return (f"record-add --record-type encryptedNotes --title {shlex.quote(title)} "
                f"--folder {fq} {shlex.quote('note=' + a.seed_text)}")
    if a.seed == "login":
        title = a.seed_title or f"{folder} - Shared Login"
        fields = []
        if a.seed_login:
            fields.append(shlex.quote(f"login={a.seed_login}"))
        fields.append(shlex.quote("password=$GEN"))
        return (f"record-add --record-type login --title {shlex.quote(title)} "
                f"--folder {fq} " + " ".join(fields))
    return None


# --------------------------------------------------------------------------
# Stateful sync (additive, no-delete)
# --------------------------------------------------------------------------
def load_state(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("teams", {})
        st.setdefault("records", {})
        return st
    return {"version": STATE_VERSION, "teams": {}, "records": {}, "runs": 0}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def run_sync(params, groups, a):
    """Reconcile teams -> folders/grants/records. Additive only; never deletes."""
    from keepercommander import api, cli
    now = _now()
    state = load_state(a.state)
    folders, grants, rec_titles = vault_state(params)

    plan = []  # (kind, command)
    for name, uids in groups.items():
        folder = a.prefix + name
        fq = shlex.quote(folder)
        fuid = folders.get(folder)
        if not fuid:
            plan.append(("folder", f"mkdir -sf {fq} {' '.join(PERMS[a.permissions])}".rstrip()))
        for uid in [u for u in uids if u]:
            if not (fuid and uid in grants.get(fuid, set())):
                plan.append(("grant", f"share-folder -a grant -e {shlex.quote(uid)} -p on -o on {fq}"))
        if a.seed:
            sc = seed_command(folder, fq, a)
            title = parse_title(sc)
            has_seed = (fuid and title in rec_titles.get(fuid, set())) or bool(state["records"].get(folder))
            if sc and not has_seed:
                plan.append(("seed", sc))

    # OPT-IN pruning: revoke grants that are out of the current scope. Off by
    # default -> strict no-delete. Never touches folders or records, only grants.
    if a.prune_grants:
        for name, uids in groups.items():
            folder = a.prefix + name
            fuid = folders.get(folder)
            if not fuid:
                continue
            desired = {u for u in uids if u}
            for guid in sorted(grants.get(fuid, set())):
                if guid and guid not in desired:
                    plan.append(("revoke",
                                 f"share-folder -a remove -e {shlex.quote(guid)} {shlex.quote(folder)}"))

    current_uids = {u for uids in groups.values() for u in uids if u}
    absent = [(uid, info.get("name")) for uid, info in state["teams"].items()
              if uid not in current_uids]

    kinds = Counter(k for k, _ in plan)
    print(f"SYNC PLAN: +{kinds.get('folder', 0)} folder(s), +{kinds.get('grant', 0)} grant(s), "
          f"+{kinds.get('seed', 0)} record(s), -{kinds.get('revoke', 0)} grant(s) [prune]. "
          f"Absent (KEPT, never deleted): {len(absent)}.", file=sys.stderr)
    for uid, nm in absent:
        print(f"  ABSENT (kept): {nm} [{uid}]", file=sys.stderr)

    if a.dry_run:
        for k, c in plan:
            print(f"[{k}] {c}")
        print("# dry-run: nothing executed, state NOT written.", file=sys.stderr)
        return

    for k, c in plan:
        try:
            cli.do_command(params, c)
            print(f"  OK [{k}]: {c[:78]}", file=sys.stderr)
        except Exception as e:
            print(f"  ERR [{k}]: {c[:78]} -> {type(e).__name__}: {e}", file=sys.stderr)

    # refresh truth, then update tracking state (additive)
    api.sync_down(params)
    folders, grants, rec_titles = vault_state(params)
    for name, uids in groups.items():
        folder = a.prefix + name
        fuid = folders.get(folder)
        if a.seed and fuid:
            t = parse_title(seed_command(folder, shlex.quote(folder), a))
            if t and t in rec_titles.get(fuid, set()):
                state["records"][folder] = t
        for uid in [u for u in uids if u]:
            ent = state["teams"].get(uid, {})
            ent.update({"name": name, "folder": folder, "folder_uid": fuid,
                        "granted": bool(fuid and uid in grants.get(fuid, set())),
                        "last_seen": now, "absent": False, "absent_since": None})
            ent.setdefault("first_seen", now)
            state["teams"][uid] = ent
    for uid, info in state["teams"].items():
        if uid not in current_uids and not info.get("absent"):
            info["absent"] = True
            info["absent_since"] = now  # flagged only -- NO DELETE
    state.update({"last_run": now, "tenant": params.user, "runs": state.get("runs", 0) + 1})
    save_state(a.state, state)
    print(f"State written to {a.state} (run #{state['runs']}).", file=sys.stderr)


# --------------------------------------------------------------------------
# One-shot generate / execute (kept for ad-hoc use)
# --------------------------------------------------------------------------
def build_plan(groups, existing, a):
    existing_lc = {n.lower() for n in existing}
    cmds, made, repaired, grants = [], 0, 0, 0
    if a.root_record:
        fields = []
        if a.root_login:
            fields.append(shlex.quote(f"login={a.root_login}"))
        fields.append(shlex.quote("password=$GEN"))
        cmds.append(f"record-add --record-type login --title {shlex.quote(a.root_record)} "
                    + " ".join(fields))
    flags = " ".join(PERMS[a.permissions])
    for name, uids in groups.items():
        folder = a.prefix + name
        fq = shlex.quote(folder)
        exists = folder.lower() in existing_lc
        if exists:
            cmds.append(f"# REPAIR (folder exists): {folder} -- re-granting only")
            repaired += 1
        else:
            cmds.append(f"mkdir -sf {fq} {flags}".rstrip())
            made += 1
        for tgt in ([u for u in uids if u] or [name]):
            cmds.append(f"share-folder -a grant -e {shlex.quote(tgt)} -p on -o on {fq}")
            grants += 1
        if not exists:
            sc = seed_command(folder, fq, a)
            if sc:
                cmds.append(sc)
        cmds.append("")
    return cmds, {"made": made, "repaired": repaired, "grants": grants}


def execute(params, cmds):
    from keepercommander import api, cli, vault

    def record_password(title):
        api.sync_down(params)
        for uid in list(params.record_cache):
            try:
                r = vault.KeeperRecord.load(params, uid)
            except Exception:
                continue
            if r and r.title == title:
                if isinstance(r, vault.TypedRecord):
                    f = r.get_typed_field("password")
                    return (f.get_default_value() if f else None), uid
                return getattr(r, "password", None), uid
        return None, None

    real = [c for c in cmds if c and not c.startswith("#")]
    ran = errors = 0
    canary_done = False
    for cmd in real:
        try:
            cli.do_command(params, cmd)
            ran += 1
            print(f"  OK: {cmd[:80]}", file=sys.stderr)
        except Exception as e:
            errors += 1
            print(f"  ERR: {cmd[:80]} -> {type(e).__name__}: {e}", file=sys.stderr)
        if not canary_done and cmd.startswith("record-add") and "--record-type login" in cmd:
            canary_done = True
            title = parse_title(cmd)
            pw, uid = record_password(title) if title else (None, None)
            if not pw:
                sys.exit(f"CANARY FAILED: '{title}' has no generated password ($GEN). Stopping.")
            print(f"  CANARY OK: '{title}' -> {len(pw)}-char password ({uid}).", file=sys.stderr)
    print(f"\nEXECUTED: {ran} ok, {errors} error(s).", file=sys.stderr)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("teams_csv", nargs="?", help="CSV from enterprise-info --teams --format csv")
    src.add_argument("--fetch-teams", action="store_true",
                     help="fetch teams via persistent login (lockout-safe)")
    ap.add_argument("--sync", action="store_true",
                    help="stateful reconcile (additive, no-delete); implies live fetch")
    ap.add_argument("--state", help="JSON state file for --sync (tracks teams by UID)")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--existing", help="list-sf CSV (offline idempotency)")
    ap.add_argument("--node", metavar="NAME|ID",
                    help="OPTIONAL: scope to a single enterprise node (name or node_id). "
                         "Omit to span all nodes (same-named teams across nodes then merge).")
    ap.add_argument("--prune-grants", action="store_true",
                    help="OPTIONAL: revoke team grants outside the current scope (e.g. the "
                         "other node's team when --node is set). Off by default = no-delete. "
                         "Only revokes grants; never deletes folders or records.")
    ap.add_argument("--include", metavar="REGEX", help="keep teams matching (case-insensitive)")
    ap.add_argument("--exclude", metavar="REGEX", help="drop teams matching (case-insensitive)")
    ap.add_argument("--prefix", default="", help='folder name prefix, e.g. "Team-"')
    ap.add_argument("--permissions", choices=list(PERMS), default="edit-only")
    ap.add_argument("--seed", choices=["note", "login"], help="ensure one record per folder")
    ap.add_argument("--seed-title")
    ap.add_argument("--seed-text", default="Team vault provisioned.")
    ap.add_argument("--seed-login", metavar="USER")
    ap.add_argument("--root-record", metavar="TITLE", help="create one login record in vault ROOT")
    ap.add_argument("--root-login", metavar="USER")
    ap.add_argument("--out", default="provision-team-folders.batch")
    ap.add_argument("--dry-run", action="store_true", help="preview only; change nothing")
    ap.add_argument("--execute", action="store_true", help="run the plan now (canary-gated)")
    a = ap.parse_args()

    # ---- SYNC mode ----
    if a.sync:
        if not a.state:
            sys.exit("--sync requires --state <file>")
        params = safe_login(a.config)
        groups = fetch_groups(params, a.node)
        if a.include or a.exclude:
            before = len(groups)
            groups = filter_groups(groups, a.include, a.exclude)
            print(f"Filtered {before} -> {len(groups)} team(s).", file=sys.stderr)
        run_sync(params, groups, a)
        return

    # ---- one-shot generate / execute ----
    params = None
    if a.fetch_teams or a.execute:
        params = safe_login(a.config)
        groups = fetch_groups(params, a.node) if a.fetch_teams else OrderedDict()
        from keepercommander.subfolder import BaseFolderNode
        existing = ({f.name for f in params.folder_cache.values()
                     if getattr(f, "type", None) == BaseFolderNode.SharedFolderType and f.name}
                    if a.fetch_teams else set())
    elif a.teams_csv:
        groups = read_csv_groups(a.teams_csv)
        existing = set(read_csv_groups(a.existing).keys()) if a.existing else set()
    else:
        groups, existing = OrderedDict(), set()

    if a.include or a.exclude:
        before = len(groups)
        groups = filter_groups(groups, a.include, a.exclude)
        print(f"Filtered {before} -> {len(groups)} team(s).", file=sys.stderr)
    if not groups and not a.root_record:
        sys.exit("Nothing to do: no teams and no --root-record.")

    cmds, stats = build_plan(groups, existing, a)
    print(f"Plan: {stats['made']} new, {stats['repaired']} repaired, {stats['grants']} grant(s)"
          f"{', + root record' if a.root_record else ''}.", file=sys.stderr)

    if a.execute:
        if params is None:
            params = safe_login(a.config)
        execute(params, cmds)
        return

    text = "\n".join([
        "# Auto-generated by gen_team_folder_batch.py -- REVIEW before running.",
        f"# Run from keeper shell:  run-batch {a.out}",
        f"# folders_new={stats['made']} repaired={stats['repaired']} grants={stats['grants']}",
        "",
    ] + cmds) + "\n"
    if a.dry_run:
        sys.stdout.write(text)
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {a.out}.", file=sys.stderr)


if __name__ == "__main__":
    main()
