import logging
import sys
import os

# Set up paths to include current directory
sys.path.append(os.getcwd())

from config.db import get_conn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_db")

def main():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT workflow_id, workflow_name FROM workflow_catalog WHERE workflow_name ILIKE '%%License%%'")
                rows = cur.fetchall()
                if rows:
                    logger.info("Found in DB:")
                    for row in rows:
                        logger.info(f" - ID: {row[0]}, Name: {row[1]}")
                else:
                    logger.info("No matches found in workflow_catalog.")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
