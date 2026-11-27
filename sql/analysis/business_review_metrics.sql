-- One row per accepted business. Zero-review businesses remain visible.
WITH review_statistics AS (
    SELECT
        business_id,
        count(*) AS accepted_review_count,
        avg(stars) AS observed_mean_rating,
        median(stars) AS observed_median_rating,
        var_samp(stars) AS sample_rating_variance,
        stddev_samp(stars) AS sample_rating_standard_deviation,
        min(stars) AS minimum_rating,
        max(stars) AS maximum_rating
    FROM reviews
    GROUP BY business_id
)
SELECT
    b.business_id,
    b.name,
    b.city,
    b.state,
    b.categories,
    b.source_stars,
    b.source_review_count,
    coalesce(r.accepted_review_count, 0) AS accepted_review_count,
    r.observed_mean_rating,
    r.observed_median_rating,
    r.sample_rating_variance,
    r.sample_rating_standard_deviation,
    r.minimum_rating,
    r.maximum_rating
FROM businesses AS b
LEFT JOIN review_statistics AS r USING (business_id)
