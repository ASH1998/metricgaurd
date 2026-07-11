-- Exec dashboard revenue: every order counts, whatever its status —
-- canceled, disputed and returned orders are all silently included.
SELECT
    DATE_TRUNC('week', o.order_date) AS week_start,
    SUM(o.total_amount) AS weekly_revenue
FROM metric.orders o
GROUP BY 1
ORDER BY 1
