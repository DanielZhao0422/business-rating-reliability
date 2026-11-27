-- Accepted review activity by calendar month.
SELECT
    CAST(date_trunc('month', date) AS DATE) AS review_month,
    count(*) AS review_count,
    avg(stars) AS observed_mean_rating
FROM reviews
GROUP BY review_month
ORDER BY review_month
