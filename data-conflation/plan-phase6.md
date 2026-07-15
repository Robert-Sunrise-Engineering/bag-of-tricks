# Phase 6: Global Collision Resolution

## Objective
After per-point matching completes, detect and resolve cases where multiple captured points claim the same authoritative point (many-to-one collisions). The closest captured point wins; the others are reclassified as "new".

## Dependencies
- Phase 5 must be complete: spatial indexing and matching producing match results

## Deliverables

### 1. Collision Detection

Implement a `detect_collisions(match_results)` function that:
1. Takes the list of match result dicts from Phase 5
2. Groups results by `auth_globalid` (for matched results only; "new" records have no auth_globalid)
3. Identifies groups where a single `auth_globalid` appears in multiple match results from different captured points
4. Returns a dict mapping `auth_globalid` to a list of conflicting match results:

```python
{
    "<auth_globalid>": [
        {"captured_objectid": 1, "distance_ft": 3.2, ...},
        {"captured_objectid": 5, "distance_ft": 7.8, ...}
    ]
}
```

### 2. Collision Resolution

Implement a `resolve_collisions(match_results, collisions)` function that:
1. Takes the match results and collision map from above
2. For each collision group:
   - Sort by `distance_ft` ascending (closest first)
   - The closest captured point keeps its match (unchanged)
   - All other captured points in the group are reclassified:
     - `match_type` → `"new"`
     - `auth_globalid` → `None`
     - `auth_objectid` → `None`
     - `auth_geom_wgs84` → `None`
     - `distance_ft` → `None` (or keep original distance for reference — see below)
3. Preserves the original distance for reclassified points in a separate field:
   - Add `collision_distance_ft` to the reclassified match result (the original distance to the claimed auth point)
4. Returns the updated list of match results

### 3. Collision Metadata

For each resolved collision, add metadata to the first (winning) match result:
```python
{
    ...existing fields...
    "collision_wins": 1,           # How many captured points claimed this auth point
    "collision_resolved": True     # This collision was resolved
}
```

### 4. Logging

Log each collision resolution:
```
Collision detected: auth GlobalID <id> claimed by captured OBJECTID <oid1> (d=<d1> ft) and captured OBJECTID <oid2> (d=<d2> ft)
  → OBJECTID <oid1> retains match (closest)
  → OBJECTID <oid2> reclassified as new
```

If no collisions are detected:
```
No collisions detected.
```

### 5. Integration in Main Flow

In `conflate.py`, after matching (Phase 5) and before dry run output (Phase 7):
1. Call `detect_collisions(match_results)`
2. If collisions found, call `resolve_collisions(match_results, collisions)`
3. Log collision summary: `"<n> collision(s) resolved"`
4. Pass the updated match results to Phase 7

## Edge Cases

| Case | Handling |
|------|----------|
| Two captured points at exactly the same distance | Tie-break by captured OBJECTID (lower wins) |
| Three or more captured points claim same auth point | Closest wins, all others reclassified as new |
| No collisions | Return empty collision map, pass match results unchanged |
| All captured points are "new" | No collisions possible, skip resolution |

## Test Criteria
- `detect_collisions()` correctly identifies all many-to-one conflicts
- `resolve_collisions()` correctly reclassifies losers as "new" while preserving the winner
- Tie-breaking by OBJECTID works for equal distances
- Collision metadata is added to winning match results
- Logging output clearly shows which captured points won/lost each collision
- Match results after resolution are consistent (no auth_globalid for "new" records)
