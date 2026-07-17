#!/usr/bin/env python3
"""metrics.py — query the demand-telemetry store, and record dashboard-only sales by hand.

The store (state/metrics.jsonl, appended by scripts/metrics_watch.py) is the org's
market memory: every observed value of every demand metric, timestamped. This CLI is
how agents and the Chairman read it — and how a human records numbers that only exist
in a dashboard (Spring mug sales, anything without an API):

  metrics.py latest                          current value + as-of for every metric
  metrics.py trend --metric spring.tshirt_sales [--days 30]
                                             last value per day (the trend line)
  metrics.py add --source spring --metric tshirt_sales --value 3 [--note "week 27"]
                                             manual observation — the next watcher
                                             sweep announces it like any API reading
  metrics.py csv                             the whole store as CSV on stdout

Reading is free and always safe; `add` appends one row (never rewrites history).
"""
import argparse
import csv
import sys

from metrics_watch import append_rows, latest_values, load_rows


def cmd_latest():
    latest = latest_values(load_rows())
    if not latest:
        print("no observations yet — run scripts/metrics_watch.py sweep (or metrics.py add)")
        return
    width = max(len(k) for k in latest)
    for key, (value, ts) in sorted(latest.items()):
        print(f"{key:<{width}}  {value:>12}  as of {ts}")


def cmd_trend(metric, days):
    rows = [r for r in load_rows()
            if f"{r.get('source')}.{r.get('metric')}" == metric and r.get("ts")]
    if not rows:
        print(f"no observations for {metric!r} (metrics.py latest lists what exists)")
        return
    by_day = {}
    for r in rows:  # append order — last write per day wins
        by_day[str(r["ts"])[:10]] = r["value"]
    for day in sorted(by_day)[-days:]:
        print(f"{day}  {by_day[day]}")


def cmd_add(source, metric, value, note):
    append_rows([(f"{source}.{metric}", value, note)])
    print(f"recorded {source}.{metric} = {value}"
          + (f" ({note})" if note else "")
          + " — the next metrics-watch sweep announces it")


def cmd_csv():
    w = csv.writer(sys.stdout)
    w.writerow(["ts", "source", "metric", "value", "note"])
    for r in load_rows():
        w.writerow([r.get("ts", ""), r.get("source", ""), r.get("metric", ""),
                    r.get("value", ""), r.get("note", "")])


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("latest")
    t = sub.add_parser("trend")
    t.add_argument("--metric", required=True, help="source.metric key (see `latest`)")
    t.add_argument("--days", type=int, default=30)
    a = sub.add_parser("add")
    a.add_argument("--source", required=True, help="e.g. spring (a `manual` source in metrics.yaml)")
    a.add_argument("--metric", required=True, help="e.g. tshirt_sales")
    a.add_argument("--value", required=True, type=float)
    a.add_argument("--note", default="")
    sub.add_parser("csv")
    args = p.parse_args()
    if args.cmd == "latest":
        cmd_latest()
    elif args.cmd == "trend":
        cmd_trend(args.metric, args.days)
    elif args.cmd == "add":
        value = int(args.value) if args.value == int(args.value) else args.value
        cmd_add(args.source, args.metric, value, args.note)
    elif args.cmd == "csv":
        cmd_csv()


if __name__ == "__main__":
    main()
