"""anl-cryspy generate: CrySPY(RS/EA)+MLIP緩和で探索し、緩和後の上位k構造をCIFで出力する。

契約: DFTCommon/docs/STRUCTSEARCH_CONTRACT.md (anl-pyxtal generate と同じ形)。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import subprocess as sp
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
CRYSPY_ROOT = HERE.parent.parent  # <repo>/Cryspy
DFT_ROOT = Path(os.environ.get("DFT_ROOT", CRYSPY_ROOT.parent))


MANIFEST = ".anl_cryspy_manifest.json"
# --force が workdir 内で消してよい CrySPY 生成物(固定名のみ)
WORKDIR_OWNED = ["cryspy.in", "cryspy.stat", "calc_in", "data", "work", "pids", "log_cryspy",
                 "err_cryspy", "lock_cryspy", "stop_cryspy", "skip_cryspy"]


def _write_manifest(out: Path, m: dict):
    (out / MANIFEST).write_text(json.dumps(m, ensure_ascii=False, indent=1))


def _read_manifest(out: Path):
    f = out / MANIFEST
    if not f.is_file():
        return None
    try:
        m = json.loads(f.read_text())
        return m if isinstance(m, dict) and m.get("tool") == "anl-cryspy" else None
    except Exception:
        return None


def _safe_rm_listed(out: Path, m: dict):
    """マニフェスト記載のファイルだけを out 直下から削除(out 外・サブディレクトリは対象外)。"""
    root = out.resolve()
    for name in m.get("files", []):
        if "/" in name or "\\" in name or name.startswith("."):
            continue
        f = out / name
        if f.is_symlink() or (f.is_file() and f.resolve().parent == root):
            f.unlink()
    (out / MANIFEST).unlink(missing_ok=True)


def _force_clean(out: Path):
    m = _read_manifest(out)
    if m is None:
        raise InputError(f"--force: no valid {MANIFEST} in {out}; refusing to delete anything", "out_not_empty")
    wd = m.get("workdir")
    if wd and m.get("workdir_user_given"):
        w = Path(wd)
        if w.is_dir() and w.resolve() not in (Path("/"), Path.home().resolve(), out.resolve()) \
                and (w / "cryspy.in").is_file():
            for nme in WORKDIR_OWNED:
                t = w / nme
                if t.is_symlink() or t.is_file():
                    t.unlink()
                elif t.is_dir():
                    shutil.rmtree(t, ignore_errors=True)
    _safe_rm_listed(out, m)


class InputError(Exception):
    code = "invalid_input"

    def __init__(self, msg, code=None):
        super().__init__(msg)
        if code:
            self.code = code


class RuntimeFail(Exception):
    code = "runtime_error"

    def __init__(self, msg, code=None):
        super().__init__(msg)
        if code:
            self.code = code


def parse_composition(s: str) -> dict[str, int]:
    from ase.data import atomic_numbers

    s = s.strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except json.JSONDecodeError as e:
            raise InputError(f"bad composition JSON: {e}")
        comp = {}
        for k, v in d.items():
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise InputError(f"count for {k} must be a positive integer")
            comp[k] = v
    else:
        comp, pos = {}, 0
        for m in re.finditer(r"([A-Z][a-z]?)(\d*)", s):
            if m.start() != pos:
                break
            pos = m.end()
            comp[m.group(1)] = comp.get(m.group(1), 0) + int(m.group(2) or 1)
        if pos != len(s) or not comp:
            raise InputError(f"cannot parse composition: {s!r}")
    for el in comp:
        if el not in atomic_numbers or el == "X":
            raise InputError(f"unknown element: {el}")
    return comp


def resolve_calculator(name: str) -> dict:
    """calculators.yaml の venv_python を解決する(yaml 可能な DFTCommon/calculators venv に委譲)。"""
    if name == "emt":
        return {"venv_python": sys.executable, "calc_path": ""}
    calc_pkg = DFT_ROOT / "DFTCommon" / "calculators"
    py = calc_pkg / ".venv" / "bin" / "python"
    if not py.exists():
        raise InputError(f"DFTCommon/calculators venv not found: {py}", "backend_unavailable")
    code = ("import json,sys;from dft_calculators.resolve import resolve_entry;"
            "e=resolve_entry(sys.argv[1]);print(json.dumps({'venv_python':e.get('venv_python')}))")
    r = sp.run([str(py), "-c", code, name], capture_output=True, text=True,
               env={**os.environ, "PYTHONPATH": str(calc_pkg)})
    if r.returncode != 0:
        raise InputError(f"unknown calculator {name!r}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}")
    vp = json.loads(r.stdout.strip().splitlines()[-1]).get("venv_python") or sys.executable
    if not Path(vp).exists():
        raise InputError(f"venv_python for {name} not found: {vp}", "backend_unavailable")
    return {"venv_python": vp, "calc_path": str(calc_pkg)}


def mem_used_frac() -> float:
    """macOS: vm_stat から (active+wired+compressed)/total。取得失敗時は 0。"""
    try:
        out = sp.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        ps = int(re.search(r"page size of (\d+)", out).group(1))
        g = lambda k: int(re.search(rf"{k}:\s+(\d+)", out).group(1))
        used = (g("Pages active") + g("Pages wired down") + g("Pages occupied by compressor")) * ps
        total = int(sp.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
        return used / total
    except Exception:
        return 0.0


def build_cryspy_in(comp, algo, tot_struc, n_pop, maxgen, workers, max_atoms):
    from ase.data import atomic_numbers, covalent_radii

    species = list(comp)
    counts = [comp[e] for e in species]
    g = math.gcd(*counts)
    if sum(counts) > max_atoms:
        counts = [c // g for c in counts]
    if sum(counts) > max_atoms:
        raise InputError(f"formula has {sum(counts)} atoms > max_atoms={max_atoms}")
    rad = [covalent_radii[atomic_numbers[e]] for e in species]
    n = len(species)
    md = "\n".join(
        f"mindist_{i + 1} = " + " ".join(f"{0.75 * (rad[i] + rad[j]):.2f}" for j in range(n))
        for i in range(n))
    lines = ["[basic]", f"algo = {algo}", "calc_code = ASE"]
    if algo == "RS":
        lines.append(f"tot_struc = {tot_struc}")
    lines += ["nstage = 1", f"njob = {workers}", "jobcmd = sh", "jobfile = job_cryspy", ""]
    if algo == "EA":
        n_crsov = max(1, round(n_pop * 0.5)) if n_pop >= 4 else 0
        n_perm = 1 if (n >= 2 and n_pop >= 5) else 0
        n_strain = 1 if n_pop >= 5 else 0
        n_rand = n_pop - n_crsov - n_perm - n_strain
        n_elite = max(1, n_pop // 5)
        lines += ["[EA]", f"n_pop = {n_pop}", f"n_crsov = {n_crsov}", f"n_perm = {n_perm}",
                  f"n_strain = {n_strain}", f"n_rand = {n_rand}", f"n_elite = {n_elite}",
                  f"n_fittest = {n_pop}", "slct_func = TNM", "t_size = 2",
                  f"maxgen_ea = {maxgen}", ""]
    lines += ["[structure]", f"natot = {sum(counts)}", "atype = " + " ".join(species),
              "nat = " + " ".join(map(str, counts)), md, "",
              "[ASE]", "ase_python = ase_in.py", "", "[option]", ""]
    return "\n".join(lines), species, counts


def run_cryspy(workdir: Path, env: dict, deadline: float, mem_limit: float, algo: str, log):
    cryspy_bin = Path(sys.executable).parent / "cryspy"
    if not cryspy_bin.exists():
        raise InputError(f"cryspy not found next to {sys.executable}", "backend_unavailable")
    done_marker = "Done all structures!" if algo == "RS" else "Reached maxgen_ea"
    peak = 0.0
    n_inv = 0
    lg0 = workdir / "log_cryspy"
    if (workdir / "cryspy.stat").exists() and lg0.exists() and done_marker in lg0.read_text()[-400:]:
        return 0.0, 0  # 完了済み: 再探索せず
    while True:
        if time.time() > deadline:
            raise RuntimeFail("overall wall-time limit exceeded", "timeout")
        m = mem_used_frac()
        peak = max(peak, m)
        if m > mem_limit:
            raise RuntimeFail(f"system memory {m:.0%} > limit {mem_limit:.0%}", "memory_limit")
        r = sp.run([str(cryspy_bin), "-n"], cwd=workdir, env=env, capture_output=True, text=True)
        n_inv += 1
        if r.returncode != 0:
            raise RuntimeFail(f"cryspy exited {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
        lg = workdir / "log_cryspy"
        txt = lg.read_text() if lg.exists() else ""
        if done_marker in txt[-400:]:
            break
        time.sleep(1.5)
    return peak, n_inv


def kill_jobs(piddir: Path):
    if not piddir.exists():
        return
    for p in piddir.iterdir():
        try:
            pid = int(p.name)
            sp.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def collect(workdir: Path, species, comp_counts):
    """cryspy の pkl から (cid, energy_per_atom, Structure, gen, check_opt) を取り出す。"""
    cwd = os.getcwd()
    os.chdir(workdir)
    try:
        from cryspy.IO import pkl_data
        rslt = pkl_data.load_rslt()
        opt = pkl_data.load_opt_struc()
    finally:
        os.chdir(cwd)
    rows = []
    for cid, row in rslt.iterrows():
        e = row.get("E_eV_atom")
        s = opt.get(cid)
        if s is None or e is None or e != e:
            continue
        gen = int(row["Gen"]) if "Gen" in rslt.columns and row["Gen"] == row["Gen"] else None
        chk = row.get("Opt") if "Opt" in rslt.columns else None
        rows.append((int(cid), float(e), s, gen, None if chk is None else str(chk)))
    return rows, len(rslt)


def generate(comp, n, seed, out, dedup_tol, max_atoms, calculator, algo, timeout, workers,
             tot_struc=None, n_pop=None, maxgen=None, fmax=0.05, steps=300, workdir=None,
             keep_workdir=False, resume=False, manifest_cmd=None):
    from ase.io import write
    from pymatgen.analysis.structure_matcher import StructureMatcher
    from pymatgen.io.ase import AseAtomsAdaptor
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    workers = max(1, min(workers, 2))  # 同時プロセス <= 2
    cal = resolve_calculator(calculator)
    tot_struc = tot_struc or max(4 * n, 20)
    n_pop = n_pop or max(6, n)
    maxgen = maxgen or 5
    cin, species, counts = build_cryspy_in(comp, algo, tot_struc, n_pop, maxgen, workers, max_atoms)

    out.mkdir(parents=True, exist_ok=True)
    old = _read_manifest(out) if resume else None
    if resume:
        if old is None:
            raise InputError(f"--resume: no valid {MANIFEST} in {out}", "resume_unavailable")
        wd0 = old.get("workdir")
        if not (wd0 and old.get("workdir_user_given") and (Path(wd0) / "cryspy.stat").is_file()):
            raise InputError("--resume: previous --workdir with cryspy.stat not found "
                             "(re-run the original command with --workdir to make it resumable)", "resume_unavailable")
        for k, v in (("composition", comp), ("algo", algo), ("seed", seed), ("calculator", calculator)):
            if old.get(k) != v:
                raise InputError(f"--resume: {k} differs from the interrupted run ({old.get(k)!r} vs {v!r})", "resume_unavailable")
        workdir = wd0
        _safe_rm_listed(out, old)  # 旧出力CIFは再出力で置き換える
    own_tmp = workdir is None
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="anl_cryspy_", dir=os.environ.get("ANL_CRYSPY_WORKROOT")))
    wd.mkdir(parents=True, exist_ok=True)
    if not resume:
        (wd / "calc_in").mkdir(exist_ok=True)
        (wd / "cryspy.in").write_text(cin)
        shutil.copy(TEMPLATES / "ase_in.py_1", wd / "calc_in" / "ase_in.py_1")
        shutil.copy(TEMPLATES / "job_cryspy", wd / "calc_in" / "job_cryspy")
    manifest = {"tool": "anl-cryspy", "command": manifest_cmd, "composition": comp, "algo": algo,
                "seed": seed, "calculator": calculator, "workdir": str(wd),
                "workdir_user_given": not own_tmp, "files": [], "status": "running"}
    _write_manifest(out, manifest)
    piddir = wd / "pids"

    env = {
        **os.environ,
        "PYTHONPATH": str(TEMPLATES),
        "ANL_CRYSPY_SEED": str(seed), "PYTHONHASHSEED": str(seed),
        "DFT_VENV_PYTHON": cal["venv_python"],
        "ANL_DFT_CALC_PATH": cal["calc_path"],
        "ANL_CALCULATOR": calculator,
        "ANL_FMAX": str(fmax), "ANL_STEPS": str(steps), "ANL_TIMEOUT": str(timeout),
        "ANL_CRYSPY_PIDDIR": str(piddir),
        "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
    }
    nstruct = tot_struc if algo == "RS" else n_pop * maxgen
    per_round = math.ceil(nstruct / workers)
    deadline = time.time() + timeout * per_round * 1.5 + 120
    mem_limit = float(os.environ.get("ANL_CRYSPY_MEM_LIMIT", "0.6"))
    t0 = time.time()
    try:
        peak, n_inv = run_cryspy(wd, env, deadline, mem_limit, algo, None)
    except BaseException:
        kill_jobs(piddir)
        raise
    rows, n_total = collect(wd, species, counts)
    rows.sort(key=lambda r: r[1])

    matcher = StructureMatcher(ltol=dedup_tol, stol=dedup_tol, angle_tol=5) if dedup_tol is not None else None
    try:
        import cryspy as _c
        from importlib.metadata import version
        ver = version("csp-cryspy")
    except Exception:
        ver = "unknown"
    structures, reps, removed = [], [], 0
    for cid, e, pm, gen, chk in rows:
        if len(structures) >= n:
            break
        if matcher is not None and any(matcher.fit(pm, r) for r in reps):
            removed += 1
            continue
        try:
            sg = int(SpacegroupAnalyzer(pm, symprec=0.1).get_space_group_number())
        except Exception:
            sg = None
        atoms = AseAtomsAdaptor.get_atoms(pm)
        sid = f"s{len(structures) + 1:04d}"
        path = out / f"{sid}.cif"
        write(str(path), atoms, format="cif")
        reps.append(pm)
        structures.append({
            "id": sid, "path": str(path), "formula": atoms.get_chemical_formula(mode="metal"),
            "natoms": len(atoms), "spacegroup": sg, "duplicate_of": None,
            "energy_per_atom": e,
            "meta": {"backend_version": ver, "calculator": calculator, "algo": algo,
                     "cryspy_id": cid, "generation": gen, "opt_status": chk,
                     "energy_unit": "eV/atom", "workdir": str(wd)},
        })
    manifest.update(files=[Path(x["path"]).name for x in structures], status="done")
    _write_manifest(out, manifest)
    info = {"n_total_relaxed": len(rows), "n_cryspy_total": n_total, "removed": removed,
            "peak_mem_frac": round(peak, 3), "seconds": round(time.time() - t0, 1),
            "cryspy_invocations": n_inv, "tot_struc": tot_struc if algo == "RS" else None,
            "n_pop": n_pop if algo == "EA" else None, "maxgen": maxgen if algo == "EA" else None}
    if own_tmp and not keep_workdir and os.environ.get("ANL_CRYSPY_KEEP") != "1":
        shutil.rmtree(wd, ignore_errors=True)
        for s in structures:
            s["meta"]["workdir"] = None
    return structures, info


def add_args(g):
    g.add_argument("--composition", required=True, help="AuCu or JSON {\"Au\":1,\"Cu\":1}")
    g.add_argument("--n", type=int, default=10, help="top-k relaxed structures to output")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--out", required=True)
    g.add_argument("--dedup-tol", type=float, default=0.05)
    g.add_argument("--max-atoms", type=int, default=40)
    g.add_argument("--calculator", default="chgnet-0.3", help="calculators.yaml key, or 'emt' (test double)")
    g.add_argument("--algo", choices=["rs", "ea"], default="ea")
    g.add_argument("--timeout", type=int, default=600, help="per-structure relaxation timeout (s)")
    g.add_argument("--workers", type=int, default=1, help="concurrent relaxations (capped at 2)")
    g.add_argument("--tot-struc", type=int, default=None, help="RS: number of structures (default max(4n,20))")
    g.add_argument("--n-pop", type=int, default=None, help="EA: population (default max(6,n))")
    g.add_argument("--maxgen", type=int, default=None, help="EA: total generations (default 5)")
    g.add_argument("--workdir", default=None, help="keep CrySPY working dir here (default: temp, deleted)")
    g.add_argument("--resume", action="store_true",
                   help="resume an interrupted run from out's manifest + its --workdir (cryspy.stat); done runs are re-exported")
    g.add_argument("--force", action="store_true",
                   help="delete only files this command created (per manifest) in out, then rerun")
    g.add_argument("--json", action="store_true")


def run(args) -> int:
    def fail(msg, code, rc):
        print(json.dumps({"status": "error", "error": msg, "code": code}, ensure_ascii=False))
        return rc

    try:
        if args.n < 1 or args.max_atoms < 1 or args.dedup_tol < 0 or args.timeout < 1 or args.workers < 1:
            raise InputError("n/max-atoms/timeout/workers must be >=1; dedup-tol >=0")
        comp = parse_composition(args.composition)
        out = Path(args.out)
        if args.resume and args.force:
            raise InputError("--resume and --force are mutually exclusive")
        if args.force and out.is_dir():
            _force_clean(out)
        if not args.resume and out.exists() and (not args.force or not out.is_dir()) and (not out.is_dir() or any(out.iterdir())):
            raise InputError(f"output dir exists and is not empty: {out}", "out_not_empty")
        try:
            import cryspy  # noqa: F401
        except ImportError:
            return fail("cryspy (csp-cryspy) not installed", "backend_unavailable", 1)
        structures, info = generate(
            comp, args.n, args.seed, out, args.dedup_tol, args.max_atoms, args.calculator,
            args.algo.upper(), args.timeout, args.workers, args.tot_struc, args.n_pop,
            args.maxgen, workdir=args.workdir, resume=args.resume,
            manifest_cmd=" ".join(sys.argv))
    except InputError as e:
        return fail(str(e), e.code, 1)
    except RuntimeFail as e:
        return fail(str(e), e.code, 2)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", "runtime_error", 2)
    if not structures:
        return fail("no relaxed structure obtained (all relaxations failed or timed out)",
                    "generation_failed", 2)
    formula = "".join(f"{k}{v if v != 1 else ''}" for k, v in comp.items())
    print(json.dumps({
        "status": "ok", "composition": formula, "backend": "cryspy", "seed": args.seed,
        "structures": structures, "n_requested": args.n, "n_generated": info["n_total_relaxed"],
        "n_unique": len(structures),
        "dedup": {"method": "StructureMatcher", "tol": args.dedup_tol, "removed": info["removed"]},
        "params": {"algo": args.algo, "calculator": args.calculator, "max_atoms": args.max_atoms,
                   "timeout": args.timeout, "workers": min(args.workers, 2), **info,
                   "reproducible": "best-effort (CrySPY RNG seeded via sitecustomize injection)"},
    }, ensure_ascii=False))
    return 0
