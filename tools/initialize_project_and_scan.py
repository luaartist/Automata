"""Initialize project YangMills and run the FullScanner parser."""
import os
import sys
from pathlib import Path
import psycopg

# Add parent directory to path so imports work correctly
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from parser.scanner import FullScanner

def main():
    database_url = settings.DATABASE_URL or os.getenv("DATABASE_URL", "postgresql://appuser:apppassword@localhost:5432/lean4_automata")
    redis_url = settings.REDIS_URL or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    print(f"Connecting to database: {database_url}")
    
    # 1. Insert/upsert project row
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO projects (id, name, root_path, lean_version, lakefile_hash, indexed_at, last_modified)
                VALUES (1, 'YangMills', '/root/workspace/Automata', '4.7.0', 'hash_placeholder', now(), now())
                ON CONFLICT (id) DO UPDATE 
                SET name = EXCLUDED.name, root_path = EXCLUDED.root_path, last_modified = now()
            """)
            conn.commit()
            print("Project 'YangMills' with ID 1 successfully registered/updated in database.")

    # 2. Invoke FullScanner
    root_dir = Path("/root/workspace/Automata/src")
    print(f"Invoking FullScanner over {root_dir}...")
    scanner = FullScanner(
        project_id=1,
        root_dir=root_dir,
        db_url=database_url,
        redis_url=redis_url,
    )
    result = scanner.run()
    print("Scanner run completed successfully!")
    print(f"Files scanned: {result.get('files', 0)}, Symbols indexed: {result.get('symbols', 0)}")

    # 3. Refresh materialized views
    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for mv in ("dependency_stats", "module_metrics"):
                cur.execute(f"REFRESH MATERIALIZED VIEW {mv}")
                print(f"Refreshed materialized view: {mv}")

if __name__ == "__main__":
    main()
