"""
Autoresearch Framework for Fouler-Play

Structured research-implement-validate cycle for DEKU agents:
1. Analyze — identify the highest-impact improvement target from battle data
2. Research — fetch online resources (Smogon, competitive guides) for the target
3. Implement — make a targeted change to the decision engine
4. Validate — run tests, deploy, measure ELO impact
5. Log — record the full cycle for audit and learning

All research activities are logged to data/autoresearch/research_log.jsonl
for DEKU monitoring and stream proof.
"""
