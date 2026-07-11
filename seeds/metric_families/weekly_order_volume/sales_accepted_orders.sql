-- Sales calls every commercially accepted order a booking, including backorders.
SELECT
    DATE_TRUNC('week', o.order_date) AS week_start,
    COUNT(DISTINCT o.order_id) AS weekly_orders
FROM metric.orders o
WHERE o.order_status IN ('confirmed', 'backordered', 'fulfilling', 'in_transit', 'delivered')
GROUP BY 1
ORDER BY 1
