#!/usr/bin/env bash
# Run this on the server as the tabi user after SSHing in.
# Usage: bash setup.sh
set -e

REPO="https://github.com/richardosinga/tabbi.git"
APP_DIR="$HOME/tabbi"
WORLD66_DIR="$HOME/world66"
VENV="$APP_DIR/venv"

# ── 1. Clone or update the repo ──────────────────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
  echo "==> Pulling latest…"
  git -C "$APP_DIR" fetch origin
  git -C "$APP_DIR" checkout feature/travel-passport
  git -C "$APP_DIR" pull --ff-only
else
  echo "==> Cloning repo…"
  git clone -b feature/travel-passport "$REPO" "$APP_DIR"
fi

# ── 2. World66 content ───────────────────────────────────────────────────────
if [ -d "$WORLD66_DIR/.git" ]; then
  echo "==> Updating world66 content…"
  git -C "$WORLD66_DIR" pull --ff-only
else
  echo "==> Cloning world66 content…"
  git clone https://github.com/richardosinga/world66.git "$WORLD66_DIR"
fi

# ── 3. Python venv & deps ────────────────────────────────────────────────────
echo "==> Installing dependencies…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

# ── 4. .env file (create once; skip if already exists) ───────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
  echo ""
  echo "==> Creating .env — fill in the values before starting the service."
  cat > "$APP_DIR/.env" <<'ENV'
DJANGO_SECRET_KEY=CHANGE_ME_generate_with_openssl_rand_hex_40
ALLOWED_HOST=tab.bi
ANTHROPIC_API_KEY=sk-ant-...
WORLD66_DIR=/home/tabi/world66
SITE_URL=https://tab.bi
# Optional Twilio for concierge feature:
# TWILIO_ACCOUNT_SID=
# TWILIO_AUTH_TOKEN=
# TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
ENV
  echo "    *** Edit $APP_DIR/.env with your real values before proceeding ***"
fi

# ── 5. Collectstatic & migrate ───────────────────────────────────────────────
echo "==> Collecting static files…"
cd "$APP_DIR"
set -a; source .env; set +a
"$VENV/bin/python" manage.py collectstatic --noinput
echo "==> Running migrations…"
"$VENV/bin/python" manage.py migrate

echo ""
echo "==> Done. Next steps:"
echo "    1. Edit $APP_DIR/.env if you haven't already"
echo "    2. Install systemd service:  sudo cp $APP_DIR/deploy/tabbi.service /etc/systemd/system/"
echo "    3. Enable & start:           sudo systemctl enable --now tabbi"
echo "    4. Install nginx config:     sudo cp $APP_DIR/deploy/tabbi.nginx /etc/nginx/sites-available/tabbi"
echo "    5. Enable nginx site:        sudo ln -s /etc/nginx/sites-available/tabbi /etc/nginx/sites-enabled/tabbi"
echo "    6. Test & reload nginx:      sudo nginx -t && sudo systemctl reload nginx"
