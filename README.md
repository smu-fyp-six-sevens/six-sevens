# six-sevens

Final Year Project w/ Greenlink Digital Bank

## Structure

- `backend/` FastAPI app with a health endpoint and example message route
- `frontend/` React + TypeScript app that calls the backend
- `docker-compose.yml` local container setup for both services

## Run locally

Backend:

```bash
cd backend
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Docker Setup

From root:

```bash
docker compose up
```

## Tests

- Backend: `pytest`

Frontend is build-only for now.

## GitHub Actions

- Pull requests and pushes to `main` run backend tests and frontend lint/build.
- Pushes to `main` also publish Docker images to GitHub Container Registry.
