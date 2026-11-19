import runpy
import os
import sys


def _reexec_into_canonical_venv(args):
    """CrySPY 本体は正の導入先(<repo>/.venv)だけを使う。別の環境(uv tool の shim 等)から
    起動された場合は、その venv の python で自分を起動し直す。ANL_CRYSPY_NO_REEXEC=1 で無効。"""
    from . import versioning

    if os.environ.get("ANL_CRYSPY_NO_REEXEC") == "1" or versioning.in_canonical_venv():
        return
    py = versioning.canonical_python()
    if not py.exists():
        return  # 正の venv が無い: 現環境で続行(generate は cryspy 不在なら backend_unavailable)
    env = {**os.environ, "ANL_CRYSPY_NO_REEXEC": "1"}
    os.execve(str(py), [str(py), "-m", "anl_cryspy.cli", *args], env)


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] in ("generate", "update") and argv is None:
        _reexec_into_canonical_venv(args)
    if args and args[0] == "generate":
        return _generate(args[1:])
    if args and args[0] == "update":
        return _update(args[1:])
    if args and args[0] == "jobscript":
        return _jobscript(args[1:])
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src_dir = os.path.join(project_root, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    runpy.run_path(os.path.join(src_dir, "main.py"), run_name="__main__")


def _generate(argv):
    import argparse
    from .generate import add_args, run

    ap = argparse.ArgumentParser(prog="anl-cryspy generate")
    add_args(ap)
    try:
        ns = ap.parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0
    return run(ns)


def _update(argv):
    """csp-cryspy を PyPI 最新へ更新する(--check は照会のみ)。exit: 0=最新/更新済, 1=更新あり(--check), 2=失敗/見送り。"""
    import argparse
    import json
    from . import versioning

    ap = argparse.ArgumentParser(prog="anl-cryspy update",
                                 description="update csp-cryspy in the canonical venv to the latest PyPI release")
    ap.add_argument("--check", action="store_true", help="only report installed/latest, do not install")
    ap.add_argument("--no-auto-commit", action="store_true",
                    help="do not auto-commit uv.lock after updating (also: ANL_CRYSPY_AUTO_COMMIT=0)")
    ap.add_argument("--json", action="store_true")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as e:
        return 1 if e.code else 0
    if ns.check:
        r = versioning.check()
        r["status"] = "error" if r["error"] else ("update_available" if r["update_available"] else "up_to_date")
        rc = 2 if r["error"] else (1 if r["update_available"] else 0)
    else:
        r = versioning.update(auto_commit=not ns.no_auto_commit)
        rc = 0 if r["status"] in ("up_to_date", "updated") else 2
    r["venv"] = str(versioning.canonical_venv())
    if ns.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"csp-cryspy: {r['status']} (installed {r.get('after', r.get('installed'))}, "
              f"latest {r.get('latest')}, venv {r['venv']})")
        for w in r.get("warnings", []) + ([r["error"]] if r.get("error") else []):
            print(f"warning: {w}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())


def _jobscript(argv):
    """スケジューラ別の job.sh を出力する(systemB=slurm / systemC=pbs)。"""
    import argparse
    from . import jobscript as J

    ap = argparse.ArgumentParser(prog="anl-cryspy jobscript")
    ap.add_argument("--cluster", help="systemB (slurm) | systemC (pbs)")
    ap.add_argument("--scheduler", choices=["pbs", "slurm"], help="クラスタ名の代わりに直接指定")
    ap.add_argument("--queue", help="既定: i キュー(slurm=i8cpu, pbs=i2cpu)")
    ap.add_argument("--walltime", default="00:30:00")
    ap.add_argument("-o", "--output", help="書き出し先(既定: 標準出力)")
    try:
        ns = ap.parse_args(argv)
        sched = J.scheduler_for(ns.cluster, ns.scheduler)
    except SystemExit as e:
        return 1 if e.code else 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    text = J.render(sched, queue=ns.queue, walltime=ns.walltime)
    if ns.output:
        import os
        import stat
        with open(ns.output, "w") as f:
            f.write(text)
        os.chmod(ns.output, os.stat(ns.output).st_mode | stat.S_IXUSR)
    else:
        sys.stdout.write(text)
    return 0
