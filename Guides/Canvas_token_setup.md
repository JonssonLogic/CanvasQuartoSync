# Canvas API Token Setup

## Getting the Canvas API token
1. Login to Canvas
2. Go to Account -> Settings
3. Under "Approved integrations", select "+ New access token"
4. Write a Purpose and click "Generate token"
5. Copy the token string — you will need it in the next step

## Storing the token

There are two options. Choose **one**.

### Option A: Token file (recommended)

Save the token to a file in your course folder (e.g., `privateCanvasToken`):

```
paste-your-token-here
```

Then reference it in `config.toml`:

```toml
canvas_api_url = "https://yourschool.instructure.com/api/v1"
canvas_token_path = "privateCanvasToken"
```

The path can be absolute or relative to the course folder. **Add the file to `.gitignore`** so it is never committed.

### Option B: Environment variables

Set the variables permanently via PowerShell:

```powershell
setx CANVAS_API_TOKEN "paste-your-token-here"
setx CANVAS_API_URL "https://yourschool.instructure.com/api/v1"
```

Environment variables take priority over `config.toml` values.

## Priority order

Credentials are resolved in this order (highest wins):

1. Environment variables (`CANVAS_API_URL`, `CANVAS_API_TOKEN`)
2. `config.toml` fields (`canvas_api_url`, `canvas_token_path`)
