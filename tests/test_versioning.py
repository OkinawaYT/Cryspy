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
    return tmp_path


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
