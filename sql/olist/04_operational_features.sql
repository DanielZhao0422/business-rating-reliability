-- Order-level features are computed before category joins to avoid value duplication.
CREATE TABLE seller_operational_features (
    order_id VARCHAR PRIMARY KEY,
    seller_id VARCHAR NOT NULL,
    order_status VARCHAR,
    purchase_timestamp TIMESTAMP,
    estimated_delivery_timestamp TIMESTAMP,
    actual_delivery_timestamp TIMESTAMP,
    delivery_delay_days BIGINT,
    delivered_late BOOLEAN,
    item_count BIGINT NOT NULL,
    distinct_product_count BIGINT NOT NULL,
    total_item_value DECIMAL(38, 2),
    total_freight_value DECIMAL(38, 2),
    all_item_values_valid BOOLEAN NOT NULL,
    all_freight_values_valid BOOLEAN NOT NULL
);

INSERT INTO seller_operational_features
WITH item_features AS (
    SELECT
        order_id,
        count(*) AS item_count,
        count(DISTINCT product_id) AS distinct_product_count,
        sum(item_value) FILTER (WHERE NOT invalid_item_value)
            AS total_item_value,
        sum(freight_value) FILTER (WHERE NOT invalid_freight_value)
            AS total_freight_value,
        bool_and(NOT invalid_item_value) AS all_item_values_valid,
        bool_and(NOT invalid_freight_value) AS all_freight_values_valid
    FROM source_order_items
    WHERE NOT invalid_order_id
    GROUP BY order_id
)
SELECT
    r.order_id,
    r.seller_id,
    r.order_status,
    r.order_purchase_timestamp AS purchase_timestamp,
    o.order_estimated_delivery_date AS estimated_delivery_timestamp,
    o.order_delivered_customer_date AS actual_delivery_timestamp,
    CASE
        WHEN o.order_delivered_customer_date IS NOT NULL
         AND o.order_estimated_delivery_date IS NOT NULL
        THEN date_diff(
            'day',
            o.order_estimated_delivery_date,
            o.order_delivered_customer_date
        )
    END AS delivery_delay_days,
    CASE
        WHEN o.order_delivered_customer_date IS NULL
          OR o.order_estimated_delivery_date IS NULL THEN NULL
        ELSE o.order_delivered_customer_date > o.order_estimated_delivery_date
    END AS delivered_late,
    f.item_count,
    f.distinct_product_count,
    f.total_item_value,
    f.total_freight_value,
    f.all_item_values_valid,
    f.all_freight_values_valid
FROM seller_rating_observations AS r
JOIN source_orders AS o USING (order_id)
JOIN item_features AS f USING (order_id);

-- Many-to-many order/category representation; no category is chosen arbitrarily.
CREATE TABLE order_product_categories AS
SELECT DISTINCT
    r.order_id,
    p.product_category_name AS product_category
FROM seller_rating_observations AS r
JOIN source_order_items AS i USING (order_id)
JOIN source_products AS p USING (product_id)
WHERE p.product_category_name IS NOT NULL
  AND trim(p.product_category_name) <> '';

CREATE UNIQUE INDEX order_product_categories_key_idx
ON order_product_categories(order_id, product_category);
