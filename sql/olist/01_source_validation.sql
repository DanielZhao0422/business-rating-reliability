-- Aggregate source validation metrics. Counts describe source rows, not orders.
CREATE TABLE source_validation_summary AS
SELECT 'orders' AS source_table, 'row_count' AS metric, count(*) AS value
FROM source_orders
UNION ALL
SELECT 'orders', 'missing_order_id', count(*) FILTER (WHERE invalid_order_id)
FROM source_orders
UNION ALL
SELECT 'orders', 'duplicate_order_id_excess_rows',
       count(*) FILTER (WHERE NOT invalid_order_id)
       - count(DISTINCT order_id) FILTER (WHERE NOT invalid_order_id)
FROM source_orders
UNION ALL
SELECT 'orders', 'invalid_purchase_timestamp',
       count(*) FILTER (WHERE invalid_purchase_timestamp)
FROM source_orders
UNION ALL
SELECT 'order_items', 'row_count', count(*) FROM source_order_items
UNION ALL
SELECT 'order_items', 'missing_order_id',
       count(*) FILTER (WHERE invalid_order_id) FROM source_order_items
UNION ALL
SELECT 'order_items', 'missing_seller_id',
       count(*) FILTER (WHERE invalid_seller_id) FROM source_order_items
UNION ALL
SELECT 'order_items', 'duplicate_order_item_key_excess_rows',
       count(*) FILTER (
           WHERE NOT invalid_order_id AND order_item_id IS NOT NULL
       ) - count(DISTINCT (order_id, order_item_id)) FILTER (
           WHERE NOT invalid_order_id AND order_item_id IS NOT NULL
       )
FROM source_order_items
UNION ALL
SELECT 'order_items', 'invalid_item_value',
       count(*) FILTER (WHERE invalid_item_value) FROM source_order_items
UNION ALL
SELECT 'order_items', 'invalid_freight_value',
       count(*) FILTER (WHERE invalid_freight_value) FROM source_order_items
UNION ALL
SELECT 'order_items', 'orphan_order_rows', count(*)
FROM source_order_items AS i
LEFT JOIN source_orders AS o ON i.order_id = o.order_id
WHERE NOT i.invalid_order_id AND o.order_id IS NULL
UNION ALL
SELECT 'order_items', 'orphan_seller_rows', count(*)
FROM source_order_items AS i
LEFT JOIN source_sellers AS s ON i.seller_id = s.seller_id
WHERE NOT i.invalid_seller_id AND s.seller_id IS NULL
UNION ALL
SELECT 'reviews', 'row_count', count(*) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'missing_order_id',
       count(*) FILTER (WHERE invalid_order_id) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'missing_review_id',
       count(*) FILTER (WHERE invalid_review_id) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'invalid_rating',
       count(*) FILTER (WHERE invalid_rating) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'invalid_review_date',
       count(*) FILTER (WHERE invalid_review_date) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'missing_review_answer_timestamp',
       count(*) FILTER (WHERE missing_review_answer_timestamp) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'invalid_review_answer_timestamp',
       count(*) FILTER (WHERE invalid_review_answer_timestamp) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'answer_before_review_creation',
       count(*) FILTER (WHERE answer_before_review_creation) FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'duplicate_review_id_excess_rows',
       count(*) FILTER (WHERE NOT invalid_review_id)
       - count(DISTINCT review_id) FILTER (WHERE NOT invalid_review_id)
FROM source_order_reviews
UNION ALL
SELECT 'reviews', 'review_ids_linked_to_multiple_orders', count(*)
FROM (
    SELECT review_id
    FROM source_order_reviews
    WHERE NOT invalid_review_id AND NOT invalid_order_id
    GROUP BY review_id
    HAVING count(DISTINCT order_id) > 1
)
UNION ALL
SELECT 'reviews', 'orders_with_multiple_review_rows', count(*)
FROM (
    SELECT order_id
    FROM source_order_reviews
    WHERE NOT invalid_order_id
    GROUP BY order_id
    HAVING count(*) > 1
)
UNION ALL
SELECT 'reviews', 'orphan_order_rows', count(*)
FROM source_order_reviews AS r
LEFT JOIN source_orders AS o ON r.order_id = o.order_id
WHERE NOT r.invalid_order_id AND o.order_id IS NULL
UNION ALL
SELECT 'sellers', 'row_count', count(*) FROM source_sellers
UNION ALL
SELECT 'sellers', 'missing_seller_id',
       count(*) FILTER (WHERE invalid_seller_id) FROM source_sellers
UNION ALL
SELECT 'sellers', 'duplicate_seller_id_excess_rows',
       count(*) FILTER (WHERE NOT invalid_seller_id)
       - count(DISTINCT seller_id) FILTER (WHERE NOT invalid_seller_id)
FROM source_sellers;
