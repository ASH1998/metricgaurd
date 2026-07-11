-- Sales ops "bookings": recomputed from line items with discounts applied,
-- excluding only canceled orders (returned/disputed still count as booked).
SELECT
    DATE_TRUNC('week', o.order_date) AS week_start,
    SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct / 100.0)) AS weekly_revenue
FROM metric.order_items oi
JOIN metric.orders o ON o.order_id = oi.order_id
WHERE o.order_status <> 'canceled'
GROUP BY 1
ORDER BY 1
