-- One row per nonblank source order ID. Flags may overlap; primary_reason does not.
CREATE TABLE order_eligibility AS
WITH order_keys AS (
    SELECT
        order_id,
        count(*) AS order_row_count,
        bool_or(invalid_order_id) AS invalid_order_id
    FROM source_orders
    WHERE order_id IS NOT NULL AND trim(order_id) <> ''
    GROUP BY order_id
),
seller_key_quality AS (
    SELECT seller_id, count(*) AS seller_source_rows
    FROM source_sellers
    WHERE NOT invalid_seller_id
    GROUP BY seller_id
),
item_key_quality AS (
    SELECT order_id, order_item_id, count(*) AS key_rows
    FROM source_order_items
    WHERE NOT invalid_order_id AND order_item_id IS NOT NULL
    GROUP BY order_id, order_item_id
),
item_summary AS (
    SELECT
        i.order_id,
        count(*) AS item_row_count,
        count(DISTINCT i.seller_id) FILTER (
            WHERE NOT i.invalid_seller_id
        ) AS distinct_seller_count,
        min(i.seller_id) FILTER (
            WHERE NOT i.invalid_seller_id
        ) AS sole_seller_id,
        bool_or(i.invalid_seller_id OR sq.seller_id IS NULL
                OR sq.seller_source_rows <> 1) AS invalid_seller_relationship,
        bool_or(kq.key_rows > 1) AS duplicate_order_item_key
    FROM source_order_items AS i
    LEFT JOIN seller_key_quality AS sq USING (seller_id)
    LEFT JOIN item_key_quality AS kq USING (order_id, order_item_id)
    WHERE NOT i.invalid_order_id
    GROUP BY i.order_id
),
review_id_quality AS (
    SELECT
        review_id,
        count(DISTINCT order_id) FILTER (
            WHERE NOT invalid_order_id
        ) AS distinct_order_count
    FROM source_order_reviews
    WHERE NOT invalid_review_id
    GROUP BY review_id
),
review_summary AS (
    SELECT
        r.order_id,
        count(*) AS review_row_count,
        min(r.review_id) AS sole_review_id,
        bool_or(r.invalid_review_id) AS invalid_review_id,
        bool_or(coalesce(riq.distinct_order_count, 0) <> 1)
            AS non_unique_review_id,
        bool_or(r.invalid_rating) AS invalid_rating,
        bool_or(r.invalid_review_date) AS invalid_review_date,
        bool_or(r.missing_review_answer_timestamp) AS missing_review_answer_timestamp,
        bool_or(r.invalid_review_answer_timestamp) AS invalid_review_answer_timestamp,
        bool_or(r.answer_before_review_creation) AS answer_before_review_creation
    FROM source_order_reviews AS r
    LEFT JOIN review_id_quality AS riq USING (review_id)
    WHERE NOT r.invalid_order_id
    GROUP BY r.order_id
),
flags AS (
    SELECT
        o.order_id,
        o.order_row_count,
        coalesce(i.item_row_count, 0) AS item_row_count,
        coalesce(i.distinct_seller_count, 0) AS distinct_seller_count,
        CASE WHEN i.distinct_seller_count = 1 THEN i.sole_seller_id END
            AS seller_id,
        coalesce(r.review_row_count, 0) AS review_row_count,
        CASE WHEN r.review_row_count = 1 THEN r.sole_review_id END AS review_id,
        o.order_row_count <> 1 AS duplicate_order_id,
        i.order_id IS NULL AS no_order_items,
        coalesce(i.duplicate_order_item_key, false)
            AS duplicate_order_item_key,
        coalesce(i.invalid_seller_relationship, false) AS invalid_seller_id,
        coalesce(i.distinct_seller_count, 0) > 1 AS multi_seller_order,
        r.order_id IS NULL AS no_review,
        coalesce(r.review_row_count, 0) > 1 AS multiple_reviews,
        coalesce(r.invalid_review_id, false) AS invalid_review_id,
        coalesce(r.non_unique_review_id, false) AS non_unique_review_id,
        coalesce(r.invalid_rating, false) AS invalid_rating,
        coalesce(r.invalid_review_date, false) AS invalid_review_date,
        coalesce(r.missing_review_answer_timestamp, false) AS missing_review_answer_timestamp,
        coalesce(r.invalid_review_answer_timestamp, false) AS invalid_review_answer_timestamp,
        coalesce(r.answer_before_review_creation, false) AS answer_before_review_creation
    FROM order_keys AS o
    LEFT JOIN item_summary AS i USING (order_id)
    LEFT JOIN review_summary AS r USING (order_id)
)
SELECT
    *,
    CASE
        WHEN duplicate_order_id THEN 'DUPLICATE_ORDER_ID'
        WHEN no_order_items THEN 'NO_ORDER_ITEMS'
        WHEN duplicate_order_item_key THEN 'DUPLICATE_ORDER_ITEM_KEY'
        WHEN invalid_seller_id OR distinct_seller_count = 0
            THEN 'INVALID_SELLER_ID'
        WHEN multi_seller_order THEN 'MULTI_SELLER_ORDER'
        WHEN no_review THEN 'NO_REVIEW'
        WHEN multiple_reviews THEN 'MULTIPLE_REVIEWS'
        WHEN invalid_review_id THEN 'INVALID_REVIEW_ID'
        WHEN non_unique_review_id THEN 'NON_UNIQUE_REVIEW_ID'
        WHEN invalid_rating THEN 'INVALID_RATING'
        WHEN invalid_review_date THEN 'INVALID_REVIEW_DATE'
        WHEN missing_review_answer_timestamp THEN 'MISSING_REVIEW_ANSWER_TIMESTAMP'
        WHEN invalid_review_answer_timestamp THEN 'INVALID_REVIEW_ANSWER_TIMESTAMP'
        WHEN answer_before_review_creation THEN 'ANSWER_BEFORE_REVIEW_CREATION'
        ELSE 'ELIGIBLE'
    END AS primary_reason,
    primary_reason = 'ELIGIBLE' AS is_eligible
FROM flags;

CREATE UNIQUE INDEX order_eligibility_order_id_idx
ON order_eligibility(order_id);
