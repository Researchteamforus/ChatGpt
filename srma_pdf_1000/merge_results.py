#!/usr/bin/env python3
from pathlib import Path
import argparse, csv, json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, help='Directory containing downloaded/extracted lane artifacts')
    parser.add_argument('--output', default='merged_1000_lane_results.csv')
    args = parser.parse_args()
    root = Path(args.input)
    files = sorted(root.rglob('PDF-AGENT-*_results.csv'))
    rows = []
    fields = []
    for path in files:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not fields:
                fields = reader.fieldnames or []
            rows.extend(reader)
    with open(args.output, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        'artifact_result_files': len(files),
        'merged_rows': len(rows),
        'output': args.output,
    }, indent=2))


if __name__ == '__main__':
    main()
