-- Exactly one privacy-safe rating observation per eligible order.
CREATE TABLE seller_rating_observations (
    order_id VARCHAR PRIMARY KEY,
    seller_id VARCHAR NOT NULL,
    review_id VARCHAR NOT NULL,
    rating TINYINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_creation_date TIMESTAMP NOT NULL,
    review_answer_timestamp TIMESTAMP NOT NULL CHECK (
        isfinite(review_answer_timestamp) AND review_answer_timestamp >= review_creation_date
    ),
    order_purchase_timestamp TIMESTAMP,
    order_status VARCHAR
);

INSERT INTO seller_rating_observations
SELECT
    e.order_id,
    e.seller_id,
    r.review_id,
    r.review_score AS rating,
    r.review_creation_date,
    r.review_answer_timestamp,
    o.order_purchase_timestamp,
    o.order_status
FROM order_eligibility AS e
JOIN source_orders AS o USING (order_id)
JOIN source_order_reviews AS r
  ON e.order_id = r.order_id AND e.review_id = r.review_id
WHERE e.is_eligible;

CREATE INDEX seller_rating_observation_seller_idx
ON seller_rating_observations(seller_id);
