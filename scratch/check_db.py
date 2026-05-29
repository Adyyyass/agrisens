import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'agriculture_db',
    'user': 'postgres',
    'password': '456'
}

def check_db():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check tables
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        tables = cur.fetchall()
        print("Tables in database:")
        for t in tables:
            print(f"- {t['table_name']}")
        
        # Check multispectral_uploads
        cur.execute("SELECT id, nama_lahan FROM multispectral_uploads LIMIT 5")
        uploads = cur.fetchall()
        print("\nRecent multispectral uploads:")
        for u in uploads:
            print(f"- {u['id']}: {u['nama_lahan']}")
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
