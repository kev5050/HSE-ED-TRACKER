# Where the site's data comes from

Checked 22 November 2026. The first age check, made against early mockups before
this site was built, is kept as a record in [hse-check-2026-11-22.md](hse-check-2026-11-22.md).

| Data | Source | Licence / caveat |
|---|---|---|
| ED and Injury Unit list, ages, Eircodes, Injury Unit hours | Each site's page in the [HSE directory](https://www2.hse.ie/services/find-urgent-emergency-care/) (`url` in `data/locations.json`) | Can change without notice |
| Map points | OpenStreetMap | ODbL, credited on the site. Used for ranking by distance only |
| Towns | [CSO Urban Areas 2022](https://data.gov.ie/), Tailte Éireann | CC BY 4.0. Some English names added from OpenStreetMap |
| City suburbs | OpenStreetMap | ODbL |
| Roads | [Tailte Éireann Roads, National 250k](https://data.gov.ie/) | CC BY 4.0. Used offline, only to build the road overrides |
| Eircode areas | [Autoaddress routing key boundaries](https://docs2.autoaddress.com/en/articles/3215874) | Published as open, free and reusable. Not official boundaries |
| Font | Atkinson Hyperlegible Next | OFL, in `site/fonts/OFL.txt` |
| Icons | Lucide | ISC, in `site/icons.js` |

To rebuild the data, see [tools/site-data](../tools/site-data/README.md).

## Rules

- **Child means 15 or younger.** The main answer only uses EDs that see every child
  age. Places that see some children (5+, 10+, 14+) can appear under "Other places",
  always with their age limit.
- **Adult means 16 or older.** Children's hospitals are never shown.
- **No age is preselected**, so nobody gets adult hospitals by accident.
- **Comparisons** need today's 8am report. A hospital that reports 0 every day is
  never compared. A change of fewer than 5 people reads "About usual".
- **Directions** use the hospital's name and Eircode, not our map point.
- **Nothing is chosen until a tap.** Typing lists places with their county, and
  Enter (the phone's Done key) never picks one: the same names repeat around the
  country and the list doesn't hold every village. Near spellings are never
  offered: a real place missing from the list says "No match" rather than suggest a
  similar name that may be far away. Choosing Adult or Child moves to the town box,
  so a parent is three taps from directions.
- **Injury Units** never show "Open now" on a public holiday, or on the weekday
  after one that falls at a weekend.
- **Border counties** (Donegal, Leitrim, Cavan, Monaghan, Louth) say that
  hospitals in Northern Ireland aren't included.

## Paused in the first version

"Use my location" and "A less busy option" are switched off. Both judged
distance in straight lines, and a review on 22 November found they could send
people across the Shannon or to a hospital over an hour further by road. They
come back only when both use road times worked out in advance and kept on the
device, so a location still never leaves it.

## Road overrides

"Nearest" is by straight line, except at 24 towns where that is clearly wrong by
road (for example Kilrush, Kilkee and Doonbeg: Limerick, not Kerry across the
Shannon). `tools/site-data/build_roads.py` sets an override only when all hold:

- the map-closest ED is at least 10 minutes slower in the base speed model;
- the same ED is quickest under all four speed models;
- the map-closest ED is at least 5 minutes slower under every model;
- the town is within 2.5 km of a road in the 250k map.

Ferries are left out because they don't run all night. The minutes are estimates
from assumed speeds (base: motorway 100, national 72, regional 52 km/h), so the
site never shows them. Don't adjust single towns by hand: change the rule and rebuild.

## Known limits

- Outside the 24 towns, straight lines can still pick a slower drive. In the base
  model the worst listed towns are about 16 minutes slower (Avoca, Ardmore,
  Williamstown), where the speed models didn't agree on a fix.
- An Eircode area is a shortcut to its list of towns. A big rural area lists only
  its own towns, which can sit on the far side of it, so typing your own town gives
  a closer answer. The list says so.
- Roads and hospitals in Northern Ireland aren't in the data. Near the border an
  NI hospital can be much nearer.
- Some places aren't in the list: Citywest (not a named place in either source)
  and small villages outside the CSO towns. The list suggests a bigger town nearby
  or the Eircode.
- When a short place name isn't in the list, longer names that start with it come
  first: Ross lists Rosslare, Inch lists Inchigeelagh, Killea lists Killeagh (13
  real names). And a same-name place elsewhere (more than 10 km away) can be
  listed first:
  Adamstown, Wexford before Adamstown, Dublin; Newport, Tipperary before Newport,
  Mayo (44 real names). Every row shows its county and nothing is chosen without a
  tap. `tools/site-data/check_names.py` counts both and fails on any other first
  row that is over 30 minutes slower by road.
- Distances are straight lines. Where two hospitals are nearly level (Dublin 15:
  Crumlin 11.2 km, Temple Street 11.3 km) the nearer may not be quicker by road.
- A few of the recent days behind "usual" are 2pm reports, and "usual" averages
  weekdays with weekends, which run about half as high. So weekdays read "busier"
  more often than a same-weekday comparison would. Fixing it means changing the
  fetch job (`edtracker/store.py`), so it is a separate task.
- CHI at Tallaght: the HSE's hours field lists only Tuesday and Wednesday, left over
  from a September closure. [CHI's own page](https://www.childrenshealthireland.ie/your-hospital-visit/our-locations/tallaght/)
  says 24 hours, 7 days a week, and the site follows CHI. Worth reporting to the HSE.
- Short-notice closures aren't known to the site. Every place links to its HSE page.
- Public holiday hours for Injury Units aren't stored. Six HSE pages publish them,
  but their summer dates are a day early, so the site works the days out itself.
- Monaghan has no HSE map pin to check against, and the Smithfield point is approximate.
- CHI at Connolly urgent care is by appointment, so it isn't listed.
- The new children's hospital will change three of the EDs when it opens.
