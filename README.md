# review-themes

Finds the complaints that keep coming back in your customer reviews. For each one you get how many reviews mention it, whether it's rising or falling, the average rating of those reviews, and real quotes. The report opens with the one complaint to fix first.

It's built for any CSV with a review text column, such as app store, Trustpilot, G2 or Google review exports, NPS comments or support tickets. The script does the counting, and Claude reads the reviews the script couldn't place and suggests new themes for them.

## The problem

Someone reads through reviews when there's time and reports back that "a few people mentioned billing." That could be 4 reviews or 40, and it's hard to tell whether it's getting worse.

## Try it in two minutes

Python 3.9 or newer is all it needs.

```bash
git clone https://github.com/rachelelz/review-themes
cd review-themes
python3 scripts/themes.py --demo
```

That reads 219 made-up reviews of a fictional coffee subscription and saves the result to `examples/sample-report.md`. Shortened, it starts like this:

```text
Fix this first: Billing and refunds. 25 reviews mention it, 13 in the last 30 days, average rating 1.6.

Reviews read: 211 (8 duplicates removed)
Negative reviews: 147
Negative reviews that matched no theme: 17 (see out/untagged.csv)

| Theme                 | Reviews | Avg rating | Last 30 days | Earlier, per 30 days | Trend   |
| Slow performance      | 32      | 2.5        | 6            | 4.9                  | steady  |
| Delivery and shipping | 31      | 2.0        | 1            | 5.5                  | falling |
| Billing and refunds   | 25      | 1.6        | 13           | 2.2                  | rising  |
```

Slow performance has the most reviews, but billing comes first: it went from about 2 complaints a month to 13 in the last 30 days, and those reviewers gave lower ratings. After the table, each theme gets a month-by-month count and three quotes from the data, like "Got billed twice for one bag subscription."

## Use it on your own reviews

1. Export your reviews as CSV. The script finds the text, rating, date and source columns by their usual names, or you can name them with `--text-col`, `--rating-col` and so on.
2. Copy `config/themes.template.json` to `themes.json`. It starts with eleven common complaint themes (billing, bugs, slow performance, support, delivery, cancellation and more). Edit the words to match your product.
3. Run it:

   ```bash
   python3 scripts/themes.py reviews.csv --themes themes.json
   ```

   The report goes to `out/report.md`, with the unmatched reviews next to it in `out/untagged.csv`.

To use it as a Claude Code skill, clone it into your skills folder:

```bash
git clone https://github.com/rachelelz/review-themes ~/.claude/skills/review-themes
```

```text
Find the recurring complaints in ~/Downloads/app-reviews.csv.
```

Claude runs the script, reads `out/untagged.csv` (the negative reviews that matched no theme), and proposes new themes or words. It only adds them to `themes.json` after you say yes, then re-runs the script and writes a summary of under 250 words: the top three complaints with counts, what's rising, and one suggested next step for each.

## How it works

- Read. The script reads one or more CSVs, handles quoted multi-line reviews and missing ratings or dates, and removes exact and near-exact duplicates.
- Tag. Each review is split into sentences, and each sentence is checked against the theme word lists. A sentence only counts as a complaint when it reads negative, so "support was fast" doesn't count toward the support theme, and "the app is not slow" doesn't count toward slow performance. A review counts once per theme.
- Count. For each theme: reviews, share of negative reviews, average rating, a count per month, and the last 30 days against the average 30 days before that. A trend is only called with at least 5 reviews in a period.
- Rank. Each review in a theme adds points: 6 minus its star rating, doubled if it's from the last 30 days. The theme with the most points is "Fix this first".
- Learn. Negative reviews that match no theme go to `out/untagged.csv`. That's where Claude looks for themes the word lists are missing.

## What it won't do

- Understand meaning. It matches words and phrases. Sarcasm, typos ("carged me twise") and other languages will slip through to the untagged file, which is why Claude reads that file.
- Explain why a complaint is rising. The summary sticks to what reviewers wrote.
- Change a quote. Quotes are copied word for word (only line breaks and extra spaces are collapsed), and sentences containing an email address or phone number are never quoted.

The starter word lists will miss your product's own vocabulary, so expect to spend your first run on real reviews adding words to `themes.json`.

## Privacy

Everything runs on your computer. The script reads your CSV and writes to `out/`. Reviews often include names and contact details. The skill sends the review text Claude reads to your model provider, so strip anything you wouldn't put into an AI tool. It also tells Claude never to put reviewer names or emails in the summary. Files ending in `.private.csv` are git-ignored, so you don't commit real reviews by accident.

## What went wrong while building it

- Praise counted as complaints. "Never had a crash or a bug" on a 5-star review was counted under bugs, because "never" was too far from "bug". Small words like "had" and "a" no longer count toward the distance.
- Words meant different things in different themes. The first word list used "slow" on its own, so "shipping is slow" landed in slow app performance. "Slow" now only counts near words like app, site, page or checkout. The same problem showed up with "first time" (counted as onboarding) and "expired" (counted as product quality), and both came out of the starter list.
- Duplicate removal merged real reviews. Two short reviews that differed by one character, like two order numbers, were treated as the same review. Near-duplicate matching now only applies to reviews of 40 characters or more.
- Some negatives make a complaint stronger. "I can't believe how slow the site is" was missed at first. Phrases like "can't believe" and "not only" no longer cancel a complaint.

## What I'd change next

- Have Claude label the untagged reviews with a theme and a confidence score, and keep those labels in a column you can see and edit, so the counts stay predictable.
- Compare two exports, like this quarter against last quarter, to answer "did the fix work?" after a release.
- Show each theme as a share of that month's reviews, and add a simple significance check so "rising" means more than noise.

## Files

```
SKILL.md                       instructions Claude Code follows
scripts/themes.py              read, de-duplicate, tag, count, rank, report
config/themes.template.json    starter complaint themes to copy and edit
examples/reviews.example.csv   219 made-up reviews
examples/sample-report.md      the demo output
tests/test_themes.py           38 offline tests: python3 -m unittest discover tests
```

## License

MIT. Built with Claude Code by [Rachel Elz](https://github.com/rachelelz).
