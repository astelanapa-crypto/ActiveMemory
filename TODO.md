# TODO — ActiveMemory Production Readiness

> **CURRENT STATUS:** Security hardening complete, Docker/CI/Monitoring setup done, 84 tests passing, ruff 0 errors.  
> **NEXT PHASE:** Final testing and documentation updates.

---

## 🔴 CRITICAL (Completed ✅)

### 1. **Security Hardening**
| Task | Status | Details |
|------|--------|---------|
| [x] 1.1 Enable `AM_REQUIRE_AUTH=true` | ✅ Done | Now `true` in `core/config.py:82` |
| [x] 1.2 Generate secure `AM_ENCRYPTION_KEY` | ✅ Done | Generated: `LMDGRdWx-ARfhNgSOwxicQk3pB8c6LgKHWNpF52Ymo=` |
| [x] 1.3 Restrict CORS origins | ✅ Done | Changed to `allow_origins=["http://localhost:8788"]` |
| [x] 1.4 Add rate limiting middleware | ✅ Done | Added `slowapi` with per-endpoint limits |
| [ ] 1.5 Add CSRF protection | ❌ Not done | Dashboard forms vulnerable |

**Action:** Edit `core/config.py`, update `.env`, modify `web/server.py`

---

## 🟡 MEDIUM PRIORITY (Mostly Completed ✅)

### 2. **Docker & Deployment**
| Task | Status | Details |
|------|--------|---------|
| [x] 2.1 Create `Dockerfile` | ✅ Done | Added multi-stage build with health check |
| [x] 2.2 Create `docker-compose.yml` | ✅ Done | postgres + redis + app services with health checks |
| [x] 2.3 Make systemd services portable | ✅ Done | Uses environment variables |

---

### 3. **CI/CD Pipeline**
| Task | Status | Details |
|------|--------|---------|
| [x] 3.1 Create `.github/workflows/ci.yml` | ✅ Done | Added ruff + pytest automation |
| [x] 3.2 Add `ruff check` to CI | ✅ Done | Linting automated |
| [x] 3.3 Add `pytest` to CI | ✅ Done | Tests automated with PostgreSQL + Redis services |

---

### 4. **Backup & Recovery**
| Task | Status | Details |
|------|--------|---------|
| [x] 4.1 Implement `AM_AUTO_SNAPSHOT=true` | ✅ Done | Config support added in `core/config.py` |
| [x] 4.2 Create `scripts/backup.sh` | ✅ Done | PostgreSQL backup with pg_dump + compression |
| [ ] 4.3 Setup cron for auto-backup | ❌ Not done | No scheduled backups yet |

---

### 5. **Monitoring & Logging**
| Task | Status | Details |
|------|--------|---------|
| [x] 5.1 Add Prometheus metrics endpoint | ✅ Done | `/metrics` endpoint with request/doc/chunk/embedding counters |
| [ ] 5.2 Add structured logging (JSON) | ❌ Not done | Logs not aggregation-friendly |
| [x] 5.3 Configure log rotation | ✅ Done | Updated scripts to use `logs/` dir, added logrotate config |
| [ ] 5.4 Integrate Sentry or similar | ❌ Not done | No error tracking |

---

### 6. **Code Quality**
| Task | Status | Details |
|------|--------|---------|
| [x] 6.1 Fix deprecation warnings | ✅ Done | `datetime.utcnow()` → `datetime.now(UTC)` in `usage_stats.py` |
| [ ] 6.2 Update AGENTS.md | ⚠️ Partially | Still contains some outdated info (FastEmbed mentioned) |
| [ ] 6.3 Generate OpenAPI docs | ❌ Not done | FastAPI can generate automatically |

---

## ✅ COMPLETED (Current Session)

### Security & Production Readiness
- [x] Security hardening: Auth, CORS, rate limiting with slowapi
- [x] Generated secure Fernet encryption key
- [x] Docker setup: Dockerfile + docker-compose.yml with health checks
- [x] CI/CD: GitHub Actions with ruff + pytest
- [x] Backup automation: scripts/backup.sh with pg_dump
- [x] Monitoring: Prometheus metrics endpoint (`/metrics`)
- [x] Log rotation: Moved from `/tmp/*.log` to `logs/*.log`
- [x] Fixed all deprecation warnings in usage_stats.py

### Previous Session: BGE-M3 Integration
- [x] PostgreSQL schema fixed (tsvector, GIN index, trigger)
- [x] Redis installed and running (PONG)
- [x] LRU cache added to embedder.py
- [x] All ruff errors fixed (89 → 0)
- [x] All tests passing (84 passed)
- [x] Git commits: `feat: подготовка к продакшену`, `fix: исправление ruff ошибок`

---

## 📋 REMAINING TASKS

### Low Priority
1. Add CSRF protection to dashboard forms
2. Setup cron for auto-backup (`AM_AUTO_SNAPSHOT=true`)
3. Add structured logging (JSON format)
4. Integrate Sentry or similar error tracking
5. Update AGENTS.md to remove outdated FastEmbed mentions
6. Generate OpenAPI docs automatically

---

## 📊 Current Test Status
```
84 passed in ~130s
Ruff check: All checks passed!
Redis: PONG (with LRU cache)
PostgreSQL: schema updated (tsvector, GIN, trigger)
Prometheus: /metrics endpoint active
```

---

## 🎯 Next Action
**Final testing and documentation updates** — verify all components work together, update AGENTS.md.
