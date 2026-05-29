## R8 Scorer Sensitivity - Overall (50 cases)

| Variant | vector ac | graph ac | hybrid ac | note |
|---|---:|---:|---:|---|
| R8_original | 0.36 | 0.46 | 0.4 | baseline |
| V1: tol_2pct | 0.38 | 0.48 | 0.4 | FinQA tolerance relaxed |
| V2: tol_2pct_unit | 0.4 | 0.48 | 0.4 | V1 + ratio/percent normalization |
| V3: partial_nc90 | 0.4 | 0.52 | 0.44 | nc >= .90 gives ac=0.5 |
| V4: no_suspect | 0.383 | 0.4894 | 0.4255 | CAGR/OF/LOSS excluded from aggregation |

## By Dataset

| Variant | FinDER graph | FinQA graph | FinDER vector | FinQA vector |
|---|---:|---:|---:|---:|
| R8_original | 0.5 | 0.4 | 0.3 | 0.45 |
| V1: tol_2pct | 0.5 | 0.45 | 0.3 | 0.5 |
| V2: tol_2pct_unit | 0.5 | 0.45 | 0.3 | 0.55 |
| V3: partial_nc90 | 0.5 | 0.55 | 0.3167 | 0.525 |
| V4: no_suspect | 0.5556 | 0.4 | 0.3333 | 0.45 |

## Interpretation

- V2 - V1 FinQA graph delta: 0.0.
- V3 - V1 overall graph delta: 0.04.
- Suspect ticker cases removed: 3.
