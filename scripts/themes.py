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
    A sentence is split into clauses at "but", "however", "although", "though"
    and ";". A clause counts toward a theme when a theme keyword matches, the
    keyword is not negated ("not slow"), and the clause is not plain praise.
    Weak keywords also need a negative cue word, or a rating of 3 or lower.
    Strong keywords (negative_keywords, or any keyword in a theme whose
    polarity is "negative") count on their own.

Dates with slashes are read as month/day/year. Ratings are expected on a 1 to 5
scale. A 0 to 10 scale (for example NPS) is detected and halved.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import html
import io
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOW_RATING = 3.0          # a rating at or below this counts as negative
MIN_FIX_COUNT = 3         # a theme needs this many reviews to be "fix first"
MIN_TREND_COUNT = 5       # per-period count needed before calling a trend
RISE_FACTOR = 1.5
FALL_FACTOR = 0.5
NEAR_DUPLICATE = 0.95
NEAR_DUPLICATE_MIN_CHARS = 40   # shorter reviews must match exactly

COLUMN_ALIASES = {
    "text": ["text", "review", "review_text", "review_body", "body", "comment",
             "comments", "content", "feedback", "message", "description"],
    "rating": ["rating", "stars", "star_rating", "score", "review_rating"],
    "date": ["date", "created_at", "review_date", "created", "submitted_at",
             "timestamp", "time", "at"],
    "source": ["source", "platform", "channel", "store"],
}

NEGATORS = {"not", "no", "never", "without", "hardly", "barely", "nothing",
            "cannot", "neither", "nor"}

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
    "impossible", "confus", "wrong", "stuck", "error", "no longer",
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
    hits: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    negative: bool = False
    rating_unreadable: bool = False   # a rating cell had text but no number


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


# ------------------------------------------------------------------- reading

def norm_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower().strip()).strip("_")


def detect_columns(headers: List[str], overrides: Dict[str, Optional[str]]) -> Dict[str, str]:
    """Map our field names to the CSV's own header names."""
    by_norm = {norm_header(h): h for h in headers}
    cols: Dict[str, str] = {}
    for fld, aliases in COLUMN_ALIASES.items():
        wanted = overrides.get(fld)
        if wanted:
            if wanted not in headers:
                raise ValueError("Column '%s' not found. Columns are: %s" % (wanted, ", ".join(headers)))
            cols[fld] = wanted
            continue
        for alias in aliases:
            if alias in by_norm:
                cols[fld] = by_norm[alias]
                break
    if "text" not in cols:
        raise ValueError("No review text column found. Columns are: %s. Use --text-col NAME."
                         % ", ".join(headers))
    return cols


def parse_rating(value: str) -> Optional[float]:
    match = re.search(r"\d+(?:[.,]\d+)?", value or "")
    return float(match.group().replace(",", ".")) if match else None


DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y",
                "%d %B %Y", "%d.%m.%Y", "%Y/%m/%d"]


def parse_date(value: str) -> Optional[date]:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        pass
    v = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", v)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def clean_text(raw: str) -> str:
    text = html.unescape(raw)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_reviews(paths, overrides=None) -> List[Review]:
    overrides = overrides or {}
    reviews: List[Review] = []
    for path in paths:
        path = Path(path)
        rows = csv.DictReader(io.StringIO(read_text_file(path), newline=""))
        if not rows.fieldnames:
            raise ValueError("%s is empty" % path.name)
        cols = detect_columns([h for h in rows.fieldnames if h], overrides)
        for row in rows:
            raw = (row.get(cols["text"]) or "").strip()
            text = clean_text(raw)
            if not text:
                continue
            rating_cell = (row.get(cols.get("rating", ""), "") or "").strip()
            rating = parse_rating(rating_cell)
            reviews.append(Review(
                raw=raw, text=text,
                rating=rating,
                rating_unreadable=bool(rating_cell) and rating is None,
                day=parse_date(row.get(cols.get("date", ""), "") or ""),
                source=(row.get(cols.get("source", ""), "") or "").strip(),
            ))
    return reviews


def scale_ratings(reviews: List[Review]) -> str:
    """Halve a 0 to 10 scale. Drop ratings that fit neither scale. Returns a note."""
    rated = [r.rating for r in reviews if r.rating is not None]
    if not rated:
        return ""
    top = max(rated)
    if top <= 5:
        return ""
    if top <= 10:
        for r in reviews:
            if r.rating is not None:
                r.rating = r.rating / 2
        return "Ratings went up to %g, so they were halved to fit a 5-point scale." % top
    for r in reviews:
        r.rating = None
    return "Ratings went above 10, so they were ignored."


