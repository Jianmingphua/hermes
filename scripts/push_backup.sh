#!/bin/bash
# Push collected backup to GitHub
set -e

BACKUP_DIR="/opt/hermes/backups/github"
REPO_DIR="/opt/hermes/backups/repo"

# Re-collect fresh
python3 /opt/hermes/scripts/collect_backup.py

# Clone or pull
if [ ! -d "$REPO_DIR/.git" ]; then
    rm -rf "$REPO_DIR"
    git clone https://github.com/Jianmingphua/hermes.git "$REPO_DIR"
fi

cd "$REPO_DIR"
git pull --rebase origin main 2>/dev/null || true

# Sync backup files (delete removed files, copy new ones)
rsync -ac --delete \
    --exclude='.git' \
    "$BACKUP_DIR/" ./

# Commit if there are changes
git add -A
if git diff --cached --quiet; then
    echo "No changes to push."
else
    git commit -m "backup: $(date -u '+%Y-%m-%d %H:%M UTC')"
    git push origin main
    echo "Push done."
fi
