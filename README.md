# social-stats

Small local API to expose basic public social metrics for dashboards, smart counters, and Home Assistant.

Supported sources:
- Instagram: followers, following, posts
- YouTube: subscribers
- GitHub user: followers, public repositories
- GitHub repository: useful public metadata (stars, forks, issues, language, etc.)
- Docker Hub image: pull count, stars, metadata

No paid API key is required. This project only uses:
- Public GitHub API endpoints
- Public web scraping for Instagram and YouTube

If a profile or repository is private or unavailable, the API returns an error payload for that item.

## 1. Requirements

- Python 3.12+
- Optional: Docker / Docker Compose

## 2. Install

PowerShell (Windows):

```powershell
# Optional: clone if needed
git clone https://github.com/WorldOfGZ/social-stats.git
cd social-stats

# Create virtual environment
python -m venv .venv

# Activate (PowerShell)
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install -r requirements.txt

# Create runtime config
Copy-Item config-example.yaml config.yaml
```

Debian/Ubuntu (bash):

```bash
# Optional: clone if needed
git clone https://github.com/WorldOfGZ/social-stats.git
cd social-stats

# Create virtual environment
python3 -m venv .venv

# Activate
source .venv/bin/activate

# Install dependencies
python3 -m pip install -r requirements.txt

# Create runtime config
cp config-example.yaml config.yaml
```

## 3. Configuration

This project uses a root YAML config file:

- Template tracked in git: `config-example.yaml`
- Runtime file (not tracked): `config.yaml`

Create `config.yaml` from the example:

```powershell
Copy-Item config-example.yaml config.yaml
```

```bash
cp config-example.yaml config.yaml
```

Default schema:

```yaml
server:
	host: 0.0.0.0
	port: 8000
	timeout_seconds: 12

cache:
	enabled: true
	refresh_seconds: 3600

targets:
	instagram:
		- nasa
	youtube:
		- "@GoogleDevelopers"
	github_users:
		- torvalds
	github_repos:
		- "psf/requests"
	dockerhub_images:
		- "library/node"
```

## 4. Run Locally

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

```bash
python3 -m pip install -r requirements.txt
```

Start the API:

```powershell
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Open:
- Local UI tester: `http://localhost:8000/`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

The `/` page provides a minimal interface to test:
- all targets from `config.yaml`
- manual target input for each endpoint

This means users can run and test the project without browsing API docs.

Caching behavior:
- enabled by default
- refresh interval is `3600` seconds (1 hour)
- repeated calls inside this window reuse cached data instead of scraping/API calls again
- clear cache manually with `POST /cache/clear` to force next call to scrape/API fetch again

## 5. Run With Docker

```powershell
docker compose up --build
```

The compose file mounts `./config.yaml` as read-only in the container.

## 6. Update A Working Instance

Before updating, keep your current runtime config:

- Ensure `config.yaml` still contains your real targets.
- If needed, make a backup copy of `config.yaml`.
- If this instance was installed from this repository, update it with `git pull` (do not run `git clone` again in the same project folder).

### Local Python instance

1. Stop the running API process (for example, stop the `uvicorn` terminal).
2. Pull the latest code:

```powershell
git pull
```

3. Activate your virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

4. Reinstall dependencies (important if `requirements.txt` changed):

```powershell
python -m pip install -r requirements.txt
```

5. If new config keys were added in updates, compare `config-example.yaml` with your `config.yaml` and merge missing keys.
6. Start the API again:

```powershell
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### Docker Compose instance

1. Pull the latest code:

```powershell
git pull
```

2. If new config keys were added in updates, compare `config-example.yaml` with your `config.yaml` and merge missing keys.
3. Rebuild and restart containers:

```powershell
docker compose up -d --build
```

4. Optional cleanup of old images:

```powershell
docker image prune -f
```

After update, validate health:

- Local UI tester: `http://localhost:8000/`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

## 7. Endpoints

- `GET /health`
- `POST /cache/clear`
- `GET /stats`
	- Fetches every target from `config.yaml`
- `GET /stats/instagram/{username}`
- `GET /stats/youtube/{identifier}`
	- `identifier` can be `@handle`, channel id (`UC...`), or plain handle
- `GET /stats/github/user/{username}`
- `GET /stats/github/repo/{owner}/{repo}`
- `GET /stats/dockerhub/{namespace}/{image}`

## 8. Notes And Limits

- Social websites change HTML/JSON structures over time; scraping selectors may require updates.
- Public/no-key access can be rate limited by providers.
- Private or restricted accounts are not fully readable by design.

## 9. Repository Files

- `api/`: API entrypoint package
- `app/`: config loader and source clients
- `Dockerfile`, `docker-compose.yml`
- `config-example.yaml`
- `copilot-instructions.md`
- `LICENCE`
