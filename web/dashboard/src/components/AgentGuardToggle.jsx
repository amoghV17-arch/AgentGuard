import React from 'react';
import { Shield, ShieldAlert } from 'lucide-react';

export default function AgentGuardToggle({ isOn, onToggle }) {
  return (
    <div className="toggle-container" onClick={onToggle}>
      <div className="toggle-label">
        {isOn ? <Shield className="icon-on" /> : <ShieldAlert className="icon-off" />}
        <span>AgentGuard Defense</span>
      </div>
      <button 
        className={`toggle-btn ${isOn ? 'active' : ''}`} 
        title={isOn ? "Disable AgentGuard" : "Enable AgentGuard"}
      >
        <div className="toggle-knob"></div>
      </button>
    </div>
  );
}
