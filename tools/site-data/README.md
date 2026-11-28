# Rebuild the site's data

Run from the repo root, with WORK set to a new empty folder outside the repo.
Nothing here runs in CI. Read the diff before committing: a wrong age can send a
child to a hospital that won't treat them.

```bash
python -I tools/site-data/fetch_sources.py "$WORK"           # network: public sources only
python -X utf8 tools/site-data/build_places.py "$WORK" .     # locations.json, places.json
python -X utf8 tools/site-data/build_roads.py "$WORK" .      # finds road overrides
python -X utf8 tools/site-data/build_overrides.py "$WORK" .  # adds them to places.json
python -X utf8 tools/site-data/check_names.py "$WORK" .   # real place names against road minutes
python checks/site_check.py check-output
```

To add or remove a hospital, change the list in `build_places.py`, its short town
label in `data/locations.json` and `HSE_AGES` in `checks/site_check.py`.
