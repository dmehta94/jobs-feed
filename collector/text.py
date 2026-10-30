"""Text cleanup and the coarse relevance filters.

The filters here are deliberately loose: they only drop postings that are clearly
off-target (wrong field, executive level, on-site somewhere far away). Judging fit
is left to the weekly digest, which reads the surviving postings in full.
"""
import html
import re
from datetime import date, datetime

# ---------- cleanup ----------

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(raw):
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)  # some boards double-escape
    return _WS.sub(" ", text).strip()


_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_US_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_NAMED_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")


def iso_date(value):
    """A posting date as YYYY-MM-DD. Boards report dates as ISO timestamps, US-style M/D/YYYY
    (Cornerstone, used by Mathematica), or month names; anything unreadable becomes None."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if m := _ISO_DATE.match(text):
            return date(*map(int, m.groups())).isoformat()
        if m := _US_DATE.match(text):
            month, day, year = map(int, m.groups())
            return date(year, month, day).isoformat()
    except ValueError:
        return None
    for fmt in _NAMED_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------- relevance ----------

TITLE_INCLUDE = re.compile(
    r"\b(data|research|researcher|analyst|analytics|analysis|statistic\w*|statistician|"
    r"evaluat\w*|efficacy|learning scien\w*|psychometric\w*|assessment|measurement|"
    r"insights?|quantitative|econometric\w*|scientist|curriculum|instructional design\w*|education|"
    r"policy|impact|evidence|metrics|reporting|survey|learning engineer\w*|institutional effectiveness|accountability)\b",
    re.I,
)

TITLE_EXCLUDE = re.compile(
    r"\b(director|vice president|vp|svp|evp|chief|head of|principal|staff engineer|"
    r"sales|account executive|account manager|recruiter|business development|"
    r"nurse|physician|attorney|counsel|"
    r"engineering manager|software engineer|devops|site reliability|"
    r"security engineer|it support|help ?desk)\b",
    re.I,
)

NYC_GOV_TITLES = re.compile(
    r"\b(city research scientist|research assistant|research projects coor|"
    r"statistician|staff analyst|associate staff analyst|administrative staff analyst|"
    r"city planner|computer systems manager|computer specialist|"
    r"data|research|analyst|analytics|evaluat\w*|policy)\b",
    re.I,
)


# Teaching jobs are out, but data and research roles about teaching ("Data Scientist, Teacher Coaching") are in.
TEACHING = re.compile(r"\b(teachers?|tutors?|paraprofessional)\b", re.I)
DATA_WORK = re.compile(r"\b(data|research\w*|analyst|analytics|scientist|statistic\w*|evaluat\w*|psychometric\w*|"
                       r"curriculum|assessment|measurement|quantitative)\b", re.I)


def title_is_relevant(title):
    if not title:
        return False
    if TEACHING.search(title) and not DATA_WORK.search(title):
        return False
    return bool(TITLE_INCLUDE.search(title)) and not TITLE_EXCLUDE.search(title)


# ---------- location ----------

# "New York" alone is often the state ("Buffalo, NY", "Albany, New York"), so a place counts as NYC only
# when it names the city, a borough, a nearby city, or a Nassau County town, or starts with "New York".
NYC_AREA = re.compile(
    r"(new york,?\s*(?:ny|new york|city)\b|new york city|^\W*new york\b|\bnyc\b|brooklyn|queens|manhattan|\bbronx|"
    r"staten island|long island city|hoboken|jersey city|"
    # Nassau County, Long Island
    r"\b(?:nassau|garden city|mineola|hempstead|uniondale|hicksville|levittown|great neck|manhasset|port washington|"
    r"glen cove|long beach|freeport|valley stream|rockville centre|oceanside|westbury|old westbury|elmont|"
    r"floral park|new hyde park|lake success|lynbrook|massapequa|bethpage|syosset|jericho|plainview|"
    r"oyster bay|roslyn|east meadow|baldwin|merrick|bellmore|wantagh|seaford|carle place|franklin square|"
    r"woodmere|cedarhurst|lawrence|hewlett|inwood|bellerose|albertson|williston park|sea cliff|"
    r"locust valley|glen head|woodbury|east williston|island park|point lookout|atlantic beach)\b)", re.I)
ANYWHERE = re.compile(r"(remote|anywhere|virtual|distributed|work from home|telework|nationwide|multiple locations|various)", re.I)
LOCATION_OK = re.compile(NYC_AREA.pattern + "|" + ANYWHERE.pattern + r"|united states|\busa?\b|u\.s\.", re.I)
# A trailing country ("Nashville, TN, United States") says nothing about where in the US the job is.
_COUNTRY = re.compile(r"[,\s-]*\b(united states(?: of america)?|usa?|u\.s\.a?\.?)\s*$", re.I)
_SPLIT = re.compile(r"\s*(?:;|\||/|\bor\b|\band\b|\n)\s*", re.I)


def _place_ok(place):
    place = place.strip(" ,-()")
    if not place:
        return False
    rest = _COUNTRY.sub("", place).strip(" ,-()")
    if not rest:
        return True  # just "United States": a US-wide posting
    return bool(NYC_AREA.search(rest) or ANYWHERE.search(rest))


NON_US = re.compile(
    r"\b(india|ireland|canada|united kingdom|\buk\b|england|london|germany|france|spain|netherlands|"
    r"poland|portugal|italy|switzerland|sweden|singapore|japan|china|australia|new zealand|brazil|"
    r"mexico|argentina|colombia|philippines|kenya|nigeria|south africa|israel|emea|apac|latam|europe|asia|"
    r"africa|toronto|vancouver|montreal|dublin|berlin|paris|bangalore|bengaluru|new delhi|delhi|mumbai)\b", re.I)
US_MARKER = re.compile(r"(?i:united states|\busa?\b|u\.s\.|new york|\bnyc\b|\bny\b|nationwide|americas|north america)|"
                       r"\b[A-Z][a-z]+,\s*[A-Z]{2}\b")


def location_is_ok(location, remote=None):
    """Keep NYC, remote within the US, US-wide, and unspecified locations; drop on-site elsewhere
    and remote roles restricted to other countries."""
    loc = (location or "").strip()
    if loc and NON_US.search(loc) and not US_MARKER.search(loc):
        return False
    if remote or not loc:
        return True
    return any(_place_ok(p) for p in _SPLIT.split(loc))


# Remote eligibility stated only in the description (e.g. "Cambridge, MA" with "can be performed fully remotely").
_STRONG_REMOTE = re.compile(
    r"(fully[- ]remote|100% remote|entirely remote|remote[- ]first|remote[- ]based|"
    r"(?:performed|done|worked) (?:fully |entirely |100% )?remotely|"
    r"(?:is|as) a remote (?:position|role|job|opportunity)|this (?:position|role|job) is (?:fully )?remote|"
    r"remote (?:within|anywhere in|from anywhere in) the (?:u\.?s\.?|united states)|"
    r"work from anywhere|location:?\s*remote|remote,? (?:us|u\.s\.|usa|united states)\b|"
    r"open to (?:fully )?remote)", re.I)
_WEAK_REMOTE = re.compile(r"(remote[- ](?:eligible|friendly|option)|remote work (?:is )?(?:available|permitted|allowed)|telework|telecommut\w*|work(?:ing)? remotely|"
                          r"work from home|remote work)", re.I)
_HYBRID = re.compile(r"(hybrid|\b(?:one|two|three|four|\d) days? (?:a|per|each) week|in[- ]office (?:days|presence)|"
                     r"on[- ]site (?:days|presence)|in[- ]person (?:days|at least))", re.I)
_NOT_REMOTE = re.compile(r"(not (?:a )?remote|not (?:be )?eligible for (?:remote|telework|telecommut)|no remote|"
                         r"remote work is not|(?:on[- ]site|in[- ]person) (?:only|required|position)|"
                         r"(?:live|reside|located) (?:with)?in (?:a )?(?:reasonable )?commut\w* distance|must (?:live|reside) in the [\w .]+ (?:area|region|metro\w*)|"
                         r"not (?:available|offered|open) (?:for|to) remote)", re.I)


def remote_from_text(text, strict=False):
    """True when a description says the job can be done fully remotely; False otherwise.

    Clear statements ("fully remote", "can be performed remotely") count unless the posting also
    says it isn't remote. With strict=True (used for postings listed outside the NYC area), only clear
    statements count. Otherwise softer ones ("remote eligible", "telework") count only when there's no
    sign of a hybrid schedule, since "two days a week remote" isn't a remote job."""
    text = text or ""
    if _NOT_REMOTE.search(text):
        return False
    if _STRONG_REMOTE.search(text):
        return True
    if strict:  # softer wording is often benefits boilerplate ("we support remote work")
        return False
    return bool(_WEAK_REMOTE.search(text)) and not _HYBRID.search(text)


