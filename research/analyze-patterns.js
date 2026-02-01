#!/usr/bin/env node
/**
 * Pattern Analyzer - Enhanced with Win/Loss Learning
 * Analyzes observed high-elo games to extract winning patterns
 * 
 * NEW: Learns from LOSSES of similar teams (what NOT to do)
 * Filters for team archetypes matching our bot
 * 
 * Outputs:
 * - Winning patterns (do this)
 * - Losing patterns (avoid this)
 * - Common mistakes in losses
 * - Team composition trends
 */

const fs = require('fs');
const path = require('path');
const TeamClassifier = require('./team-classifier');

const LOG_DIR = path.join(__dirname, 'observed-games');
const OUTPUT_DIR = path.join(__dirname, 'learned-patterns');

if (!fs.existsSync(OUTPUT_DIR)) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
}

class PatternAnalyzer {
  constructor() {
    this.games = [];
    this.relevantGames = [];
    this.patterns = {
      winningMoves: {},
      losingMoves: {},
      winningOpenings: {},
      losingOpenings: {},
      switchPatterns: { wins: {}, losses: {} },
      lateGamePlays: { wins: {}, losses: {} },
      mistakes: []
    };
  }

  loadGames() {
    console.log('Loading observed games...');
    const files = fs.readdirSync(LOG_DIR).filter(f => f.endsWith('.json'));
    
    for (const file of files) {
      try {
        const data = JSON.parse(fs.readFileSync(path.join(LOG_DIR, file), 'utf8'));
        this.games.push(data);
        
        // Filter for games relevant to our bot's archetype
        if (data.relevantToBot) {
          this.relevantGames.push(data);
        }
      } catch (err) {
        console.error(`Failed to load ${file}:`, err.message);
      }
    }

    console.log(`✅ Loaded ${this.games.length} games (${this.relevantGames.length} relevant to bot)`);
  }

  analyzeOpenings() {
    console.log('\n📊 Analyzing opening moves (wins vs losses)...');
    
    for (const game of this.relevantGames) {
      if (game.turns.length === 0) continue;
      
      const firstTurn = game.turns[0];
      const isWin = game.learningValue === 'WIN_WITH_OUR_ARCHETYPE';
      const isLoss = game.learningValue === 'LOSS_WITH_OUR_ARCHETYPE';
      
      if (!isWin && !isLoss) continue;
      
      for (const event of firstTurn.events) {
        if (event.startsWith('|move|')) {
          const parts = event.split('|');
          const pokemon = parts[2].split(':')[1].trim(); // "p1a: Corviknight" → "Corviknight"
          const move = parts[3];
          
          const key = `${pokemon}:${move}`;
          
          if (isWin) {
            if (!this.patterns.winningOpenings[key]) {
              this.patterns.winningOpenings[key] = 0;
            }
            this.patterns.winningOpenings[key]++;
          } else if (isLoss) {
            if (!this.patterns.losingOpenings[key]) {
              this.patterns.losingOpenings[key] = 0;
            }
            this.patterns.losingOpenings[key]++;
          }
        }
      }
    }

    // Calculate win vs loss rates
    const openingInsights = this.compareWinLoss(
      this.patterns.winningOpenings,
      this.patterns.losingOpenings
    );

    console.log('\n✅ Best opening moves (high win rate):');
    openingInsights.slice(0, 5).forEach(s => {
      console.log(`  ${s.pattern}: ${s.winRate}% (${s.wins} wins, ${s.losses} losses)`);
    });

    console.log('\n❌ Worst opening moves (high loss rate):');
    openingInsights.slice(-5).forEach(s => {
      console.log(`  ${s.pattern}: ${s.winRate}% (${s.wins} wins, ${s.losses} losses)`);
    });

    return openingInsights;
  }

