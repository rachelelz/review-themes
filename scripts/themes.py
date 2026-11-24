#!/usr/bin/env python3
"""Find recurring complaints in customer reviews.

Reads review CSVs, tags each sentence against a theme dictionary, and writes a
markdown report: which problems come up most, whether each is rising or
falling, and real quotes. Python 3.9 standard library only.

How "Fix this first" is chosen (see score_themes):
    Each review that mentions a theme adds (recency weight x severity weight).
    Recency weight: 2 if the review is from the last --recent-days days, else 1
    (undated reviews get 1). Severity weight: 6 minus the star rating, so 1 star
    is 5 and 5 stars is 1 (no rating counts as 3).
    The theme with the highest total wins. Themes with fewer than 3 reviews
    are not eligible.

What counts as a complaint (see tag_review):
    A sentence is split into clauses at commas, semicolons, "but", "however",
    "although" and "though". Strong keywords (negative_keywords, or any keyword
    in a theme whose polarity is "negative") count on their own. Weak keywords
    only count when the same clause has a negative word: a negative cue
    ("rude", "broken"), negated praise ("not helpful"), or, in reviews rated 2
    or less, a negator ("not", "never", "haven't"). A keyword with a negator
    just before it ("not slow", "zero crashes") is ignored, except in reviews
    rated 2 or less, where the negation is the complaint ("I haven't received
    my refund").

How files are read (see load_reviews):
    The delimiter (comma, semicolon or tab) and the encoding (UTF-8, UTF-16 or
    Windows-1252) are detected per file. Columns are found by name. Ratings are
    read as 1 to 5, unless more than 1 in 10 of a file's ratings are between 5
    and 10: then the file is read as 0 to 10 and halved. A rating outside its
    file's scale counts as missing. Dates with slashes are read as month/day
    and dates with dashes or dots as day/month, unless a date in the same file
    only fits the other order. A date after tomorrow counts as missing.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import html
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOW_RATING = 3.0          # a rating at or below this counts as negative
VERY_LOW_RATING = 2.0     # at or below this, a negation is part of the complaint
MIN_FIX_COUNT = 3         # a theme needs this many reviews to be "fix first"
MIN_TREND_COUNT = 5       # per-period count needed before calling a trend
RISE_FACTOR = 1.5
FALL_FACTOR = 0.5
NEAR_DUPLICATE = 0.95
NEAR_DUPLICATE_MIN_CHARS = 40   # shorter reviews must match exactly
TEN_POINT_SHARE = 0.1     # more than this share of ratings in (5, 10] means a 0 to 10 file
CSV_FIELD_LIMIT = 10 * 1024 * 1024   # the csv module's default of 128 KB is too small
QUOTE_LENGTH = (30, 140)  # quotes of this many characters read best

# Exact header names (after normalizing), best first.
COLUMN_ALIASES = {
    "text": ["text", "review", "review_text", "review_body", "body", "comment",
             "comments", "content", "feedback", "message", "description", "reviews"],
    "rating": ["rating", "stars", "star_rating", "score", "review_rating"],
    "date": ["date", "created_at", "review_date", "created", "submitted_at",
             "timestamp", "time", "at"],
    "source": ["source", "platform", "channel", "store"],
    "reviewer": ["reviewer", "author", "user", "username", "user_name", "name",
                 "reviewer_name", "author_name", "customer", "customer_name", "nickname"],
    "id": ["id", "review_id", "reviewid"],
}

# Words inside longer headers. The last such word names the column, so
# "Review Stars" is a rating and "Source Of Review" is a source.
COLUMN_WORDS = {
    "text": {"text", "body", "comment", "comments", "content", "feedback", "message",
             "description"},
    "rating": {"rating", "ratings", "stars", "star", "score"},
    "date": {"date", "created", "submitted", "submit", "timestamp", "time", "posted",
             "published"},
    "source": {"source", "platform", "channel", "store"},
    "reviewer": {"reviewer", "author", "nickname", "username", "customer"},
    "id": {"id"},
}

# Headers with these words describe something else, like "Developer Reply Text",
# "Review Title" or "Review Last Update Date and Time".
COLUMN_SKIP_WORDS = {"reply", "response", "developer", "title", "update", "updated", "last",
                     "millis", "epoch", "language", "link", "url", "version", "device"}

COLUMN_LABELS = [("text", "text"), ("rating", "rating"), ("date", "date"),
                 ("source", "source"), ("reviewer", "reviewer"), ("id", "ID")]

DELIMITERS = ",;\t"
DELIMITER_NAMES = {",": "commas", ";": "semicolons", "\t": "tabs", "|": "bars"}

NEGATORS = {"not", "no", "never", "without", "hardly", "barely", "nothing",
            "cannot", "neither", "nor", "zero"}

NEGATIVE_CUES = [
    "terrible", "awful", "horrible", "worst", "bad$", "poor", "useless",
    "unacceptable", "ridiculous", "annoying", "frustrat", "disappoint",
    "angry", "upset", "unhappy", "not happy", "not good", "not great",
    "not worth", "hate", "waste", "scam", "rude", "fail", "unable",
    "doesn't work", "does not work", "didn't work", "did not work",
    "not working", "stopped working", "can't recommend", "regret", "stale",
    "overcharg", "too expensive", "too high", "too long", "still waiting",
    "waited", "fed up", "nightmare", "mess$", "broken", "problem", "issue$",
    "issues$", "complain", "worse", "hassle", "hard to", "difficult",
    "impossible", "confus", "wrong", "stuck", "error", "no longer", "slow",
    "late$", "slipped", "gone downhill", "went downhill",
]

POSITIVE_CUES = [
    "great$", "good$", "love", "excellent", "amazing", "fantastic", "perfect",
    "helpful", "fast$", "quick", "easy$", "easier", "smooth", "happy",
    "pleased", "recommend", "awesome", "best$", "friendly", "wonderful",
    "thank", "delicious", "solid$", "reliable", "responsive", "worth$",
    "fair$", "early$", "on time",
]

FILLER = {"a", "an", "the", "any", "or", "nor", "had", "have", "has", "been", "was",
          "is", "are", "were", "too", "so", "really", "very", "ever", "of", "my"}

# Phrases that contain a negator but make the complaint stronger, not weaker:
# "I can't believe how slow the site is", "not only slow but broken".
NOT_A_NEGATION = re.compile(
    r"\b(?:can't|cannot|can not|couldn't|could not) (?:believe|stand|get over)\b"
    r"|\bnot only\b|\bno wonder\b")

DANGLING_START = re.compile(r"(first|second|third|also|and|then|plus|so)\b[ ,]", re.I)

EMAIL_OR_PHONE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{8,}\d")


# ---------------------------------------------------------------- data model

@dataclass
class Review:
    raw: str
    text: str
    rating: Optional[float]
    day: Optional[date]
    source: str
    reviewer: str = ""
    review_id: str = ""
    hits: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    negative: bool = False
    rating_unreadable: bool = False   # a rating cell had text but no number
    rating_out_of_range: bool = False  # a number above the file's scale, like 40
    date_unreadable: bool = False     # a date cell had text that is not a date
    date_in_future: bool = False      # a date after tomorrow, like 2062


@dataclass
class FileInfo:
    name: str
    columns: Dict[str, str]
    delimiter: str
    encoding: str
    notes: List[str]


@dataclass
class Theme:
    name: str
    weak: list
    strong: list


@dataclass
class ThemeStats:
    name: str
    reviews: List[Review]
    sentences: List[Tuple[str, str, Review]]
    count: int = 0
    share: float = 0.0
    avg_rating: Optional[float] = None
    recent: int = 0
    earlier_rate: float = 0.0
    trend: str = ""
    score: float = 0.0
    months: Counter = field(default_factory=Counter)


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))


# ------------------------------------------------------------------- reading

def norm_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower().strip()).strip("_")


def column_field(header: str) -> Optional[str]:
    """The field a longer header names, judged by its last field word."""
    words = norm_header(header).split("_")
    if COLUMN_SKIP_WORDS & set(words):
        return None
    for word in reversed(words):
        for fld, vocab in COLUMN_WORDS.items():
            if word in vocab:
                return fld
    return "text" if "review" in words else None   # "Your Review"


def detect_columns(headers: List[str], overrides: Dict[str, Optional[str]]) -> Dict[str, str]:
    """Map our field names to the CSV's own header names.

    An exact name like "rating" wins, then a header whose words name the field,
    like "Review Stars". Ties go to the column further left.
    """
    cols: Dict[str, str] = {}
    for fld, wanted in overrides.items():
        if not wanted:
            continue
        if wanted not in headers:
            raise ValueError("Column '%s' not found. Columns are: %s" % (wanted, ", ".join(headers)))
        cols[fld] = wanted
    used = set(cols.values())
    for fld, aliases in COLUMN_ALIASES.items():
        if fld in cols:
            continue
        best = None
        for position, header in enumerate(headers):
            if header in used:
                continue
            name = norm_header(header)
            if name in aliases:
                rank = (0, aliases.index(name), position)
            elif column_field(header) == fld:
                rank = (1, 0, position)
            else:
                continue
            if best is None or rank < best[0]:
                best = (rank, header)
        if best:
            cols[fld] = best[1]
            used.add(best[1])
    if "text" not in cols:
        raise ValueError("No review text column found. Columns are: %s. Use --text-col NAME."
                         % ", ".join(headers))
    return cols


def parse_rating(value: str) -> Optional[float]:
    match = re.search(r"\d+(?:[.,]\d+)?", value or "")
    return float(match.group().replace(",", ".")) if match else None


DATE_FORMATS = ["%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d"]
NUMERIC_DATE = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})$")


def strip_time(value: str) -> str:
    return re.sub(r"\s+\d{1,2}:\d{2}.*$", "", value.strip())


def date_order(values: List[str]) -> Optional[bool]:
    """True if a file's numeric dates must be day first, False if month first,
    None if every date fits both (then the separator decides)."""
    first_big = second_big = False
    for value in values:
        match = NUMERIC_DATE.match(strip_time(value))
        if match:
            first_big = first_big or int(match.group(1)) > 12
            second_big = second_big or int(match.group(3)) > 12
    return first_big if first_big != second_big else None


def parse_date(value: str, day_first: Optional[bool] = None) -> Optional[date]:
    """Read a date. Numeric dates with slashes are month first and with dashes
    or dots day first, unless day_first says otherwise."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        pass
    v = strip_time(v)
    match = NUMERIC_DATE.match(v)
    if match:
        a, sep, b, year = int(match.group(1)), match.group(2), int(match.group(3)), int(match.group(4))
        if year < 100:
            year += 2000
        first_is_day = day_first if day_first is not None else sep != "/"
        day, month = (a, b) if first_is_day else (b, a)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def read_text_file(path: Path) -> Tuple[str, str]:
    """Return (text, encoding). Handles UTF-8 with or without a BOM, UTF-16
    (Excel's "Unicode Text") and, as a fallback, Windows-1252."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "UTF-16"
    head = data[:2000]
    if len(head) >= 4 and head.count(0) * 3 >= len(head):   # UTF-16 without a BOM
        codec = "utf-16-le" if head[1::2].count(0) > head[0::2].count(0) else "utf-16-be"
        try:
            return data.decode(codec), "UTF-16"
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "Windows-1252"


def sniff_delimiter(text: str) -> str:
    """Comma, semicolon or tab. The header line has the final say, since
    review text can be full of commas and semicolons."""
    header = text.split("\n", 1)[0]
    try:
        found = csv.Sniffer().sniff(text[:20000], delimiters=DELIMITERS).delimiter
    except csv.Error:
        found = ""
    if found and found in header:
        return found
    best = max(DELIMITERS, key=header.count)
    return best if header.count(best) else ","


def clean_text(raw: str) -> str:
    text = html.unescape(raw)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fit_rating_scale(reviews: List[Review], name: str) -> List[str]:
    """Put one file's ratings on a 5-point scale. Returns notes for the report.

    A file is read as 0 to 10 (and halved) when more than 1 in 10 of its
    ratings are above 5 and at most 10. A rating above the file's scale, like
    a typo of 40, is treated as missing on its own.
    """
    rated = [r for r in reviews if r.rating is not None]
    if not rated:
        return []
    high = sum(1 for r in rated if 5 < r.rating <= 10)
    top = 10.0 if high > TEN_POINT_SHARE * len(rated) else 5.0
    for r in rated:
        if r.rating > top:
            r.rating = None
            r.rating_out_of_range = True
        elif top == 10.0:
            r.rating = r.rating / 2
    if top == 10.0:
        return ["%s has ratings on a 0 to 10 scale, so they were halved to fit a 5-point scale."
                % name]
    return []


def load_reviews(paths, overrides=None, delimiter=None,
                 today: Optional[date] = None) -> Tuple[List[Review], List[FileInfo]]:
    """Read every file. Returns the reviews and what was found in each file."""
    overrides = overrides or {}
    latest = (today or date.today()) + timedelta(days=1)   # one day of slack for time zones
    csv.field_size_limit(max(csv.field_size_limit(), CSV_FIELD_LIMIT))
    reviews: List[Review] = []
    infos: List[FileInfo] = []
    for path in paths:
        path = Path(path)
        text, encoding = read_text_file(path)
        if "\x00" in text:
            raise ValueError("%s contains NUL bytes, so it is not a plain CSV file. If it came "
                             "from Excel, save it again as CSV UTF-8." % path.name)
        hint = re.match(r"sep=(.)\r?\n", text)   # Excel sometimes writes "sep=;" first
        if hint:
            text = text[hint.end():]
        sep = delimiter or (hint.group(1) if hint else sniff_delimiter(text))
        reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=sep)
        try:
            headers = reader.fieldnames
            rows = list(reader)
        except csv.Error as err:
            raise ValueError("%s could not be read as CSV (line %d): %s"
                             % (path.name, reader.line_num, err)) from err
        if not headers:
            raise ValueError("%s is empty" % path.name)
        cols = detect_columns([h for h in headers if h], overrides)

        def cell(row, fld, cols=cols):
            value = row.get(cols[fld]) if fld in cols else ""
            return value.strip() if isinstance(value, str) else ""

        date_cells = [cell(row, "date") for row in rows]
        day_first = date_order(date_cells)
        found: List[Review] = []
        for row, date_cell in zip(rows, date_cells):
            raw = cell(row, "text")
            text_clean = clean_text(raw)
            if not text_clean:
                continue
            rating_cell = cell(row, "rating")
            rating = parse_rating(rating_cell)
            day = parse_date(date_cell, day_first)
            review = Review(raw=raw, text=text_clean, rating=rating, day=day,
                            source=cell(row, "source"), reviewer=cell(row, "reviewer"),
                            review_id=cell(row, "id"),
                            rating_unreadable=bool(rating_cell) and rating is None,
                            date_unreadable=bool(date_cell) and day is None)
            if day and day > latest:
                review.day = None
                review.date_in_future = True
            found.append(review)
        notes = fit_rating_scale(found, path.name)
        infos.append(FileInfo(path.name, cols, sep, encoding, notes))
        reviews += found
    return reviews, infos


def describe_columns(info: FileInfo) -> str:
    """One line naming the columns used, like: text "review", rating "stars"."""
    parts = []
    for fld, label in COLUMN_LABELS:
        if fld in info.columns:
            parts.append('%s "%s"' % (label, info.columns[fld]))
        elif fld in ("rating", "date", "source"):
            parts.append("no %s column" % label)
    how = []
    if info.delimiter != ",":
        how.append("separated by %s" % DELIMITER_NAMES.get(info.delimiter, repr(info.delimiter)))
    if info.encoding != "UTF-8":
        how.append(info.encoding)
    return "Columns in %s%s: %s" % (info.name, " (%s)" % ", ".join(how) if how else "",
                                    ", ".join(parts))


# ------------------------------------------------------------------- cleaning

def normalize_text(text: str) -> str:
    t = text.lower().replace("’", "'")
    return re.sub(r"[^\w']+", " ", t).strip()


def same_value(a: str, b: str) -> bool:
    return not a or not b or a.casefold() == b.casefold()


def could_be_same(a: Review, b: Review) -> bool:
    """Nothing but the text says these rows are different reviews: the dates,
    ratings, reviewer names and IDs match wherever both rows have one."""
    return ((a.day is None or b.day is None or a.day == b.day)
            and (a.rating is None or b.rating is None or a.rating == b.rating)
            and same_value(a.reviewer, b.reviewer)
            and same_value(a.review_id, b.review_id))


PROFILE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789 '"


def char_profile(key: str) -> Tuple[int, ...]:
    """Letter counts, plus one count for every other character. The overlap of
    two profiles is an upper bound on difflib's quick_ratio(), and is much
    cheaper to compute."""
    counts = [key.count(c) for c in PROFILE_CHARS]
    return tuple(counts) + (len(key) - sum(counts),)


def dedupe(reviews: List[Review]) -> Tuple[List[Review], int]:
    """Drop copies of the same review, such as one review in two exports.

    Two rows are copies when the text is the same after normalizing (or, for
    texts of 40 characters or more, at least 95% the same) and could_be_same()
    holds. A row with no date is a copy of a dated row with the same text, and
    the dated row is kept. Near-copies are only looked for among rows with the
    same date and a similar length, which keeps big files fast.
    """
    order = sorted(range(len(reviews)), key=lambda i: reviews[i].day is None)  # dated first
    exact: Dict[Tuple[str, Optional[date]], List[Review]] = defaultdict(list)
    dated_by_text: Dict[str, List[Review]] = defaultdict(list)
    near: Dict[tuple, list] = defaultdict(list)   # (date, rating, length) -> rows
    ratings_seen = set()
    # ratio() can only reach NEAR_DUPLICATE when the shorter text is at least
    # this share of the longer one, so other lengths are never compared.
    spread = NEAR_DUPLICATE / (2 - NEAR_DUPLICATE)
    keep = [False] * len(reviews)
    for i in order:
        review = reviews[i]
        key = normalize_text(review.text)
        candidates = exact[(key, review.day)]
        if review.day is None:
            candidates = candidates + dated_by_text[key]
        duplicate = any(could_be_same(review, other) for other in candidates)
        size = len(key)
        if not duplicate and size >= NEAR_DUPLICATE_MIN_CHARS:
            matcher = difflib.SequenceMatcher(None, "", key)
            profile = char_profile(key)
            ratings = ratings_seen if review.rating is None else (review.rating, None)
            lengths = range(math.ceil(size * spread), int(size / spread) + 1)
            for other_key, other, other_profile in (
                    row for rating in ratings for length in lengths
                    for row in near.get((review.day, rating, length), ())):
                if (2 * sum(map(min, profile, other_profile)) < NEAR_DUPLICATE * (size + len(other_key))
                        or not could_be_same(review, other)):
                    continue
                matcher.set_seq1(other_key)
                if (matcher.real_quick_ratio() >= NEAR_DUPLICATE
                        and matcher.quick_ratio() >= NEAR_DUPLICATE
                        and matcher.ratio() >= NEAR_DUPLICATE):
                    duplicate = True
                    break
        if duplicate:
            continue
        keep[i] = True
        exact[(key, review.day)].append(review)
        if review.day is not None:
            dated_by_text[key].append(review)
        if size >= NEAR_DUPLICATE_MIN_CHARS * spread:
            near[(review.day, review.rating, size)].append((key, review, char_profile(key)))
            ratings_seen.add(review.rating)
    kept = [r for i, r in enumerate(reviews) if keep[i]]
    return kept, len(reviews) - len(kept)


# -------------------------------------------------------------------- themes

class Matcher:
    """A compiled pattern. Regex matchers also count a negator inside the match."""

    def __init__(self, regex, is_regex: bool = False, body: str = ""):
        self.regex = regex
        self.is_regex = is_regex
        self.body = body   # a keyword's words, without the word-boundary checks

    def finditer(self, text: str):
        return self.regex.finditer(text)


class Patterns(list):
    """A list of matchers, plus one combined search that rules out most
    clauses before each matcher is tried on its own."""

    def __init__(self, matchers=()):
        super().__init__(matchers)
        keywords = [m.body for m in self if not m.is_regex]
        self.any_keyword = (re.compile(r"(?<!\w)(?:%s)" % "|".join(keywords), re.I)
                            if keywords else None)
        self.regexes = [m.regex for m in self if m.is_regex]

    def might_match(self, text: str) -> bool:
        return bool((self.any_keyword and self.any_keyword.search(text))
                    or any(rx.search(text) for rx in self.regexes))


def keyword_pattern(keyword: str):
    """A keyword matches from a word start. A trailing $ means whole word only."""
    kw = keyword.lower().replace("’", "'").strip()
    exact = kw.endswith("$")
    words = kw.rstrip("$").split()
    body = r"\s+".join(re.escape(w) for w in words)
    return Matcher(re.compile(r"(?<!\w)" + body + (r"(?!\w)" if exact else r"\w*"), re.I),
                   body=body)


def regex_pattern(theme: str, expression: str):
    try:
        return Matcher(re.compile(expression, re.I), is_regex=True)
    except re.error as err:
        raise ValueError("Bad regex in theme '%s': %s" % (theme, err)) from err


THEME_LISTS = ("keywords", "regex", "negative_keywords", "negative_regex")


def build_theme(name: str, spec) -> Theme:
    if not isinstance(spec, dict):
        raise ValueError("Theme '%s' must be an object, like {\"keywords\": [\"crash\"]}" % name)
    for key in THEME_LISTS:
        value = spec.get(key, [])
        if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip().rstrip("$").strip() for v in value):
            raise ValueError("Theme '%s': %s must be a list of words or phrases, like "
                             "[\"crash\"]" % (name, key))
    polarity = spec.get("polarity", "neutral")
    if polarity not in ("neutral", "negative"):
        raise ValueError("Theme '%s': polarity must be 'neutral' or 'negative'" % name)
    weak: list = []
    strong: list = []
    target = strong if polarity == "negative" else weak
    for kw in spec.get("keywords", []):
        target.append(keyword_pattern(kw))
    for rx in spec.get("regex", []):
        target.append(regex_pattern(name, rx))
    for kw in spec.get("negative_keywords", []):
        strong.append(keyword_pattern(kw))
    for rx in spec.get("negative_regex", []):
        strong.append(regex_pattern(name, rx))
    if not weak and not strong:
        raise ValueError("Theme '%s' has no keywords" % name)
    return Theme(name, Patterns(weak), Patterns(strong))


def load_themes(path: Path) -> List[Theme]:
    text, _ = read_text_file(path)
    try:
        config = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError("%s is not valid JSON: %s" % (path.name, err)) from err
    specs = config.get("themes") if isinstance(config, dict) else None
    if not isinstance(specs, dict) or not specs:
        raise ValueError("%s needs a top-level 'themes' object, like "
                         "{\"themes\": {\"Bugs\": {\"keywords\": [\"crash\"]}}}" % path.name)
    return [build_theme(name, spec) for name, spec in specs.items()
            if not name.startswith("_")]


NEGATIVE_PATTERNS = Patterns(keyword_pattern(c) for c in NEGATIVE_CUES)
POSITIVE_PATTERNS = Patterns(keyword_pattern(c) for c in POSITIVE_CUES)


# ------------------------------------------------------------------- tagging

def match_text(text: str) -> str:
    return (text.lower().replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"'))


def words_in(text: str) -> List[str]:
    return re.findall(r"[a-z]+(?:'[a-z]+)?", NOT_A_NEGATION.sub(" ", text))


def has_negator(words: List[str]) -> bool:
    return any(w in NEGATORS or w.endswith("n't") for w in words)


def is_negated(clause: str, start: int) -> bool:
    """True if a negator sits within 3 words before the match, in the same phrase.

    Filler words do not use up the window, so "never had a crash or a bug"
    negates both "crash" and "bug".
    """
    before = re.split(r"[,;:.!?()]", clause[:start])[-1]
    words = [w for w in words_in(before) if w not in FILLER]
    return has_negator(words[-3:])


def scan(patterns, clause: str, keep_negated: bool = False) -> Tuple[int, int]:
    """Count matches that stand (live) and matches that are negated.

    With keep_negated, a negated match still stands (used for reviews rated 2
    or less, where "I haven't received my refund" is the complaint itself).
    """
    live = negated = 0
    if isinstance(patterns, Patterns) and not patterns.might_match(clause):
        return live, negated
    for pattern in patterns:
        for m in pattern.finditer(clause):
            inside = pattern.is_regex and has_negator(words_in(m.group()))
            if not keep_negated and (inside or is_negated(clause, m.start())):
                negated += 1
            else:
                live += 1
    return live, negated


def clause_tone(clause: str, very_low: bool = False) -> str:
    """'neg', 'pos' or 'neutral'. A negative cue beats a positive one.

    Negated praise ("not helpful") is negative. In a review rated 2 or less, a
    negator ("never", "haven't") is negative too.
    """
    neg_live, _ = scan(NEGATIVE_PATTERNS, clause)
    pos_live, pos_negated = scan(POSITIVE_PATTERNS, clause)
    if neg_live or pos_negated or (very_low and has_negator(words_in(clause))):
        return "neg"
    return "pos" if pos_live else "neutral"


def match_theme(theme: Theme, clause: str, keep_negated: bool = False) -> Optional[str]:
    if scan(theme.strong, clause, keep_negated)[0]:
        return "strong"
    if scan(theme.weak, clause, keep_negated)[0]:
        return "weak"
    return None


def split_sentences(raw: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\s*\n+\s*", raw)
    return [clean_text(p) for p in parts if clean_text(p)]


def split_clauses(sentence: str) -> List[str]:
    parts = re.split(r"[,;]|\b(?:but|however|although|though)\b", sentence, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def tag_review(review: Review, themes: List[Theme]) -> None:
    """Fill review.hits (theme -> sentences) and review.negative.

    A strong match counts on its own, even next to praise ("Love the beans, I
    was charged twice"). A weak match needs a negative word in the same clause.
    """
    low = review.rating is not None and review.rating <= LOW_RATING
    very_low = review.rating is not None and review.rating <= VERY_LOW_RATING
    negative_tone = False
    hits: Dict[str, List[Tuple[str, str]]] = {}
    for sentence in split_sentences(review.raw):
        for clause in split_clauses(sentence):
            text = match_text(clause)
            tone = clause_tone(text, very_low)
            negative_tone = negative_tone or tone == "neg"
            for theme in themes:
                kind = match_theme(theme, text, keep_negated=very_low)
                if kind == "strong" or (kind == "weak" and tone == "neg"):
                    found = hits.setdefault(theme.name, [])
                    if all(sentence != old for old, _ in found):
                        found.append((sentence, kind))
    review.hits = hits
    review.negative = low or bool(hits) or (review.rating is None and negative_tone)


# ------------------------------------------------------------------ analysis

def trend_label(recent: int, earlier_rate: float, windows: float, min_count: int) -> str:
    if windows < 1:
        return "not enough history"
    min_count = max(min_count, 1)   # zero reviews is never a trend
    if max(recent, earlier_rate) < min_count:
        return "too few to call"
    if recent >= min_count and recent >= RISE_FACTOR * earlier_rate:
        return "rising"
    if earlier_rate >= min_count and recent <= FALL_FACTOR * earlier_rate:
        return "falling"
    return "steady"


def month_key(day: date) -> str:
    return "%d-%02d" % (day.year, day.month)


def score_themes(stats: List[ThemeStats], ref: Optional[date], recent_days: int) -> None:
    cutoff = ref - timedelta(days=recent_days) if ref else None
    for st in stats:
        for r in st.reviews:
            recency = 2.0 if (cutoff and r.day and r.day > cutoff) else 1.0
            severity = 6 - r.rating if r.rating is not None else 3
            st.score += recency * severity


def analyze(reviews: List[Review], themes: List[Theme], recent_days: int, min_trend: int):
    for r in reviews:
        tag_review(r, themes)
    dated = [r for r in reviews if r.day]
    ref = max(r.day for r in dated) if dated else None
    first = min(r.day for r in dated) if dated else None
    cutoff = ref - timedelta(days=recent_days) if ref else None
    windows = ((cutoff - first).days / recent_days) if ref else 0
    negatives = [r for r in reviews if r.negative]

    stats: List[ThemeStats] = []
    for theme in themes:
        tagged = [r for r in reviews if theme.name in r.hits]
        if not tagged:
            continue
        st = ThemeStats(theme.name, tagged,
                        [(s, kind, r) for r in tagged for s, kind in r.hits[theme.name]])
        st.count = len(tagged)
        st.share = st.count / len(negatives) if negatives else 0.0
        rated = [r.rating for r in tagged if r.rating is not None]
        st.avg_rating = sum(rated) / len(rated) if rated else None
        for r in tagged:
            if r.day:
                st.months[month_key(r.day)] += 1
        recent = sum(1 for r in tagged if r.day and r.day > cutoff) if ref else 0
        earlier = sum(1 for r in tagged if r.day and r.day <= cutoff) if ref else 0
        st.recent = recent
        st.earlier_rate = earlier / windows if windows >= 1 else 0.0
        st.trend = trend_label(recent, st.earlier_rate, windows, min_trend)
        stats.append(st)
    score_themes(stats, ref, recent_days)
    stats.sort(key=lambda s: (-s.count, s.name))
    untagged = [r for r in negatives if not r.hits]
    return stats, negatives, untagged, ref, windows


def quote_rank(sentence: str, kind: str, review: Review):
    """Lowest rating first (no rating counts as 3), then a readable length,
    then strong matches, then shorter."""
    shortest, longest = QUOTE_LENGTH
    size = len(sentence)
    off = shortest - size if size < shortest else max(0, size - longest)
    rating = review.rating if review.rating is not None else 3
    return (rating, off, kind != "strong", size, sentence)


def pick_quotes(entries, limit: int = 3) -> List[Tuple[str, Review]]:
    """Sentences that name the problem, each from a different review.

    Entries are (sentence, kind, review). Sentences from the lowest-rated
    reviews come first (see quote_rank). Sentences are quoted exactly as
    written.
    """
    seen = set()
    candidates = []
    for sentence, kind, review in entries:
        key = normalize_text(sentence)
        if (len(sentence.split()) < 4 or len(sentence) > 200 or key in seen
                or EMAIL_OR_PHONE.search(sentence) or DANGLING_START.match(sentence)):
            continue
        seen.add(key)
        candidates.append((quote_rank(sentence, kind, review), sentence, review))
    candidates.sort(key=lambda c: c[0])
    picked: List[Tuple[str, Review]] = []
    for _, sentence, review in candidates:
        if any(review is p[1] for p in picked):
            continue
        if any(difflib.SequenceMatcher(None, sentence.lower(), p[0].lower()).ratio() > 0.8
               for p in picked):
            continue
        picked.append((sentence, review))
        if len(picked) == limit:
            break
    return picked


# -------------------------------------------------------------------- report

def table(header: List[str], rows: List[List[str]]) -> List[str]:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def stars(value: Optional[float]) -> str:
    return "%.1f" % value if value is not None else "n/a"


def describe_quote_source(review: Review) -> str:
    bits = []
    if review.rating is not None:
        bits.append("%g star%s" % (review.rating, "" if review.rating == 1 else "s"))
    if review.day:
        bits.append(month_key(review.day))
    if review.source:
        bits.append(review.source)
    return " (%s)" % ", ".join(bits) if bits else ""


def flag_notes(reviews: List[Review]) -> List[str]:
    """Count the cells that were set aside, so the report says so."""
    counts = [
        (sum(r.rating_unreadable for r in reviews),
         "rating could not be read and was", "ratings could not be read and were"),
        (sum(r.rating_out_of_range for r in reviews),
         "rating was outside its file's scale and was",
         "ratings were outside their file's scale and were"),
        (sum(r.date_unreadable for r in reviews),
         "date could not be read and was", "dates could not be read and were"),
        (sum(r.date_in_future for r in reviews),
         "date was in the future and was", "dates were in the future and were"),
    ]
    return ["%s treated as missing." % plural(n, one, many) for n, one, many in counts if n]


def build_report(reviews, removed, stats, negatives, untagged, ref, windows,
                 recent_days, min_trend, files, notes, untagged_label="untagged.csv",
                 columns=None, kept_duplicates=False) -> str:
    n = recent_days
    out: List[str] = []
    eligible = [s for s in stats if s.count >= MIN_FIX_COUNT]
    if eligible:
        top = max(eligible, key=lambda s: (s.score, s.count))
        if ref:
            out.append("Fix this first: **%s**. %d reviews mention it, %d in the last %d days "
                       "(up to %s, the newest review), average rating %s."
                       % (top.name, top.count, top.recent, n, ref, stars(top.avg_rating)))
        else:
            out.append("Fix this first: **%s**. %d reviews mention it, average rating %s."
                       % (top.name, top.count, stars(top.avg_rating)))
    elif stats:
        out.append("Fix this first: nothing yet. No theme has %d or more reviews." % MIN_FIX_COUNT)
    else:
        out.append("Fix this first: nothing. No complaints found.")

    dated = [r for r in reviews if r.day]
    out += ["", "## Overview", ""]
    out.append("- Files: %s" % ", ".join(files))
    for line in columns or []:
        out.append("- %s" % line)
    if kept_duplicates:
        out.append("- Reviews read: %d (duplicates kept)" % len(reviews))
    else:
        out.append("- Reviews read: %d (%s removed)" % (len(reviews), plural(removed, "duplicate")))
    if dated:
        out.append("- Dates: %s to %s (%s no date)" % (
            min(r.day for r in dated), ref,
            plural(len(reviews) - len(dated), "review has", "reviews have")))
    out.append("- Negative reviews: %d" % len(negatives))
    out.append("- Negative reviews that matched no theme: %d (see %s)" % (
        len(untagged), untagged_label))
    for note in notes:
        out.append("- Note: %s" % note)

    out += ["", "## Themes", ""]
    if not stats:
        out.append("No complaints found.")
    else:
        header = ["Theme", "Reviews", "% of negative", "Avg rating", "Last %d days" % n,
                  "Earlier, per %d days" % n, "Trend", "Score"]
        rows = [[s.name, str(s.count), "%.0f%%" % (s.share * 100), stars(s.avg_rating),
                 str(s.recent), "%.1f" % s.earlier_rate if windows >= 1 else "n/a",
                 s.trend, "%.0f" % s.score] for s in stats]
        out += table(header, rows)
        out += ["", "A review counts once per theme. Score is the fix-first rule: each review "
                "adds (2 if from the last %d days, else 1) times (6 minus its rating). Trend "
                "needs at least %d reviews in a period." % (n, min_trend)]

    months = sorted({month_key(r.day) for r in dated})[-12:]
    if months and stats:
        out += ["", "## Month by month", ""]
        rows = [[s.name] + [str(s.months.get(m, 0)) for m in months] for s in stats]
        rows.append(["All negative reviews"] + [
            str(sum(1 for r in negatives if r.day and month_key(r.day) == m)) for m in months])
        rows.append(["All reviews"] + [
            str(sum(1 for r in dated if month_key(r.day) == m)) for m in months])
        out += table(["Theme"] + months, rows)

    if stats:
        out += ["", "## Quotes", ""]
    for s in stats:
        out.append("### %s" % s.name)
        out.append("")
        for sentence, review in pick_quotes(s.sentences):
            out.append('> "%s"%s' % (sentence, describe_quote_source(review)))
            out.append("")

    small = []
    if len(reviews) < 50:
        small.append("Only %s. Treat every count as a rough sketch." % plural(len(reviews), "review"))
    thin = [s.name for s in stats if s.count < min_trend]
    if thin:
        small.append("Fewer than %d reviews, so too few to rank with confidence: %s." % (
            min_trend, ", ".join(thin)))
    vague = [s.name for s in stats if s.trend in ("too few to call", "not enough history")]
    if vague:
        small.append("No trend called (under %d reviews in a period, or under %d days of "
                     "history): %s." % (min_trend, 2 * n, ", ".join(vague)))
    if len(dated) < len(reviews):
        small.append("%s no date and %s left out of trends and months." % (
            plural(len(reviews) - len(dated), "review has", "reviews have"),
            "is" if len(reviews) - len(dated) == 1 else "are"))
    if not any(r.rating is not None for r in reviews):
        small.append("No ratings found, so only negative wording marks a complaint.")
    if small:
        out += ([""] if out[-1] else []) + ["## Not enough data to say", ""]
        out += ["- " + line for line in small]
    return "\n".join(out).rstrip() + "\n"


def write_untagged(path: Path, untagged: List[Review]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "rating", "source", "text"])
        for r in untagged:
            writer.writerow([r.day or "", "" if r.rating is None else "%g" % r.rating,
                             r.source, r.text])


# ----------------------------------------------------------------------- cli

def path_label(path: Path) -> str:
    """Name a path the way the user can find it: relative to the current folder,
    else relative to the repo (the demo), else just the file name."""
    for base in (Path.cwd(), ROOT):
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return path.name


def run(args) -> Tuple[str, str, List[str]]:
    """Run the whole pipeline. Returns (report text, themes file used, column lines)."""
    overrides = {"text": args.text_col, "rating": args.rating_col, "date": args.date_col,
                 "source": args.source_col, "reviewer": args.reviewer_col, "id": args.id_col}
    reviews, infos = load_reviews(args.files, overrides, args.delimiter)
    if not reviews:
        raise ValueError("No reviews found in the input files.")
    notes = [note for info in infos for note in info.notes]
    removed = 0
    if not args.keep_duplicates:
        reviews, removed = dedupe(reviews)
    notes += flag_notes(reviews)
    themes_path = Path(args.themes)
    themes = load_themes(themes_path)
    stats, negatives, untagged, ref, windows = analyze(
        reviews, themes, args.recent_days, args.min_trend_count)
    write_untagged(Path(args.untagged), untagged)
    label = path_label(Path(args.untagged))
    columns = [describe_columns(info) for info in infos]
    report = build_report(reviews, removed, stats, negatives, untagged, ref, windows,
                          args.recent_days, args.min_trend_count,
                          [Path(f).name for f in args.files], notes, label,
                          columns=columns, kept_duplicates=args.keep_duplicates)
    return report, str(themes_path), columns


def parse_args(argv):
    p = argparse.ArgumentParser(description="Find recurring complaints in review CSVs.")
    p.add_argument("files", nargs="*", help="one or more review CSV files")
    p.add_argument("--themes", help="theme dictionary (default: ./themes.json, else the template)")
    p.add_argument("--out", help="report path, relative to the current folder (default: out/report.md)")
    p.add_argument("--untagged", help="untagged reviews CSV (default: untagged.csv next to the report)")
    p.add_argument("--recent-days", type=int, default=30, help="length of the recent period")
    p.add_argument("--min-trend-count", type=int, default=MIN_TREND_COUNT,
                   help="reviews needed in a period before calling a trend")
    p.add_argument("--text-col", help="column with the review text (default: found by name)")
    p.add_argument("--rating-col", help="column with the star rating (default: found by name)")
    p.add_argument("--date-col", help="column with the review date (default: found by name)")
    p.add_argument("--source-col", help="column with the platform, like App Store (default: found by name)")
    p.add_argument("--reviewer-col", help="column with the reviewer's name, used to spot copies "
                                          "(default: found by name)")
    p.add_argument("--id-col", help="column with a review ID, used to spot copies (default: found by name)")
    p.add_argument("--delimiter", help='column separator: ",", ";" or "tab" (default: detected per file)')
    p.add_argument("--keep-duplicates", action="store_true",
                   help="keep every row, even copies of the same review")
    p.add_argument("--demo", action="store_true",
                   help="run on examples/reviews.example.csv and write examples/sample-report.md")
    args = p.parse_args(argv)
    if args.recent_days < 1:
        p.error("--recent-days must be at least 1")
    if args.min_trend_count < 1:
        p.error("--min-trend-count must be at least 1")
    if args.delimiter is not None:
        named = {"tab": "\t", "\\t": "\t", "comma": ",", "semicolon": ";"}
        args.delimiter = named.get(args.delimiter.lower(), args.delimiter)
        if len(args.delimiter) != 1:
            p.error('--delimiter must be one character, like ";", or "tab"')
    if args.demo:
        args.files = [str(ROOT / "examples" / "reviews.example.csv")]
        args.themes = args.themes or str(ROOT / "config" / "themes.template.json")
        args.out = args.out or str(ROOT / "examples" / "sample-report.md")
        args.untagged = args.untagged or str(ROOT / "out" / "untagged.csv")
    if not args.files:
        p.error("give at least one CSV file, or use --demo")
    if not args.themes:
        local = Path("themes.json")
        if local.exists():
            args.themes = str(local)
        else:
            args.themes = str(ROOT / "config" / "themes.template.json")
            print("No themes.json in this folder, so the starter themes are used.",
                  file=sys.stderr)
    args.out = args.out or "out/report.md"
    args.untagged = args.untagged or str(Path(args.out).parent / "untagged.csv")
    return args


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        report, themes_used, columns = run(args)
    except (ValueError, OSError, csv.Error) as err:
        print("Error: %s" % err, file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    for line in columns:
        print(line, file=sys.stderr)
    print(report.splitlines()[0].replace("**", ""))
    print("Themes: %s" % themes_used)
    print("Report: %s" % out)
    print("Untagged: %s" % args.untagged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
