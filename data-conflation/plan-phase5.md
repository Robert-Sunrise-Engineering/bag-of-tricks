# Phase 5: Spatial Indexing & Matching

## Objective
Build a spatial index on authoritative geometries and match each captured point to the nearest authoritative point(s), classifying each match as clean, ambiguous, or new.

## Dependencies
- Phase 4 must be complete: schema validation and backup working
- Requires UTM-reprojected GeoDataFrames from Phase 3 (`captured_utm`, `auth_utm`)
- Requires matching config: `threshold_ft` and `ambiguity_pct` from `config.json`

## Deliverables

### 1. Spatial Indexing

Implement a `build_spatial_index(gdf)` function that:
1. Takes a GeoDataFrame in UTM coordinates
2. Builds a spatial index using `gdf.sindex` (R-tree from `pygeos`/`shapely`)
3. Returns the spatial index object
4. If the GeoDataFrame is empty, returns `None`

### 2. Distance Calculation

Implement a `calculate_distance_ft(geom_a, geom_b)` function that:
1. Takes two geometries in UTM (meters) coordinates
2. Calculates the Euclidean distance between them
3. Returns the distance in feet: `distance_meters * 3.28084`
4. Since both geometries are in the same UTM zone (meters), this is a direct Euclidean distance

### 3. Point Matching Logic

Implement a `match_points(captured_utm, auth_utm, spatial_index, threshold_ft, ambiguity_pct)` function that:
1. Takes each captured point and queries the spatial index for the **2 nearest** authoritative points
2. Calculates the distance from the captured point to each neighbor (in feet)
3. Classifies the match based on the following rules:

#### Classification Rules

Let:
- `d1` = distance to nearest authoritative point
- `d2` = distance to second-nearest authoritative point (or `infinity` if only 1 neighbor exists)
- `threshold` = `threshold_ft` from config
- `ambiguity_factor` = `1 + (ambiguity_pct / 100)` from config

Then:

| Condition | Classification |
|-----------|---------------|
| `d1 > threshold` OR no authoritative points | **new** |
| `d1 <= threshold` AND `d2 > threshold` | **clean** |
| `d1 <= threshold` AND `d2 <= threshold` AND `d2 > d1 * ambiguity_factor` | **clean** |
| `d1 <= threshold` AND `d2 <= threshold` AND `d2 <= d1 * ambiguity_factor` | **ambiguous** |

In plain terms:
- **Clean match**: nearest point is within threshold, AND either the second point is farther than threshold OR significantly farther than the nearest point (by ambiguity_pct)
- **Ambiguous match**: nearest point is within threshold, AND second point is also within threshold AND within the ambiguity factor of the nearest distance (two authoritative points are equally close candidates)
- **New**: nearest point is farther than threshold, or no authoritative points exist

4. Returns a list of match result dicts, one per captured point:

```python
{
    "captured_objectid": <int>,           # OBJECTID from captured layer
    "auth_globalid": <str or None>,       # GlobalID of matched auth point, or None if "new"
    "auth_objectid": <int or None>,       # OBJECTID of matched auth point, or None if "new"
    "distance_ft": <float>,               # Distance to nearest (None if "new" and no neighbor)
    "match_type": <str>,                  # "clean", "ambiguous", or "new"
    "d1": <float>,                        # Distance to nearest neighbor (None if no neighbors)
    "d2": <float or None>,                # Distance to second neighbor (None if only 1 neighbor)
    "captured_geom_wgs84": <Geometry>,    # Original WGS 84 captured geometry
    "auth_geom_wgs84": <Geometry or None> # Original WGS 84 auth geometry (None if "new")
}
```

### 4. Handling Edge Cases in Matching

| Case | Handling |
|------|----------|
| Empty authoritative layer | All captured points classified as "new" |
| Empty captured layer | Return empty list |
| Single authoritative point | d2 is always infinity; match is "clean" if within threshold, "new" otherwise |
| Captured point exactly at threshold distance | Classified as "new" (threshold is exclusive for match) |

### 5. Logging

Log each match result:
```
Matched OBJECTID <captured_oid>: <match_type> (d1=<d1:.1f> ft, d2=<d2:.1f> ft)
```
For "new" matches:
```
New OBJECTID <captured_oid>: no match within <threshold> ft (nearest: <d1:.1f> ft)
```

### 6. Integration in Main Flow

In `conflate.py`, after schema validation and backup:
1. Build spatial index on `auth_utm`
2. Call `match_points()` with captured/UTM GeoDataFrames and config parameters
3. Store match results for use in Phase 6 (collision resolution) and Phase 7 (dry run output)

## Test Criteria
- `build_spatial_index()` creates a valid spatial index from a GeoDataFrame
- `calculate_distance_ft()` returns correct distance in feet for known geometries
- `match_points()` correctly classifies matches:
  - Nearest within threshold, second far away → "clean"
  - Nearest within threshold, second also close → "ambiguous"
  - Nearest beyond threshold → "new"
  - Exactly at threshold boundary → "new"
- Empty layers are handled without errors
- Match result dicts contain all required fields
- Distance calculations are in feet, not meters
- Logging output is clear and informative
