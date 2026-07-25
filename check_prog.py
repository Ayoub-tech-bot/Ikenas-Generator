import sqlite3
import json

def check_prog():
    conn = sqlite3.connect('build/app.db')
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM kv_store WHERE key='programme:1' OR key='programme'")
    for (val,) in cursor.fetchall():
        data = json.loads(val)
        if isinstance(data, list) and len(data) > 0:
            print("First item in array:")
            print(json.dumps(data[0], indent=2))
        else:
            print("Data is not an array or is empty:")
            print(json.dumps(data, indent=2)[:500])
        print('-'*40)

if __name__ == '__main__':
    check_prog()