# ---------- details pulled from descriptions ----------

_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{2,3}(?:\.\d)?\s?[kK])"
_RANGE = re.compile(_MONEY + r"\s*(?:-|–|—|to)\s*" + r"\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{2,3}(?:\.\d)?\s?[kK])")
_SINGLE = re.compile(_MONEY)


def _to_number(token):
    token = token.replace(",", "").replace(" ", "")
    if token[-1] in "kK":
        return float(token[:-1]) * 1000
    return float(token)


def extract_salary(text):
    """Return (min, max, snippet) for an annual salary found in text, else (None, None, None).
    Hourly-looking amounts (under $1,000) are ignored."""
    if not text:
        return None, None, None
    for match in _RANGE.finditer(text):
        lo, hi = _to_number(match.group(1)), _to_number(match.group(2))
        if lo >= 1000 and hi >= lo:
            start = max(0, match.start() - 60)
            return lo, hi, text[start:match.end() + 20].strip()
    for match in _SINGLE.finditer(text):
        val = _to_number(match.group(1))
        if val >= 20000:
            start = max(0, match.start() - 60)
            return val, val, text[start:match.end() + 20].strip()
    return None, None, None


_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(?:\d{1,2}\s*)?\+?\s*years?", re.I)


def years_required(text):
    """Smallest 'N years' figure mentioned near the word 'experience', or None."""
    if not text:
        return None
    found = []
    for match in _YEARS.finditer(text):
        window = text[match.end():match.end() + 80].lower()
        if "experience" in window:
            n = int(match.group(1))
            if 0 < n <= 20:
                found.append(n)
    return min(found) if found else None


