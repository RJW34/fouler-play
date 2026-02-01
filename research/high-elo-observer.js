#!/usr/bin/env node
/**
 * High-Elo Game Observer
 * Spectates gen9ou games above 1700 elo to learn winning patterns
 * 
 * Goals:
 * - Watch top player decision-making
 * - Log common switches, predictions, late-game plays
 * - Identify winning team compositions
 * - Build pattern database for bot to learn from
 */

const WebSocket = require('ws');
const fs = require('fs');
const path = require('path');
const TeamClassifier = require('./team-classifier');

const MIN_ELO = 1700;
const FORMAT = 'gen9ou';
const LOG_DIR = path.join(__dirname, 'observed-games');
const BOT_ARCHETYPE = 'fat'; // Our bot uses fat/bulky teams

// Ensure log directory exists
if (!fs.existsSync(LOG_DIR)) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
}

class HighEloObserver {
  constructor() {
    this.ws = null;
    this.observedGames = new Set();
    this.currentBattles = new Map(); // battleId -> game data
  }

  connect() {
    console.log('Connecting to Pokemon Showdown...');
    this.ws = new WebSocket('wss://sim3.psim.us/showdown/websocket');

    this.ws.on('open', () => {
      console.log('✅ Connected to Showdown');
      // Join the gen9ou battle room to see available battles
      this.send(`|/join gen9ou`);
      // Search for battles to spectate
      this.searchHighEloBattles();
    });

    this.ws.on('message', (data) => {
      this.handleMessage(data.toString());
    });

    this.ws.on('close', () => {
      console.log('Disconnected. Reconnecting in 5s...');
      setTimeout(() => this.connect(), 5000);
    });

    this.ws.on('error', (err) => {
      console.error('WebSocket error:', err.message);
    });
  }

  send(message) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(message);
    }
  }

  async searchHighEloBattles() {
    // Request list of current battles
    this.send('|/cmd roomlist');
    
    // Periodically refresh battle list
    setInterval(() => {
      this.send('|/cmd roomlist');
    }, 30000); // Every 30 seconds
  }

  handleMessage(data) {
    const lines = data.split('\n');
    
    for (const line of lines) {
      if (line.startsWith('|queryresponse|')) {
        this.handleQueryResponse(line);
      } else if (line.startsWith('>')) {
        // Battle room message
        const battleId = line.slice(1);
        this.handleBattleMessage(battleId, lines);
      }
    }
  }

  handleQueryResponse(line) {
    // Parse roomlist response to find high-elo gen9ou battles
    try {
      const parts = line.split('|');
      if (parts[2] === 'roomlist') {
        const data = JSON.parse(parts[3]);
        
        if (data.rooms) {
          data.rooms.forEach(room => {
            if (room.format === FORMAT) {
              // Check if battle has high enough elo
              const minElo = Math.min(room.p1elo || 0, room.p2elo || 0);
              
              if (minElo >= MIN_ELO && !this.observedGames.has(room.roomid)) {
                console.log(`\n🎯 Found high-elo battle: ${room.roomid}`);
                console.log(`   Players: ${room.p1} (${room.p1elo}) vs ${room.p2} (${room.p2elo})`);
                this.spectateGame(room.roomid, room);
              }
            }
          });
        }
      }
    } catch (err) {
      // Ignore parse errors
    }
  }

  spectateGame(battleId, metadata) {
    console.log(`👁️  Spectating: ${battleId}`);
    this.observedGames.add(battleId);
    
    this.currentBattles.set(battleId, {
      id: battleId,
      p1: metadata.p1,
      p2: metadata.p2,
      p1elo: metadata.p1elo,
      p2elo: metadata.p2elo,
      startTime: Date.now(),
      turns: [],
      winner: null
    });

    // Join the battle room to spectate
    this.send(`|/join ${battleId}`);
  }

  handleBattleMessage(battleId, lines) {
    const battleData = this.currentBattles.get(battleId);
    if (!battleData) return;

    for (const line of lines) {
      if (line.startsWith('|turn|')) {
        const turn = parseInt(line.split('|')[2]);
        battleData.turns.push({ turn, events: [] });
      } else if (line.startsWith('|switch|') || line.startsWith('|move|')) {
        // Log switches and moves
        const currentTurn = battleData.turns[battleData.turns.length - 1];
        if (currentTurn) {
          currentTurn.events.push(line);
        }
      } else if (line.startsWith('|win|')) {
        const winner = line.split('|')[2];
        battleData.winner = winner;
        battleData.endTime = Date.now();
        this.saveBattle(battleData);
      }
    }
  }

  saveBattle(battleData) {
    // Classify teams before saving
    const teams = TeamClassifier.extractTeams(battleData);
    battleData.p1Team = teams.p1Team;
    battleData.p2Team = teams.p2Team;
    battleData.p1Classification = teams.p1Classification;
    battleData.p2Classification = teams.p2Classification;
    
    // Determine which side (if any) matches our archetype
    const p1MatchesArchetype = TeamClassifier.matchesArchetype(teams.p1Team, BOT_ARCHETYPE);
    const p2MatchesArchetype = TeamClassifier.matchesArchetype(teams.p2Team, BOT_ARCHETYPE);
    
    battleData.relevantToBot = p1MatchesArchetype || p2MatchesArchetype;
    
    // Tag the learning value
    if (p1MatchesArchetype && battleData.winner === battleData.p1) {
      battleData.learningValue = 'WIN_WITH_OUR_ARCHETYPE'; // Learn what works
    } else if (p1MatchesArchetype && battleData.winner === battleData.p2) {
      battleData.learningValue = 'LOSS_WITH_OUR_ARCHETYPE'; // Learn what NOT to do
    } else if (p2MatchesArchetype && battleData.winner === battleData.p2) {
      battleData.learningValue = 'WIN_WITH_OUR_ARCHETYPE';
    } else if (p2MatchesArchetype && battleData.winner === battleData.p1) {
      battleData.learningValue = 'LOSS_WITH_OUR_ARCHETYPE';
    } else {
      battleData.learningValue = 'IRRELEVANT_ARCHETYPE'; // Different playstyle
    }
    
    const filename = path.join(
      LOG_DIR,
      `${battleData.id.replace(/[^a-z0-9]/gi, '_')}_${Date.now()}.json`
    );

    fs.writeFileSync(filename, JSON.stringify(battleData, null, 2));
    console.log(`\n💾 Saved battle: ${filename}`);
    console.log(`   Winner: ${battleData.winner}`);
    console.log(`   Duration: ${battleData.turns.length} turns`);
    console.log(`   P1 Team: ${battleData.p1Classification.archetype} (${teams.p1Team.join(', ')})`);
    console.log(`   P2 Team: ${battleData.p2Classification.archetype} (${teams.p2Team.join(', ')})`);
    console.log(`   Learning Value: ${battleData.learningValue}`);
    
    // Clean up
    this.currentBattles.delete(battleData.id);
    this.send(`|/leave ${battleData.id}`);
  }
}

// Start observer
const observer = new HighEloObserver();
observer.connect();

console.log(`
🔍 High-Elo Game Observer Started
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Format: ${FORMAT}
Min Elo: ${MIN_ELO}
Log Dir: ${LOG_DIR}

Watching for high-level gameplay...
`);
