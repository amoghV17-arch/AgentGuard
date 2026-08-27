"""
Red Team module for AgentGuard.

Houses the threat taxonomy (Identify pillar), LLM-powered attack generation
(Generate pillar), and the DEAP-based genetic mutation engine for the
adversarial feedback loop.

This is the offensive side of the system -- everything here runs offline
or on-demand, never inside the live decision path.
"""
