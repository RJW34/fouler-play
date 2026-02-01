/**
 * Team Archetype Classifier
 * Categorizes Pokemon teams as Fat/Bulky, Hyper Offense, Balanced, etc.
 * Used to filter observed games for relevant learning
 */

// Pokemon known for bulk/defensive play (common on "fat" teams)
const FAT_MONS = new Set([
  'blissey', 'chansey', 'toxapex', 'corviknight', 'skarmory',
  'ferrothorn', 'slowbro', 'slowking', 'slowkinggalar', 'hippowdon',
  'clefable', 'alomomola', 'dondozo', 'clodsire', 'glowking',
  'greattuks', 'garganacl', 'ting-lu', 'gastrodon', 'gliscor',
  'moltresgalar', 'zapdos', 'mandibuzz', 'quagsire', 'amoonguss'
]);

// Pokemon known for offensive/sweeper roles
const OFFENSE_MONS = new Set([
  'dragapult', 'ironvaliant', 'greattusk', 'roaringmoon', 'chienpa',
  'volcarona', 'gholdengo', 'kingambit', 'baxcalibur', 'dragapult',
  'ironmoth', 'ironbundle', 'meowscarada', 'samurott', 'rillaboom',
  'darkrai', 'greninja', 'ogerpon', 'wellspring', 'hearthflame'
]);

// Speed tiers (avg base speed)
const FAST_THRESHOLD = 100; // Base speed
const SLOW_THRESHOLD = 60;

class TeamClassifier {
  /**
   * Analyze a team's archetype
   * @param {Array<string>} team - Array of Pokemon species names
   * @returns {Object} Classification result
   */
  static classifyTeam(team) {
    const normalized = team.map(mon => this.normalizeName(mon));
    
    const fatCount = normalized.filter(mon => FAT_MONS.has(mon)).length;
    const offenseCount = normalized.filter(mon => OFFENSE_MONS.has(mon)).length;
    
    const fatRatio = fatCount / team.length;
    const offenseRatio = offenseCount / team.length;
    
    // Classify based on composition
    let archetype = 'balanced';
    let confidence = 0.5;
    
    if (fatRatio >= 0.5) {
      archetype = 'fat';
      confidence = fatRatio;
    } else if (fatRatio >= 0.33) {
      archetype = 'semi-fat';
      confidence = fatRatio;
    } else if (offenseRatio >= 0.5) {
      archetype = 'hyper-offense';
      confidence = offenseRatio;
    } else if (offenseRatio >= 0.33) {
      archetype = 'offensive';
      confidence = offenseRatio;
    }
    
    return {
      archetype,
      confidence,
      fatCount,
      offenseCount,
      team: normalized,
      isFat: archetype === 'fat' || archetype === 'semi-fat',
      isOffensive: archetype === 'hyper-offense' || archetype === 'offensive'
    };
  }

  /**
   * Check if a team matches our bot's archetype
   * @param {Array<string>} team 
   * @param {string} botArchetype - Our bot's team style
   * @returns {boolean}
   */
  static matchesArchetype(team, botArchetype = 'fat') {
    const classification = this.classifyTeam(team);
    
    if (botArchetype === 'fat') {
      return classification.isFat;
    } else if (botArchetype === 'offense') {
      return classification.isOffensive;
    }
    
    return true; // Default: accept all for balanced
  }

  /**
   * Normalize Pokemon name for comparison
   * Handles forms, hyphens, etc.
   */
  static normalizeName(name) {
    return name
      .toLowerCase()
      .replace(/[^a-z]/g, '')
      .replace(/galar$/, 'galar')
      .replace(/alola$/, 'alola')
      .replace(/mega$/, '')
      .replace(/primal$/, '');
  }

  /**
   * Extract team from battle data
   * @param {Object} battleData 
   * @returns {Object} { p1Team, p2Team }
   */
  static extractTeams(battleData) {
    const p1Team = new Set();
    const p2Team = new Set();
    
    for (const turn of battleData.turns) {
      for (const event of turn.events) {
        if (event.startsWith('|switch|') || event.startsWith('|drag|')) {
          const parts = event.split('|');
          const player = parts[2]; // "p1a: Corviknight" format
          const pokemonInfo = parts[3]; // "Corviknight, L78, M"
          
          const species = pokemonInfo.split(',')[0].trim();
          
          if (player.startsWith('p1')) {
            p1Team.add(species);
          } else if (player.startsWith('p2')) {
            p2Team.add(species);
          }
        }
      }
    }
    
    return {
      p1Team: Array.from(p1Team),
      p2Team: Array.from(p2Team),
      p1Classification: this.classifyTeam(Array.from(p1Team)),
      p2Classification: this.classifyTeam(Array.from(p2Team))
    };
  }
}

module.exports = TeamClassifier;
