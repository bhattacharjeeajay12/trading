# 🧠 FULL EXECUTION BLUEPRINT — SHORT HORIZON RULE MINING SYSTEM

---

# ⚙️ GLOBAL DEFINITIONS (DO THIS FIRST)

Before ANY experiment, define constants:

TIMEFRAME = 1 minute  
HORIZON = [1, 2, 3 candles]  
TARGET_POINTS = [50, 100, 150]  
SYMBOL = NIFTY  

IMPORTANT:
- Fix these upfront → do NOT keep changing during experiments
- Otherwise results become incomparable

---

# 🧱 PHASE 0 — DATA PIPELINE (DEEP)

---

## Experiment 0.1 — Raw Data Audit (MANDATORY)

### Objective
Eliminate hidden data issues that will destroy model learning.

### Step-by-step

1. Load data into pandas DataFrame:
   columns = [timestamp, open, high, low, close, volume]

2. Sort:
   df = df.sort_values("timestamp")

3. Check duplicates:
   df[df.duplicated("timestamp")]

4. Check missing intervals:
   df['delta'] = df['timestamp'].diff()

   Expected:
   - 1 minute gap

   If gaps:
   - Decide:
     - forward fill? ❌ (dangerous)
     - drop? ✔️ safer

5. Validate OHLC:
   assert high >= max(open, close)
   assert low <= min(open, close)

6. Remove outliers:
   Compute:
   return = close.pct_change()

   If abs(return) > 5% in 1 min → likely bad tick → drop

---

### Expected Output
Clean, continuous time series

---

## Experiment 0.2 — Session Filtering

### Objective
Remove non-tradable noise

### Steps

1. Keep only market hours:
   9:15 → 15:30

2. Remove:
   - Pre-open
   - Illiquid times (optional)

3. Add:
   df["minute_of_day"]

---

### Why this matters
Intraday behavior is NOT stationary across session

---

# 🎯 PHASE 1 — LABEL ENGINEERING (VERY DEEP)

---

## Experiment 1.1 — Barrier Labeling (CORE LOGIC)

### Objective
Simulate real trade outcome

---

### CRITICAL LOGIC (this must be exact)

For each candle t:

1. entry_price = close[t]

2. Define:
   TP = entry_price + target_points  
   SL = entry_price - target_points  

3. Look ahead candles:
   t+1, t+2, t+3

---

### Important nuance (MOST PEOPLE MISS THIS)

Inside EACH future candle:

Check BOTH:

IF high hits TP AND low hits SL in SAME candle:

👉 You must define rule:

Option A (conservative):
- Assume SL hit first

Option B (random):
- Not recommended

Option C (skip sample):
- BEST for clean dataset

---

### Final Label

- TP hit first → Y = 1
- SL hit first → Y = -1
- None hit → Y = 0

---

### Edge Case Handling

- If spread/slippage matters → adjust TP/SL
- If gaps occur → handle explicitly

---

### Expected Output
Column: target_100 (for 100 points)

Repeat for:
- target_50
- target_150

---

## Experiment 1.2 — Multi-Horizon Labeling

### Objective
Capture time sensitivity

---

For same t:

- Horizon 1 → only t+1
- Horizon 2 → t+1 to t+2
- Horizon 3 → t+1 to t+3

---

### Output columns:
- y_1_100
- y_2_100
- y_3_100

---

### Insight
Shorter horizon = more noise  
Longer horizon = smoother signal

---

# 🧩 PHASE 2 — FEATURE ENGINEERING (VERY DETAILED)

---

## Experiment 2.1 — Indicator Computation (WITH PRECAUTIONS)

### Steps

Use TA-lib or manual:

- RSI(14)
- EMA(9), EMA(21)
- ATR(14)
- BB(20,2)
- Stochastic

---

### CRITICAL RULE

👉 NEVER use future data

Ensure:
rolling(window) only uses past

---

### Warmup Removal

First ~50 rows → drop

---

---

## Experiment 2.2 — Normalization (MANDATORY)

---

### ATR normalization

atr_norm = ATR / close

---

### Distance normalization

distance_to_level / ATR

---

### Why?

- Makes model invariant to price scale

---

## Experiment 2.3 — Distance Features (VERY HIGH VALUE)

---

### Compute:

distance_to_pdh = close - PDH  
distance_to_pdl = close - PDL  
distance_to_ema21 = close - EMA21  

---

Normalize ALL:

distance / ATR

---

### Add direction:

sign(distance)

