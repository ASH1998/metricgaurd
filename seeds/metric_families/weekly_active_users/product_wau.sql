-- Product's WAU: signed-in users only, heartbeats excluded,
-- weeks bucketed in America/New_York.
SELECT
    DATE_TRUNC('week', e.event_at AT TIME ZONE 'America/New_York') AS week_start,
    COUNT(DISTINCT e.user_id) AS weekly_active_users
FROM events e
WHERE e.is_anonymous = FALSE
  AND e.event_type <> 'heartbeat'
GROUP BY 1
ORDER BY 1