  analyzeSwitchPatterns() {
    console.log('\n📊 Analyzing switch patterns (wins vs losses)...');
    
    const switchReasons = {
      wins: { 'after-ko': 0, 'predicted-counter': 0, 'early-game': 0, 'late-game': 0 },
      losses: { 'after-ko': 0, 'predicted-counter': 0, 'early-game': 0, 'late-game': 0 }
    };

    for (const game of this.relevantGames) {
      const isWin = game.learningValue === 'WIN_WITH_OUR_ARCHETYPE';
      const isLoss = game.learningValue === 'LOSS_WITH_OUR_ARCHETYPE';
      if (!isWin && !isLoss) continue;
      
      const bucket = isWin ? switchReasons.wins : switchReasons.losses;
      
      for (let i = 0; i < game.turns.length; i++) {
        const turn = game.turns[i];
        
        for (const event of turn.events) {
          if (event.startsWith('|switch|')) {
            const turnNum = turn.turn;
            
            if (turn.events.some(e => e.includes('|faint|'))) {
              bucket['after-ko']++;
            } else if (turnNum <= 5) {
              bucket['early-game']++;
            } else if (turnNum > game.turns.length - 5) {
              bucket['late-game']++;
            } else {
              bucket['predicted-counter']++;
            }
          }
        }
      }
    }

    console.log('\nSwitch patterns (wins):');
    Object.entries(switchReasons.wins).forEach(([reason, count]) => {
      console.log(`  ${reason}: ${count}`);
    });

    console.log('\nSwitch patterns (losses):');
    Object.entries(switchReasons.losses).forEach(([reason, count]) => {
      console.log(`  ${reason}: ${count}`);
    });

    return switchReasons;
  }

  analyzeLateGame() {
    console.log('\n📊 Analyzing late-game strategies (wins vs losses)...');
    
    const lateGameWins = {};
    const lateGameLosses = {};
    
    for (const game of this.relevantGames) {
      const isWin = game.learningValue === 'WIN_WITH_OUR_ARCHETYPE';
      const isLoss = game.learningValue === 'LOSS_WITH_OUR_ARCHETYPE';
      if (!isWin && !isLoss) continue;
      
      const bucket = isWin ? lateGameWins : lateGameLosses;
      
      const totalTurns = game.turns.length;
      const lateGameStart = Math.floor(totalTurns * 0.75);
      
      for (let i = lateGameStart; i < game.turns.length; i++) {
        const turn = game.turns[i];
        
        for (const event of turn.events) {
          if (event.startsWith('|move|')) {
            const parts = event.split('|');
            const move = parts[3];
            
            if (!bucket[move]) bucket[move] = 0;
            bucket[move]++;
          }
        }
      }
    }

    const lateGameInsights = this.compareWinLoss(lateGameWins, lateGameLosses);

    console.log('\n✅ Best late-game moves:');
    lateGameInsights.slice(0, 5).forEach(s => {
      console.log(`  ${s.pattern}: ${s.winRate}% (${s.wins} wins, ${s.losses} losses)`);
    });

    console.log('\n❌ Worst late-game moves:');
    lateGameInsights.slice(-5).forEach(s => {
      console.log(`  ${s.pattern}: ${s.winRate}% (${s.wins} wins, ${s.losses} losses)`);
    });

    return lateGameInsights;
  }

  identifyMistakes() {
    console.log('\n📊 Identifying common mistakes in losses...');
    
    const mistakes = [];
    
    // Find patterns that appear MORE in losses than wins
    const allPatterns = new Set([
      ...Object.keys(this.patterns.winningOpenings),
      ...Object.keys(this.patterns.losingOpenings)
    ]);
    
    for (const pattern of allPatterns) {
      const wins = this.patterns.winningOpenings[pattern] || 0;
      const losses = this.patterns.losingOpenings[pattern] || 0;
      
      if (losses > wins && losses >= 2) {
        mistakes.push({
          pattern,
          lossCount: losses,
          winCount: wins,
          avoid: true
        });
      }
    }

    mistakes.sort((a, b) => b.lossCount - a.lossCount);

    console.log('\nCommon mistakes (appear more in losses):');
    mistakes.slice(0, 10).forEach(m => {
      console.log(`  ❌ ${m.pattern}: ${m.lossCount} losses vs ${m.winCount} wins`);
    });

    return mistakes;
  }

  compareWinLoss(winData, lossData) {
    const combined = new Set([...Object.keys(winData), ...Object.keys(lossData)]);
    
    return Array.from(combined)
      .map(pattern => {
        const wins = winData[pattern] || 0;
        const losses = lossData[pattern] || 0;
        const total = wins + losses;
        const winRate = total > 0 ? ((wins / total) * 100).toFixed(1) : 0;
        
        return { pattern, wins, losses, total, winRate: parseFloat(winRate) };
      })
      .filter(s => s.total >= 2) // Min 2 occurrences
      .sort((a, b) => b.winRate - a.winRate);
  }

