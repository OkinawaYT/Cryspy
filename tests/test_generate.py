import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ase.io import read

SCHEMA = Path(__file__).resolve().parents[2] / "DFTCommon" / "docs" / "schemas" / "structsearch_generate.schema.json"
TYPES = {"string": str, "integer": int, "number": (int, float), "object": dict, "array": list, "null": type(None)}


def _check(schema, v):
    """JSON Schema の部分実装(oneOf/const/type/required/properties/items)。jsonschema 未導入の venv 用。"""
    if "oneOf" in schema:
        ok = []
        for s in schema["oneOf"]:
            try:
                _check(s, v)
                ok.append(1)
            except AssertionError:
                pass
        assert len(ok) == 1, f"oneOf matched {len(ok)}"
        return
    if "const" in schema:
        assert v == schema["const"]
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        assert any(isinstance(v, TYPES[x]) and not (x in ("integer", "number") and isinstance(v, bool)) for x in ts), (t, v)
    for k in schema.get("required", []):
        assert k in v, f"missing {k}"
    for k, s in schema.get("properties", {}).items():
        if isinstance(v, dict) and k in v:
            _check(s, v[k])
    if "items" in schema and isinstance(v, list):
        for x in v:
            _check(schema["items"], x)


def validate(d):
    if SCHEMA.exists():
        _check(json.loads(SCHEMA.read_text()), d)
    else:
        assert d["status"] in ("ok", "error")


def run(*a):
    code = "import sys;from anl_cryspy.cli import main;sys.exit(main(sys.argv[1:]))"
    p = subprocess.run([sys.executable, "-c", code, "generate", *a, "--json"],
                       capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


def test_emt_e2e_contract(tmp_path):
    rc, d = run("--composition", "Cu2Au2", "--n", "2", "--seed", "1", "--out", str(tmp_path / "o"),
                "--calculator", "emt", "--algo", "rs", "--tot-struc", "4", "--workers", "2", "--timeout", "120")
    assert rc == 0, d
    validate(d)
    assert d["backend"] == "cryspy" and 1 <= len(d["structures"]) <= 2
    es = [s["energy_per_atom"] for s in d["structures"]]
    assert es == sorted(es)
    for s in d["structures"]:
        assert len(read(s["path"])) == s["natoms"]


@pytest.mark.skipif(not os.environ.get("ANL_CRYSPY_TEST_SLOW"), reason="set ANL_CRYSPY_TEST_SLOW=1")
def test_emt_ea_and_seed(tmp_path):
    a = ["--composition", "Cu2Au2", "--n", "3", "--seed", "2", "--calculator", "emt", "--algo", "ea",
         "--n-pop", "6", "--maxgen", "2", "--workers", "2"]
    _, x = run(*a, "--out", str(tmp_path / "x"))
    _, y = run(*a, "--out", str(tmp_path / "y"))
    validate(x)
    assert [s["energy_per_atom"] for s in x["structures"]] == [s["energy_per_atom"] for s in y["structures"]]


@pytest.mark.skipif(not os.environ.get("ANL_CRYSPY_TEST_CHGNET"), reason="set ANL_CRYSPY_TEST_CHGNET=1")
def test_chgnet_smoke(tmp_path):
    rc, d = run("--composition", "AuCu", "--n", "2", "--seed", "1", "--out", str(tmp_path / "o"),
                "--algo", "rs", "--tot-struc", "4", "--workers", "1")
    assert rc == 0, d
    validate(d)


@pytest.mark.parametrize("args", [
    ["--composition", "Xx3", "--n", "2"],
    ["--composition", "Cu2(", "--n", "2"],
    ["--composition", "Cu2Au2", "--n", "0"],
    ["--composition", "Cu2Au2", "--n", "2", "--calculator", "no-such-mlip"],
    ["--composition", "Cu7Au5", "--n", "2", "--max-atoms", "10", "--calculator", "emt"],
])
def test_bad_input_exit1(tmp_path, args):
    rc, d = run(*args, "--out", str(tmp_path / "o"))
    assert rc == 1 and d["status"] == "error" and d["code"]
    validate(d)


def test_out_not_empty(tmp_path):
    (tmp_path / "f").write_text("x")
    rc, d = run("--composition", "Cu2Au2", "--out", str(tmp_path), "--calculator", "emt")
    assert rc == 1 and d["code"] == "out_not_empty"


BASE = ["--composition", "Cu2Au2", "--n", "2", "--seed", "1", "--calculator", "emt",
        "--algo", "rs", "--tot-struc", "4", "--workers", "2", "--timeout", "120"]


def test_force_resume_manifest(tmp_path):
    out, wd = tmp_path / "o", tmp_path / "wd"
    args = [*BASE, "--out", str(out), "--workdir", str(wd)]
    rc, d = run(*args)
    assert rc == 0, d
    assert (out / ".anl_cryspy_manifest.json").is_file()
    # (a) default: out_not_empty
    rc, d = run(*args)
    assert rc == 1 and d["code"] == "out_not_empty"
    # (c) resume of a finished run: re-export without re-searching
    rc, d = run(*args, "--resume")
    assert rc == 0, d
    assert d["params"]["cryspy_invocations"] == 0
    # (d) unrelated files survive --force
    (out / "keep.txt").write_text("x")
    (wd / "keep_wd.txt").write_text("x")
    rc, d = run(*args, "--force")
    assert rc == 0, d
    assert (out / "keep.txt").exists() and (wd / "keep_wd.txt").exists()
    validate(d)


def test_force_without_manifest_refuses(tmp_path):
    (tmp_path / "f").write_text("x")
    rc, d = run(*BASE, "--out", str(tmp_path), "--force")
    assert rc == 1 and d["code"] == "out_not_empty"
    assert (tmp_path / "f").exists()


def test_resume_unavailable(tmp_path):
    rc, d = run(*BASE, "--out", str(tmp_path / "o"), "--resume")
    assert rc == 1 and d["code"] == "resume_unavailable"
    rc, d = run(*BASE, "--out", str(tmp_path / "o2"))  # temp workdir (deleted)
    rc, d = run(*BASE, "--out", str(tmp_path / "o2"), "--resume")
    assert rc == 1 and d["code"] == "resume_unavailable"


# ---- deadline / stall-timeout / EA fallback -------------------------------------------------
import signal
import time

from anl_cryspy import generate as G


def _fake(tmp_path, body):
    f = tmp_path / "fake_cryspy"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return f


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _gone(pid, t=5):
    end = time.time() + t
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("which", ["stall", "deadline"])
def test_watchdog_kills_group_only(tmp_path, monkeypatch, which):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "cryspy.stat").write_text("x")
    child = tmp_path / "child.pid"
    # 進捗を出さず sleep する偽 cryspy(孫プロセスも同グループに居る)
    fake = _fake(tmp_path, f"sleep 300 &\necho $! > {child}\necho $$ > {tmp_path}/main.pid\nsleep 300\n")
    monkeypatch.setenv("ANL_CRYSPY_BIN", str(fake))
    # 無関係なプロセス(別グループ)。巻き込まれないこと
    bystander = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        t0 = time.time()
        if which == "stall":
            r = G.run_cryspy(wd, dict(os.environ), time.time() + 100, 0.99, "RS", None, stall_timeout=2, kill_grace=1)
        else:
            r = G.run_cryspy(wd, dict(os.environ), time.time() + 2, 0.99, "RS", None, stall_timeout=100, kill_grace=1)
        assert r[2] == which and time.time() - t0 < 20
        assert _gone(int((tmp_path / "main.pid").read_text()))
        assert _gone(int(child.read_text()))
        assert bystander.poll() is None and _alive(bystander.pid)
    finally:
        bystander.kill()