# ------------------------------------------------------------------- cleaning

def normalize_text(text: str) -> str:
    t = text.lower().replace("’", "'")
    return re.sub(r"[^\w']+", " ", t).strip()


def dedupe(reviews: List[Review]) -> Tuple[List[Review], int]:
    """Drop exact and near-exact copies (same words after normalizing)."""
    kept: List[Review] = []
    buckets: Dict[int, list] = defaultdict(list)
    removed = 0
    for review in reviews:
        key = normalize_text(review.text)
        slot = len(key) // 10
        duplicate = False
        for near in (slot - 1, slot, slot + 1):
            for other_key, other in buckets[near]:
                if review.rating is not None and other.rating is not None \
                        and review.rating != other.rating:
                    continue
                if key == other_key or (
                        len(key) >= NEAR_DUPLICATE_MIN_CHARS
                        and difflib.SequenceMatcher(None, key, other_key).ratio() >= NEAR_DUPLICATE):
                    duplicate = True
                    break
            if duplicate:
                break
        if duplicate:
            removed += 1
        else:
            kept.append(review)
            buckets[slot].append((key, review))
    return kept, removed


# -------------------------------------------------------------------- themes

class Matcher:
    """A compiled pattern. Regex matchers also count a negator inside the match."""

    def __init__(self, regex, is_regex: bool = False):
        self.regex = regex
        self.is_regex = is_regex

    def finditer(self, text: str):
        return self.regex.finditer(text)


def keyword_pattern(keyword: str):
    """A keyword matches from a word start. A trailing $ means whole word only."""
    kw = keyword.lower().replace("’", "'").strip()
    exact = kw.endswith("$")
    words = kw.rstrip("$").split()
    body = r"\s+".join(re.escape(w) for w in words)
    return Matcher(re.compile(r"(?<!\w)" + body + (r"(?!\w)" if exact else r"\w*"), re.I))


def regex_pattern(theme: str, expression: str):
    try:
        return Matcher(re.compile(expression, re.I), is_regex=True)
    except re.error as err:
        raise ValueError("Bad regex in theme '%s': %s" % (theme, err))


def build_theme(name: str, spec: dict) -> Theme:
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
    return Theme(name, weak, strong)


def load_themes(path: Path) -> List[Theme]:
    config = json.loads(read_text_file(path))
    specs = config.get("themes")
    if not isinstance(specs, dict) or not specs:
        raise ValueError("%s needs a top-level 'themes' object" % path.name)
    return [build_theme(name, spec) for name, spec in specs.items()
            if not name.startswith("_")]


NEGATIVE_PATTERNS = [keyword_pattern(c) for c in NEGATIVE_CUES]
POSITIVE_PATTERNS = [keyword_pattern(c) for c in POSITIVE_CUES]


# ------------------------------------------------------------------- tagging

