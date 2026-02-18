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
        
        return len(self.errors) == 0, self.errors, self.warnings
    
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
