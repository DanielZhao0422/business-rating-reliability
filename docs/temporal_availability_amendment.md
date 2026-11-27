# Temporal availability amendment

Protocol v1.1.0 supersedes the original creation-date implementation for
rating availability. The original computation and protocol v1.0.0 were
preserved in the local baseline under
`reports/local/temporal_correction_baseline/`. The amendment followed the
takeover inspection and the original analysis. It retains the estimator,
prior grid, expanding-window dates, January 1, 2018 cutoff, paired seller
cohort rule, primary weighting, bootstrap and uncertainty models.

## Rating time and eligibility

Olist describes sending a customer satisfaction survey and receiving the
customer's score as distinct events. The review schema describes
`review_creation_date` as survey send time and `review_answer_timestamp` as
customer answer time. Answer time is the available source proxy for the
first possible use of a score. The source does not measure a separate
ingestion, moderation or publication lag. [Olist dataset description](https://www.kaggle.com/olistbr/brazilian-ecommerce) ·
[Review-table schema](https://www.cse.iitd.ac.in/~mausam/papers/emnlp25.pdf).

The typed table retains both timestamps. A missing, unparseable or nonfinite
answer, or a finite answer earlier than creation, disqualifies an otherwise
attributable rating. Preparation reports these as separate flags and primary
reasons. Exact equality between answer and creation is valid. There is no
maximum-delay threshold or substitution of creation for an invalid answer.
All 95,245 originally eligible scores satisfy the corrected rules.

Each cutoff represents midnight on that date in the source's timezone-naive
clock. Training uses answers strictly before that timestamp. Validation
and evaluation start inclusively and have exclusive end dates. The final
end is midnight after the latest eligible answer date, October 30, 2018 in
this dataset. The prior and pooled reference obey the same timestamp rules.
On the primary boundary, 655 ratings from 322 sellers moved from historical
to subsequent. The July and October 2017 fold boundaries moved 321 ratings
from 197 sellers and 428 ratings from 235 sellers, respectively.

## Contrast and sensitivity

Improvement is raw MAE minus adjusted MAE. Positive values favor adjustment.
The bootstrap already used this contrast. Protocol v1.1.0 aligns the
configuration and validator with it. The historical minimum of 50 and
evaluation minima of 3 and 20 remain available as supplemental sensitivities.
The originally configured thresholds remain preplanned. Prior selection
still uses only the two locked 2017 folds and selected 20 again.

| Checkpoint | Creation-date baseline | Answer-time correction |
|---|---:|---:|
| Historical / later ratings | 40,976 / 54,269 | 40,321 / 54,924 |
| Paired sellers | 1,094 | 1,098 |
| Raw / adjusted seller-equal MAE | 0.690120 / 0.537479 | 0.693688 / 0.538918 |
| Absolute / relative improvement | 0.152640 / 22.118% | 0.154769 / 22.311% |
| Paired 95% bootstrap interval | [0.120896, 0.182846] | [0.124276, 0.186277] |

Both absolute error estimates increased; their difference also increased
slightly. The runs have different period membership and partially different
seller cohorts. The within-run intervals quantify improvement for each
cohort and do not test the difference between runs.

## Operational timing and Power BI

Purchase time still defines the forward order event. An eligible forward
order needs a subsequent review answer and a historical seller estimate.
Actual and estimated delivery timestamps define lateness, conditional on
delivered status and having both timestamps. Changing the answer gate caused
no forward purchases to enter or leave in this dataset. The forward cohort
lost 119 orders from eight sellers who no longer had a historical estimate.

All 15 corrected CSV imports reconciled with the existing export contract.
The local presentation was refreshed and saved with the corrected data in
its own cache. Keep the entire corrected package together when refreshing.
Its pseudonymization key differs from the preserved creation-date baseline.
See the [Power BI implementation guide](powerbi_import_guide.md).
