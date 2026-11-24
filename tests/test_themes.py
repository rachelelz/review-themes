"""Offline tests. Run from the repo root: python3 -m unittest discover tests"""
import csv
import io
import json
import os
import random
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import themes as rt  # noqa: E402

TEMPLATE = ROOT / "config" / "themes.template.json"


def write_csv(directory, name, header, rows, encoding="utf-8", delimiter=","):
    path = Path(directory) / name
    with open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def make_review(text, rating=None, day=None, reviewer="", review_id="", source=""):
    return rt.Review(raw=text, text=rt.clean_text(text), rating=rating, day=day,
                     source=source, reviewer=reviewer, review_id=review_id)


def tag(text, rating=None, theme_file=TEMPLATE):
    review = make_review(text, rating)
    rt.tag_review(review, rt.load_themes(theme_file))
    return review


def run_main(argv):
    """Run the CLI quietly. Returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = rt.main(argv)
    return code, out.getvalue(), err.getvalue()


class ColumnDetectionTests(unittest.TestCase):
    def test_aliases_are_found(self):
        cols = rt.detect_columns(["Created At", "Stars", "Review Body", "Platform"], {})
        self.assertEqual(cols, {"text": "Review Body", "rating": "Stars",
                                "date": "Created At", "source": "Platform"})

    def test_override_wins(self):
        cols = rt.detect_columns(["text", "notes"], {"text": "notes"})
        self.assertEqual(cols["text"], "notes")

    def test_missing_text_column_explains_itself(self):
        with self.assertRaises(ValueError) as ctx:
            rt.detect_columns(["a", "b"], {})
        self.assertIn("--text-col", str(ctx.exception))

    def test_header_words_are_matched(self):
        cols = rt.detect_columns(["Review Content", "Review Stars", "Review Created (UTC)",
                                  "Source Of Review"], {})
        self.assertEqual(cols, {"text": "Review Content", "rating": "Review Stars",
                                "date": "Review Created (UTC)", "source": "Source Of Review"})

    def test_google_play_headers(self):
        headers = ["Package Name", "Reviewer Language", "Device", "Review Submit Date and Time",
                   "Review Submit Millis Since Epoch", "Review Last Update Date and Time",
                   "Star Rating", "Review Title", "Review Text", "Developer Reply Date and Time",
                   "Developer Reply Text", "Review Link"]
        cols = rt.detect_columns(headers, {})
        self.assertEqual(cols, {"text": "Review Text", "rating": "Star Rating",
                                "date": "Review Submit Date and Time"})

    def test_bom_and_multiline_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.csv"
            path.write_bytes(("﻿Review,Rating,Date\n"
                              '"Line one.\nLine two, with a comma.",2,2026-03-04\n'
                              "No rating or date here,,\n").encode("utf-8"))
            reviews, infos = rt.load_reviews([path])
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0].text, "Line one. Line two, with a comma.")
        self.assertEqual(reviews[0].rating, 2.0)
        self.assertEqual(reviews[0].day, date(2026, 3, 4))
        self.assertIsNone(reviews[1].rating)
        self.assertIsNone(reviews[1].day)
        self.assertEqual(infos[0].columns["text"], "Review")

    def test_date_formats(self):
        self.assertEqual(rt.parse_date("2026-03-04T10:11:12Z"), date(2026, 3, 4))
        self.assertEqual(rt.parse_date("03/04/2026"), date(2026, 3, 4))
        self.assertEqual(rt.parse_date("Mar 4, 2026 10:30 AM"), date(2026, 3, 4))
        self.assertEqual(rt.parse_date("01-11-2026"), date(2026, 11, 1))
        self.assertEqual(rt.parse_date("04.05.2026"), date(2026, 5, 4))
        self.assertIsNone(rt.parse_date("not a date"))

    def test_ten_point_ratings_are_halved(self):
        reviews = [make_review("a", 10), make_review("b", 4)]
        notes = rt.fit_rating_scale(reviews, "nps.csv")
        self.assertEqual([r.rating for r in reviews], [5.0, 2.0])
        self.assertIn("halved", notes[0])


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_semicolon_file_is_read(self):
        path = Path(self.tmp.name) / "nl.csv"
        path.write_text("date;rating;review\n01-11-2026;2;The app crashes, every time.\n"
                        "02-11-2026;4,5;Fine\n", encoding="utf-8")
        reviews, infos = rt.load_reviews([path])
        self.assertEqual(infos[0].delimiter, ";")
        self.assertEqual([r.text for r in reviews], ["The app crashes, every time.", "Fine"])
        self.assertEqual([r.rating for r in reviews], [2.0, 4.5])
        self.assertEqual([r.day for r in reviews], [date(2026, 11, 1), date(2026, 11, 2)])

    def test_delimiter_option(self):
        src = write_csv(self.tmp.name, "t.tsv", ["review", "rating"],
                        [["App crashes, sadly.", 1]], delimiter="\t")
        code, _, _ = run_main([str(src), "--themes", str(TEMPLATE), "--delimiter", "tab",
                               "--out", str(Path(self.tmp.name) / "r.md")])
        self.assertEqual(code, 0)
        report = (Path(self.tmp.name) / "r.md").read_text(encoding="utf-8")
        self.assertIn("(separated by tabs)", report)
        self.assertIn("| Bugs and crashes | 1 |", report)

    def test_utf16_file_is_read(self):
        path = Path(self.tmp.name) / "excel.txt"
        path.write_bytes("review\trating\nApp crashes on launch.\t1\n".encode("utf-16"))
        reviews, infos = rt.load_reviews([path])
        self.assertEqual(infos[0].encoding, "UTF-16")
        self.assertEqual(infos[0].delimiter, "\t")
        self.assertEqual((reviews[0].text, reviews[0].rating), ("App crashes on launch.", 1.0))

    def test_mixed_rating_scales_are_fitted_per_file(self):
        stars = write_csv(self.tmp.name, "stars.csv", ["review", "rating"],
                          [["Love the coffee.", 5], ["Great beans.", 5], ["Good box.", 4]])
        nps = write_csv(self.tmp.name, "nps.csv", ["comment", "score"],
                        [["Would recommend.", 10], ["Price is fine.", 8]])
        reviews, infos = rt.load_reviews([stars, nps])
        self.assertEqual([r.rating for r in reviews], [5.0, 5.0, 4.0, 5.0, 4.0])
        self.assertEqual(infos[0].notes, [])
        self.assertIn("nps.csv", infos[1].notes[0])

    def test_one_typo_rating_is_set_aside_alone(self):
        src = write_csv(self.tmp.name, "t.csv", ["review", "rating"],
                        [["The app is slow.", 4], ["Great.", 5], ["Typo row.", 40], ["Meh.", 3]])
        reviews, _ = rt.load_reviews([src])
        self.assertEqual([r.rating for r in reviews], [4.0, 5.0, None, 3.0])
        self.assertTrue(reviews[2].rating_out_of_range)
        self.assertEqual(rt.flag_notes(reviews),
                         ["1 rating was outside its file's scale and was treated as missing."])

    def test_european_dates_are_read_day_first(self):
        src = write_csv(self.tmp.name, "eu.csv", ["review", "date"],
                        [["First.", "04/05/2026"], ["Second.", "13/10/2026"]])
        reviews, _ = rt.load_reviews([src])
        self.assertEqual([r.day for r in reviews], [date(2026, 5, 4), date(2026, 10, 13)])

    def test_us_dates_stay_month_first(self):
        src = write_csv(self.tmp.name, "us.csv", ["review", "date"],
                        [["First.", "04/05/2026"], ["Second.", "11/13/2026"]])
        reviews, _ = rt.load_reviews([src])
        self.assertEqual([r.day for r in reviews], [date(2026, 4, 5), date(2026, 11, 13)])

    def test_unreadable_dates_are_counted(self):
        src = write_csv(self.tmp.name, "d.csv", ["review", "date"],
                        [["First.", "yesterday"], ["Second.", "31/31/2026"], ["Third.", ""]])
        reviews, _ = rt.load_reviews([src])
        self.assertEqual(rt.flag_notes(reviews),
                         ["2 dates could not be read and were treated as missing."])

    def test_future_dates_are_set_aside(self):
        src = write_csv(self.tmp.name, "f.csv", ["review", "date"],
                        [["Fine.", "2026-03-01"], ["Typo.", "2062-11-19"]])
        reviews, _ = rt.load_reviews([src], today=date(2026, 11, 24))
        self.assertEqual([r.day for r in reviews], [date(2026, 3, 1), None])
        self.assertEqual(rt.flag_notes(reviews),
                         ["1 date was in the future and was treated as missing."])

    def test_a_far_future_row_does_not_break_trends(self):
        rows = [["2026-%02d-05" % m, 1, "I was charged twice, case %d-%d." % (m, i)]
                for m, n in ((1, 2), (2, 2), (3, 2), (4, 10)) for i in range(n)]
        rows.append(["2062-11-19", 5, "Lovely coffee."])
        src = write_csv(self.tmp.name, "in.csv", ["date", "rating", "review"], rows)
        code, _, _ = run_main([str(src), "--themes", str(TEMPLATE),
                               "--out", str(Path(self.tmp.name) / "r.md")])
        report = (Path(self.tmp.name) / "r.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("(up to 2026-04-05, the newest review)", report)
        self.assertTrue(any(line.startswith("| Billing and refunds") and "rising" in line
                            for line in report.splitlines()))
        self.assertIn("1 date was in the future and was treated as missing.", report)

    def test_large_fields_are_read(self):
        path = Path(self.tmp.name) / "big.csv"
        path.write_text('review,rating\n"%s The app crashes.",1\n' % ("x" * 200000),
                        encoding="utf-8")
        reviews, _ = rt.load_reviews([path])
        self.assertEqual(len(reviews), 1)


class ErrorTests(unittest.TestCase):
    """Bad input ends with one clear line and exit code 2, never a traceback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = write_csv(self.tmp.name, "in.csv", ["review", "rating"],
                             [["App crashes.", 1]])

    def assert_clean_error(self, argv, expected):
        code, _, err = run_main(argv + ["--out", str(Path(self.tmp.name) / "r.md")])
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(err.startswith("Error: "), err)
        self.assertIn(expected, err)

    def themes_file(self, content):
        path = Path(self.tmp.name) / "t.json"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_nul_bytes(self):
        path = Path(self.tmp.name) / "nul.csv"
        path.write_bytes(b'review,rating\n"ok\x00bad",1\n')
        self.assert_clean_error([str(path), "--themes", str(TEMPLATE)], "NUL bytes")

    def test_themes_file_that_is_a_list(self):
        self.assert_clean_error([str(self.csv), "--themes", self.themes_file("[]")],
                                "'themes' object")

    def test_theme_that_is_a_list(self):
        self.assert_clean_error(
            [str(self.csv), "--themes", self.themes_file('{"themes": {"X": ["crash"]}}')],
            "must be an object")

    def test_keywords_given_as_a_string(self):
        self.assert_clean_error(
            [str(self.csv), "--themes", self.themes_file('{"themes": {"X": {"keywords": "crash"}}}')],
            "keywords must be a list")

    def test_broken_json(self):
        self.assert_clean_error([str(self.csv), "--themes", self.themes_file('{"themes": {')],
                                "t.json is not valid JSON")

    def test_bad_regex_keeps_its_cause(self):
        with self.assertRaises(ValueError) as ctx:
            rt.build_theme("X", {"regex": ["("]})
        self.assertIsInstance(ctx.exception.__cause__, re.error)


