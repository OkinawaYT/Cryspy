import runpy
import os
import sys


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src_dir = os.path.join(project_root, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    runpy.run_path(os.path.join(src_dir, "main.py"), run_name="__main__")
