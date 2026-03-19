"""
Subscription Mapping Table Converter
Creates a mapping of which option symbols were tracked at each subscription change moment
Works with ANY option depth - automatically detected from logs
"""

import json
import os
import pandas as pd
from pathlib import Path
from datetime import datetime
import re
from typing import List, Dict, Tuple
from collections import defaultdict

# ============================================================================
# CONFIGURATION - Modify these settings
# ============================================================================

# Directory containing your tick logs
LOG_DIRECTORY = "assets/logs/19MAR2026/ticks/"

# Output file name
OUTPUT_FILE = "subscription_mapping.json"

# Output format: 'json', 'csv', or 'both'
OUTPUT_FORMAT = "json"

# ============================================================================


class SubscriptionMappingConverter:
    """Convert subscription events to mapping table"""

    def __init__(self, log_directory: str):
        self.log_directory = Path(log_directory)
        if not self.log_directory.exists():
            raise ValueError(f"Directory not found: {log_directory}")

        # Store subscription events
        self.subscription_events = []

    def parse_subscription_line(self, line: str) -> Dict:
        """
        Parse SUBSCRIBED/UNSUBSCRIBED lines with Nifty price and ATM

        Format: SUBSCRIBED: Token=12345, Symbol=NIFTY24MAR22150CE, Nifty=22165.5, ATM=22200
        """
        # Extract timestamp from log line
        ts_match = re.search(r'^([\d\-: ,]+) - INFO', line)
        if not ts_match:
            return None

        timestamp = ts_match.group(1).strip()

        # Parse subscription event
        sub_match = re.search(
            r'(SUBSCRIBED|UNSUBSCRIBED): Token=(\d+), Symbol=([^,]+), Nifty=([\d.]+), ATM=(\d+)',
            line
        )

        if sub_match:
            return {
                'timestamp': timestamp,
                'event': sub_match.group(1),
                'token': int(sub_match.group(2)),
                'symbol': sub_match.group(3),
                'nifty_price': float(sub_match.group(4)),
                'atm_strike': int(sub_match.group(5))
            }

        return None

    def get_all_log_files(self) -> List[Path]:
        """Get all log files in the directory"""
        log_files = []
        for file in sorted(self.log_directory.glob('*.log*')):
            if file.is_file():
                log_files.append(file)
        return log_files

    def parse_all_subscription_events(self):
        """Parse all subscription events from logs"""
        log_files = self.get_all_log_files()
        print(f"\nFound {len(log_files)} log files")

        for log_file in log_files:
            print(f"Scanning {log_file.name}...")

            with open(log_file, 'r') as f:
                for line in f:
                    event = self.parse_subscription_line(line)
                    if event:
                        self.subscription_events.append(event)

        print(f"\nFound {len(self.subscription_events)} subscription events")

    def group_events_by_change(self) -> List[Dict]:
        """
        Group subscription events by change moment
        Events within the same second are considered part of the same change
        """
        # Group by timestamp rounded to second + nifty_price + atm_strike
        grouped = defaultdict(lambda: {'subscribed': [], 'unsubscribed': [], 'exact_timestamp': None})

        for event in self.subscription_events:
            # Round timestamp to second (remove milliseconds)
            # Example: "2026-03-19 16:26:14,840" -> "2026-03-19 16:26:14"
            timestamp_parts = event['timestamp'].split(',')
            timestamp_rounded = timestamp_parts[0]  # Take only the part before comma/milliseconds

            key = (timestamp_rounded, event['nifty_price'], event['atm_strike'])

            # Keep the first exact timestamp we see for this group
            if grouped[key]['exact_timestamp'] is None:
                grouped[key]['exact_timestamp'] = event['timestamp']

            if event['event'] == 'SUBSCRIBED':
                if event['symbol'] not in grouped[key]['subscribed']:
                    grouped[key]['subscribed'].append(event['symbol'])
            else:
                if event['symbol'] not in grouped[key]['unsubscribed']:
                    grouped[key]['unsubscribed'].append(event['symbol'])

        # Convert to list of changes
        changes = []
        for (timestamp_rounded, nifty_price, atm_strike), events in sorted(grouped.items()):
            changes.append({
                'timestamp': events['exact_timestamp'],  # Use first exact timestamp seen
                'nifty_price': nifty_price,
                'atm_strike': atm_strike,
                'subscribed': events['subscribed'],
                'unsubscribed': events['unsubscribed']
            })

        return changes

    def calculate_option_position(self, symbol: str, atm_strike: int) -> Tuple[str, str]:
        """
        Calculate option position from symbol and ATM

        Returns: (position_type, ce_or_pe)
        position_type: 'ITM-2', 'ITM-1', 'ATM', 'OTM+1', 'OTM+2', etc.
        ce_or_pe: 'CE' or 'PE'
        """
        # Extract strike and option type from symbol
        # Example: NIFTY24MAR22150CE -> strike=22150, type=CE
        match = re.search(r'(\d{5})(CE|PE)', symbol)
        if not match:
            return ('UNKNOWN', 'UNKNOWN')

        strike = int(match.group(1))
        ce_pe = match.group(2)

        # Calculate position relative to ATM
        strike_diff = (strike - atm_strike) // 50  # Nifty has 50-point intervals

        if strike_diff == 0:
            position = 'ATM'
        elif strike_diff > 0:
            # Above ATM
            if ce_pe == 'CE':
                position = f'OTM+{strike_diff}'
            else:  # PE
                position = f'ITM+{strike_diff}'
        else:  # strike_diff < 0
            # Below ATM
            abs_diff = abs(strike_diff)
            if ce_pe == 'CE':
                position = f'ITM-{abs_diff}'
            else:  # PE
                position = f'OTM-{abs_diff}'

        return (position, ce_pe)

    def build_mapping_table(self, changes: List[Dict]) -> pd.DataFrame:
        """
        Build mapping table from subscription changes
        Auto-detects depth from data
        """
        if not changes:
            return pd.DataFrame()

        print("\nBuilding mapping table...")

        # Track currently subscribed symbols
        current_subscriptions = {}  # {symbol: (position, ce_pe)}

        rows = []

        for change in changes:
            timestamp = change['timestamp']
            nifty_price = change['nifty_price']
            atm_strike = change['atm_strike']

            # Remove unsubscribed symbols
            for symbol in change['unsubscribed']:
                if symbol in current_subscriptions:
                    del current_subscriptions[symbol]

            # Add newly subscribed symbols
            for symbol in change['subscribed']:
                position, ce_pe = self.calculate_option_position(symbol, atm_strike)
                current_subscriptions[symbol] = (position, ce_pe)

            # Build row for this subscription change
            row = {
                'timestamp': timestamp,
                'Nifty_Price': nifty_price
            }

            # Add all currently subscribed symbols to row
            for symbol, (position, ce_pe) in current_subscriptions.items():
                col_name = f'{position}_{ce_pe}'
                row[col_name] = symbol

            rows.append(row)

        # Create DataFrame
        df = pd.DataFrame(rows)

        # Auto-detect depth and create proper column order
        if not df.empty:
            # Find all position columns
            position_cols = [col for col in df.columns if col not in ['timestamp', 'Nifty_Price']]

            # Extract depth
            max_itm = 0
            max_otm = 0

            for col in position_cols:
                if 'ITM-' in col or 'ITM+' in col:
                    if 'ITM-' in col:
                        num = int(col.split('-')[1].split('_')[0])
                    else:
                        num = int(col.split('+')[1].split('_')[0])
                    max_itm = max(max_itm, num)
                elif 'OTM-' in col or 'OTM+' in col:
                    if 'OTM-' in col:
                        num = int(col.split('-')[1].split('_')[0])
                    else:
                        num = int(col.split('+')[1].split('_')[0])
                    max_otm = max(max_otm, num)

            depth = max(max_itm, max_otm)

            # Build proper column order with correct CE/PE separation
            ordered_cols = ['timestamp', 'Nifty_Price']

            # Add ITM columns (descending)
            # For CE: ITM is below ATM (ITM-2, ITM-1)
            # For PE: ITM is above ATM (ITM+2, ITM+1)
            for i in range(depth, 0, -1):
                ordered_cols.append(f'ITM-{i}_CE')  # CE strikes below ATM
                ordered_cols.append(f'OTM-{i}_PE')  # PE strikes below ATM (OTM for PE)

            # Add ATM
            ordered_cols.extend(['ATM_CE', 'ATM_PE'])

            # Add OTM columns (ascending)
            # For CE: OTM is above ATM (OTM+1, OTM+2)
            # For PE: ITM is above ATM (ITM+1, ITM+2)
            for i in range(1, depth + 1):
                ordered_cols.append(f'OTM+{i}_CE')  # CE strikes above ATM
                ordered_cols.append(f'ITM+{i}_PE')  # PE strikes above ATM (ITM for PE)

            # Reorder and fill missing columns with None
            df = df.reindex(columns=ordered_cols)

            print(f"\nDetected depth: {depth}")
            print(f"Created {len(df)} rows with {len(ordered_cols)} columns")

        return df

    def save_to_json(self, df: pd.DataFrame, output_file: str):
        """Save to JSON"""
        df.to_json(output_file, orient='records', indent=2)
        print(f"\nSaved to: {output_file}")
        print(f"File size: {os.path.getsize(output_file) / 1024:.2f} KB")

    def save_to_csv(self, df: pd.DataFrame, output_file: str):
        """Save to CSV"""
        df.to_csv(output_file, index=False)
        print(f"\nSaved to: {output_file}")
        print(f"File size: {os.path.getsize(output_file) / 1024:.2f} KB")