def test_stall_not_triggered_with_progress(tmp_path, monkeypatch):
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "cryspy.stat").write_text("x")
    fake = _fake(tmp_path, f"for i in 1 2 3 4; do echo $i >> {wd}/log_cryspy; sleep 1; done\n"
                           f"echo 'Done all structures!' >> {wd}/log_cryspy\n")
    monkeypatch.setenv("ANL_CRYSPY_BIN", str(fake))
    r = G.run_cryspy(wd, dict(os.environ), time.time() + 60, 0.99, "RS", None, stall_timeout=3)
    assert r[2] is None


def test_generate_stall_no_structures_exit2(tmp_path, monkeypatch):
    monkeypatch.setenv("ANL_CRYSPY_BIN", str(_fake(tmp_path, "sleep 300\n")))
    code = "import sys;from anl_cryspy.cli import main;sys.exit(main(sys.argv[1:]))"
    p = subprocess.run([sys.executable, "-c", code, "generate", "--composition", "Cu2Au2", "--calculator", "emt",
                        "--algo", "rs", "--out", str(tmp_path / "o"), "--workdir", str(tmp_path / "w"),
                        "--timeout", "1", "--stall-timeout", "2", "--json"],
                       capture_output=True, text=True, env=os.environ)
    d = json.loads(p.stdout)
    assert p.returncode == 2 and d["status"] == "error" and d["code"] == "search_stalled"
    m = json.loads((tmp_path / "o" / ".anl_cryspy_manifest.json").read_text())


def test_ea_fallback_to_rs_small_natot(tmp_path):
    rc, d = run("--composition", "AuCu", "--n", "2", "--seed", "1", "--out", str(tmp_path / "o"),
                "--calculator", "emt", "--algo", "ea", "--max-atoms", "2", "--tot-struc", "4", "--timeout", "120")
    assert rc == 0, d
    validate(d)
    assert d["params"]["algo_effective"] == "rs" and d["params"]["algo_fallback"] == "ea->rs (natot<3)"
    assert d["warnings"] and all(s["meta"]["algo"] == "RS" for s in d["structures"])


def test_ea_fallback_disabled_and_reduced_counts():
    assert sum(G.reduced_counts({"Au": 2, "Cu": 2}, 2)) == 2   # max-atoms で既約化 -> natot=2
    assert sum(G.reduced_counts({"Au": 2, "Cu": 2}, 40)) == 4


def test_cryspy_in_native_seed():
    """csp-cryspy>=1.5 の再現性は [option] seed に依存する(1.5.1 で sitecustomize だけでは EA の seed 再現が崩れた)。"""
    cin, _, _ = G.build_cryspy_in({"Cu": 2, "Au": 2}, "EA", 20, 6, 2, 1, 40, seed=2)
    opt = cin.split("[option]", 1)[1]
    assert "seed = 2" in opt
    cin, _, _ = G.build_cryspy_in({"Cu": 2, "Au": 2}, "RS", 20, 6, 2, 1, 40, seed=-1)
    assert f"seed = {2**32 - 1}" in cin
    cin, _, _ = G.build_cryspy_in({"Cu": 2, "Au": 2}, "RS", 20, 6, 2, 1, 40)
    assert "seed" not in cin
