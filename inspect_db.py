import sys
sys.path.insert(0, 'D:/REACHLO/backend')
from app.config import settings
from sqlalchemy import create_engine, inspect, text

engine = create_engine(settings.DATABASE_URL)
inspector = inspect(engine)

for table in ['users', 'businesses', 'campaigns', 'ai_campaign_drafts']:
    cols = inspector.get_columns(table)
    names = [c['name'] for c in cols]
    print(table + ':', names)
    print()

with engine.connect() as conn:
    r1 = conn.execute(text('SELECT COUNT(*) FROM login_history'))
    print('login_history rows:', r1.scalar())
    r2 = conn.execute(text('SELECT COUNT(*) FROM users'))
    print('users rows:', r2.scalar())
    r3 = conn.execute(text('SELECT COUNT(*) FROM businesses WHERE whatsapp_number IS NULL'))
    print('businesses with null whatsapp_number:', r3.scalar())
    r4 = conn.execute(text('SELECT login_id, user_id, login_time FROM login_history LIMIT 5'))
    print('login_history sample:', r4.fetchall())
