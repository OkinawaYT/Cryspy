#!/usr/bin/env python3
"""
CrySPY Batch Runner with Parallel Processing

Usage:
    python run_cryspy.py          # Uses input.json and env var or default
    python run_cryspy.py -n 8     # Uses 8 workers
    CRYSPY_NUM_WORKERS=16 python run_cryspy.py  # Uses env var
"""
import os
import subprocess
import json
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import shutil


def load_config(config_file="input.json"):
    """Load parameters from input.json"""
    if not os.path.exists(config_file):
        print(f"Error: {config_file} not found.")
        sys.exit(1)

    with open(config_file, 'r') as f:
        config = json.load(f)

    return config


def get_num_workers(config, cli_args):
    """
    Determine number of workers (priority order):
    1. Command line argument (-n / --num-workers)
    2. Environment variable (CRYSPY_NUM_WORKERS)
    3. input.json num_workers
    4. Default: 4
    """
    # Check command line arguments
    if '-n' in cli_args:
        idx = cli_args.index('-n')
        if idx + 1 < len(cli_args):
            return int(cli_args[idx + 1])
    if '--num-workers' in cli_args:
        idx = cli_args.index('--num-workers')
        if idx + 1 < len(cli_args):
            return int(cli_args[idx + 1])

    # Check environment variable
    env_workers = os.environ.get('CRYSPY_NUM_WORKERS')
    if env_workers:
        return int(env_workers)

    # Get from input.json
    if 'num_workers' in config:
        return config['num_workers']

    # Default
    return 4


def generate_nat_lists(total_atoms, num_elements):
    """Generate compositions of total_atoms into num_elements positive ints."""

    def rec(remaining, position):
        if position == num_elements - 1:
            if remaining >= 1:
                yield [remaining]
            return

        max_val = remaining - (num_elements - position - 1)
        for val in range(1, max_val + 1):
            for tail in rec(remaining - val, position + 1):
                yield [val] + tail

    if num_elements < 1:
        return []

    return rec(total_atoms, 0)


