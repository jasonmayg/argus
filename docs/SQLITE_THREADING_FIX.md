# SQLite Threading Issue - Fixed

**Date**: 2026-02-13 | **Status**: ✅ Resolved

---

## 🔧 Problem

When running the ARGUS pipeline through the Flask web dashboard, you may encounter:

```
SQLite objects created in a thread can only be used in that same thread.
The object was created in thread id 8531160128 and this is thread id 6167195648.
```

### Root Cause

SQLite connections are **not thread-safe**. The issue occurs because:

1. Flask creates a **background thread** to run the pipeline asynchronously
2. The original `DatabaseAgent` created a database connection in `__init__`
3. This connection was created in the main thread
4. The background thread tried to use this connection
5. SQLite detected the mismatch and raised an error

---

## ✅ Solution Applied

### What Was Changed

Modified **DatabaseAgent** in `agents_system.py`:

**Before** (❌ Thread-unsafe):
```python
class DatabaseAgent(Agent):
    def __init__(self):
        super().__init__("DatabaseManager")
        self.db = FedNewsDatabase()  # ❌ Connection created in main thread

    async def execute(self, action, data, **kwargs):
        # Uses self.db from different thread ❌
        inserted = self.db.insert_news(data['news_items'])
```

**After** (✅ Thread-safe):
```python
class DatabaseAgent(Agent):
    def __init__(self):
        super().__init__("DatabaseManager")
        # ✅ No database connection created in __init__

    async def execute(self, action, data, **kwargs):
        # ✅ Fresh connection created for each execution (in each thread)
        db = FedNewsDatabase()
        try:
            inserted = db.insert_news(data['news_items'])
            # ... rest of operations ...
        finally:
            db.close()  # ✅ Always close when done
```

### Key Changes

1. **Removed** `self.db = FedNewsDatabase()` from `__init__`
2. **Added** `db = FedNewsDatabase()` inside `execute()` method
3. Each thread now gets its own fresh database connection
4. Connection is properly closed in the `finally` block

---

## 🚀 Testing the Fix

### Run the Pipeline

1. **Start Flask server**:
   ```bash
   python frontend_app.py
   ```

2. **Open dashboard**:
   Open http://localhost:5000

3. **Click "Start Pipeline"**:
   Pipeline should now execute without threading errors

4. **Verify success**:
   - All 5 stages should complete
   - No SQLite threading errors
   - Results should display in dashboard

### Alternative: Direct Execution

```bash
python -c "
import asyncio
from agents_system import AgentOrchestrator

async def test():
    orchestrator = AgentOrchestrator()
    result = await orchestrator.execute_pipeline()
    print('Pipeline completed successfully!')

asyncio.run(test())
"
```

---

## 📚 Understanding SQLite Threading

### Why SQLite Has This Limitation

- SQLite uses **file-level locking**
- Each connection has its own state
- Sharing connections across threads causes race conditions
- SQLite enforces thread affinity to prevent data corruption

### Best Practices for Threading

✅ **DO**:
- Create a new database connection per thread
- Close connections when done
- Use thread-local storage if needed
- Pool connections if creating many

❌ **DON'T**:
- Share database connections across threads
- Create connections in one thread, use in another
- Keep long-lived shared connections
- Ignore the threading error

---

## 🔍 How It Works Now

### Thread-Safe Execution Flow

```
Flask Main Thread
    ↓
  POST /api/execute
    ↓
  Create background thread
    ↓
Background Thread
    ├─ NewsGathererAgent (creates new crawler)
    ├─ RiskClassifierAgent (creates new classifier)
    ├─ DatabaseAgent
    │   ├─ Create fresh DB connection ✅
    │   ├─ Insert news items
    │   ├─ Close DB connection ✅
    ├─ AnalysisAgent
    ├─ ReportingAgent
    ↓
Results returned to Flask
    ↓
Dashboard updated
```

Each step creates what it needs and doesn't share connections across threads.

---

## 💡 Additional Notes

### Why Not Use Connection Pooling?

For ARGUS's use case:
- **Single user** dashboard
- **Sequential pipeline** execution
- **Short operations** (a few seconds)
- Creating fresh connections is simple and efficient

If you scale to multiple concurrent users, consider:
- **SQLAlchemy** with thread pooling
- **PostgreSQL** (better concurrency)
- **Connection pool libraries** (DBUtils, etc.)

### Environment

The fix works with:
- ✅ Python 3.8+
- ✅ SQLite 3.x
- ✅ Flask with threading
- ✅ Async/await operations

---

## 🐛 If You Still See Errors

### Error Still Occurs

**Check**:
1. Did you update `agents_system.py`? Verify the change:
   ```bash
   grep -n "db = FedNewsDatabase()" agents_system.py
   ```
   Should show line ~161 inside `execute()` method

2. Did you restart Flask?
   ```bash
   python frontend_app.py
   ```

3. Are you running the latest code?
   ```bash
   git pull  # If using version control
   ```

### Different Error

If you see a different database error:
1. Check database file exists: `ls -la data/fed_news/fed_news.db`
2. Check database is readable: `sqlite3 data/fed_news/fed_news.db ".tables"`
3. Check permissions: `chmod 644 data/fed_news/fed_news.db`

---

## 📋 Affected Files

- ✅ **agents_system.py** - Fixed DatabaseAgent class
- ℹ️ **fed_news_db.py** - No changes needed
- ℹ️ **frontend_app.py** - No changes needed

---

## ✨ Summary

| Aspect | Before | After |
|--------|--------|-------|
| Database connection | Shared in __init__ | Created per execute() |
| Thread-safe | ❌ No | ✅ Yes |
| Error | SQLite threading error | None |
| Performance | Slightly faster | Negligible difference |
| Code clarity | Less clear | More explicit |

---

## 🎯 Verification Checklist

After applying the fix:

- [ ] Read this document
- [ ] Verified `agents_system.py` is updated
- [ ] Restarted `python frontend_app.py`
- [ ] Opened http://localhost:5000
- [ ] Clicked "Start Pipeline"
- [ ] Pipeline completed successfully
- [ ] No SQLite threading errors
- [ ] Results displayed in dashboard

---

## 📚 Related Documentation

- **SETUP_GUIDE.md** - General setup
- **IMPLEMENTATION_GUIDE.md** - Architecture details
- **RUN_FRONTEND.md** - Dashboard usage

---

**Status**: ✅ Fixed and Verified
**Version**: 1.0
**Date**: 2026-02-13
