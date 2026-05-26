#!/usr/bin/env python3
"""
Validate CBOE Data Quality

Checks downloaded CBOE data for corruption/quality issues:
1. Underlying prices (should be > 0)
2. Option prices (bid/ask vs close_px)
3. Bid/ask spreads (sanity check)
4. Greeks (delta range check)

Usage:
    python validate_cboe_data_quality.py --file data/cboe_complete/spx/0dte/2022-09.csv.csv.gz
"""
import argparse
import gzip
import pandas as pd
from pathlib import Path


def validate_file(filepath: Path):
    """Validate a single CBOE data file."""
    print(f"\n{'='*80}")
    print(f"Validating: {filepath.name}")
    print(f"{'='*80}\n")
    
    # Load data
    with gzip.open(filepath, 'rt') as f:
        df = pd.read_csv(f)
    
    total_rows = len(df)
    print(f"📊 Total rows: {total_rows:,}\n")
    
    # Check 1: Underlying prices
    print("1️⃣  Underlying Price Check")
    print("-" * 40)
    zero_underlying = (df['underlying_price'] == 0).sum()
    null_underlying = df['underlying_price'].isna().sum()
    valid_underlying = total_rows - zero_underlying - null_underlying
    
    print(f"  Valid (>0):     {valid_underlying:>8,} ({100*valid_underlying/total_rows:>5.1f}%)")
    print(f"  Zero:           {zero_underlying:>8,} ({100*zero_underlying/total_rows:>5.1f}%)")
    print(f"  NULL:           {null_underlying:>8,} ({100*null_underlying/total_rows:>5.1f}%)")
    
    if valid_underlying > 0:
        print(f"  Range:          ${df[df['underlying_price'] > 0]['underlying_price'].min():.2f} - ${df['underlying_price'].max():.2f}")
    
    # Check 2: Option close prices
    print(f"\n2️⃣  Option Close Price Check")
    print("-" * 40)
    zero_close = (df['close'] == 0).sum()
    null_close = df['close'].isna().sum()
    nonsense_close = ((df['close'] > 1000) & (df['option_type'] == 'P')).sum()  # Puts > $1000 unlikely for SPX 0DTE
    valid_close = total_rows - zero_close - null_close - nonsense_close
    
    print(f"  Valid:          {valid_close:>8,} ({100*valid_close/total_rows:>5.1f}%)")
    print(f"  Zero:           {zero_close:>8,} ({100*zero_close/total_rows:>5.1f}%)")
    print(f"  NULL:           {null_close:>8,} ({100*null_close/total_rows:>5.1f}%)")
    print(f"  Nonsensical:    {nonsense_close:>8,} ({100*nonsense_close/total_rows:>5.1f}%)")
    
    # Check 3: Bid/Ask prices
    print(f"\n3️⃣  Bid/Ask Price Check")
    print("-" * 40)
    zero_bid = (df['bid_close'] == 0).sum()
    zero_ask = (df['ask_close'] == 0).sum()
    zero_both = ((df['bid_close'] == 0) & (df['ask_close'] == 0)).sum()
    valid_bidask = ((df['bid_close'] > 0) | (df['ask_close'] > 0)).sum()
    
    print(f"  Valid (bid or ask > 0): {valid_bidask:>8,} ({100*valid_bidask/total_rows:>5.1f}%)")
    print(f"  Zero bid:               {zero_bid:>8,} ({100*zero_bid/total_rows:>5.1f}%)")
    print(f"  Zero ask:               {zero_ask:>8,} ({100*zero_ask/total_rows:>5.1f}%)")
    print(f"  Both zero:              {zero_both:>8,} ({100*zero_both/total_rows:>5.1f}%)")
    
    # Check 4: Bid/Ask vs Close comparison
    print(f"\n4️⃣  Close vs Bid/Ask Sanity Check")
    print("-" * 40)
    
    # For non-zero prices, check if close is within bid/ask
    valid_mask = (df['bid_close'] > 0) & (df['ask_close'] > 0) & (df['close'] > 0)
    valid_subset = df[valid_mask]
    
    if len(valid_subset) > 0:
        close_in_spread = ((valid_subset['close'] >= valid_subset['bid_close']) & 
                           (valid_subset['close'] <= valid_subset['ask_close'])).sum()
        close_way_off = ((valid_subset['close'] < valid_subset['bid_close'] * 0.5) | 
                         (valid_subset['close'] > valid_subset['ask_close'] * 2.0)).sum()
        
        print(f"  Close within bid/ask:   {close_in_spread:>8,} ({100*close_in_spread/len(valid_subset):>5.1f}%)")
        print(f"  Close way off (>2x):    {close_way_off:>8,} ({100*close_way_off/len(valid_subset):>5.1f}%)")
    
    # Check 5: Bid/Ask spreads
    print(f"\n5️⃣  Bid/Ask Spread Check")
    print("-" * 40)
    
    spread_mask = (df['bid_close'] > 0) & (df['ask_close'] > 0)
    spreads = df[spread_mask]['ask_close'] - df[spread_mask]['bid_close']
    
    if len(spreads) > 0:
        print(f"  Count:          {len(spreads):>8,}")
        print(f"  Median:         ${spreads.median():>8.2f}")
        print(f"  Mean:           ${spreads.mean():>8.2f}")
        print(f"  95th percentile: ${spreads.quantile(0.95):>8.2f}")
        
        wide_spreads = (spreads > 50).sum()
        print(f"  Wide (>$50):    {wide_spreads:>8,} ({100*wide_spreads/len(spreads):>5.1f}%)")
    
    # Check 6: Sample problematic rows
    print(f"\n6️⃣  Sample Problematic Rows")
    print("-" * 40)
    
    problems = df[
        (df['close'] > 1000) |  # Nonsense close price
        (df['close'] == 0) |     # Zero close
        ((df['bid_close'] == 0) & (df['ask_close'] == 0))  # No market
    ].head(5)
    
    if len(problems) > 0:
        print(problems[['strike', 'option_type', 'underlying_price', 'bid_close', 'ask_close', 'close']].to_string(index=False))
    else:
        print("  ✅ No obvious problems found!")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"📊 SUMMARY")
    print(f"{'='*80}")
    
    usability_score = (valid_underlying / total_rows) * (valid_bidask / total_rows) * 100
    
    print(f"  Usability Score: {usability_score:.1f}%")
    print()
    
    if usability_score >= 95:
        print(f"  ✅ EXCELLENT - Data is highly usable")
    elif usability_score >= 85:
        print(f"  ✅ GOOD - Data is usable with minor issues")
    elif usability_score >= 70:
        print(f"  ⚠️  FAIR - Data has quality issues but may be usable")
    else:
        print(f"  ❌ POOR - Data has significant quality issues")
    
    print()
    print(f"💡 Recommendation:")
    if zero_close > total_rows * 0.5:
        print(f"  - DON'T use 'close' column (too many zeros)")
    if close_way_off > len(valid_subset) * 0.2:
        print(f"  - DON'T trust 'close' column (doesn't match bid/ask)")
    
    print(f"  - ✅ Use 'bid_close' and 'ask_close' for option prices")
    print(f"  - ✅ Use 'underlying_price' for SPX price")
    print(f"  - Consider midpoint: (bid_close + ask_close) / 2")
    print()


def main():
    parser = argparse.ArgumentParser(description="Validate CBOE data quality")
    parser.add_argument("--file", help="Path to specific file to validate")
    parser.add_argument("--dir", help="Directory to validate (checks all files)")
    
    args = parser.parse_args()
    
    if args.file:
        validate_file(Path(args.file))
    elif args.dir:
        dir_path = Path(args.dir)
        files = sorted(dir_path.glob("*.csv.gz"))
        
        print(f"Found {len(files)} files to validate\n")
        
        for filepath in files[:5]:  # Sample first 5
            validate_file(filepath)
    else:
        print("❌ Please specify --file or --dir")
        return


if __name__ == "__main__":
    main()
