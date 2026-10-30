# Plan Assistant

AngularJS frontend + FastAPI backend (dummy data, no database yet).

## Prerequisites
- Python 3.10+ (tested on 3.12)
- Internet access in the browser (AngularJS is loaded from the Google CDN)

## Install
```powershell
cd backend
pip install -r requirements.txt
```

## Run
Open two terminals.

**Backend** (http://localhost:8000, API docs at /docs):
```powershell
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```

**Frontend** (http://localhost:4200):
```powershell
cd frontend
python -m http.server 4200
```

Open http://localhost:4200 and sign in with `bharath` / `password` or `randy` / `password`.

Port 4200 is required: the backend only allows CORS from `localhost:4200`.

## Features
- Login against `backend/app/data/users.json` (token-based, 1 hour expiry)
- Chat (landing page after login), ChatGPT-style: the assistant's first message shows the welcome, premium and inbox cards and suggested questions; replies are placeholders based on the user's dummy account data

## Configuration
- `JWT_SECRET` – token signing secret (set a real value outside local development)
- `TOKEN_TTL_SECONDS` – token lifetime, default 3600
- API URL: `API_BASE` constant in `frontend/js/app.js`

## Layout
```
backend/app/   auth.py, home.py, chat.py, main.py, data/*.json
frontend/      index.html, *.html views, styles.css, js/
```
