#!/usr/bin/env python3
"""
全ての結果フォルダからCIFファイルを生成するスクリプト
CrySPYのバッチ処理完了後に実行してください
"""

import os
import glob
from pymatgen.core import Structure
from pymatgen.core.lattice import Lattice


def parse_init_POSCARS(file_path):
    """init_POSCARSファイルを解析して構造リストを返す"""
    with open(file_path, 'r') as f:
        content = f.read().split('ID_')[1:]

    structures = []
    for block in content:
        lines = block.strip().split('\n')
        id_line = lines[0]
        scale = float(lines[1])
        lattice = [
            list(map(float, lines[2].split())),
            list(map(float, lines[3].split())),
            list(map(float, lines[4].split()))
        ]
        species = lines[5].split()
        num_atoms = list(map(int, lines[6].split()))
        coord_type = lines[7]
        coords = []
        types = []
        for line in lines[8:8+sum(num_atoms)]:
            parts = line.split()
            coords.append([float(parts[0]), float(parts[1]), float(parts[2])])
            types.append(parts[3])
        lattice = Lattice(lattice)
        structure = Structure(
            lattice, types, coords, coords_are_cartesian=False
        )
        structures.append((id_line, structure))
    return structures


def write_cif(structures, output_dir, result_name):
    """CIFファイルを出力"""
    os.makedirs(output_dir, exist_ok=True)
    cif_count = 0
    for id_, structure in structures:
        composition = structure.composition
        composition_str = "".join(
            [f"{el}{int(n)}" for el, n in composition.items()]
        )
        cif_path = os.path.join(
            output_dir, f"{result_name}_{composition_str}_{id_}.cif"
        )
        structure.to(filename=cif_path)
        cif_count += 1
    return cif_count


def process_result_folder(result_folder):
    """1つの結果フォルダを処理"""
    init_poscars = os.path.join(result_folder, 'data', 'init_POSCARS')

    if not os.path.exists(init_poscars):
        return None

    # 結果フォルダ内にinputディレクトリを作成
    output_dir = os.path.join(result_folder, 'input')
    result_name = os.path.basename(result_folder)

    try:
        structures = parse_init_POSCARS(init_poscars)
        cif_count = write_cif(structures, output_dir, result_name)
        return cif_count
    except Exception as e:
        print(f"Error processing {result_folder}: {e}")
        return None


def main():
    """メイン処理"""
    # フォルダ名は run_cryspy.py の save_results と合わせる
    # 現行実装: "Fe1Si1Al2" のような連結名
    # 互換のため旧フォーマット result_natot* も探索
    result_folders = glob.glob('*[0-9]')
    if not result_folders:
        result_folders = glob.glob('result_natot*')

    if not result_folders:
        print("No result folders found (result_natot*)")
        return

    print(f"Found {len(result_folders)} result folders")
    print("=" * 60)

    total_cifs = 0
    processed = 0
    skipped = 0

    for folder in sorted(result_folders):
        cif_count = process_result_folder(folder)

        if cif_count is not None:
            print(f"✓ {folder}: {cif_count} CIF files generated")
            total_cifs += cif_count
            processed += 1
        else:
            print(f"✗ {folder}: Skipped (no init_POSCARS)")
            skipped += 1

    print("=" * 60)
    print(f"Processed: {processed} folders")
    print(f"Skipped: {skipped} folders")
    print(f"Total CIF files generated: {total_cifs}")


if __name__ == "__main__":
    main()
