-- Finance's "WAU": actually counts billable activity rows, not distinct users,
-- and reads from the billing-filtered view instead of raw events.
SELECT
    DATE_TRUNC('week', b.activity_at) AS week_start,
    COUNT(b.user_id) AS weekly_active_users
FROM billable_events b
WHERE b.plan_tier <> 'free'
GROUP BY 1
ORDER BY 1
