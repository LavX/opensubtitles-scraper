# CloudScraper Library Reorganization Plan

## Overview
This document outlines the plan to reorganize the cloudscraper library from the current root-level `cloudscraper-3.0.0/` directory to a more professional `vendor/` directory structure.

## Current Situation
- **Current Location**: `cloudscraper-3.0.0/` in project root
- **Current Import**: `import cloudscraper` in `src/core/session_manager.py`
- **Issue**: Root-level third-party library directory looks unprofessional

## Target Structure
```
opensubtitles-scraper/
├── vendor/
│   └── cloudscraper/           # Only the Python package
│       ├── __init__.py
│       ├── cloudflare.py
│       ├── exceptions.py
│       └── ... (other cloudscraper files)
├── src/
│   ├── core/
│   │   └── session_manager.py  # Import remains unchanged
│   └── ...
├── main.py                     # Updated with vendor path
├── requirements.txt            # Updated documentation
└── AGENTS.md                   # Updated documentation
```

## Implementation Steps

### Step 1: Create Vendor Directory
- Create `vendor/` directory in project root
- Copy only the `cloudscraper/` package from `cloudscraper-3.0.0/cloudscraper/`
- Exclude examples, tests, and setup files

### Step 2: Update Python Path
Add to `main.py` (at the top):
```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor'))
```

### Step 3: Verify Imports
- No changes needed to `src/core/session_manager.py`
- Import `import cloudscraper` will work from vendor directory
- Test import functionality

### Step 4: Update Documentation
- Update `requirements.txt` comment about cloudscraper location
- Update `AGENTS.md` to reference `vendor/cloudscraper/`
- Update any other references to the old path

### Step 5: Testing
- Test cloudscraper import
- Verify session creation
- Test HTTP request functionality
- Run full application test

### Step 6: Cleanup
- Remove `cloudscraper-3.0.0/` directory
- Verify no broken references

## Benefits

1. **Industry Standard**: Follows established conventions for vendored dependencies
2. **Clean Separation**: Clear distinction between source code and third-party libraries
3. **Professional Structure**: More organized and maintainable project layout
4. **Minimal Changes**: Only requires path setup, no import modifications
5. **Future-Proof**: Easy to update or replace the library

## Risk Mitigation

- Keep original `cloudscraper-3.0.0/` until testing is complete
- Test each step incrementally
- Simple rollback plan if issues arise

## Files to Modify

1. `main.py` - Add vendor path to sys.path
2. `requirements.txt` - Update comment about cloudscraper location
3. `AGENTS.md` - Update references to cloudscraper location

## Files to Create

1. `vendor/cloudscraper/` - Copy from `cloudscraper-3.0.0/cloudscraper/`

## Files to Remove

1. `cloudscraper-3.0.0/` - After successful testing

## Success Criteria

- [ ] Cloudscraper imports successfully from vendor directory
- [ ] SessionManager creates sessions without errors
- [ ] HTTP requests work as before
- [ ] Application starts and functions normally
- [ ] Clean project structure with vendor directory