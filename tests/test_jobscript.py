import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from anl_cryspy import jobscript as J
from anl_cryspy import versioning as V

ROOT = Path(__file__).resolve().parents[1]


def test_pbs_header_and_body():
    t = J.render("pbs")
    assert "#PBS -q i2cpu" in t and "#SBATCH" not in t
    assert "ncpus=126" in t and "#PBS -m e -M" in t


def test_slurm_header_and_body():
    t = J.render("slurm")
    assert "#SBATCH -p i8cpu" in t and "#SBATCH -c 16" in t and "#PBS" not in t
    assert "--mail-type=FAIL" in t


@pytest.mark.parametrize("sched", ["pbs", "slurm"])
def test_body_keeps_cryspy_vars_and_no_update(sched):
    t = J.render(sched)
    for s in ("CRYSPY_BIN", "RUN_CRYSPY", "ANL_CRYSPY_AUTO_UPDATE=0", "SLURM_SUBMIT_DIR", "PBS_O_WORKDIR",
              "SLURM_CPUS_PER_TASK", "PBS_NCPUS"):
        assert s in t
    assert "SLURM_JOB_ID" in V.BATCH_JOB_VARS and "PBS_JOBID" in V.BATCH_JOB_VARS
    p = subprocess.run(["bash", "-n", "/dev/stdin"], input=t, text=True, capture_output=True)
    assert p.returncode == 0, p.stderr


def test_cluster_mapping_and_queue_override():
    assert J.scheduler_for("systemB") == "slurm" and J.scheduler_for("systemC") == "pbs"
    assert J.scheduler_for(None, "pbs") == "pbs"
    with pytest.raises(ValueError):
        J.scheduler_for("nope")
    assert "#SBATCH -p F1cpu" in J.render("slurm", queue="F1cpu")


def test_committed_scripts_match_generator():
    assert (ROOT / "job_pbs.sh").read_text() == J.render("pbs")
    assert (ROOT / "job_slurm.sh").read_text() == J.render("slurm")
    assert (ROOT / "job.sh").read_text() == J.render("pbs")


def test_cli_jobscript(tmp_path):
    out = tmp_path / "job.sh"
    code = "import sys;from anl_cryspy.cli import main;sys.exit(main(sys.argv[1:]))"
    p = subprocess.run([sys.executable, "-c", code, "jobscript", "--cluster", "systemB", "-o", str(out)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert out.read_text() == J.render("slurm") and os.access(out, os.X_OK)


# ---- run_cryspy.py 経路の版記録 -------------------------------------------------------------

@pytest.fixture
def rc(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    import run_cryspy
    for k in V.BATCH_JOB_VARS + ("ANL_CRYSPY_UPDATE_IN_JOB",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(V, "installed_version", lambda python=None: "1.5.1")
    return run_cryspy


@pytest.mark.parametrize("var", ["PBS_JOBID", "SLURM_JOB_ID"])
def test_run_cryspy_records_skipped_batch_job(rc, tmp_path, monkeypatch, var):
    monkeypatch.setenv(var, "42")
    monkeypatch.setenv("ANL_CRYSPY_AUTO_UPDATE", "0")  # job.sh と同じ
    monkeypatch.setattr(V, "latest_pypi_version", lambda timeout=10.0: pytest.fail("no PyPI in job"))
    r = rc.record_version(tmp_path)
    p = V.read_provenance(tmp_path)
    assert p["version"] == "1.5.1" and p["auto_update"]["status"] == "skipped_batch_job"
    assert r["warnings"] and not r["resumed"]


def test_run_cryspy_records_disabled_outside_job(rc, tmp_path, monkeypatch):
    monkeypatch.setenv("ANL_CRYSPY_AUTO_UPDATE", "0")
    rc.record_version(tmp_path)
    assert V.read_provenance(tmp_path)["auto_update"]["status"] == "disabled"


def test_run_cryspy_resume_warns_on_mismatch(rc, tmp_path, monkeypatch):
    monkeypatch.setenv("ANL_CRYSPY_AUTO_UPDATE", "0")
    V.write_provenance(tmp_path, "1.4.0", {"status": "disabled"})
    r = rc.record_version(tmp_path)
    assert r["resumed"] and "1.4.0 -> 1.5.1" in r["warnings"][0]
    assert V.read_provenance(tmp_path)["version"] == "1.4.0"  # 上書きしない
