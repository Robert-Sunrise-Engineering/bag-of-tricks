# Phase 3: Data Loading & CRS Handling

## Objective
Load captured and authoritative layers from AGOL as GeoDataFrames, and handle coordinate reference system conversion for accurate distance calculations.

## Dependencies
- Phase 2 must be complete: AGOL authentication and layer metadata retrieval working

## Deliverables

### 1. Data Loading

Implement a `load_layer_as_gdf(gis, layer_url, layer_info)` function that:
1. Uses the `arcgis.features.FeatureLayerCollection` API to query all features from the layer
2. Converts the result to a `geopandas.GeoDataFrame`
3. Ensures the GeoDataFrame has a proper geometry column
4. Preserves all attribute fields including `OBJECTID` and `GlobalID`
5. Skips records where the geometry is null or empty:
   - Log a warning: `"Skipping record OBJECTID=<id>: null/empty geometry"`
   - Do NOT include these records in the returned GeoDataFrame
6. Returns the GeoDataFrame with original WGS 84 (EPSG:4326) geometries

If the layer is empty (0 features after filtering null geometries):
- Log a warning: `"Layer <name> has no features with valid geometry"`
- Return an empty GeoDataFrame with the same schema (columns preserved)

### 2. CRS Auto-Detection

Implement a `detect_utm_zone(gdf)` function that:
1. Computes the centroid of all geometries in the GeoDataFrame:
   - Extract longitude and latitude from the centroid point
2. Calculates the UTM zone number:
   ```
   zone = int(np.floor((centroid_lon + 180) / 6)) + 1
   ```
3. Determines the UTM hemisphere:
   - If centroid latitude >= 0: use northern hemisphere (EPSG 326xx)
   - If centroid latitude < 0: use southern hemisphere (EPSG 327xx)
4. Computes the EPSG code:
   ```
   epsg = 32600 + zone   # northern
   epsg = 32700 + zone   # southern
   ```
5. Returns the EPSG code as an integer

### 3. CRS Reprojection

Implement a `reproject_to_utm(gdf, epsg_code)` function that:
1. Takes a GeoDataFrame in WGS 84 and a target EPSG code
2. Reprojects the GeoDataFrame to the target CRS
3. Returns the reprojected GeoDataFrame
4. If the GeoDataFrame is empty, return it unchanged (no reprojection needed)

### 4. Workflow: Load + Reproject

Implement a `prepare_data(gis, captured_url, auth_url, captured_info, auth_info)` function that:
1. Loads both layers as GeoDataFrames in WGS 84 (EPSG:4326) using `load_layer_as_gdf()`
2. Logs feature counts: `"Loaded <n> features from <layer_name>"`
3. Logs skipped counts: `"Skipped <n> records with null/empty geometry from <layer_name>"`
4. Detects UTM zone from the centroid of the **authoritative** layer using `detect_utm_zone()`
5. Logs the detected zone: `"Detected UTM zone: EPSG:<code>"`
6. Creates transient UTM-reprojected copies of both GeoDataFrames using `reproject_to_utm()`
7. Returns an object/dict containing:
   - `captured_wgs84` — GeoDataFrame in WGS 84 (for output)
   - `auth_wgs84` — GeoDataFrame in WGS 84 (for output)
   - `captured_utm` — GeoDataFrame in UTM (for spatial indexing and distance calculations)
   - `auth_utm` — GeoDataFrame in UTM (for spatial indexing and distance calculations)
   - `utm_epsg` — the detected EPSG code

## Coordinate System Details

- **Source CRS**: WGS 84 (EPSG:4326) — degrees, as stored in AGOL
- **Working CRS**: UTM zone — meters, for distance calculations
- **Output CRS**: WGS 84 — original geometries are preserved for review and output

The UTM reprojection is **transient only**. All geometries stored in review files and used for output must be in WGS 84. The UTM GeoDataFrames are discarded after matching completes.

## Edge Cases

| Case | Handling |
|------|----------|
| Empty captured layer | Return empty GeoDataFrame, log warning, continue to matching (all will be "new") |
| Empty authoritative layer | Return empty GeoDataFrame, all captured records will be "new" |
| Single-point dataset | UTM zone detection still works from the single point's coordinates |
| Data spanning UTM zone boundary | Uses authoritative layer's centroid; document this limitation |

## Test Criteria
- `load_layer_as_gdf()` returns a GeoDataFrame with all fields and correct WGS 84 geometries
- Null/empty geometries are skipped with warnings logged
- `detect_utm_zone()` returns correct EPSG codes for known locations:
  - New York (lat ~40.7, lon ~-74.0) → EPSG 32618
  - London (lat ~51.5, lon ~-0.1) → EPSG 32630
  - Sydney (lat ~-33.9, lon ~151.2) → EPSG 32756
- `reproject_to_utm()` correctly converts coordinates from degrees to meters
- `prepare_data()` returns all four GeoDataFrames (WGS84 + UTM for both layers)
- Empty layers are handled gracefully without errors
