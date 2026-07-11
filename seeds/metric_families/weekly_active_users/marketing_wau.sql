-- Marketing's WAU: counts everyone who fired any tracked event, anonymous included.
-- Weeks bucket in UTC (no explicit conversion).
SELECT
    DATE_TRUNC('week', e.event_at) AS week_start,
    COUNT(DISTINCT e.user_id) AS weekly_active_users
FROM events e
WHERE e.event_type IN ('page_view', 'click', 'session_start')
GROUP BY 1
ORDER BY 1
