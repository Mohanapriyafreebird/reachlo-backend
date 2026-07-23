from app.database import engine; from sqlalchemy import text;
with engine.connect() as conn:
    print('CAMPAIGNS:')
    print(conn.execute(text('SHOW CREATE TABLE campaigns')).fetchone()[1])
    print('USERS:')
    print(conn.execute(text('SHOW CREATE TABLE users')).fetchone()[1])
