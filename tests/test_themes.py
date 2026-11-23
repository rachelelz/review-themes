"""Offline tests. Run from the repo root: python3 -m unittest discover tests"""
import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import themes as rt  # noqa: E402

TEMPLATE = ROOT / "config" / "themes.template.json"


def write_csv(directory, name, header, rows, encoding="utf-8"):
    path = Path(directory) / name
    with open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def make_review(text, rating=None, day=None):
    return rt.Review(raw=text, text=rt.clean_text(text), rating=rating, day=day, source="")


def tag(text, rating=None, theme_file=TEMPLATE):
    review = make_review(text, rating)
    rt.tag_review(review, rt.load_themes(theme_file))
    return review


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

    def test_bom_and_multiline_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.csv"
            path.write_bytes(("\ufeffReview,Rating,Date\n"
                              '"Line one.\nLine two, with a comma.",2,2026-03-04\n'
                              "No rating or date here,,\n").encode("utf-8"))
            reviews = rt.load_reviews([path])
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0].text, "Line one. Line two, with a comma.")
        self.assertEqual(reviews[0].rating, 2.0)
        self.assertEqual(reviews[0].day, date(2026, 3, 4))
        self.assertIsNone(reviews[1].rating)
        self.assertIsNone(reviews[1].day)

    def test_date_formats(self):
        self.assertEqual(rt.parse_date("2026-03-04T10:11:12Z"), date(2026, 3, 4))
        self.assertEqual(rt.parse_date("03/04/2026"), date(2026, 3, 4))
        self.assertEqual(rt.parse_date("Mar 4, 2026 10:30 AM"), date(2026, 3, 4))
        self.assertIsNone(rt.parse_date("not a date"))

    def test_ten_point_ratings_are_halved(self):
        reviews = [make_review("a", 10), make_review("b", 4)]
        note = rt.scale_ratings(reviews)
        self.assertEqual([r.rating for r in reviews], [5.0, 2.0])
        self.assertIn("halved", note)


class DedupeTests(unittest.TestCase):
    def test_exact_and_near_exact_copies_are_removed(self):
        reviews = [make_review("The app is slow and keeps freezing on my phone.", 2),
                   make_review("The app is slow and keeps freezing on my phone.", 2),
                   make_review("THE APP IS SLOW AND KEEPS FREEZING ON MY PHONE!!", 2),
                   make_review("Delivery was late twice this month.", 2)]
        kept, removed = rt.dedupe(reviews)
        self.assertEqual(removed, 2)
        self.assertEqual(len(kept), 2)

    def test_short_reviews_that_differ_by_a_digit_are_kept(self):
        kept, removed = rt.dedupe([make_review("Charged twice, order 1.", 1),
                                   make_review("Charged twice, order 2.", 1)])
        self.assertEqual(removed, 0)

    def test_same_words_with_different_ratings_are_kept(self):
        kept, removed = rt.dedupe([make_review("Fine overall, nothing special.", 3),
                                   make_review("Fine overall, nothing special.", 5)])
        self.assertEqual(removed, 0)
        self.assertEqual(len(kept), 2)


