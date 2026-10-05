import runpy
import os
import sys


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "generate":
        return _generate(args[1:])
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
