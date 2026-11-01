Real prices of next-day QQQ contracts near the close (for the overnight 1DTE study).
One file per trading day: <date>.json = contracts expiring the NEXT trading day, Webull M5 bars around 12:40-12:55 PT.
Filled by a weekday scheduled task (~1:17 PM PT). Next-morning exits come from the logger's same-day quotes (data/<next>/am).
Read-only data pull; no orders.
