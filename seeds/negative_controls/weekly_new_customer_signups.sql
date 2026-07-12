SELECT
  DATE_TRUNC('week', signup_date)::date AS week_start,
  COUNT(DISTINCT customer_id) AS weekly_new_customer_signups
FROM metric.customers
GROUP BY 1
ORDER BY 1;
