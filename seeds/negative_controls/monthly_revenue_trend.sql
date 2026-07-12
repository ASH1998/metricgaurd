SELECT
  DATE_TRUNC('month', order_date)::date AS month_start,
  SUM(total_amount) AS monthly_revenue
FROM metric.orders
GROUP BY 1
ORDER BY 1;