---

---

## Experiment 2.4 — Momentum Features

---

### Compute:

RSI_slope = RSI - RSI.shift(1)  
EMA_slope = EMA21 - EMA21.shift(1)  
ROC = (close - close.shift(3)) / close.shift(3)

---

### Why

You need:
- velocity
- acceleration

---

---

## Experiment 2.5 — Candle Microstructure (DETAILED)

---

### Compute:

range = high - low  
body = abs(close - open)

upper_wick = high - max(open, close)  
lower_wick = min(open, close) - low  

---

### Ratios:

body_ratio = body / range  
upper_wick_ratio = upper_wick / range  
lower_wick_ratio = lower_wick / range  

---

### Add:

is_bullish = close > open  
is_bearish = close < open  

---

### Important patterns:

engulfing:
- bullish engulf:
  close[t] > open[t] AND close[t] > high[t-1]

---

---

## Experiment 2.6 — Volatility Regime

---

### Compute:

ATR_percentile (rolling 100 window)

---

### Buckets:

- low vol → < 30%
- mid → 30–70%
- high → > 70%

---

### Also:

BB_width = (upper - lower) / MA

---

---

## Experiment 2.7 — Trend Encoding (IMPORTANT)

---

### Instead of vague trend:

trend_flag:
- 1 → EMA9 > EMA21
- -1 → EMA9 < EMA21

trend_strength:
= abs(EMA9 - EMA21) / ATR

---

---

## Experiment 2.8 — Multi-Timeframe Features

---

### Steps

1. Create 5-min data
2. Compute:
   - EMA trend
   - RSI

3. Merge back to 1-min

---

### Output:

htf_trend  
htf_rsi  

---

---

# 🤖 PHASE 3 — MODELING (DEEP)

---

## Experiment 3.1 — Train LightGBM

---

### Data Split (VERY IMPORTANT)

DO NOT random split

Use:

Train: first 70%  
Test: last 30%

---

### Model

LightGBMClassifier(
    num_leaves=31,
    max_depth=5,
    learning_rate=0.05,
    n_estimators=200
)

---

### Target

Use:
- y_3_100 (best starting point)

---

### Output

prob_up  
prob_down  

---

---

## Experiment 3.2 — Thresholding

---

Instead of:
predict = 0/1

Use:

- Trade only if:
  prob_up > 0.6

---

---

## Experiment 3.3 — SHAP Analysis

---

### Use:

shap.TreeExplainer(model)

---

### Extract:

- top features
- interaction patterns

---

---

# 🔍 PHASE 4 — RULE EXTRACTION

---

## Experiment 4.1 — Extract Tree Rules

---

### For each tree:

Traverse paths:

IF:
- RSI < 30
- BB_z < -1
- trend_flag = 1

THEN:
- prob_up = high

---

### Store rules

---

---

## Experiment 4.2 — Discretization

---

Convert:

RSI → bins  
ATR → bins  
BB → zones  

---

---

## Experiment 4.3 — FP-Growth

---

### Input

Each row → transaction

---

### Output

Rules like:

{RSI_low, BB_lower, trend_up} → UP

---

---

# 🧬 PHASE 5 — GENETIC ALGORITHM

---

## Objective

Optimize rule thresholds

---

### Chromosome

A rule:

[RSI < x, EMA diff > y, ATR < z]

---

### Fitness

PnL  
Sharpe  
Drawdown  

---

---

# 🧠 PHASE 6 — REGIME MODELING

---

## Cluster market:

Using:
- ATR
- trend strength

---

### KMeans → 3 clusters:

- trending
- ranging
- volatile

---

---

# 🎯 PHASE 7 — META MODEL

---

Second model:

Input:
- prob_up
- features

Output:
- trade / no trade

---

---

# 📈 PHASE 8 — BACKTEST

---

## Walk-forward:

Train → Test → Slide window

---

## Metrics:

- Win rate
- Avg win/loss
- Max DD
- Expectancy

---

---

# 🔥 FINAL SYSTEM

---

You will have:

- ML model (LightGBM)
- Extracted rules
- Regime filters
- Trade filter model

---

# ⚠️ FINAL WARNINGS

---

1. Overfitting is your biggest enemy  
2. Indicators alone won’t give edge  
3. Context > signals  
4. Always validate on unseen future data  

---

# 🚀 END STATE

---

A system that:

- Finds multiple rules  
- Adapts to regimes  
- Filters bad trades  
- Works on short horizon  

---