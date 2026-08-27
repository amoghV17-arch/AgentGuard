import React from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

export default function SuccessRateChart({ data, agentGuardOn }) {
  const chartData = data.map(round => ({
    name: `Round ${round.round}`,
    'Attacks Stopped': agentGuardOn ? round.attacks_evaded : 0,
    'Attacks Successful': agentGuardOn ? (round.total_attacks - round.attacks_evaded) : round.total_attacks,
  }));

  return (
    <div className="chart-wrapper">
      <h3 className="chart-title">Attack Prevention (By Round)</h3>
      <div className="chart-container" style={{ height: '300px' }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
            <XAxis dataKey="name" stroke="#a3a3a3" />
            <YAxis stroke="#a3a3a3" />
            <Tooltip 
              contentStyle={{ backgroundColor: '#171717', border: '1px solid #333', borderRadius: '8px' }} 
              itemStyle={{ color: '#e5e5e5' }}
            />
            <Legend wrapperStyle={{ paddingTop: '10px' }} />
            <Bar dataKey="Attacks Stopped" stackId="a" fill="#10b981" radius={[0, 0, 4, 4]} />
            <Bar dataKey="Attacks Successful" stackId="a" fill="#ef4444" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
