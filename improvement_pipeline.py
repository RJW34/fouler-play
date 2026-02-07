#!/usr/bin/env python3
"""
Fouler-Play Improvement Pipeline
Aggregates loss analyses, counts pattern frequencies, cross-references
with existing heuristics, and outputs structured improvement TODOs.
"""

import json
import glob
import os
import re
from collections import Counter
from pathlib import Path


LOSSES_DIR = Path("/home/ryan/projects/fouler-play/replay_analysis/losses")
SEARCH_MAIN = Path("/home/ryan/projects/fouler-play/fp/search/main.py")
CONSTANTS = Path("/home/ryan/projects/fouler-play/constants.py")
OUTPUT_FILE = Path("/home/ryan/projects/fouler-play/replay_analysis/improvement_todo.json")


def get_existing_heuristics() -> dict:
    """Scan main.py and constants.py for existing penalty/detection patterns"""
    heuristics = {}

    if SEARCH_MAIN.exists():
        content = SEARCH_MAIN.read_text()
        # Find penalty application patterns
        for match in re.finditer(r'has_(\w+)\s*[:=]', content):
            heuristics[match.group(1)] = {
                "file": str(SEARCH_MAIN),
                "type": "detection",
            }
        for match in re.finditer(r'ABILITY_PENALTY_(\w+)', content):
            heuristics[f"penalty_{match.group(1).lower()}"] = {
                "file": str(SEARCH_MAIN),
                "type": "penalty",
            }

    if CONSTANTS.exists():
        content = CONSTANTS.read_text()
        for match in re.finditer(r'POKEMON_COMMONLY_(\w+)', content):
            heuristics[f"pokemon_list_{match.group(1).lower()}"] = {
                "file": str(CONSTANTS),
                "type": "pokemon_list",
            }

    return heuristics


def aggregate_losses(n: int = 10) -> dict:
    """Aggregate patterns from the last N losses"""
    files = sorted(
        glob.glob(str(LOSSES_DIR / "*.json")),
        key=os.path.getmtime,
        reverse=True,
    )[:n]

    if not files:
        return {"count": 0, "patterns": {}, "severities": {}}

    patterns = Counter()
    severities = {}
    examples = {}

    for f in files:
        try:
            data = json.load(open(f))
            replay_url = data.get("replay_url", "")
            for mistake in data.get("mistakes", []):
                desc = mistake.get("description", "unknown")
                severity = mistake.get("severity", "medium")
                # Normalize to group similar patterns
                key = desc[:80]
                patterns[key] += 1
                severities[key] = severity
                if key not in examples:
                    examples[key] = []
                examples[key].append(replay_url)
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "count": len(files),
        "patterns": dict(patterns.most_common(20)),
        "severities": severities,
        "examples": {k: v[:3] for k, v in examples.items()},
    }


def generate_improvement_todo(n_losses: int = 10) -> list:
    """Generate structured improvement TODOs"""
    existing = get_existing_heuristics()
    loss_data = aggregate_losses(n_losses)

    if loss_data["count"] == 0:
        return []

    todos = []
    for pattern, frequency in loss_data["patterns"].items():
        severity = loss_data["severities"].get(pattern, "medium")
        replays = loss_data["examples"].get(pattern, [])

        # Check if any existing heuristic covers this
        covered_by = None
        pattern_lower = pattern.lower()
        for heuristic_name, info in existing.items():
            # Simple keyword matching
            for keyword in heuristic_name.split("_"):
                if len(keyword) > 3 and keyword in pattern_lower:
                    covered_by = heuristic_name
                    break

        todos.append({
            "pattern": pattern,
            "frequency": frequency,
            "severity": severity,
            "covered_by_existing": covered_by,
            "needs_new_heuristic": covered_by is None,
            "example_replays": replays,
            "suggested_action": (
                f"Improve existing '{covered_by}' heuristic"
                if covered_by
                else "Add new detection/penalty in fp/search/main.py"
            ),
        })

    # Sort: uncovered patterns first, then by frequency
    todos.sort(key=lambda t: (0 if t["needs_new_heuristic"] else 1, -t["frequency"]))

    return todos


def run_pipeline(n_losses: int = 10):
    """Run the full pipeline and write output"""
    todos = generate_improvement_todo(n_losses)

    output = {
        "generated_at": str(Path("/tmp/fp-pipeline-timestamp").stat().st_mtime
                           if Path("/tmp/fp-pipeline-timestamp").exists()
                           else "unknown"),
        "loss_count": n_losses,
        "improvements": todos,
        "summary": {
            "total_patterns": len(todos),
            "uncovered": sum(1 for t in todos if t["needs_new_heuristic"]),
            "covered_but_recurring": sum(1 for t in todos if not t["needs_new_heuristic"]),
        },
    }

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Pipeline output written to {OUTPUT_FILE}")
    print(f"  {output['summary']['total_patterns']} patterns found")
    print(f"  {output['summary']['uncovered']} need new heuristics")
    print(f"  {output['summary']['covered_but_recurring']} have existing coverage but keep recurring")

    # Print top 5
    for i, todo in enumerate(todos[:5], 1):
        status = "NEW" if todo["needs_new_heuristic"] else f"EXISTS:{todo['covered_by_existing']}"
        print(f"  {i}. [{todo['severity'].upper()}] x{todo['frequency']} [{status}] {todo['pattern'][:60]}")

    return output


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    run_pipeline(n)
