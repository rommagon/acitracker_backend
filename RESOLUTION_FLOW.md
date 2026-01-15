# File Resolution Flow Diagram

## Request Flow

```
User/GPT Request
      ↓
GET /api/must-reads?mode=daily
      ↓
get_latest_run(mode="daily")
      ↓
[Read Manifests/Daily/latest.json]
      ↓
run_id = "daily-2026-01-14"
      ↓
[Load manifest: Manifests/Daily/daily-2026-01-14.json]
      ↓
resolve_and_get_output_file(file_key="must_reads")
      ↓
┌─────────────────────────────────────────────────────┐
│         FILE RESOLUTION STRATEGY (4-TIER)           │
└─────────────────────────────────────────────────────┘
```

## Resolution Strategy Details

```
┌─────────────────────────────────────────────────────────────────┐
│ Priority 1: drive_file_ids (Direct File ID)                    │
│ Speed: ⚡⚡⚡ ~200ms                                              │
└─────────────────────────────────────────────────────────────────┘
         ↓
    Try keys: ["must_reads", "must_reads_json"]
         ↓
    ┌─────────────────────────────────┐
    │ drive_file_ids["must_reads"]?   │
    └─────────────────────────────────┘
         ↓ YES
    [Download file by ID: 1ABC_file_id]
         ↓
    ✅ SUCCESS → Return content

         ↓ NO
    ┌─────────────────────────────────────┐
    │ drive_file_ids["must_reads_json"]? │
    └─────────────────────────────────────┘
         ↓ YES
    [Download file by ID: 1ABC_file_id]
         ↓
    ✅ SUCCESS → Return content

         ↓ NO
┌─────────────────────────────────────────────────────────────────┐
│ Priority 2: drive_output_paths (Folder Navigation)             │
│ Speed: ⚡⚡ ~300ms                                               │
└─────────────────────────────────────────────────────────────────┘
         ↓
    Try keys: ["must_reads", "must_reads_json"]
         ↓
    ┌─────────────────────────────────────────┐
    │ drive_output_paths["must_reads"]?      │
    └─────────────────────────────────────────┘
         ↓ YES
    [Navigate path: Daily/daily-2026-01-14/must_reads.json]
         ↓
    ✅ SUCCESS → Return content

         ↓ NO
    ┌─────────────────────────────────────────────┐
    │ drive_output_paths["must_reads_json"]?     │
    └─────────────────────────────────────────────┘
         ↓ YES
    [Navigate path: Daily/daily-2026-01-14/must_reads.json]
         ↓
    ✅ SUCCESS → Return content

         ↓ NO
┌─────────────────────────────────────────────────────────────────┐
│ Priority 3: Smart Folder Traversal (NEW - Robust Fallback)     │
│ Speed: ⚡ ~500ms                                                │
│ Works: Even with empty manifests!                              │
└─────────────────────────────────────────────────────────────────┘
         ↓
    Map file_key to filename:
    "must_reads" → "must_reads.json"
    "report" → "report.md"
    "summaries" → "summaries.json"
    "new" → "new.csv"
         ↓
    ┌────────────────────────────────────┐
    │ Step 1: Find mode folder           │
    │ find_folder_in_parent("Daily",     │
    │   DRIVE_FOLDER_ID)                 │
    └────────────────────────────────────┘
         ↓ mode_folder_id
    ┌────────────────────────────────────┐
    │ Step 2: Find run folder            │
    │ find_folder_in_parent(              │
    │   "daily-2026-01-14",              │
    │   mode_folder_id)                  │
    └────────────────────────────────────┘
         ↓ run_folder_id
    ┌────────────────────────────────────┐
    │ Step 3: Find file                  │
    │ find_file_in_folder_by_id(         │
    │   "must_reads.json",               │
    │   run_folder_id)                   │
    └────────────────────────────────────┘
         ↓ file_id
    [Download file by ID]
         ↓
    ✅ SUCCESS → Return content

         ↓ NO
┌─────────────────────────────────────────────────────────────────┐
│ Priority 4: Legacy output_paths (Backward Compatibility)       │
│ Speed: ⚡ ~500ms                                                │
└─────────────────────────────────────────────────────────────────┘
         ↓
    Map file_key to legacy key:
    "must_reads" → "must_reads_json"
         ↓
    ┌──────────────────────────────────────────────┐
    │ output_paths["must_reads_json"]?            │
    └──────────────────────────────────────────────┘
         ↓ YES
    local_path = "data/outputs/daily/daily-2026-01-14/must_reads.json"
    drive_path = "outputs/daily/daily-2026-01-14/must_reads.json"
         ↓
    [Navigate Drive path]
         ↓
    ✅ SUCCESS → Return content

         ↓ NO
┌─────────────────────────────────────────────────────────────────┐
│ ALL METHODS FAILED                                             │
└─────────────────────────────────────────────────────────────────┘
         ↓
    ❌ HTTPException 503
    Error: "Output file 'must_reads' not found via any resolution method
           for mode=daily, run_id=daily-2026-01-14.
           Tried: drive_file_ids=['report_md'],
                  drive_output_paths=['report_md'],
                  folder_traversal, legacy_paths"
```

## Example Scenarios

### Scenario 1: Modern Manifest (Optimal)

**Manifest**:
```json
{
  "run_id": "daily-2026-01-14",
  "drive_file_ids": {
    "must_reads_json": "1ABC_file_id",
    "report_md": "2DEF_file_id"
  }
}
```

