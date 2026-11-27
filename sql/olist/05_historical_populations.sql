-- The placeholder is replaced by the fixed pipeline cutoff before execution.
CREATE VIEW historical_rating_observations AS
SELECT *
FROM seller_rating_observations
WHERE review_answer_timestamp < DATE '{{HISTORICAL_CUTOFF}}';

CREATE VIEW evaluation_rating_observations AS
SELECT *
FROM seller_rating_observations
WHERE review_answer_timestamp >= DATE '{{HISTORICAL_CUTOFF}}';

CREATE TABLE analytical_population_summary AS
SELECT 'source_order_rows' AS population, count(*) AS observation_count,
       count(DISTINCT order_id) AS seller_or_entity_count
FROM source_orders
UNION ALL
SELECT 'order_eligibility_rows', count(*), count(DISTINCT order_id)
FROM order_eligibility
UNION ALL
SELECT 'eligible_rating_observations', count(*), count(DISTINCT seller_id)
FROM seller_rating_observations
UNION ALL
SELECT 'historical_rating_observations', count(*), count(DISTINCT seller_id)
FROM historical_rating_observations
UNION ALL
SELECT 'evaluation_rating_observations', count(*), count(DISTINCT seller_id)
FROM evaluation_rating_observations;
