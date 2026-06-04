import yaml, os, shutil

BASE = '/opt/hermes/backups/github'
os.makedirs(BASE, exist_ok=True)

SENSITIVE_KEYS = ['token', 'secret', 'password', 'api_key', 'apikey', 'private', 'bot_token', 'credential']

def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                out[k] = '***REDACTED***'
            else:
                out[k] = redact(v)
        return out
    elif isinstance(obj, list):
        return [redact(i) for i in obj]
    return obj

# Copy config.yaml (redacted)
with open('/opt/hermes/.hermes/config.yaml') as f:
    cfg = yaml.safe_load(f)
safe_cfg = redact(cfg)
with open(f'{BASE}/config.yaml', 'w') as f:
    yaml.dump(safe_cfg, f, default_flow_style=False)
print('config.yaml -> OK')

# Copy .env (redacted)
env_lines = []
with open('/opt/hermes/.hermes/.env') as f:
    for line in f:
        if '=' in line:
            key, _, val = line.partition('=')
            if any(s in key.lower() for s in SENSITIVE_KEYS):
                env_lines.append(f'{key}=***REDACTED***\n')
            else:
                env_lines.append(line)
        else:
            env_lines.append(line)
with open(f'{BASE}/env.example', 'w') as f:
    f.writelines(env_lines)
print('env.example -> OK')

# Copy scripts
scripts_src = '/opt/hermes/scripts'
scripts_dst = f'{BASE}/scripts'
os.makedirs(scripts_dst, exist_ok=True)
if os.path.isdir(scripts_src):
    for f in os.listdir(scripts_src):
        src = os.path.join(scripts_src, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(scripts_dst, f))
    print(f'scripts -> OK ({len(os.listdir(scripts_dst))} files)')

# Copy identity/docs files
for fname in ['SOUL.md', 'AGENTS.md', 'CLAUDE.md']:
    src = f'/opt/hermes/.hermes/{fname}'
    if os.path.exists(src):
        shutil.copy2(src, f'{BASE}/{fname}')
        print(f'{fname} -> OK')

# Copy custom skills (user-created, not bundled)
skills_src = '/opt/hermes/.hermes/skills'
skills_dst = f'{BASE}/skills'
os.makedirs(skills_dst, exist_ok=True)
if os.path.isdir(skills_src):
    for d in os.listdir(skills_src):
        src = os.path.join(skills_src, d)
        dst = os.path.join(skills_dst, d)
        if os.path.isdir(src) and not d.startswith('.'):
            shutil.copytree(dst, dst, dirs_exist_ok=True) if os.path.exists(dst) else shutil.copytree(src, dst)
    print(f'skills -> OK')

print('Backup collection done.')
