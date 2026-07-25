import sqlite3

def check_keys():
    conn = sqlite3.connect('build/app.db')
    cursor = conn.cursor()
    cursor.execute("SELECT key FROM kv_store")
    print([r[0] for r in cursor.fetchall()])

if __name__ == '__main__':
    check_keys()
