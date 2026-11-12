# JFT formula investigation — 12 November 2026

## Result

The full formula has not been recovered. The previous-session high/low midpoint
is supported by all five reference stocks. A formula that matches one stock is
insufficient: the offsets must match every independent level across symbols and
additional trading days before automatic calculation can be enabled.

The seven plot values contain six distinct levels; the seventh repeats the
fourth. The duplicated plot is checked for fidelity but is not independent
evidence of the formula.

## Confirmed midpoint relationship

Let H and L be the previous completed trading session's high and low.
The observed pair centre is P = (H + L) / 2.

| Symbol | Prior H | Prior L | P | Outer half-width | Inner half-width | Reference half-width |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SBILIFE | 1800.00 | 1748.20 | 1774.10 | 50.80 | 39.40 | 15.00 |
| UPL | 561.75 | 542.60 | 552.175 | 18.555 | 14.575 | 5.555 |
| GODREJIND | 1004.90 | 952.00 | 978.45 | 51.035 | 40.05 | 15.31 |
| DLF | 759.40 | 734.15 | 746.775 | 24.835 | 19.305 | 7.325 |
| NHPC | 80.40 | 77.81 | 79.105 | 2.485 | 1.965 | 0.755 |

Daily OHLC was retrieved from Yahoo Finance's daily chart API for the session
2026-11-11, then converted from binary floats to two-decimal prices. The source
URL for each symbol is preserved in examples/references-2026-11-12.json.
Godrej's outer pair midpoint is 978.465 rather than 978.45 because the upper
value is displayed to one decimal; this is within its displayed precision.
The inner and reference pair centres match 978.45 exactly.

## Candidate that fits SBI but fails the cross-check

With R = H - L, the candidate is:

    Upper outer = P + 0.98 * R
    Lower outer = P - 0.98 * R
    Upper inner = P + 0.76 * R
    Lower inner = P - 0.76 * R
    Lower reference = P - 0.29 * R
    Upper reference = P + 0.29 * R

After rounding to each plot's displayed precision, all seven SBI values match.
The same formula fails the remaining four stocks:

| Symbol | Screenshot upper outer | Candidate upper outer | Difference |
| --- | ---: | ---: | ---: |
| SBILIFE | 1824.9 | 1824.9 | 0.0 |
| UPL | 570.73 | 570.94 | +0.21 |
| GODREJIND | 1029.5 | 1030.3 | +0.8 |
| DLF | 771.61 | 771.52 | -0.09 |
| NHPC | 81.59 | 81.64 | +0.05 |

The saved JSON report contains all predicted values and absolute errors, not
only the upper outer boundary. The candidate is a research hypothesis and is
not called by the live watcher.

## Public source comparison

[Dynamic Range by daytraderph](https://www.tradingview.com/script/jFpI3ckv-Dynamic-Range/)
describes itself as an open-source JFT reconstruction. Its publicly readable
Pine source uses the current day's open and averages of five and ten previous
daily ranges, with four plotted zones. That differs from the midpoint and seven
plots in the supplied screenshots. It cannot be treated as this indicator's
source. No code from that MPL-2.0 publication was incorporated into this project.

## Work still needed

Validate the preceding daily high/low using the same feed and session settings
as TradingView. Add exact plotted values for additional dates, including low-
and high-range days of the same stock. Test rolling-range, volatility, and
other offset rules against held-out cases; a per-symbol fitted coefficient
that simply memorises these screenshots is not a recovered formula.

Broker access and a Telegram delivery check remain unverified until the user
configures the access token, bot token, and destination chat. Meanwhile, supplied
daily chart levels can be monitored directly without implying the formula is
known.
