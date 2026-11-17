# Synthetic Sample Data

Only approved, clearly labeled synthetic fixtures may be committed here. Do not
place Yelp records, real review text, private records, or derived real data in
this directory.

`synthetic_businesses.jsonl` and `synthetic_reviews.jsonl` contain fictional
identifiers and businesses. They intentionally include valid records, missing
optional values, malformed lines, non-object JSON, duplicates, invalid required
fields, and one orphaned review. They contain no review text.

Expected final results after first-occurrence deduplication and orphan removal:

| Input | Total lines | Blank | Accepted | Rejected |
|---|---:|---:|---:|---:|
| Businesses | 12 | 1 | 5 | 6 |
| Reviews | 20 | 1 | 7 | 12 |

Expected business duplicate outcomes are one identical and one conflicting
duplicate. Expected review duplicate outcomes are also one identical and one
conflicting duplicate. The accepted reviews preserve ratings at both ends of
the valid 1--5 scale. `empty_businesses.jsonl` and `empty_reviews.jsonl` verify
the empty-input behavior.

