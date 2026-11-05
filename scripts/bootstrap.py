"""Creates a unique password and secrets; does not overwrite existing .env."""
from pathlib import Path
import secrets
import sys
root=Path(__file__).resolve().parents[1]
dotenv=root/'.env'
if dotenv.exists():
    print('Already configured: .env exists. Refusing to overwrite existing secrets.');sys.exit(1)
password=secrets.token_urlsafe(19)
secret=secrets.token_urlsafe(48)
agent=secrets.token_urlsafe(38)
dotenv.write_text(f'''APP_SECRET={secret}\nADMIN_PASSWORD={password}\nAGENT_API_KEY={agent}\nCOOKIE_SECURE=false\n# DATABASE_URL=postgresql+psycopg://ninja:password@localhost:5432/ninja\n''')
try: dotenv.chmod(0o600)
except OSError: pass
print('Created .env — SAVE these secrets in a password manager. This output is shown only once.')
print('Admin password:',password)
print('Agent API key:',agent)
print('The panel should be opened ONLY on 127.0.0.1 unless HTTPS + secure cookies + firewall are configured.')
