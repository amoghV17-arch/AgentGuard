import React, { useState } from 'react';
import AgentGuardToggle from './components/AgentGuardToggle';
import SuccessRateChart from './components/SuccessRateChart';
import './index.css';

// Hardcoded metrics representing report.json output
const MOCK_DATA = {
  overall_metrics: {
    precision: 0.7750,
    recall: 0.3543,
    f1_score: 0.4863,
    roc_auc: 0.6097
  },
  latency: {
    average_ms: 47.1,
    p99_ms: 86.7
  },
  attack_success_by_round: [
    { round: 1, total_attacks: 100, attacks_evaded: 80, attack_success_rate: 0.2 },
    { round: 2, total_attacks: 100, attacks_evaded: 60, attack_success_rate: 0.4 },
    { round: 3, total_attacks: 100, attacks_evaded: 35, attack_success_rate: 0.65 }
  ]
};

function App() {
  const [agentGuardOn, setAgentGuardOn] = useState(true);
  
  const metrics = MOCK_DATA.overall_metrics;
  const currentRecall = agentGuardOn ? (metrics.recall * 100).toFixed(1) : 0;
  const currentFPR = agentGuardOn ? "10.3" : "0.0";
  
  return (
    <div className="dashboard-layout">
      <header className="dashboard-header">
        <div className="header-title">
          <h1>AgentGuard Dashboard</h1>
          <p>GenAI Payment Fraud Defense System</p>
        </div>
        <AgentGuardToggle 
          isOn={agentGuardOn} 
          onToggle={() => setAgentGuardOn(!agentGuardOn)} 
        />
      </header>

      <main>
        <div className="metrics-grid">
          <div className="metric-card">
            <span className="metric-label">Threat Detection (Recall)</span>
            <span className={`metric-value ${agentGuardOn ? 'good' : 'bad'}`}>
              {currentRecall}%
            </span>
          </div>
          <div className="metric-card">
            <span className="metric-label">False Positive Rate</span>
            <span className="metric-value">
              {currentFPR}%
            </span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Avg Latency (Budget: 150ms)</span>
            <span className="metric-value">
              {agentGuardOn ? MOCK_DATA.latency.average_ms : 0} ms
            </span>
          </div>
        </div>

        <SuccessRateChart 
          data={MOCK_DATA.attack_success_by_round} 
          agentGuardOn={agentGuardOn} 
        />
      </main>
    </div>
  );
}

export default App;