def update_cryspy_in(natot, nat_list, work_dir, elements, cryspy_in):
    """Update natot, nat, atype in cryspy.in"""
    cryspy_in_path = os.path.join(work_dir, cryspy_in)
    if not os.path.exists(cryspy_in_path):
        print(f"Error: {cryspy_in_path} not found.")
        return False

    with open(cryspy_in_path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        sline = line.strip()

        # Update natot
        if sline.startswith("natot") and "=" in sline:
            new_lines.append(f"natot = {natot}\n")

        # Update nat
        elif (sline.startswith("nat") and
              not sline.startswith("natot") and
              "=" in sline):
            nat_str = " ".join(map(str, nat_list))
            new_lines.append(f"nat = {nat_str}\n")

        # Update atype
        elif sline.startswith("atype") and "=" in sline:
            atype_str = " ".join(elements)
            new_lines.append(f"atype = {atype_str}\n")

        else:
            new_lines.append(line)

    with open(cryspy_in_path, 'w') as f:
        f.writelines(new_lines)

    return True


def run_cryspy(work_dir):
    """Run CrySPY in work_dir"""
    print(f"  Running CrySPY in {work_dir} ...")
    subprocess.run("cryspy", shell=True, check=True, cwd=work_dir)


def save_results(natot, nat_list, work_dir, elements):
    """Save results to result directory"""
    # Create directory name (e.g., Fe1Si1Al2)
    dir_suffix = "".join(
        [f"{elm}{num}" for elm, num in zip(elements, nat_list)]
    )
    save_dir = f"{dir_suffix}"

    if os.path.exists(save_dir):
        shutil.rmtree(save_dir)
    os.makedirs(save_dir)

    # Move CrySPY output files
    targets = ["data", "cryspy.stat", "err_cryspy",
               "log_cryspy", "lock_cryspy"]

    for target in targets:
        src = os.path.join(work_dir, target)
        if os.path.exists(src):
            shutil.move(src, os.path.join(save_dir, target))

    print(f"  Saved results to: {save_dir}")


def run_one_case(args, elements, cryspy_in):
    """Run one calculation case"""
    natot, nat_list = args
    print("=========================================")
    print(f"natot: {natot}")
    print(f"Target: nat={nat_list} for atype={elements}")

    # Create temporary directory
    with tempfile.TemporaryDirectory() as work_dir:
        # Copy necessary files
        shutil.copy(cryspy_in, work_dir)

        # Optional helpers: job_cryspy and ase_in.py
        for candidate in ["job_cryspy", os.path.join("calc_in", "job_cryspy")]:
            if os.path.exists(candidate):
                shutil.copy(candidate, work_dir)
                break

        ase_src = None
        for candidate in [
            "ase_in.py",
            "ase_in.py_1",
            os.path.join("calc_in", "ase_in.py"),
            os.path.join("calc_in", "ase_in.py_1"),
        ]:
            if os.path.exists(candidate):
                ase_src = candidate
                break
        if ase_src:
            shutil.copy(ase_src, os.path.join(work_dir, "ase_in.py"))

        # Update cryspy.in
        if not update_cryspy_in(natot, nat_list, work_dir,
                                elements, cryspy_in):
            msg = f"Error: cryspy.in not found for nat={nat_list}"
            return msg

        # Run
        try:
            run_cryspy(work_dir)
            save_results(natot, nat_list, work_dir, elements)
            return f"Success: nat={nat_list}"
        except subprocess.CalledProcessError:
            err_path = os.path.join(work_dir, "err_cryspy")
            err_msg = ""
            if os.path.exists(err_path):
                with open(err_path, "r") as fe:
                    err_msg = fe.read()
            return f"CrySPY failed for nat={nat_list}. {err_msg}"
        except Exception as e:
            return f"Unexpected Error for nat={nat_list}: {e}"


def main():
    """Main function"""
    # Load input.json
    config = load_config("input.json")
    elements = config.get("elements", ["Fe", "Si", "Al"])
    natot_min = config.get("natot_min", 3)
    natot_max = config.get("natot_max", 20)
    cryspy_in = config.get("cryspy_in", "cryspy.in")

    # Get number of workers
    num_workers = get_num_workers(config, sys.argv[1:])

    print(f"Starting batch process for elements: {elements}")
    print(f"Using {num_workers} workers")
    print(f"natot range: {natot_min} - {natot_max}")

    # Generate all cases dynamically for arbitrary element counts
    num_elements = len(elements)
    if num_elements < 1:
        print("Error: elements list is empty in input.json")
        sys.exit(1)

    cases = []
    for natot in range(natot_min, natot_max + 1):
        for nat_list in generate_nat_lists(natot, num_elements):
            cases.append((natot, nat_list))

    total_cases = len(cases)
    print(f"Total cases: {total_cases}")

    # Run in parallel
    start_time = time.time()
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {}
        for case in cases:
            future = executor.submit(run_one_case, case,
                                     elements, cryspy_in)
            futures[future] = case
        done = 0
        for future in as_completed(futures):
            result = future.result()
            done += 1
            elapsed = time.time() - start_time
            print(f"[{done}/{total_cases}] {result} (elapsed: {elapsed:.1f}s)")

    print("All tasks completed.")

    # Post-process: generate CIFs if enabled
    use_makecif = config.get("makecif", True)
    if use_makecif:
        # 探索順: 同ディレクトリ > PATH 上
        local_script = os.path.join(
            os.path.dirname(__file__), "cryspy_make_cifs.py"
        )
        path_script = shutil.which("cryspy_make_cifs.py")
        script_path = (
            local_script if os.path.exists(local_script) else path_script
        )

        if script_path:
            print("Running cryspy_make_cifs.py to generate CIFs...")
            try:
                subprocess.run(
                    [sys.executable, script_path], check=True
                )
            except subprocess.CalledProcessError as exc:
                print(f"makecif failed: {exc}")
        else:
            msg = (
                "makecif on but cryspy_make_cifs.py not found in dir or PATH; "
                "skipped."
            )
            print(msg)


if __name__ == "__main__":
    main()
