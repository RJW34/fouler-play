"""Unit checks for _parse_updatesearch_formats (private-room / ladder search tracking).

Run: .venv\\Scripts\\python.exe test_updatesearch_parse.py
Regression guard for the inactivity-forfeit doom loop: the modern |updatesearch|
payload is JSON and must NOT be split on ',' (that made active_searches never
contain the format id, so the search manager could never cancel at capacity).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fp.websocket_client import _parse_updatesearch_formats as p


def check(name, got, want):
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got!r} want={want!r}")
    return ok


def main():
    results = []
    # Modern JSON: actively searching gen9ou (no games yet)
    results.append(check(
        "json_searching_one",
        p('{"searching":["gen9ou"],"games":null}'),
        {"gen9ou"},
    ))
    # Modern JSON: search consumed by a match -> searching empty, game present.
    # THIS is the case the old code got wrong; must be an EMPTY set so the
    # manager knows it is no longer searching.
    results.append(check(
        "json_matched_not_searching",
        p('{"searching":[],"games":{"battle-gen9ou-2654792924-wv3t2e9084k2046cxms12wc81ny8lf0pw":"[Gen 9] OU"}}'),
        set(),
    ))
    # Modern JSON: multiple formats searching.
    results.append(check(
        "json_searching_multi",
        p('{"searching":["gen9ou","gen9randombattle"],"games":null}'),
        {"gen9ou", "gen9randombattle"},
    ))
    # Legacy CSV fallback.
    results.append(check(
        "legacy_csv",
        p('gen9ou,gen9randombattle'),
        {"gen9ou", "gen9randombattle"},
    ))
    # Empty / not searching.
    results.append(check("empty", p(''), set()))
    results.append(check("none", p(None), set()))
    # Malformed JSON must fail closed to empty (never crash the dispatcher).
    results.append(check("malformed_json", p('{"searching":['), set()))

    if all(results):
        print("ALL PASS")
        return 0
    print("FAILURES PRESENT")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
