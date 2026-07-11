-- Risk's "refund" dashboard only reports fraud and duplicate-order exposure.
SELECT
    DATE_TRUNC('week', r.return_date) AS week_start,
    SUM(r.refund_amount) AS weekly_refunds
FROM metric.returns r
WHERE r.return_reason_code IN ('FRAUD_CHARGEBACK', 'DUPLICATE_ORDER')
GROUP BY 1
ORDER BY 1
