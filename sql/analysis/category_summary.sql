-- Comma-separated source categories are expanded without changing the source table.
WITH expanded_categories AS (
    SELECT
        b.business_id,
        trim(category) AS category
    FROM businesses AS b,
    unnest(string_split(b.categories, ',')) AS values_table(category)
    WHERE b.categories IS NOT NULL
),
business_metrics AS (
    SELECT
        business_id,
        count(*) AS review_count,
        avg(stars) AS observed_mean_rating
    FROM reviews
    GROUP BY business_id
)
SELECT
    c.category,
    count(DISTINCT c.business_id) AS business_count,
    coalesce(sum(m.review_count), 0) AS accepted_review_count,
    sum(m.observed_mean_rating * m.review_count)
        / nullif(sum(m.review_count), 0) AS review_weighted_mean_rating
FROM expanded_categories AS c
LEFT JOIN business_metrics AS m USING (business_id)
WHERE c.category <> ''
GROUP BY c.category
ORDER BY business_count DESC, c.category
