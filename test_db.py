from app.database import engine; from sqlalchemy import text;
with engine.connect() as conn:
    res = conn.execute(text('SELECT image_url, image_urls FROM campaigns ORDER BY created_at DESC LIMIT 1')).fetchone(); print(res)