**Resolution Path**:
```
Request: GET /api/must-reads?mode=daily
  → Priority 1: Try drive_file_ids["must_reads"] → NOT FOUND
  → Priority 1: Try drive_file_ids["must_reads_json"] → FOUND ✅
  → Download file by ID: 1ABC_file_id
  → Return content (200ms)
```

**Logs**:
```
[daily/daily-2026-01-14] Resolving 'must_reads' - will try keys: ['must_reads', 'must_reads_json']
[daily/daily-2026-01-14] Trying drive_file_id for key 'must_reads_json': 1ABC_file_id
[daily/daily-2026-01-14] ✓ Resolved 'must_reads' via drive_file_id['must_reads_json']: 1ABC_file_id
```

---

### Scenario 2: Manifest with Base Keys

**Manifest**:
```json
{
  "run_id": "daily-2026-01-14",
  "drive_file_ids": {
    "must_reads": "1ABC_file_id",
    "report": "2DEF_file_id"
  }
}
```

**Resolution Path**:
```
Request: GET /api/must-reads?mode=daily
  → Priority 1: Try drive_file_ids["must_reads"] → FOUND ✅
  → Download file by ID: 1ABC_file_id
  → Return content (200ms)
```

**Logs**:
```
[daily/daily-2026-01-14] Resolving 'must_reads' - will try keys: ['must_reads', 'must_reads_json']
[daily/daily-2026-01-14] Trying drive_file_id for key 'must_reads': 1ABC_file_id
[daily/daily-2026-01-14] ✓ Resolved 'must_reads' via drive_file_id['must_reads']: 1ABC_file_id
```

---

### Scenario 3: Empty Manifest (Fallback)

**Manifest**:
```json
{
  "run_id": "daily-2026-01-14",
  "mode": "daily"
}
```

**Resolution Path**:
```
Request: GET /api/must-reads?mode=daily
  → Priority 1: drive_file_ids missing → SKIP
  → Priority 2: drive_output_paths missing → SKIP
  → Priority 3: Smart folder traversal → ATTEMPT
    → Find folder: Daily
    → Find folder: daily-2026-01-14
    → Find file: must_reads.json → FOUND ✅
  → Download file by ID
  → Return content (500ms)
```

**Logs**:
```
[daily/daily-2026-01-14] Resolving 'must_reads' - will try keys: ['must_reads', 'must_reads_json']
[daily/daily-2026-01-14] Attempting smart folder traversal fallback
[daily/daily-2026-01-14] Looking for file in: Daily/daily-2026-01-14/must_reads.json
[daily/daily-2026-01-14] ✓ Resolved 'must_reads' via folder traversal: must_reads.json
```

---

### Scenario 4: File Not Found Anywhere

**Manifest**:
```json
{
  "run_id": "daily-2026-01-14",
  "drive_file_ids": {
    "report_md": "2DEF_file_id"
  }
}
```

**Resolution Path**:
```
Request: GET /api/must-reads?mode=daily
  → Priority 1: Try drive_file_ids["must_reads"] → NOT FOUND
  → Priority 1: Try drive_file_ids["must_reads_json"] → NOT FOUND
  → Priority 2: drive_output_paths missing → SKIP
  → Priority 3: Smart folder traversal → ATTEMPT
    → Find folder: Daily → FOUND
    → Find folder: daily-2026-01-14 → FOUND
    → Find file: must_reads.json → NOT FOUND ❌
  → Priority 4: Legacy output_paths missing → SKIP
  → FAIL: Raise HTTPException 503
```

**Logs**:
```
[daily/daily-2026-01-14] Resolving 'must_reads' - will try keys: ['must_reads', 'must_reads_json']
[daily/daily-2026-01-14] Attempting smart folder traversal fallback
[daily/daily-2026-01-14] Looking for file in: Daily/daily-2026-01-14/must_reads.json
[daily/daily-2026-01-14] File 'must_reads.json' not found in Daily/daily-2026-01-14/
[daily/daily-2026-01-14] Attempting legacy output_paths fallback
[daily/daily-2026-01-14] Output file 'must_reads' not found via any resolution method for mode=daily, run_id=daily-2026-01-14. Tried: drive_file_ids=['report_md'], drive_output_paths=N/A, folder_traversal, legacy_paths
```

**Error Response**:
```json
{
  "detail": "Output file 'must_reads' not found via any resolution method for mode=daily, run_id=daily-2026-01-14. Tried: drive_file_ids=['report_md'], drive_output_paths=N/A, folder_traversal, legacy_paths"
}
```

## Key Improvements

### Before Fix ❌
- Only tried exact key match (`"must_reads"`)
- No folder traversal fallback
- Failed if manifest schema changed

### After Fix ✅
- Tries multiple key variants (`"must_reads"`, `"must_reads_json"`)
- Smart folder traversal as fallback
- Works even with empty/minimal manifests
- Detailed error messages showing what was tried

## Performance Comparison

| Method | Speed | Reliability | Use Case |
|--------|-------|-------------|----------|
| drive_file_ids | ⚡⚡⚡ ~200ms | Highest | Modern pipeline with file IDs |
| drive_output_paths | ⚡⚡ ~300ms | High | Modern pipeline with paths |
| Smart folder traversal | ⚡ ~500ms | Medium | Fallback for old/minimal manifests |
| Legacy output_paths | ⚡ ~500ms | Low | Backward compatibility only |

## Recommendations

For optimal performance, pipeline should generate manifests with:
1. `drive_file_ids` (fastest)
2. `drive_output_paths` (fast fallback)
3. Use suffixed keys: `must_reads_json`, `report_md`, `summaries_json`, `new_csv`