class DedupeTests(unittest.TestCase):
    def test_exact_and_near_exact_copies_are_removed(self):
        reviews = [make_review("The app is slow and keeps freezing on my phone.", 2),
                   make_review("The app is slow and keeps freezing on my phone.", 2),
                   make_review("THE APP IS SLOW AND KEEPS FREEZING ON MY PHONE!!", 2),
                   make_review("Delivery was late twice this month.", 2)]
        kept, removed = rt.dedupe(reviews)
        self.assertEqual(removed, 2)
        self.assertEqual(len(kept), 2)

    def test_near_copy_on_the_same_day_is_removed(self):
        day = date(2026, 10, 1)
        kept, removed = rt.dedupe([
            make_review("The app is slow and keeps freezing on my phone.", 2, day),
            make_review("The app is slow and keeps freezing on my phone..", 2, day),
            make_review("The app is slow and keps freezing on my phone.", 2, day),
            make_review("The app is slow and keps freezing on my phone.", 2, day + timedelta(1))])
        self.assertEqual(removed, 2)

    def test_short_reviews_that_differ_by_a_digit_are_kept(self):
        kept, removed = rt.dedupe([make_review("Charged twice, order 1.", 1),
                                   make_review("Charged twice, order 2.", 1)])
        self.assertEqual(removed, 0)

    def test_same_words_with_different_ratings_are_kept(self):
        kept, removed = rt.dedupe([make_review("Fine overall, nothing special.", 3),
                                   make_review("Fine overall, nothing special.", 5)])
        self.assertEqual(removed, 0)
        self.assertEqual(len(kept), 2)

    def test_same_text_on_different_days_is_kept(self):
        reviews = [make_review("App keeps crashing.", 1, date(2026, 10, d)) for d in range(1, 21)]
        kept, removed = rt.dedupe(reviews)
        self.assertEqual((len(kept), removed), (20, 0))

    def test_same_text_from_different_reviewers_is_kept(self):
        day = date(2026, 10, 1)
        kept, removed = rt.dedupe([make_review("App keeps crashing.", 1, day, reviewer="A.B."),
                                   make_review("App keeps crashing.", 1, day, reviewer="C.D.")])
        self.assertEqual(removed, 0)

    def test_different_ids_are_kept(self):
        day = date(2026, 10, 1)
        kept, removed = rt.dedupe([make_review("App keeps crashing.", 1, day, review_id="17"),
                                   make_review("App keeps crashing.", 1, day, review_id="18")])
        self.assertEqual(removed, 0)

    def test_double_export_is_removed(self):
        day = date(2026, 10, 1)
        kept, removed = rt.dedupe([
            make_review("App keeps crashing.", 1, day, reviewer="A.B.", source="App Store"),
            make_review("App keeps crashing.", 1, day, reviewer="A.B.", source="AppFollow")])
        self.assertEqual(removed, 1)

    def test_the_dated_copy_is_kept(self):
        kept, removed = rt.dedupe([make_review("App keeps crashing.", 1, None, reviewer="A.B."),
                                   make_review("App keeps crashing.", 1, date(2026, 10, 1),
                                               reviewer="A.B.")])
        self.assertEqual(removed, 1)
        self.assertEqual(kept[0].day, date(2026, 10, 1))

    def test_keep_duplicates_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = write_csv(tmp, "in.csv", ["review", "rating"],
                            [["I was charged twice.", 1], ["I was charged twice.", 1]])
            out = Path(tmp) / "r.md"
            run_main([str(src), "--themes", str(TEMPLATE), "--out", str(out), "--keep-duplicates"])
            report = out.read_text(encoding="utf-8")
        self.assertIn("Reviews read: 2 (duplicates kept)", report)

    def test_five_thousand_rows_finish_in_a_few_seconds(self):
        rng = random.Random(7)
        bases = ["It was delivered to the wrong address.", "Waited on hold for 40 minutes.",
                 "I work from home so it is easy to check.", "The app crashes on the order screen.",
                 "I was charged twice this month.", "Great beans, simple plan."]
        words = ["roast", "app", "plan", "box", "morning", "beans", "order", "week"]
        rows = []
        for i in range(5000):
            day = "" if i % 5 == 0 else str(date(2026, 5, 1) + timedelta(rng.randrange(200)))
            text = " ".join([rng.choice(bases)] + rng.sample(words, rng.randrange(1, 6)))
            rows.append([day, rng.randrange(1, 6), text])
        with tempfile.TemporaryDirectory() as tmp:
            src = write_csv(tmp, "big.csv", ["date", "rating", "review"], rows)
            started = time.perf_counter()
            code, _, _ = run_main([str(src), "--themes", str(TEMPLATE),
                                   "--out", str(Path(tmp) / "r.md")])
            elapsed = time.perf_counter() - started
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 5.0)


