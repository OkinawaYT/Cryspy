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
