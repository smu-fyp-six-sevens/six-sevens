import { useEffect, useState } from 'react';

type HealthResponse = {
  status: string;
};

type MessageResponse = {
  message: string;
};

const apiBaseUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [message, setMessage] = useState<string>('Loading API data...');

  useEffect(() => {
    const loadData = async () => {
      try {
        const [healthResponse, messageResponse] = await Promise.all([
          fetch(`${apiBaseUrl}/api/health`),
          fetch(`${apiBaseUrl}/api/message`),
        ]);

        const healthData = (await healthResponse.json()) as HealthResponse;
        const messageData = (await messageResponse.json()) as MessageResponse;

        setHealth(healthData);
        setMessage(messageData.message);
      } catch {
        setMessage('Unable to reach the backend API.');
      }
    };

    void loadData();
  }, []);

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">FastAPI + React starter</p>
        <h1>fyp</h1>
        <p className="lede">
          A basic full-stack scaffold with a FastAPI backend, a React frontend, Docker, and starter tests.
        </p>
      </section>

      <section className="card-grid">
        <article className="card">
          <h2>Backend</h2>
          <p>Health check: {health?.status ?? 'loading...'}</p>
        </article>

        <article className="card">
          <h2>API Message</h2>
          <p>{message}</p>
        </article>
      </section>
    </main>
  );
}
