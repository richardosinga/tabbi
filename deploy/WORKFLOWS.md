# Tabbi — Deployment & Sync Workflows

## Deploy latest code to server

```bash
# Locally: merge PR on GitHub, then on the server:
cd ~/tabbi && git pull
sudo systemctl restart tabi
```

## Sync world66 content to server

```bash
# On the server:
cd ~/tabbi/world66 && git pull
sudo systemctl restart tabi
```

## Submit draft POIs from trips to world66

Draft POIs are written by the MCP agent to `plans/pois/`. Run these locally.

### Step 1 — Pull any POIs created on the live server

```bash
rsync -av tabi@tab.bi:~/tabbi/plans/pois/ ~/Repos/tabbi/plans/pois/
```

### Step 2 — Preview what will be copied to world66

```bash
cd ~/Repos/tabbi
python deploy/pois-to-world66.py
```

### Step 3 — Copy and open PR

```bash
python deploy/pois-to-world66.py --apply
```

This creates a branch `tabbi-pois-YYYY-MM-DD` on world66 and opens a PR.

### Step 4 — Review and clean up the PR

Open the PR on GitHub. Check each file:
- Remove placeholder/test entries (no body, "Hotel ABC", single-sentence stubs)
- Ensure all POIs have `latitude` and `longitude`
- Remove any `source_url` field (not valid frontmatter)
- Expand thin writeups to 2–3 paragraphs per STYLE.md

Or ask Claude: *"look at PR 1477 and fix the POIs"*

### Step 5 — Merge the PR, then pull locally

```bash
cd ~/Repos/world66 && git pull
```

### Step 6 — Update plan files to use world66 paths

```bash
cd ~/Repos/tabbi
python deploy/update-plan-refs.py        # preview
python deploy/update-plan-refs.py --apply
```

This replaces `~pois/plan-slug/path/poi` with the real world66 path in plan files,
but only where the file actually exists — safe to run at any time.

---

## Chrome extension

The extension is in `tabbi-extension/`. To submit an update to the Chrome Web Store:
1. Zip the `tabbi-extension/` folder
2. Upload at https://chrome.google.com/webstore/devconsole

## MCP server

The MCP endpoint is served by Django at `https://tab.bi/mcp` — no separate service needed.

To register on Smithery: https://smithery.ai — paste `https://tab.bi/mcp` as the server URL.

Users connect in Claude via: **Settings → Integrations → Add → `https://tab.bi/mcp`**

A setup page is available at: https://tab.bi/connect
