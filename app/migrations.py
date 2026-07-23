from sqlalchemy import inspect, text

from app.database import engine
from app import models  # noqa: F401 - register models with Base


def run_migrations() -> None:
    """Apply lightweight schema updates for columns added after initial table creation."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    alterations = []

    # ------------------------------------------------------------------ users
    if "users" in table_names:
        columns = {col["name"] for col in inspector.get_columns("users")}
        if "area" in columns:
            alterations.append("ALTER TABLE users DROP COLUMN area")
        if "fcm_token" in columns:
            alterations.append("ALTER TABLE users DROP COLUMN fcm_token")
        if "city" not in columns:
            alterations.append("ALTER TABLE users ADD COLUMN city VARCHAR(100) NULL")
        if "profile_picture" not in columns:
            alterations.append("ALTER TABLE users ADD COLUMN profile_picture VARCHAR(255) NULL")
        if "preferences" not in columns:
            alterations.append("ALTER TABLE users ADD COLUMN preferences TEXT NULL")

    # -------------------------------------------------------------- businesses
    if "businesses" in table_names:
        columns = {col["name"] for col in inspector.get_columns("businesses")}
        
        # Columns to DROP
        for col_to_drop in ["description", "logo_url", "area", "created_at", "updated_at", "ai_generation_count", "ai_generation_month"]:
            if col_to_drop in columns:
                alterations.append(f"ALTER TABLE businesses DROP COLUMN {col_to_drop}")

        # New location fields
        if "location_address" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN location_address TEXT NULL")
        if "latitude" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN latitude DECIMAL(10,7) NULL")
        if "longitude" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN longitude DECIMAL(10,7) NULL")
        # business_description: what the seller provides — feeds AI generation
        # DIFFERENT from campaigns.description (the per-campaign marketing copy)
        if "business_description" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN business_description TEXT NULL")
        # usp: Unique Selling Proposition — personalisation signal for AI
        if "usp" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN usp TEXT NULL")
        # ai_business_analysis: cached Gemini deep analysis of the business
        # Computed once at registration, reused for all campaign generations
        if "ai_business_analysis" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN ai_business_analysis TEXT NULL")
        # ai_visual_style_memory: locked brand visual style after first campaign generation
        # Used to maintain palette/mood consistency across all campaigns for this seller
        if "ai_visual_style_memory" not in columns:
            alterations.append("ALTER TABLE businesses ADD COLUMN ai_visual_style_memory TEXT NULL")

        # Backfill whatsapp_number from users.phone if null
        alterations.append("""
            UPDATE businesses b 
            INNER JOIN users u ON b.user_id = u.id 
            SET b.whatsapp_number = u.phone 
            WHERE b.whatsapp_number IS NULL
        """)

    # --------------------------------------------------------------- campaigns
    if "campaigns" in table_names:
        columns = {col["name"] for col in inspector.get_columns("campaigns")}
        
        # Columns to DROP
        for col_to_drop in ["price_min", "city", "area"]:
            if col_to_drop in columns:
                alterations.append(f"ALTER TABLE campaigns DROP COLUMN {col_to_drop}")

        if "price" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN price FLOAT NULL")
        if "image_urls" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN image_urls TEXT NULL")
        if "location_address" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN location_address TEXT NULL")
        if "latitude" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN latitude FLOAT NULL")
        if "longitude" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN longitude FLOAT NULL")
        if "google_place_id" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN google_place_id VARCHAR(255) NULL")
        if "view_count" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN view_count INT NULL DEFAULT 0")
        if "lead_count" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN lead_count INT NULL DEFAULT 0")
        if "target_cities" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN target_cities TEXT NULL")
        # Flag AI-generated campaigns
        if "ai_generated" not in columns:
            alterations.append("ALTER TABLE campaigns ADD COLUMN ai_generated TINYINT(1) NOT NULL DEFAULT 0")
        alterations.append("UPDATE campaigns SET view_count = 0 WHERE view_count IS NULL")
        alterations.append("UPDATE campaigns SET lead_count = 0 WHERE lead_count IS NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN view_count INT NOT NULL DEFAULT 0")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN lead_count INT NOT NULL DEFAULT 0")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN cta_type VARCHAR(50) NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN category VARCHAR(150) NOT NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN target_audience VARCHAR(255) NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN status VARCHAR(20) NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN start_date DATETIME NOT NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN end_date DATETIME NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN boost_until DATETIME NULL")

        # Convert latitude/longitude to DECIMAL with sufficient precision
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN latitude DECIMAL(10,8) NULL")
        alterations.append("ALTER TABLE campaigns MODIFY COLUMN longitude DECIMAL(11,8) NULL")

        # Add indexes for faster proximity queries
        try:
            existing_indexes = {idx['name'] for idx in inspector.get_indexes('campaigns')}
        except Exception:
            existing_indexes = set()

        if 'idx_campaigns_latitude' not in existing_indexes:
            alterations.append("CREATE INDEX idx_campaigns_latitude ON campaigns (latitude)")
        if 'idx_campaigns_longitude' not in existing_indexes:
            alterations.append("CREATE INDEX idx_campaigns_longitude ON campaigns (longitude)")
        if 'idx_campaigns_status' not in existing_indexes:
            alterations.append("CREATE INDEX idx_campaigns_status ON campaigns (status)")

    # ---------------------------------------------------- campaign_views
    if "campaign_views" in table_names:
        columns = {col["name"] for col in inspector.get_columns("campaign_views")}
        if "viewer_id" not in columns:
            alterations.append("ALTER TABLE campaign_views ADD COLUMN viewer_id VARCHAR(10) NULL")
        if "created_at" not in columns:
            alterations.append("ALTER TABLE campaign_views ADD COLUMN created_at DATETIME NULL")
        if "viewer_ip" in columns:
            alterations.append("ALTER TABLE campaign_views MODIFY COLUMN viewer_ip VARCHAR(45) NULL")

    # ------------------------------------------------- ai_campaign_drafts (new table)
    if "ai_campaign_drafts" not in table_names:
        alterations.append("""
            CREATE TABLE ai_campaign_drafts (
                draft_no INT AUTO_INCREMENT PRIMARY KEY,
                id VARCHAR(10) NOT NULL UNIQUE,
                business_id VARCHAR(10) NOT NULL,
                campaign_topic TEXT NOT NULL,
                price_or_deal VARCHAR(255) NULL,
                start_date DATETIME NULL,
                end_date DATETIME NULL,
                title VARCHAR(200) NULL,
                campaign_description TEXT NULL,
                offer VARCHAR(255) NULL,
                cta_type VARCHAR(50) NULL,
                cta_value VARCHAR(255) NULL,
                target_audience VARCHAR(50) NULL,
                target_cities TEXT NULL,
                image_url VARCHAR(500) NULL,
                image_prompt TEXT NULL,
                market_signals_used TEXT NULL,
                hallucination_warnings TEXT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
                campaign_id VARCHAR(10) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE,
                INDEX idx_drafts_business (business_id),
                INDEX idx_drafts_status (status)
            )
        """)
    else:
        # ai_campaign_drafts exists, check if new columns need to be added
        draft_cols = {col["name"] for col in inspector.get_columns("ai_campaign_drafts")}
        if "target_cities" not in draft_cols:
            alterations.append("ALTER TABLE ai_campaign_drafts ADD COLUMN target_cities TEXT NULL")
        # ai_pipeline_stages: full JSON of all reasoning stages (marketing_strategy, buyer_psychology, creative_brief)
        if "ai_pipeline_stages" not in draft_cols:
            alterations.append("ALTER TABLE ai_campaign_drafts ADD COLUMN ai_pipeline_stages TEXT NULL")
        # Location fields — added to model after initial table creation
        if "location_address" not in draft_cols:
            alterations.append("ALTER TABLE ai_campaign_drafts ADD COLUMN location_address TEXT NULL")
        if "latitude" not in draft_cols:
            alterations.append("ALTER TABLE ai_campaign_drafts ADD COLUMN latitude DECIMAL(10,8) NULL")
        if "longitude" not in draft_cols:
            alterations.append("ALTER TABLE ai_campaign_drafts ADD COLUMN longitude DECIMAL(11,8) NULL")

    # ------------------------------------------------------ market_signals (new table)
    if "market_signals" not in table_names:
        alterations.append("""
            CREATE TABLE market_signals (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                category VARCHAR(100) NOT NULL,
                city VARCHAR(100) NOT NULL,
                festival_name VARCHAR(100) NULL,
                festival_date DATETIME NULL,
                days_to_festival INT NULL,
                season VARCHAR(100) NULL,
                trend_direction VARCHAR(20) NULL,
                trend_context VARCHAR(255) NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_signals_category_city (category, city)
            )
        """)

    # ---------------------------------------------------- category_playbooks (new table or alter)
    if "category_playbooks" not in table_names:
        alterations.append("""
            CREATE TABLE category_playbooks (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                category VARCHAR(100) NOT NULL UNIQUE,
                system_context TEXT NOT NULL,
                typical_cta VARCHAR(50) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
    else:
        columns = {col["name"] for col in inspector.get_columns("category_playbooks")}
        if "system_context" not in columns:
            alterations.append("ALTER TABLE category_playbooks ADD COLUMN system_context TEXT NULL")
        if "typical_cta" not in columns:
            alterations.append("ALTER TABLE category_playbooks ADD COLUMN typical_cta VARCHAR(50) NULL")

    # ------------------------------------------------------------------ users (push token)
    if "users" in table_names:
        columns = {col["name"] for col in inspector.get_columns("users")}
        if "expo_push_token" not in columns:
            alterations.append("ALTER TABLE users ADD COLUMN expo_push_token VARCHAR(255) NULL")

    # ------------------------------------------------------------------ chat_threads (CREATE)
    if "chat_threads" not in table_names:
        alterations.append("""
            CREATE TABLE chat_threads (
                id VARCHAR(10) PRIMARY KEY,
                lead_id VARCHAR(10) NOT NULL UNIQUE,
                campaign_id VARCHAR(10) NOT NULL,
                buyer_id VARCHAR(10) NOT NULL,
                seller_id VARCHAR(10) NOT NULL,
                last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_buyer_message_at DATETIME NULL,
                last_seller_reply_at DATETIME NULL,
                seller_unread_count INT NOT NULL DEFAULT 1,
                buyer_unread_count INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                FOREIGN KEY (buyer_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (seller_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
    else:
        columns = {col["name"] for col in inspector.get_columns("chat_threads")}
        if "last_buyer_message_at" not in columns:
            alterations.append("ALTER TABLE chat_threads ADD COLUMN last_buyer_message_at DATETIME NULL")
        if "last_seller_reply_at" not in columns:
            alterations.append("ALTER TABLE chat_threads ADD COLUMN last_seller_reply_at DATETIME NULL")

    # ------------------------------------------------------------------ chat_messages (CREATE)
    if "chat_messages" not in table_names:
        alterations.append("""
            CREATE TABLE chat_messages (
                id VARCHAR(10) PRIMARY KEY,
                thread_id VARCHAR(10) NOT NULL,
                sender_id VARCHAR(10) NOT NULL,
                sender_role VARCHAR(10) NOT NULL,
                body TEXT NOT NULL,
                is_system TINYINT(1) NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE,
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

    if alterations:
        with engine.begin() as conn:
            for statement in alterations:
                try:
                    conn.execute(text(statement))
                except Exception as e:
                    # Log but don't crash — some ALTER TABLE statements are idempotent-safe
                    # (e.g., modifying columns that already have correct types)
                    err_str = str(e)
                    # Ignore "duplicate column" and "duplicate key" errors which are non-fatal
                    if "Duplicate column name" in err_str or "Duplicate key name" in err_str:
                        continue
                    # Re-raise truly unexpected errors
                    raise
