#!/usr/bin/env bash
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Ensure we are in the project root
if [ ! -d "src/git_sqlite_filter" ]; then
    echo -e "${RED}Error: run_tests.sh must be run from the project root.${NC}"
    exit 1
fi

PROJECT_ROOT=$(pwd)
FIXTURE_DIR="test/fixtures"
TMP_DIR=".tmp/test_runs"

mkdir -p "$TMP_DIR"

# 1. Generate fixtures
echo "Generating test fixtures..."
python3 test/generate_test_dbs.py

# 2. Iterate over fixtures
echo "Running semantic parity tests..."
SUCCESS_COUNT=0
FAILURE_COUNT=0

for db_path in "$FIXTURE_DIR"/*.db; do
    db_name=$(basename "$db_path")
    echo -n "Test case: $db_name ... "

    # Step A: Clean original DB -> SQL Jump A
    python3 src/git_sqlite_filter/clean.py "$db_path" > "$TMP_DIR/${db_name}.dump_a.sql" 2> /dev/null

    # Step B: Smudge SQL Jump A -> Rebuilt DB
    cat "$TMP_DIR/${db_name}.dump_a.sql" | python3 src/git_sqlite_filter/smudge.py > "$TMP_DIR/${db_name}.rebuilt.db" 2> /dev/null

    # Step C: Clean Rebuilt DB -> SQL Jump B
    python3 src/git_sqlite_filter/clean.py "$TMP_DIR/${db_name}.rebuilt.db" > "$TMP_DIR/${db_name}.dump_b.sql" 2> /dev/null

    # Step D: Compare SQL Dump A and B
    if diff "$TMP_DIR/${db_name}.dump_a.sql" "$TMP_DIR/${db_name}.dump_b.sql" > /dev/null; then
        echo -e "${GREEN}PASSED${NC}"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        echo -e "${RED}FAILED${NC}"
        echo "Diff found for $db_name:"
        diff -u "$TMP_DIR/${db_name}.dump_a.sql" "$TMP_DIR/${db_name}.dump_b.sql" | head -n 20
        FAILURE_COUNT=$((FAILURE_COUNT + 1))
    fi
done

# 3. Ultimate Fallback (Inaccessible/New/Corrupt)
echo -n "Test case: binary_fallback ... "
echo "raw binary content" > "$TMP_DIR/binary_only.db"
# This file is not in Git, and is not a valid SQLite DB, so clean.py should fall back to raw read
if python3 src/git_sqlite_filter/clean.py "$TMP_DIR/binary_only.db" 2>/dev/null | grep -q "raw binary content"; then
    echo -e "${GREEN}PASSED${NC}"
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
else
    echo -e "${RED}FAILED${NC}"
    FAILURE_COUNT=$((FAILURE_COUNT + 1))
fi

echo
echo -e "${GREEN}Passed: $SUCCESS_COUNT${NC}"
if [ $FAILURE_COUNT -gt 0 ]; then
    echo -e "${RED}Failed: $FAILURE_COUNT${NC}"
    exit 1
else
    echo "All tests passed successfully!"
fi
