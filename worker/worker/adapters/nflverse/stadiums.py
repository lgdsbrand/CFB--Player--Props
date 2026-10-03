"""NFL stadiums: coordinates and roof, which nflverse does not carry.

The schedule names a stadium per game (`stadium_id`, `stadium`, `roof`) but no
latitude or longitude, and the weather job needs both. Thirty-odd stadiums
change rarely, so they live here rather than behind another provider.

WHY OUR OWN ROOF FLAG AND NOT nflverse's `roof`. Measured on the 2026 schedule:
the open-air MCG, Allianz Arena and Stade de France are labelled "dome", and
future games at retractable-roof stadiums are mostly blank because the roof is
decided on the day. `is_dome` here means a FIXED roof only: a retractable roof
is open often enough that its forecast is worth showing.

WHY NAMES CAN OVERRIDE IDS. The same schedule carries a London game at
Tottenham under JAX00, Jacksonville's id. A coordinate lookup on the id alone
would forecast Florida for a game in London, so a stadium NAME that belongs to
a different entry here wins over the id.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stadium:
    stadium_id: str
    name: str
    city: str
    state: str | None
    country_code: str
    latitude: float
    longitude: float
    is_dome: bool
    aliases: tuple[str, ...] = ()


STADIUMS: tuple[Stadium, ...] = (
    Stadium("ATL97", "Mercedes-Benz Stadium", "Atlanta", "GA", "US", 33.7554, -84.4008, False),
    Stadium("BAL00", "M&T Bank Stadium", "Baltimore", "MD", "US", 39.2780, -76.6227, False),
    Stadium("BOS00", "Gillette Stadium", "Foxborough", "MA", "US", 42.0909, -71.2643, False),
    Stadium("BUF00", "Highmark Stadium", "Orchard Park", "NY", "US", 42.7738, -78.7870, False),
    Stadium("CAR00", "Bank of America Stadium", "Charlotte", "NC", "US", 35.2258, -80.8528, False),
    Stadium("CHI98", "Soldier Field", "Chicago", "IL", "US", 41.8623, -87.6167, False),
    Stadium("CIN00", "Paycor Stadium", "Cincinnati", "OH", "US", 39.0954, -84.5160, False),
    Stadium("CLE00", "Huntington Bank Field", "Cleveland", "OH", "US", 41.5061, -81.6995, False),
    Stadium("DAL00", "AT&T Stadium", "Arlington", "TX", "US", 32.7473, -97.0945, False),
    Stadium("DEN00", "Empower Field at Mile High", "Denver", "CO", "US", 39.7439, -105.0201, False),
    Stadium("DET00", "Ford Field", "Detroit", "MI", "US", 42.3400, -83.0456, True),
    Stadium("GNB00", "Lambeau Field", "Green Bay", "WI", "US", 44.5013, -88.0622, False),
    Stadium("HOU00", "NRG Stadium", "Houston", "TX", "US", 29.6847, -95.4107, False,
            aliases=("Reliant Stadium",)),
    Stadium("IND00", "Lucas Oil Stadium", "Indianapolis", "IN", "US", 39.7601, -86.1639, False),
    Stadium("JAX00", "EverBank Stadium", "Jacksonville", "FL", "US", 30.3239, -81.6373, False),
    Stadium("KAN00", "GEHA Field at Arrowhead Stadium", "Kansas City", "MO", "US",
            39.0489, -94.4839, False),
    Stadium("LAX01", "SoFi Stadium", "Inglewood", "CA", "US", 33.9535, -118.3392, True),
    Stadium("LON00", "Wembley Stadium", "London", None, "GB", 51.5560, -0.2796, False),
    Stadium("LON02", "Tottenham Hotspur Stadium", "London", None, "GB", 51.6043, -0.0664, False),
    Stadium("MAD01", "Bernabeu", "Madrid", None, "ES", 40.4531, -3.6883, False,
            aliases=("Santiago Bernabeu", "Estadio Santiago Bernabeu")),
    Stadium("MEL00", "Melbourne Cricket Ground", "Melbourne", None, "AU", -37.8200, 144.9834,
            False),
    Stadium("MEX00", "Estadio Banorte", "Mexico City", None, "MX", 19.3029, -99.1505, False,
            aliases=("Estadio Azteca",)),
    Stadium("MIA00", "Hard Rock Stadium", "Miami Gardens", "FL", "US", 25.9580, -80.2389, False),
    Stadium("MIN01", "U.S. Bank Stadium", "Minneapolis", "MN", "US", 44.9737, -93.2581, True),
    Stadium("MUN01", "FC Bayern Munich Stadium", "Munich", None, "DE", 48.2188, 11.6247, False,
            aliases=("Allianz Arena",)),
    Stadium("NAS00", "Nissan Stadium", "Nashville", "TN", "US", 36.1665, -86.7713, False),
    Stadium("NOR00", "Caesars Superdome", "New Orleans", "LA", "US", 29.9511, -90.0812, True),
    Stadium("NYC01", "MetLife Stadium", "East Rutherford", "NJ", "US", 40.8135, -74.0745, False),
    Stadium("PAR00", "Stade de France", "Saint-Denis", None, "FR", 48.9245, 2.3602, False),
    Stadium("PHI00", "Lincoln Financial Field", "Philadelphia", "PA", "US", 39.9008, -75.1675,
            False),
    Stadium("PHO00", "State Farm Stadium", "Glendale", "AZ", "US", 33.5276, -112.2626, False),
    Stadium("PIT00", "Acrisure Stadium", "Pittsburgh", "PA", "US", 40.4468, -80.0158, False),
    Stadium("RIO00", "Maracana Stadium", "Rio de Janeiro", None, "BR", -22.9122, -43.2302, False),
    Stadium("SEA00", "Lumen Field", "Seattle", "WA", "US", 47.5952, -122.3316, False),
    Stadium("SFO01", "Levi's Stadium", "Santa Clara", "CA", "US", 37.4030, -121.9700, False),
    Stadium("TAM00", "Raymond James Stadium", "Tampa", "FL", "US", 27.9759, -82.5033, False),
    Stadium("VEG00", "Allegiant Stadium", "Las Vegas", "NV", "US", 36.0909, -115.1833, True),
    Stadium("WAS00", "Northwest Stadium", "Landover", "MD", "US", 38.9078, -76.8645, False),
)

_BY_ID = {s.stadium_id: s for s in STADIUMS}
_BY_NAME = {
    n.casefold(): s for s in STADIUMS for n in (s.name, *s.aliases)
}


def resolve_stadium(stadium_id: str | None, stadium_name: str | None) -> Stadium | None:
    """The stadium a schedule row is played at, or None if we do not know it.

    The NAME wins when it belongs to a different entry than the id: the schedule
    files a Tottenham game under JAX00, and the id would put it in Florida.
    """
    by_name = _BY_NAME.get(stadium_name.strip().casefold()) if stadium_name else None
    by_id = _BY_ID.get(stadium_id.strip()) if stadium_id else None
    if by_name is not None and by_name is not by_id:
        return by_name
    return by_id or by_name
