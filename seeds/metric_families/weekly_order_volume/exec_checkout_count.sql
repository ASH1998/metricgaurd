-- The executive funnel tile counts every checkout attempt, including failed outcomes.
SELECT
    DATE_TRUNC('week', o.order_date) AS week_start,
    COUNT(DISTINCT o.order_id) AS weekly_orders
FROM metric.orders o
GROUP BY 1
ORDER BY 1