class TaggingTests(unittest.TestCase):
    def test_complaint_is_tagged(self):
        self.assertIn("Billing and refunds", tag("I was charged twice this month.", 2).hits)

    def test_negation_blocks_a_match(self):
        self.assertEqual(tag("The app is not slow at all.", 5).hits, {})
        self.assertEqual(tag("Never had a crash or a bug.", 5).hits, {})

    def test_phrases_that_only_look_negated(self):
        self.assertIn("Slow performance", tag("I can't believe how slow the site is.", 3).hits)
        self.assertIn("Slow performance", tag("The app is not only slow, it crashes.", 3).hits)

    def test_praise_is_not_a_complaint(self):
        for text in ["Support was fast and helpful.", "Delivery arrived a day early.",
                     "Billing is clear, no surprise charges."]:
            self.assertEqual(tag(text, 5).hits, {}, text)

    def test_praise_stays_out_even_with_a_low_rating(self):
        review = tag("Support was fast. The coffee was average.", 3)
        self.assertNotIn("Customer support response", review.hits)

    def test_strong_phrase_counts_next_to_praise(self):
        cases = [("Love the beans, I was charged twice.", 1, "Billing and refunds"),
                 ("Quick question: why was I charged twice?", 2, "Billing and refunds"),
                 ("I would love a refund after being charged twice.", 1, "Billing and refunds"),
                 ("Happy with the coffee, the delivery never arrived.", 2, "Delivery and shipping")]
        for text, rating, theme in cases:
            self.assertIn(theme, tag(text, rating).hits, text)

    def test_negation_is_the_complaint_in_low_reviews(self):
        cases = [("I haven't received my refund yet.", "Billing and refunds"),
                 ("They never refunded me.", "Billing and refunds"),
                 ("I never received my delivery.", "Delivery and shipping"),
                 ("Haven't heard from support in a week.", "Customer support response"),
                 ("I would not recommend the support team.", "Customer support response")]
        for text, theme in cases:
            self.assertIn(theme, tag(text, 1).hits, text)

    def test_negated_praise_in_high_reviews(self):
        for text in ["I've had zero crashes.", "Zero issues with billing.",
                     "I wish I had found this sooner!"]:
            self.assertEqual(tag(text, 5).hits, {}, text)
        self.assertIn("Missing feature", tag("I wish I could pick the roast for each bag.", 3).hits)

    def test_plain_keyword_needs_a_negative_word(self):
        self.assertEqual(tag("The price is listed on the site.", 5).hits, {})
        self.assertEqual(tag("The price is listed on the site.", 2).hits, {})
        self.assertEqual(tag("Delivery came on Tuesday as usual. The roast tasted burnt.", 2).hits, {})
        self.assertEqual(tag("I pay by credit card every month.", 3).hits, {})
        self.assertIn("Price and value", tag("The price is ridiculous.", 5).hits)

    def test_clauses_are_judged_separately(self):
        review = tag("Support was great but billing charged me twice.", 2)
        self.assertEqual(list(review.hits), ["Billing and refunds"])

    def test_shipping_slow_is_not_slow_performance(self):
        review = tag("Shipping is slow.", 2)
        self.assertEqual(list(review.hits), ["Delivery and shipping"])
        self.assertIn("Slow performance", tag("The app is slow.", 2).hits)

    def test_narrowed_starter_words(self):
        self.assertNotIn("Customer support response", tag("Still waiting for a refund.", 1).hits)
        self.assertIn("Customer support response", tag("Still waiting for a reply.", 1).hits)
        self.assertEqual(tag("I bought the value pack, it is fine.", 2).hits, {})

    def test_review_counts_once_per_theme(self):
        review = tag("Charged twice. Billed twice again. Duplicate charge.", 1)
        self.assertEqual(list(review.hits), ["Billing and refunds"])

    def test_negative_flag(self):
        self.assertTrue(tag("Nothing special.", 2).negative)
        self.assertFalse(tag("Lovely coffee.", 5).negative)
        self.assertTrue(tag("This is terrible, I want my money back.", None).negative)

    def test_bad_config_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            path.write_text(json.dumps({"themes": {"X": {"regex": ["("]}}}))
            with self.assertRaises(ValueError):
                rt.load_themes(path)


