-- Finance books every processed refund as a weekly liability.
SELECT
    DATE_TRUNC('week', r.return_date) AS week_start,
    SUM(r.refund_amount) AS weekly_refunds
FROM metric.returns r
GROUP BY 1
ORDER BY 1
