import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from anl_cryspy import versioning as V
from anl_cryspy import generate as G


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(V, "_cryspy_processes", lambda: [])
    monkeypatch.setenv("ANL_CRYSPY_AUTO_COMMIT", "0")  # 実リポジトリには決して触れない
    for k in V.BATCH_JOB_VARS + ("ANL_CRYSPY_UPDATE_IN_JOB",):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_update_skipped_in_batch_job(state, monkeypatch):
    monkeypatch.setattr(V, "installed_version", lambda python=None: "1.5.1")
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10.0: pytest.fail("must not query PyPI"))
    monkeypatch.setattr(V, "_run_uv", lambda a: pytest.fail("must not run uv"))
    monkeypatch.setenv("PBS_JOBID", "123.pbs")
    r = V.update()
    assert r["status"] == "skipped_batch_job" and r["after"] == "1.5.1"
    assert "login node" in r["warnings"][0]
    monkeypatch.setenv("ANL_CRYSPY_UPDATE_IN_JOB", "1")
    assert V.in_batch_job() is None


def test_is_newer():
    assert V.is_newer("1.5.1", "1.4.3")
    assert V.is_newer("1.5.1", "1.5")
    assert not V.is_newer("1.5.1", "1.5.1")
    assert not V.is_newer("1.5.0", "1.5.1")
    assert not V.is_newer("1.6.0rc1", "1.5.1")  # pre-release は最新扱いしない
    assert not V.is_newer(None, "1.5.1")
    assert V.is_newer("1.5.1", None)


def test_latest_pypi_version_parses(monkeypatch):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps({"info": {"version": "9.9.9"}}).encode()

    monkeypatch.setattr(V.urllib.request, "urlopen", lambda req, timeout: R())
    assert V.latest_pypi_version() == "9.9.9"


def _uv_recorder(monkeypatch, versions, rc=0):
    """installed_version を versions の順に返し、uv 呼び出しを記録する。"""
    calls = []
    seq = iter(versions)
    monkeypatch.setattr(V, "installed_version", lambda python=None: next(seq))
    monkeypatch.setattr(V, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(V, "_run_uv", lambda a: (calls.append(a), subprocess.CompletedProcess(a, rc, "", "boom"))[1])
    return calls


def test_update_installs_when_newer(state, monkeypatch):
    calls = _uv_recorder(monkeypatch, ["1.5.1", "1.6.0"])
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10: "1.6.0")
    r = V.update()
    assert r["status"] == "updated" and r["before"] == "1.5.1" and r["after"] == "1.6.0"
    assert calls == [["lock", "--upgrade-package", "csp-cryspy"], ["sync", "--inexact"]]


def test_update_noop_when_current(state, monkeypatch):
    calls = _uv_recorder(monkeypatch, ["1.5.1"])
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10: "1.5.1")
    assert V.update()["status"] == "up_to_date" and calls == []


def test_update_offline_continues(state, monkeypatch):
    calls = _uv_recorder(monkeypatch, ["1.5.1"])

    def boom(timeout=10):
        raise OSError("network unreachable")

    monkeypatch.setattr(V, "latest_pypi_version", boom)
    r = V.update()
    assert r["status"] == "skipped_offline" and r["after"] == "1.5.1" and r["warnings"] and calls == []


def test_update_skipped_when_other_run_active(state, monkeypatch):
    calls = _uv_recorder(monkeypatch, ["1.5.1"])
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10: "1.6.0")
    other = subprocess.Popen(["sleep", "30"])
    try:
        d = V._runs_dir()
        d.mkdir(parents=True)
        (d / f"{other.pid}.json").write_text(json.dumps({"pid": other.pid, "workdir": "/x"}))
        r = V.update()
        assert r["status"] == "skipped_busy" and calls == [] and r["active_runs"]
    finally:
        other.kill()
        other.wait()
    # 死んだ登録は掃除され、次は更新される
    calls2 = _uv_recorder(monkeypatch, ["1.5.1", "1.6.0"])
    assert V.update()["status"] == "updated" and len(calls2) == 2
    assert not list(V._runs_dir().glob("*.json"))


