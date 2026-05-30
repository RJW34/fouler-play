#!/usr/bin/env python3
"""
Gen 9 OU Mechanical Validator
Catches hallucinated mechanics before they reach Discord

Validates that analysis mentions:
- Only Gen 9 legal Pokemon
- Only Gen 9 legal moves
- Only Gen 9 legal abilities
- Only Gen 9 legal mechanics (Dynamax, NOT Gigantimaxing/Megas from other gens)

This runs on all analysis output before posting to Discord.
"""

import re
from typing import List, Tuple

from data.pokedex_oracle import oracle as pokedex_oracle

# Gen 9 Forbidden Mechanics (Will never exist in Gen 9 OU)
FORBIDDEN_MECHANICS = {
    "gigantimaxing": "Gigantimaxing is Gen 8 (Galar) exclusive, not in Gen 9",
    "gigantamax": "Gigantamax forms don't exist in Gen 9",
    "mega": "Mega Evolution doesn't exist in Gen 9 OU",
    "dynamax": "Dynamaxing is Gen 8 (Galar) exclusive. Not in Gen 9 ladder play.",
    "z-move": "Z-moves are Gen 7, not in Gen 9",
    # NOTE: terastallization/tera is VALID in Gen 9, removed from forbidden list
}

# Gen 9 OU Reality Checks
GEN9_MECHANICS = [
    "dynamax",  # Exists in Raids, but doesn't apply to ladder OU
    "tera",     # Terastallization (type changing)
    "abilities",  # Gen 9 ability pool
    "moves",      # Gen 9 movepool
]