def themed_reviews(per_period):
    """Make dated reviews. per_period is {iso_date: count} of one complaint each."""
    reviews = []
    for iso, n in per_period.items():
        for i in range(n):
            reviews.append(make_review("I was charged twice number %d." % i, 1, date.fromisoformat(iso)))
    return reviews


class TrendTests(unittest.TestCase):
    def run_analysis(self, reviews, min_trend=5):
        themes = rt.load_themes(TEMPLATE)
        return rt.analyze(reviews, themes, 30, min_trend)

    def test_rising_when_enough_reviews(self):
        reviews = themed_reviews({"2026-01-05": 2, "2026-02-05": 2, "2026-03-05": 2, "2026-04-05": 10})
        stats = self.run_analysis(reviews)[0]
        self.assertEqual(stats[0].trend, "rising")

    def test_small_counts_are_not_called(self):
        reviews = themed_reviews({"2026-01-05": 1, "2026-02-05": 1, "2026-03-05": 1, "2026-04-05": 3})
        stats = self.run_analysis(reviews)[0]
        self.assertEqual(stats[0].trend, "too few to call")

    def test_falling(self):
        reviews = themed_reviews({"2026-01-05": 6, "2026-02-05": 6, "2026-03-05": 6, "2026-04-05": 1})
        stats = self.run_analysis(reviews)[0]
        self.assertEqual(stats[0].trend, "falling")

    def test_short_history(self):
        reviews = themed_reviews({"2026-04-01": 6, "2026-04-10": 6})
        stats = self.run_analysis(reviews)[0]
        self.assertEqual(stats[0].trend, "not enough history")

    def test_zero_reviews_are_never_rising(self):
        self.assertEqual(rt.trend_label(0, 0.0, 3, 0), "too few to call")

    def test_fix_first_prefers_recent_and_low_rated(self):
        old = [make_review("The app is slow today %d." % i, 3, date(2026, 1, 5)) for i in range(6)]
        new = [make_review("I was charged twice again %d." % i, 1, date(2026, 4, 10)) for i in range(5)]
        stats = self.run_analysis(old + new)[0]
        best = max(stats, key=lambda s: s.score)
        self.assertEqual(best.name, "Billing and refunds")


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, rows, extra=None, header=("date", "rating", "review")):
        src = write_csv(self.tmp.name, "in.csv", list(header), rows)
        out = Path(self.tmp.name) / "report.md"
        untagged = Path(self.tmp.name) / "untagged.csv"
        argv = [str(src), "--themes", str(TEMPLATE), "--out", str(out),
                "--untagged", str(untagged)] + (extra or [])
        code, self.stdout, self.stderr = run_main(argv)
        self.assertEqual(code, 0)
        return out.read_text(encoding="utf-8"), untagged

    def test_report_opens_with_fix_this_first(self):
        rows = [["2026-04-%02d" % (i + 1), 1, "I was charged twice, order %d." % i] for i in range(6)]
        report, _ = self.run_cli(rows)
        self.assertTrue(report.startswith("Fix this first: **Billing and refunds**"))
        self.assertIn("in the last 30 days (up to 2026-04-06, the newest review)", report)

    def test_stdout_is_plain_text(self):
        rows = [["2026-04-%02d" % (i + 1), 1, "I was charged twice, order %d." % i] for i in range(6)]
        self.run_cli(rows)
        self.assertTrue(self.stdout.startswith("Fix this first: Billing and refunds."))
        self.assertNotIn("**", self.stdout)

    def test_columns_used_are_reported(self):
        report, _ = self.run_cli([["I was charged twice.", "1/5", "2026-11-01", "Trustpilot"]],
                                 header=("Review Content", "Review Stars", "Review Created (UTC)",
                                         "Source Of Review"))
        line = ('Columns in in.csv: text "Review Content", rating "Review Stars", '
                'date "Review Created (UTC)", source "Source Of Review"')
        self.assertIn("- " + line, report)
        self.assertIn(line, self.stderr)

    def test_no_complaints_found(self):
        report, _ = self.run_cli([["2026-01-01", 5, "Love it."], ["2026-02-01", 5, "Great beans."]])
        self.assertTrue(report.startswith("Fix this first: nothing. No complaints found."))
        self.assertIn("## Themes\n\nNo complaints found.", report)
        self.assertNotIn("| Theme |", report)

    def test_quotes_are_verbatim(self):
        original = "Customer service took four days to respond."
        rows = [["2026-04-01", 2, original + " Extra words follow."],
                ["2026-04-02", 2, "Support never replied to my two emails."]]
        report, _ = self.run_cli(rows)
        self.assertIn('> "%s"' % original, report)
        self.assertIn('> "Support never replied to my two emails."', report)

    def test_quotes_prefer_the_lowest_ratings(self):
        rows = [["2026-04-01", 3, "The app is a bit slow but it works."],
                ["2026-04-02", 1, "The app is so slow that I gave up on my order this morning."],
                ["2026-04-03", 2, "Checkout takes forever to load."],
                ["2026-04-04", 1, "Slow app."]]
        report, _ = self.run_cli(rows)
        quotes = [line for line in report.splitlines() if line.startswith('> "')]
        self.assertTrue(quotes[0].startswith('> "The app is so slow that I gave up'))
        self.assertTrue(quotes[1].startswith('> "Checkout takes forever'))

    def test_quotes_skip_sentences_with_contact_details(self):
        rows = [["2026-04-01", 1, "Charged twice, mail me at jane@example.com about it."],
                ["2026-04-02", 1, "Charged twice on the same card this month."]]
        report, _ = self.run_cli(rows)
        self.assertNotIn("jane@example.com", report)

    def test_untagged_negative_reviews_are_written(self):
        rows = [["2026-04-01", 1, "Tastes burnt and far too dark for me."],
                ["2026-04-02", 5, "Lovely coffee, thank you."],
                ["2026-04-03", 1, "I was charged twice."]]
        _, untagged = self.run_cli(rows)
        with open(untagged, encoding="utf-8", newline="") as handle:
            written = list(csv.DictReader(handle))
        self.assertEqual([r["text"] for r in written], ["Tastes burnt and far too dark for me."])

    def test_untagged_goes_next_to_the_report(self):
        src = write_csv(self.tmp.name, "in.csv", ["rating", "review"],
                        [[1, "Where is my cat picture?"]])
        out = Path(self.tmp.name) / "other" / "r.md"
        code, _, _ = run_main([str(src), "--themes", str(TEMPLATE), "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue((out.parent / "untagged.csv").exists())
        self.assertIn("(see ", out.read_text(encoding="utf-8"))

    def test_untagged_label_is_the_relative_path(self):
        src = write_csv(self.tmp.name, "in.csv", ["rating", "review"],
                        [[1, "Where is my cat picture?"]])
        old = Path.cwd()
        os.chdir(self.tmp.name)
        try:
            code, _, _ = run_main([str(src), "--themes", str(TEMPLATE), "--out", "other/r.md"])
            report = (Path(self.tmp.name) / "other" / "r.md").read_text(encoding="utf-8")
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        self.assertIn("(see other/untagged.csv)", report)
        self.assertTrue((Path(self.tmp.name) / "other" / "untagged.csv").exists())

    def test_unreadable_ratings_are_counted(self):
        rows = [["2026-04-01", "abc", "I was charged twice."],
                ["2026-04-02", "n/a", "App crashes on launch."],
                ["2026-04-03", "", "Support never answered."],
                ["2026-04-04", 4, "Nice and fast."]]
        report, _ = self.run_cli(rows)
        self.assertIn("2 ratings could not be read and were treated as missing.", report)

    def test_one_unreadable_rating_reads_right(self):
        report, _ = self.run_cli([["2026-04-01", "abc", "I was charged twice."]])
        self.assertIn("1 rating could not be read and was treated as missing.", report)

    def test_small_sample_note(self):
        report, _ = self.run_cli([["2026-04-01", 1, "I was charged twice."]])
        self.assertIn("## Not enough data to say", report)
        self.assertIn("Only 1 review.", report)

    def test_history_wording_matches_the_trend_rule(self):
        report, _ = self.run_cli([["2026-04-01", 1, "I was charged twice."]])
        self.assertIn("or under 60 days of history", report)

    def test_thin_themes_use_the_min_trend_count(self):
        rows = [["2026-04-%02d" % (i + 1), 1, "I was charged twice, order %d." % i] for i in range(3)]
        rows.append(["2026-04-05", 1, "The app crashes on launch."])
        report, _ = self.run_cli(rows, ["--min-trend-count", "2"])
        self.assertIn("Fewer than 2 reviews, so too few to rank with confidence: Bugs and crashes.",
                      report)

    def test_min_trend_count_must_be_positive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rt.parse_args(["in.csv", "--min-trend-count", "0"])

    def test_column_options_explain_themselves(self):
        parser_help = io.StringIO()
        with redirect_stdout(parser_help), self.assertRaises(SystemExit):
            rt.parse_args(["--help"])
        for words in ["column with the review text", "column with the star rating",
                      "column with the review date", "--keep-duplicates", "--delimiter"]:
            self.assertIn(words, " ".join(parser_help.getvalue().split()))

    def test_two_files_are_merged_and_deduped(self):
        a = write_csv(self.tmp.name, "a.csv", ["review", "rating"], [["I was charged twice.", 1]])
        b = write_csv(self.tmp.name, "b.csv", ["comment", "stars"], [["I was charged twice.", 1],
                                                                       ["Support never replied.", 1]])
        out = Path(self.tmp.name) / "r.md"
        run_main([str(a), str(b), "--themes", str(TEMPLATE), "--out", str(out),
                  "--untagged", str(Path(self.tmp.name) / "u.csv")])
        report = out.read_text(encoding="utf-8")
        self.assertIn("Reviews read: 2 (1 duplicate removed)", report)


def sample_table(report):
    """Rows of the sample report's theme table, keyed by theme then column name."""
    section = report.split("## Themes", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = {}
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[cells[0]] = dict(zip(header, cells))
    return rows


class DemoTests(unittest.TestCase):
    def test_demo_end_to_end(self):
        data = ROOT / "examples" / "reviews.example.csv"
        self.assertTrue(data.exists())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo.md"
            untagged = Path(tmp) / "untagged.csv"
            code, _, _ = run_main([str(data), "--themes", str(TEMPLATE), "--out", str(out),
                                   "--untagged", str(untagged)])
            self.assertEqual(code, 0)
            report = out.read_text(encoding="utf-8")
        self.assertTrue(report.startswith("Fix this first: **Billing and refunds**"))
        rows = sample_table(report)
        self.assertEqual(rows["Billing and refunds"]["Trend"], "rising")
        self.assertEqual(rows["Delivery and shipping"]["Trend"], "falling")
        self.assertEqual(rows["Slow performance"]["Trend"], "steady")
        most = max(rows.values(), key=lambda r: int(r["Reviews"]))
        self.assertEqual(most["Theme"], "Slow performance")

    def test_committed_sample_report_is_current(self):
        sample = (ROOT / "examples" / "sample-report.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo.md"
            run_main([str(ROOT / "examples" / "reviews.example.csv"), "--themes", str(TEMPLATE),
                      "--out", str(out), "--untagged", str(Path(tmp) / "u.csv")])
            fresh = out.read_text(encoding="utf-8")
        see = r"\(see [^)]*\)"   # the path depends on where untagged.csv went
        self.assertEqual(re.sub(see, "(see X)", sample), re.sub(see, "(see X)", fresh))

    def test_example_data_has_no_emails(self):
        text = (ROOT / "examples" / "reviews.example.csv").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"[\w.]+@[\w.]+\.\w+")

    def test_example_duplicates_are_double_exports(self):
        with open(ROOT / "examples" / "reviews.example.csv", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        seen = {}
        copies = 0
        for row in rows:
            key = rt.normalize_text(row["review"])
            if key in seen:
                copies += 1
                self.assertEqual((seen[key]["date"], seen[key]["reviewer"]),
                                 (row["date"], row["reviewer"]), row["review"])
            seen[key] = row
        self.assertEqual(copies, 8)


class DocsTests(unittest.TestCase):
    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.sample = (ROOT / "examples" / "sample-report.md").read_text(encoding="utf-8")

    def test_readme_quotes_the_sample_report(self):
        first = self.sample.splitlines()[0].replace("**", "")
        self.assertIn(first, self.readme)
        overview = re.search(r"- Reviews read: (\d+) \((\d+) duplicates removed\)", self.sample)
        self.assertIn("Reviews read: %s (%s duplicates removed)" % overview.groups(), self.readme)
        sample_rows = sample_table(self.sample)
        quoted = [line for line in self.readme.splitlines() if line.startswith("| ")]
        header = [c.strip() for c in quoted[0].strip("|").split("|")]
        self.assertGreater(len(quoted), 1)
        for line in quoted[1:]:
            cells = dict(zip(header, (c.strip() for c in line.strip("|").split("|"))))
            for column, value in cells.items():
                self.assertEqual(sample_rows[cells["Theme"]][column], value, (line, column))

    def test_readme_test_count_is_current(self):
        count = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertIn("%d offline tests" % count.countTestCases(), self.readme)

    def test_gitignore_keeps_user_data_out(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for entry in ["out/", "themes.json", "*.csv", "!examples/*.csv"]:
            self.assertIn(entry, lines)
        self.assertNotIn("report.md", lines)

    def test_skill_names_the_real_report_path(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotRegex(skill, r"(?<!out/)`report\.md`")
        self.assertIn("untrusted", skill)
        self.assertIn("200 rows", skill)


if __name__ == "__main__":
    unittest.main()
