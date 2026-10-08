"""Detect place names in song titles.

Sources, in priority order:
  1. A curated list of places that show up in song titles a lot (rivers,
     streets, islands, landmarks, regions, nicknames like "Vegas").
  2. Countries, US states and continents (geonamescache).
  3. World cities with population >= 15k (geonamescache), filtered so that
     common English words ("Time", "Best", "Paradise") don't match.
  4. A pattern for "<Capitalized words> Street/River/Island/..." and
     "Route 66" / "Highway 61" style names.

Each match gets a confidence: high / medium / low. Review medium and low by
hand - "Phoenix" or "Jackson" might be a bird or a person.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

CONF_RANK = {"high": 3, "medium": 2, "low": 1}

# -- Title cleanup -------------------------------------------------------------

_VERSION_WORDS = (
    r"remaster|live|version|mix|edit|mono|stereo|demo|feat\.?|ft\.|with |acoustic|"
    r"instrumental|session|take \d|bonus|deluxe|radio|single|recorded|from |anniversary|"
    r"bootleg|outtake|reprise|unplugged|explicit|clean"
)
_SUFFIX_RE = re.compile(r"\s+-\s+.*$")
_VERSION_PAREN_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:" + _VERSION_WORDS + r")[^\)\]]*[\)\]]", re.I
)


def clean_title(title: str) -> str:
    """Drop " - Remastered 2011", "(Live at Wembley)", "(feat. X)" and the like."""
    t = _SUFFIX_RE.sub("", title or "")
    t = _VERSION_PAREN_RE.sub("", t)
    return t.strip()


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace("’", "'").replace("&", " and ")


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")


def _tokens(s: str) -> list[str]:
    return _TOKEN_RE.findall(_fold(s))


def _key(s: str) -> str:
    return " ".join(_tokens(s))


# -- Curated places ------------------------------------------------------------
# One entry per line: "Name" or "Name|alias|alias". Type comes from the block.

CURATED = {
    "province": """
        Alberta
        British Columbia
        Manitoba
        New Brunswick
        Newfoundland|Newfoundland and Labrador
        Labrador
        Nova Scotia
        Ontario
        Prince Edward Island|PEI
        Quebec|Québec
        Saskatchewan
        Yukon
        Northwest Territories
        Nunavut
    """,
    "river": """
        Mississippi|Mississippi River
        Missouri River
        Ohio River
        Hudson|Hudson River
        Rio Grande
        Colorado River
        Thames|River Thames
        Seine
        Danube|Blue Danube
        Nile
        Amazon River
        Shannon|River Shannon
        Mersey
        Jordan River|River Jordan
        Swanee|Suwannee|Swanee River|Suwannee River
        Shenandoah
        ?Delta|Mississippi Delta
        ?Moon River
        Yukon River
        Tennessee River
        Volga
        Rhine
        Ganges
        Yangtze
        Euphrates
        Tigris
        Brazos
        Red River
        Wabash
        Monongahela
        Chattahoochee
        Hackensack
        Potomac
        Susquehanna
        Allegheny
        Columbia River
        Snake River
        Pecos
        Guadalupe
    """,
    "street": """
        Abbey Road
        Penny Lane
        Baker Street
        Electric Avenue
        Route 66
        Highway 61
        Highway 101
        Broadway
        Bourbon Street
        Beale Street
        Basin Street
        ?Desolation Row
        Bleecker Street
        Positively 4th Street|4th Street|Fourth Street
        Sunset Boulevard|Sunset Blvd|Sunset Strip
        Hollywood Boulevard
        Mulholland Drive|Mulholland
        Lombard Street
        Lexington Avenue
        Park Avenue
        Fifth Avenue|5th Avenue
        Madison Avenue
        Wall Street
        Carnaby Street
        Oxford Street
        Portobello Road
        Kings Road
        Ventura Highway
        Pacific Coast Highway|PCH
        ?Lonesome Highway
        ?Telegraph Road
        ?Thunder Road
        ?Copperhead Road
        Ocean Avenue
        Tobacco Road
        ?Mean Streets
        42nd Street
        Itchycoo Park
        Rodeo Drive
        Michigan Avenue
        Champs-Elysees|Champs Elysees
        Las Ramblas
        Strawberry Fields
        ?Lovers Lane
        ?Electric Ladyland
        Melrose|Melrose Place
        Haight-Ashbury|Haight Ashbury
        Ocean Drive
        ?Main Street
        ?Yellow Brick Road
        ?Easy Street
        ?Shakedown Street
        ?Sesame Street
        ?Dead End Street
        Blue Ridge Parkway
        Dixie Highway
        ?Lonely Street
        Mohawk Trail
        Oregon Trail
        Santa Fe Trail
        Chisholm Trail
        Appian Way
    """,
    "island": """
        Hawaii|Hawai'i
        Maui
        Oahu
        Kauai
        Waikiki
        Jamaica
        Bermuda
        Bahamas
        Barbados
        Trinidad
        Tobago
        Aruba
        Martinique
        Cuba
        Puerto Rico
        Key West
        Key Largo
        Kokomo
        Capri
        Ibiza
        Mallorca|Majorca
        Mykonos
        Santorini
        Crete
        Corsica
        Sardinia
        Sicily
        Bali
        Tahiti
        Fiji
        Samoa
        Galapagos
        Manhattan
        Staten Island
        Long Island
        Coney Island
        Rhode Island
        Ellis Island
        Alcatraz
        Nantucket
        Martha's Vineyard
        Catalina|Santa Catalina
        Galveston
        Iona
        Skye|Isle of Skye
        Isle of Man
        Isle of Wight
        Anglesey
        Greenland
        Iceland
        Tasmania
        Zanzibar
        Madagascar
        Easter Island
        Bora Bora
        ?Shangri-La|Shangri La
        ?Atlantis
        ?Avalon
        ?Treasure Island
        ?Gilligan's Island
        ?Margaritaville
        ?Fantasy Island
        Hokkaido
        Okinawa
        Guam
    """,
    "landmark": """
        Graceland
        Golden Gate|Golden Gate Bridge
        Brooklyn Bridge
        London Bridge
        Tower Bridge
        Waterloo
        Piccadilly|Piccadilly Circus
        Trafalgar Square
        Times Square
        Central Park
        Madison Square Garden
        Empire State|Empire State Building
        Statue of Liberty
        Chelsea Hotel|Hotel Chelsea
        Hotel California
        Grand Canyon
        Niagara|Niagara Falls
        Yosemite
        Yellowstone
        Mount Rushmore
        Alamo
        Stonehenge
        Eiffel Tower
        Notre Dame
        Taj Mahal
        Kremlin
        Red Square
        Great Wall
        Machu Picchu
        Kilimanjaro
        Everest|Mount Everest
        Fuji|Mount Fuji
        Matterhorn
        ?Mount Olympus|Olympus
        Vesuvius
        Pompeii
        ?Babylon
        Jericho
        Bethlehem
        Galilee
        ?Calvary
        Gethsemane
        ?Zion|Mount Zion
        Sinai|Mount Sinai
        Ararat
        Gibraltar
        Checkpoint Charlie
        Berlin Wall
        Hoover Dam
        Pentagon
        White House
        Capitol Hill
        Wrigley Field
        Fenway|Fenway Park
        Yankee Stadium
        Wembley
        Woodstock
        Altamont
        Monterey
        Studio 54
        Abbey Road Studios
        Cavern Club
        ?Apollo Theater|Apollo
        Ryman|Ryman Auditorium
        Grand Ole Opry|Opry
        Copacabana
        Ipanema
        Malibu
        Big Sur
        Death Valley
        Monument Valley
        Joshua Tree
        Lake Tahoe|Tahoe
        Lake Michigan
        Lake Erie
        Lake Superior
        Lake Geneva
        Loch Lomond
        Loch Ness
        Mount Shasta
        Pikes Peak
        Rocky Mountains|Rockies|Rocky Mountain
        Blue Ridge|Blue Ridge Mountains
        Smoky Mountains|Great Smoky Mountains|Smokies
        Appalachia|Appalachian
        ?Sierra Nevada|Sierra
        Andes
        Alps
        Himalaya|Himalayas
        Sahara
        Mojave
        Kalahari
        Serengeti
        ?Outback
        ?Badlands
        Everglades
        ?Bayou
        Cape Cod
        Cape Fear
        Cape Horn
        Cape of Good Hope
        Plymouth Rock
        Gettysburg
        Normandy
        Dunkirk
        Verdun
        Flanders
        Gallipoli
        Iwo Jima
        Pearl Harbor
        Hiroshima
        Nagasaki
        Chernobyl
        Roswell
        Area 51
        Walden|Walden Pond
        ?Sherwood Forest|Sherwood
        ?Narnia
        ?Neverland
        ?Xanadu
        ?El Dorado|Eldorado
        ?Mordor
        ?Camelot
        ?Valhalla
        ?Eden|Garden of Eden
    """,
    "region": """
        Dixie|Dixieland
        Dakota|Dakotas
        Kintyre|Mull of Kintyre
        Grantchester
        Avalon Peninsula
        Appalachia
        New England
        Midwest
        Deep South
        ?Southland
        Wild West
        Old West
        West Coast
        East Coast
        Gulf Coast
        Bay Area
        Pacific Northwest
        Great Plains
        Panhandle
        Ozarks|Ozark
        Klondike
        Riviera|French Riviera
        Costa del Sol
        Cote d'Azur
        Provence
        Tuscany
        Bavaria
        Transylvania
        Siberia
        Patagonia
        Lapland
        Scandinavia
        Bohemia
        Andalusia|Andalucia
        Catalonia
        Galicia
        ?Highlands|Scottish Highlands
        Yorkshire
        Cornwall
        Devon
        Somerset
        Lancashire
        Merseyside
        Ulster
        Connemara
        ?Kerry
        Donegal
        Galway|Galway Bay
        Tipperary
        Shropshire
        ?Middle Earth
        Mesopotamia
        Arabia
        Persia
        Siam
        Indochina
        Caribbean
        Polynesia
        Mediterranean
        ?Atlantic|Atlantic Ocean
        ?Pacific|Pacific Ocean
        ?Arctic
        Antarctica
        North Pole
        South Pole
        ?Equator
        Bermuda Triangle
        Big Apple
        Motor City
        Windy City
        ?Emerald City
        Tinseltown
        ?Gotham
        Silicon Valley
        Napa|Napa Valley
        San Fernando Valley
        ?The Valley
        Hill Country
        Low Country|Lowcountry
        ?Upstate
        Rust Belt
        Bible Belt
        Sun Belt
        ?Heartland
        ?Badlands
        Queensland
        New South Wales
        ?Victoria
        Siberia
        Scotland
        England
        Wales
        Britain|Great Britain
        ?Ireland|Eire|Erin
        Northern Ireland
        Holland
        America|USA|U.S.A.|United States|United States of America
        Korea
        Vietnam|Viet Nam
        Russia
        Congo
        Burma
        Ceylon
        Rhodesia
        Prussia
        Yugoslavia
        Czechoslovakia
        Soviet Union|USSR|U.S.S.R.
        Bosnia
        Kosovo
        Tibet
        Kashmir
        Punjab
        Bengal
        ?Hollywood
        Harlem
        Brooklyn
        Bronx|The Bronx
        ?Queens
        Soho
        Greenwich Village
        ?Chelsea
        Tribeca
        Bowery
        Hell's Kitchen
        Chinatown
        Little Italy
        Compton
        Watts
        Inglewood
        Venice Beach
        Laurel Canyon
        Topanga
        Haight
        Notting Hill
        Camden Town|Camden
        Brixton
        Shoreditch
        Kensington
        Hackney
        Peckham
        Montmartre
        Pigalle
        Kreuzberg
        Harajuku
        Shibuya
        ?Southside|South Side
        ?East Side|Eastside
        ?West Side|Westside
        Upper East Side
        Lower East Side
        ?Uptown
        ?Downtown
        ?Midtown
        French Quarter
        Storyville
        Music Row
        Bel Air|Bel-Air
        Beverly Hills
        Laurel Canyon
        Muscle Shoals
    """,
    "city": """
        New York|New York City|NYC|N.Y.C.
        Los Angeles|L.A.
        San Francisco|Frisco
        New Orleans|NOLA|N'awlins
        Las Vegas|Vegas
        Philadelphia|Philly
        Saint Louis|St Louis|St. Louis
        Saint Paul|St Paul
        Kansas City
        Oklahoma City
        Salt Lake City
        Atlantic City
        Mexico City
        Panama City
        Chi-Town|Chi-town
        Washington D.C.|Washington DC|D.C.
        Rio|Rio de Janeiro
        Buenos Aires
        Hong Kong
        Saigon
        Bombay
        Calcutta
        Peking
        Constantinople
        Byzantium
        Leningrad
        Stalingrad
        Mandalay
        Timbuktu
        Casablanca
        Marrakesh|Marrakech
        Kathmandu
        Katmandu
        Abilene
        Amarillo
        Tulsa
        Wichita
        Muskogee
        Okolona
        Tupelo
        Memphis
        Nashville
        Chattanooga
        Knoxville
        Birmingham
        Montgomery
        ?Mobile
        ?Jackson
        Biloxi
        Natchez
        Vicksburg
        Galveston
        El Paso
        San Antonio
        Houston
        Dallas
        ?Austin
        Laredo
        Albuquerque
        Santa Fe
        ?Phoenix
        Tucson
        Winslow
        Flagstaff
        Bakersfield
        Fresno
        Sacramento
        Oakland
        San Diego
        San Jose
        Pasadena
        Santa Monica
        Santa Barbara
        Monterey
        ?Reno
        Tahoe
        ?Portland
        Seattle
        Boise
        Cheyenne
        Laramie
        Denver
        Boulder
        Omaha
        ?Lincoln
        Des Moines
        Detroit
        Chicago
        Cleveland
        Akron
        Toledo
        Cincinnati
        Dayton
        Pittsburgh
        Allentown
        Baltimore
        Boston
        ?Providence
        Hartford
        ?Buffalo
        Rochester
        Syracuse
        Albany
        Poughkeepsie
        ?Jersey|New Jersey
        Asbury Park
        Hoboken
        Atlanta
        ?Savannah
        Macon
        Charleston
        Raleigh
        ?Durham
        Asheville
        ?Richmond
        Norfolk
        Miami
        Tampa
        Orlando
        Jacksonville
        Daytona
        Pensacola
        Tallahassee
        Louisville
        ?Lexington
        Indianapolis
        Milwaukee
        Minneapolis
        Duluth
        Fargo
        Anchorage
        Juneau
        Honolulu
        Toronto
        Montreal
        Vancouver
        Winnipeg
        Calgary
        Edmonton
        Halifax
        London
        Liverpool
        Manchester
        Glasgow
        Edinburgh
        Dublin
        Belfast
        Derry
        Cardiff
        Brighton
        Paris
        Berlin
        Amsterdam
        Rome
        Venice
        ?Florence
        Milan
        Naples
        Madrid
        Barcelona
        Lisbon
        Vienna
        Prague
        Budapest
        Warsaw
        Moscow
        Stockholm
        Oslo
        Copenhagen
        Helsinki
        Athens
        Istanbul
        Jerusalem
        Cairo
        Tokyo
        Kyoto
        Osaka
        Beijing
        Shanghai
        Seoul
        Bangkok
        Manila
        Singapore
        Sydney
        Melbourne
        Perth
        Adelaide
        Brisbane
        Auckland
        Havana
        Acapulco
        Tijuana
        Cancun
        Lima
        Bogota
        Caracas
        Santiago
        ?Kingston
        Nairobi
        Lagos
        Johannesburg
        Cape Town
        Soweto
        Mumbai
        Delhi
        Baghdad
        Kabul
        Tehran
        Beirut
        Damascus
        Dubai
    """,
}

# Single-word geonames city names to ignore no matter the population
# (words, given names, brands that happen to be city names).
DROP = set("""
man nice reading van batman oral pest kennedy commonwealth metro hub saga
toyota shaping santos hassan nigel henderson chandler irving gilbert aurora
regina riverside brent vladimir ya'an ha'il ma'an salvador kobe gaza
alexandria delta surprise paradise orange independence enterprise springs
split tours nancy derby norman sandy victoria-downtown tri-cities
salem charlotte madison phoenix providence richmond hamilton lincoln columbus
newton florence mercedes jackson kingston
""".split())
# Some DROP names (Mobile, Phoenix, Jackson, Kingston...) are also in the
# curated list below, which always wins - so they still match, at the
# confidence the curated list gives them.

# Countries whose names are also everyday English words.
AMBIGUOUS_COUNTRIES = {"turkey", "chad", "jordan", "guinea", "china", "georgia", "niger", "togo"}


def _city_confidence(name_key: str, population: int, zipf: float | None) -> str | None:
    words = name_key.split()
    if name_key in DROP or len(name_key.replace(" ", "")) <= 3:
        return None
    if len(words) > 1:
        return "high" if population >= 50_000 else "medium"
    if zipf is not None and zipf >= 3.6:  # also an everyday word
        if population >= 1_000_000:
            return "high"
        if population >= 250_000:
            return "medium"
        return None
    return "high" if population >= 100_000 else "medium"


def _english_zipf():
    """Optional: use wordfreq (if installed) to spot city names that are words."""
    try:
        from wordfreq import zipf_frequency
        return lambda w: zipf_frequency(w, "en")
    except ImportError:
        return None


@lru_cache(maxsize=1)
def gazetteer() -> dict[str, tuple[str, str, str]]:
    """key -> (display name, place type, confidence)."""
    import geonamescache

    gaz: dict[str, tuple[str, str, str]] = {}

    def put(name: str, ptype: str, conf: str):
        k = _key(name)
        if not k:
            return
        cur = gaz.get(k)
        if cur is None or CONF_RANK[conf] > CONF_RANK[cur[2]]:
            gaz[k] = (name, ptype, conf)

    gc = geonamescache.GeonamesCache(min_city_population=15000)
    zipf = _english_zipf()
    common = _COMMON_WORDS

    for city in gc.get_cities().values():
        name = city["name"]
        k = _key(name)
        z = zipf(name) if zipf else (5.0 if k in common else 0.0)
        conf = _city_confidence(k, city.get("population") or 0, z)
        if conf:
            put(name, "city", conf)

    for country in gc.get_countries().values():
        name = country["name"]
        put(name, "country", "medium" if _key(name) in AMBIGUOUS_COUNTRIES else "high")
    for state in gc.get_us_states().values():
        put(state["name"], "us_state", "high")
    for cont in gc.get_continents().values():
        put(cont["name"], "continent", "high")

    # Curated entries always win. A leading "?" marks an ambiguous or
    # fictional place (Phoenix, Downtown, Neverland) -> medium confidence.
    for ptype, block in CURATED.items():
        for line in block.strip().splitlines():
            line = line.strip()
            conf = "medium" if line.startswith("?") else "high"
            names = [n.strip() for n in line.lstrip("?").split("|") if n.strip()]
            for n in names:
                k = _key(n)
                if k:  # aliases display under the main name
                    gaz[k] = (names[0], ptype, conf)
    return gaz


# Fallback "is this an everyday word" list, used only when wordfreq is not
# installed. Generated from the geonames city names (pop >= 15k) that have an
# English Zipf frequency >= 3.6 - i.e. city names that are also common words
# or first names.
_COMMON_WORDS = set("""
of time most much say best man same young university along nice york police
deal buy london march date goes reading federal david green central washington
union mine george officer normal sale san opportunity bar spring rich gay male
mission pop obama mobile airport wedding chicago paris mary van florida mexico
surprise mid-city martin bay bear forest boston golden holiday liberal split
asia taylor orange un virginia salt republic kim bell independence jackson adam
tank clinton manage sydney manchester wilson carolina hollywood semi jordan
roman toronto kong olympic marks davis howard gap miami elizabeth pa colorado
superior rome temple victoria bath liverpool moscow philadelphia dallas houston
walker sake rugby columbia crystal alliance cambridge gary seattle allen berlin
oxford arizona pen enterprise pace hook anna atlanta wayne melbourne fate
anderson austin chelsea ann singapore detroit commonwealth liberty boom fleet
lincoln hamilton ali lawrence madrid bow summit oregon graham se delhi clay
kennedy edinburgh moore tokyo murray cleveland barry oral alice warren ye brick
brooklyn roy nelson marshall mitchell springs barcelona campbell denver parker
ho ron sue batman evans perry baltimore charlotte orleans imperial douglas
franklin phoenix ontario jerusalem wright stanley glasgow sandy birmingham metro
portland lens wa jam madison tyler kent paradise vancouver manhattan harvey peer
horn tours nancy pearl mason norman kyle buffalo vincent stuart murphy sterling
pittsburgh eagle bend retreat richmond delta beijing brad humble derby holland
hurricane lucas rogers dubai dublin hub shaw harrison newcastle hull queens
linda montana milan savage sunset shanghai harper orlando columbus logan bristol
amsterdam hudson duncan tire palmer brandon roses leeds lebanon bradley
milwaukee geneva arnold bo westminster adelaide brussels palestine mercedes
vienna ottawa nashville bryan brisbane manga mumbai florence cincinnati colombia
munich toyota troy frederick adrian newton barnes perth venezuela leicester
butterfly acre alot venice seoul henderson vladimir mentor berkeley lend marco
honda cardiff athens gaza boo montgomery tampa essex jamaica oakland bake
sheffield richardson carson fountain anthem reservoir dome sebastian griffin
delaware gilbert leslie bury lanka moss monroe memphis hopkins roosevelt terrace
belfast manila ferguson hayes saga burke beverly minneapolis robertson
sacramento mon lynn brighton para bra princeton sherman miranda milton cairo vic
bangkok bryant louisville donna norfolk marina wellington sunrise indianapolis
eden tracy albany lindsay southampton rochester chester aberdeen lyon sim marion
burton providence banning eugene preston lopez tucker springfield johnston
baldwin calgary vernon worms newport lancaster durham soo thomson parole
somerset wyoming kanye cornwall surrey alexandria flint kai charleston hammond
matthews nigel darwin swords gardner sparks leigh bradford norton chandler sofia
kingston canterbury istanbul newark clayton aurora castro windsor carey sutton
shirley stoke arlington dixon paramount auburn salvador shaping nottingham
plantation whitney ashton vera cork rodriguez apex ada irving vista boulder
santos hyde hastings salem stella auckland sur flora petersburg highland sidney
bali plymouth norwich brent plaque martinez ramsey patterson midway jupiter
carlton nikki torres copenhagen jakarta hassan borne riverside holt johannesburg
manly santiago rouge nokia aston warsaw tara trinidad bolton prague hampton kobe
monaco katy woodland canberra edmonton pest baghdad bam lagos alicia lisbon
cocoa morton sunderland naples helena laurel holden mascot hamburg valencia
swansea tehran savannah suffolk clive syracuse carnegie brunswick regina
wimbledon tottenham conway jacksonville omaha bedford lawson mao portsmouth hale
dundee benghazi kendall seymour fargo hercules
""".split())


# -- Pattern matching ----------------------------------------------------------

_GENERIC = (
    r"Street|St\.|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Drive|Highway|"
    r"Freeway|Turnpike|Parkway|Bridge|River|Creek|Island|Islands|Isle|Bay|Beach|"
    r"Harbor|Harbour|Hill|Hills|Heights|Park|Square|Mountain|Mountains|Canyon|"
    r"Valley|Lake|Falls|Station|County|Parish|Plaza|Pier|Coast|Cove|Point|Gulch"
)
_LEADING_STOP = {
    "the", "a", "an", "my", "your", "our", "his", "her", "their", "this", "that",
    "on", "in", "at", "to", "of", "down", "up", "from", "by", "over", "under",
    "across", "back", "and", "or", "for", "into", "off", "out", "lonely", "old",
    "new", "little", "long", "dead", "end", "last", "one", "no", "every",
}
_PATTERN_RE = re.compile(
    r"\b((?:[A-Z0-9][\w'.\-]*\s+){1,3})(" + _GENERIC + r")(?=\s|$|[,;:!?)\]])"
)
_ROUTE_RE = re.compile(
    r"\b(Route|Highway|Hwy|Interstate|I-|Rte\.?)\s?(\d{1,3})\b", re.I
)


def _pattern_matches(title: str) -> list[tuple[str, str, str]]:
    found = []
    for m in _ROUTE_RE.finditer(title):
        found.append((m.group(0), "street", "high"))
    for m in _PATTERN_RE.finditer(title):
        words = m.group(1).split()
        while words and words[0].lower() in _LEADING_STOP:
            words.pop(0)
        if not words:
            continue
        found.append((" ".join(words + [m.group(2)]), "pattern", "low"))
    return found


# -- Public API ----------------------------------------------------------------


def find_places(title: str) -> list[tuple[str, str, str]]:
    """Return [(place, type, confidence), ...] found in a song title."""
    title = clean_title(title)
    gaz = gazetteer()
    toks = _tokens(title)
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    i = 0
    while i < len(toks):
        for n in range(min(6, len(toks) - i), 0, -1):
            k = " ".join(toks[i:i + n])
            if k in gaz:
                name, ptype, conf = gaz[k]
                if name not in seen:
                    seen.add(name)
                    results.append((name, ptype, conf))
                i += n
                break
        else:
            i += 1

    covered = " ".join(_key(r[0]) for r in results)
    for name, ptype, conf in _pattern_matches(title):
        k = _key(name)
        if k and k not in covered and name not in seen:
            seen.add(name)
            results.append((name, ptype, conf))

    results.sort(key=lambda r: -CONF_RANK[r[2]])
    return results


def song_key(title: str, artist_id: str) -> str:
    """Identity of a song for de-duplication: cleaned title + primary artist."""
    return f"{_key(clean_title(title))}|{artist_id}"