class TaggingTests(unittest.TestCase):
    def test_complaint_is_tagged(self):
        self.assertIn("Billing and refunds", tag("I was charged twice this month.", 2).hits)

    def test_negation_blocks_a_match(self):
        self.assertEqual(tag("The app is not slow at all.", 5).hits, {})
        self.assertEqual(tag("Never had a crash or a bug.", 5).hits, {})

    def test_phrases_that_only_look_negated(self):
        self.assertIn("Slow performance", tag("I can't believe how slow the site is.", 2).hits)
        self.assertIn("Slow performance", tag("The app is not only slow, it crashes.", 1).hits)

    def test_praise_is_not_a_complaint(self):
        for text in ["Support was fast and helpful.", "Delivery arrived a day early.",
                     "Billing is clear, no surprise charges."]:
            self.assertEqual(tag(text, 5).hits, {}, text)

    def test_praise_stays_out_even_with_a_low_rating(self):
        review = tag("Support was fast. The coffee was average.", 3)
        self.assertNotIn("Customer support response", review.hits)

    def test_plain_keyword_needs_a_low_rating_or_negative_words(self):
        self.assertEqual(tag("The price is listed on the site.", 5).hits, {})
        self.assertIn("Price and value", tag("The price is listed on the site.", 2).hits)
        self.assertIn("Price and value", tag("The price is ridiculous.", 5).hits)

    def test_clauses_are_judged_separately(self):
        review = tag("Support was great but billing charged me twice.", 2)
        self.assertEqual(list(review.hits), ["Billing and refunds"])

    def test_shipping_slow_is_not_slow_performance(self):
        review = tag("Shipping is slow.", 2)
        self.assertEqual(list(review.hits), ["Delivery and shipping"])
        self.assertIn("Slow performance", tag("The app is slow.", 2).hits)

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

    def run_cli(self, rows, extra=None):
        src = write_csv(self.tmp.name, "in.csv", ["date", "rating", "review"], rows)
        out = Path(self.tmp.name) / "report.md"
        untagged = Path(self.tmp.name) / "untagged.csv"
        argv = [str(src), "--themes", str(TEMPLATE), "--out", str(out),
                "--untagged", str(untagged)] + (extra or [])
        with redirect_stdout(io.StringIO()):
            code = rt.main(argv)
        self.assertEqual(code, 0)
        return out.read_text(encoding="utf-8"), untagged

    def test_report_opens_with_fix_this_first(self):
        rows = [["2026-04-%02d" % (i + 1), 1, "I was charged twice, order %d." % i] for i in range(6)]
        report, _ = self.run_cli(rows)
        self.assertTrue(report.startswith("Fix this first: **Billing and refunds**"))

    def test_quotes_are_verbatim(self):
        original = "Customer service took four days to respond."
        rows = [["2026-04-01", 2, original + " Extra words follow."],
                ["2026-04-02", 2, "Support never replied to my two emails."]]
        report, _ = self.run_cli(rows)
        self.assertIn('> "%s"' % original, report)
        self.assertIn('> "Support never replied to my two emails."', report)

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

    def test_small_sample_note(self):
        report, _ = self.run_cli([["2026-04-01", 1, "I was charged twice."]])
        self.assertIn("## Not enough data to say", report)
        self.assertIn("Only 1 reviews", report)

    def test_two_files_are_merged_and_deduped(self):
        a = write_csv(self.tmp.name, "a.csv", ["review", "rating"], [["I was charged twice.", 1]])
        b = write_csv(self.tmp.name, "b.csv", ["comment", "stars"], [["I was charged twice.", 1],
                                                                       ["Support never replied.", 1]])
        out = Path(self.tmp.name) / "r.md"
        with redirect_stdout(io.StringIO()):
            rt.main([str(a), str(b), "--themes", str(TEMPLATE), "--out", str(out),
                     "--untagged", str(Path(self.tmp.name) / "u.csv")])
        report = out.read_text(encoding="utf-8")
        self.assertIn("Reviews read: 2 (1 duplicates removed)", report)


class DemoTests(unittest.TestCase):
    def test_demo_end_to_end(self):
        data = ROOT / "examples" / "reviews.example.csv"
        self.assertTrue(data.exists())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo.md"
            untagged = Path(tmp) / "untagged.csv"
            with redirect_stdout(io.StringIO()):
                code = rt.main([str(data), "--themes", str(TEMPLATE), "--out", str(out),
                                "--untagged", str(untagged)])
            self.assertEqual(code, 0)
            report = out.read_text(encoding="utf-8")
        self.assertTrue(report.startswith("Fix this first: **Billing and refunds**"))
        table_lines = [l for l in report.splitlines() if l.startswith("| Billing and refunds | 25")]
        self.assertTrue(table_lines and "rising" in table_lines[0])
        self.assertTrue(any(l.startswith("| Delivery and shipping") and "falling" in l
                            for l in report.splitlines()))
        self.assertTrue(any(l.startswith("| Slow performance") and "steady" in l
                            for l in report.splitlines()))

    def test_committed_sample_report_is_current(self):
        sample = (ROOT / "examples" / "sample-report.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo.md"
            with redirect_stdout(io.StringIO()):
                rt.main([str(ROOT / "examples" / "reviews.example.csv"), "--themes", str(TEMPLATE),
                         "--out", str(out), "--untagged", str(Path(tmp) / "u.csv")])
            fresh = out.read_text(encoding="utf-8")
        self.assertEqual(sample, fresh)

    def test_example_data_has_no_emails(self):
        text = (ROOT / "examples" / "reviews.example.csv").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"[\w.]+@[\w.]+\.\w+")


if __name__ == "__main__":
    unittest.main()
