#!/usr/bin/env python3
"""Build the three input CSVs from demo_data.COMPANIES (in-session live sample)."""
from build_screen_inputs import build_record, write_all
from demo_data import COMPANIES


def main():
    records = []
    for c in COMPANIES:
        rec = build_record(c["sym"], c["km"], c["rat"], c["inc"], c["bal"],
                           c.get("cf", []), c["prof"])
        records.append(rec)
    n = write_all(records)
    print(f"built input CSVs for {n} companies: {', '.join(sorted(r['ticker'] for r in records))}")


if __name__ == "__main__":
    main()
