#!/usr/bin/env python3
"""Resolve which model an agent runs on, per org-chart.yaml policy. Prints an alias
(opus|sonnet|haiku). Defaults: a department's node uses 'sonnet'; a SUB-TEAM (nested)
defaults to 'haiku' (cheapest that reliably completes the task). An explicit `model:`
on the node always wins."""
import os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        import yaml
        chart = yaml.safe_load(open(os.path.join(REPO, "org-chart.yaml")))
    except Exception:
        print("sonnet")
        return

    found = {"node": None, "team": False}

    def walk(depts, is_team):
        for d in depts or []:
            if d.get("name") == name:
                found["node"], found["team"] = d, is_team
                return True
            if walk(d.get("teams"), True):
                return True
        return False

    walk(chart.get("departments"), False)
    node = found["node"]
    if node and node.get("model"):
        print(node["model"])
    elif found["team"]:
        print("haiku")          # sub-team default: lowest cost
    else:
        print("sonnet")         # department default


if __name__ == "__main__":
    main()
