"""Open-Meteo weather forecasts.

CLAUDE.md §4 names Open-Meteo as the fallback for outdoor venues where CFBD
carries no weather. It is the fallback for a specific and unavoidable reason:
**CFBD's `/games/weather` serves OBSERVED conditions, so it returns nothing at
all for a game that has not been played.** Measured 2026-08-13 with the response
cache bypassed: 2026 week 1 and week 2 both returned 0 rows, while 2025 week 7
returned 55. A board that is meant to be useful early in the week therefore
cannot get its weather from CFBD, by construction.

Nothing here is sport-specific — it is a latitude, a longitude and an hour — so
this sits beside `odds` and `ai` rather than inside `cfbd` (CLAUDE.md §3).
"""
