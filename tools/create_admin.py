"""Create an admin account, or reset its password. Admins can't be created from the web.

    python -m tools.create_admin --email admin@example.org --name "Control room"
    echo "$PW" | python -m tools.create_admin --email admin@example.org --password-stdin

The password is prompted for (never passed on the command line, so it stays out of shell
history). Re-running for an existing email resets the password and promotes it to admin.
"""
import argparse
import getpass
import sys

from fastapi import HTTPException

from server import auth, db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="Admin")
    ap.add_argument("--password-stdin", action="store_true")
    a = ap.parse_args()
    if a.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass(f"Password for {a.email} (min {auth.MIN_PASSWORD['admin']} chars): ")
        if pw != getpass.getpass("Repeat password: "):
            sys.exit("Passwords don't match")
    try:
        auth.check_password_rules(pw, "admin")
        row = db.one("SELECT id FROM users WHERE email = ?", (a.email.strip().lower(),))
        if row:
            with db.tx() as c:
                c.execute("UPDATE users SET password_hash = ?, role = 'admin', disabled = 0, name = ? WHERE id = ?",
                          (auth.hash_password(pw), a.name, row["id"]))
                c.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            print(f"Updated {a.email}: password reset, role admin, signed out everywhere")
        else:
            auth.create_user(a.email, a.name, pw, "admin")
            print(f"Created admin {a.email}")
    except HTTPException as e:
        sys.exit(e.detail)


if __name__ == "__main__":
    main()
