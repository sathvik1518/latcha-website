import getpass, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.security import hash_password

pw = getpass.getpass("Choose admin password (12+ characters): ")
if len(pw) < 12:
    sys.exit("Too short.")
print("\nPut this in ADMIN_PASSWORD_HASH:\n" + hash_password(pw))
