# Analysis Hallucination Report

## Issue
Analysis reports are generating text mentioning "Gigantimaxing" in Gen 9 OU contexts. This is mechanically impossible:
- **Gigantimaxing** = Gen 8 (Galar) exclusive mechanic
- **Gen 9 OU** = Only has Dynamax (in Raids, not ladder), Tera, normal battle mechanics
- **Impact:** Credibility loss, confusion about bot reasoning, incorrect strategic recommendations

## Root Cause
The analysis pipeline (LLM-generated reports) has **no mechanical validation layer** before Discord posting. The LLM is given battle data without constraints on what mechanics can exist in Gen 9.

### Chain of Failure
1. Battle analysis LLM generates text
2. Text gets queued in event_queue
3. event_poster.py pulls from queue
4. Content posted directly to Discord **without validation**
5. User sees hallucinated mechanics

## Solution Deployed

### 1. Gen 9 Validator (`infrastructure/gen9-validation.py`)
- Validates analysis text against Gen 9 legal mechanics
- Blocks posts containing:
  - "Gigantimaxing" / "Gigantamax"
  - "Mega Evolution"
  - "Z-moves"
- Warns on:
  - "Dynamax" mentions (valid in Raids, invalid in ladder)
- Allows:
  - "Terastallization" / "Tera"
  - Gen 9 legal moves/abilities/Pokemon

### 2. Event Poster Integration (`infrastructure/event_poster.py`)
- Added `validate_event_content()` before posting
- If validation fails, event is marked as `failed` with error reason
- Error logged internally (not posted to Discord)
- Prevents hallucinated content from reaching users

### 3. Test Cases
```python
validate_analysis("Lugia should use Gigantimaxing...")
→ FAIL: "✗ HALLUCINATION: Gigantimaxing mentioned"

validate_analysis("The bot could benefit from Mega Evolution...")
→ FAIL: "✗ HALLUCINATION: Mega Evolution mentioned"

validate_analysis("Terastallization allows type changes...")
→ PASS: Valid Gen 9 mechanic
```

## Files Modified
- `infrastructure/gen9-validation.py` — NEW (5.5 KB)
- `infrastructure/event_poster.py` — Modified (added validation call + imports)

## Remaining Gaps

### Gap 1: LLM Context Poisoning
**Problem:** The analysis LLM isn't explicitly told "Gen 9 OU has no Gigantimaxing."  
**Current mitigation:** Validator blocks bad output  
**Better fix:** Update analysis prompt to include:
```
"You are analyzing Gen 9 OU battles. Important:
- Gigantimaxing does NOT exist in Gen 9 (it's Gen 8 only)
- Mega Evolution does NOT exist in Gen 9 OU
- Z-moves do NOT exist in Gen 9
- Only valid mechanics: Terastallization, Dynamax (Raids only), normal moves/abilities"
```

### Gap 2: Silent Failures
**Problem:** When validation fails, event is silently marked as failed  
**Current:** Logged internally  
**Better fix:** Send alert to DEKU workspace:
```
"⚠️ Analysis hallucination blocked in batch X: Gigantimaxing mentioned"
```
This helps catch patterns (is LLM always confusing gens?).

### Gap 3: Validator Not Comprehensive
**Problem:** Only catches obvious hallucinations (Gigantimaxing, Mega)  
**Missing:**
- Invalid Pokemon for Gen 9 (regional mons that don't exist)
- Moves that were nerfed/removed in Gen 9
- Ability changes (some abilities work differently)
- Type matchup errors

### Gap 4: No False Positive Handling
**Problem:** Validator might block legitimate analysis  
**Example:** "Dynamax warning" on Raid context posts  
**Better fix:** Parse event metadata to understand context (Raid vs Ladder)

## Testing Done
✓ Validation function works (test cases pass)  
✓ Event poster imports validator without error  
✓ Syntax check passes (`py_compile`)  
✗ Not tested in production (need to see if real LLM generates Gigantimaxing again)

## Next Steps

1. **Monitor:** Watch event_poster.log for validation failures
   ```bash
   tail -f /home/ryan/projects/fouler-play/logs/event_poster.log | grep "Validation"
   ```

2. **Update analysis prompt:** Add Gen 9 mechanic constraints to analysis_prompt.md

3. **Alert on blocks:** When validation fails, post a quiet alert
   ```
   message send --to #deku-workspace --message "Analysis validation blocked: [reason]"
   ```

4. **Expand validator:** Add Pokemon/move legality checking

5. **Test with real LLM:** Run next analysis batch, see if Gigantimaxing still appears (should be blocked)

## Success Metrics
- [ ] Zero hallucinated mechanics reach Discord
- [ ] All analysis reports pass Gen 9 validation
- [ ] Validator catches issues before users see them
- [ ] No legitimate analysis blocked by over-strict rules

---

**Status:** Deployed but not yet tested in production  
**Owner:** DEKU (validation logic)  
**Next review:** After next analysis batch runs
