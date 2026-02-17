#!/usr/bin/env python3
"""
Test script to verify the JSON None-value handling fix
Run this to test the /api/results and /api/guardian/dashboard endpoints
"""

import json
import requests
import time
from typing import Any


def test_json_encoder():
    """Test the SafeJSONEncoder with various None value scenarios"""
    print("\n" + "="*70)
    print("TEST 1: SafeJSONEncoder with None values")
    print("="*70)

    # Import from frontend_app
    import sys
    sys.path.insert(0, '/path/to/Downloads/argus')
    from frontend_app import SafeJSONEncoder, sanitize_for_json

    test_cases = [
        {
            "name": "Simple None value",
            "data": {"key": None, "other": "value"},
            "expected": {"other": "value"}
        },
        {
            "name": "Nested None values",
            "data": {"outer": {"inner": None, "valid": "data"}},
            "expected": {"outer": {"valid": "data"}}
        },
        {
            "name": "List with None",
            "data": {"items": [1, None, 2, None, 3]},
            "expected": {"items": [1, 2, 3]}
        },
        {
            "name": "Mixed complex structure",
            "data": {
                "status": "success",
                "result": None,
                "data": {
                    "nested": None,
                    "valid": "value"
                },
                "items": [None, "item1", None, {"key": None, "value": "test"}]
            },
            "expected": {
                "status": "success",
                "data": {
                    "valid": "value"
                },
                "items": ["item1", {"value": "test"}]
            }
        }
    ]

    for test in test_cases:
        result = sanitize_for_json(test["data"])
        passed = result == test["expected"]
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"\n{status}: {test['name']}")
        if not passed:
            print(f"  Input:    {test['data']}")
            print(f"  Expected: {test['expected']}")
            print(f"  Got:      {result}")

    print("\n✅ SafeJSONEncoder tests completed")


def test_flask_endpoints():
    """Test Flask endpoints for JSON serialization"""
    print("\n" + "="*70)
    print("TEST 2: Flask API Endpoints")
    print("="*70)

    base_url = "http://localhost:5000"
    endpoints = [
        ("/api/status", "GET", "Status endpoint"),
        ("/api/results", "GET", "Results endpoint"),
        ("/api/agents/summary", "GET", "Agents summary"),
        ("/api/guardian/dashboard", "GET", "Guardian dashboard"),
    ]

    print("\n⏳ Testing endpoints (ensure Flask is running on http://localhost:5000)...")

    for endpoint, method, description in endpoints:
        try:
            url = f"{base_url}{endpoint}"
            if method == "GET":
                response = requests.get(url, timeout=5)
            else:
                response = requests.post(url, timeout=5)

            status_ok = 200 <= response.status_code < 400
            status_symbol = "✅" if status_ok else "⚠️"

            print(f"\n{status_symbol} {description}")
            print(f"   URL:      {endpoint}")
            print(f"   Status:   {response.status_code}")

            # Try to parse JSON
            try:
                data = response.json()
                # Check for None values in response (should be converted to null)
                json_str = json.dumps(data, indent=2)
                has_nulls = "null" in json_str
                print(f"   JSON:     ✅ Valid (contains {json_str.count(chr(10))} lines)")
                if has_nulls:
                    print(f"   ℹ️  Response contains 'null' values (this is expected)")
            except json.JSONDecodeError as e:
                print(f"   JSON:     ❌ Invalid JSON - {str(e)}")

        except requests.ConnectionError:
            print(f"\n⚠️  {description}")
            print(f"   Cannot connect to {base_url}")
            print("   → Start Flask: python frontend_app.py")
            break
        except Exception as e:
            print(f"\n❌ {description}")
            print(f"   Error: {str(e)}")


def test_problematic_data_structures():
    """Test sanitization with problematic data structures"""
    print("\n" + "="*70)
    print("TEST 3: Problematic Data Structures from OpenClaw/Crawl4AI")
    print("="*70)

    import sys
    sys.path.insert(0, '/path/to/Downloads/argus')
    from frontend_app import sanitize_for_json
    import json

    # Simulate scraped data with None values
    problematic_scrape_result = {
        "status": "success",
        "data": {
            "title": "Federal Reserve News",
            "content": None,  # Missing content
            "links": [
                {"url": "https://example.com", "title": "Link 1"},
                {"url": None, "title": None},  # Bad data
                {"url": "https://example2.com", "title": "Link 2"},
            ],
            "metadata": {
                "crawled_at": "2024-01-01T00:00:00",
                "error": None,
                "warnings": None,
            }
        }
    }

    print("\nOriginal problematic structure:")
    print(json.dumps(problematic_scrape_result, indent=2, default=str))

    sanitized = sanitize_for_json(problematic_scrape_result)

    print("\n✅ After sanitization:")
    print(json.dumps(sanitized, indent=2))

    # Try to jsonify it
    try:
        json_str = json.dumps(sanitized)
        print(f"\n✅ JSON serialization successful ({len(json_str)} bytes)")
    except TypeError as e:
        print(f"\n❌ JSON serialization failed: {e}")


def test_sort_keys_config():
    """Verify Flask JSON_SORT_KEYS is disabled"""
    print("\n" + "="*70)
    print("TEST 4: Flask Configuration Check")
    print("="*70)

    import sys
    sys.path.insert(0, '/path/to/Downloads/argus')
    from frontend_app import app

    sort_keys = app.config.get('JSON_SORT_KEYS', None)
    print(f"\nJSON_SORT_KEYS setting: {sort_keys}")

    if sort_keys is False:
        print("✅ JSON_SORT_KEYS is disabled (prevents sort comparison errors)")
    elif sort_keys is True:
        print("⚠️  JSON_SORT_KEYS is enabled (may cause comparison errors with None)")
    else:
        print("⚠️  JSON_SORT_KEYS is not set explicitly (defaults to Flask behavior)")

    encoder = app.json_encoder
    print(f"\nJSON Encoder: {encoder.__name__}")
    if encoder.__name__ == "SafeJSONEncoder":
        print("✅ SafeJSONEncoder is configured")
    else:
        print(f"⚠️  Using default encoder: {encoder.__name__}")


def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("ARGUS Flask JSON Fix - Comprehensive Test Suite")
    print("="*70)

    try:
        # Test 1: Local encoder tests
        test_json_encoder()

        # Test 2: Problematic data structures
        test_problematic_data_structures()

        # Test 3: Configuration check
        test_sort_keys_config()

        # Test 4: Flask endpoints (if running)
        try:
            test_flask_endpoints()
        except Exception as e:
            print(f"\n⚠️  Skipping Flask endpoint tests: {e}")

        print("\n" + "="*70)
        print("Test suite completed!")
        print("="*70)

    except Exception as e:
        print(f"\n❌ Test suite failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
