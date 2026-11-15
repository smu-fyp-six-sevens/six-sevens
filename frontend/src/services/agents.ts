import { authFetch } from '../lib/api';
import { assertOk } from './uploadService/request';

export interface AgentRecord {
  id: string;
  name: string;
  group: string;
  purpose: string;
  limitations: string;
  model: string;
  prompt_version: string | null;
  tools: string[];
  // What each tool does: the description the model itself is given.
  tool_details: { name: string; description: string }[];
  access_summary: string[];
  call_budget: string;
  owner: string | null;
  prompt_options: string[];
  // Hash of the agent's prompts, model, tools and call budget (backend agent_versions.py).
  fingerprint?: string | null;
}

export interface AgentPrompt {
  agent_id: string;
  document_type: string | null;
  note: string;
  sections: { title: string; text: string }[];
}

export async function getAgentRegistry(): Promise<AgentRecord[]> {
  const response = await authFetch('/agents');
  await assertOk(response, 'Failed to load the agent registry');
  return (await response.json()) as AgentRecord[];
}

export async function getAgentPrompt(agentId: string, documentType?: string): Promise<AgentPrompt> {
  const query = documentType ? `?document_type=${encodeURIComponent(documentType)}` : '';
  const response = await authFetch(`/agents/${encodeURIComponent(agentId)}/prompt${query}`);
  await assertOk(response, 'Failed to load the agent prompt');
  return (await response.json()) as AgentPrompt;
}
