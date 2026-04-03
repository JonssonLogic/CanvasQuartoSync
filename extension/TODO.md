# TODO

## Comment highlighting on math/KaTeX content

Kalle is implementing a working version of this in his quarto viewer.

## Module Structure enhancements

- **Publish/unpublish toggle**: Add a publish/unpublish button per module and per item in the Module Structure panel. Modules: toggle the entire module's published state on Canvas. Items: toggle individual item visibility. Should reflect the current state (published/draft badge) and update Canvas via API, then refresh the panel.
- **Accept Canvas changes**: When Canvas is newer, add an "Accept Canvas" action to pull the Canvas version and overwrite the local file.
- **Quiz import**: The `--import-item` flow doesn't yet handle Quiz items (classic or new). Needs question fetching and QMD generation.
- **Module creation**: Local-only modules (orphan section) can't be synced yet because there's no corresponding Canvas module. Add a "Create Module on Canvas" action.

## Multi-teacher collaboration via GitHub

The goal: multiple teachers collaborate on a course using a shared GitHub repo as the single source of truth. Local edits get pushed to GitHub, and Canvas is synced from there.

### Setup flow
1. **Lead teacher** creates the course repo on GitHub (already contains QMD files, config.toml, etc.)
2. **Other teachers** clone the repo and open it in VS Code. The extension detects config.toml and opens Module Structure.
3. Each teacher needs their own Canvas API token (stored locally in `.env` or `token.txt`, gitignored). The Canvas API URL and course ID come from config.toml in the repo.

### Sync model
- **GitHub is the source of truth** — all content changes should be committed and pushed to the repo.
- **Canvas is a deployment target** — syncing pushes content from the local repo to Canvas.
- **Canvas-side edits** are still detected via `updated_at` timestamps (yellow dot). Teachers can review and pull changes back into their local repo, then commit.

### Authentication considerations
- **Canvas**: Each teacher uses their own API token. Tokens are personal and must not be committed. Store in `.env` or `token.txt` (already gitignored).
- **GitHub**: Standard git authentication (SSH keys, HTTPS with credential manager, or GitHub CLI). No special handling needed — teachers already have git set up.

### Workflow
1. Teacher edits QMD locally → commits → pushes to GitHub
2. Teacher clicks "Sync ↑" in Module Structure → pushes to Canvas
3. If another teacher edited on Canvas directly → yellow dot appears → teacher pulls Canvas changes into local file → commits → pushes to GitHub
4. Other teachers pull from GitHub to stay in sync

### Open questions
- Should we add a "git pull" / "git push" button to the Module Structure panel?
- Should we detect when the local repo is behind the remote (unpulled commits) and warn before syncing to Canvas?
- Should Canvas sync be triggered automatically on git push (via GitHub Actions)?