CLAIM_VALUE_RE = re.compile(r"\b(?P<key>pokemon|species|move|ability|type|types)\s*[:=]\s*(?P<value>[^;\n,]+)", re.IGNORECASE)
IS_TYPE_RE = re.compile(r"\b(?P<species>[A-Z][A-Za-z0-9 .'-]{1,32})\s+is\s+(?:an?\s+)?(?P<type>[A-Za-z]+)[-\s]type\b")
HAS_ABILITY_RE = re.compile(
    r"\b(?P<species>[A-Z][A-Za-z0-9 .'-]{1,32})\s+(?:has|gets|uses|with)\s+(?:the\s+)?(?P<ability>[A-Z][A-Za-z0-9 .'-]{1,40}?)(?:\s+ability)?\b"
)
MOVE_EFFECTIVENESS_RE = re.compile(
    r"\b(?P<move>[A-Z][A-Za-z0-9 .'-]{1,32}?)\s+(?:hits|damages)\s+(?P<target>[A-Z][A-Za-z0-9 .'-]{1,32}?)\s+(?P<claim>super effectively|not very effectively|for no damage|with no effect|neutrally|for neutral damage)\b"
)


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _claim_values(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for match in CLAIM_VALUE_RE.finditer(text or ""):
        key = match.group("key").lower()
        value = match.group("value").strip().strip("`* ")
        values.setdefault(key, []).append(value)
    return values

class Gen9Validator:
    """Validates analysis text for Gen 9 mechanical accuracy."""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
    
    def validate_analysis(self, text: str) -> Tuple[bool, List[str], List[str]]:
        """
        Check if analysis contains illegal mechanics.
        Returns: (is_valid, error_list, warning_list)
        """
        self.errors = []
        self.warnings = []
        
        text_lower = text.lower()
        
        # Check for forbidden mechanics
        for mechanic, reason in FORBIDDEN_MECHANICS.items():
            if mechanic in text_lower:
                # All Gen 8+ exclusive mechanics are errors in Gen 9 context
                self.errors.append(
                    f"✗ HALLUCINATION: '{mechanic}' - {reason}"
                )
        
        # Specific checks
        if "gigantim" in text_lower:
            self.errors.append(
                "✗ HALLUCINATION: Gigantimaxing mentioned in Gen 9 context"
            )
        
        if "mega" in text_lower and "gen 9" in text_lower:
            self.errors.append(
                "✗ HALLUCINATION: Mega Evolution mentioned in Gen 9 context"
            )
        
        if "z-move" in text_lower or "z move" in text_lower:
            self.errors.append(
                "✗ HALLUCINATION: Z-moves mentioned (Gen 7 mechanic, not Gen 9)"
            )

        self._validate_structured_pokemon_claims(text)
        
        return len(self.errors) == 0, self.errors, self.warnings

    def _validate_structured_pokemon_claims(self, text: str) -> None:
        """Validate explicit Pokemon fact claims against local oracle data."""
        values = _claim_values(text)
        species_values = values.get("pokemon", []) + values.get("species", [])
        move_values = values.get("move", [])
        ability_values = values.get("ability", [])
        type_values = values.get("type", []) + values.get("types", [])

        species = species_values[0] if species_values else ""
        dex = pokedex_oracle.pokemon(species) if species else None
        if species and dex is None:
            self.errors.append(f"✗ HALLUCINATION: unknown Pokemon/species claim '{species}'")

        for move in move_values:
            if pokedex_oracle.move(move) is None:
                self.errors.append(f"✗ HALLUCINATION: unknown or illegal Gen 9 move claim '{move}'")

        for ability in ability_values:
            if species and dex is not None and not pokedex_oracle.validate_ability_claim(species, ability):
                self.errors.append(f"✗ HALLUCINATION: '{species}' cannot have ability '{ability}'")
            elif not species:
                self.warnings.append(f"Ability claim '{ability}' has no Pokemon/species context for oracle validation")

        if species and dex is not None and type_values:
            actual_types = {_norm(item) for item in dex.get("types", [])}
            for claimed_type in type_values:
                if _norm(claimed_type) not in actual_types:
                    self.errors.append(
                        f"✗ HALLUCINATION: '{species}' type claim '{claimed_type}' contradicts oracle types {dex.get('types', [])}"
                    )

        for match in IS_TYPE_RE.finditer(text or ""):
            species_name = match.group("species").strip()
            claimed_type = match.group("type").strip()
            dex_for_phrase = pokedex_oracle.pokemon(species_name)
            if dex_for_phrase is None:
                self.errors.append(f"✗ HALLUCINATION: unknown Pokemon/species claim '{species_name}'")
                continue
            actual_types = {_norm(item) for item in dex_for_phrase.get("types", [])}
            if _norm(claimed_type) not in actual_types:
                self.errors.append(
                    f"✗ HALLUCINATION: '{species_name}' type claim '{claimed_type}' contradicts oracle types {dex_for_phrase.get('types', [])}"
                )

        for match in HAS_ABILITY_RE.finditer(text or ""):
            species_name = match.group("species").strip()
            ability = match.group("ability").strip()
            dex_for_phrase = pokedex_oracle.pokemon(species_name)
            if dex_for_phrase is None:
                continue
            if not pokedex_oracle.validate_ability_claim(species_name, ability):
                self.errors.append(f"✗ HALLUCINATION: '{species_name}' cannot have ability '{ability}'")

        for match in MOVE_EFFECTIVENESS_RE.finditer(text or ""):
            move_name = match.group("move").strip()
            target_name = match.group("target").strip()
            claim = match.group("claim").lower()
            move = pokedex_oracle.move(move_name)
            target = pokedex_oracle.pokemon(target_name)
            if move is None or target is None:
                continue
            actual = pokedex_oracle.effectiveness(str(move.get("type", "")), list(target.get("types", [])))
            if not _effectiveness_claim_matches(claim, actual):
                self.errors.append(
                    f"✗ HALLUCINATION: '{move_name}' effectiveness claim against '{target_name}' is '{claim}', but oracle multiplier is {actual}"
                )
    
    def sanitize_analysis(self, text: str) -> str:
        """
        Remove/fix hallucinated mechanics from analysis text.
        WARNING: This is lossy — better to reject and regenerate.
        """
        
        # Replace common hallucinations
        replacements = {
            r"gigantimaxing": "[REMOVED: Gigantimaxing not in Gen 9]",
            r"gigantamax": "[REMOVED: Gigantamax not in Gen 9]",
            r"mega ": "ability to ",  # Lossy but preserves readability
            r"z-move": "[REMOVED: Z-moves not in Gen 9]",
        }
        
        sanitized = text
        for pattern, replacement in replacements.items():
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        
        return sanitized
    
    def report_hallucination(self, analysis_text: str, batch_id: str) -> str:
        """Generate a report of what went wrong."""
        is_valid, errors, warnings = self.validate_analysis(analysis_text)
        
        if not is_valid or warnings:
            report = f"[Gen 9 Validation Report for batch {batch_id}]\n"
            
            if errors:
                report += f"\n❌ ERRORS (Block posting):\n"
                for error in errors:
                    report += f"  {error}\n"
            
            if warnings:
                report += f"\n⚠️ WARNINGS (May need review):\n"
                for warning in warnings:
                    report += f"  {warning}\n"
            
            report += f"\nACTION: Regenerate analysis without these mechanics.\n"
            return report
        
        return ""


def _effectiveness_claim_matches(claim: str, actual: float) -> bool:
    if claim == "super effectively":
        return actual > 1.0
    if claim == "not very effectively":
        return 0.0 < actual < 1.0
    if claim in {"for no damage", "with no effect"}:
        return actual == 0.0
    if claim in {"neutrally", "for neutral damage"}:
        return actual == 1.0
    return True


# Example usage for testing
if __name__ == "__main__":
    validator = Gen9Validator()
    
    # Test cases
    test_cases = [
        (
            "Lugia should use Gigantimaxing to counter this threat",
            "FAIL: Gigantimaxing hallucination (Gen 8 only)"
        ),
        (
            "The bot could benefit from Mega Evolution coverage",
            "FAIL: Mega Evolution hallucination (Gen 8 and prior)"
        ),
        (
            "Dynamax is useful for defensive pivots",
            "FAIL: Dynamax hallucination (Gen 8 only)"
        ),
        (
            "Terastallization allows type changes for coverage moves",
            "PASS: Tera is Gen 9 legal mechanic"
        ),
        (
            "Use Stealth Rock setup and Tera to break stall cores",
            "PASS: Pure Gen 9 mechanics"
        ),
    ]
    
    for text, expected in test_cases:
        is_valid, errors, warnings = validator.validate_analysis(text)
        print(f"\nInput: {text}")
        print(f"Expected: {expected}")
        print(f"Valid: {is_valid}")
        if errors:
            print(f"Errors: {errors}")
        if warnings:
            print(f"Warnings: {warnings}")
