# Global optimal matching design

## Context

The current matching algorithm (`pick_closest_unclaimed` in `conflate/matching.py`, called at step
7c inside the per-feature loop in `conflate/cli.py`) resolves each captured feature's match
independently and greedily: it claims whichever unclaimed authoritative feature is nearest at the
moment it's processed, in whatever order the paged API happens to return captured features.

A real production dry run surfaced why this breaks: a cluster of captured points sits at a small,
uniform positional offset from its true authoritative counterparts (a systematic capture-method
artifact, not noise). Greedy nearest-neighbor can be fooled by this into matching the wrong
physical features — silently, with no error, and a report that looks perfectly clean (small
distances, all under threshold, no duplicates flagged). Since the goal of this tool is to
eventually run unattended, a matching algorithm with this blind spot can't be trusted to do that.

This document replaces the greedy algorithm with a global optimal assignment
(`scipy.optimize.linear_sum_assignment`, the Hungarian algorithm), proves the correctness of the
append-penalty construction, and verifies it against two worked, script-reproduced failure
patterns before implementation begins.

## Algorithm: `assign_matches()` (new function, `conflate/matching.py`)

Replaces the per-feature `pick_closest_unclaimed` call in `cli.py`'s loop with a single, whole-layer
solve.

**Inputs**: the pool of *unprocessed* captured features (not in the ledger, per `is_processed`) and
*unclaimed* authoritative features (OID not in `claimed_authoritative_oids`, seeded from the ledger
exactly as today via `_seed_claimed_oids`) — same eligibility rules as today, unchanged.

**Cost matrix construction**: rows = captured features (n), columns = unclaimed authoritative
features (m) **plus** n dedicated per-row "append" dummy columns (one reserved slot per captured
feature — cheap only for its own row, effectively infinite for every other row, so opt-outs can't
interfere with each other).

