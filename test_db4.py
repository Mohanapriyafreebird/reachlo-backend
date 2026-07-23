from app.database import engine; from sqlalchemy import text;
with engine.connect() as conn:
    try:
        conn.execute(text('''CREATE TABLE chat_threads (id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, lead_id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, campaign_id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, buyer_id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, seller_id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, last_message_at DATETIME, seller_unread_count INTEGER, buyer_unread_count INTEGER, created_at DATETIME, PRIMARY KEY (id), UNIQUE (lead_id), FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE, FOREIGN KEY(campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE, FOREIGN KEY(buyer_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY(seller_id) REFERENCES users (id) ON DELETE CASCADE)'''))
        conn.commit()
        print('SUCCESS!')
    except Exception as e:
        print(e)