_DOCTORATE = re.compile(r"\b(ph\.?\s?d|doctorate|doctoral degree|ed\.?\s?d)\b", re.I)
_MASTERS = re.compile(r"\b(master'?s|masters|m\.?a\.?|m\.?s\.?|mpp|mpa|mph|msw|m\.?ed|graduate degree|advanced degree)\b", re.I)
_REQUIRED = re.compile(r"\b(required|requires?|must (have|hold|possess)|minimum|mandatory|necessary)\b", re.I)
# Signals that the advanced degree is optional: preferred, or a bachelor's path also qualifies.
_OPTIONAL = re.compile(r"\b(prefer\w*|a plus|plus|desir\w*|ideal\w*|helpful|bonus|not required|"
                       r"or equivalent|equivalent (experience|combination)|bachelor'?s?|baccalaureate|b\.?a\.?|b\.?s\.?|"
                       r"undergraduate|in lieu)\b", re.I)
# Split into clauses at sentence ends, semicolons, line breaks, bullets, and before numbered items,
# but not right after a list number like "1." (that period belongs to the item that follows).
_SEGMENTS = re.compile(r"(?<![0-9][.])(?<=[.;!?])\s+|\n+|\s+(?=\d\.\s)|\s[•·]\s")


def advanced_degree_required(text):
    """'PhD' or "Master's" when the text requires that degree with no bachelor's path, else None.

    Works clause by clause. A clause counts when it names an advanced degree, is marked required
    (in the clause itself or by a short header just before it, like "Minimum qualifications:"),
    isn't marked optional, and isn't followed by an "or ... bachelor's" alternative.
    """
    if not text:
        return None
    segs = [seg for seg in _SEGMENTS.split(text) if seg and seg.strip()]
    found = None
    for i, seg in enumerate(segs):
        is_phd = bool(_DOCTORATE.search(seg))
        if not (is_phd or _MASTERS.search(seg)) or _OPTIONAL.search(seg):
            continue
        following = " ".join(segs[i + 1:i + 3])
        if re.match(r"\s*or\b", following, re.I) and _OPTIONAL.search(following):
            continue
        prev = segs[i - 1] if i else ""
        if _REQUIRED.search(seg) or (_REQUIRED.search(prev) and len(prev) < 80):
            if is_phd:
                return "PhD"
            found = "Master's"
    return found


def phd_required(text):
    return advanced_degree_required(text) == "PhD"
