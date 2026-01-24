#!/usr/bin/env python3
"""
Simple smoke test script to verify database connection and basic operations.
Run this after setting DATABASE_URL environment variable.
"""

import os
import sys
from datetime import datetime

def test_db_connection():
    """Test database connectivity and basic operations."""

    print("=" * 60)
    print("AciTrack Backend - Database Connection Test")
    print("=" * 60)

    # Check environment variables
    print("\n1. Checking environment variables...")
    database_url = os.getenv("DATABASE_URL")
    api_key = os.getenv("ACITRACK_API_KEY")

    if not database_url:
        print("❌ DATABASE_URL not set")
        print("   Please set: export DATABASE_URL='postgresql://user:pass@host:port/db'")
        return False
    else:
        # Mask password for display
        masked_url = database_url
        if "@" in database_url:
            parts = database_url.split("@")
            if ":" in parts[0]:
                user_pass = parts[0].split("://")[1]
                user = user_pass.split(":")[0]
                masked_url = database_url.replace(user_pass, f"{user}:****")
        print(f"✓ DATABASE_URL: {masked_url}")

    if not api_key:
        print("⚠️  ACITRACK_API_KEY not set (API key auth disabled)")
    else:
        print(f"✓ ACITRACK_API_KEY: {api_key[:8]}...")

    # Import database module
    print("\n2. Importing database module...")
    try:
        from db import engine, SessionLocal, test_connection, init_db, Run, TriModelEvent, MustRead
        print("✓ Database module imported successfully")
    except Exception as e:
        print(f"❌ Failed to import database module: {e}")
        return False

    # Test connection
    print("\n3. Testing database connection...")
    if test_connection():
        print("✓ Database connection successful")
    else:
        print("❌ Database connection failed")
        return False

    # Initialize tables
    print("\n4. Initializing database tables...")
    try:
        init_db()
        print("✓ Database tables created/verified")
    except Exception as e:
        print(f"❌ Failed to initialize tables: {e}")
        return False

    # Test insert/query
    print("\n5. Testing database operations...")
    try:
        db = SessionLocal()

        # Insert a test run
        test_run_id = f"test-run-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        test_run = Run(
            run_id=test_run_id,
            mode="test",
            started_at=datetime.utcnow(),
            counts_json='{"test": true}'
        )
        db.add(test_run)
        db.commit()
        print(f"✓ Inserted test run: {test_run_id}")

        # Query the run
        queried_run = db.query(Run).filter(Run.run_id == test_run_id).first()
        if queried_run and queried_run.run_id == test_run_id:
            print(f"✓ Successfully queried test run: {queried_run.run_id}")
        else:
            print("❌ Failed to query test run")
            db.close()
            return False

        # Clean up test data
        db.delete(queried_run)
        db.commit()
        print("✓ Cleaned up test data")

        db.close()
    except Exception as e:
        print(f"❌ Database operation failed: {e}")
        return False

    # Test FastAPI import
    print("\n6. Testing FastAPI application import...")
    try:
        from main import app
        print("✓ FastAPI application imported successfully")
    except Exception as e:
        print(f"❌ Failed to import FastAPI app: {e}")
        return False

    print("\n" + "=" * 60)
    print("✅ All smoke tests passed!")
    print("=" * 60)
    print("\nYou can now run the server with:")
    print("  python main.py")
    print("\nOr test endpoints with:")
    print("  curl http://localhost:8000/health")
    print(f"  curl -H 'X-API-Key: {api_key[:8] if api_key else 'your-key'}...' http://localhost:8000/runs/latest")
    print("\n")

    return True


if __name__ == "__main__":
    success = test_db_connection()
    sys.exit(0 if success else 1)
