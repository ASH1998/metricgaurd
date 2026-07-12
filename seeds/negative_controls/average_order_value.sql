SELECT
  DATE_TRUNC('week', order_date)::date AS week_start,
  AVG(total_amount) AS average_order_value
FROM metric.orders
GROUP BY 1
ORDER BY 1;
