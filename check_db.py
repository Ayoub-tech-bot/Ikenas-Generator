import sqlite3

def check_db():
    conn = sqlite3.connect('build/app.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
    for name, sql in cursor.fetchall():
        print(f'Table: {name}')
        print(sql)
        print('-'*40)

if __name__ == '__main__':
    check_db()
