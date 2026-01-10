
## 2026-01-09
- Fixed auth-required redirect handling used by helper functions.
- Updated layout template.
- Improved rooms page layout and inline editing behaviour.

# RenoTracker – Changelog

## [v1.3.0] – 2026-01-09

### Added
- Task scheduling support:
  - `start_date` and `end_date` fields added to tasks
  - Automatic schema patching via `ensure_schema()` (no migration required)
- Gantt chart view:
  - New `/gantt` page with visual task timeline
  - Tasks grouped and labelled by room where applicable
  - Intelligent date fallbacks:
    - Start: start_date → created_at → today
    - End: end_date → completed_at → due_date → +1 day
- Navigation link to Gantt view added to main layout

### Improved
- Inline task editing now supports start/end dates
- Task lifecycle visualisation aligned with task status
- Robust auth redirect handling via global RuntimeError handler

### Technical
- Safe JSON rendering for Gantt data to prevent template escaping issues
- No breaking database changes (backwards compatible)
- Schema auto-patching extended for task planning fields

## [v1.3] - 2026-01-10

### Fixed
- Fixed task update crashing with “Internal Server Error” when saving, caused by missing `depends_on` form binding.
- Task edit now correctly persists `progress` (0–100) and `depends_on` values.

### Added
- Task fields: `progress` (0–100) and `depends_on` (CSV of task IDs) supported end-to-end.
- Gantt view now uses stored task progress (falls back to 0/100 based on status).