def test_own_registration_does_not_block(state, monkeypatch):
    f = V.register_run(Path("/x"))
    try:
        assert V.active_runs() == []
    finally:
        V.unregister_run(f)
    assert not f.exists()


def test_cryspy_process_detection(monkeypatch):
    out = ("  101 /a/.venv/bin/python3 /a/.venv/bin/cryspy -n\n"
           "  102 /a/.venv/bin/cryspy\n"
           "  103 vim cryspy.in\n"
           "  104 /usr/bin/python3 run_cryspy.py\n")
    monkeypatch.setattr(V.sp, "run", lambda *a, **k: types.SimpleNamespace(stdout=out))
    got = V._cryspy_processes()
    assert [g.split()[0] for g in got] == ["101", "102"]


def test_update_failure_reported(state, monkeypatch):
    _uv_recorder(monkeypatch, ["1.5.1", "1.5.1"], rc=1)
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10: "1.6.0")
    r = V.update()
    assert r["status"] == "failed" and r["after"] == "1.5.1" and "uv lock" in r["warnings"][0]


def test_auto_update_switch(monkeypatch):
    monkeypatch.delenv("ANL_CRYSPY_AUTO_UPDATE", raising=False)
    assert V.auto_update_enabled()
    assert not V.auto_update_enabled(no_update_flag=True)
    monkeypatch.setenv("ANL_CRYSPY_AUTO_UPDATE", "0")
    assert not V.auto_update_enabled()


def test_provenance_and_resume_warning(tmp_path):
    assert "not recorded" in V.resume_warning(tmp_path, "1.5.1")
    V.write_provenance(tmp_path, "1.5.1", {"status": "updated", "before": "1.4.3", "latest": "1.5.1", "after": "1.5.1"})
    p = V.read_provenance(tmp_path)
    assert p["version"] == "1.5.1" and p["auto_update"]["before"] == "1.4.3"
    assert V.resume_warning(tmp_path, "1.5.1") is None
    assert "1.5.1 -> 1.6.0" in V.resume_warning(tmp_path, "1.6.0")