- Real cell cost = geodesic distance (`geometry.geodesic_distance`), if within `match_threshold_m`
  **and** type fields match (when both `type_field_authoritative`/`type_field_captured` are
  configured for the layer — same rule as today's `find_candidates`); otherwise a large infeasible
  sentinel.
- Each row's own dummy/append cost is `K`. **Invariant: `K` must strictly exceed the maximum total
  real cost any full assignment can ever accumulate**, i.e. `K > min(n, m) * threshold_m`.
  Concretely: `K = threshold_m * (min(n, m) + 1)`.
- **Ordering requirement**: the infeasible-real-cell sentinel must be strictly greater than `K`,
  which must be strictly greater than any feasible real cost. If the sentinel isn't kept above `K`,
  the solver could prefer an out-of-threshold real match over an append — silently consuming an
  authoritative feature for a match beyond `match_threshold_m` before any post-processing check can
  catch it. A should-never-fire assertion with loud logging stays in place as a backstop, not as the
  primary correctness mechanism.

**Why the invariant is sufficient (proof)**: consider any two full assignments A and B, where A uses
`a` real matches (with real-cost sum `sumA`) and B uses `b < a` real matches (sum `sumB`), the rest
of each being appends at cost `K`. Then:

```
total(A) - total(B) = (sumA + (n-a)*K) - (sumB + (n-b)*K)
                    = (sumA - sumB) - (a-b)*K
```

Since `sumA <= a*threshold_m <= min(n,m)*threshold_m` and `sumB >= 0`, we have
`sumA - sumB <= min(n,m)*threshold_m`. Since `a > b` are integers, `(a-b) >= 1`, so
`(a-b)*K >= K`. Therefore:

```
total(A) - total(B) <= min(n,m)*threshold_m - K < 0   (since K > min(n,m)*threshold_m)
```

So `total(A) < total(B)` always, whenever `a > b` — **more real matches always strictly wins,
regardless of which individual distances are involved.** This is what guarantees maximizing the
number of real matches always dominates minimizing total distance among them, for any number of
traded appends, not just one. With `match_threshold_m` in the low tens of meters and `n`/`m` in the
low hundreds (this project's layer sizes), `K` lands in the low thousands — no float-precision or
overflow concern, but worth stating explicitly so a future editor doesn't "simplify" it to a bare
small constant.

**Solve once** via `linear_sum_assignment` (handles rectangular matrices natively — `n` rows,
`m + n` columns — and always assigns every row; O(n³), trivial at these layer sizes).

**Post-processing**: a row assigned to a real column with distance ≤ `match_threshold_m` → update;
a row assigned to its dummy column → append.

## Worked examples (script-verified)

Both examples below were computed with a standalone Python script (Euclidean distance, flat local
plane — not `geodesic_distance` — since the point is to verify the assignment logic, not the
projection math) and the raw output is reproduced verbatim. Anyone doubting these numbers can
re-run the same script from these coordinates.

### Row-of-4 (forced spurious append)

Authoritative points, spaced 4m apart on a line: `A1=(0,0)`, `A2=(4,0)`, `A3=(8,0)`, `A4=(12,0)`.
Captured points, each shifted +3m along the same line: `C1=(3,0)`, `C2=(7,0)`, `C3=(11,0)`,
`C4=(15,0)`. `match_threshold_m = 10.67` (the real per-layer threshold used for the valve layers in
this project's `config.json`).

Full distance matrix (meters):

| | A1 | A2 | A3 | A4 |
|---|---|---|---|---|
| C1 | 3.000 | 1.000 | 5.000 | 9.000 |
| C2 | 7.000 | 3.000 | 1.000 | 5.000 |
| C3 | 11.000 | 7.000 | 3.000 | 1.000 |
| C4 | 15.000 | 11.000 | 7.000 | 3.000 |

(C3-A1 at 11.000 and C4-A1/A2 at 15.000/11.000 exceed the 10.67m threshold and are infeasible.)

**Greedy** (process C1→C2→C3→C4 in order, claim nearest unclaimed): C1 claims A2 (1.000m, wrong —
its true match is A1 at 3.000m), C2 claims A3 (1.000m, wrong), C3 claims A4 (1.000m, wrong), C4 has
no unclaimed candidate left within threshold → **unmatched, would append**. Result: 0 of 4 captured
points get their true match; every individual "match" looks clean (1.000m, comfortably under
threshold); C4 is spuriously appended despite having a perfectly good true match (A4, 3.000m) that
was stolen by C3.

**Diagonal assignment** (C1-A1, C2-A2, C3-A3, C4-A4 — each captured point matched to its true
counterpart): total cost 12.000m, all 4 correct. Since `K = 10.67 * 5 = 53.35` is far larger than
any real cost here, `assign_matches()` will always prefer 4 real matches (12.000m total) over
greedy's 3 real matches + 1 append (which costs at least `K = 53.35` for the appended row alone) —
per the invariant proof above, this holds regardless of the specific distances.

### Diamond cluster (locally-tempting wrong match)

Authoritative points, a diamond elongated north-south: `N=(0,10)`, `S=(0,-10)`, `E=(5,0)`,
`W=(-5,0)`. Captured points, each shifted southeast by `(7,-7)`: `N_cap=(7,3)`, `S_cap=(7,-17)`,
`E_cap=(12,-7)`, `W_cap=(2,-7)`.

Full distance matrix (meters):

| | N | S | E | W |
|---|---|---|---|---|
| N_cap | 9.8995 | 14.7648 | 3.6056 | 12.3693 |
| S_cap | 27.8927 | 9.8995 | 17.1172 | 20.8087 |
| E_cap | 20.8087 | 12.3693 | 9.8995 | 18.3848 |
| W_cap | 17.1172 | 3.6056 | 7.6158 | 9.8995 |

Two of the four captured points have a *wrong* nearest neighbor: `N_cap`'s true match (`N`,
9.8995m) ranks only 2nd, behind `E` at 3.6056m; `W_cap`'s true match (`W`, 9.8995m) ranks only 3rd,
behind `S` (3.6056m) and `E` (7.6158m). This is exactly the "leading edge" pattern the plan set out
to reproduce: points oriented toward the shift direction have their true match buried in their
candidate list, not at the top.

All 24 permutations of the 4-point assignment were brute-force enumerated. Results (full list
script-verified, top 3 shown):

| Rank | Total cost | N_cap→ | S_cap→ | E_cap→ | W_cap→ |
|---|---|---|---|---|---|
| 1 (global min) | 39.5980 | N | S | E | W |
| 2 (runner-up) | 44.2132 | N | W | E | S |
| 3 | 44.2132 | E | S | N | W |

The **true diagonal assignment (N-N, S-S, E-E, W-W) is the unique global minimum** at 39.5980m
(each leg exactly 9.8995m). The runner-up, at 44.2132m, is a 4.6152m / 11.66% margin above the
optimum — a real, checkable margin, not paper-thin, though the margin's exact size is a property of
this specific geometry and shouldn't be read as a general robustness guarantee for all
near-symmetric clusters. An earlier, hand-verified pass at this same style of example (coordinates
not available to re-derive here) reportedly found a margin under 1% — thinner than this instance's
11.66%. Rather than treat either figure as characteristic, `assignment_overridden_nearest` (below)
exists precisely so a human can sanity-check exactly these cases rather than trust the algorithm
blindly on close calls, regardless of how wide or thin the margin happens to be in a given instance.

**Greedy** (process N_cap→S_cap→E_cap→W_cap in order, claim nearest unclaimed, no threshold
filtering needed at this scale): N_cap claims E (3.6056m, wrong), S_cap claims S (9.8995m,
correct — its true match was already its nearest), E_cap's true match E is now taken, so it claims
W (18.3848m, wrong), and W_cap's true match W is now also taken, so it claims N (17.1172m, wrong).
Greedy total: 49.0071m — only 1 of 4 correct, and 23.76% worse than the true optimum (39.5980m).
The global solver, unlike greedy, sees that grabbing the locally-cheap wrong match for `N_cap`
forces a much worse compensating match onto whichever row later loses its own true match.

## Report schema changes (`conflate/cli.py`)

There are two separate row schemas built in `cli.py`, and both need the new columns — not just the
one the original draft of this plan covered.

- **Dry-run rows** (`would_update`, `would_append`, `skipped_no_geometry` — built at cli.py:323-432,
  written by `write_report` only when `--apply` is not passed): columns
  `captured_global_id, action, matched_authoritative_oid, distance_m, threshold_m, layer` today.
- **Apply/outcome rows** (built by `_build_outcome_row`, cli.py:483-536, written only on `--apply`
  and the schema `rollback.py` reads back): columns
  `captured_global_id, action, authoritative_oid, distance_m, threshold_m, success, error, attachments_status, attachments_added, ledgered, layer`
  today — note the field is `authoritative_oid` here, not `matched_authoritative_oid`.

This distinction matters because **the dry-run report is never written during an `--apply` run**
(`cli.py:436-445` returns immediately after writing it, only when `not args.apply`). If the new
columns were added only to the dry-run schema, a live `--apply` run's own persisted report — the
one `rollback.py` reads, the one anyone doing post-hoc review of an actual production change would
look at — would carry zero information about whether a given match was a landslide or a coin-flip.
That would defeat the entire purpose of `assignment_overridden_nearest` for exactly the run where a
bad call has real consequences. So:

- **New column `candidates_json`**, added to **both** schemas: for every captured feature, a
  JSON-encoded list of every authoritative feature within `match_threshold_m` (OID, GlobalID,
  distance_m — sorted nearest-first), regardless of who it was actually assigned to. JSON-in-CSV
  matches existing precedent (`attachments_added` is already `json.dumps`'d through the CSV
  round-trip, cli.py:533, specifically because a raw Python list would otherwise be serialized via
  `str(list)` — a Python repr, not valid JSON — as the comment at rollback.py:228-230 explains).
  **`skipped_no_geometry` rows get an explicit empty list (`json.dumps([])`), never a bare `None` or
  missing key.** This isn't because `None` would come through the CSV round-trip as the literal
  text `"None"` — tested directly, `csv.DictWriter` writes a `None` value as an empty string, not
  that text, so that specific mechanism doesn't apply here. The real reason is consistency with the
  defensive pattern `rollback.py:232` already uses for `attachments_added`
  (`json.loads(row.get("attachments_added") or "[]")` plus a `try/except (JSONDecodeError, TypeError)`
  fallback): guaranteeing the column is always valid, parseable JSON means no downstream reader ever
  needs to special-case an empty or missing cell.
- **New boolean column `assignment_overridden_nearest`**, added to **both** schemas: true whenever
  the assigned match is not simply the nearest entry in `candidates_json` — a quick-scan signal for
  reviewing a large report without parsing JSON in every row. In the diamond example above, `N_cap`
  and `W_cap` would both be flagged even though the assignment is correct — that's intentional:
  "needed the full picture to resolve, take a look," not "this is wrong."
- Existing columns in both schemas are unchanged.
- **Implementation note for the outcome schema**: `_build_outcome_row` is called with a `planned`
  dict that already carries private, underscore-prefixed fields threaded through from the original
  loop (`_captured_global_id`, `_authoritative_oid`, `_distance`, etc. — cli.py:489-490,527,546-548).
  The natural mechanism is to stash `_candidates_json` / `_assignment_overridden_nearest` onto
  `planned` at the point `assign_matches()`'s result is consumed (alongside the other `_`-prefixed
  fields), and have `_build_outcome_row` copy them into its returned dict exactly like the other
  planned-derived fields.

## `cli.py` integration

`assign_matches()` is called once, before the per-feature loop (replacing today's steps 7b/7c — the
per-feature `find_candidates` + `pick_closest_unclaimed` calls). The loop then looks up each
captured feature's precomputed result by `GlobalID` and proceeds exactly as today for building
update/append records — with one change from the original draft of this plan: the update/append
record building (dry-run rows) **and** `_build_outcome_row` (apply/outcome rows) both now also
attach `candidates_json`/`assignment_overridden_nearest` from the precomputed result, per the report
schema section above. Everything else downstream (backups, attachments, ledger writes) is
unchanged, since it already just consumes "matched to X at distance Y" or "no match."

## Cross-run invariant (unchanged)

Pools fed into `assign_matches` exclude anything already in the ledger (`is_processed`) or already
claimed (`_seed_claimed_oids`), exactly like today. Already-applied matches from prior runs are
frozen and never reconsidered — this only resolves contention among the current run's unprocessed
features.

## Testing

New tests in `test_matching.py`:

- Direct reproduction of the row-of-4 case above, asserting the exact wrong assignment an
  under-scaled penalty would produce (3 real matches at 1.000m + 1 append) against the exact correct
  assignment the properly-scaled `K` produces (4 matches, 12.000m total) — pinning the arithmetic
  itself, not just "it worked," so a future change to the penalty formula that silently breaks the
  invariant gets caught.
- Direct reproduction of the diamond case above (proving the diagonal assignment, 39.5980m, wins
  over the runner-up, 44.2132m).
- `candidates_json` content correctness (right OIDs/GlobalIDs/distances, right sort order, present
  on both update and append rows, and on both the dry-run and outcome schemas).
- `assignment_overridden_nearest` correctness on both a match-optimal-and-nearest case and a
  match-optimal-but-not-nearest case (e.g. `N_cap`/`W_cap` above).
- New test (added to the outcome-row scope amendment): `_build_outcome_row` carries
  `candidates_json`/`assignment_overridden_nearest` through into the apply/outcome report, mirroring
  the dry-run assertions.
- Type-field filtering still respected (candidates of the wrong type never appear as feasible edges
  or in `candidates_json`).
- Sanity check at realistic scale (a few hundred features) for solve time.

## Dependency

Add `scipy` to `requirements.txt` (currently `arcgis`, `pyproj`, `pytest` only).

## Scope guardrail

Neither `K` (the append penalty) nor the margin used to eyeball "close call" matches is exposed as
a `config.json` knob. Both are derived/fixed quantities (`K` from the invariant proved above;
`assignment_overridden_nearest` is a strict "not the plain-nearest" check, not a tunable margin at
all). Making either configurable would invite tuning that quietly defeats the correctness guarantee
instead of preserving it — not needed for the problem as scoped.

## Verification (once implemented)

- Full `pytest tests/` run, including the new adversarial-cluster regression tests and the
  outcome-row column-propagation test, must pass.
- Re-run the existing dry run against the `virgin_*` layers and confirm: (a) the near-threshold
  hydrant/valve matches flagged in the earlier verification pass now show up with full
  `candidates_json` context, and (b) total counts/assignments are sane (no unexpected swing in
  update/append counts versus the original dry run, since most of that data isn't part of an
  ambiguous cluster).
