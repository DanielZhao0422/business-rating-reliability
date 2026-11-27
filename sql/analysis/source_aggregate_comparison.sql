-- Source fields and reconstructed metrics are shown side by side, not as errors.
WITH reconstructed AS (
    SELECT
        business_id,
        count(*) AS reconstructed_review_count,
        avg(stars) AS reconstructed_mean_rating
    FROM reviews
    GROUP BY business_id
)
SELECT
    b.business_id,
    b.source_stars,
    b.source_review_count,
    r.reconstructed_mean_rating,
    r.reconstructed_review_count,
    b.source_stars - r.reconstructed_mean_rating AS rating_difference,
    b.source_review_count - r.reconstructed_review_count AS count_difference
FROM businesses AS b
LEFT JOIN reconstructed AS r USING (business_id)
