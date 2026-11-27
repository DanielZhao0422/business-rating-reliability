-- Persistent invariant results make database validation inspectable.
CREATE TABLE analytical_quality_checks AS
SELECT 'duplicate_final_order_ids' AS check_name,
       count(*) - count(DISTINCT order_id) AS failure_count
FROM seller_rating_observations
UNION ALL
SELECT 'invalid_final_ratings', count(*)
FROM seller_rating_observations
WHERE rating IS NULL OR rating NOT BETWEEN 1 AND 5
UNION ALL
SELECT 'missing_final_review_dates', count(*)
FROM seller_rating_observations
WHERE review_creation_date IS NULL
UNION ALL
SELECT 'invalid_final_review_answer_timestamps', count(*)
FROM seller_rating_observations
WHERE review_answer_timestamp IS NULL OR NOT isfinite(review_answer_timestamp)
   OR review_answer_timestamp < review_creation_date
UNION ALL
SELECT 'missing_final_sellers', count(*)
FROM seller_rating_observations AS r
LEFT JOIN source_sellers AS s USING (seller_id)
WHERE s.seller_id IS NULL
UNION ALL
SELECT 'ambiguous_review_ids_in_final', count(*)
FROM seller_rating_observations AS f
JOIN (
    SELECT review_id
    FROM source_order_reviews
    WHERE NOT invalid_review_id AND NOT invalid_order_id
    GROUP BY review_id
    HAVING count(DISTINCT order_id) > 1
) AS ambiguous USING (review_id)
UNION ALL
SELECT 'historical_evaluation_overlap', count(*)
FROM historical_rating_observations AS h
JOIN evaluation_rating_observations AS e USING (order_id)
UNION ALL
SELECT 'period_total_difference', abs(
    (SELECT count(*) FROM seller_rating_observations)
    - (SELECT count(*) FROM historical_rating_observations)
    - (SELECT count(*) FROM evaluation_rating_observations)
);
