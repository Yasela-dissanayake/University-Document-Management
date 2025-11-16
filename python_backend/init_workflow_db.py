import sqlite3

# 1. Check auth.sqlite (users, roles)
conn = sqlite3.connect('python_backend/auth.sqlite')
print("=== auth.sqlite ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"Tables: {tables}")
for table in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
    print(f"  {table[0]}: {count} rows")
conn.close()

# 2. Check acl.sqlite (encryption keys)
conn = sqlite3.connect('python_backend/acl.sqlite')
print("\n=== acl.sqlite ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"Tables: {tables}")
for table in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
    print(f"  {table[0]}: {count} rows")
conn.close()

# 3. Check semester_workflows.sqlite
conn = sqlite3.connect('python_backend/semester_workflows.sqlite')
print("\n=== semester_workflows.sqlite ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"Tables: {tables}")
for table in tables:
    count = conn.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
    print(f"  {table[0]}: {count} rows")
conn.close()

exit()