def main():
    print("=" * 70)
    print("SUBSCRIPTION MAPPING TABLE CONVERTER")
    print("=" * 70)
    print(f"\nConfiguration:")
    print(f"  Log Directory: {LOG_DIRECTORY}")
    print(f"  Output File: {OUTPUT_FILE}")
    print(f"  Output Format: {OUTPUT_FORMAT}")

    # Initialize converter
    converter = SubscriptionMappingConverter(LOG_DIRECTORY)

    # Parse subscription events
    print("\n" + "=" * 70)
    print("STEP 1: Parsing subscription events")
    print("=" * 70)
    converter.parse_all_subscription_events()

    if not converter.subscription_events:
        print("\n❌ No subscription events found. Check your logs!")
        return

    # Group events by change moment
    print("\n" + "=" * 70)
    print("STEP 2: Grouping events by subscription change")
    print("=" * 70)
    changes = converter.group_events_by_change()
    print(f"Found {len(changes)} subscription change moments")

    # Build mapping table
    print("\n" + "=" * 70)
    print("STEP 3: Building mapping table")
    print("=" * 70)
    mapping_df = converter.build_mapping_table(changes)

    if mapping_df.empty:
        print("\n❌ No mapping data created!")
        return

    # Show sample
    print("\n" + "=" * 70)
    print("Sample data (first 5 rows):")
    print("=" * 70)
    print(mapping_df.head().to_string())

    # Save
    print("\n" + "=" * 70)
    print("STEP 4: Saving mapping table")
    print("=" * 70)
    if OUTPUT_FORMAT in ['json', 'both']:
        converter.save_to_json(mapping_df, OUTPUT_FILE)
    if OUTPUT_FORMAT in ['csv', 'both']:
        csv_output = OUTPUT_FILE.replace('.json', '.csv')
        converter.save_to_csv(mapping_df, csv_output)

    # Show how to load
    print("\n" + "=" * 70)
    print("✓ CONVERSION COMPLETE!")
    print("=" * 70)
    print("\nTo load in pandas:")
    if OUTPUT_FORMAT in ['json', 'both']:
        print(f"\n  import pandas as pd")
        print(f"  df = pd.read_json('{OUTPUT_FILE}')")
    if OUTPUT_FORMAT in ['csv', 'both']:
        csv_output = OUTPUT_FILE.replace('.json', '.csv')
        print(f"\n  import pandas as pd")
        print(f"  df = pd.read_csv('{csv_output}')")

    print("\n" + "=" * 70)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()