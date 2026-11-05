# Fixtures

`synthetic_*.json` are hand-written in the shape we expect from SERPHouse. They are **not**
real responses. Save real captured responses here as `serphouse_<slug>.json` (strip the API
key) and add tests against them; then narrow the key lists in `tracker/checks/serphouse.py`.
