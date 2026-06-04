#!/usr/bin/env python3
"""Collect Hermes config/scripts and push backup to GitHub."""
import yaml, os, shutil, subprocess, sys
from datetime import datetime, timezone

HOME = '/opt/hermes'
HERMES_DIR = f'{HOME}/.hermes'
REPO_DIR = f'{HOME}/backups/repo'
REPO_URL = 'https://github.com/Jianmingphua/hermes.git'

SENSITIVE_KEYS = ['token', 'secret', 'password', 'api_key', 'apikey', 'private', 'bot_token', 'credential']

# Files/dirs to skip in skills
SKILL_SKIP = {'.git', '.bundled_manifest', '.curator_backups', '.curator_state', '.hub', '.usage.json', '.usage.json.lock', '__pycache__'}

def redact(obj):
    if isinstance(obj, dict):
        return {k: ('***REDACTED***' if any(s in k.lower() for s in SENSITIVE_KEYS) else redact(v)) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [redact(i) for i in obj]
    return obj

def safe_rmtree(path):
    if os.path.isdir(path):
        shutil.rmtree(path)

def safe_makedirs(path):
    os.makedirs(path, exist_ok=True)

def copy_skill(src, dst):
    """Copy a skill directory, skipping junk."""
    safe_makedirs(dst)
    for item in sorted(os.listdir(src)):
        if item in SKILL_SKIP or item.startswith('.'):
            continue
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

def collect_and_push():
    # Ensure repo exists
    if not os.path.isdir(f'{REPO_DIR}/.git'):
        safe_rmtree(REPO_DIR)
        subprocess.run(['git', 'clone', REPO_URL, REPO_DIR], check=True)
    
    subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], cwd=REPO_DIR, capture_output=True)

    repo = REPO_DIR

    # 1. config.yaml (redacted)
    with open(f'{HERMES_DIR}/config.yaml') as f:
        cfg = yaml.safe_load(f)
    with open(f'{repo}/config.yaml', 'w') as f:
        yaml.dump(redact(cfg), f, default_flow_style=False)
    print('config.yaml')

    # 2. env.example (redacted)
    env_candidates = [f'{HERMES_DIR}/env', f'{HERMES_DIR}/.env']
    env_src = next((p for p in env_candidates if os.path.exists(p)), None)
    if env_src:
        with open(env_src) as f:
            lines = f.readlines()
        out = []
        for line in lines:
            if '=' in line:
                key = line.split('=')[0]
                out.append(f'{key}=***REDACTED***\n' if any(s in key.lower() for s in SENSITIVE_KEYS) else line)
            else:
                out.append(line)
        with open(f'{repo}/env.example', 'w') as f:
            f.writelines(out)
        print('env.example')

    # 3. SOUL.md and identity files
    for fname in ['SOUL.md', 'AGENTS.md', 'CLAUDE.md']:
        src = f'{HERMES_DIR}/{fname}'
        if os.path.exists(src):
            shutil.copy2(src, f'{repo}/{fname}')
            print(fname)

    # 4. Custom scripts
    scripts_src = f'{HOME}/scripts'
    scripts_dst = f'{repo}/scripts'
    safe_makedirs(scripts_dst)
    if os.path.isdir(scripts_src):
        for f in sorted(os.listdir(scripts_src)):
            src = os.path.join(scripts_src, f)
            if os.path.isfile(src) and not f.startswith('__'):
                shutil.copy2(src, os.path.join(scripts_dst, f))
        # Clean up scripts dir: remove files that no longer exist in source
        for f in os.listdir(scripts_dst):
            if not os.path.exists(os.path.join(scripts_src, f)):
                os.remove(os.path.join(scripts_dst, f))
        print(f'scripts/ ({len(os.listdir(scripts_dst))} files)')

    # 5. Custom skills only
    CUSTOM_SKILLS = ['productivity/sg-weather', 'productivity/budget-tracker']
    skills_dst_base = f'{repo}/skills'
    safe_makedirs(skills_dst_base)
    for skill_rel in CUSTOM_SKILLS:
        src = os.path.join(HERMES_DIR, 'skills', skill_rel)
        dst = os.path.join(skills_dst_base, skill_rel)
        if os.path.isdir(src):
            safe_rmtree(dst)
            copy_skill(src, dst)
            print(f'skills/{skill_rel}/')

    # 6. README
    readme = """# Hermes Agent Backup

Auto-collected backup of Hermes Agent configuration and custom scripts.

- `config.yaml` — Agent config (secrets redacted)
- `env.example` — Environment variable template (secrets redacted)
- `SOUL.md` — Agent identity file
- `scripts/` — Custom utility scripts
- `skills/` — Custom skills (sg-weather, budget-tracker)

Updated automatically by `scripts/backup_to_github.py`.
"""
    with open(f'{repo}/README.md', 'w') as f:
        f.write(readme)

    # 7. .gitignore
    gitignore = """# Secrets
.env
key.json
*credential*
*.pem
*.key

# Python
__pycache__/
*.pyc
.venv/
venv/

# OS
.DS_Store

# Editor
*.swp
"""
    with open(f'{repo}/.gitignore', 'w') as f:
        f.write(gitignore)

    # Commit
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True)
    result = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo)
    if result.returncode == 0:
        print('No changes.')
        return
    
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    subprocess.run(['git', 'commit', '-m', f'backup: {ts}'], cwd=repo, check=True)
    
    # Push
    try:
        subprocess.run(['git', 'push', 'origin', 'main'], cwd=repo, check=True, timeout=30)
        print(f'Pushed: backup {ts}')
    except subprocess.CalledProcessError:
        print('ERROR: Push failed. Token may lack write permission.')
        print('Go to https://github.com/settings/personal-access-tokens and ensure:')
        print('  - Repository access: Jianmingphua/hermes')
        print('  - Permissions: Contents = Read and write')
        sys.exit(1)

if __name__ == '__main__':
    collect_and_push()
