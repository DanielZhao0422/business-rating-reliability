-- Distribution of accepted individual review ratings.
SELECT
    stars,
    count(*) AS review_count,
    count(*) / nullif(sum(count(*)) OVER (), 0) AS proportion
FROM reviews
GROUP BY stars
ORDER BY stars