  saveResults() {
    const results = {
      meta: {
        totalGames: this.games.length,
        relevantGames: this.relevantGames.length,
        winsWithArchetype: this.relevantGames.filter(g => g.learningValue === 'WIN_WITH_OUR_ARCHETYPE').length,
        lossesWithArchetype: this.relevantGames.filter(g => g.learningValue === 'LOSS_WITH_OUR_ARCHETYPE').length,
        analyzedAt: new Date().toISOString()
      },
      openings: this.analyzeOpenings(),
      switches: this.analyzeSwitchPatterns(),
      lateGame: this.analyzeLateGame(),
      mistakes: this.identifyMistakes()
    };

    const outputFile = path.join(OUTPUT_DIR, 'learned-patterns.json');
    fs.writeFileSync(outputFile, JSON.stringify(results, null, 2));
    
    console.log(`\n💾 Saved analysis: ${outputFile}`);
    
    // Create summary
    const summaryFile = path.join(OUTPUT_DIR, 'summary.md');
    this.writeSummary(summaryFile, results);
    console.log(`📝 Saved summary: ${summaryFile}`);
  }

  writeSummary(file, results) {
    const lines = [
      '# High-Elo Pattern Analysis (Fat Team Focus)',
      '',
      `**Total Games:** ${results.meta.totalGames}`,
      `**Relevant Games:** ${results.meta.relevantGames}`,
      `**Wins with Our Archetype:** ${results.meta.winsWithArchetype}`,
      `**Losses with Our Archetype:** ${results.meta.lossesWithArchetype}`,
      `**Analyzed:** ${results.meta.analyzedAt}`,
      '',
      '## ✅ Best Opening Moves (High Win Rate)',
      '',
      ...results.openings.slice(0, 10).map(m => 
        `- ${m.pattern}: ${m.winRate}% (${m.wins}W / ${m.losses}L)`
      ),
      '',
      '## ❌ Avoid These Openings (High Loss Rate)',
      '',
      ...results.openings.slice(-5).map(m =>
        `- ${m.pattern}: ${m.winRate}% (${m.wins}W / ${m.losses}L) - **AVOID**`
      ),
      '',
      '## 🔄 Switch Pattern Insights',
      '',
      '### Winning Games',
      ...Object.entries(results.switches.wins).map(([reason, count]) =>
        `- ${reason}: ${count}`
      ),
      '',
      '### Losing Games',
      ...Object.entries(results.switches.losses).map(([reason, count]) =>
        `- ${reason}: ${count}`
      ),
      '',
      '## 🎯 Best Late-Game Moves',
      '',
      ...results.lateGame.slice(0, 10).map(m =>
        `- ${m.pattern}: ${m.winRate}% (${m.wins}W / ${m.losses}L)`
      ),
      '',
      '## 🚫 Common Mistakes (From Losses)',
      '',
      ...results.mistakes.slice(0, 10).map(m =>
        `- **AVOID:** ${m.pattern} (${m.lossCount} losses vs ${m.winCount} wins)`
      ),
      '',
      '## 💡 Bot Integration Guidance',
      '',
      '### DO:',
      ...results.openings.slice(0, 3).map((m, i) => 
        `${i+1}. ${m.pattern.split(':')[1]} with ${m.pattern.split(':')[0]} (${m.winRate}% win rate)`
      ),
      '',
      '### DON\'T:',
      ...results.mistakes.slice(0, 3).map((m, i) =>
        `${i+1}. ${m.pattern} (appears ${m.lossCount}x in losses)`
      ),
      ''
    ];

    fs.writeFileSync(file, lines.join('\n'));
  }

  run() {
    this.loadGames();
    
    if (this.relevantGames.length === 0) {
      console.log('❌ No relevant games found. Need more fat team replays!');
      return;
    }

    this.saveResults();
    console.log('\n✅ Analysis complete!');
  }
}

const analyzer = new PatternAnalyzer();
analyzer.run();
