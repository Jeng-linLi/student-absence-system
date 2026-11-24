# Quick Bilingual Role-Card Demo / 快速雙語角色卡演示

This mode is for local presentation only. It is **disabled by default** and must not be enabled on a public deployment.

此模式只供本機演示使用，**預設關閉**；不要在公開部署啟用。

## Start / 啟動

### Windows

Double-click `START_DEMO_QUICK.bat`.

### Git Bash / Linux / macOS

```bash
./START_DEMO_QUICK.sh
```

Manual start:

```bash
cd leave-system
LEAVE_DEBUG=0 LEAVE_DEMO_QUICK=1 python app.py
```

Then open <http://127.0.0.1:5055/login>.

## What it does / 功能

- `/login` shows four bilingual role cards: `Student / 學生`, `Advisor / 導師`, `Faculty / 院系`, `Admin / 管理員`.
- Clicking a card establishes a normal Flask session through the existing `_establish_session()` helper.
- The role is selected from a fixed allowlist defined in `DEMO_ROLE_ACCOUNTS`.
- CSRF protection is unchanged.
- `/login/password` keeps the original account/password login.
- `/healthz` reports `version 1.20.0`.

## Security boundary / 安全界線

- Enabled only when `LEAVE_DEMO_QUICK=1`.
- If the environment variable is absent, `/login/demo-role` returns 404.
- Never expose this mode to the internet. It is a presentation convenience, not an authentication feature.
- Local test cookies are not committed.

## Demo route / 演示路線

1. Open `/login`.
2. Click `Student / 學生`.
3. Submit one request; the affected courses appear from the timetable.
4. Log out, click `Advisor / 導師`, approve the request.
5. For a request over three days, click `Faculty / 院系` for second-level review.
6. Show the operation log / audit trail on the request page.

## Long-term maintenance / 長期有效性

- The code is versioned on GitHub in branch `demo/quick-bilingual-role-cards`.
- No production data, password, or session cookie is stored in this branch.
- Startup scripts fall back to `python` / `python3` if the WorkBuddy Python environment is unavailable.
- GitHub stores the code permanently; it does not keep `127.0.0.1` running. Run the startup script before presenting.
