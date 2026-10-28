#!/bin/bash
# Amex Award Hunter - fetch the latest version from GitHub. Double-click to run.
# Keeps your .env (API key), data and logs; replaces only the program files.
cd "$(dirname "$0")"
REPO_URL="${AWARD_HUNTER_REPO:-https://github.com/joshuadcoons-cpu/amex-award-hunter.git}"
echo "Amex Award Hunter - update"
echo

if ! command -v git >/dev/null 2>&1 || ! git --version >/dev/null 2>&1; then
  echo "git is not installed yet. macOS will offer to install the Command Line Tools;"
  echo "click Install, wait for it to finish, then double-click this file again."
  xcode-select --install 2>/dev/null || true
  read -r -p "Press Return to close." _
  exit 1
fi

if [ ! -d .git ]; then
  echo "First update: linking this folder to GitHub ($REPO_URL)..."
  git init -q -b main
  git remote add origin "$REPO_URL"
fi
git remote set-url origin "$REPO_URL"

BEFORE=$(git rev-parse HEAD 2>/dev/null || echo none)
echo "Checking GitHub for changes..."
if ! git fetch -q origin main; then
  echo
  echo "Could not reach GitHub. If the repository is private, macOS will ask you to sign in to GitHub"
  echo "the first time; otherwise check your internet connection and try again."
  read -r -p "Press Return to close." _
  exit 1
fi
git checkout -q -f -B main origin/main
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
  echo "Already up to date."
else
  echo "Updated to version $(git log -1 --format='%h (%cd)' --date=short)."
  git log --format='  - %s' "${BEFORE/none/}"..HEAD 2>/dev/null | head -20
  if [ -x .venv/bin/python ]; then
    echo
    echo "Updating dependencies..."
    bash scripts/setup_mac.sh >/dev/null 2>&1 && echo "Dependencies OK." || echo "Dependency update had a problem; run 'Install Award Hunter.command' again."
  fi
fi
chmod +x *.command scripts/*.sh 2>/dev/null || true
xattr -dr com.apple.quarantine . 2>/dev/null || true
echo
echo "Done. If the dashboard is open, close its window and open it again to load the new version."
read -r -p "Press Return to close." _
