# Full Project Handoff

This is a runnable handoff copy of the Order Sheet app with checklist + cutting plan integration.

## Quick Start

```bash
./SETUP_AND_RUN.sh
```

Then open:
- http://127.0.0.1:5001/login

## Default Login
- Email: `admin@example.com`
- Password: `ChangeMe123!`

## Important Routes
- `/orders/<id>/edit`
- `/orders/<id>/checklist`
- `/orders/<id>/cutting-plan`

## Notes
- Uses SQLite by default via `DATABASE_URL=sqlite:///ordersheet.db`.
- AI roster import needs `OPENAI_API_KEY`.
