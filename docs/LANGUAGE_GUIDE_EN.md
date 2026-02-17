# Documentation Language Guide

**Date**: 2026-02-17 | **Status**: ✅ Complete

---

## 📚 File Naming Convention

All documentation files in the `docs/` folder now use language suffixes to clearly indicate their language:

- **`_EN` suffix**: English language documentation
- **`_CN` suffix**: Chinese (中文) language documentation

---

## 🇬🇧 English Documentation (_EN)

Complete list of English documentation files:

| File | Description |
|------|-------------|
| **SYSTEM_READY_EN.md** | ⭐ START HERE - System status & quick start |
| **INSTALL_DEPENDENCIES_EN.md** | Dependency installation guide |
| **RUN_FRONTEND_EN.md** | Web frontend deployment & demo |
| **RISK_CLASSIFICATION_GUIDE_EN.md** | Risk classification framework (English) |
| **RISK_CLASSIFICATION_QUICK_REFERENCE_EN.md** | Risk classification quick reference |
| **SETUP_GUIDE_EN.md** | Complete setup guide |
| **SQLITE_THREADING_FIX_EN.md** | SQLite threading issue solution |
| **FLASK_JSON_FIX_EN.md** | Flask JSON None values fix |
| **JSON_FIX_README_EN.md** | JSON fix documentation index |
| **JSON_FIX_SUMMARY_EN.md** | JSON fix detailed summary |
| **JSON_FIX_QUICK_REFERENCE_EN.md** | JSON fix quick reference |
| **SESSION_SUMMARY_EN.md** | Session deliverables summary |
| **INDEX_EN.md** | Master documentation index |

**Total English docs: 13 files**

---

## 🇨🇳 Chinese Documentation (_CN)

Complete list of Chinese (中文) documentation files:

| 文件 | 说明 |
|------|------|
| **COMPLETION_SUMMARY_CN.md** | 项目完成总结 |
| **FED_CRAWLER_GUIDE_CN.md** | 美联储新闻爬虫使用指南 |
| **IMPLEMENTATION_GUIDE_CN.md** | ARGUS完整实现指南 |
| **NEXT_STEPS_CN.md** | 立即开始指南 |
| **RISK_CLASSIFICATION_GUIDE_CN.md** | 风险分类指南 |
| **RISK_CLASSIFICATION_QUICK_REFERENCE_CN.md** | 风险分类快速参考 |
| **SETUP_SUMMARY_CN.md** | 项目设置总结 |
| **LOCALIZATION_CHANGES_CN.md** | 本地化变更记录 |

**总中文文档: 8 个文件**

---

## 📋 Quick Reference

### For English Users

Start with:
1. **SYSTEM_READY_EN.md** (5 min overview)
2. **RUN_FRONTEND_EN.md** (15 min deployment)
3. **INDEX_EN.md** (full navigation)

### For Chinese Users

从以下开始:
1. **COMPLETION_SUMMARY_CN.md** (项目完成)
2. **NEXT_STEPS_CN.md** (快速开始)
3. **IMPLEMENTATION_GUIDE_CN.md** (完整指南)

---

## 🔍 How to Find What You Need

### Language Detection

Files are now clearly marked:
- ✅ `FILENAME_EN.md` = English content
- ✅ `FILENAME_CN.md` = Chinese (中文) content
- ℹ️ No suffix on Python files (e.g., `test_json_fix.py`)

### Navigation

- **English Index**: See `INDEX_EN.md`
- **Master Index**: See `INDEX_EN.md` (bilingual references)
- **All Files**: List in `docs/` folder

---

## 📊 Statistics

```
English Documents:  13 files (62%)
Chinese Documents:   8 files (38%)
Total Documentation: 21 markdown files + 1 Python test file

By Category:
- Getting Started:      2 files (EN)
- Guides & Tutorials:   6 files (EN) + 4 files (CN)
- Technical Reference:  3 files (EN) + 2 files (CN)
- Quick Reference:      2 files (EN) + 1 file (CN)
- Bug Fixes & Updates:  2 files (EN)
- Project Summaries:    1 file (EN) + 2 files (CN)
```

---

## 🔄 File Organization Structure

