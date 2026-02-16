# Polymarket Monitor: US Strikes Iran

A news monitoring tool that returns `true` or `false` based on whether credible sources report that the US has struck Iran. Built for the [Polymarket event](https://polymarket.com/event/us-strikes-iran-by).

## How It Works

### Architecture

The monitor uses a two-layer approach to balance speed with API cost:

```
Layer 1: Google News RSS (free, unlimited)
   |
   |-- Polls 8 different search queries every cycle
   |-- Deduplicates results across queries
   |-- Filters to articles from the last 24 hours
   |-- Scores each article with regex pattern matching
   |
   |-- If positive signals found:
   v
Layer 2: NewsData.io API (200 credits/day, optional)
   |
   |-- Deep search for confirmation
   |-- Scores and merges with RSS results
   |
   v
Decision Engine --> { "result": true/false, "confidence": 0.0-1.0 }
```

### Scoring Engine

Each article is scored using two sets of regex patterns:

**Positive signals** - phrases that indicate a real strike occurred:
- "US strikes Iran", "US bombs Iran", "airstrikes on Iran"
- "Iran hit by US", "Pentagon strikes Iran"
- Cruise/ballistic missile references targeting Iran

**Negative signals** - phrases that indicate speculation, not a real event:
- Speculative language: "could strike", "may strike", "considering", "preparing to"
- Wrong event type: "cyberattack", "sanctions", "ground troops", "naval"
- Non-events: "intercepted", "shot down"
- Meta-content: "betting odds", "polymarket", "prediction market", "analysis"

The **net score** is calculated as:
```
net_score = (positive_hits * credibility_boost) - (negative_hits * 1.5)
```

Where `credibility_boost` is **2.0** for sources like Reuters, AP, BBC, CNN, Al Jazeera, etc., and **1.0** for everything else.

### Decision Thresholds (Aggressive Mode)

The evaluation uses a tiered system that prioritizes speed:

| Tier | Condition | Confidence | What it means |
|------|-----------|------------|---------------|
| 1 | Single credible source, net score >= 3 | 90% | Strong confirmed signal |
| 2 | 2+ credible sources, any positive score | 80% | Multiple outlets reporting |
| 3 | 1 credible source, any positive score | 60% | Early signal, verify manually |
| 4 | 3+ non-credible sources positive | 40% | Possible breaking news, unconfirmed |
| -- | Anything else | 0% | **Result: false** |

### Noise Filtering

Three filters reduce false positives:

1. **Recency filter** - Only articles from the last 24 hours are considered. Old scenario pieces and analysis articles are dropped.
2. **Source blocklist** - Articles from Polymarket, PredictIt, Kalshi, and Metaculus are excluded (self-referencing noise).
3. **Negative signal penalty** - Speculative and opinion language is penalized at 1.5x weight, so a headline like "Will the US strike Iran?" gets a negative net score even though it contains "US strike Iran".

### Resolution Criteria (from Polymarket)

The market resolves YES if:
- The US initiates a **drone, missile, or air strike** on Iranian soil or any official Iranian embassy/consulate
- Must be **aerial bombs, drones, or missiles** (cruise or ballistic) launched by US military forces

Does NOT count:
- Intercepted missiles or drones
- Cyberattacks
- Ground incursions or artillery
- Naval shelling
- Operations by US ground operatives

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# (Optional) Add NewsData.io API key for deeper search
cp .env.example .env
# Edit .env and add your key from https://newsdata.io
```

## Usage

```bash
# Single check, RSS only (free, no API key needed)
python3 monitor.py --once --no-newsdata

# Single check with NewsData.io confirmation
python3 monitor.py --once

# Continuous monitoring every 5 minutes
python3 monitor.py

# Faster polling (every 2 minutes)
python3 monitor.py --interval 120

# RSS-only continuous monitoring
python3 monitor.py --interval 120 --no-newsdata
```

## Output

```json
{
  "timestamp": "2026-02-16T18:42:54+00:00",
  "result": false,
  "confidence": 0.0,
  "reason": "No credible signals (2 weak hits)",
  "top_articles": [
    {
      "title": "Trump told Netanyahu he would support Israeli missile strikes on Iran",
      "source": "rbc-ukraine",
      "net_score": 1.0,
      "is_credible": false
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `result` | `true` if a US strike on Iran is detected, `false` otherwise |
| `confidence` | 0.0 to 1.0 — how confident the monitor is in the result |
| `reason` | Human-readable explanation of why it returned this result |
| `top_articles` | The highest-scoring articles that informed the decision |

## Files

```
polymarket-iran-monitor/
├── monitor.py         # Main monitoring script
├── requirements.txt   # Python dependencies
├── .env.example       # Template for API key
└── README.md          # This file
```

## Credible Sources

The monitor gives 2x weight to articles from these outlets:

Reuters, Associated Press, BBC, CNN, Al Jazeera, New York Times, Washington Post, Wall Street Journal, The Guardian, Bloomberg, NBC News, ABC News, CBS News, Fox News, Sky News, AFP, DW News, France 24, Times of Israel, Breaking Defense, Defense One, Military Times

## Limitations

- **Not financial advice** - This is a news monitoring tool, not a trading bot.
- **RSS lag** - Google News RSS can be delayed by a few minutes compared to live TV or Twitter/X.
- **Headline-only analysis** - The monitor scores article titles and descriptions, not full article text. A nuanced article with a clickbait headline could trigger a false positive.
- **English only** - Only monitors English-language sources. Breaking news in Farsi or Arabic may appear first in those languages.