def match_text(text: str) -> str:
    return (text.lower().replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"'))


def has_negator(words: List[str]) -> bool:
    return any(w in NEGATORS or w.endswith("n't") for w in words)


def is_negated(clause: str, start: int) -> bool:
    """True if a negator sits within 3 words before the match, in the same phrase.

    Filler words do not use up the window, so "never had a crash or a bug"
    negates both "crash" and "bug".
    """
    before = re.split(r"[,;:.!?()]", clause[:start])[-1]
    before = NOT_A_NEGATION.sub(" ", before)
    words = [w for w in re.findall(r"[a-z]+(?:'[a-z]+)?", before) if w not in FILLER]
    return has_negator(words[-3:])


def scan(patterns, clause: str) -> Tuple[int, int]:
    """Count matches that stand (live) and matches that are negated."""
    live = negated = 0
    for pattern in patterns:
        for m in pattern.finditer(clause):
            inside = pattern.is_regex and has_negator(
                re.findall(r"[a-z']+", NOT_A_NEGATION.sub(" ", m.group())))
            if inside or is_negated(clause, m.start()):
                negated += 1
            else:
                live += 1
    return live, negated


def clause_tone(clause: str) -> str:
    """'neg', 'pos' or 'neutral'. A negative cue beats a positive one."""
    neg_live, _ = scan(NEGATIVE_PATTERNS, clause)
    pos_live, pos_negated = scan(POSITIVE_PATTERNS, clause)
    if neg_live or pos_negated:
        return "neg"
    return "pos" if pos_live else "neutral"


def match_theme(theme: Theme, clause: str) -> Optional[str]:
    if scan(theme.strong, clause)[0]:
        return "strong"
    if scan(theme.weak, clause)[0]:
        return "weak"
    return None


def split_sentences(raw: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\s*\n+\s*", raw)
    return [clean_text(p) for p in parts if clean_text(p)]


def split_clauses(sentence: str) -> List[str]:
    parts = re.split(r";|\b(?:but|however|although|though)\b", sentence, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def tag_review(review: Review, themes: List[Theme]) -> None:
    """Fill review.hits (theme -> sentences) and review.negative."""
    low = review.rating is not None and review.rating <= LOW_RATING
    negative_tone = False
    hits: Dict[str, List[Tuple[str, str]]] = {}
    for sentence in split_sentences(review.raw):
        for clause in split_clauses(sentence):
            text = match_text(clause)
            tone = clause_tone(text)
            negative_tone = negative_tone or tone == "neg"
            if tone == "pos":
                continue
            for theme in themes:
                kind = match_theme(theme, text)
                if kind and (tone == "neg" or kind == "strong" or low):
                    found = hits.setdefault(theme.name, [])
                    if all(sentence != old for old, _ in found):
                        found.append((sentence, kind))
    review.hits = hits
    review.negative = low or bool(hits) or (review.rating is None and negative_tone)


# ------------------------------------------------------------------ analysis

def trend_label(recent: int, earlier_rate: float, windows: float, min_count: int) -> str:
    if windows < 1:
        return "not enough history"
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


def pick_quotes(entries, limit: int = 3) -> List[Tuple[str, Review]]:
    """Short sentences that name the problem outright, each from a different review.

    Entries are (sentence, kind, review). Strong matches come first, then the
    shortest sentence. Sentences are quoted exactly as written.
    """
    seen = set()
    candidates = []
    for sentence, kind, review in entries:
        key = normalize_text(sentence)
        if (len(sentence.split()) < 4 or len(sentence) > 200 or key in seen
                or EMAIL_OR_PHONE.search(sentence) or DANGLING_START.match(sentence)):
            continue
        seen.add(key)
        candidates.append((kind != "strong", len(sentence), sentence, review))
    candidates.sort(key=lambda c: c[:3])
    picked: List[Tuple[str, Review]] = []
    for _, _, sentence, review in candidates:
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


def build_report(reviews, removed, stats, negatives, untagged, ref, windows,
                 recent_days, min_trend, files, notes, untagged_label="untagged.csv") -> str:
    n = recent_days
    out: List[str] = []
    eligible = [s for s in stats if s.count >= MIN_FIX_COUNT]
    if eligible:
        top = max(eligible, key=lambda s: (s.score, s.count))
        out.append("Fix this first: **%s**. %d reviews mention it, %d in the last %d days, "
                   "average rating %s." % (top.name, top.count, top.recent, n, stars(top.avg_rating)))
    else:
        out.append("Fix this first: nothing yet. No theme has %d or more reviews." % MIN_FIX_COUNT)

    dated = [r for r in reviews if r.day]
    out += ["", "## Overview", ""]
    out.append("- Files: %s" % ", ".join(files))
    out.append("- Reviews read: %d (%d duplicates removed)" % (len(reviews), removed))
    if dated:
        out.append("- Dates: %s to %s (%d reviews have no date)" % (
            min(r.day for r in dated), ref, len(reviews) - len(dated)))
    out.append("- Negative reviews: %d" % len(negatives))
    out.append("- Negative reviews that matched no theme: %d (see %s)" % (
        len(untagged), untagged_label))
    for note in notes:
        out.append("- Note: %s" % note)

    out += ["", "## Themes", ""]
    header = ["Theme", "Reviews", "% of negative", "Avg rating", "Last %d days" % n,
              "Earlier, per %d days" % n, "Trend", "Score"]
    rows = [[s.name, str(s.count), "%.0f%%" % (s.share * 100), stars(s.avg_rating),
             str(s.recent), "%.1f" % s.earlier_rate if windows >= 1 else "n/a",
             s.trend, "%.0f" % s.score] for s in stats]
    out += table(header, rows)
    out += ["", "A review counts once per theme. Score is the fix-first rule: each review adds "
            "(2 if from the last %d days, else 1) times (6 minus its rating). Trend needs at "
            "least %d reviews in a period." % (n, min_trend)]

    months = sorted({month_key(r.day) for r in dated})[-12:]
    if months:
        out += ["", "## Month by month", ""]
        rows = [[s.name] + [str(s.months.get(m, 0)) for m in months] for s in stats]
        rows.append(["All negative reviews"] + [
            str(sum(1 for r in negatives if r.day and month_key(r.day) == m)) for m in months])
        rows.append(["All reviews"] + [
            str(sum(1 for r in dated if month_key(r.day) == m)) for m in months])
        out += table(["Theme"] + months, rows)

    out += ["", "## Quotes", ""]
    for s in stats:
        out.append("### %s" % s.name)
        out.append("")
        for sentence, review in pick_quotes(s.sentences):
            out.append('> "%s"%s' % (sentence, describe_quote_source(review)))
            out.append("")

    small = []
    if len(reviews) < 50:
        small.append("Only %d reviews. Treat every count as a rough sketch." % len(reviews))
    thin = [s.name for s in stats if s.count < MIN_TREND_COUNT]
    if thin:
        small.append("Fewer than %d reviews, so too few to rank with confidence: %s." % (
            MIN_TREND_COUNT, ", ".join(thin)))
    vague = [s.name for s in stats if s.trend in ("too few to call", "not enough history")]
    if vague:
        small.append("No trend called (under %d reviews in a period, or under %d days of "
                     "history): %s." % (min_trend, n, ", ".join(vague)))
    if len(dated) < len(reviews):
        small.append("%d reviews have no date and are left out of trends and months." % (
            len(reviews) - len(dated)))
    if not any(r.rating is not None for r in reviews):
        small.append("No ratings found, so only negative wording marks a complaint.")
    if small:
        out += ["## Not enough data to say", ""] + ["- " + line for line in small]
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


def run(args) -> Tuple[str, str]:
    """Run the whole pipeline. Returns (report text, path of themes file used)."""
    overrides = {"text": args.text_col, "rating": args.rating_col,
                 "date": args.date_col, "source": args.source_col}
    reviews = load_reviews(args.files, overrides)
    if not reviews:
        raise ValueError("No reviews found in the input files.")
    notes = [n for n in [scale_ratings(reviews)] if n]
    reviews, removed = dedupe(reviews)
    unreadable = sum(1 for r in reviews if r.rating_unreadable)
    if unreadable:
        notes.append("%d rating%s could not be read and %s treated as missing." % (
            unreadable, "" if unreadable == 1 else "s", "was" if unreadable == 1 else "were"))
    themes_path = Path(args.themes)
    themes = load_themes(themes_path)
    stats, negatives, untagged, ref, windows = analyze(
        reviews, themes, args.recent_days, args.min_trend_count)
    write_untagged(Path(args.untagged), untagged)
    label = path_label(Path(args.untagged))
    report = build_report(reviews, removed, stats, negatives, untagged, ref, windows,
                          args.recent_days, args.min_trend_count,
                          [Path(f).name for f in args.files], notes, label)
    return report, str(themes_path)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Find recurring complaints in review CSVs.")
    p.add_argument("files", nargs="*", help="one or more review CSV files")
    p.add_argument("--themes", help="theme dictionary (default: ./themes.json, else the template)")
    p.add_argument("--out", help="report path, relative to the current folder (default: out/report.md)")
    p.add_argument("--untagged", help="untagged reviews CSV (default: untagged.csv next to the report)")
    p.add_argument("--recent-days", type=int, default=30, help="length of the recent period")
    p.add_argument("--min-trend-count", type=int, default=MIN_TREND_COUNT,
                   help="reviews needed in a period before calling a trend")
    p.add_argument("--text-col")
    p.add_argument("--rating-col")
    p.add_argument("--date-col")
    p.add_argument("--source-col")
    p.add_argument("--demo", action="store_true",
                   help="run on examples/reviews.example.csv and write examples/sample-report.md")
    args = p.parse_args(argv)
    if args.recent_days < 1:
        p.error("--recent-days must be at least 1")
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
        report, themes_used = run(args)
    except (ValueError, OSError, json.JSONDecodeError) as err:
        print("Error: %s" % err, file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report.splitlines()[0])
    print("Themes: %s" % themes_used)
    print("Report: %s" % out)
    print("Untagged: %s" % args.untagged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
