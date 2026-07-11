-- Finance revenue: only orders that stayed sold. Canceled, returned and
-- disputed orders are excluded entirely.
SELECT
    DATE_TRUNC('week', o.order_date) AS week_start,
    SUM(o.total_amount) AS weekly_revenue
FROM metric.orders o
WHERE o.order_status NOT IN ('canceled', 'returned', 'disputed')
GROUP BY 1
ORDER BY 1
