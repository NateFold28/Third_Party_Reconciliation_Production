# Vendor Mapping Fix List (from remaining multi-SF partner conflicts)

Source extract: logs/vendor_fix_priority_from_conflicts_20260826.csv

## Vendors to fix first (ranked by impacted rows)
1. KeepIT
2. Acronis
3. SentinelOne
4. Webroot
5. Proofpoint
6. ESET
7. Bitdefender
8. Auvik
9. Exium

## Impact snapshot
| Vendor | Impacted rows | Impacted partner keys | Abs amount delta |
|---|---:|---:|---:|
| KeepIT | 885 | 27 | 126,820.93 |
| Acronis | 378 | 16 | 100,042.42 |
| SentinelOne | 197 | 18 | 279,150.33 |
| Webroot | 73 | 10 | 9,182.78 |
| Proofpoint | 69 | 7 | 22,289.70 |
| ESET | 62 | 4 | 21,880.04 |
| Bitdefender | 25 | 5 | 10,477.38 |
| Auvik | 22 | 3 | 26,947.97 |
| Exium | 20 | 3 | 10,151.00 |

## Interpretation
- Yes, these are primarily mapping-quality issues for the remaining partner-name -> multi-SF conflicts.
- They are not auto-resolvable by merge-date logic: 47 conflict keys remain and all require manual curation.
- Merge-date logic already cleaned stale old->new SF_ID transitions; what remains are true ambiguous partner mappings (same normalized partner mapped to multiple valid canonical SF_IDs).
