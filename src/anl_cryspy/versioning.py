"""csp-cryspy の版管理: 正の導入先(Cryspy/.venv)の解決、新規探索開始時の自動更新、run ごとの版記録。

方針(README「CrySPY の版管理」):
- 正の導入先は <repo>/.venv の 1 か所(pyproject の csp-cryspy>=下限 + uv.lock)。`ANL_CRYSPY_VENV` で差し替え可。
- 新しい探索の開始時だけ PyPI の最新版を照会し、新しければ `uv lock --upgrade-package csp-cryspy` +
  `uv sync --inexact` で更新してから始める。再開・継続時は更新しない。
- 照会失敗・オフライン・uv 不在・他の run が実行中なら更新せず、現在の版で続行(警告)。
- 無効化: `--no-update` または `ANL_CRYSPY_AUTO_UPDATE=0`。
- 更新で uv.lock が変わったら uv.lock だけを自動コミットする(push はしない)。無効化: `--no-auto-commit` または
  `ANL_CRYSPY_AUTO_COMMIT=0`。git 管理外・uv.lock に更新前からの未コミット変更・detached HEAD / rebase / merge 中・
  user.name/email 未設定・フック失敗のときはコミットせず警告して続行する。
- 各 run の作業ディレクトリに使った版を `.anl_cryspy_version.json` で記録し、再開時に現在の版と比較する。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess as sp
import sys
import urllib.request
from pathlib import Path

DIST = "csp-cryspy"
PYPI_JSON = f"https://pypi.org/pypi/{DIST}/json"
CRYSPY_ROOT = Path(__file__).resolve().parents[2]  # <repo>/Cryspy
PROVENANCE = ".anl_cryspy_version.json"
STATE_DIR = Path(os.environ.get("ANL_CRYSPY_STATE_DIR") or Path.home() / ".cache" / "anl_cryspy")


def canonical_venv() -> Path:
    return Path(os.environ.get("ANL_CRYSPY_VENV") or CRYSPY_ROOT / ".venv")


def canonical_python() -> Path:
    return canonical_venv() / "bin" / "python"


def in_canonical_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == canonical_venv().resolve()
    except OSError:
        return False


def auto_update_enabled(no_update_flag: bool = False) -> bool:
    if no_update_flag:
        return False
    return os.environ.get("ANL_CRYSPY_AUTO_UPDATE", "1").strip().lower() not in ("0", "false", "no", "off")


BATCH_JOB_VARS = ("PBS_JOBID", "SLURM_JOB_ID", "PJM_JOBID", "LSB_JOBID")


def in_batch_job() -> str | None:
    """バッチジョブ(計算ノード)内なら該当する環境変数名。クラスタの計算ノードは外部ネットワークに出られない前提で、
    そこでは自動更新しない(更新はログインノードで `anl-cryspy update`)。`ANL_CRYSPY_UPDATE_IN_JOB=1` で解除。"""
    if os.environ.get("ANL_CRYSPY_UPDATE_IN_JOB") == "1":
        return None
    return next((k for k in BATCH_JOB_VARS if os.environ.get(k)), None)


def auto_commit_enabled(no_commit_flag: bool = False) -> bool:
    if no_commit_flag:
        return False
    return os.environ.get("ANL_CRYSPY_AUTO_COMMIT", "1").strip().lower() not in ("0", "false", "no", "off")


def _vkey(v: str):
    """PEP 440 の簡易比較キー(数字部のみ。pre/dev 版は最新扱いしない)。"""
    m = re.match(r"^(\d+(?:\.\d+)*)$", v.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest: str | None, current: str | None) -> bool:
    a, b = (_vkey(latest) if latest else None), (_vkey(current) if current else None)
    if a is None:
        return False
    if b is None:
        return True
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def installed_version(python: Path | None = None) -> str | None:
    """正の venv(既定)に入っている csp-cryspy の版。未導入なら None。"""
    py = Path(python) if python else canonical_python()
    if not py.exists():
        return None
    code = f"import importlib.metadata as m;print(m.version({DIST!r}))"
    try:
        r = sp.run([str(py), "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, sp.TimeoutExpired):
        return None
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else None


def latest_pypi_version(timeout: float = 10.0) -> str:
    """PyPI JSON API で最新の正式版を返す(失敗時は例外)。"""
    req = urllib.request.Request(PYPI_JSON, headers={"Accept": "application/json", "User-Agent": "anl-cryspy"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    v = d["info"]["version"]
    if not isinstance(v, str) or _vkey(v) is None:
        raise ValueError(f"unexpected version from PyPI: {v!r}")
    return v


# ---- 実行中 run の検出 -------------------------------------------------------------------

def _runs_dir() -> Path:
    return STATE_DIR / "runs"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def register_run(workdir: Path) -> Path | None:
    """自分(この generate プロセス)を実行中 run として登録する。戻り値は解除用のパス。"""
    try:
        d = _runs_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{os.getpid()}.json"
        f.write_text(json.dumps({"pid": os.getpid(), "workdir": str(workdir),
                                 "started": _dt.datetime.now().isoformat(timespec="seconds")}))
        return f
    except OSError:
        return None


def unregister_run(f: Path | None):
    if f is not None:
        try:
            f.unlink()
        except OSError:
            pass


def _cryspy_processes() -> list[str]:
    """argv[0] が cryspy(CrySPY 本体)のプロセス。登録外の手動 run も拾う。"""
    try:
        out = sp.run(["ps", "-Ao", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, sp.TimeoutExpired):
        return []
    me = os.getpid()
    found = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) == me:
            continue
        argv = parts[1:]
        cmd = argv[0]
        # python .../bin/cryspy -n のように interpreter 経由の場合もある
        if os.path.basename(cmd) != "cryspy" and len(parts) > 2:
            first = parts[2].split()[0] if parts[2].split() else ""
            if os.path.basename(cmd).startswith("python") and os.path.basename(first) == "cryspy":
                cmd = first
        if os.path.basename(cmd) == "cryspy":
            found.append(line.strip())
    return found


def active_runs() -> list[str]:
    """実行中の別 run(登録された generate と cryspy 本体プロセス)。死んだ登録は掃除する。"""
    res = []
    d = _runs_dir()
    if d.is_dir():
        for f in d.glob("*.json"):
            try:
                pid = int(f.stem)
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            if _pid_alive(pid):
                try:
                    res.append(f"anl-cryspy generate pid={pid} workdir={json.loads(f.read_text()).get('workdir')}")
                except (OSError, ValueError):
                    res.append(f"anl-cryspy generate pid={pid}")
            else:
                try:
                    f.unlink()
                except OSError:
                    pass
    res += [f"cryspy process: {p}" for p in _cryspy_processes()]
    return res


# ---- 更新 ---------------------------------------------------------------------------------

def _uv() -> str | None:
    return os.environ.get("ANL_CRYSPY_UV") or shutil.which("uv") or next(
        (p for p in ("/opt/homebrew/bin/uv", str(Path.home() / ".local" / "bin" / "uv")) if Path(p).exists()), None)


def _run_uv(args: list[str]) -> sp.CompletedProcess:
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(canonical_venv())}
    env.pop("VIRTUAL_ENV", None)
    return sp.run([_uv(), *args], cwd=CRYSPY_ROOT, env=env, capture_output=True, text=True, timeout=900)


# ---- uv.lock の自動コミット ---------------------------------------------------------------

LOCK = "uv.lock"


def _git(args: list[str]) -> sp.CompletedProcess:
    return sp.run(["git", *args], cwd=CRYSPY_ROOT, capture_output=True, text=True, timeout=120)


def _lock_packages(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    try:
        import tomllib
        d = tomllib.loads(text)
    except Exception:
        return {}
    return {p["name"]: p.get("version", "?") for p in d.get("package", []) if "name" in p}


def commit_precheck() -> str | None:
    """uv.lock を自動コミットできない理由(できるなら None)。更新前に呼ぶ。"""
    try:
        if _git(["rev-parse", "--is-inside-work-tree"]).stdout.strip() != "true":
            return "not a git work tree"
        if _git(["ls-files", "--error-unmatch", LOCK]).returncode != 0:
            return f"{LOCK} is not tracked by git"
        if _git(["status", "--porcelain", "--", LOCK]).stdout.strip():
            return f"{LOCK} had uncommitted changes before the update"
        if _git(["symbolic-ref", "-q", "HEAD"]).returncode != 0:
            return "detached HEAD"
        gd = Path(_git(["rev-parse", "--absolute-git-dir"]).stdout.strip())
        for n in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            if (gd / n).exists():
                return f"git operation in progress ({n})"
        for k in ("user.name", "user.email"):
            if not _git(["config", k]).stdout.strip():
                return f"git {k} is not set"
    except (OSError, sp.TimeoutExpired) as e:
        return f"git unavailable ({type(e).__name__})"
    return None


def commit_lock(before: str | None, after: str | None, old: str | None, new: str | None) -> tuple[str | None, str | None]:
    """uv.lock だけをコミットする。戻り値 (commit hash | None, 警告 | None)。lock が変わっていなければ (None, None)。"""
    if before == after:
        return None, None
    a, b = _lock_packages(before), _lock_packages(after)
    others = sorted(f"{n} {a.get(n, '-')} -> {b.get(n, '-')}" for n in set(a) | set(b)
                    if a.get(n) != b.get(n) and n != DIST)
    msg = f"deps: {DIST} {old} -> {new} (auto-update)"
    if others:
        msg += "\n\nAlso changed in uv.lock:\n" + "\n".join(f"- {o}" for o in others)
    try:
        r = _git(["add", "--", LOCK])
        if r.returncode == 0:
            r = _git(["commit", "-m", msg, "--", LOCK])
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-2:]
            return None, f"{LOCK} auto-commit failed ({' / '.join(tail)}); commit it manually"
        h = _git(["rev-parse", "HEAD"]).stdout.strip()
        return h, None
    except (OSError, sp.TimeoutExpired) as e:
        return None, f"{LOCK} auto-commit failed ({type(e).__name__}); commit it manually"


def check(timeout: float = 10.0) -> dict:
    cur = installed_version()
    try:
        latest, err = latest_pypi_version(timeout), None
    except Exception as e:  # オフライン等
        latest, err = None, f"{type(e).__name__}: {e}"
    return {"installed": cur, "latest": latest, "update_available": is_newer(latest, cur), "error": err}


def update(timeout: float = 10.0, force_busy: bool = False, auto_commit: bool = True) -> dict:
    """PyPI 最新が新しければ正の venv を更新する。例外は投げず、結果 dict を返す。

    status: up_to_date | updated | skipped_offline | skipped_busy | skipped_no_uv | skipped_batch_job | failed
    """
    job = in_batch_job()
    if job:
        cur = installed_version()
        return {"dist": DIST, "before": cur, "latest": None, "after": cur, "status": "skipped_batch_job",
                "warnings": [f"{DIST}: inside a batch job ({job}); not updating on a compute node "
                             f"(run `anl-cryspy update` on the login node). Using installed {cur}"]}
    c = check(timeout)
    res = {"dist": DIST, "before": c["installed"], "latest": c["latest"], "after": c["installed"], "warnings": []}
    if c["error"]:
        res.update(status="skipped_offline")
        res["warnings"].append(f"{DIST}: PyPI query failed ({c['error']}); continuing with installed {c['installed']}")
        return res
    if not c["update_available"]:
        res.update(status="up_to_date")
        return res
    busy = [] if force_busy else active_runs()
    if busy:
        res.update(status="skipped_busy", active_runs=busy)
        res["warnings"].append(f"{DIST} {c['latest']} is available but another CrySPY run is active; "
                               f"not updating (installed {c['installed']})")
        return res
    if not _uv():
        res.update(status="skipped_no_uv")
        res["warnings"].append(f"{DIST} {c['latest']} is available but uv was not found; not updating")
        return res
    lock_path = CRYSPY_ROOT / LOCK
    skip = commit_precheck() if (auto_commit and auto_commit_enabled()) else None
    do_commit = auto_commit and auto_commit_enabled() and skip is None
    if skip:
        res["warnings"].append(f"{LOCK} will not be auto-committed: {skip}")
    lock_before = lock_path.read_text() if lock_path.exists() else None
    steps = [["lock", "--upgrade-package", DIST], ["sync", "--inexact"]]
    for a in steps:
        try:
            r = _run_uv(a)
        except (OSError, sp.TimeoutExpired) as e:
            r = sp.CompletedProcess(a, 1, "", f"{type(e).__name__}: {e}")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
            res.update(status="failed", after=installed_version())
            res["warnings"].append(f"{DIST} update failed at `uv {' '.join(a)}`: {' / '.join(tail)}; "
                                   f"continuing with installed {res['after']}")
            return res
    res.update(after=installed_version())
    res.update(status="updated" if res["after"] == c["latest"] else "failed")
    if res["status"] == "failed":
        res["warnings"].append(f"{DIST} update did not reach {c['latest']} (now {res['after']})")
    elif do_commit:
        lock_after = lock_path.read_text() if lock_path.exists() else None
        h, w = commit_lock(lock_before, lock_after, res["before"], res["after"])
        if h:
            res["commit"] = h
        if w:
            res["warnings"].append(w)
    return res


# ---- provenance ---------------------------------------------------------------------------

def write_provenance(workdir: Path, version: str | None, update_result: dict | None = None):
    d = {"dist": DIST, "version": version, "python": sys.executable,
         "recorded": _dt.datetime.now().isoformat(timespec="seconds")}
    if update_result:
        d["auto_update"] = {k: update_result.get(k) for k in ("status", "before", "latest", "after", "commit")}
    (Path(workdir) / PROVENANCE).write_text(json.dumps(d, ensure_ascii=False, indent=1))


def read_provenance(workdir: Path) -> dict | None:
    f = Path(workdir) / PROVENANCE
    try:
        d = json.loads(f.read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def resume_warning(workdir: Path, current: str | None) -> str | None:
    p = read_provenance(workdir)
    if p is None:
        return f"{DIST} version of the interrupted run is not recorded ({PROVENANCE} missing); now {current}"
    if p.get("version") != current:
        return (f"{DIST} version differs from the interrupted run ({p.get('version')} -> {current}); "
                "results may not be reproducible")
    return None


def record_run(workdir: Path, python: Path | None = None, no_update: bool = False,
               auto_commit: bool = True) -> dict:
    """run_cryspy.py 経路(job.sh から)用: generate と同じ記録を workdir に書く。

    - 既に記録がある(再開): 更新せず、記録は上書きせず、版が違えば警告(resume_warning)を返す。
    - 新規: バッチジョブ内なら update() が skipped_batch_job を返す。ジョブ外は auto_update_enabled のときだけ更新、
      無効なら auto_update.status=disabled。使った版(python の csp-cryspy)を write_provenance で記録する。
    戻り値: {"version", "resumed", "auto_update"(dict|None), "warnings"[list]}
    """
    wd = Path(workdir)
    cur = installed_version(python)
    if read_provenance(wd) is not None or (wd / PROVENANCE).exists():
        w = resume_warning(wd, cur)
        return {"version": cur, "resumed": True, "auto_update": None, "warnings": [w] if w else []}
    upd = None
    if in_batch_job() or auto_update_enabled(no_update):
        upd = update(auto_commit=auto_commit)
        cur = installed_version(python)
    else:
        upd = {"status": "disabled"}
    write_provenance(wd, cur, upd)
    return {"version": cur, "resumed": False, "auto_update": upd, "warnings": list(upd.get("warnings", []))}
