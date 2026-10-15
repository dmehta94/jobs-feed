"""Text cleanup and the coarse relevance filters.

The filters here are deliberately loose: they only drop postings that are clearly
off-target (wrong field, executive level, on-site somewhere far away). Judging fit
is left to the weekly digest, which reads the surviving postings in full.
"""
import html
import re

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
    r"teacher|tutor|paraprofessional|nurse|physician|attorney|counsel|"
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


def title_is_relevant(title):
    if not title:
        return False
    return bool(TITLE_INCLUDE.search(title)) and not TITLE_EXCLUDE.search(title)


# ---------- location ----------

LOCATION_OK = re.compile(
    r"(new york|\bnyc\b|\bny\b|brooklyn|queens|manhattan|bronx|staten island|long island city|"
    r"remote|anywhere|virtual|distributed|work from home|telework|"
    r"united states|\busa?\b|u\.s\.|nationwide|multiple locations|various)",
    re.I,
)


def location_is_ok(location, remote=None):
    """Keep NYC, remote, US-wide, and unspecified locations; drop on-site elsewhere."""
    if remote:
        return True
    if not location or not location.strip():
        return True
    return bool(LOCATION_OK.search(location))


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


_PHD = re.compile(r"(ph\.?\s?d|doctorate|doctoral degree)[^.]{0,60}(required|must have)|"
                  r"(required|must have)[^.]{0,40}(ph\.?\s?d|doctorate|doctoral degree)", re.I)


def phd_required(text):
    if not text:
        return False
    for match in _PHD.finditer(text):
        context = text[max(0, match.start() - 40):match.end() + 40].lower()
        if not re.search(r"not required|preferred|or equivalent|master'?s|helpful", context):
            return True
    return False