def test_generate_updates_only_on_new_run(tmp_path, monkeypatch):
    """新規 run では update() が呼ばれ、--resume / --no-update では呼ばれない。"""
    called = []
    monkeypatch.delenv("ANL_CRYSPY_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(V, "update", lambda *a, **k: (called.append(1), {"status": "up_to_date", "warnings": []})[1])
    monkeypatch.setattr(G, "generate", lambda *a, **k: ([], {"truncated": None}))
    base = ["--composition", "Cu2Au2", "--calculator", "emt", "--json"]
    import argparse

    def ns(*extra):
        ap = argparse.ArgumentParser()
        G.add_args(ap)
        return ap.parse_args([*base, *extra])

    G.run(ns("--out", str(tmp_path / "a")))
    assert called == [1]
    G.run(ns("--out", str(tmp_path / "b"), "--no-update"))
    assert called == [1]
    G.run(ns("--out", str(tmp_path / "a"), "--resume"))
    assert called == [1]
    monkeypatch.setenv("ANL_CRYSPY_AUTO_UPDATE", "0")
    G.run(ns("--out", str(tmp_path / "c")))
    assert called == [1]


def test_generate_records_version(tmp_path):
    """実 CrySPY(EMT, RS 4 構造)で workdir に版が記録され、出力 JSON に載る。"""
    code = "import sys;from anl_cryspy.cli import main;sys.exit(main(sys.argv[1:]))"
    wd = tmp_path / "wd"
    p = subprocess.run([sys.executable, "-c", code, "generate", "--composition", "Cu2Au2", "--n", "2", "--seed", "1",
                        "--out", str(tmp_path / "o"), "--workdir", str(wd), "--calculator", "emt", "--algo", "rs",
                        "--tot-struc", "4", "--workers", "2", "--timeout", "120", "--json"],
                       capture_output=True, text=True)
    d = json.loads(p.stdout)
    assert p.returncode == 0, d
    from importlib.metadata import version
    v = version("csp-cryspy")
    assert V.read_provenance(wd)["version"] == v
    assert d["params"]["csp_cryspy_version"] == v and d["params"]["auto_update"] == "disabled"
    assert all(s["meta"]["backend_version"] == v for s in d["structures"])
    m = json.loads((tmp_path / "o" / ".anl_cryspy_manifest.json").read_text())
    assert m["csp_cryspy_version"] == v


def test_update_cli_check_json(monkeypatch, capsys):
    from anl_cryspy import cli
    monkeypatch.setattr(V, "check", lambda timeout=10: {"installed": "1.5.1", "latest": "1.6.0",
                                                         "update_available": True, "error": None})
    rc = cli.main(["update", "--check", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 1 and d["status"] == "update_available" and d["latest"] == "1.6.0"


# ---- uv.lock 自動コミット(一時 git リポジトリ) ---------------------------------------------

LOCK_OLD = ('version = 1\n[[package]]\nname = "csp-cryspy"\nversion = "1.5.1"\n'
            '[[package]]\nname = "numpy"\nversion = "2.0.0"\n')
LOCK_NEW = LOCK_OLD.replace('"1.5.1"', '"1.6.0"')
LOCK_NEW_CHAIN = LOCK_NEW.replace('"2.0.0"', '"2.1.0"')


def _g(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    for a in (["init", "-q", "-b", "main"], ["config", "user.name", "t"], ["config", "user.email", "t@x"]):
        _g(r, *a)
    (r / "uv.lock").write_text(LOCK_OLD)
    (r / "other.txt").write_text("a\n")
    _g(r, "add", "-A")
    _g(r, "commit", "-qm", "init")
    monkeypatch.setattr(V, "CRYSPY_ROOT", r)
    monkeypatch.setattr(V, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(V, "_cryspy_processes", lambda: [])
    monkeypatch.delenv("ANL_CRYSPY_AUTO_COMMIT", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10: "1.6.0")
    return r


def _fake_uv(monkeypatch, repo, new_lock, versions=("1.5.1", "1.6.0")):
    seq = iter(versions)
    monkeypatch.setattr(V, "installed_version", lambda python=None: next(seq))
    monkeypatch.setattr(V, "_uv", lambda: "/fake/uv")

    def run(a):
        if a[0] == "lock" and new_lock is not None:
            (repo / "uv.lock").write_text(new_lock)
        return subprocess.CompletedProcess(a, 0, "", "")

    monkeypatch.setattr(V, "_run_uv", run)


def _n_commits(repo):
    return int(_g(repo, "rev-list", "--count", "HEAD"))


def test_autocommit_only_uvlock(repo, monkeypatch):
    _fake_uv(monkeypatch, repo, LOCK_NEW)
    (repo / "other.txt").write_text("dirty\n")  # 持ち主の未コミット変更
    (repo / "new.txt").write_text("untracked\n")
    _g(repo, "add", "new.txt")  # 他のステージ済み変更も巻き込まない
    r = V.update()
    assert r["status"] == "updated" and r["commit"] == _g(repo, "rev-parse", "HEAD").strip()
    assert _g(repo, "show", "--name-only", "--format=%s", "HEAD").split() == \
        ["deps:", "csp-cryspy", "1.5.1", "->", "1.6.0", "(auto-update)", "uv.lock"]
    st = _g(repo, "status", "--porcelain")
    assert " M other.txt" in st and "A  new.txt" in st and "uv.lock" not in st
    assert "Co-Authored" not in _g(repo, "log", "-1", "--format=%B")


def test_autocommit_lists_chained_packages(repo, monkeypatch):
    _fake_uv(monkeypatch, repo, LOCK_NEW_CHAIN)
    V.update()
    assert "numpy 2.0.0 -> 2.1.0" in _g(repo, "log", "-1", "--format=%B")


def test_no_commit_when_lock_unchanged(repo, monkeypatch):
    _fake_uv(monkeypatch, repo, None)
    n = _n_commits(repo)
    r = V.update()
    assert r["status"] == "updated" and "commit" not in r and _n_commits(repo) == n


def test_no_commit_when_up_to_date(repo, monkeypatch):
    _fake_uv(monkeypatch, repo, LOCK_NEW, versions=("1.6.0",))
    n = _n_commits(repo)
    assert V.update()["status"] == "up_to_date" and _n_commits(repo) == n


def _skipped(repo, monkeypatch, needle):
    _fake_uv(monkeypatch, repo, LOCK_NEW)
    n = _n_commits(repo)
    r = V.update()
    assert r["status"] == "updated" and "commit" not in r
    assert any(needle in w for w in r["warnings"]), r["warnings"]
    assert _n_commits(repo) == n


def test_skip_dirty_lock(repo, monkeypatch):
    (repo / "uv.lock").write_text(LOCK_OLD + "# local edit\n")
    _skipped(repo, monkeypatch, "uncommitted changes")


def test_skip_detached_head(repo, monkeypatch):
    _g(repo, "checkout", "-q", "--detach")
    _skipped(repo, monkeypatch, "detached HEAD")


def test_skip_merge_in_progress(repo, monkeypatch):
    gd = repo / ".git"
    (gd / "MERGE_HEAD").write_text(_g(repo, "rev-parse", "HEAD"))
    _skipped(repo, monkeypatch, "MERGE_HEAD")


def test_skip_rebase_in_progress(repo, monkeypatch):
    (repo / ".git" / "rebase-merge").mkdir()
    _skipped(repo, monkeypatch, "rebase-merge")


def test_skip_no_git_identity(repo, monkeypatch):
    _g(repo, "config", "--unset", "user.email")
    _skipped(repo, monkeypatch, "user.email")


def test_skip_not_a_git_repo(tmp_path, monkeypatch):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "uv.lock").write_text(LOCK_OLD)
    monkeypatch.setattr(V, "CRYSPY_ROOT", d)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(V, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(V, "_cryspy_processes", lambda: [])
    monkeypatch.delenv("ANL_CRYSPY_AUTO_COMMIT", raising=False)
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10: "1.6.0")
    _fake_uv(monkeypatch, d, LOCK_NEW)
    r = V.update()
    assert r["status"] == "updated" and "commit" not in r and any("git" in w for w in r["warnings"])


def test_skip_hook_failure(repo, monkeypatch):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _skipped(repo, monkeypatch, "auto-commit failed")
    assert (repo / "uv.lock").read_text() == LOCK_NEW  # lock 自体は更新済みのまま


def test_disable_env_and_flag(repo, monkeypatch):
    _fake_uv(monkeypatch, repo, LOCK_NEW)
    n = _n_commits(repo)
    monkeypatch.setenv("ANL_CRYSPY_AUTO_COMMIT", "0")
    assert V.update()["status"] == "updated" and _n_commits(repo) == n
    monkeypatch.delenv("ANL_CRYSPY_AUTO_COMMIT")
    _g(repo, "checkout", "--", "uv.lock")
    _fake_uv(monkeypatch, repo, LOCK_NEW)
    assert V.update(auto_commit=False)["status"] == "updated" and _n_commits(repo) == n
    assert not V.auto_commit_enabled(no_commit_flag=True)


def test_provenance_records_commit(tmp_path):
    V.write_provenance(tmp_path, "1.6.0", {"status": "updated", "before": "1.5.1", "latest": "1.6.0",
                                           "after": "1.6.0", "commit": "abc123"})
    assert V.read_provenance(tmp_path)["auto_update"]["commit"] == "abc123"
