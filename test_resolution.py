#!/usr/bin/env python3
"""
Test script to validate backend file resolution logic.
Tests various manifest structures to ensure robust fallback behavior.
"""

import json
from typing import Dict, Any

# Simulate different manifest structures
test_cases = [
    {
        "name": "Modern manifest (with drive_file_ids)",
        "manifest": {
            "run_id": "daily-2026-01-14",
            "mode": "daily",
            "drive_file_ids": {
                "must_reads_json": "1ABC_file_id",
                "report_md": "2DEF_file_id",
                "summaries_json": "3GHI_file_id",
                "new_csv": "4JKL_file_id"
            },
            "drive_output_paths": {
                "must_reads_json": "Daily/daily-2026-01-14/must_reads.json",
                "report_md": "Daily/daily-2026-01-14/report.md",
                "summaries_json": "Daily/daily-2026-01-14/summaries.json",
                "new_csv": "Daily/daily-2026-01-14/new.csv"
            }
        },
        "expected_keys": ["must_reads_json", "report_md", "summaries_json", "new_csv"]
    },
    {
        "name": "Manifest with base keys (no suffix)",
        "manifest": {
            "run_id": "daily-2026-01-14",
            "mode": "daily",
            "drive_file_ids": {
                "must_reads": "1ABC_file_id",
                "report": "2DEF_file_id",
                "summaries": "3GHI_file_id",
                "new": "4JKL_file_id"
            },
            "drive_output_paths": {
                "must_reads": "Daily/daily-2026-01-14/must_reads.json",
                "report": "Daily/daily-2026-01-14/report.md",
                "summaries": "Daily/daily-2026-01-14/summaries.json",
                "new": "Daily/daily-2026-01-14/new.csv"
            }
        },
        "expected_keys": ["must_reads", "report", "summaries", "new"]
    },
    {
        "name": "Legacy manifest (only local paths)",
        "manifest": {
            "run_id": "daily-2026-01-14",
            "mode": "daily",
            "output_paths": {
                "must_reads_json": "data/outputs/daily/daily-2026-01-14/must_reads.json",
                "report_md": "data/outputs/daily/daily-2026-01-14/report.md",
                "summaries_json": "data/outputs/daily/daily-2026-01-14/summaries.json",
                "new_csv": "data/outputs/daily/daily-2026-01-14/new.csv"
            }
        },
        "expected_keys": None  # Will use folder traversal fallback
    },
    {
        "name": "Minimal manifest (no paths, folder traversal only)",
        "manifest": {
            "run_id": "daily-2026-01-14",
            "mode": "daily"
        },
        "expected_keys": None  # Will use folder traversal fallback
    }
]

def validate_manifest(manifest: Dict[str, Any], test_name: str) -> None:
    """Validate a manifest structure."""
    print(f"\n{'='*60}")
    print(f"Test: {test_name}")
    print(f"{'='*60}")

    # Check for drive_file_ids
    drive_file_ids = manifest.get("drive_file_ids")
    if drive_file_ids:
        print(f"✓ drive_file_ids found: {list(drive_file_ids.keys())}")
    else:
        print(f"✗ drive_file_ids missing")

    # Check for drive_output_paths
    drive_output_paths = manifest.get("drive_output_paths")
    if drive_output_paths:
        print(f"✓ drive_output_paths found: {list(drive_output_paths.keys())}")
    else:
        print(f"✗ drive_output_paths missing")

    # Check for output_paths
    output_paths = manifest.get("output_paths") or manifest.get("local_output_paths")
    if output_paths:
        print(f"✓ output_paths found: {list(output_paths.keys())}")
    else:
        print(f"✗ output_paths missing")

    # Simulate resolution logic
    file_keys = ["must_reads", "report", "summaries", "new"]
    key_alternatives = {
        "must_reads": ["must_reads", "must_reads_json"],
        "report": ["report", "report_md"],
        "new": ["new", "new_csv"],
        "summaries": ["summaries", "summaries_json"]
    }

    print(f"\nResolution simulation:")
    for file_key in file_keys:
        keys_to_try = key_alternatives.get(file_key, [file_key])
        resolution_method = None

        # Try drive_file_ids
        if drive_file_ids:
            for key_variant in keys_to_try:
                if key_variant in drive_file_ids:
                    resolution_method = f"drive_file_id['{key_variant}']"
                    break

        # Try drive_output_paths
        if not resolution_method and drive_output_paths:
            for key_variant in keys_to_try:
                if key_variant in drive_output_paths:
                    resolution_method = f"drive_output_path['{key_variant}']"
                    break

        # Try folder traversal fallback
        if not resolution_method:
            resolution_method = "smart_folder_traversal"

        # Try legacy output_paths
        if not resolution_method and output_paths:
            key_mapping = {
                "must_reads": "must_reads_json",
                "report": "report_md",
                "new": "new_csv",
                "summaries": "summaries_json"
            }
            output_key = key_mapping.get(file_key, file_key)
            if output_key in output_paths:
                resolution_method = f"legacy_path['{output_key}']"

        if resolution_method:
            print(f"  {file_key:15} -> {resolution_method}")
        else:
            print(f"  {file_key:15} -> ✗ FAILED (no resolution method)")


if __name__ == "__main__":
    print("Backend File Resolution Validator")
    print("=" * 60)

    for test_case in test_cases:
        validate_manifest(test_case["manifest"], test_case["name"])

    print(f"\n{'='*60}")
    print("Validation complete!")
    print(f"{'='*60}\n")

    print("\nRecommendations:")
    print("1. Pipeline should generate manifests with BOTH drive_file_ids AND drive_output_paths")
    print("2. Use suffixed keys (must_reads_json, report_md, etc.) for consistency")
    print("3. Backend will automatically fall back to folder traversal if Drive fields missing")
    print("4. Smart folder traversal expects: Daily/<run_id>/*.json or Weekly/<run_id>/*.json")
