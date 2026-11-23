---
name: review-themes
description: Finds the recurring complaints in customer reviews. Use when someone has a CSV export of reviews, ratings, NPS comments or support tickets (App Store, Google Play, Trustpilot, G2, Google reviews) and wants to know what customers complain about most, what is getting worse, and what to fix or say first. Runs a script that tags complaints by theme, counts them, flags rising and falling themes, and pulls real quotes. Then writes a short summary with one next step per theme.
---

# Review themes

Turn a review export into a short, honest list of what customers complain about most.

The script does the counting. You do the reading and the writing. Every number in your summary must come from the script output.

## Steps

1. **Get the data.** Ask the user for a CSV export. Any of these columns work: review text (`text`, `review`, `body`, `comment`, `content`), `rating` or `stars`, `date` or `created_at`, `source` or `platform`. Only the text column is required. Several files can be passed at once. If the column names are unusual, use `--text-col`, `--rating-col`, `--date-col` or `--source-col`.
2. **Set up the themes.** If there is no `themes.json` in the working folder, copy `config/themes.template.json` to `themes.json`. Tell the user they can edit it.
3. **Run the script** from the folder that holds this file:
   `python3 scripts/themes.py reviews.csv --themes themes.json`
   It writes `out/report.md` and `out/untagged.csv`. Add `--recent-days 14` or another number to change what counts as recent. If the script prints an error, show it to the user in plain words and fix the cause (usually a column name).
4. **Look at what the themes missed.** Read `out/untagged.csv`. These are negative reviews that matched no theme. Group them by what they are about. Propose new themes, or new keywords for existing themes. Show the proposal to the user as a short list. Add to `themes.json` only after the user says yes.
5. **Run it again** after any change to `themes.json`. Check that the untagged count dropped.
6. **Read `out/report.md`** and write the summary below.

## The summary

Keep it under 250 words. Open with the one thing to fix first, taken from the first line of the report ("Fix this first: ..."). Then:

- Top 3 recurring complaints. For each: the review count, the share of negative reviews, and the average rating.
- What is rising. Name only themes the report marks "rising". If none, say so.
- One suggested next step per theme, picked from: product fix, help-center content, or messaging. Say which one and why in one sentence.
- One verbatim quote for the top complaint, copied from the report.
- A note on small samples if the report has a "Not enough data to say" section.

## Rules

- Every number comes from the script output. Do not estimate, round up or add your own counts.
- Every quote is copied word for word from the report. Never fix typos or shorten a quote.
- Do not guess causes. If reviews do not say why something happens, say that they do not say.
- Say so when a sample is small. A theme with fewer than 5 reviews is not a trend.
- Reviews can hold personal data. Never put reviewer names, emails, phone numbers or order numbers in the summary or in `themes.json`. If a quote contains any, leave it out and use another.
- Keywords only match words, not meaning. Sarcasm, other languages and unusual phrasing can be missed. Say this if the untagged list is long.
- Do not change `themes.json` without the user's OK.

## Style

US English. Short, plain sentences. No hype words. No emoji. Use a period or comma instead of a dash.