```
docs/
├── 🇬🇧 ENGLISH GUIDES (_EN)
│   ├── SYSTEM_READY_EN.md ⭐
│   ├── INSTALL_DEPENDENCIES_EN.md
│   ├── RUN_FRONTEND_EN.md
│   ├── SETUP_GUIDE_EN.md
│   └── ...
│
├── 🇨🇳 CHINESE GUIDES (_CN)
│   ├── COMPLETION_SUMMARY_CN.md
│   ├── NEXT_STEPS_CN.md
│   ├── IMPLEMENTATION_GUIDE_CN.md
│   └── ...
│
├── 📚 INDEXES (Both Languages)
│   ├── INDEX_EN.md (Master index)
│   └── LANGUAGE_GUIDE_EN.md (this file)
│
└── 🧪 TESTS
    └── test_json_fix.py
```

---

## ✅ Benefits of This Naming Convention

| Benefit | Description |
|---------|-------------|
| 🎯 **Clarity** | Immediately know which language document is in |
| 🔍 **Easy Search** | Filter: `*_EN.md` or `*_CN.md` |
| 📊 **Organization** | Bilingual docs organized side-by-side |
| 🌍 **Scalability** | Easy to add more languages (e.g., `_ES`, `_FR`, `_JA`) |
| 🛠️ **Maintenance** | Clear which versions need updating |
| 👥 **Multi-language Support** | Users can choose their preferred language |

---

## 🚀 Migration History

**Before (2026-02-13):** Mixed language files without clear markers
```
INDEX.md (had bilingual references but unclear)
NEXT_STEPS.md (Chinese only, no marker)
RUN_FRONTEND.md (English only, no marker)
```

**After (2026-02-17):** Clear language markers
```
INDEX_EN.md (bilingual references, marked _EN)
NEXT_STEPS_CN.md (Chinese, marked _CN)
RUN_FRONTEND_EN.md (English, marked _EN)
```

---

## 📝 Usage Examples

### Shell Commands

List all English documentation:
```bash
ls -1 docs/*_EN.md
```

List all Chinese documentation:
```bash
ls -1 docs/*_CN.md
```

Count by language:
```bash
echo "English: $(ls -1 docs/*_EN.md | wc -l)"
echo "Chinese: $(ls -1 docs/*_CN.md | wc -l)"
```

### Python Examples

```python
import os
import glob

# Find all English docs
en_docs = glob.glob('docs/*_EN.md')

# Find all Chinese docs
cn_docs = glob.glob('docs/*_CN.md')

# Print by language
for doc in en_docs:
    print(f"English: {doc}")

for doc in cn_docs:
    print(f"Chinese: {doc}")
```

---

## 🔗 Cross-References

### English → Chinese
- SYSTEM_READY_EN.md (English) ↔ COMPLETION_SUMMARY_CN.md (Chinese)
- RUN_FRONTEND_EN.md (English) ↔ NEXT_STEPS_CN.md (Chinese)
- SETUP_GUIDE_EN.md (English) ↔ SETUP_SUMMARY_CN.md (Chinese)

### No Chinese Equivalent
- FLASK_JSON_FIX_EN.md (English only - recent fix)
- JSON_FIX_SUMMARY_EN.md (English only - recent fix)
- SQLITE_THREADING_FIX_EN.md (English only - recent fix)

---

## ⚠️ Important Notes

1. **File paths in code**: Update any hardcoded file paths
   - Old: `docs/README.md`
   - New: `docs/README_EN.md` or `docs/README_CN.md`

2. **Imports and references**: Check verify_system.py and other scripts
   - Already updated: ✅ verify_system.py

3. **URL paths**: If hosting documentation online
   - Use: `/docs/INDEX_EN.md` or `/docs/INDEX_CN.md`

4. **Version control**: All renamed files are tracked
   - Git will show files as renamed, not deleted/created

---

## 📞 Need Help?

- **English users**: See `INDEX_EN.md`
- **中文用户**: Search for `*_CN.md` files in `docs/` folder
- **Bilingual reference**: See `LANGUAGE_GUIDE_EN.md` (this file)

---

**Status**: ✅ COMPLETE
**Last Updated**: 2026-02-17
**Files Renamed**: 21 markdown files
**Language Support**: English (13) + Chinese (8)
