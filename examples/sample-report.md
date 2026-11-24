Fix this first: **Billing and refunds**. 25 reviews mention it, 13 in the last 30 days (up to 2026-11-19, the newest review), average rating 1.6.

## Overview

- Files: reviews.example.csv
- Columns in reviews.example.csv: text "review", rating "rating", date "date", source "source", reviewer "reviewer"
- Reviews read: 211 (8 duplicates removed)
- Dates: 2026-05-25 to 2026-11-19 (9 reviews have no date)
- Negative reviews: 147
- Negative reviews that matched no theme: 17 (see out/untagged.csv)

## Themes

| Theme | Reviews | % of negative | Avg rating | Last 30 days | Earlier, per 30 days | Trend | Score |
|---|---|---|---|---|---|---|---|
| Slow performance | 32 | 22% | 2.5 | 6 | 5.1 | steady | 134 |
| Delivery and shipping | 31 | 21% | 2.0 | 1 | 5.7 | falling | 129 |
| Billing and refunds | 25 | 17% | 1.6 | 13 | 2.2 | rising | 165 |
| Bugs and crashes | 9 | 6% | 1.9 | 3 | 1.0 | too few to call | 51 |
| Customer support response | 9 | 6% | 2.0 | 2 | 1.2 | too few to call | 44 |
| Login and account | 9 | 6% | 2.5 | 2 | 1.2 | too few to call | 39 |
| Price and value | 8 | 5% | 2.0 | 1 | 1.2 | too few to call | 35 |
| Cancellation difficulty | 7 | 5% | 1.8 | 1 | 1.0 | too few to call | 33 |
| Missing feature | 7 | 5% | 2.9 | 2 | 1.0 | too few to call | 28 |
| Product quality | 7 | 5% | 2.1 | 1 | 1.0 | too few to call | 31 |
| Onboarding and setup | 3 | 2% | 2.3 | 0 | 0.6 | too few to call | 11 |

A review counts once per theme. Score is the fix-first rule: each review adds (2 if from the last 30 days, else 1) times (6 minus its rating). Trend needs at least 5 reviews in a period.

## Month by month

| Theme | 2026-05 | 2026-06 | 2026-07 | 2026-08 | 2026-09 | 2026-10 | 2026-11 |
|---|---|---|---|---|---|---|---|
| Slow performance | 1 | 3 | 5 | 6 | 6 | 6 | 4 |
| Delivery and shipping | 1 | 7 | 7 | 7 | 5 | 2 | 0 |
| Billing and refunds | 0 | 1 | 1 | 3 | 3 | 7 | 9 |
| Bugs and crashes | 0 | 2 | 0 | 0 | 2 | 2 | 2 |
| Customer support response | 0 | 1 | 1 | 3 | 0 | 1 | 2 |
| Login and account | 1 | 2 | 1 | 1 | 0 | 1 | 2 |
| Price and value | 0 | 3 | 0 | 2 | 0 | 1 | 1 |
| Cancellation difficulty | 0 | 1 | 0 | 1 | 1 | 2 | 1 |
| Missing feature | 0 | 2 | 0 | 1 | 2 | 1 | 1 |
| Product quality | 0 | 1 | 1 | 2 | 1 | 1 | 0 |
| Onboarding and setup | 0 | 1 | 0 | 0 | 1 | 1 | 0 |
| All negative reviews | 3 | 22 | 16 | 29 | 24 | 23 | 22 |
| All reviews | 3 | 32 | 28 | 44 | 34 | 31 | 30 |

## Quotes

### Slow performance

> "Pages are really slow to open on my phone." (1 star, 2026-09, Google)

> "App is realy slow and it crashed on the checkout page." (1 star, 2026-10, Google)

> "Checkout takes forever to load." (2 stars, 2026-07, App Store)

### Delivery and shipping

> "Late delivery every single time." (1 star, 2026-09, App Store)

> "Delivery delayed twice this month." (1 star, 2026-10, Trustpilot)

> "Tracking said delivered but I never got the box." (1 star, 2026-08, App Store)

### Billing and refunds

> "Got billed twice for one bag subscription." (1 star, 2026-10, Trustpilot)

> "I was overcharged by the full price of a box." (1 star, 2026-10, Trustpilot)

> "Charged me twice and the refund is taking weeks." (1 star, 2026-11, Trustpilot)

### Bugs and crashes

> "The app crashes when I open the order screen." (1 star, 2026-11, App Store)

> "App is realy slow and it crashed on the checkout page." (1 star, 2026-10, Google)

> "Blank screen after the latest update." (2 stars, 2026-10, Trustpilot)

### Customer support response

> "Support ignored my question about my plan." (1 star, 2026-11, App Store)

> "Waited on hold for 40 minutes, then the call dropped." (1 star, 2026-08, App Store)

> "Support never replied to my two emails." (2 stars, 2026-07, Trustpilot)

### Login and account

> "Locked out of my account after the update and the reset email never came." (2 stars, 2026-05, Google)

> "Login fails on my laptop but works on my phone." (2 stars, 2026-11, Trustpilot)

> "I can't log in on my phone." (2 stars, 2026-11, Google)

### Price and value

> "I was overcharged by the full price of a box." (1 star, 2026-10, Trustpilot)

> "Honestly overpriced for what you get." (2 stars, 2026-06, Trustpilot)

> "Not worth the money after the last price increase." (2 stars, Trustpilot)

### Cancellation difficulty

> "I could not cancel from the app and had to email." (1 star, Trustpilot)

> "Had to call a phone number just to cancel my subscription." (1 star, 2026-10, Trustpilot)

> "Tried to cancel and could not find the button anywhere." (1 star, 2026-11, App Store)

### Missing feature

> "No option to change the grind size once the order is placed." (2 stars, 2026-11, Google)

> "Please add a way to skip a week." (3 stars, 2026-09, Google)

> "Would be nice to have a gift option." (3 stars, 2026-06, Trustpilot)

### Product quality

> "One of the bags was past its best before date." (2 stars, 2026-08, Trustpilot)

> "Poor quality this month, the bag was half stale." (2 stars, 2026-09, Google)

> "Beans arrived stale, nothing like the roast date on the bag." (2 stars, Trustpilot)

### Onboarding and setup

> "Setup was confusing and the first screen gave no hint where to start." (2 stars, 2026-09, Trustpilot)

> "The instructions are unclear." (2 stars, 2026-10, Google)

> "I could not figure out how to pick my first beans." (3 stars, 2026-06, Trustpilot)

## Not enough data to say

- Fewer than 5 reviews, so too few to rank with confidence: Onboarding and setup.
- No trend called (under 5 reviews in a period, or under 60 days of history): Bugs and crashes, Customer support response, Login and account, Price and value, Cancellation difficulty, Missing feature, Product quality, Onboarding and setup.
- 9 reviews have no date and are left out of trends and months.
