from app.database import engine; from sqlalchemy import text;
with engine.connect() as conn:
    try:
        conn.execute(text('''CREATE TABLE chat_messages (id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, thread_id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, sender_id VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, sender_role VARCHAR(10) COLLATE utf8mb4_0900_ai_ci NOT NULL, body TEXT COLLATE utf8mb4_0900_ai_ci NOT NULL, is_system TINYINT(1) NOT NULL DEFAULT 0, created_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(thread_id) REFERENCES chat_threads (id) ON DELETE CASCADE, FOREIGN KEY(sender_id) REFERENCES users (id) ON DELETE CASCADE)'''))
        conn.commit()
        print('SUCCESS!')
    except Exception as e:
        print(e)
